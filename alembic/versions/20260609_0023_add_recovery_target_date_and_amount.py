"""add recovery_target_date and recovery_amount to financial_line

Revision ID: 20260609_0023
Revises: 20260608_0022
Create Date: 2026-06-09
"""

from alembic import op
import sqlalchemy as sa

revision = "20260609_0023"
down_revision = "20260608_0022"
branch_labels = None
depends_on = None

NEW_COLS = [
    ("recovery_target_date", sa.Date()),
    ("recovery_amount",      sa.Numeric(18, 2)),
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "financial_line" not in inspector.get_table_names():
        return
    existing = {c["name"] for c in inspector.get_columns("financial_line")}
    for name, col_type in NEW_COLS:
        if name not in existing:
            op.add_column("financial_line", sa.Column(name, col_type, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "financial_line" not in inspector.get_table_names():
        return
    existing = {c["name"] for c in inspector.get_columns("financial_line")}
    for name, _ in reversed(NEW_COLS):
        if name in existing:
            op.drop_column("financial_line", name)
