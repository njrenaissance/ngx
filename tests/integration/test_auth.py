"""Integration test for the auth-protected /v1/me endpoint.

This test only verifies the route is mounted and rejects unauthenticated
requests against the live stack. End-to-end happy-path tests (valid seeded key
→ 200) need automated alembic + seed against the compose Postgres, which lands
in a follow-up alongside #28B's schema migrations.
"""

import httpx


def test_me_without_auth_returns_401(forge_url: str) -> None:
    response = httpx.get(f"{forge_url}/v1/me")
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


def test_me_with_malformed_header_returns_401(forge_url: str) -> None:
    response = httpx.get(f"{forge_url}/v1/me", headers={"Authorization": "Basic abc"})
    assert response.status_code == 401
