from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from forge.models.base import Base

if TYPE_CHECKING:
    from forge.models.catalog import ResourceType, TierPolicy
    from forge.models.identity import AppUser, Team
    from forge.models.topology import LogicalRegion, RegionAzMap


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ResourceRequest(Base):
    __tablename__ = "resource_request"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("team.id"), nullable=False, index=True)
    requested_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("app_user.id"), nullable=False)
    resource_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resource_type.id"), nullable=False
    )
    tier_policy_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tier_policy.id"), nullable=False)
    logical_region_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("logical_region.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    confirmation_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    confirmation_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scheduled_destroy_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    team: Mapped[Team] = relationship()
    requester: Mapped[AppUser] = relationship()
    resource_type: Mapped[ResourceType] = relationship()
    tier_policy: Mapped[TierPolicy] = relationship()
    logical_region: Mapped[LogicalRegion] = relationship()
    deployments: Mapped[list["Deployment"]] = relationship(back_populates="resource_request")


class Deployment(Base):
    __tablename__ = "deployment"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    resource_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resource_request.id"), nullable=False, index=True
    )
    logical_region_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("logical_region.id"), nullable=False
    )
    tf_workspace_id: Mapped[str] = mapped_column(String(255), nullable=False)
    tf_state_key: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    # AES-256-GCM ciphertext of Terraform outputs (plaintext in POC; see SPEC §8.3).
    outputs_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # Sanitized error message — cloud coordinates stripped before persisting.
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    provisioned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    __table_args__ = (
        UniqueConstraint("tf_workspace_id", name="uq_deployment_tf_workspace_id"),
        UniqueConstraint("tf_state_key", name="uq_deployment_tf_state_key"),
    )

    resource_request: Mapped[ResourceRequest] = relationship(back_populates="deployments")
    logical_region: Mapped[LogicalRegion] = relationship()
    az_assignments: Mapped[list["DeploymentAz"]] = relationship(back_populates="deployment")
    apply_jobs: Mapped[list["ApplyJob"]] = relationship(back_populates="deployment")


class DeploymentAz(Base):
    __tablename__ = "deployment_az"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    deployment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("deployment.id"), nullable=False)
    az_map_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("region_az_map.id"), nullable=False)
    az_role: Mapped[str] = mapped_column(String(16), nullable=False)  # primary | secondary

    deployment: Mapped[Deployment] = relationship(back_populates="az_assignments")
    az_map: Mapped[RegionAzMap] = relationship()


class ApplyJob(Base):
    __tablename__ = "apply_job"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    deployment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deployment.id"), nullable=False, index=True
    )
    operation: Mapped[str] = mapped_column(String(16), nullable=False)  # apply | destroy | plan-only
    status: Mapped[str] = mapped_column(
        String(16), nullable=False
    )  # queued | running | succeeded | failed | dead-lettered
    runner_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    log_sanitized: Mapped[str | None] = mapped_column(Text, nullable=True)
    enqueued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    deployment: Mapped[Deployment] = relationship(back_populates="apply_jobs")
