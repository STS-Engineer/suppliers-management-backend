"""add is_active and inactivated_at to supplier_unit

Revision ID: 20260624_0048
Revises: 20260624_0047
Create Date: 2026-06-24
"""

from alembic import op
import sqlalchemy as sa

revision = "20260624_0048"
down_revision = "20260624_0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "supplier_unit",
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
    )
    op.add_column(
        "supplier_unit",
        sa.Column("inactivated_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("supplier_unit", "inactivated_at")
    op.drop_column("supplier_unit", "is_active")
