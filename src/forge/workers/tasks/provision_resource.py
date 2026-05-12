"""provision_resource Celery task — async engine entry point.

E.2 scope: flips RESOURCE_REQUEST.status from `pending` to `provisioning`,
materializes a per-request Terraform workspace on disk (no terraform run
yet), persists DEPLOYMENT + DEPLOYMENT_AZ, then flips to `provisioned`.
Real plan-then-apply arrives in E.3.
"""

import logging
import uuid

from celery import shared_task  # type: ignore[import-untyped]

from forge.db import SyncSession
from forge.models.provisioning import ResourceRequest
from forge.workers.workspace import WorkspaceMaterializationError, materialize_workspace

logger = logging.getLogger(__name__)


@shared_task(name="forge.provision_resource")
def provision_resource(resource_request_id: str) -> str:
    """Drive a ResourceRequest from pending to provisioned.

    Idempotent: re-entry on a terminal-status row is a no-op. The task
    receives only the resource_request_id (SPEC §9.1) — all configuration
    is read from the database here so retries see current state.

    Args:
        resource_request_id: UUID of the ResourceRequest row to drive.
            Stringified because Celery's JSON serializer can't round-trip
            uuid.UUID natively.

    Returns:
        Final status string. "not_found" if the row was deleted between
        enqueue and consume.
    """
    rr_id = uuid.UUID(resource_request_id)

    with SyncSession() as session:
        rr = session.query(ResourceRequest).filter(ResourceRequest.id == rr_id).first()
        if rr is None:
            logger.warning("provision_resource: ResourceRequest %s not found", rr_id)
            return "not_found"

        # Idempotency guard — re-entry after success is a no-op. Matters
        # because task_acks_late + task_reject_on_worker_lost can cause a
        # redelivery if the worker dies between status update and ack.
        if rr.status in {"provisioned", "failed"}:
            logger.info("provision_resource: %s already in terminal status %s", rr_id, rr.status)
            return str(rr.status)

        # `provisioning` is also a valid re-entry state: a worker may have
        # crashed after flipping pending -> provisioning but before
        # finishing the work. Treat it as resume, not skip, so the row
        # can't get stranded. E.2/E.3 will replace this stub with
        # workspace materialization + real terraform, both of which must
        # be designed to be safe to re-run on a partially-applied row.
        if rr.status not in {"pending", "provisioning"}:
            # Other states (destroy_requested, destroying, destroyed) are
            # not our lifecycle; refuse to drive them forward.
            logger.warning("provision_resource: %s in non-resumable status %s; skipping", rr_id, rr.status)
            return str(rr.status)

        if rr.status == "pending":
            rr.status = "provisioning"
            session.commit()

        # E.2: materialize the on-disk Terraform workspace and persist
        # DEPLOYMENT + DEPLOYMENT_AZ rows. Still no `terraform` invocation —
        # that arrives in E.3. WorkspaceMaterializationError represents a
        # structural mismatch (config keys don't match terraform_variable_map,
        # missing package on disk, etc.) — retrying won't help, so we flip
        # straight to `failed`.
        try:
            materialize_workspace(session, rr)
        except WorkspaceMaterializationError as exc:
            logger.error("materialize_workspace failed for %s: %s", rr_id, exc)
            rr.status = "failed"
            session.commit()
            return str(rr.status)

        rr.status = "provisioned"
        session.commit()
        logger.info("provision_resource: %s -> provisioned", rr_id)
        return str(rr.status)
