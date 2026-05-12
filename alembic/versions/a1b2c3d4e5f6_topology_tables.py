"""topology tables

Revision ID: a1b2c3d4e5f6
Revises: c629c1fa2679
Create Date: 2026-05-11 18:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "c629c1fa2679"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "logical_region",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(32), nullable=False, unique=True),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("physical_region", sa.String(64), nullable=False),
        sa.Column("jurisdiction", sa.String(16), nullable=False),
        sa.Column("platform_assigned_only", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "region_az_map",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "logical_region_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("logical_region.id"),
            nullable=False,
        ),
        sa.Column("physical_az", sa.String(64), nullable=False),
        sa.Column("az_index", sa.Integer, nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.UniqueConstraint("logical_region_id", "physical_az", name="uq_region_az_physical"),
        sa.UniqueConstraint("logical_region_id", "az_index", name="uq_region_az_index"),
    )

    op.create_index("ix_region_az_map_logical_region_id", "region_az_map", ["logical_region_id"])


def downgrade() -> None:
    raise NotImplementedError("fix-forward only — create a new migration to undo")
