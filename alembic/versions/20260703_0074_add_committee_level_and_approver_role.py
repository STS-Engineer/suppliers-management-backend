"""add committee_level and approver_role for Phase 1-4 sourcing committee gates

Revision ID: 20260703_0074
Revises: 20260703_0073
Create Date: 2026-07-03
"""
from alembic import op
import sqlalchemy as sa

revision = "20260703_0074"
down_revision = "20260703_0073"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "opportunity",
        sa.Column("committee_level", sa.String(20), nullable=True),
    )
    op.add_column(
        "gate_approval_request",
        sa.Column("committee_level", sa.String(20), nullable=True),
    )
    op.add_column(
        "gate_approval_vote",
        sa.Column("approver_role", sa.String(50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("gate_approval_vote", "approver_role")
    op.drop_column("gate_approval_request", "committee_level")
    op.drop_column("opportunity", "committee_level")
