"""Remove unit-level category field (redundant with group commodity cascade)

Revision ID: 20260625_0056
Revises: 20260625_0055
Create Date: 2026-06-25
"""
from alembic import op
import sqlalchemy as sa

revision = "20260625_0056"
down_revision = "20260625_0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("supplier_unit", "category")


def downgrade() -> None:
    op.add_column("supplier_unit", sa.Column("category", sa.String(500), nullable=True))
