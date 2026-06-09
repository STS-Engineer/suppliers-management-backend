"""add component fields to financial_line and cash tracking to monthly_financial

Revision ID: 20260603_0015
Revises: 20260603_0014
Create Date: 2026-06-03
"""

from alembic import op
import sqlalchemy as sa

revision = "20260603_0015"
down_revision = "20260603_0014"
branch_labels = None
depends_on = None

FL_COLS = [
    ("component_name", sa.Text()),
    ("component_pn",   sa.String(200)),
]

MF_COLS = [
    ("cash_expected",           sa.Numeric(18, 2)),
    ("cash_actual",             sa.Numeric(18, 2)),
    ("cumulated_cash_actual",   sa.Numeric(18, 2)),
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    if "financial_line" in existing:
        cols = {c["name"] for c in inspector.get_columns("financial_line")}
        for name, col_type in FL_COLS:
            if name not in cols:
                op.add_column("financial_line", sa.Column(name, col_type, nullable=True))

    if "monthly_financial" in existing:
        cols = {c["name"] for c in inspector.get_columns("monthly_financial")}
        for name, col_type in MF_COLS:
            if name not in cols:
                op.add_column("monthly_financial", sa.Column(name, col_type, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    if "monthly_financial" in existing:
        cols = {c["name"] for c in inspector.get_columns("monthly_financial")}
        for name, _ in reversed(MF_COLS):
            if name in cols:
                op.drop_column("monthly_financial", name)

    if "financial_line" in existing:
        cols = {c["name"] for c in inspector.get_columns("financial_line")}
        for name, _ in reversed(FL_COLS):
            if name in cols:
                op.drop_column("financial_line", name)
