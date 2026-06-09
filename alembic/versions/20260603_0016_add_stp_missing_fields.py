"""add missing STP fields: supplier logistics, price projections, initial step

Revision ID: 20260603_0016
Revises: 20260603_0015
Create Date: 2026-06-03
"""

from alembic import op
import sqlalchemy as sa

revision = "20260603_0016"
down_revision = "20260603_0015"
branch_labels = None
depends_on = None

NEW_COLS = [
    ("proposed_price_n1",      sa.Numeric(18, 6)),
    ("proposed_price_n2",      sa.Numeric(18, 6)),
    ("proposed_price_n3",      sa.Numeric(18, 6)),
    ("incoterms_before",       sa.String(20)),
    ("incoterms_after",        sa.String(20)),
    ("top_days_before",        sa.Integer()),
    ("top_days_after",         sa.Integer()),
    ("transit_days_before",    sa.Integer()),
    ("transit_days_after",     sa.Integer()),
    ("country_before",         sa.String(100)),
    ("country_after",          sa.String(100)),
    ("bonus_before",           sa.Numeric(18, 2)),
    ("bonus_after",            sa.Numeric(18, 2)),
    ("supplier_asked",         sa.Boolean()),
    ("supplier_asked_result",  sa.Text()),
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "opportunity" not in set(inspector.get_table_names()):
        return
    existing = {c["name"] for c in inspector.get_columns("opportunity")}
    for name, col_type in NEW_COLS:
        if name not in existing:
            op.add_column("opportunity", sa.Column(name, col_type, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "opportunity" not in set(inspector.get_table_names()):
        return
    existing = {c["name"] for c in inspector.get_columns("opportunity")}
    for name, _ in reversed(NEW_COLS):
        if name in existing:
            op.drop_column("opportunity", name)
