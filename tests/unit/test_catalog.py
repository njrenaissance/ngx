"""Unit tests for catalog read endpoints and response model field rules."""

import uuid
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from forge.api.auth import AuthContext
from forge.api.deps import get_db_session
from forge.api.schemas.catalog import RegionResponse, TierResponse
from forge.main import get_app
from forge.models.catalog import ResourceType, ResourceTypeTierConstraint, TierPolicy
from forge.models.identity import AppUser, Team
from forge.models.topology import LogicalRegion

pytestmark = pytest.mark.unit

_TEAM_ID = uuid.uuid4()
_USER_ID = uuid.uuid4()


def _fake_auth() -> AuthContext:
    team = MagicMock(spec=Team)
    team.id = _TEAM_ID
    user = MagicMock(spec=AppUser)
    user.id = _USER_ID
    user.team = team
    user.team_id = _TEAM_ID
    return AuthContext(user=user, team_id=_TEAM_ID)


def _make_region(**kwargs) -> MagicMock:
    r = MagicMock(spec=LogicalRegion)
    r.id = kwargs.get("id", uuid.uuid4())
    r.name = kwargs.get("name", "ngx-region-1a")
    r.label = kwargs.get("label", "NGX Region 1A")
    r.description = kwargs.get("description", "Primary region")
    r.provider = "aws"  # INTERNAL — should never appear in response
    r.physical_region = "us-east-1"  # INTERNAL
    r.jurisdiction = kwargs.get("jurisdiction", "US")
    r.platform_assigned_only = kwargs.get("platform_assigned_only", False)
    r.active = kwargs.get("active", True)
    return r


def _make_tier(**kwargs) -> MagicMock:
    t = MagicMock(spec=TierPolicy)
    t.id = kwargs.get("id", uuid.uuid4())
    t.tier_name = kwargs.get("tier_name", "tier1")
    t.label = kwargs.get("label", "Premium")
    t.sla_class = kwargs.get("sla_class", "99.99%")
    t.min_regions = kwargs.get("min_regions", 2)
    t.min_azs_per_region = 2  # should NOT appear in TierResponse
    t.auto_expire_days = kwargs.get("auto_expire_days", None)
    t.approval_required = kwargs.get("approval_required", False)
    return t


def _make_resource_type(**kwargs) -> MagicMock:
    rt = MagicMock(spec=ResourceType)
    rt.id = kwargs.get("id", uuid.uuid4())
    rt.name = kwargs.get("name", "managed_database")
    rt.version = kwargs.get("version", 1)
    rt.label = kwargs.get("label", "Managed Database")
    rt.description = kwargs.get("description", "A managed database")
    rt.base_config_schema = kwargs.get("base_config_schema", {"type": "object"})
    rt.terraform_variable_map = {}
    rt.active = kwargs.get("active", True)
    rt.latest = kwargs.get("latest", True)
    return rt


def _client_with_session(session: MagicMock) -> TestClient:
    app = get_app()
    from forge.api.auth import require_auth

    app.dependency_overrides[require_auth] = _fake_auth
    app.dependency_overrides[get_db_session] = lambda: session
    return TestClient(app)


# ── RegionResponse field rules ────────────────────────────────────────────────


class TestRegionResponseFields:
    def test_does_not_expose_provider(self):
        r = _make_region()
        resp = RegionResponse.model_validate(r)
        assert not hasattr(resp, "provider")

    def test_does_not_expose_physical_region(self):
        r = _make_region()
        resp = RegionResponse.model_validate(r)
        assert not hasattr(resp, "physical_region")

    def test_exposes_jurisdiction(self):
        r = _make_region(jurisdiction="EU")
        resp = RegionResponse.model_validate(r)
        assert resp.jurisdiction == "EU"

    def test_exposes_required_fields(self):
        r = _make_region()
        resp = RegionResponse.model_validate(r)
        assert resp.name == "ngx-region-1a"
        assert resp.label == "NGX Region 1A"
        assert resp.active is True


# ── TierResponse field rules ──────────────────────────────────────────────────


class TestTierResponseFields:
    def test_does_not_expose_min_azs_per_region(self):
        t = _make_tier()
        resp = TierResponse.model_validate(t)
        assert not hasattr(resp, "min_azs_per_region")

    def test_exposes_required_fields(self):
        t = _make_tier()
        resp = TierResponse.model_validate(t)
        assert resp.tier_name == "tier1"
        assert resp.sla_class == "99.99%"
        assert resp.min_regions == 2


# ── GET /v1/catalog/regions ───────────────────────────────────────────────────


class TestListRegions:
    def test_returns_200_with_active_consumer_regions(self):
        region = _make_region()
        session = MagicMock()
        session.query.return_value.filter.return_value.all.return_value = [region]
        resp = _client_with_session(session).get("/v1/catalog/regions")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_response_excludes_internal_fields(self):
        region = _make_region()
        session = MagicMock()
        session.query.return_value.filter.return_value.all.return_value = [region]
        body = _client_with_session(session).get("/v1/catalog/regions").json()
        assert "provider" not in body[0]
        assert "physical_region" not in body[0]

    def test_returns_401_without_auth(self):
        app = get_app()
        resp = TestClient(app).get("/v1/catalog/regions")
        assert resp.status_code == 401


# ── GET /v1/catalog/tiers ─────────────────────────────────────────────────────


class TestListTiers:
    def test_returns_200(self):
        tier = _make_tier()
        session = MagicMock()
        session.query.return_value.order_by.return_value.all.return_value = [tier]
        resp = _client_with_session(session).get("/v1/catalog/tiers")
        assert resp.status_code == 200

    def test_response_excludes_min_azs_per_region(self):
        tier = _make_tier()
        session = MagicMock()
        session.query.return_value.order_by.return_value.all.return_value = [tier]
        body = _client_with_session(session).get("/v1/catalog/tiers").json()
        assert "min_azs_per_region" not in body[0]

    def test_returns_401_without_auth(self):
        app = get_app()
        resp = TestClient(app).get("/v1/catalog/tiers")
        assert resp.status_code == 401


# ── GET /v1/catalog/resource-types ───────────────────────────────────────────


class TestListResourceTypes:
    def test_returns_200(self):
        rt = _make_resource_type()
        session = MagicMock()
        session.query.return_value.filter.return_value.all.return_value = [rt]
        resp = _client_with_session(session).get("/v1/catalog/resource-types")
        assert resp.status_code == 200
        assert resp.json()[0]["config_schema"] == {"type": "object"}

    def test_returns_401_without_auth(self):
        app = get_app()
        resp = TestClient(app).get("/v1/catalog/resource-types")
        assert resp.status_code == 401


# ── GET /v1/catalog/resource-types/{name} ────────────────────────────────────


class TestGetResourceType:
    def _session_for_rt(self, rt, tier=None, constraint=None):
        session = MagicMock()

        def query_side_effect(model):
            q = MagicMock()
            if model is ResourceType:
                q.filter.return_value.first.return_value = rt
            elif model is TierPolicy:
                q.filter.return_value.first.return_value = tier
            elif model is ResourceTypeTierConstraint:
                q.filter_by.return_value.first.return_value = constraint
            return q

        session.query.side_effect = query_side_effect
        return session

    def test_returns_base_schema_without_tier_param(self):
        rt = _make_resource_type(base_config_schema={"type": "object", "properties": {"size": {}}})
        session = self._session_for_rt(rt)
        resp = _client_with_session(session).get("/v1/catalog/resource-types/managed_database")
        assert resp.status_code == 200
        assert resp.json()["config_schema"] == rt.base_config_schema

    def test_merges_schema_when_constraint_exists(self):
        base = {"type": "object", "properties": {"size": {"enum": ["small", "large"]}}}
        override = {"properties": {"size": {"enum": ["large"]}}}
        rt = _make_resource_type(base_config_schema=base)
        tier = _make_tier(tier_name="tier1")
        constraint = MagicMock(spec=ResourceTypeTierConstraint)
        constraint.config_schema_override = override
        session = self._session_for_rt(rt, tier=tier, constraint=constraint)
        resp = _client_with_session(session).get("/v1/catalog/resource-types/managed_database?tier=tier1")
        assert resp.status_code == 200
        merged = resp.json()["config_schema"]
        assert merged["properties"]["size"] == {"enum": ["large"]}

    def test_returns_base_schema_when_no_constraint(self):
        base = {"type": "object"}
        rt = _make_resource_type(base_config_schema=base)
        tier = _make_tier(tier_name="dev")
        session = self._session_for_rt(rt, tier=tier, constraint=None)
        resp = _client_with_session(session).get("/v1/catalog/resource-types/managed_database?tier=dev")
        assert resp.status_code == 200
        assert resp.json()["config_schema"] == base

    def test_returns_404_for_unknown_resource_type(self):
        session = self._session_for_rt(rt=None)
        resp = _client_with_session(session).get("/v1/catalog/resource-types/nonexistent")
        assert resp.status_code == 404

    def test_returns_404_for_unknown_tier(self):
        rt = _make_resource_type()
        session = self._session_for_rt(rt, tier=None)
        resp = _client_with_session(session).get("/v1/catalog/resource-types/managed_database?tier=badtier")
        assert resp.status_code == 404

    def test_returns_401_without_auth(self):
        app = get_app()
        resp = TestClient(app).get("/v1/catalog/resource-types/managed_database")
        assert resp.status_code == 401
