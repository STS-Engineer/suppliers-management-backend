"""Rename supplier_unit.supplier_code to supplier_name; drop unused columns/tables

Renames supplier_unit.supplier_code -> supplier_name (the column always held the
supplier/entity name, not a code) and drops columns/tables confirmed unused:
- supplier_unit: product_type, product_category, amount_value, amount_currency
- supplier_group: exit_supplier, strategic_reason
- supplier_site_relation: annual_spend_currency, preferred_dev_supplier, sb1_item_name
- opportunity: results, gate_conditions
- supplier_category, supplier_group_category tables (redundant with the
  commodity/family/sub_family/product_line taxonomy already on supplier_unit)

Revision ID: 20260702_0072
Revises: 20260702_0071
Create Date: 2026-07-02
"""

from alembic import op
import sqlalchemy as sa


revision = "20260702_0072"
down_revision = "20260702_0071"
branch_labels = None
depends_on = None


_DROP_COLS = [
    ("supplier_unit", "product_type"),
    ("supplier_unit", "product_category"),
    ("supplier_unit", "amount_value"),
    ("supplier_unit", "amount_currency"),
    ("supplier_group", "exit_supplier"),
    ("supplier_group", "strategic_reason"),
    ("supplier_site_relation", "annual_spend_currency"),
    ("supplier_site_relation", "preferred_dev_supplier"),
    ("supplier_site_relation", "sb1_item_name"),
    ("opportunity", "results"),
    ("opportunity", "gate_conditions"),
]


def _drop_column_if_exists(table: str, column: str) -> None:
    op.execute(
        f"""
        ALTER TABLE "{table}"
        DROP COLUMN IF EXISTS "{column}"
        """
    )


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE "supplier_unit" DROP CONSTRAINT IF EXISTS uq_supplier_unit_group_code
        """
    )
    op.execute(
        """
        ALTER TABLE "supplier_unit" RENAME COLUMN "supplier_code" TO "supplier_name"
        """
    )
    op.create_unique_constraint(
        "uq_supplier_unit_group_code",
        "supplier_unit",
        ["id_group", "supplier_name"],
    )

    for table, col in _DROP_COLS:
        _drop_column_if_exists(table, col)

    op.execute('DROP TABLE IF EXISTS "supplier_group_category"')
    op.execute('DROP TABLE IF EXISTS "supplier_category"')


def downgrade() -> None:
    op.create_table(
        "supplier_category",
        sa.Column("id_category", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("category_key", sa.String(length=100), nullable=False, unique=True),
        sa.Column("category_label", sa.String(length=100), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by", sa.String(length=200), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_by", sa.String(length=200), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_table(
        "supplier_group_category",
        sa.Column("id_group_category", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "id_group",
            sa.Integer,
            sa.ForeignKey("supplier_group.id_group", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "id_category",
            sa.Integer,
            sa.ForeignKey("supplier_category.id_category", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by", sa.String(length=200), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_by", sa.String(length=200), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("id_group", "id_category", name="uq_supplier_group_category"),
    )

    with op.batch_alter_table("opportunity") as batch_op:
        batch_op.add_column(sa.Column("gate_conditions", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("results", sa.Numeric(18, 2), nullable=True))

    with op.batch_alter_table("supplier_site_relation") as batch_op:
        batch_op.add_column(sa.Column("sb1_item_name", sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column("preferred_dev_supplier", sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column("annual_spend_currency", sa.String(length=10), nullable=True))

    with op.batch_alter_table("supplier_group") as batch_op:
        batch_op.add_column(sa.Column("strategic_reason", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("exit_supplier", sa.Boolean(), nullable=False, server_default="false"))

    with op.batch_alter_table("supplier_unit") as batch_op:
        batch_op.add_column(sa.Column("amount_currency", sa.String(length=10), nullable=True))
        batch_op.add_column(sa.Column("amount_value", sa.Numeric(18, 2), nullable=True))
        batch_op.add_column(sa.Column("product_category", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("product_type", sa.String(length=255), nullable=True))

    op.execute(
        """
        ALTER TABLE "supplier_unit" DROP CONSTRAINT IF EXISTS uq_supplier_unit_group_code
        """
    )
    op.execute(
        """
        ALTER TABLE "supplier_unit" RENAME COLUMN "supplier_name" TO "supplier_code"
        """
    )
    op.create_unique_constraint(
        "uq_supplier_unit_group_code",
        "supplier_unit",
        ["id_group", "supplier_code"],
    )
