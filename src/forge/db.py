from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from forge.config import settings

# Sync engine — used by Alembic migrations and the seed script.
# The async engine (asyncpg) will be added here when async API endpoints land.
sync_engine = create_engine(
    settings.database.sync_url,
    pool_pre_ping=True,
    pool_timeout=settings.database.POOL_TIMEOUT,
    pool_recycle=settings.database.POOL_RECYCLE,
    connect_args={"connect_timeout": settings.database.CONNECT_TIMEOUT},
)

SyncSession: sessionmaker[Session] = sessionmaker(bind=sync_engine, expire_on_commit=False)


def readiness_check() -> tuple[bool, str]:
    """Verify the database is reachable by issuing SELECT 1.

    Returns (True, "ok") on success or (False, "<reason>") on failure.
    Caller (e.g. /readyz) translates the result into an HTTP response.
    Must not raise — exceptions are caught and converted to (False, str)
    so the readiness endpoint can always respond.
    """
    try:
        with sync_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return (True, "ok")
    except Exception as e:
        return (False, str(e))
