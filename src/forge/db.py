from sqlalchemy import create_engine
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
