"""add is_plant_manager to gate_approval_vote

Revision ID: 20260618_0043
Revises: 20260618_0042
Create Date: 2026-06-18
"""
from alembic import op
import sqlalchemy as sa

revision = "20260618_0043"
down_revision = "20260618_0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "gate_approval_vote",
        sa.Column("is_plant_manager", sa.Boolean(), nullable=True, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("gate_approval_vote", "is_plant_manager")
