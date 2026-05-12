import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import bcrypt
import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from forge.api.auth import (
    AuthContext,
    _extract_bearer,
    _verify_key,
    require_admin,
    require_auth,
)
from forge.api.deps import get_db_session
from forge.main import get_app
from forge.models import AppUser


def _make_request(authorization: str | None) -> Request:
    request = MagicMock(spec=Request)
    request.headers = {} if authorization is None else {"authorization": authorization}
    return request


def test_extract_bearer_missing_header_raises_401() -> None:
    with pytest.raises(HTTPException) as exc:
        _extract_bearer(_make_request(None))
    assert exc.value.status_code == 401


def test_extract_bearer_wrong_scheme_raises_401() -> None:
    with pytest.raises(HTTPException) as exc:
        _extract_bearer(_make_request("Basic abc123"))
    assert exc.value.status_code == 401


def test_extract_bearer_empty_token_raises_401() -> None:
    with pytest.raises(HTTPException) as exc:
        _extract_bearer(_make_request("Bearer "))
    assert exc.value.status_code == 401


def test_extract_bearer_returns_token() -> None:
    assert _extract_bearer(_make_request("Bearer crp_dev_abc")) == "crp_dev_abc"


def test_extract_bearer_is_case_insensitive_for_scheme() -> None:
    assert _extract_bearer(_make_request("bearer crp_dev_abc")) == "crp_dev_abc"


def _fake_user(api_key: str, role: str = "member") -> AppUser:
    user = AppUser(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        first_name="Test",
        last_name="User",
        email="test@example.com",
        api_key_hash=bcrypt.hashpw(api_key.encode(), bcrypt.gensalt(rounds=4)).decode(),
        role=role,
    )
    return user


def test_verify_key_returns_matching_user() -> None:
    user = _fake_user("crp_dev_secret")
    session = MagicMock()
    session.query.return_value.all.return_value = [user]
    assert _verify_key(session, "crp_dev_secret") is user


def test_verify_key_returns_none_when_no_match() -> None:
    session = MagicMock()
    session.query.return_value.all.return_value = [_fake_user("crp_dev_other")]
    assert _verify_key(session, "crp_dev_wrong") is None


def test_verify_key_returns_none_when_table_empty() -> None:
    session = MagicMock()
    session.query.return_value.all.return_value = []
    assert _verify_key(session, "anything") is None


# ---------- FastAPI-integrated tests via dependency overrides ----------


@pytest.fixture
def app_with_session_override() -> TestClient:
    """TestClient with a controllable in-memory user list.

    Yields a (client, users) tuple so each test can register seeded users for
    the fake session to return from session.query(AppUser).all().
    """
    app = get_app()
    users: list[AppUser] = []
    fake_session = MagicMock()
    fake_session.query.return_value.all = lambda: users
    fake_session.commit = MagicMock()
    app.dependency_overrides[get_db_session] = lambda: fake_session
    client = TestClient(app)
    client.users = users  # type: ignore[attr-defined]
    return client


def test_me_without_auth_header_returns_401(app_with_session_override: TestClient) -> None:
    response = app_with_session_override.get("/v1/me")
    assert response.status_code == 401


def test_me_with_invalid_key_returns_401(app_with_session_override: TestClient) -> None:
    response = app_with_session_override.get("/v1/me", headers={"Authorization": "Bearer crp_dev_nope"})
    assert response.status_code == 401


def test_me_with_valid_key_returns_user_payload() -> None:
    app = get_app()
    user = _fake_user("crp_dev_alice", role="admin")
    user.first_name = "Alice"
    user.last_name = "Admin"
    user.email = "alice@example.com"
    # /v1/me reads user.team — attach a minimal stand-in
    team = MagicMock()
    team.id = uuid.uuid4()
    team.name = "Platform Team"
    user.team = team  # type: ignore[assignment]

    fake_session = MagicMock()
    fake_session.query.return_value.all = lambda: [user]
    fake_session.commit = MagicMock()
    app.dependency_overrides[get_db_session] = lambda: fake_session

    client = TestClient(app)
    response = client.get("/v1/me", headers={"Authorization": "Bearer crp_dev_alice"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["email"] == "alice@example.com"
    assert body["role"] == "admin"
    assert body["team"]["name"] == "Platform Team"
    fake_session.commit.assert_called()  # last_seen_at write


def test_require_admin_rejects_member_role() -> None:
    user = _fake_user("k", role="member")
    ctx = AuthContext(user=user, team_id=user.team_id)
    with pytest.raises(HTTPException) as exc:
        require_admin(ctx)
    assert exc.value.status_code == 403


def test_require_admin_allows_admin_role() -> None:
    user = _fake_user("k", role="admin")
    ctx = AuthContext(user=user, team_id=user.team_id)
    assert require_admin(ctx) is ctx


def test_require_auth_updates_last_seen() -> None:
    user = _fake_user("crp_dev_x")
    before = datetime.now(timezone.utc)

    session = MagicMock()
    session.query.return_value.all.return_value = [user]

    request = _make_request("Bearer crp_dev_x")
    ctx = require_auth(request=request, session=session)
    assert ctx.user is user
    assert user.last_seen_at is not None
    assert user.last_seen_at >= before
    session.commit.assert_called_once()
