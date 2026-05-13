from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from forge.config import settings
from forge.logging import get_logger

logger = get_logger(__name__)

# Sync engine — used by Alembic migrations and the seed script.
# The async engine (asyncpg) will be added here when async API endpoints land.
sync_engine = create_engine(
    settings.database.sync_url,
    pool_pre_ping=True,
    pool_timeout=settings.database.pool_timeout,
    pool_recycle=settings.database.pool_recycle,
    connect_args={"connect_timeout": settings.database.connect_timeout},
)

SyncSession: sessionmaker[Session] = sessionmaker(bind=sync_engine, expire_on_commit=False)

logger.debug(
    "database engine initialised",
    extra={
        "db_host": settings.database.host,
        "db_port": settings.database.port,
        "db_name": settings.database.name,
        "db_schema": settings.database.schema,
        "ssl_mode": settings.database.ssl_mode,
        "pool_timeout": settings.database.pool_timeout,
        "pool_recycle": settings.database.pool_recycle,
    },
)


def readiness_check() -> tuple[bool, str]:
    """Verify the database is reachable by issuing SELECT 1.

    Returns (True, "ok") on success or (False, "<reason>") on failure.
    Caller (e.g. /readyz) translates the result into an HTTP response.
    Must not raise — exceptions are caught and converted to (False, str)
    so the readiness endpoint can always respond.
    """
    # SELECT 1 is the canonical no-op query — cheap, supported by every SQL
    # dialect, and forces the pool's pre-ping to actually round-trip the
    # connection rather than just checking that a socket is open.
    try:
        with sync_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return (True, "ok")
    except (SQLAlchemyError, OSError) as e:
        # SQLAlchemyError covers driver/query errors; OSError covers the
        # underlying socket failures (refused, reset, DNS). KeyboardInterrupt
        # and SystemExit must propagate, so we deliberately do NOT catch
        # bare Exception.
        logger.warning(
            "database readiness check failed",
            extra={"host": settings.database.host, "port": settings.database.port, "error": str(e)},
        )
        return (False, str(e))
