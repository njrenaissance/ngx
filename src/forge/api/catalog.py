from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from forge.api.auth import AuthContext, require_auth
from forge.api.deps import get_db_session
from forge.api.problem_details import ProblemDetailsException
from forge.api.schemas.catalog import RegionResponse, ResourceTypeResponse, TierResponse
from forge.models.catalog import ResourceType, ResourceTypeTierConstraint, TierPolicy
from forge.models.topology import LogicalRegion

router = APIRouter(prefix="/v1/catalog", tags=["catalog"])


def _rt_response(rt: ResourceType, schema: dict) -> ResourceTypeResponse:
    return ResourceTypeResponse(
        id=rt.id,
        name=rt.name,
        version=rt.version,
        label=rt.label,
        description=rt.description,
        config_schema=schema,
        active=rt.active,
        latest=rt.latest,
    )


@router.get(
    "/regions",
    response_model=list[RegionResponse],
    responses={401: {"description": "Invalid or missing API key"}},
)
def list_regions(
    auth: AuthContext = Depends(require_auth),
    session: Session = Depends(get_db_session),
) -> list[RegionResponse]:
    regions = (
        session.query(LogicalRegion)
        .filter(
            LogicalRegion.active.is_(True),
            LogicalRegion.platform_assigned_only.is_(False),
        )
        .all()
    )
    return [RegionResponse.model_validate(r) for r in regions]


@router.get(
    "/tiers",
    response_model=list[TierResponse],
    responses={401: {"description": "Invalid or missing API key"}},
)
def list_tiers(
    auth: AuthContext = Depends(require_auth),
    session: Session = Depends(get_db_session),
) -> list[TierResponse]:
    tiers = session.query(TierPolicy).order_by(TierPolicy.tier_name).all()
    return [TierResponse.model_validate(t) for t in tiers]


@router.get(
    "/resource-types",
    response_model=list[ResourceTypeResponse],
    responses={401: {"description": "Invalid or missing API key"}},
)
def list_resource_types(
    auth: AuthContext = Depends(require_auth),
    session: Session = Depends(get_db_session),
) -> list[ResourceTypeResponse]:
    types = session.query(ResourceType).filter(ResourceType.active.is_(True), ResourceType.latest.is_(True)).all()
    return [_rt_response(rt, rt.base_config_schema) for rt in types]


@router.get(
    "/resource-types/{name}",
    response_model=ResourceTypeResponse,
    responses={
        401: {"description": "Invalid or missing API key"},
        404: {"description": "Resource type or tier not found"},
    },
)
def get_resource_type(
    name: str,
    tier: str | None = None,
    auth: AuthContext = Depends(require_auth),
    session: Session = Depends(get_db_session),
) -> ResourceTypeResponse:
    rt = (
        session.query(ResourceType)
        .filter(
            ResourceType.name == name,
            ResourceType.latest.is_(True),
            ResourceType.active.is_(True),
        )
        .first()
    )
    if rt is None:
        raise ProblemDetailsException(
            status=404,
            type="urn:forge:error:resource-type-not-found",
            title="Resource type not found",
            detail=f"Resource type '{name}' not found",
        )

    schema = rt.base_config_schema

    if tier is not None:
        tp = session.query(TierPolicy).filter(TierPolicy.tier_name == tier).first()
        if tp is None:
            raise ProblemDetailsException(
                status=404,
                type="urn:forge:error:tier-not-found",
                title="Tier not found",
                detail=f"Tier '{tier}' not found",
            )

        constraint = (
            session.query(ResourceTypeTierConstraint).filter_by(resource_type_id=rt.id, tier_policy_id=tp.id).first()
        )
        if constraint is not None:
            # Shallow merge: override keys replace base keys wholesale. Constraints
            # are expected to replace entire sub-keys (e.g. the full "properties" dict),
            # not add individual leaf properties alongside existing ones.
            schema = {**schema, **constraint.config_schema_override}

    return _rt_response(rt, schema)
