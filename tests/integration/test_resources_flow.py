"""End-to-end flow for /v1/resources against the Docker Compose stack.

POST a resource request, then GET it back via detail/status/list. Confirms the
full provisioning create+read path works through the real Postgres schema and
seeded catalog. Uses Bob Builder's seeded key (Platform Team, member role) —
same convention as tests/integration/test_auth.py.
"""

import httpx

BOB_KEY = "crp_87435518f7b581136434fcf3af2bad34"
AUTH = {"Authorization": f"Bearer {BOB_KEY}"}

# Any state the worker can have transiently set the row to since #28E wired
# Celery in. The POST response itself always returns "pending" — the worker
# can flip the row before subsequent GETs land.
_LIFECYCLE_STATES = {"pending", "provisioning", "provisioned"}


def test_post_then_get_resource(forge_url: str) -> None:
    payload = {
        "resource_type": "managed_database",
        # Use "dev" tier (min_azs_per_region=1) — the seeded ngx-region-1a
        # only has one AZ, so tier2 (min_azs=2) would cause the materializer
        # to raise and flip the row to "failed".
        "tier": "dev",
        "logical_region": "ngx-region-1a",
        "name": "integration-test-db",
        # storage_gb required: bidirectional varmap check in materialize_workspace
        # requires all terraform_variable_map keys to be present in config.
        "config": {"engine": "postgres", "size": "small", "storage_gb": 100},
    }

    create = httpx.post(f"{forge_url}/v1/resources", json=payload, headers=AUTH)
    assert create.status_code == 202, create.text
    created = create.json()
    # The POST response is built from the row state pre-enqueue; it's deterministic.
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
    assert body["tier"] == "dev"
    assert body["logical_region"] == "ngx-region-1a"
    # The worker may have already advanced the row by the time we GET. Accept
    # any state in the forward lifecycle.
    assert body["status"] in _LIFECYCLE_STATES
    # Cloud coordinate embargo — these must never appear in responses.
    for forbidden in ("provider", "physical_region", "tf_workspace_id", "tf_state_key", "outputs_encrypted"):
        assert forbidden not in body, f"Embargoed field '{forbidden}' leaked in detail response"

    status_resp = httpx.get(f"{forge_url}/v1/resources/{resource_id}/status", headers=AUTH)
    assert status_resp.status_code == 200, status_resp.text
    status_body = status_resp.json()
    assert status_body["status"] in _LIFECYCLE_STATES
    assert status_body["resource_id"] == resource_id
    # End-to-end polling is exercised in test_provisioning_flow_wiring.py.


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
    body = resp.json()
    assert resp.headers.get("content-type") == "application/problem+json"
    assert body["type"] == "urn:forge:error:bad-request"
    assert body["status"] == 400
    assert "team_id" in body["detail"]


def test_unknown_resource_id_returns_404(forge_url: str) -> None:
    resp = httpx.get(
        f"{forge_url}/v1/resources/00000000-0000-0000-0000-000000000000",
        headers=AUTH,
    )
    assert resp.status_code == 404
    body = resp.json()
    assert resp.headers.get("content-type") == "application/problem+json"
    assert body["type"] == "urn:forge:error:resource-not-found"
    assert body["status"] == 404


def test_list_requires_auth(forge_url: str) -> None:
    resp = httpx.get(f"{forge_url}/v1/resources")
    assert resp.status_code == 401
