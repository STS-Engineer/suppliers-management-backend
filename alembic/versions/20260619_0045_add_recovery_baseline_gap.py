"""add recovery baseline gap snapshot fields

Revision ID: 20260619_0045
Revises: 20260619_0044
Create Date: 2026-06-19
"""
from alembic import op
import sqlalchemy as sa

revision = "20260619_0045"
down_revision = "20260619_0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "financial_line",
        sa.Column("recovery_baseline_gap", sa.Numeric(18, 2), nullable=True),
    )
    op.add_column(
        "financial_line",
        sa.Column("recovery_baseline_set_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("financial_line", "recovery_baseline_set_at")
    op.drop_column("financial_line", "recovery_baseline_gap")
