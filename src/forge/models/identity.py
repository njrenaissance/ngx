import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from forge.models.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CostCenter(Base):
    __tablename__ = "cost_center"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    teams: Mapped[list["Team"]] = relationship(back_populates="cost_center")


class Team(Base):
    __tablename__ = "team"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cost_center_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cost_center.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    chargeback_multiplier: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False, default=Decimal("1.0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    cost_center: Mapped["CostCenter"] = relationship(back_populates="teams")
    users: Mapped[list["AppUser"]] = relationship(back_populates="team")


class AppUser(Base):
    # 'user' is a reserved word in PostgreSQL; table name is app_user.
    __tablename__ = "app_user"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("team.id"), nullable=False)
    first_name: Mapped[str] = mapped_column(String(128), nullable=False)
    last_name: Mapped[str] = mapped_column(String(128), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    api_key_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)  # member | admin
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    team: Mapped["Team"] = relationship(back_populates="users")
