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

Output schema (one JSON object per line):
    timestamp  ISO 8601 with UTC offset, e.g. "2026-05-12T14:49:40.099908+00:00"
    level      "DEBUG" / "INFO" / "WARNING" / "ERROR" / "CRITICAL"
    logger     dotted logger name, typically the module __name__
    message    the formatted log message string
    exc_info   (optional) full traceback as a single string with embedded
               '\\n' newlines. Present iff the caller used logger.exception()
               or passed exc_info=True. JSON-safe — newlines stay escaped
               inside the string value, so the record remains one line.
    *          any extra={} keys passed by the caller are merged at the top
               level. Reserved key names that collide with stdlib LogRecord
               attributes ('name', 'msg', 'levelname', 'pathname', 'module',
               etc.) raise KeyError inside logging.makeRecord BEFORE this
               formatter runs — use distinct names like 'db_name'.

Aggregator parsing: the format is newline-delimited JSON (NDJSON / JSONL).
Most log aggregators ingest this natively — point CloudWatch / Datadog /
Loki at stdout and they will parse each line as a structured record. Set
FORGE_LOG__JSON_INDENT in dev only; an indented record spans multiple
lines and breaks ingestion.
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

    NOTE on reserved names: Python's logging.makeRecord() raises KeyError if
    extra={} contains any key that already exists as a LogRecord attribute
    (including 'name', 'msg', 'levelname', 'pathname', 'module', etc.). This
    happens BEFORE our formatter runs, so this class's silent-drop is only a
    fallback for the four output-format keys (timestamp/level/logger/message).
    Use distinct names like 'db_name' instead of 'name' in your extra={} dict.
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

    level = getattr(logging, settings.log.level.upper(), logging.INFO)
    root.setLevel(level)

    # stdout (not stderr) per Twelve-Factor App §XI and the Kubernetes/CRI
    # logging contract: container runtimes (containerd, Docker) capture both
    # streams, but ECS/EKS/GKE log routers and `kubectl logs` treat stdout
    # as the canonical event stream. stderr is reserved for runtime crashes
    # and panics that bypass the application logger entirely.
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(_JsonFormatter(indent=settings.log.json_indent))
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Return a named child logger. Drop-in for logging.getLogger(__name__)."""
    return logging.getLogger(name)
