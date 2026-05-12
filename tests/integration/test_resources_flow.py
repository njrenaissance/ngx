"""End-to-end flow for /v1/resources against the Docker Compose stack.

POST a resource request, then GET it back via detail/status/list. Confirms the
full provisioning create+read path works through the real Postgres schema and
seeded catalog. Uses Bob Builder's seeded key (Platform Team, member role) —
same convention as tests/integration/test_auth.py.
"""

import httpx

BOB_KEY = "crp_87435518f7b581136434fcf3af2bad34"
AUTH = {"Authorization": f"Bearer {BOB_KEY}"}


def test_post_then_get_resource(forge_url: str) -> None:
    payload = {
        "resource_type": "managed_database",
        "tier": "tier2",
        "logical_region": "ngx-region-1a",
        "name": "integration-test-db",
        "config": {"engine": "postgres", "size": "small"},
    }

    create = httpx.post(f"{forge_url}/v1/resources", json=payload, headers=AUTH)
    assert create.status_code == 202, create.text
    created = create.json()
    assert created["status"] == "pending"
    assert "resource_id" in created
    resource_id = created["resource_id"]
    assert created["poll_url"] == f"/v1/resources/{resource_id}/status"

    detail = httpx.get(f"{forge_url}/v1/resources/{resource_id}", headers=AUTH)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["resource_id"] == resource_id
    assert body["name"] == "integration-test-db"
    assert body["resource_type"] == "managed_database"
    assert body["tier"] == "tier2"
    assert body["logical_region"] == "ngx-region-1a"
    assert body["status"] == "pending"
    # Cloud coordinate embargo — these must never appear in responses.
    for forbidden in ("provider", "physical_region", "tf_workspace_id", "tf_state_key", "outputs_encrypted"):
        assert forbidden not in body, f"Embargoed field '{forbidden}' leaked in detail response"

    status_resp = httpx.get(f"{forge_url}/v1/resources/{resource_id}/status", headers=AUTH)
    assert status_resp.status_code == 200, status_resp.text
    status_body = status_resp.json()
    assert status_body["status"] == "pending"
    assert status_body["resource_id"] == resource_id

    listing = httpx.get(f"{forge_url}/v1/resources?status=pending", headers=AUTH)
    assert listing.status_code == 200, listing.text
    items = listing.json()["items"]
    assert any(item["resource_id"] == resource_id for item in items)


def test_post_with_team_id_in_body_returns_400(forge_url: str) -> None:
    payload = {
        "resource_type": "managed_database",
        "tier": "tier2",
        "logical_region": "ngx-region-1a",
        "name": "should-be-rejected",
        "config": {"engine": "postgres", "size": "small"},
        "team_id": "00000000-0000-0000-0000-000000000001",
    }
    resp = httpx.post(f"{forge_url}/v1/resources", json=payload, headers=AUTH)
    assert resp.status_code == 400
    assert "team_id" in resp.json()["detail"]


def test_unknown_resource_id_returns_404(forge_url: str) -> None:
    resp = httpx.get(
        f"{forge_url}/v1/resources/00000000-0000-0000-0000-000000000000",
        headers=AUTH,
    )
    assert resp.status_code == 404


def test_list_requires_auth(forge_url: str) -> None:
    resp = httpx.get(f"{forge_url}/v1/resources")
    assert resp.status_code == 401
