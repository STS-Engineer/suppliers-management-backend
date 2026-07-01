"""Remove obsolete supplier_unit fields: main_plants, supplier_email, commodity_responsible, copper_brass_pct, GHG fields

Revision ID: 20260625_0055
Revises: 20260625_0054
Create Date: 2026-06-25
"""
from alembic import op
import sqlalchemy as sa

revision = "20260625_0055"
down_revision = "20260625_0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("supplier_unit", "main_plants")
    op.drop_column("supplier_unit", "supplier_email")
    op.drop_column("supplier_unit", "commodity_responsible")
    op.drop_column("supplier_unit", "copper_brass_pct")
    op.drop_column("supplier_unit", "scope1_ghg")
    op.drop_column("supplier_unit", "scope2_ghg")
    op.drop_column("supplier_unit", "ghg_comments")
    op.drop_column("supplier_unit", "ghg_requested_date")
    op.drop_column("supplier_unit", "ghg_completion_pct")


def downgrade() -> None:
    op.add_column("supplier_unit", sa.Column("ghg_completion_pct", sa.String(50), nullable=True))
    op.add_column("supplier_unit", sa.Column("ghg_requested_date", sa.Date(), nullable=True))
    op.add_column("supplier_unit", sa.Column("ghg_comments", sa.Text(), nullable=True))
    op.add_column("supplier_unit", sa.Column("scope2_ghg", sa.Numeric(18, 4), nullable=True))
    op.add_column("supplier_unit", sa.Column("scope1_ghg", sa.Numeric(18, 4), nullable=True))
    op.add_column("supplier_unit", sa.Column("copper_brass_pct", sa.String(10), nullable=True))
    op.add_column("supplier_unit", sa.Column("commodity_responsible", sa.String(200), nullable=True))
    op.add_column("supplier_unit", sa.Column("supplier_email", sa.String(255), nullable=True))
    op.add_column("supplier_unit", sa.Column("main_plants", sa.Text(), nullable=True))
