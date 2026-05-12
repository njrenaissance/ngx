"""provision_resource Celery task — async engine entry point.

E.1 scope (wiring proof): only flips RESOURCE_REQUEST.status from
`pending` to `provisioning` to `provisioned`. This proves the
enqueue/consume path end-to-end. Workspace materialization arrives in
E.2 and real plan-then-apply in E.3.
"""

import logging
import uuid

from celery import shared_task  # type: ignore[import-untyped]

from forge.db import SyncSession
from forge.models.provisioning import ResourceRequest

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

        if rr.status != "pending":
            # Unexpected mid-state — another worker likely holds it. Skip
            # rather than racing.
            logger.warning("provision_resource: %s in unexpected status %s; skipping", rr_id, rr.status)
            return str(rr.status)

        rr.status = "provisioning"
        session.commit()

        # E.1 stub: no Terraform yet. Future PRs replace this with
        # workspace materialization (E.2) and plan-then-apply (E.3).

        rr.status = "provisioned"
        session.commit()
        logger.info("provision_resource: %s -> provisioned", rr_id)
        return str(rr.status)
