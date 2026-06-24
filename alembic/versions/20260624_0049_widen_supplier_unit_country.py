"""widen supplier_unit.country to match model

Revision ID: 20260624_0049
Revises: 20260624_0048
Create Date: 2026-06-24
"""

from alembic import op
import sqlalchemy as sa

revision = "20260624_0049"
down_revision = "20260624_0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "supplier_unit",
        "country",
        existing_type=sa.String(length=5),
        type_=sa.String(length=100),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "supplier_unit",
        "country",
        existing_type=sa.String(length=100),
        type_=sa.String(length=5),
        existing_nullable=True,
    )
