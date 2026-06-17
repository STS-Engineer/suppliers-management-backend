"""add per-year estimated saving fields to opportunity

Client request 14/06/2026 — Est. Annual Saving ignored the next-year prices/
quantities the user entered (N+1..N+3). Keep the headline Est. Annual Saving as
year N (Excel parity), but persist the estimated saving for each year so the
breakdown can be shown and reused by reports/KPIs.

Per Excel "format STP rev 1.2" period formula (D52), each year's saving is:
  saving_year_n  = (current_price    - proposed_price)    * annual_quantity_n1 + bonus_delta
  saving_year_n1 = (current_price_n1 - proposed_price_n1) * annual_quantity_n2
  saving_year_n2 = (current_price_n2 - proposed_price_n2) * annual_quantity_n3
  saving_year_n3 = (current_price_n3 - proposed_price_n3) * annual_quantity_n4
The four sum to period_saving (D52).

Revision ID: 20260614_0033
Revises: 20260612_0032
Create Date: 2026-06-14
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260614_0033"
down_revision = "20260612_0032"
branch_labels = None
depends_on = None

NEW_COLS = [
    ("saving_year_n",  sa.Numeric(18, 2)),
    ("saving_year_n1", sa.Numeric(18, 2)),
    ("saving_year_n2", sa.Numeric(18, 2)),
    ("saving_year_n3", sa.Numeric(18, 2)),
    # Calendar-year prorated estimate {"2026": 1234.56, ...} — anchored on planned_start_date
    ("saving_by_year", JSONB()),
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
