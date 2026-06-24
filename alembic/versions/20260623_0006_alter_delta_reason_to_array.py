"""Alter delta_reason to TEXT[] for multi-value support.

Revision ID: 20260623_0006
Revises: 20260623_0005
Create Date: 2026-06-23

Why: delta_reason is now multi-valued — one opportunity-FY can have several
reasons explaining the EOY/budget gap (e.g. "Supplier Issue" + "Lower volume").
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260623_0006"
down_revision = "20260623_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "opportunity_budget_year",
        "delta_reason",
        type_=postgresql.ARRAY(sa.Text()),
        postgresql_using=(
            "CASE WHEN delta_reason IS NULL THEN NULL "
            "ELSE ARRAY[delta_reason]::text[] END"
        ),
    )


def downgrade() -> None:
    op.alter_column(
        "opportunity_budget_year",
        "delta_reason",
        type_=sa.String(100),
        postgresql_using=(
            "CASE WHEN delta_reason IS NULL THEN NULL "
            "WHEN array_length(delta_reason, 1) > 0 THEN (delta_reason)[1] "
            "ELSE NULL END"
        ),
    )
