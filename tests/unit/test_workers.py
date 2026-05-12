"""Unit tests for the workers package (E.1 wiring).

Covers:
- The provision_resource task body — status transitions and idempotency.
- The Celery app config — settings flow through correctly.
- The TaskBroker — submit / get_status / revoke delegate to Celery.
- The API wiring — POST /v1/resources calls TaskBroker.submit.
"""

import sys
import uuid
from unittest.mock import MagicMock, patch

import pytest

from forge.workers.tasks.provision_resource import provision_resource  # the @shared_task

# The submodule name `provision_resource` is shadowed in `forge.workers.tasks`
# by the task object re-exported via __init__.py. Pull the actual module out
# of sys.modules so we can patch attributes on it (e.g. SyncSession).
task_module = sys.modules["forge.workers.tasks.provision_resource"]


def _mock_session_with_request(status: str = "pending") -> tuple[MagicMock, MagicMock]:
    """Build a session+request pair that mimics SyncSession() as a contextmgr."""
    rr = MagicMock()
    rr.id = uuid.uuid4()
    rr.status = status

    session = MagicMock()
    session.__enter__.return_value = session
    session.__exit__.return_value = False
    session.query.return_value.filter.return_value.first.return_value = rr
    return session, rr


def _run(
    session: MagicMock,
    rr_id: uuid.UUID,
    materializer: MagicMock | None = None,
) -> tuple[str, MagicMock]:
    """Invoke the task body synchronously with SyncSession + materializer patched.

    Note: `.run()` calls the wrapped Python function directly, bypassing
    the Celery task envelope — no autoretry_for, no result backend
    persistence, no acks_late semantics. When E.3 adds retry policies
    (e.g. `autoretry_for=(TerraformError,)`), those will need separate
    coverage via a Celery-aware test harness or integration test.

    Returns (result, materializer_mock) so callers can assert on it.
    """
    mat = materializer or MagicMock(return_value="/tmp/forge-workspaces/fake")
    with (
        patch.object(task_module, "SyncSession", return_value=session),
        patch.object(task_module, "materialize_workspace", mat),
    ):
        return provision_resource.run(str(rr_id)), mat


class TestProvisionResource:
    def test_pending_transitions_to_provisioned(self) -> None:
        session, rr = _mock_session_with_request(status="pending")
        result, _ = _run(session, rr.id)
        assert result == "provisioned"
        assert rr.status == "provisioned"

    def test_intermediate_status_set_before_terminal(self) -> None:
        """Status flips to `provisioning` then `provisioned` — both commits happen."""
        session, rr = _mock_session_with_request(status="pending")
        observed_statuses: list[str] = []
        session.commit.side_effect = lambda: observed_statuses.append(rr.status)
        _run(session, rr.id)
        # The mocked materializer doesn't call session.commit(), so only the
        # two task-driven transitions are observed here.
        assert observed_statuses == ["provisioning", "provisioned"]

    def test_idempotent_on_provisioned(self) -> None:
        """Re-entry on a terminal row is a no-op (no commits)."""
        session, rr = _mock_session_with_request(status="provisioned")
        result, mat = _run(session, rr.id)
        assert result == "provisioned"
        session.commit.assert_not_called()
        mat.assert_not_called()

    def test_idempotent_on_failed(self) -> None:
        session, rr = _mock_session_with_request(status="failed")
        result, mat = _run(session, rr.id)
        assert result == "failed"
        session.commit.assert_not_called()
        mat.assert_not_called()

    def test_missing_row_returns_not_found(self) -> None:
        session = MagicMock()
        session.__enter__.return_value = session
        session.__exit__.return_value = False
        session.query.return_value.filter.return_value.first.return_value = None
        result, mat = _run(session, uuid.uuid4())
        assert result == "not_found"
        mat.assert_not_called()

    def test_provisioning_is_resumed(self) -> None:
        """A row already in `provisioning` (worker crashed mid-flight) is resumed,
        not skipped. Otherwise an acks_late redelivery would strand the row.
        Materializer is still invoked on the resume path because itself is
        idempotent — it converges on the same on-disk + on-DB state."""
        session, rr = _mock_session_with_request(status="provisioning")
        result, mat = _run(session, rr.id)
        assert result == "provisioned"
        assert rr.status == "provisioned"
        # Materializer called exactly once even on the resume path.
        mat.assert_called_once_with(session, rr)
        # Only one task-level commit on the resume path — pending->provisioning
        # was already done, so we only commit the terminal transition.
        assert session.commit.call_count == 1

    def test_non_resumable_status_skipped(self) -> None:
        """Statuses outside our lifecycle (e.g. destroy_*) are refused."""
        session, rr = _mock_session_with_request(status="destroying")
        result, mat = _run(session, rr.id)
        assert result == "destroying"
        session.commit.assert_not_called()
        mat.assert_not_called()

    def test_materializer_called_exactly_once_on_happy_path(self) -> None:
        session, rr = _mock_session_with_request(status="pending")
        _, mat = _run(session, rr.id)
        mat.assert_called_once_with(session, rr)

    def test_materializer_failure_marks_request_failed(self) -> None:
        """WorkspaceMaterializationError flips status to `failed` and does
        not propagate — the task returns "failed" cleanly so Celery doesn't
        retry a structural mismatch that won't fix itself."""
        from forge.workers.workspace import WorkspaceMaterializationError

        session, rr = _mock_session_with_request(status="pending")
        bad_mat = MagicMock(side_effect=WorkspaceMaterializationError("config mismatch"))
        result, _ = _run(session, rr.id, materializer=bad_mat)
        assert result == "failed"
        assert rr.status == "failed"
        # Two commits: pending->provisioning, then ->failed. The materializer
        # raised before its own commit could fire.
        assert session.commit.call_count == 2


class TestCeleryAppConfig:
    def test_celery_app_uses_settings_broker(self) -> None:
        """The app instance reads broker URL and queue from CelerySettings."""
        from forge.config import settings
        from forge.workers import celery_app

        assert celery_app.conf.broker_url == settings.celery.BROKER_URL
        assert celery_app.conf.task_default_queue == settings.celery.TASK_DEFAULT_QUEUE

    def test_result_backend_derived_from_database_dsn(self) -> None:
        """Result backend reuses Aurora via the db+ prefix on the sync DSN."""
        from forge.config import settings
        from forge.workers import celery_app

        assert celery_app.conf.result_backend == f"db+{settings.database.sync_url}"

    def test_time_limits_wired_through(self) -> None:
        from forge.config import settings
        from forge.workers import celery_app

        assert celery_app.conf.task_time_limit == settings.celery.TASK_TIME_LIMIT
        assert celery_app.conf.task_soft_time_limit == settings.celery.TASK_SOFT_TIME_LIMIT


class TestTaskRegistry:
    def test_registry_contains_provision_resource(self) -> None:
        from forge.workers.registry import TASK_REGISTRY

        assert TASK_REGISTRY["provision_resource"] == "forge.provision_resource"


@pytest.mark.no_broker_stub
class TestTaskBroker:
    def test_submit_resolves_through_registry(self) -> None:
        from forge.workers.broker import TaskBroker

        fake_celery = MagicMock()
        fake_celery.send_task.return_value.id = "task-xyz"
        broker = TaskBroker(fake_celery)
        result = broker.submit("provision_resource", kwargs={"resource_request_id": "rid"})

        assert result == "task-xyz"
        fake_celery.send_task.assert_called_once_with(
            "forge.provision_resource", args=[], kwargs={"resource_request_id": "rid"}
        )

    def test_submit_unknown_task_raises(self) -> None:
        from forge.workers.broker import TaskBroker

        broker = TaskBroker(MagicMock())
        try:
            broker.submit("not_in_registry")
        except KeyError as e:
            assert "not_in_registry" in str(e)
        else:
            raise AssertionError("Expected KeyError for unknown task name")

    def test_get_status_returns_dataclass(self) -> None:
        from forge.workers.broker import TaskBroker

        fake_celery = MagicMock()
        async_result = MagicMock()
        async_result.state = "SUCCESS"
        async_result.result = "provisioned"
        async_result.traceback = None
        async_result.date_done = None
        fake_celery.AsyncResult.return_value = async_result

        broker = TaskBroker(fake_celery)
        status = broker.get_status("tid")
        assert status.state == "SUCCESS"
        assert status.result == "provisioned"

    def test_revoke_delegates_to_control(self) -> None:
        from forge.workers.broker import TaskBroker

        fake_celery = MagicMock()
        broker = TaskBroker(fake_celery)
        broker.revoke("tid", terminate=True)
        fake_celery.control.revoke.assert_called_once_with("tid", terminate=True)


class TestAPIWiringDispatchesTask:
    """Confirm POST /v1/resources actually calls broker.submit() with the right kwargs."""

    def test_create_resource_submits_provision_task(self, _stub_task_broker_submit: MagicMock) -> None:
        from tests.unit.test_resources import (
            VALID_BODY,
            _client_with_session,
            _make_region,
            _make_resource_type,
            _make_tier,
            _session_for_create,
        )

        session = _session_for_create(_make_resource_type(), _make_tier(), _make_region())
        resp = _client_with_session(session).post("/v1/resources", json=VALID_BODY)
        assert resp.status_code == 202
        _stub_task_broker_submit.assert_called_once()
        call = _stub_task_broker_submit.call_args
        assert call.args[0] == "provision_resource"
        assert call.kwargs["kwargs"]["resource_request_id"] == resp.json()["resource_id"]
