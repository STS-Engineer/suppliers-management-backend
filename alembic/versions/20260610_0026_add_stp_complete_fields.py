"""add consignment, current_price projections, other_cost, stp_risks, stp_benefits to opportunity

Revision ID: 20260610_0026
Revises: 20260609_0025
Create Date: 2026-06-10
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260610_0026"
down_revision = "20260609_0025"
branch_labels = None
depends_on = None

NEW_COLS = [
    # Consignment (Yes/No) — individual columns; used in inventory gap formula
    ("consignment_before",  sa.String(10)),
    ("consignment_after",   sa.String(10)),
    # Before-prices for N+1, N+2, N+3 — individual columns; used in period-saving formula
    ("current_price_n1",    sa.Numeric(18, 6)),
    ("current_price_n2",    sa.Numeric(18, 6)),
    ("current_price_n3",    sa.Numeric(18, 6)),
    # 4th investment cost line — individual column; summed into total_investment
    ("other_cost",          sa.Numeric(18, 2)),
    # Risks (before/after per type) + spec questions — stored as JSONB
    ("stp_risks",           JSONB()),
    # Benefits narrative — stored as JSONB
    ("stp_benefits",        JSONB()),
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
