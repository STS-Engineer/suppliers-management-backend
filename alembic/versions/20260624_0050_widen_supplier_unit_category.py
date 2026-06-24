"""widen supplier_unit.category to match model

Revision ID: 20260624_0050
Revises: 20260624_0049
Create Date: 2026-06-24
"""

from alembic import op
import sqlalchemy as sa

revision = "20260624_0050"
down_revision = "20260624_0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "supplier_unit",
        "category",
        existing_type=sa.String(length=5),
        type_=sa.String(length=500),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "supplier_unit",
        "category",
        existing_type=sa.String(length=500),
        type_=sa.String(length=5),
        existing_nullable=True,
    )
