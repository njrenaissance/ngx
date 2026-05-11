"""End-to-end checks against the live container in the Docker Compose stack.

Auto-marked `integration` by the auto-marker in tests/conftest.py. Skipped by
the unit-tests workflow; run explicitly with `pytest -m integration`.
"""

import httpx

from forge import __version__


def test_root_redirects_to_docs(forge_url: str) -> None:
    response = httpx.get(f"{forge_url}/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/docs"


def test_root_followed_lands_on_docs(forge_url: str) -> None:
    response = httpx.get(f"{forge_url}/", follow_redirects=True)
    assert response.status_code == 200
    assert "Swagger UI" in response.text


def test_livez_returns_version(forge_url: str) -> None:
    response = httpx.get(f"{forge_url}/livez")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert "Forge" in body["message"]
    assert __version__ in body["message"]
    assert "running" in body["message"]


def test_readyz(forge_url: str) -> None:
    response = httpx.get(f"{forge_url}/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_schema_advertises_version(forge_url: str) -> None:
    response = httpx.get(f"{forge_url}/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["version"] == __version__
