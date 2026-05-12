import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from forge.api.auth import AuthContext, require_auth

router = APIRouter(prefix="/v1", tags=["me"])


class TeamResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str


class MeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    first_name: str
    last_name: str
    role: str
    team: TeamResponse
    last_seen_at: datetime | None


@router.get(
    "/me",
    response_model=MeResponse,
    responses={401: {"description": "Invalid or missing API key"}},
)
def me(auth: AuthContext = Depends(require_auth)) -> MeResponse:
    user = auth.user
    return MeResponse(
        id=user.id,
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        role=user.role,
        team=TeamResponse(id=user.team.id, name=user.team.name),
        last_seen_at=user.last_seen_at,
    )
