"""Unit tests for materialize_workspace.

Pattern matches tests/unit/test_workers.py — MagicMock session standing in
for SyncSession, monkeypatch for module-level constants and config. Tmp
path holds both the source package layout and the destination workspace,
so no test pollutes /tmp on the runner.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from forge.workers import workspace as workspace_module
from forge.workers.workspace import (
    WorkspaceMaterializationError,
    materialize_workspace,
)

# ---------------------------------------------------------------------------
# Stubs — minimal stand-ins for the SQLAlchemy rows the materializer reads.
# We mock the session itself; the only reason these exist is to give the
# materializer something with the right attribute names.
# ---------------------------------------------------------------------------


class _ResourceTypeStub:
    def __init__(
        self,
        name: str = "managed_database",
        version: int = 1,
        terraform_variable_map: dict[str, str] | None = None,
    ) -> None:
        self.id = uuid.uuid4()
        self.name = name
        self.version = version
        self.terraform_variable_map = terraform_variable_map or {
            "engine": "db_engine",
            "size": "db_size",
            "storage_gb": "db_storage_gb",
        }


class _TierPolicyStub:
    def __init__(self, min_azs_per_region: int = 1) -> None:
        self.id = uuid.uuid4()
        self.min_azs_per_region = min_azs_per_region


class _LogicalRegionStub:
    def __init__(self, name: str = "ngx-region-1a") -> None:
        self.id = uuid.uuid4()
        self.name = name


class _AzStub:
    def __init__(self, az_index: int, active: bool = True) -> None:
        self.id = uuid.uuid4()
        self.az_index = az_index
        self.active = active


class _ResourceRequestStub:
    def __init__(
        self,
        config: dict[str, Any] | None = None,
        resource_type_id: uuid.UUID | None = None,
        tier_policy_id: uuid.UUID | None = None,
        logical_region_id: uuid.UUID | None = None,
        team_id: uuid.UUID | None = None,
    ) -> None:
        self.id = uuid.uuid4()
        self.team_id = team_id or uuid.uuid4()
        self.resource_type_id = resource_type_id or uuid.uuid4()
        self.tier_policy_id = tier_policy_id or uuid.uuid4()
        self.logical_region_id = logical_region_id or uuid.uuid4()
        self.config = config or {"engine": "postgres", "size": "small", "storage_gb": 100}


# ---------------------------------------------------------------------------
# Test fixtures.
# ---------------------------------------------------------------------------


@pytest.fixture
def packages_dir(tmp_path: Path) -> Path:
    """Lay down a minimal managed_database/v1/terraform package on disk."""
    pkg = tmp_path / "packages" / "managed_database" / "v1" / "terraform"
    pkg.mkdir(parents=True)
    (pkg / "variables.tf").write_text('variable "db_engine" { type = string }\n')
    (pkg / "outputs.tf").write_text('output "connection_host" { value = null }\n')
    (pkg / "aws").mkdir()
    (pkg / "aws" / "main.tf").write_text("# stub\n")
    return tmp_path / "packages"


@pytest.fixture
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point WORKSPACE_ROOT at a tmpdir so tests don't write to /tmp."""
    root = tmp_path / "forge-workspaces"
    monkeypatch.setattr(workspace_module, "WORKSPACE_ROOT", root)
    return root


@pytest.fixture
def patched_settings(monkeypatch: pytest.MonkeyPatch, packages_dir: Path) -> None:
    """Override settings.terraform.* and ENVIRONMENT in the workspace module's
    namespace. The materializer reads `settings.ENVIRONMENT` and
    `settings.terraform.*` directly, so we patch the module-bound `settings`."""
    fake = MagicMock()
    fake.ENVIRONMENT = "dev"
    fake.terraform.MANAGED_RESOURCES_BUCKET = "test-bucket"
    fake.terraform.MANAGED_RESOURCES_REGION = "us-east-1"
    fake.terraform.PACKAGES_DIR = str(packages_dir)
    monkeypatch.setattr(workspace_module, "settings", fake)


def _build_session(
    resource_type: _ResourceTypeStub,
    tier_policy: _TierPolicyStub,
    logical_region: _LogicalRegionStub,
    az_maps: list[_AzStub],
) -> MagicMock:
    """Construct a MagicMock session that returns the right row for each query.

    The materializer issues four queries: ResourceType, TierPolicy,
    LogicalRegion, RegionAzMap (.all()). We route by the model class passed
    to session.query() so the mock stays declarative.
    """
    added: list[Any] = []
    existing_deployment: list[Any] = []  # mutable cell so re-entry test can flip it

    from forge.models.catalog import ResourceType, TierPolicy
    from forge.models.provisioning import Deployment, DeploymentAz
    from forge.models.topology import LogicalRegion, RegionAzMap

    def query_side_effect(model: Any) -> MagicMock:
        q = MagicMock()
        if model is ResourceType:
            q.filter.return_value.first.return_value = resource_type
        elif model is TierPolicy:
            q.filter.return_value.first.return_value = tier_policy
        elif model is LogicalRegion:
            q.filter.return_value.first.return_value = logical_region
        elif model is RegionAzMap:
            q.filter.return_value.all.return_value = az_maps
        elif model is Deployment:
            # Returns whatever's currently in the existing_deployment cell —
            # idempotent tests overwrite this between calls.
            q.filter.return_value.first.return_value = existing_deployment[0] if existing_deployment else None
        elif model is DeploymentAz:
            # Returns None — DeploymentAz upsert path always adds on first run.
            # The re-entry test overrides this via its own session.
            q.filter.return_value.first.return_value = None
        return q

    session = MagicMock()
    session.query.side_effect = query_side_effect

    def add_side_effect(obj: Any) -> None:
        added.append(obj)
        # Simulate session.flush() populating deployment.id without a DB.
        if isinstance(obj, Deployment) and obj.id is None:
            obj.id = uuid.uuid4()

    session.add.side_effect = add_side_effect

    def flush_side_effect() -> None:
        # Mirror SQLAlchemy: a Deployment that doesn't yet have an id (because
        # it was constructed without one) gets a uuid assigned here.
        for obj in added:
            if isinstance(obj, Deployment) and obj.id is None:
                obj.id = uuid.uuid4()

    session.flush.side_effect = flush_side_effect
    session._added = added  # type: ignore[attr-defined]
    session._existing_deployment_cell = existing_deployment  # type: ignore[attr-defined]
    return session


# ---------------------------------------------------------------------------
# Tests.
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("workspace_root", "patched_settings")
class TestMaterializeWorkspaceHappyPath:
    def test_tfvars_keys_mapped_through_variable_map(self) -> None:
        rt = _ResourceTypeStub()
        rr = _ResourceRequestStub(
            config={"engine": "postgres", "size": "small", "storage_gb": 100},
            resource_type_id=rt.id,
        )
        session = _build_session(rt, _TierPolicyStub(min_azs_per_region=1), _LogicalRegionStub(), [_AzStub(1)])

        dest = materialize_workspace(session, rr)
        tfvars = json.loads((dest / "terraform.tfvars.json").read_text())
        # Keys are renamed via terraform_variable_map; values are preserved.
        assert tfvars == {"db_engine": "postgres", "db_size": "small", "db_storage_gb": 100}

    def test_backend_tf_contains_expected_state_key(self) -> None:
        rt = _ResourceTypeStub()
        region = _LogicalRegionStub(name="ngx-region-1a")
        rr = _ResourceRequestStub(resource_type_id=rt.id, logical_region_id=region.id)
        session = _build_session(rt, _TierPolicyStub(1), region, [_AzStub(1)])

        dest = materialize_workspace(session, rr)
        backend = (dest / "backend.tf").read_text()
        expected_key = f"dev/{rr.team_id}/standalone/{rr.id}/ngx-region-1a/terraform.tfstate"
        assert f'key    = "{expected_key}"' in backend
        assert 'bucket = "test-bucket"' in backend
        assert 'region = "us-east-1"' in backend

    def test_deployment_state_key_matches_spec_format(self) -> None:
        rt = _ResourceTypeStub()
        region = _LogicalRegionStub(name="ngx-region-1a")
        rr = _ResourceRequestStub(resource_type_id=rt.id, logical_region_id=region.id)
        session = _build_session(rt, _TierPolicyStub(1), region, [_AzStub(1)])

        materialize_workspace(session, rr)

        from forge.models.provisioning import Deployment

        deployments = [o for o in session._added if isinstance(o, Deployment)]
        assert len(deployments) == 1
        expected_key = f"dev/{rr.team_id}/standalone/{rr.id}/ngx-region-1a/terraform.tfstate"
        assert deployments[0].tf_state_key == expected_key
        # tf_workspace_id is filesystem-safe: state key minus trailing
        # /terraform.tfstate, with / -> __.
        assert deployments[0].tf_workspace_id == expected_key.removesuffix("/terraform.tfstate").replace("/", "__")
        assert deployments[0].status == "pending"  # E.2 placeholder; no terraform run yet

    def test_deployment_az_rows_split_by_role(self) -> None:
        rt = _ResourceTypeStub()
        rr = _ResourceRequestStub(resource_type_id=rt.id)
        az1 = _AzStub(1)
        az2 = _AzStub(2)
        session = _build_session(rt, _TierPolicyStub(2), _LogicalRegionStub(), [az2, az1])

        materialize_workspace(session, rr)

        from forge.models.provisioning import DeploymentAz

        az_rows = [o for o in session._added if isinstance(o, DeploymentAz)]
        assert len(az_rows) == 2
        roles_by_index = {next(a.az_index for a in [az1, az2] if a.id == row.az_map_id): row.az_role for row in az_rows}
        assert roles_by_index == {1: "primary", 2: "secondary"}

    def test_package_files_copied_into_workspace(self) -> None:
        rt = _ResourceTypeStub()
        rr = _ResourceRequestStub(resource_type_id=rt.id)
        session = _build_session(rt, _TierPolicyStub(1), _LogicalRegionStub(), [_AzStub(1)])

        dest = materialize_workspace(session, rr)
        assert (dest / "variables.tf").exists()
        assert (dest / "outputs.tf").exists()
        assert (dest / "aws" / "main.tf").exists()


@pytest.mark.usefixtures("workspace_root", "patched_settings")
class TestVarmapMismatch:
    def test_forward_violation_extra_config_key(self) -> None:
        """request.config has a key not present in terraform_variable_map."""
        rt = _ResourceTypeStub()
        rr = _ResourceRequestStub(
            config={"engine": "postgres", "size": "small", "storage_gb": 100, "extra": "x"},
            resource_type_id=rt.id,
        )
        session = _build_session(rt, _TierPolicyStub(1), _LogicalRegionStub(), [_AzStub(1)])

        with pytest.raises(WorkspaceMaterializationError, match="missing_in_varmap=\\['extra'\\]"):
            materialize_workspace(session, rr)
        session.commit.assert_not_called()

    def test_reverse_violation_missing_config_key(self) -> None:
        """terraform_variable_map has a key absent from request.config."""
        rt = _ResourceTypeStub()
        rr = _ResourceRequestStub(
            config={"engine": "postgres", "size": "small"},  # storage_gb missing
            resource_type_id=rt.id,
        )
        session = _build_session(rt, _TierPolicyStub(1), _LogicalRegionStub(), [_AzStub(1)])

        with pytest.raises(WorkspaceMaterializationError, match="missing_in_config=\\['storage_gb'\\]"):
            materialize_workspace(session, rr)
        session.commit.assert_not_called()


@pytest.mark.usefixtures("workspace_root", "patched_settings")
class TestIdempotentReentry:
    def test_second_call_reuses_deployment_and_skips_duplicate_az_rows(self) -> None:
        """Re-entry on the same request: same workspace path, no duplicate DEPLOYMENT,
        no duplicate DEPLOYMENT_AZ rows.

        Simulates the crash-and-redeliver path: first call inserts everything,
        second call (crash recovery) finds the existing rows and skips."""
        rt = _ResourceTypeStub()
        rr = _ResourceRequestStub(resource_type_id=rt.id)
        az = _AzStub(1)
        session = _build_session(rt, _TierPolicyStub(1), _LogicalRegionStub(), [az])

        # First call — creates everything.
        dest1 = materialize_workspace(session, rr)

        from forge.models.provisioning import Deployment, DeploymentAz

        first_deployments = [o for o in session._added if isinstance(o, Deployment)]
        first_az_rows = [o for o in session._added if isinstance(o, DeploymentAz)]
        assert len(first_deployments) == 1
        assert len(first_az_rows) == 1

        # Wire the existing deployment back through the query stub so the
        # second call's Deployment lookup finds it. Also flip the DeploymentAz
        # query stub to return the existing az row so the upsert skips.
        existing_dep = first_deployments[0]
        existing_az_row = first_az_rows[0]
        session._existing_deployment_cell.append(existing_dep)

        # Re-route DeploymentAz query to return the existing row this time.
        original_side_effect = session.query.side_effect

        def reentry_query(model: Any) -> MagicMock:
            q = original_side_effect(model)
            if model is DeploymentAz:
                q.filter.return_value.first.return_value = existing_az_row
            return q

        session.query.side_effect = reentry_query

        # Second call — should reuse, not duplicate.
        dest2 = materialize_workspace(session, rr)

        assert dest1 == dest2
        # No new Deployment or DeploymentAz rows added on the second call.
        all_deployments = [o for o in session._added if isinstance(o, Deployment)]
        all_az_rows = [o for o in session._added if isinstance(o, DeploymentAz)]
        assert len(all_deployments) == 1  # still just the first one
        assert len(all_az_rows) == 1


@pytest.mark.usefixtures("workspace_root", "patched_settings")
class TestMissingPackageDir:
    def test_missing_terraform_dir_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Override PACKAGES_DIR to a location that has no managed_database/v1 tree.
        empty = tmp_path / "empty-packages"
        empty.mkdir()
        fake = MagicMock()
        fake.ENVIRONMENT = "dev"
        fake.terraform.MANAGED_RESOURCES_BUCKET = "test-bucket"
        fake.terraform.MANAGED_RESOURCES_REGION = "us-east-1"
        fake.terraform.PACKAGES_DIR = str(empty)
        monkeypatch.setattr(workspace_module, "settings", fake)

        rt = _ResourceTypeStub()
        rr = _ResourceRequestStub(resource_type_id=rt.id)
        session = _build_session(rt, _TierPolicyStub(1), _LogicalRegionStub(), [_AzStub(1)])

        with pytest.raises(WorkspaceMaterializationError, match="package terraform dir not found"):
            materialize_workspace(session, rr)
