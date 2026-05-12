"""Unit tests for /v1/resources endpoints (POST + GET + list + status)."""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from forge.api.auth import AuthContext, require_auth
from forge.api.deps import get_db_session
from forge.main import get_app
from forge.models.catalog import ResourceType, ResourceTypeTierConstraint, TierPolicy, TierRegionMember
from forge.models.identity import AppUser, Team
from forge.models.provisioning import ResourceRequest
from forge.models.topology import LogicalRegion

pytestmark = pytest.mark.unit

_TEAM_ID = uuid.uuid4()
_USER_ID = uuid.uuid4()
_OTHER_TEAM_ID = uuid.uuid4()


def _fake_auth() -> AuthContext:
    team = MagicMock(spec=Team)
    team.id = _TEAM_ID
    user = MagicMock(spec=AppUser)
    user.id = _USER_ID
    user.team = team
    user.team_id = _TEAM_ID
    return AuthContext(user=user, team_id=_TEAM_ID)


def _make_resource_type(**kwargs) -> MagicMock:
    rt = MagicMock(spec=ResourceType)
    rt.id = kwargs.get("id", uuid.uuid4())
    rt.name = kwargs.get("name", "managed_database")
    rt.version = kwargs.get("version", 1)
    rt.base_config_schema = kwargs.get(
        "base_config_schema",
        {
            "type": "object",
            "properties": {"size": {"type": "string", "enum": ["small", "large"]}},
            "required": ["size"],
        },
    )
    rt.active = True
    rt.latest = True
    return rt


def _make_tier(**kwargs) -> MagicMock:
    t = MagicMock(spec=TierPolicy)
    t.id = kwargs.get("id", uuid.uuid4())
    t.tier_name = kwargs.get("tier_name", "tier1")
    return t


def _make_region(**kwargs) -> MagicMock:
    r = MagicMock(spec=LogicalRegion)
    r.id = kwargs.get("id", uuid.uuid4())
    r.name = kwargs.get("name", "ngx-region-1a")
    r.active = True
    r.platform_assigned_only = False
    return r


def _make_resource_request(**kwargs) -> MagicMock:
    rr = MagicMock(spec=ResourceRequest)
    rr.id = kwargs.get("id", uuid.uuid4())
    rr.team_id = kwargs.get("team_id", _TEAM_ID)
    rr.requested_by = kwargs.get("requested_by", _USER_ID)
    rr.name = kwargs.get("name", "my-db")
    rr.status = kwargs.get("status", "pending")
    rr.config = kwargs.get("config", {"size": "small"})
    rr.created_at = kwargs.get("created_at", datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc))
    rr.updated_at = kwargs.get("updated_at", datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc))
    rr.scheduled_destroy_at = None
    rr.resource_type = kwargs.get("resource_type", _make_resource_type())
    rr.tier_policy = kwargs.get("tier_policy", _make_tier())
    rr.logical_region = kwargs.get("logical_region", _make_region())
    return rr


def _client_with_session(session: MagicMock) -> TestClient:
    app = get_app()
    app.dependency_overrides[require_auth] = _fake_auth
    app.dependency_overrides[get_db_session] = lambda: session
    return TestClient(app)


def _session_for_create(
    rt: MagicMock | None = None,
    tier: MagicMock | None = None,
    region: MagicMock | None = None,
    constraint: MagicMock | None = None,
    captured: list | None = None,
    tier_region_eligible: bool = True,
) -> MagicMock:
    """Build a MagicMock session that handles the POST handler's query chain."""
    session = MagicMock()

    eligible_member = MagicMock(spec=TierRegionMember) if tier_region_eligible else None

    def query_side_effect(model):
        q = MagicMock()
        if model is ResourceType:
            q.filter.return_value.filter.return_value.first.return_value = rt
            q.filter.return_value.first.return_value = rt  # version specified path
        elif model is TierPolicy:
            q.filter.return_value.first.return_value = tier
        elif model is LogicalRegion:
            q.filter.return_value.first.return_value = region
        elif model is TierRegionMember:
            q.filter_by.return_value.first.return_value = eligible_member
        elif model is ResourceTypeTierConstraint:
            q.filter_by.return_value.first.return_value = constraint
        return q

    session.query.side_effect = query_side_effect

    def add(obj):
        if captured is not None:
            captured.append(obj)

    def refresh(obj):
        # Simulate DB-side defaults populated on commit.
        if not getattr(obj, "id", None):
            obj.id = uuid.uuid4()
        if not getattr(obj, "created_at", None):
            obj.created_at = datetime.now(timezone.utc)
        if not getattr(obj, "updated_at", None):
            obj.updated_at = obj.created_at

    session.add.side_effect = add
    session.refresh.side_effect = refresh
    return session


VALID_BODY = {
    "resource_type": "managed_database",
    "tier": "tier1",
    "logical_region": "ngx-region-1a",
    "name": "my-db",
    "config": {"size": "small"},
}


# ── POST /v1/resources ────────────────────────────────────────────────────────


class TestCreateResource:
    def test_happy_path_returns_202(self):
        session = _session_for_create(_make_resource_type(), _make_tier(), _make_region())
        resp = _client_with_session(session).post("/v1/resources", json=VALID_BODY)
        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "pending"
        assert "resource_id" in body
        assert body["poll_url"].endswith(f"/v1/resources/{body['resource_id']}/status")
        assert "created_at" in body

    def test_persists_with_server_assigned_team_id(self):
        captured: list[ResourceRequest] = []
        session = _session_for_create(_make_resource_type(), _make_tier(), _make_region(), captured=captured)
        _client_with_session(session).post("/v1/resources", json=VALID_BODY)
        assert len(captured) == 1
        rr = captured[0]
        assert rr.team_id == _TEAM_ID
        assert rr.requested_by == _USER_ID
        assert rr.status == "pending"

    def test_rejects_team_id_in_body_with_400(self):
        session = _session_for_create(_make_resource_type(), _make_tier(), _make_region())
        body = {**VALID_BODY, "team_id": str(_OTHER_TEAM_ID)}
        resp = _client_with_session(session).post("/v1/resources", json=body)
        assert resp.status_code == 400
        assert "team_id" in resp.json()["detail"]

    def test_rejects_unknown_field_with_422(self):
        session = _session_for_create(_make_resource_type(), _make_tier(), _make_region())
        body = {**VALID_BODY, "unknown_field": "value"}
        resp = _client_with_session(session).post("/v1/resources", json=body)
        assert resp.status_code == 422

    def test_invalid_config_returns_422(self):
        session = _session_for_create(_make_resource_type(), _make_tier(), _make_region())
        body = {**VALID_BODY, "config": {"size": "huge"}}  # not in enum
        resp = _client_with_session(session).post("/v1/resources", json=body)
        assert resp.status_code == 422
        assert "config validation failed" in resp.json()["detail"]

    def test_missing_required_config_field_returns_422(self):
        session = _session_for_create(_make_resource_type(), _make_tier(), _make_region())
        body = {**VALID_BODY, "config": {}}  # missing 'size'
        resp = _client_with_session(session).post("/v1/resources", json=body)
        assert resp.status_code == 422

    def test_unknown_resource_type_returns_404(self):
        session = _session_for_create(rt=None, tier=_make_tier(), region=_make_region())
        resp = _client_with_session(session).post("/v1/resources", json=VALID_BODY)
        assert resp.status_code == 404
        assert "Resource type" in resp.json()["detail"]

    def test_unknown_tier_returns_404(self):
        session = _session_for_create(rt=_make_resource_type(), tier=None, region=_make_region())
        resp = _client_with_session(session).post("/v1/resources", json=VALID_BODY)
        assert resp.status_code == 404
        assert "Tier" in resp.json()["detail"]

    def test_unknown_region_returns_404(self):
        session = _session_for_create(rt=_make_resource_type(), tier=_make_tier(), region=None)
        resp = _client_with_session(session).post("/v1/resources", json=VALID_BODY)
        assert resp.status_code == 404
        assert "region" in resp.json()["detail"]

    def test_constraint_override_applied(self):
        """When a tier constraint exists, it must replace base schema keys."""
        rt = _make_resource_type(
            base_config_schema={
                "type": "object",
                "properties": {"size": {"type": "string", "enum": ["small", "large"]}},
                "required": ["size"],
            }
        )
        constraint = MagicMock(spec=ResourceTypeTierConstraint)
        constraint.config_schema_override = {
            "properties": {"size": {"type": "string", "enum": ["large"]}},  # only "large"
        }
        session = _session_for_create(rt, _make_tier(), _make_region(), constraint=constraint)
        # "small" was valid in base, but constraint restricts to "large"
        resp = _client_with_session(session).post("/v1/resources", json={**VALID_BODY, "config": {"size": "small"}})
        assert resp.status_code == 422

    def test_ineligible_tier_region_combination_returns_404(self):
        session = _session_for_create(_make_resource_type(), _make_tier(), _make_region(), tier_region_eligible=False)
        resp = _client_with_session(session).post("/v1/resources", json=VALID_BODY)
        assert resp.status_code == 404
        assert "not available for tier" in resp.json()["detail"]

    def test_returns_401_without_auth(self):
        app = get_app()
        resp = TestClient(app).post("/v1/resources", json=VALID_BODY)
        assert resp.status_code == 401


# ── GET /v1/resources ─────────────────────────────────────────────────────────


def _list_session(rows: list = [], total: int | None = None) -> MagicMock:
    """Build a MagicMock session for the list endpoint (handles joinedload options chain)."""
    session = MagicMock()
    q = MagicMock()
    session.query.return_value = q
    q.options.return_value = q
    q.filter.return_value = q
    q.join.return_value = q
    q.order_by.return_value = q
    q.offset.return_value = q
    q.limit.return_value = q
    q.count.return_value = total if total is not None else len(rows)
    q.all.return_value = rows
    return session


class TestListResources:
    def test_list_filters_by_team_id(self):
        session = _list_session()
        q = session.query.return_value

        _client_with_session(session).get("/v1/resources")

        # First filter call must be the team_id filter
        first_filter_args = q.filter.call_args_list[0].args
        assert any(_TEAM_ID == _extract_uuid_from_clause(c) for c in first_filter_args)

    def test_returns_pagination_envelope(self):
        rr = _make_resource_request()
        session = _list_session(rows=[rr], total=1)

        resp = _client_with_session(session).get("/v1/resources")
        assert resp.status_code == 200
        body = resp.json()
        assert body["page"] == 1
        assert body["limit"] == 50
        assert body["total"] == 1
        assert len(body["items"]) == 1
        assert body["items"][0]["resource_id"] == str(rr.id)

    def test_invalid_status_returns_422(self):
        session = _list_session()
        resp = _client_with_session(session).get("/v1/resources?status=pendng")
        assert resp.status_code == 422

    def test_limit_above_max_returns_422(self):
        session = _list_session()
        resp = _client_with_session(session).get("/v1/resources?limit=300")
        assert resp.status_code == 422

    def test_page_below_one_returns_422(self):
        session = _list_session()
        resp = _client_with_session(session).get("/v1/resources?page=0")
        assert resp.status_code == 422

    def test_returns_401_without_auth(self):
        app = get_app()
        resp = TestClient(app).get("/v1/resources")
        assert resp.status_code == 401

    def test_items_exclude_embargoed_fields(self):
        rr = _make_resource_request()
        session = _list_session(rows=[rr], total=1)
        items = _client_with_session(session).get("/v1/resources").json()["items"]
        assert len(items) == 1
        for forbidden in ("provider", "physical_region", "tf_workspace_id", "tf_state_key", "outputs_encrypted"):
            assert forbidden not in items[0], f"Embargoed field '{forbidden}' leaked into list item"


# ── GET /v1/resources/{id} ────────────────────────────────────────────────────


class TestGetResource:
    def test_returns_detail(self):
        rr = _make_resource_request()
        session = MagicMock()
        session.query.return_value.filter.return_value.first.return_value = rr
        resp = _client_with_session(session).get(f"/v1/resources/{rr.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["resource_id"] == str(rr.id)
        assert body["status"] == "pending"
        assert body["resource_type"] == "managed_database"
        assert body["tier"] == "tier1"
        assert body["logical_region"] == "ngx-region-1a"

    def test_response_excludes_embargoed_fields(self):
        rr = _make_resource_request()
        session = MagicMock()
        session.query.return_value.filter.return_value.first.return_value = rr
        body = _client_with_session(session).get(f"/v1/resources/{rr.id}").json()
        for forbidden in ("provider", "physical_region", "tf_workspace_id", "tf_state_key", "outputs_encrypted"):
            assert forbidden not in body, f"Embargoed field '{forbidden}' leaked into response"

    def test_cross_team_uuid_returns_404_not_403(self):
        session = MagicMock()
        session.query.return_value.filter.return_value.first.return_value = None
        resp = _client_with_session(session).get(f"/v1/resources/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_returns_401_without_auth(self):
        app = get_app()
        resp = TestClient(app).get(f"/v1/resources/{uuid.uuid4()}")
        assert resp.status_code == 401


# ── GET /v1/resources/{id}/status ─────────────────────────────────────────────


class TestGetResourceStatus:
    def test_returns_status(self):
        rr = _make_resource_request()
        session = MagicMock()
        session.query.return_value.filter.return_value.first.return_value = rr
        resp = _client_with_session(session).get(f"/v1/resources/{rr.id}/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["resource_id"] == str(rr.id)
        assert body["status"] == "pending"
        assert "updated_at" in body

    def test_cross_team_uuid_returns_404(self):
        session = MagicMock()
        session.query.return_value.filter.return_value.first.return_value = None
        resp = _client_with_session(session).get(f"/v1/resources/{uuid.uuid4()}/status")
        assert resp.status_code == 404

    def test_returns_401_without_auth(self):
        app = get_app()
        resp = TestClient(app).get(f"/v1/resources/{uuid.uuid4()}/status")
        assert resp.status_code == 401


# ── Helpers ───────────────────────────────────────────────────────────────────


def _extract_uuid_from_clause(clause) -> uuid.UUID | None:
    """Pull a literal UUID value out of a SQLAlchemy BinaryExpression for assertions."""
    try:
        right = getattr(clause, "right", None)
        if right is not None and hasattr(right, "value"):
            val = right.value
            if isinstance(val, uuid.UUID):
                return val
    except Exception:
        return None
    return None
