"""E.1 wiring proof: POST -> Celery enqueue -> worker -> status `provisioned`.

Requires the compose stack to include `redis` and `worker` services. The
worker has no Terraform yet — it only flips the RESOURCE_REQUEST.status
column. This test catches a broken broker URL, a missing worker service,
or a regression in the enqueue path.
"""

import time

import httpx

BOB_KEY = "crp_87435518f7b581136434fcf3af2bad34"
AUTH = {"Authorization": f"Bearer {BOB_KEY}"}


def _poll_until_provisioned(forge_url: str, resource_id: str, timeout: float = 15.0) -> str:
    """Poll the status endpoint until the worker reaches `provisioned`.

    Returns the final status. Raises AssertionError on timeout.
    """
    deadline = time.monotonic() + timeout
    last_status = "<no response>"
    while time.monotonic() < deadline:
        resp = httpx.get(f"{forge_url}/v1/resources/{resource_id}/status", headers=AUTH)
        assert resp.status_code == 200, resp.text
        last_status = resp.json()["status"]
        if last_status == "provisioned":
            return last_status
        if last_status == "failed":
            # Surface immediately — don't waste the rest of the timeout.
            return last_status
        time.sleep(0.25)
    raise AssertionError(f"Resource {resource_id} did not reach 'provisioned' in {timeout}s (last: {last_status})")


def test_worker_drives_status_to_provisioned(forge_url: str) -> None:
    payload = {
        "resource_type": "managed_database",
        "tier": "tier2",
        "logical_region": "ngx-region-1a",
        "name": "wiring-proof-db",
        "config": {"engine": "postgres", "size": "small"},
    }

    create = httpx.post(f"{forge_url}/v1/resources", json=payload, headers=AUTH)
    assert create.status_code == 202, create.text
    resource_id = create.json()["resource_id"]
    # POST response is built before the task runs — always `pending`.
    assert create.json()["status"] == "pending"

    final_status = _poll_until_provisioned(forge_url, resource_id)
    assert final_status == "provisioned"


def test_worker_idempotent_under_repeat_enqueue(forge_url: str) -> None:
    """Defensive: a second create on the same name should still drive to provisioned.

    Each POST creates a distinct resource_id (the uniqueness constraint is on
    (team_id, name)), so we use distinct names to exercise the path twice.
    """
    for suffix in ("a", "b"):
        payload = {
            "resource_type": "managed_database",
            "tier": "tier2",
            "logical_region": "ngx-region-1a",
            "name": f"wiring-repeat-{suffix}",
            "config": {"engine": "postgres", "size": "small"},
        }
        resp = httpx.post(f"{forge_url}/v1/resources", json=payload, headers=AUTH)
        assert resp.status_code == 202, resp.text
        rid = resp.json()["resource_id"]
        assert _poll_until_provisioned(forge_url, rid) == "provisioned"
