"""Integration-test fixtures.

These tests exercise the entire Docker Compose stack: the FastAPI app is built
into the production image and reached over HTTP via the published port. They
catch failure modes unit tests cannot — uvicorn boot, Dockerfile correctness,
healthcheck wiring, and the python -m forge entry point.

Lifecycle is managed by pytest-docker:
  - `docker_compose_file` points at the repo-root docker-compose.yml
  - `docker_services` starts the stack once per test session, tears it down on
    session exit, and `wait_until_responsive` blocks until /livez returns 200
"""

import sys
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Make db/ importable so the seed function can be called directly.
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(scope="session")
def docker_compose_file(pytestconfig: pytest.Config) -> str:
    return str(REPO_ROOT / "docker-compose.yml")


@pytest.fixture(scope="session")
def docker_compose_project_name() -> str:
    return "forge-integration"


def _is_responsive(url: str) -> Callable[[], bool]:
    def check() -> bool:
        try:
            return httpx.get(f"{url}/livez", timeout=2.0).status_code == 200
        except httpx.RequestError:
            return False

    return check


@pytest.fixture(scope="session")
def forge_url(docker_ip: str, docker_services: pytest.FixtureRequest) -> str:
    """Base URL of the live Forge service inside the compose stack."""
    port = docker_services.port_for("api", 8000)  # type: ignore[attr-defined]
    url = f"http://{docker_ip}:{port}"
    docker_services.wait_until_responsive(  # type: ignore[attr-defined]
        # 180s covers cold CI runs that rebuild the Dockerfile from scratch
        # (no layer cache). A warm local run completes well inside the window.
        timeout=180.0,
        pause=1.0,
        check=_is_responsive(url),
    )
    return url


@pytest.fixture(scope="session", autouse=True)
def seeded_db(forge_url: str) -> None:
    """Run alembic migrations + seed against the compose Postgres.

    Depends on forge_url so we know the stack is fully up before touching the
    DB. Both operations are idempotent — safe to run on an already-migrated,
    already-seeded DB.

    The test process connects to localhost:5432 (the published compose port);
    FORGE_DATABASE__HOST defaults to 'localhost' so no env override is needed.
    """
    from alembic.config import Config

    from alembic import command

    alembic_cfg = Config(str(REPO_ROOT / "alembic.ini"))
    command.upgrade(alembic_cfg, "head")

    from db.seed import (
        _load_fixtures,
        seed,
        seed_logical_regions,
        seed_resource_types,
        seed_tier_policies,
        seed_tier_region_members,
    )
    from forge.db import SyncSession

    fixtures = _load_fixtures()
    with SyncSession() as session:
        from forge.models import AppUser, TierPolicy

        if not session.query(AppUser).count():
            seed(session, fixtures)

        if not session.query(TierPolicy).count():
            tier_map = seed_tier_policies(session, fixtures)
            region_map = seed_logical_regions(session, fixtures)
            seed_tier_region_members(session, fixtures, tier_map, region_map)
            seed_resource_types(session, fixtures)
            session.commit()
