"""add recovery_history to financial_line

Revision ID: 20260609_0024
Revises: 20260609_0023
Create Date: 2026-06-09
"""

from alembic import op
import sqlalchemy as sa

revision = "20260609_0024"
down_revision = "20260609_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "financial_line" not in inspector.get_table_names():
        return
    existing = {c["name"] for c in inspector.get_columns("financial_line")}
    if "recovery_history" not in existing:
        op.add_column("financial_line", sa.Column("recovery_history", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "financial_line" not in inspector.get_table_names():
        return
    existing = {c["name"] for c in inspector.get_columns("financial_line")}
    if "recovery_history" in existing:
        op.drop_column("financial_line", "recovery_history")
