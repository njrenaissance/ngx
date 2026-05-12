"""Structured JSON logging for Forge.

Configures the root logger once with a JSON formatter. All child loggers
obtained via get_logger(__name__) inherit the root handler automatically.

Call configure_root_logger() exactly once at application startup:
  - FastAPI: first line of get_app() in main.py
  - Celery: at module level in workers/__init__.py, and again via
    worker_process_init signal in each forked pool child

Callers obtain a child logger with:
    from forge.logging import get_logger
    logger = get_logger(__name__)
    logger.info("thing happened", extra={"resource_id": id, "tier": tier})

Each log line is a single JSON object on stdout:
    {"timestamp": "...", "level": "INFO", "logger": "forge.db", "message": "...", "resource_id": "abc"}

Extra keys passed via extra={} are merged into the top-level JSON object.
"""

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from forge.config import settings


class _JsonFormatter(logging.Formatter):
    """Emit one JSON object per log record to stdout.

    Standard fields always present: timestamp, level, logger, message.
    Any keys passed via extra={} on the log call are merged at the top level.
    Keys colliding with standard field names are silently dropped.
    """

    _RESERVED = frozenset({"timestamp", "level", "logger", "message"})
    # All attributes set by LogRecord.__init__ — anything else was added by the caller via extra={}.
    _RECORD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None)))

    def __init__(self, indent: int | None = None) -> None:
        super().__init__()
        self._indent = indent

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key, value in record.__dict__.items():
            if key not in self._RECORD_ATTRS and key not in self._RESERVED:
                payload[key] = value

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, indent=self._indent)


def configure_root_logger() -> None:
    """Configure the root logger with a JSON formatter. Idempotent.

    Safe to call from both FastAPI and Celery entry points. If a
    StreamHandler pointing to stdout is already on the root logger this
    is a no-op, preventing double-handler accumulation when called from
    both the parent worker process and forked pool children.
    """
    root = logging.getLogger()

    if any(isinstance(h, logging.StreamHandler) and h.stream is sys.stdout for h in root.handlers):
        return

    level = getattr(logging, settings.log.LEVEL.upper(), logging.INFO)
    root.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(_JsonFormatter(indent=settings.log.JSON_INDENT))
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Return a named child logger. Drop-in for logging.getLogger(__name__)."""
    return logging.getLogger(name)
