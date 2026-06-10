"""add budget_confirmed_at, budget_confirmed_by, planned_end_date to opportunity

Revision ID: 20260609_0025
Revises: 20260609_0024
Create Date: 2026-06-09
"""

from alembic import op
import sqlalchemy as sa

revision = "20260609_0025"
down_revision = "20260609_0024"
branch_labels = None
depends_on = None

NEW_COLS = [
    ("budget_confirmed_at",  sa.DateTime()),
    ("budget_confirmed_by",  sa.String(200)),
    ("planned_end_date",     sa.Date()),
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "opportunity" not in inspector.get_table_names():
        return
    existing = {c["name"] for c in inspector.get_columns("opportunity")}
    for name, col_type in NEW_COLS:
        if name not in existing:
            op.add_column("opportunity", sa.Column(name, col_type, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "opportunity" not in inspector.get_table_names():
        return
    existing = {c["name"] for c in inspector.get_columns("opportunity")}
    for name, _ in reversed(NEW_COLS):
        if name in existing:
            op.drop_column("opportunity", name)
