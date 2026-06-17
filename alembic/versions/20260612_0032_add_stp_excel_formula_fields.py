"""add STP Excel-formula fields to opportunity

Client request 12/06/2026 — reproduce the "format STP rev 1.2" Excel formulas
and add the missing workbook fields:
  - secondary_plants   : Phase 0 row 8 "Secondary plants" (free text)
  - gate_conditions    : Phase 0 C67 "Conditions / Actions requested"
  - period_saving      : EBITDA savings "Period" (Excel D52)
  - roi_period_percent : ROI over the period (Excel F52)

Revision ID: 20260612_0032
Revises: 20260612_0031
Create Date: 2026-06-12
"""

from alembic import op
import sqlalchemy as sa

revision = "20260612_0032"
down_revision = "20260612_0031"
branch_labels = None
depends_on = None

NEW_COLS = [
    ("secondary_plants",   sa.Text()),
    ("gate_conditions",    sa.Text()),
    ("period_saving",      sa.Numeric(18, 2)),
    ("roi_period_percent", sa.Numeric(10, 2)),
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
