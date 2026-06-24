"""Add delta_reason to opportunity_budget_year.

Revision ID: 20260623_0005
Revises: 20260521_0004
Create Date: 2026-06-23

Why: KPI chart "Delta EOY vs Budget by plant, stacked by main reason" requires
a per-FY reason field on OpportunityBudgetYear. Values come from the Monday.com
delta-reason taxonomy (As planned / Supplier Issue / Price increase / etc.).
"""

from alembic import op
import sqlalchemy as sa


revision = "20260623_0005"
down_revision = "20260622_0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "opportunity_budget_year",
        sa.Column("delta_reason", sa.String(100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("opportunity_budget_year", "delta_reason")
