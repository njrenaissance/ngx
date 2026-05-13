"""Unit tests for the workers package (E.1 wiring).

Covers:
- The Celery app config — settings flow through correctly.
- The TaskBroker — submit / get_status / revoke delegate to Celery.
- The API wiring — POST /v1/resources calls TaskBroker.submit.

The provision_resource task body has its own dedicated test module
(test_provision_resource.py) — it grew complex enough with the E.3
plan-then-apply lifecycle that mocking it from here would obscure
both the broker tests and the lifecycle tests.
"""

from unittest.mock import MagicMock

import pytest


class TestCeleryAppConfig:
    def test_celery_app_uses_settings_broker(self) -> None:
        """The app instance reads broker URL and queue from CelerySettings."""
        from forge.config import settings
        from forge.workers import celery_app

        assert celery_app.conf.broker_url == settings.celery.broker_url
        assert celery_app.conf.task_default_queue == settings.celery.task_default_queue

    def test_result_backend_derived_from_database_dsn(self) -> None:
        """Result backend reuses Aurora via the db+ prefix on the sync DSN."""
        from forge.config import settings
        from forge.workers import celery_app

        assert celery_app.conf.result_backend == f"db+{settings.database.sync_url}"

    def test_time_limits_wired_through(self) -> None:
        from forge.config import settings
        from forge.workers import celery_app

        assert celery_app.conf.task_time_limit == settings.celery.task_time_limit
        assert celery_app.conf.task_soft_time_limit == settings.celery.task_soft_time_limit


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
