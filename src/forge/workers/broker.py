"""TaskBroker — thin abstraction over Celery for submit, status, and revoke.

Keeps the FastAPI layer decoupled from Celery internals so the background
job backend can be swapped in the future without touching the API router.
The API resolves user-facing task names via TASK_REGISTRY rather than
importing concrete task functions; this keeps the broker the only place
that knows Celery exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from celery import Celery  # type: ignore[import-untyped]

from forge.workers import celery_app
from forge.workers.registry import TASK_REGISTRY


@dataclass(frozen=True)
class TaskStatusResult:
    """Snapshot of a task's current state from the result backend."""

    state: str
    result: Any | None
    date_done: datetime | None
    traceback: str | None


class TaskBroker:
    """Wraps Celery send_task / AsyncResult / control.revoke."""

    def __init__(self, celery: Celery) -> None:
        self._celery = celery

    def submit(self, task_name: str, args: list[Any] | None = None, kwargs: dict[str, Any] | None = None) -> str:
        """Submit a task by its user-facing name (resolved through TASK_REGISTRY).

        Raises KeyError if `task_name` is not registered — fail loud rather
        than enqueue a task no consumer will pick up.
        """
        celery_task_name = TASK_REGISTRY[task_name]
        result = self._celery.send_task(celery_task_name, args=args or [], kwargs=kwargs or {})
        return str(result.id)

    def get_status(self, task_id: str) -> TaskStatusResult:
        """Query the result backend for live task state."""
        r = self._celery.AsyncResult(task_id)
        return TaskStatusResult(
            state=r.state,
            result=r.result,
            date_done=getattr(r, "date_done", None),
            traceback=r.traceback,
        )

    def revoke(self, task_id: str, *, terminate: bool = False) -> None:
        """Revoke (cancel) a pending or running task."""
        self._celery.control.revoke(task_id, terminate=terminate)


# Singleton — shared across all requests.
_broker = TaskBroker(celery_app)


def get_task_broker() -> TaskBroker:
    """FastAPI dependency returning the TaskBroker singleton."""
    return _broker
