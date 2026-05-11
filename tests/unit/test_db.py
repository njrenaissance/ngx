"""Unit tests for forge.db.readiness_check.

The function is tested in isolation by patching `sync_engine.connect` so we
don't need a live Postgres. A real-database assertion lives in the
integration suite (tests/integration/test_endpoints.py::test_readyz).
"""

from unittest.mock import MagicMock, patch

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


def test_readiness_check_returns_failure_on_connection_error() -> None:
    with patch.object(
        db.sync_engine,
        "connect",
        side_effect=ConnectionError("connection refused"),
    ):
        ok, detail = db.readiness_check()

    assert ok is False
    assert "connection refused" in detail


def test_readiness_check_does_not_raise_on_query_error() -> None:
    """Even if SELECT 1 itself raises, the function must convert to a tuple."""
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_conn.__exit__.return_value = False
    fake_conn.execute.side_effect = RuntimeError("query failed")

    with patch.object(db.sync_engine, "connect", return_value=fake_conn):
        ok, detail = db.readiness_check()

    assert ok is False
    assert "query failed" in detail
