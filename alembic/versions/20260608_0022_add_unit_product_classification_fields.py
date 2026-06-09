"""Add product classification and sustainability fields to supplier_unit.

Revision ID: 20260608_0022
Revises: 20260608_0021
Create Date: 2026-06-08
"""

from alembic import op
import sqlalchemy as sa

revision = "20260608_0022"
down_revision = "20260608_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("supplier_unit", sa.Column("family", sa.String(500), nullable=True))
    op.add_column("supplier_unit", sa.Column("sub_family", sa.String(500), nullable=True))
    op.add_column("supplier_unit", sa.Column("product_line", sa.String(500), nullable=True))
    op.add_column("supplier_unit", sa.Column("website", sa.String(500), nullable=True))
    op.add_column("supplier_unit", sa.Column("carbon_footprint", sa.String(100), nullable=True))
    op.add_column("supplier_unit", sa.Column("green_electricity_pct", sa.String(10), nullable=True))
    op.add_column("supplier_unit", sa.Column("copper_brass_pct", sa.String(10), nullable=True))
    op.add_column("supplier_unit", sa.Column("category", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("supplier_unit", "category")
    op.drop_column("supplier_unit", "copper_brass_pct")
    op.drop_column("supplier_unit", "green_electricity_pct")
    op.drop_column("supplier_unit", "carbon_footprint")
    op.drop_column("supplier_unit", "website")
    op.drop_column("supplier_unit", "product_line")
    op.drop_column("supplier_unit", "sub_family")
    op.drop_column("supplier_unit", "family")
