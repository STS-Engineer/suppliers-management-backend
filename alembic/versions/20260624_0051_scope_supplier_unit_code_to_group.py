"""scope supplier_unit supplier_code uniqueness to group

Revision ID: 20260624_0051
Revises: 20260624_0050
Create Date: 2026-06-24
"""

from alembic import op

revision = "20260624_0051"
down_revision = "20260624_0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_supplier_unit_code", "supplier_unit", type_="unique")
    op.drop_constraint("uq_supplier_unit_supplier_code", "supplier_unit", type_="unique")
    op.create_unique_constraint(
        "uq_supplier_unit_group_code",
        "supplier_unit",
        ["id_group", "supplier_code"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_supplier_unit_group_code", "supplier_unit", type_="unique")
    op.create_unique_constraint(
        "uq_supplier_unit_supplier_code",
        "supplier_unit",
        ["supplier_code"],
    )
    op.create_unique_constraint(
        "uq_supplier_unit_code",
        "supplier_unit",
        ["supplier_code"],
    )
