"""add delay_alert_last_sent_at to financial_line

Revision ID: 20260619_0044
Revises: 20260618_0043
Create Date: 2026-06-19
"""
from alembic import op
import sqlalchemy as sa

revision = "20260619_0044"
down_revision = "20260618_0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "financial_line",
        sa.Column("delay_alert_last_sent_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("financial_line", "delay_alert_last_sent_at")
