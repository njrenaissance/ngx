import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import bcrypt
from fastapi import Depends, Request
from sqlalchemy import update
from sqlalchemy.orm import Session, joinedload

from forge.api.deps import get_db_session
from forge.api.problem_details import ProblemDetailsException
from forge.models import AppUser


def _unauth() -> ProblemDetailsException:
    # Constructed fresh per raise so concurrent unauthorized requests do not
    # share a mutable __traceback__ / __context__ across threads.
    return ProblemDetailsException(
        status=401,
        type="urn:forge:error:unauthorized",
        title="Unauthorized",
        detail="Invalid or missing API key",
        headers={"WWW-Authenticate": "Bearer"},
    )


@dataclass(frozen=True)
class AuthContext:
    user: AppUser
    team_id: uuid.UUID


def _extract_bearer(request: Request) -> str:
    header = request.headers.get("authorization")
    if not header:
        raise _unauth()
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _unauth()
    return token


def _verify_key(session: Session, raw_key: str) -> AppUser | None:
    # bcrypt is intentionally slow (cost 12 ≈ 250ms). With the POC seed of 4
    # users this is acceptable; at scale add a fast secondary lookup column
    # (e.g. SHA-256 prefix index) to reduce candidates to one before bcrypt.
    # Limit guards against memory exhaustion if the table grows unexpectedly.
    candidates = session.query(AppUser).options(joinedload(AppUser.team)).limit(10_000).all()
    raw_bytes = raw_key.encode()
    for user in candidates:
        if not user.api_key_hash:
            continue
        if bcrypt.checkpw(raw_bytes, user.api_key_hash.encode()):
            return user
    return None


def require_auth(
    request: Request,
    session: Session = Depends(get_db_session),
) -> AuthContext:
    raw_key = _extract_bearer(request)
    user = _verify_key(session, raw_key)
    if user is None:
        raise _unauth()
    # Targeted UPDATE avoids a load-modify-commit race; both concurrent writers
    # stamp "now" independently so the result is always monotonically correct.
    session.execute(update(AppUser).where(AppUser.id == user.id).values(last_seen_at=datetime.now(timezone.utc)))
    session.commit()
    return AuthContext(user=user, team_id=user.team_id)


def require_admin(auth: AuthContext = Depends(require_auth)) -> AuthContext:
    if auth.user.role != "admin":
        raise ProblemDetailsException(
            status=403,
            type="urn:forge:error:forbidden",
            title="Forbidden",
            detail="Admin role required",
        )
    return auth
