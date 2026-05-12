"""Celery application — broker connection and base configuration.

The celery command discovers this module via `celery -A forge.workers`.
Task modules under `forge.workers.tasks` are auto-discovered; each module
must be imported in `forge/workers/tasks/__init__.py` so its
`@shared_task` decorator runs and registers the task with Celery.
"""

from celery import Celery  # type: ignore[import-untyped]
from celery.signals import (  # type: ignore[import-untyped]
    worker_process_init,
    worker_ready,
    worker_shutdown,
)

from forge import __version__
from forge.config import settings
from forge.logging import configure_root_logger, get_logger

logger = get_logger(__name__)

celery_app = Celery("forge")

# Result backend reuses Aurora — the SQLAlchemy backend requires a `db+`
# prefix on the DSN and Celery auto-creates its `celery_taskmeta` /
# `celery_tasksetmeta` tables in the public schema on first use. Reusing
# the DB means one source of truth for connection credentials and no
# extra infrastructure.
_result_backend = f"db+{settings.database.sync_url}"

celery_app.conf.update(
    broker_url=settings.celery.BROKER_URL,
    result_backend=_result_backend,
    task_default_queue=settings.celery.TASK_DEFAULT_QUEUE,
    # Time limits — hard kill at TASK_TIME_LIMIT, soft signal earlier so
    # the task can clean up. Both are tunable via FORGE_CELERY__* env vars.
    task_time_limit=settings.celery.TASK_TIME_LIMIT,
    task_soft_time_limit=settings.celery.TASK_SOFT_TIME_LIMIT,
    # Acks late + reject on worker lost: if a worker process dies mid-task,
    # the message is requeued rather than silently dropped. Provisioning
    # tasks must not be lost.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # One task at a time per worker — provisioning is IO-heavy (subprocess
    # waits on terraform). Concurrency is controlled via the worker
    # --concurrency flag, not in-process prefetching.
    worker_prefetch_multiplier=1,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # We own the root logger; prevent Celery from replacing our JSON handler.
    worker_hijack_root_logger=False,
)

configure_root_logger()


@worker_process_init.connect(dispatch_uid="forge.logging.reattach")
def _on_worker_process_init(**_kwargs: object) -> None:
    """Re-attach JSON log handler in each forked pool child.

    Called via worker_process_init signal after Celery forks a pool child.
    The fork inherits the parent's logging handler chain. Unlike OTel's
    OTLP exporter (which has a stale HTTP connection pool post-fork), a
    plain StreamHandler is fork-safe — but we re-run configure_root_logger()
    to guarantee correct state in the child regardless of Celery internals.
    """
    configure_root_logger()


@worker_ready.connect(dispatch_uid="forge.workers.startup")
def _on_worker_ready(**_kwargs: object) -> None:
    """Fired once when the worker has finished booting and is ready to consume."""
    logger.info(
        "celery worker ready",
        extra={
            "version": __version__,
            "environment": settings.ENVIRONMENT,
            "queue": settings.celery.TASK_DEFAULT_QUEUE,
            "broker_url": settings.celery.BROKER_URL,
            "task_time_limit": settings.celery.TASK_TIME_LIMIT,
            "task_soft_time_limit": settings.celery.TASK_SOFT_TIME_LIMIT,
        },
    )


@worker_shutdown.connect(dispatch_uid="forge.workers.shutdown")
def _on_worker_shutdown(**_kwargs: object) -> None:
    """Fired when the worker is shutting down (after warm shutdown drains)."""
    logger.info("celery worker shutdown", extra={"queue": settings.celery.TASK_DEFAULT_QUEUE})


celery_app.autodiscover_tasks(["forge.workers"])
