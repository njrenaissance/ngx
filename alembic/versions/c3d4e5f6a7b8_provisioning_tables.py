"""provisioning tables

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-05-12 22:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RR_STATUS_CHECK = (
    "status IN ('pending','provisioning','provisioned','failed','destroy_requested','destroying','destroyed')"
)
_DEPLOY_STATUS_CHECK = "status IN ('pending','provisioned','failed','destroying','destroyed')"


def upgrade() -> None:
    op.create_table(
        "resource_request",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("team.id"), nullable=False),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column(
            "resource_type_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("resource_type.id"),
            nullable=False,
        ),
        sa.Column(
            "tier_policy_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tier_policy.id"),
            nullable=False,
        ),
        sa.Column(
            "logical_region_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("logical_region.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("config", postgresql.JSONB, nullable=False),
        sa.Column("confirmation_token", sa.String(128), nullable=True),
        sa.Column("confirmation_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scheduled_destroy_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_RR_STATUS_CHECK, name="ck_resource_request_status"),
        sa.UniqueConstraint("team_id", "name", name="uq_resource_request_team_name"),
    )

    op.create_index("ix_resource_request_team_id", "resource_request", ["team_id"])
    op.create_index("ix_resource_request_status", "resource_request", ["status"])
    op.create_index("ix_resource_request_resource_type_id", "resource_request", ["resource_type_id"])
    op.create_index("ix_resource_request_requested_by", "resource_request", ["requested_by"])
    op.create_index("ix_resource_request_tier_policy_id", "resource_request", ["tier_policy_id"])
    op.create_index("ix_resource_request_logical_region_id", "resource_request", ["logical_region_id"])

    op.create_table(
        "deployment",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "resource_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("resource_request.id"),
            nullable=False,
        ),
        sa.Column(
            "logical_region_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("logical_region.id"),
            nullable=False,
        ),
        sa.Column("tf_workspace_id", sa.String(255), nullable=False),
        sa.Column("tf_state_key", sa.String(512), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("outputs_encrypted", sa.LargeBinary, nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("provisioned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_DEPLOY_STATUS_CHECK, name="ck_deployment_status"),
        sa.UniqueConstraint("tf_workspace_id", name="uq_deployment_tf_workspace_id"),
        sa.UniqueConstraint("tf_state_key", name="uq_deployment_tf_state_key"),
    )

    op.create_index("ix_deployment_resource_request_id", "deployment", ["resource_request_id"])

    op.create_table(
        "deployment_az",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "deployment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("deployment.id"),
            nullable=False,
        ),
        sa.Column(
            "az_map_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("region_az_map.id"),
            nullable=False,
        ),
        sa.Column("az_role", sa.String(16), nullable=False),
        sa.UniqueConstraint("deployment_id", "az_role", name="uq_deployment_az_role"),
    )

    op.create_index("ix_deployment_az_deployment_id", "deployment_az", ["deployment_id"])

    op.create_table(
        "apply_job",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "deployment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("deployment.id"),
            nullable=False,
        ),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("runner_id", sa.String(128), nullable=True),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("log_sanitized", sa.Text, nullable=True),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index("ix_apply_job_deployment_id", "apply_job", ["deployment_id"])


def downgrade() -> None:
    raise NotImplementedError("fix-forward only — create a new migration to undo")
