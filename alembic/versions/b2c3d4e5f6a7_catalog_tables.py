"""catalog tables

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-05-11 18:01:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tier_policy",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tier_name", sa.String(32), nullable=False, unique=True),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column("sla_class", sa.String(16), nullable=False),
        sa.Column("min_regions", sa.Integer, nullable=False),
        sa.Column("min_azs_per_region", sa.Integer, nullable=False),
        sa.Column("auto_expire_days", sa.Integer, nullable=True),
        sa.Column("approval_required", sa.Boolean, nullable=False, server_default="false"),
    )

    op.create_table(
        "tier_region_member",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
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
        sa.Column("priority", sa.Integer, nullable=False, server_default="1"),
        sa.UniqueConstraint("tier_policy_id", "logical_region_id", name="uq_tier_region_member"),
    )

    op.create_index("ix_tier_region_member_tier_policy_id", "tier_region_member", ["tier_policy_id"])
    op.create_index("ix_tier_region_member_logical_region_id", "tier_region_member", ["logical_region_id"])

    op.create_table(
        "resource_type",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("base_config_schema", postgresql.JSONB, nullable=False),
        sa.Column("terraform_variable_map", postgresql.JSONB, nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("latest", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", "version", name="uq_resource_type_name_version"),
    )

    op.create_index("ix_resource_type_name", "resource_type", ["name"])

    op.create_table(
        "resource_type_tier_constraint",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
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
        sa.Column("config_schema_override", postgresql.JSONB, nullable=False),
        sa.UniqueConstraint("resource_type_id", "tier_policy_id", name="uq_constraint_pair"),
    )

    op.create_index("ix_rttc_resource_type_id", "resource_type_tier_constraint", ["resource_type_id"])
    op.create_index("ix_rttc_tier_policy_id", "resource_type_tier_constraint", ["tier_policy_id"])


def downgrade() -> None:
    raise NotImplementedError("fix-forward only — create a new migration to undo")
