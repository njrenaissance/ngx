from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from forge.models.base import Base

if TYPE_CHECKING:
    from forge.models.topology import LogicalRegion


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TierPolicy(Base):
    __tablename__ = "tier_policy"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tier_name: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    sla_class: Mapped[str] = mapped_column(String(16), nullable=False)
    min_regions: Mapped[int] = mapped_column(Integer, nullable=False)
    min_azs_per_region: Mapped[int] = mapped_column(Integer, nullable=False)
    auto_expire_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approval_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    region_members: Mapped[list["TierRegionMember"]] = relationship(back_populates="tier_policy")
    resource_constraints: Mapped[list["ResourceTypeTierConstraint"]] = relationship(back_populates="tier_policy")


class TierRegionMember(Base):
    __tablename__ = "tier_region_member"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tier_policy_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tier_policy.id"), nullable=False)
    logical_region_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("logical_region.id"), nullable=False
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    tier_policy: Mapped["TierPolicy"] = relationship(back_populates="region_members")
    logical_region: Mapped[LogicalRegion] = relationship(back_populates="tier_memberships")


class ResourceType(Base):
    __tablename__ = "resource_type"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    base_config_schema: Mapped[dict] = mapped_column(JSONB, nullable=False)
    terraform_variable_map: Mapped[dict] = mapped_column(JSONB, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    latest: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    tier_constraints: Mapped[list["ResourceTypeTierConstraint"]] = relationship(back_populates="resource_type")


class ResourceTypeTierConstraint(Base):
    __tablename__ = "resource_type_tier_constraint"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    resource_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resource_type.id"), nullable=False
    )
    tier_policy_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tier_policy.id"), nullable=False)
    config_schema_override: Mapped[dict] = mapped_column(JSONB, nullable=False)

    resource_type: Mapped["ResourceType"] = relationship(back_populates="tier_constraints")
    tier_policy: Mapped["TierPolicy"] = relationship(back_populates="resource_constraints")
