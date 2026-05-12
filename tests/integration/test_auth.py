"""Integration tests for the auth-protected /v1/me endpoint.

Requires the full compose stack (postgres + api) and seeded data.
Valid-key tests use Bob Builder (member role, Platform Team) from the
dev seed fixtures — key: crp_dev_b0b00000000000000000000000000000.

Note on 403: /v1/me uses require_auth (not require_admin), so it never
returns 403. Tests for 403 belong with the first admin-only endpoint
landing in #28C+.
"""

import httpx

BOB_KEY = "crp_dev_b0b00000000000000000000000000000"
BAD_KEY = "crp_dev_notavalidkey00000000000000000"


def test_me_without_auth_returns_401(forge_url: str) -> None:
    response = httpx.get(f"{forge_url}/v1/me")
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


def test_me_with_invalid_key_returns_401(forge_url: str) -> None:
    response = httpx.get(f"{forge_url}/v1/me", headers={"Authorization": f"Bearer {BAD_KEY}"})
    assert response.status_code == 401


def test_me_with_valid_key_returns_user_profile(forge_url: str) -> None:
    response = httpx.get(f"{forge_url}/v1/me", headers={"Authorization": f"Bearer {BOB_KEY}"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["email"] == "bob@example.com"
    assert body["first_name"] == "Bob"
    assert body["last_name"] == "Builder"
    assert body["role"] == "member"
    assert body["team"]["name"] == "Platform Team"
    assert body["last_seen_at"] is not None
