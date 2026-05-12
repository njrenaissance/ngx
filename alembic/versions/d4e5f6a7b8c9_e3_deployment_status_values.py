"""E.3 deployment status values

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-05-12 19:58:00.000000

E.3 introduces three intermediate Deployment statuses (`planned`,
`applying`, `applied`) so the per-stage progress of plan-then-apply is
observable in the audit trail. The original ck_deployment_status check
constraint only allowed `pending`, `provisioned`, `failed`, `destroying`,
`destroyed` — `planned` and friends raised CheckViolation when the
worker tried to commit them.

Fix-forward per CLAUDE.md: drop and recreate the constraint with the
expanded value set rather than altering the existing one (Postgres
doesn't support ALTER CONSTRAINT for check predicates).
"""

from typing import Sequence, Union

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_DEPLOY_STATUS_CHECK = (
    "status IN ('pending','planned','applying','applied','provisioned','failed','destroying','destroyed')"
)


def upgrade() -> None:
    op.drop_constraint("ck_deployment_status", "deployment", type_="check")
    op.create_check_constraint("ck_deployment_status", "deployment", _NEW_DEPLOY_STATUS_CHECK)


def downgrade() -> None:
    # Fix-forward only — see CLAUDE.md. A fresh forward migration would
    # be authored to recover from a bad state, never a downgrade.
    raise NotImplementedError("forge migrations are fix-forward; author a new forward revision instead")
