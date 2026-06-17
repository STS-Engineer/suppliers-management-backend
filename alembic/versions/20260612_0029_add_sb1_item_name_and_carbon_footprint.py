"""Add sb1_item_name to supplier_site_relation and create supplier_carbon_footprint table.

Revision ID: 20260612_0029
Revises: 20260611_0028
Create Date: 2026-06-12
"""

from alembic import op
import sqlalchemy as sa

revision = "20260612_0029"
down_revision = "20260611_0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Store the originating SB1 item name so relation_lookup can be rebuilt
    # by SB1 name even after supplier_unit.supplier_code changed to SB9 entity name.
    op.add_column(
        "supplier_site_relation",
        sa.Column("sb1_item_name", sa.String(200), nullable=True),
    )

    # Carbon footprint data from SB8 board (one row per supplier entity × plant × year)
    op.create_table(
        "supplier_carbon_footprint",
        sa.Column("id_carbon_footprint", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("id_supplier_unit", sa.Integer(), sa.ForeignKey("supplier_unit.id_supplier_unit"), nullable=True),
        sa.Column("id_relation", sa.Integer(), sa.ForeignKey("supplier_site_relation.id_relation"), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("carbon_fp_grade", sa.String(5), nullable=True),
        sa.Column("purchase_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("weighted_footprint", sa.Numeric(12, 6), nullable=True),
        sa.Column("production_fp_grade", sa.String(5), nullable=True),
        sa.Column("transport_impact", sa.Numeric(10, 4), nullable=True),
        sa.Column("global_fp_impact", sa.Numeric(10, 4), nullable=True),
        sa.Column("supplier_origin", sa.String(100), nullable=True),
        sa.Column("supplier_continent", sa.String(100), nullable=True),
        sa.Column("site_location", sa.String(100), nullable=True),
        sa.Column("site_continent", sa.String(100), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("supplier_carbon_footprint")
    op.drop_column("supplier_site_relation", "sb1_item_name")
