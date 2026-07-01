"""add commodity to supplier_unit

Revision ID: 20260630_0065
Revises: 20260629_0064
Create Date: 2026-06-30
"""

from alembic import op
import sqlalchemy as sa

revision = "20260630_0065"
down_revision = "20260629_0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "supplier_unit",
        sa.Column("commodity", sa.String(500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("supplier_unit", "commodity")
