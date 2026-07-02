"""drop unused supplier_site_relation logistics columns

Revision ID: 20260702_0070
Revises: 20260701_0069
Create Date: 2026-07-02
"""

from alembic import op
import sqlalchemy as sa


revision = "20260702_0070"
down_revision = "20260701_0069"
branch_labels = None
depends_on = None


_DROP_RELATION_COLS = [
    "incoterm_place",
    "req_ap_date",
    "real_ap_days_validated",
    "real_ap_days",
    "consignment",
    "data_validity",
    "quality_cert_required",
    "delivery_status",
    "transport_mode",
    "transit_days",
]


def _drop_column_if_exists(table: str, column: str) -> None:
    op.execute(
        f"""
        ALTER TABLE "{table}"
        DROP COLUMN IF EXISTS "{column}"
        """
    )


def upgrade() -> None:
    for col in _DROP_RELATION_COLS:
        _drop_column_if_exists("supplier_site_relation", col)


def downgrade() -> None:
    with op.batch_alter_table("supplier_site_relation") as batch_op:
        batch_op.add_column(sa.Column("transit_days", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("transport_mode", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("real_ap_days", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("real_ap_days_validated", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("incoterm_place", sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column("consignment", sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column("data_validity", sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column("quality_cert_required", sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column("delivery_status", sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column("req_ap_date", sa.Date(), nullable=True))

