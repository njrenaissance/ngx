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

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


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
        timeout=60.0,
        pause=1.0,
        check=_is_responsive(url),
    )
    return url
