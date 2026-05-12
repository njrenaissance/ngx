import logging
import uuid
from typing import Any, Literal

import jsonschema
from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy.orm import Session, joinedload

from forge.api.auth import AuthContext, require_auth
from forge.api.deps import get_db_session
from forge.api.pagination import Page, page_params
from forge.api.schemas.resources import (
    ResourceCreateRequest,
    ResourceCreateResponse,
    ResourceDetailResponse,
    ResourceListItem,
    ResourceStatusResponse,
)
from forge.models.catalog import ResourceType, ResourceTypeTierConstraint, TierPolicy, TierRegionMember
from forge.models.provisioning import ResourceRequest
from forge.models.topology import LogicalRegion
from forge.workers.broker import TaskBroker, get_task_broker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/resources", tags=["resources"])

ResourceStatus = Literal[
    "pending", "provisioning", "provisioned", "failed", "destroy_requested", "destroying", "destroyed"
]


def _to_detail(rr: ResourceRequest) -> ResourceDetailResponse:
    return ResourceDetailResponse(
        resource_id=rr.id,
        name=rr.name,
        resource_type=rr.resource_type.name,
        resource_type_version=rr.resource_type.version,
        tier=rr.tier_policy.tier_name,
        logical_region=rr.logical_region.name,
        status=rr.status,
        config=rr.config,
        owner_id=rr.requested_by,
        created_at=rr.created_at,
        updated_at=rr.updated_at,
        scheduled_destroy_at=rr.scheduled_destroy_at,
    )


def _to_list_item(rr: ResourceRequest) -> ResourceListItem:
    return ResourceListItem(
        resource_id=rr.id,
        name=rr.name,
        resource_type=rr.resource_type.name,
        resource_type_version=rr.resource_type.version,
        tier=rr.tier_policy.tier_name,
        logical_region=rr.logical_region.name,
        status=rr.status,
        owner_id=rr.requested_by,
        created_at=rr.created_at,
        updated_at=rr.updated_at,
    )


@router.post(
    "",
    response_model=ResourceCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        400: {"description": "team_id supplied by client (server-assigned only)"},
        401: {"description": "Invalid or missing API key"},
        404: {"description": "Unknown resource_type, tier, logical_region, or tier↔region combination not in catalog"},
        422: {"description": "Validation error (schema or config)"},
    },
)
def create_resource(
    payload: dict[str, Any] = Body(...),
    auth: AuthContext = Depends(require_auth),
    session: Session = Depends(get_db_session),
    broker: TaskBroker = Depends(get_task_broker),
) -> ResourceCreateResponse:
    if "team_id" in payload:
        raise HTTPException(
            status_code=400,
            detail="team_id is server-assigned from the authenticated user and cannot be supplied by clients",
        )

    try:
        req = ResourceCreateRequest.model_validate(payload)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors()) from e

    rt_query = session.query(ResourceType).filter(
        ResourceType.name == req.resource_type,
        ResourceType.active.is_(True),
    )
    if req.version is not None:
        rt_query = rt_query.filter(ResourceType.version == req.version)
    else:
        rt_query = rt_query.filter(ResourceType.latest.is_(True))
    rt = rt_query.first()
    if rt is None:
        raise HTTPException(status_code=404, detail=f"Resource type '{req.resource_type}' not found")

    tier = session.query(TierPolicy).filter(TierPolicy.tier_name == req.tier).first()
    if tier is None:
        raise HTTPException(status_code=404, detail=f"Tier '{req.tier}' not found")

    # platform_assigned_only regions are silently 404'd — same information-hiding
    # pattern as cross-team resource isolation (no existence acknowledgement).
    region = (
        session.query(LogicalRegion)
        .filter(
            LogicalRegion.name == req.logical_region,
            LogicalRegion.active.is_(True),
            LogicalRegion.platform_assigned_only.is_(False),
        )
        .first()
    )
    if region is None:
        raise HTTPException(status_code=404, detail=f"Logical region '{req.logical_region}' not found")

    # Enforce catalog-defined tier↔region eligibility. A tier only permits the
    # regions explicitly listed in TierRegionMember — an unlisted combination is
    # a business-rule violation, not just a missing row.
    eligible = session.query(TierRegionMember).filter_by(tier_policy_id=tier.id, logical_region_id=region.id).first()
    if eligible is None:
        raise HTTPException(
            status_code=404,
            detail=f"Region '{req.logical_region}' is not available for tier '{req.tier}'",
        )

    schema = rt.base_config_schema
    constraint = (
        session.query(ResourceTypeTierConstraint).filter_by(resource_type_id=rt.id, tier_policy_id=tier.id).first()
    )
    if constraint is not None:
        # Shallow merge: constraint keys replace base keys wholesale (see api/catalog.py).
        schema = {**schema, **constraint.config_schema_override}

    try:
        jsonschema.validate(req.config, schema)
    except jsonschema.ValidationError as e:
        raise HTTPException(status_code=422, detail=f"config validation failed: {e.message}") from e
    except jsonschema.SchemaError as e:
        logger.error("Malformed config schema for resource_type=%s tier=%s: %s", req.resource_type, req.tier, e)
        raise HTTPException(status_code=500, detail="Internal error: malformed resource type schema") from e

    rr = ResourceRequest(
        team_id=auth.team_id,
        requested_by=auth.user.id,
        resource_type_id=rt.id,
        tier_policy_id=tier.id,
        logical_region_id=region.id,
        name=req.name,
        status="pending",
        config=req.config,
    )
    session.add(rr)
    session.commit()
    session.refresh(rr)

    task_id = broker.submit("provision_resource", kwargs={"resource_request_id": str(rr.id)})
    # Log at INFO so the Celery task ID can be correlated with the
    # resource_id in the worker log. A dedicated celery_task_id column on
    # ResourceRequest is a separate follow-up if stronger correlation is
    # needed (e.g. for revoke from the API).
    logger.info("provision_resource enqueued: resource_id=%s celery_task_id=%s", rr.id, task_id)

    return ResourceCreateResponse(
        resource_id=rr.id,
        status=rr.status,
        poll_url=f"/v1/resources/{rr.id}/status",
        created_at=rr.created_at,
    )


@router.get(
    "",
    response_model=Page[ResourceListItem],
    responses={401: {"description": "Invalid or missing API key"}},
)
def list_resources(
    status_filter: ResourceStatus | None = Query(None, alias="status"),
    resource_type: str | None = Query(None, max_length=64),
    owner_id: uuid.UUID | None = Query(None),
    pagination: tuple[int, int] = Depends(page_params),
    auth: AuthContext = Depends(require_auth),
    session: Session = Depends(get_db_session),
) -> Page[ResourceListItem]:
    page, limit = pagination
    q = (
        session.query(ResourceRequest)
        .options(
            joinedload(ResourceRequest.resource_type),
            joinedload(ResourceRequest.tier_policy),
            joinedload(ResourceRequest.logical_region),
        )
        .filter(ResourceRequest.team_id == auth.team_id)
    )
    if status_filter is not None:
        q = q.filter(ResourceRequest.status == status_filter)
    if resource_type is not None:
        # Join constrains to active types only; historical rows for a now-inactive
        # type name will not appear (soft-deleted types are excluded by design).
        q = q.join(ResourceType, ResourceRequest.resource_type_id == ResourceType.id).filter(
            ResourceType.name == resource_type,
            ResourceType.active.is_(True),
        )
    if owner_id is not None:
        # Cross-team owner_ids return 0 rows silently — the team_id filter above
        # excludes them. No 403, no existence leak.
        q = q.filter(ResourceRequest.requested_by == owner_id)

    total = q.count()
    rows = q.order_by(ResourceRequest.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    return Page[ResourceListItem](
        items=[_to_list_item(rr) for rr in rows],
        page=page,
        limit=limit,
        total=total,
    )


@router.get(
    "/{resource_id}",
    response_model=ResourceDetailResponse,
    responses={
        401: {"description": "Invalid or missing API key"},
        404: {"description": "Resource not found"},
    },
)
def get_resource(
    resource_id: uuid.UUID,
    auth: AuthContext = Depends(require_auth),
    session: Session = Depends(get_db_session),
) -> ResourceDetailResponse:
    rr = (
        session.query(ResourceRequest)
        .filter(ResourceRequest.id == resource_id, ResourceRequest.team_id == auth.team_id)
        .first()
    )
    if rr is None:
        # Silent cross-team isolation: a UUID owned by another team returns 404,
        # not 403 — we don't acknowledge its existence.
        raise HTTPException(status_code=404, detail="Resource not found")
    return _to_detail(rr)


@router.get(
    "/{resource_id}/status",
    response_model=ResourceStatusResponse,
    responses={
        401: {"description": "Invalid or missing API key"},
        404: {"description": "Resource not found"},
    },
)
def get_resource_status(
    resource_id: uuid.UUID,
    auth: AuthContext = Depends(require_auth),
    session: Session = Depends(get_db_session),
) -> ResourceStatusResponse:
    rr = (
        session.query(ResourceRequest)
        .filter(ResourceRequest.id == resource_id, ResourceRequest.team_id == auth.team_id)
        .first()
    )
    if rr is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    return ResourceStatusResponse(resource_id=rr.id, status=rr.status, updated_at=rr.updated_at)
