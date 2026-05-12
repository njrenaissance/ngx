"""Unit tests for the provision_resource Celery task body — E.3 lifecycle.

Mocks SyncSession + materialize_workspace + TerraformRunner so the task body
runs in-process without a real database, real terraform binary, or real
broker. The integration suite covers the full real-stack path with a fake
terraform binary; this module exercises the FSM transitions and error paths
that are tedious to drive through the integration harness.

The session mock distinguishes ResourceRequest queries from Deployment +
ApplyJob queries via a dispatch on the model class passed to session.query() —
the new lifecycle does both.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from forge.models.provisioning import ApplyJob, Deployment, ResourceRequest
from forge.workers.tasks.provision_resource import provision_resource
from forge.workers.terraform_runner import (
    PlanRequiredError,
    TerraformExecutionError,
)
from forge.workers.workspace import WorkspaceMaterializationError

pytestmark = pytest.mark.unit

task_module = sys.modules["forge.workers.tasks.provision_resource"]


def _build_session(rr: MagicMock, deployment: MagicMock | None) -> MagicMock:
    """Build a session mock that dispatches by query target.

    session.query(ResourceRequest) -> rr
    session.query(Deployment)      -> deployment (or None if not provided)
    session.query(ApplyJob).count() -> 0 (no prior attempts)
    """

    def _query(model: type) -> MagicMock:
        result = MagicMock()
        if model is ResourceRequest:
            result.filter.return_value.first.return_value = rr
        elif model is Deployment:
            result.filter.return_value.first.return_value = deployment
        elif model is ApplyJob:
            result.filter.return_value.count.return_value = 0
        else:
            result.filter.return_value.first.return_value = None
        return result

    session = MagicMock()
    session.__enter__.return_value = session
    session.__exit__.return_value = False
    session.query.side_effect = _query
    return session


def _build_request(status: str = "pending") -> MagicMock:
    rr = MagicMock(spec=ResourceRequest)
    rr.id = uuid.uuid4()
    rr.status = status
    return rr


def _build_deployment() -> MagicMock:
    deployment = MagicMock(spec=Deployment)
    deployment.id = uuid.uuid4()
    deployment.status = "pending"
    deployment.outputs_encrypted = None
    deployment.last_error = None
    deployment.provisioned_at = None
    return deployment


def _success_runner(workdir_path: str = "/tmp/forge-workspaces/fake") -> MagicMock:
    """A TerraformRunner mock whose init/plan/apply all return cleanly."""
    runner = MagicMock()
    runner.init.return_value = MagicMock(stdout="Initialized.", stderr="", returncode=0)
    runner.plan.return_value = Path(workdir_path) / "tfplan"
    runner.apply.return_value = {"endpoint": {"value": "fake.example.internal"}}
    runner.cumulative_log.return_value = "$ terraform init\nInitialized.\n\n$ terraform apply\nApply complete."
    return runner


def _run_task(
    session: MagicMock,
    rr_id: uuid.UUID,
    materializer: MagicMock | None = None,
    runner: MagicMock | None = None,
) -> str:
    mat = materializer or MagicMock(return_value=Path("/tmp/forge-workspaces/fake"))
    runner_factory = MagicMock(return_value=runner or _success_runner())
    with (
        patch.object(task_module, "SyncSession", return_value=session),
        patch.object(task_module, "materialize_workspace", mat),
        patch.object(task_module, "TerraformRunner", runner_factory),
    ):
        return provision_resource.run(str(rr_id))


# ─── happy path ───────────────────────────────────────────────────────────────


class TestHappyPath:
    def test_pending_drives_to_provisioned(self) -> None:
        rr = _build_request(status="pending")
        deployment = _build_deployment()
        session = _build_session(rr, deployment)

        result = _run_task(session, rr.id)

        assert result == "provisioned"
        assert rr.status == "provisioned"
        assert deployment.status == "applied"
        assert deployment.outputs_encrypted is not None
        # Plaintext bytes per SPEC §8.3 — we can decode and assert structure.
        import json as _json

        decoded = _json.loads(deployment.outputs_encrypted.decode("utf-8"))
        assert decoded["endpoint"]["value"] == "fake.example.internal"
        assert deployment.provisioned_at is not None

    def test_apply_job_lifecycle_recorded(self) -> None:
        """An ApplyJob row is added with status 'queued', flipped through
        'running' to 'succeeded', with started_at and completed_at set."""
        rr = _build_request(status="pending")
        deployment = _build_deployment()
        session = _build_session(rr, deployment)

        added: list[object] = []
        session.add.side_effect = added.append

        _run_task(session, rr.id)

        apply_jobs = [obj for obj in added if isinstance(obj, ApplyJob)]
        assert len(apply_jobs) == 1
        job = apply_jobs[0]
        assert job.status == "succeeded"
        assert job.operation == "apply"
        assert job.attempt_count == 1
        assert job.started_at is not None
        assert job.completed_at is not None
        assert job.log_sanitized is not None
        # The log comes from runner.cumulative_log() — verify it contains
        # the apply banner so we know the runner's output trail flows through.
        assert "terraform apply" in job.log_sanitized

    def test_runner_called_in_correct_order(self) -> None:
        rr = _build_request(status="pending")
        deployment = _build_deployment()
        session = _build_session(rr, deployment)
        runner = _success_runner()

        _run_task(session, rr.id, runner=runner)

        # init, plan, apply must all fire in that order. cumulative_log() is
        # called after apply to capture the audit trail; we filter it out so
        # the assertion stays focused on the terraform-stage ordering.
        terraform_stages = [c[0] for c in runner.method_calls if c[0] in {"init", "plan", "apply"}]
        assert terraform_stages == ["init", "plan", "apply"]


# ─── failure paths ────────────────────────────────────────────────────────────


class TestFailurePaths:
    def test_apply_failure_marks_failed_with_sanitized_error(self) -> None:
        rr = _build_request(status="pending")
        deployment = _build_deployment()
        session = _build_session(rr, deployment)

        runner = _success_runner()
        runner.apply.side_effect = TerraformExecutionError(
            stage="apply",
            returncode=2,
            sanitized_stderr="Error: ***ARN*** in account ***ACCOUNT***",
        )

        result = _run_task(session, rr.id, runner=runner)

        assert result == "failed"
        assert rr.status == "failed"
        assert deployment.status == "failed"
        assert deployment.last_error is not None
        # The error is already sanitized when it leaves the runner — no
        # cloud coordinates may appear in last_error.
        assert "arn:aws:" not in deployment.last_error
        assert "***ARN***" in deployment.last_error

    def test_apply_failure_records_apply_job_failed(self) -> None:
        rr = _build_request(status="pending")
        deployment = _build_deployment()
        session = _build_session(rr, deployment)

        added: list[object] = []
        session.add.side_effect = added.append

        runner = _success_runner()
        runner.apply.side_effect = TerraformExecutionError("apply", 2, "boom")

        _run_task(session, rr.id, runner=runner)

        jobs = [o for o in added if isinstance(o, ApplyJob)]
        assert len(jobs) == 1
        assert jobs[0].status == "failed"
        assert jobs[0].completed_at is not None
        assert "apply (failed)" in (jobs[0].log_sanitized or "")
        # Cumulative log from successful prior stages should still be there.
        assert (
            "$ terraform init" in (jobs[0].log_sanitized or "")
            or jobs[0].log_sanitized == "$ terraform apply (failed)\nboom"
        )

    def test_plan_failure_marks_failed(self) -> None:
        rr = _build_request(status="pending")
        deployment = _build_deployment()
        session = _build_session(rr, deployment)

        runner = _success_runner()
        runner.plan.side_effect = TerraformExecutionError("plan", 1, "plan-failed")

        result = _run_task(session, rr.id, runner=runner)

        assert result == "failed"
        assert deployment.status == "failed"
        assert "plan-failed" in (deployment.last_error or "")

    def test_plan_required_error_treated_as_failure(self) -> None:
        """If TerraformRunner refuses apply (e.g. plan() didn't run), the task
        still terminates cleanly as 'failed' rather than re-raising.
        """
        rr = _build_request(status="pending")
        deployment = _build_deployment()
        session = _build_session(rr, deployment)

        runner = _success_runner()
        runner.apply.side_effect = PlanRequiredError("apply refused")

        result = _run_task(session, rr.id, runner=runner)

        assert result == "failed"
        assert deployment.status == "failed"

    def test_materialize_workspace_failure_skips_apply_job(self) -> None:
        """A workspace error happens BEFORE we know which deployment to
        attach an ApplyJob to — no APPLY_JOB row should be created.
        """
        rr = _build_request(status="pending")
        session = _build_session(rr, deployment=None)

        added: list[object] = []
        session.add.side_effect = added.append

        bad_mat = MagicMock(side_effect=WorkspaceMaterializationError("config mismatch"))

        result = _run_task(session, rr.id, materializer=bad_mat)

        assert result == "failed"
        assert rr.status == "failed"
        assert not any(isinstance(o, ApplyJob) for o in added)


# ─── idempotency / re-entry ───────────────────────────────────────────────────


class TestIdempotency:
    def test_provisioned_is_noop(self) -> None:
        rr = _build_request(status="provisioned")
        session = _build_session(rr, deployment=None)
        added: list[object] = []
        session.add.side_effect = added.append

        result = _run_task(session, rr.id)

        assert result == "provisioned"
        session.commit.assert_not_called()
        assert added == []

    def test_failed_is_noop(self) -> None:
        rr = _build_request(status="failed")
        session = _build_session(rr, deployment=None)

        result = _run_task(session, rr.id)

        assert result == "failed"
        session.commit.assert_not_called()

    def test_missing_row_returns_not_found(self) -> None:
        session = _build_session(rr=None, deployment=None)  # type: ignore[arg-type]

        result = _run_task(session, uuid.uuid4())

        assert result == "not_found"

    def test_provisioning_resumes_with_new_apply_job(self) -> None:
        """A row stuck in 'provisioning' (worker crashed) gets a new ApplyJob
        with attempt_count > 1 so the audit trail shows the retry."""
        rr = _build_request(status="provisioning")
        deployment = _build_deployment()

        # Override the dispatch so ApplyJob.count() returns 1 (one prior).
        def _query(model: type) -> MagicMock:
            result = MagicMock()
            if model is ResourceRequest:
                result.filter.return_value.first.return_value = rr
            elif model is Deployment:
                result.filter.return_value.first.return_value = deployment
            elif model is ApplyJob:
                result.filter.return_value.count.return_value = 1
            return result

        session = MagicMock()
        session.__enter__.return_value = session
        session.__exit__.return_value = False
        session.query.side_effect = _query
        added: list[object] = []
        session.add.side_effect = added.append

        result = _run_task(session, rr.id)

        assert result == "provisioned"
        jobs = [o for o in added if isinstance(o, ApplyJob)]
        assert len(jobs) == 1
        assert jobs[0].attempt_count == 2

    def test_non_resumable_status_skipped(self) -> None:
        rr = _build_request(status="destroying")
        session = _build_session(rr, deployment=None)

        result = _run_task(session, rr.id)

        assert result == "destroying"
        session.commit.assert_not_called()
