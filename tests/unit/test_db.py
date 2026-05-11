"""Unit tests for forge.db.readiness_check.

The function is tested in isolation by patching `sync_engine.connect` so we
don't need a live Postgres. A real-database assertion lives in the
integration suite (tests/integration/test_endpoints.py::test_readyz).
"""

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

from forge import db


def test_readiness_check_returns_ok_when_select_1_succeeds() -> None:
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_conn.__exit__.return_value = False

    with patch.object(db.sync_engine, "connect", return_value=fake_conn):
        ok, detail = db.readiness_check()

    assert ok is True
    assert detail == "ok"
    fake_conn.execute.assert_called_once()


def test_readiness_check_returns_failure_on_socket_error() -> None:
    """OSError covers the underlying socket failures (refused, reset, DNS)."""
    with patch.object(
        db.sync_engine,
        "connect",
        side_effect=OSError("connection refused"),
    ):
        ok, detail = db.readiness_check()

    assert ok is False
    assert "connection refused" in detail


def test_readiness_check_returns_failure_on_sqlalchemy_error() -> None:
    """SQLAlchemyError covers driver/query failures from psycopg2 et al."""
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_conn.__exit__.return_value = False
    fake_conn.execute.side_effect = OperationalError("SELECT 1", None, Exception("boom"))

    with patch.object(db.sync_engine, "connect", return_value=fake_conn):
        ok, detail = db.readiness_check()

    assert ok is False
    assert "boom" in detail


def test_readiness_check_propagates_keyboard_interrupt() -> None:
    """Critical exceptions must NOT be swallowed — they have to bubble up."""
    with patch.object(db.sync_engine, "connect", side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            db.readiness_check()
