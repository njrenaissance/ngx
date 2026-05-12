from collections.abc import Iterator

from sqlalchemy.orm import Session

from forge.db import SyncSession


def get_db_session() -> Iterator[Session]:
    session = SyncSession()
    try:
        yield session
    finally:
        session.close()
