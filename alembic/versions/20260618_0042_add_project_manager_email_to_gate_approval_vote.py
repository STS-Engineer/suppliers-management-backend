"""add project_manager_email to gate_approval_vote

Revision ID: 20260618_0042
Revises: 20260618_0041
Create Date: 2026-06-18
"""
from alembic import op
import sqlalchemy as sa

revision = "20260618_0042"
down_revision = "20260618_0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "gate_approval_vote",
        sa.Column("project_manager_email", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("gate_approval_vote", "project_manager_email")
