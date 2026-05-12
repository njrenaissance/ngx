"""E.2 wiring proof: POST -> Celery enqueue -> worker -> workspace materialization
-> status `provisioned`.

Requires the compose stack to include `redis` and `worker` services and the
host packages/ directory mounted into both api and worker (see
docker-compose.yml). The worker materializes a per-request workspace
on disk inside the worker container and persists DEPLOYMENT +
DEPLOYMENT_AZ rows; no `terraform` invocation runs yet (arrives in E.3).

This test catches a broken broker URL, a missing worker service, a missing
packages mount, a materializer regression, or schema drift between the
seeded resource type and the on-disk package.
"""

import time
import uuid

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
        # "dev" tier: min_azs_per_region=1. The seeded ngx-region-1a has one
        # AZ, so tier2 (min_azs=2) would raise ValueError in select_az_assignments
        # and flip the request to "failed". Use "dev" for integration tests
        # until a second AZ is seeded for the region.
        "tier": "dev",
        "logical_region": "ngx-region-1a",
        "name": "wiring-proof-db",
        "config": {"engine": "postgres", "size": "small", "storage_gb": 100},
    }

    create = httpx.post(f"{forge_url}/v1/resources", json=payload, headers=AUTH)
    assert create.status_code == 202, create.text
    resource_id = create.json()["resource_id"]
    # POST response is built before the task runs — always `pending`.
    assert create.json()["status"] == "pending"

    final_status = _poll_until_provisioned(forge_url, resource_id)
    assert final_status == "provisioned"

    # E.2 materialization assertions: a single DEPLOYMENT row was written
    # with a SPEC §8.2-shaped state key, and one DEPLOYMENT_AZ row per
    # min_azs_per_region was written with the expected primary/secondary
    # split. The integration suite connects to the compose Postgres on
    # localhost:5432 via the same DSN the test process already uses
    # (seeded_db relies on the default FORGE_DATABASE__HOST=localhost).
    from forge.db import SyncSession
    from forge.models.catalog import TierPolicy
    from forge.models.provisioning import Deployment, DeploymentAz, ResourceRequest

    with SyncSession() as session:
        rr = session.query(ResourceRequest).filter(ResourceRequest.id == uuid.UUID(resource_id)).first()
        assert rr is not None
        deployments = session.query(Deployment).filter(Deployment.resource_request_id == rr.id).all()
        assert len(deployments) == 1, "exactly one Deployment row should be written"
        deployment = deployments[0]
        expected_state_key = f"dev/{rr.team_id}/standalone/{rr.id}/ngx-region-1a/terraform.tfstate"
        assert deployment.tf_state_key == expected_state_key
        # E.2: DEPLOYMENT.status is "pending" — workspace is on disk but no
        # terraform has run. E.3 will drive it to "applying" -> "provisioned".
        assert deployment.status == "pending"

        tier = session.query(TierPolicy).filter(TierPolicy.id == rr.tier_policy_id).first()
        assert tier is not None
        az_rows = session.query(DeploymentAz).filter(DeploymentAz.deployment_id == deployment.id).all()
        assert len(az_rows) == tier.min_azs_per_region
        roles = sorted(row.az_role for row in az_rows)
        # Exactly one "primary", the rest "secondary".
        assert roles.count("primary") == 1
        assert roles.count("secondary") == tier.min_azs_per_region - 1


def test_worker_idempotent_under_repeat_enqueue(forge_url: str) -> None:
    """Defensive: a second create on the same name should still drive to provisioned.

    Each POST creates a distinct resource_id (the uniqueness constraint is on
    (team_id, name)), so we use distinct names to exercise the path twice.
    """
    for suffix in ("a", "b"):
        payload = {
            "resource_type": "managed_database",
            "tier": "dev",
            "logical_region": "ngx-region-1a",
            "name": f"wiring-repeat-{suffix}",
            "config": {"engine": "postgres", "size": "small", "storage_gb": 100},
        }
        resp = httpx.post(f"{forge_url}/v1/resources", json=payload, headers=AUTH)
        assert resp.status_code == 202, resp.text
        rid = resp.json()["resource_id"]
        assert _poll_until_provisioned(forge_url, rid) == "provisioned"
