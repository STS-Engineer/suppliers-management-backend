"""budget year jan1 and closure

Fix fiscal year to calendar year (Jan 1 – Dec 31), add is_additional flag on
opportunity_budget_year, and create budget_year_closure table for director
budget-closure tracking.

Revision ID: 20260629_0063
Revises: 20260629_0062
Create Date: 2026-06-29
"""

from alembic import op
import sqlalchemy as sa

revision = "20260629_0063"
down_revision = "20260629_0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    # Add is_additional to opportunity_budget_year
    if "opportunity_budget_year" in existing_tables:
        cols = {c["name"] for c in inspector.get_columns("opportunity_budget_year")}
        if "is_additional" not in cols:
            op.add_column(
                "opportunity_budget_year",
                sa.Column(
                    "is_additional",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("false"),
                ),
            )

    # Create budget_year_closure table
    if "budget_year_closure" not in existing_tables:
        op.create_table(
            "budget_year_closure",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("fiscal_year", sa.Integer(), nullable=False, unique=True),
            sa.Column("closed_at", sa.DateTime(), nullable=False),
            sa.Column("closed_by", sa.String(200), nullable=False),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "budget_year_closure" in existing_tables:
        op.drop_table("budget_year_closure")

    if "opportunity_budget_year" in existing_tables:
        cols = {c["name"] for c in inspector.get_columns("opportunity_budget_year")}
        if "is_additional" in cols:
            op.drop_column("opportunity_budget_year", "is_additional")
