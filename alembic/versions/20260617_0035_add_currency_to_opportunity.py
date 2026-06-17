"""add transaction currency + FX rate to opportunity

Finance requirement — opportunities can be in EUR / USD / RMB / INR (per the STP
workbook "Data" sheet). Monetary figures are stored in the opportunity's transaction
currency; `fx_rate_to_eur` is the rate used to convert to the group reporting currency
(EUR) for consolidated views. The rate is stored ON the opportunity (rate at time of
estimate) so consolidated totals are reproducible and auditable.

Revision ID: 20260617_0035
Revises: 20260615_0034
Create Date: 2026-06-17
"""

from alembic import op
import sqlalchemy as sa

revision = "20260617_0035"
down_revision = "20260615_0034"
branch_labels = None
depends_on = None

NEW_COLS = [
    ("currency", sa.String(10), "EUR"),
    ("fx_rate_to_eur", sa.Numeric(18, 6), "1"),
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "opportunity" not in inspector.get_table_names():
        return
    existing = {c["name"] for c in inspector.get_columns("opportunity")}
    for name, col_type, default in NEW_COLS:
        if name not in existing:
            op.add_column(
                "opportunity",
                sa.Column(name, col_type, nullable=True, server_default=default),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "opportunity" not in inspector.get_table_names():
        return
    existing = {c["name"] for c in inspector.get_columns("opportunity")}
    for name, _, _ in reversed(NEW_COLS):
        if name in existing:
            op.drop_column("opportunity", name)
