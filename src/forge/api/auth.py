import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from forge.api.deps import get_db_session
from forge.models import AppUser

UNAUTH = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
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
        raise UNAUTH
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise UNAUTH
    return token


def _verify_key(session: Session, raw_key: str) -> AppUser | None:
    # bcrypt is intentionally slow (cost 12 ≈ 250ms). With the POC seed of 4
    # users this is fine; at scale we'd add a fast secondary lookup column
    # (e.g. a SHA-256 prefix index) so we bcrypt-verify a single candidate row.
    candidates = session.query(AppUser).all()
    raw_bytes = raw_key.encode()
    for user in candidates:
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
        raise UNAUTH
    user.last_seen_at = datetime.now(timezone.utc)
    session.commit()
    return AuthContext(user=user, team_id=user.team_id)


def require_admin(auth: AuthContext = Depends(require_auth)) -> AuthContext:
    if auth.user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required",
        )
    return auth
