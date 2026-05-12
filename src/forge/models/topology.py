from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from forge.models.base import Base

if TYPE_CHECKING:
    from forge.models.catalog import TierRegionMember


def _now() -> datetime:
    return datetime.now(timezone.utc)


class LogicalRegion(Base):
    __tablename__ = "logical_region"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # Internal coordinates — never surfaced in API responses (cloud coordinate embargo).
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    physical_region: Mapped[str] = mapped_column(String(64), nullable=False)
    jurisdiction: Mapped[str] = mapped_column(String(16), nullable=False)
    platform_assigned_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    az_maps: Mapped[list["RegionAzMap"]] = relationship(back_populates="logical_region")
    tier_memberships: Mapped[list["TierRegionMember"]] = relationship(back_populates="logical_region")


class RegionAzMap(Base):
    __tablename__ = "region_az_map"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    logical_region_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("logical_region.id"), nullable=False
    )
    # physical_az is an internal label used by the provisioning engine — never sent to consumers.
    physical_az: Mapped[str] = mapped_column(String(64), nullable=False)
    az_index: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    logical_region: Mapped["LogicalRegion"] = relationship(back_populates="az_maps")


# TierRegionMember is defined in catalog.py (after TierPolicy) to avoid
# forward-reference FK complexity — it has FKs to both logical_region and tier_policy.
