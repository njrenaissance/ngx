"""provision_resource Celery task — async engine entry point.

E.3 scope: drives a ResourceRequest through the full plan-then-apply
lifecycle. Status transitions:

    ResourceRequest:  pending -> provisioning -> provisioned (or failed)
    Deployment:       pending -> planned -> applying -> applied (or failed)
    ApplyJob:         queued  -> running -> succeeded (or failed)

SPEC Appendix B rule 4 (no apply without a saved plan) is enforced inside
TerraformRunner. Rule 1 (no cloud coordinates in API responses / logs) is
enforced inside TerraformRunner._sanitize and applied to every stdout/stderr
returned to this task. APPLY_JOB.log_sanitized is the audit trail of what
the worker did and is safe to expose via /v1/resources/{id}/logs in the
POC; outputs go into DEPLOYMENT.outputs_encrypted as plaintext bytes
(SPEC §8.3 — encryption is a hardening follow-up).

Failure handling: any TerraformExecutionError / PlanRequiredError /
WorkspaceMaterializationError is captured into APPLY_JOB.log_sanitized +
DEPLOYMENT.last_error + DEPLOYMENT.status="failed" + RR.status="failed",
then the task returns "failed" cleanly. We do NOT re-raise — Celery would
retry the task, and APPLY_JOB is the audit trail for failed runs.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from celery import shared_task  # type: ignore[import-untyped]

from forge.db import SyncSession
from forge.logging import get_logger
from forge.models.provisioning import ApplyJob, Deployment, ResourceRequest
from forge.workers.terraform_runner import (
    PlanRequiredError,
    TerraformExecutionError,
    TerraformRunner,
)
from forge.workers.workspace import WorkspaceMaterializationError, materialize_workspace

logger = get_logger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


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
    log_ctx = {"resource_id": str(rr_id), "task": "provision_resource"}

    with SyncSession() as session:
        rr = session.query(ResourceRequest).filter(ResourceRequest.id == rr_id).first()
        if rr is None:
            logger.warning("resource_request not found", extra=log_ctx)
            return "not_found"

        # Idempotency guard — re-entry after success is a no-op. Matters
        # because task_acks_late + task_reject_on_worker_lost can cause a
        # redelivery if the worker dies between status update and ack.
        if rr.status in {"provisioned", "failed"}:
            logger.info("already in terminal status; skipping", extra={**log_ctx, "status": rr.status})
            return str(rr.status)

        # `provisioning` is also a valid re-entry state: a worker may have
        # crashed after flipping pending -> provisioning but before
        # finishing the work. Treat as resume — workspace materializer is
        # idempotent (upserts on tf_state_key), as is plan/apply against
        # the saved tfstate. A resumed run gets a NEW ApplyJob row so
        # attempt_count grows and the audit trail stays honest.
        if rr.status not in {"pending", "provisioning"}:
            # Other states (destroy_requested, destroying, destroyed) are
            # not our lifecycle; refuse to drive them forward.
            logger.warning("non-resumable status; skipping", extra={**log_ctx, "status": rr.status})
            return str(rr.status)

        if rr.status == "pending":
            logger.debug("status transition", extra={**log_ctx, "from": "pending", "to": "provisioning"})
            rr.status = "provisioning"
            session.commit()

        # ─── Materialize workspace ────────────────────────────────────────
        # WorkspaceMaterializationError is a structural mismatch — retrying
        # won't help. Flip RR to failed and return without an APPLY_JOB row
        # (the failure happened before we got far enough to decide which
        # deployment to attach a job to).
        try:
            workdir = materialize_workspace(session, rr)
        except WorkspaceMaterializationError as exc:
            logger.error("materialize_workspace failed", extra={**log_ctx, "error": str(exc)})
            rr.status = "failed"
            session.commit()
            return str(rr.status)

        # The deployment row was just upserted by materialize_workspace.
        # Re-read by tf_state_key — re-entry safe and avoids relying on
        # a stale local reference if materialize_workspace was a no-op.
        deployment = session.query(Deployment).filter(Deployment.resource_request_id == rr.id).first()
        if deployment is None:
            # Should never happen — materialize_workspace just wrote it.
            # Treat as a structural failure rather than a crash.
            logger.error("deployment row missing after materialize", extra=log_ctx)
            rr.status = "failed"
            session.commit()
            return str(rr.status)

        # ─── ApplyJob: queued -> running ──────────────────────────────────
        # attempt_count starts at the count of prior jobs + 1 so a resumed
        # run is visible as attempt 2 in the audit trail.
        prior_attempts = session.query(ApplyJob).filter(ApplyJob.deployment_id == deployment.id).count()
        apply_job = ApplyJob(
            deployment_id=deployment.id,
            operation="apply",
            status="queued",
            attempt_count=prior_attempts + 1,
            enqueued_at=_now(),
        )
        session.add(apply_job)
        session.commit()
        apply_job.status = "running"
        apply_job.started_at = _now()
        session.commit()

        # ─── Plan-then-apply ──────────────────────────────────────────────
        runner = TerraformRunner()
        try:
            runner.init(workdir)
            plan_path = runner.plan(workdir)
            deployment.status = "planned"
            session.commit()

            deployment.status = "applying"
            session.commit()
            outputs = runner.apply(workdir, plan_path)

            deployment.status = "applied"
            # Plaintext bytes in the POC per SPEC §8.3 — column is bytea so
            # the encryption upgrade is field-only, not schema-changing.
            deployment.outputs_encrypted = json.dumps(outputs, sort_keys=True).encode("utf-8")
            # Sanitized init+plan+apply+output stdout from the runner. SPEC
            # Appendix B rule 1 — guaranteed clean of ARNs/account IDs/regions.
            apply_job.log_sanitized = runner.cumulative_log()
            apply_job.status = "succeeded"
            apply_job.completed_at = _now()
            deployment.provisioned_at = _now()
            rr.status = "provisioned"
            session.commit()

            logger.info("provisioned", extra={**log_ctx, "deployment_id": str(deployment.id)})
            return "provisioned"

        except (TerraformExecutionError, PlanRequiredError) as exc:
            # Both error types carry already-sanitized text. The error
            # message goes in last_error; the cumulative log of stages that
            # ran successfully (init, maybe plan) plus a banner for the
            # failed stage goes in log_sanitized.
            sanitized = getattr(exc, "sanitized_stderr", str(exc))
            stage = getattr(exc, "stage", "plan")
            failure_log = f"{runner.cumulative_log()}\n\n$ terraform {stage} (failed)\n{sanitized}".strip()

            deployment.status = "failed"
            deployment.last_error = sanitized
            apply_job.log_sanitized = failure_log
            apply_job.status = "failed"
            apply_job.completed_at = _now()
            rr.status = "failed"
            session.commit()

            logger.error(
                "terraform failed",
                extra={**log_ctx, "stage": stage, "deployment_id": str(deployment.id)},
            )
            return "failed"
