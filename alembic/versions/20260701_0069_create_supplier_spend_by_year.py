"""create supplier_spend_by_year table

Revision ID: 20260701_0069
Revises: 20260701_0068
Create Date: 2026-07-01
"""

from alembic import op
import sqlalchemy as sa


revision = "20260701_0069"
down_revision = "20260701_0068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "supplier_spend_by_year",
        sa.Column("id_spend", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("id_relation", sa.Integer(), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("spend_value", sa.Numeric(18, 2), nullable=False),
        sa.Column("spend_currency", sa.String(length=10), nullable=False, server_default="EUR"),
        sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("created_by", sa.String(length=200), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(
            ["id_relation"],
            ["supplier_site_relation.id_relation"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("id_relation", "fiscal_year", name="uq_spend_relation_year"),
    )
    op.create_index(
        "idx_supplier_spend_by_year_relation",
        "supplier_spend_by_year",
        ["id_relation"],
    )


def downgrade() -> None:
    op.drop_index("idx_supplier_spend_by_year_relation", table_name="supplier_spend_by_year")
    op.drop_table("supplier_spend_by_year")
