from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from forge import __version__
from forge.api import health


def test_livez_returns_200_with_version(client: TestClient) -> None:
    response = client.get("/livez")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert __version__ in body["message"]
    assert "Forge" in body["message"]
    assert "running" in body["message"]


@pytest.fixture
def restore_readiness_checks() -> Iterator[None]:
    """Snapshot/restore READINESS_CHECKS so per-test patches don't leak."""
    original = health.READINESS_CHECKS.copy()
    try:
        yield
    finally:
        health.READINESS_CHECKS.clear()
        health.READINESS_CHECKS.update(original)


def test_readyz_status_ok_when_all_checks_pass(client: TestClient, restore_readiness_checks: None) -> None:
    health.READINESS_CHECKS["db"] = lambda: (True, "ok")

    response = client.get("/readyz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["db"] == {"status": "ok", "detail": "ok"}


def test_readyz_status_degraded_when_db_check_fails(client: TestClient, restore_readiness_checks: None) -> None:
    health.READINESS_CHECKS["db"] = lambda: (False, "connection refused")

    response = client.get("/readyz")

    # Soft readiness — still 200 even when degraded so the ALB keeps
    # routing traffic while we troubleshoot.
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["db"] == {
        "status": "error",
        "detail": "connection refused",
    }


def test_readyz_payload_includes_all_registered_checks(client: TestClient, restore_readiness_checks: None) -> None:
    health.READINESS_CHECKS["db"] = lambda: (True, "ok")
    health.READINESS_CHECKS["fake_service"] = lambda: (True, "ok")

    response = client.get("/readyz")

    body = response.json()
    assert set(body["checks"].keys()) == {"db", "fake_service"}
