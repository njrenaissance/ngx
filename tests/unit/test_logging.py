"""Unit tests for forge.logging — JSON formatter + root logger configuration."""

import json
import logging
import sys
from collections.abc import Iterator

import pytest

from forge.logging import _JsonFormatter, configure_root_logger, get_logger

pytestmark = pytest.mark.unit


@pytest.fixture
def _reset_root_logger() -> Iterator[None]:
    """Restore root logger handlers/level around each test.

    configure_root_logger() mutates the global root logger; without this
    fixture, test order would change behaviour and other tests would
    inherit the JSON handler.
    """
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    root.handlers = []
    try:
        yield
    finally:
        root.handlers = saved_handlers
        root.setLevel(saved_level)


def _format(record_kwargs: dict, *, indent: int | None = None) -> dict:
    """Build a LogRecord, run it through _JsonFormatter, return parsed JSON."""
    defaults: dict = {
        "name": "forge.test",
        "level": logging.INFO,
        "pathname": __file__,
        "lineno": 0,
        "msg": "hello",
        "args": (),
        "exc_info": None,
    }
    defaults.update(record_kwargs)
    record = logging.LogRecord(**defaults)
    return json.loads(_JsonFormatter(indent=indent).format(record))


def test_format_emits_standard_fields() -> None:
    payload = _format({})
    assert set(payload.keys()) >= {"timestamp", "level", "logger", "message"}
    assert payload["level"] == "INFO"
    assert payload["logger"] == "forge.test"
    assert payload["message"] == "hello"
    # ISO 8601 with UTC offset — `+00:00` suffix proves tz-aware formatting.
    assert payload["timestamp"].endswith("+00:00")


def test_format_merges_extra_keys() -> None:
    record = logging.LogRecord(
        name="forge.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg="provisioning",
        args=(),
        exc_info=None,
    )
    record.resource_id = "abc-123"  # type: ignore[attr-defined]
    record.tier = "gold"  # type: ignore[attr-defined]

    payload = json.loads(_JsonFormatter().format(record))

    assert payload["resource_id"] == "abc-123"
    assert payload["tier"] == "gold"


def test_format_drops_reserved_key_collisions() -> None:
    """Caller cannot spoof standard fields via extra={'level': 'fake'}."""
    record = logging.LogRecord(
        name="forge.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg="real",
        args=(),
        exc_info=None,
    )
    record.level = "SPOOFED"  # type: ignore[attr-defined]
    record.message = "spoofed"  # type: ignore[attr-defined]

    payload = json.loads(_JsonFormatter().format(record))

    assert payload["level"] == "INFO"
    assert payload["message"] == "real"


def test_format_includes_exc_info() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="forge.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=0,
        msg="failure",
        args=(),
        exc_info=exc_info,
    )
    payload = json.loads(_JsonFormatter().format(record))

    assert "exc_info" in payload
    assert "ValueError: boom" in payload["exc_info"]


def test_format_serializes_non_json_types() -> None:
    """default=str handles UUID/Path/datetime without crashing."""
    import uuid
    from pathlib import Path

    record = logging.LogRecord(
        name="forge.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg="paths",
        args=(),
        exc_info=None,
    )
    rid = uuid.uuid4()
    record.resource_id = rid  # type: ignore[attr-defined]
    record.workspace = Path("/tmp/forge")  # type: ignore[attr-defined]

    payload = json.loads(_JsonFormatter().format(record))

    assert payload["resource_id"] == str(rid)
    assert payload["workspace"] == str(Path("/tmp/forge"))


def test_format_indent_pretty_prints() -> None:
    """When indent is set, output spans multiple lines."""
    record = logging.LogRecord(
        name="forge.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg="hello",
        args=(),
        exc_info=None,
    )
    raw = _JsonFormatter(indent=2).format(record)
    assert "\n" in raw
    assert json.loads(raw)["message"] == "hello"


def test_configure_root_logger_attaches_handler(_reset_root_logger: None) -> None:
    configure_root_logger()
    root = logging.getLogger()
    handlers = [h for h in root.handlers if isinstance(h, logging.StreamHandler) and h.stream is sys.stdout]
    assert len(handlers) == 1
    assert isinstance(handlers[0].formatter, _JsonFormatter)


def test_configure_root_logger_is_idempotent(_reset_root_logger: None) -> None:
    configure_root_logger()
    configure_root_logger()
    configure_root_logger()
    root = logging.getLogger()
    stdout_handlers = [h for h in root.handlers if isinstance(h, logging.StreamHandler) and h.stream is sys.stdout]
    assert len(stdout_handlers) == 1


def test_get_logger_returns_child_in_root_hierarchy() -> None:
    """get_logger(__name__) must produce a logger that propagates to root."""
    logger = get_logger("forge.test.subpkg")
    assert logger.name == "forge.test.subpkg"
    assert logger.propagate is True
    # Walking up the parent chain must terminate at the root logger.
    assert logger.parent is not None
    root = logging.getLogger()
    parent = logger.parent
    while parent is not root and parent is not None:
        parent = parent.parent
    assert parent is root
