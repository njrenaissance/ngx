"""Per-request Terraform workspace materializer.

E.2 scope: copy the matching versioned package out of PACKAGES_DIR, render
terraform.tfvars.json + backend.tf into an ephemeral workspace dir, and
persist DEPLOYMENT + DEPLOYMENT_AZ rows. No `terraform` commands run here
— that's E.3.

The workspace path and tf_state_key are deterministic functions of
(env, team_id, resource_request_id, logical_region.name) so re-entry on a
crashed/redelivered task is safe: shutil.copytree(..., dirs_exist_ok=True)
plus tf_state_key-keyed deployment upsert plus (deployment, az) unique
checks mean we converge on the same on-disk + on-DB state.

State key shape (SPEC §8.2):
    {env}/{team_id}/standalone/{rr_id}/{logical_region}/terraform.tfstate

Workspace id is the same key minus the trailing /terraform.tfstate, with
/ swapped for __ so it works as a directory name.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from forge.config import settings
from forge.logging import get_logger
from forge.models.catalog import ResourceType, TierPolicy
from forge.models.provisioning import Deployment, DeploymentAz, ResourceRequest
from forge.models.topology import LogicalRegion, RegionAzMap
from forge.workers.tier_topology import select_az_assignments

logger = get_logger(__name__)

WORKSPACE_ROOT = Path("/tmp/forge-workspaces")  # noqa: S108 — POC; ephemeral by design

_BACKEND_TF_TEMPLATE = """terraform {{
  backend "s3" {{
    bucket = "{bucket}"
    key    = "{key}"
    region = "{region}"
  }}
}}
"""


class WorkspaceMaterializationError(Exception):
    """Raised when a workspace can't be materialized.

    Cases that surface here: config keys diverge from terraform_variable_map
    in either direction, package directory missing on disk, or required
    catalog rows (resource_type / tier_policy / logical_region) missing.
    The provision_resource task catches this and flips RESOURCE_REQUEST.status
    to `failed` rather than retrying — these are structural mismatches that
    a retry won't fix.
    """


def materialize_workspace(session: Session, resource_request: ResourceRequest) -> Path:
    """Build the on-disk workspace and persist DEPLOYMENT + DEPLOYMENT_AZ rows.

    Idempotent: safe to re-run on the same ResourceRequest. Returns the
    workspace directory path.
    """
    resource_type = session.query(ResourceType).filter(ResourceType.id == resource_request.resource_type_id).first()
    if resource_type is None:
        raise WorkspaceMaterializationError(f"resource_type {resource_request.resource_type_id} not found")

    tier_policy = session.query(TierPolicy).filter(TierPolicy.id == resource_request.tier_policy_id).first()
    if tier_policy is None:
        raise WorkspaceMaterializationError(f"tier_policy {resource_request.tier_policy_id} not found")

    logical_region = session.query(LogicalRegion).filter(LogicalRegion.id == resource_request.logical_region_id).first()
    if logical_region is None:
        raise WorkspaceMaterializationError(f"logical_region {resource_request.logical_region_id} not found")

    region_az_maps = (
        session.query(RegionAzMap).filter(RegionAzMap.logical_region_id == resource_request.logical_region_id).all()
    )

    # Bidirectional varmap check. The request validator (API layer) enforces
    # this against the resource_type's base_config_schema before the row is
    # written, but we re-check here because (a) the catalog can drift
    # post-validation, and (b) a worker crash + redelivery shouldn't blow
    # up with KeyError deep inside the tfvars render — explicit error first.
    config_keys = set(resource_request.config.keys())
    varmap_keys = set(resource_type.terraform_variable_map.keys())
    if config_keys != varmap_keys:
        missing_in_varmap = config_keys - varmap_keys
        missing_in_config = varmap_keys - config_keys
        raise WorkspaceMaterializationError(
            f"config/terraform_variable_map mismatch for resource_type "
            f"{resource_type.name} v{resource_type.version}: "
            f"missing_in_varmap={sorted(missing_in_varmap)} "
            f"missing_in_config={sorted(missing_in_config)}"
        )

    tf_state_key = (
        f"{settings.environment}/{resource_request.team_id}/standalone/"
        f"{resource_request.id}/{logical_region.name}/terraform.tfstate"
    )
    tf_workspace_id = tf_state_key.removesuffix("/terraform.tfstate").replace("/", "__")

    packages_dir = Path(settings.terraform.packages_dir)
    source_terraform_dir = packages_dir / resource_type.name / f"v{resource_type.version}" / "terraform"
    if not source_terraform_dir.is_dir():
        raise WorkspaceMaterializationError(f"package terraform dir not found: {source_terraform_dir}")

    dest = WORKSPACE_ROOT / tf_workspace_id
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_terraform_dir, dest, dirs_exist_ok=True)

    tfvars = {resource_type.terraform_variable_map[k]: v for k, v in resource_request.config.items()}
    (dest / "terraform.tfvars.json").write_text(json.dumps(tfvars, indent=2, sort_keys=True))

    (dest / "backend.tf").write_text(
        _BACKEND_TF_TEMPLATE.format(
            bucket=settings.terraform.managed_resources_bucket,
            key=tf_state_key,
            region=settings.terraform.managed_resources_region,
        )
    )

    # Deployment upsert keyed on tf_state_key (UNIQUE constraint per migration
    # c3d4e5f6a7b8). On re-entry we reuse the existing row.
    existing = session.query(Deployment).filter(Deployment.tf_state_key == tf_state_key).first()
    if existing is None:
        deployment = Deployment(
            resource_request_id=resource_request.id,
            logical_region_id=resource_request.logical_region_id,
            tf_workspace_id=tf_workspace_id,
            tf_state_key=tf_state_key,
            # E.2 placeholder: workspace is on disk but terraform has not run.
            # E.3 introduces "applying" -> "provisioned" transitions via apply.
            status="pending",
        )
        session.add(deployment)
        session.flush()
    else:
        deployment = existing

    try:
        az_assignments = select_az_assignments(region_az_maps, tier_policy.min_azs_per_region)
    except ValueError as exc:
        raise WorkspaceMaterializationError(str(exc)) from exc
    for az_map, role in az_assignments:
        already = (
            session.query(DeploymentAz)
            .filter(
                DeploymentAz.deployment_id == deployment.id,
                DeploymentAz.az_map_id == az_map.id,
            )
            .first()
        )
        if already is None:
            session.add(
                DeploymentAz(
                    deployment_id=deployment.id,
                    az_map_id=az_map.id,
                    az_role=role,
                )
            )

    session.commit()
    logger.info(
        "workspace materialized",
        extra={
            "resource_id": str(resource_request.id),
            "deployment_id": str(deployment.id),
            "workspace_path": str(dest),
            "tf_state_key": tf_state_key,
            "az_count": len(az_assignments),
        },
    )
    return dest
