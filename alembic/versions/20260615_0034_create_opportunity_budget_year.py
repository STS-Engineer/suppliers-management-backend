"""create opportunity_budget_year table

Client request 15/06/2026 — per-fiscal-year budgeting module. One row per
opportunity per fiscal year, holding the pro-rata saving that lands in that year
and the buyer's per-year budget decision (Empty | Opportunity | Budgeted).
Source of truth for the budgeting page; Opportunity.budget_status is a derived
rollup over these rows.

Revision ID: 20260615_0034
Revises: 20260614_0033
Create Date: 2026-06-15
"""

from alembic import op
import sqlalchemy as sa

revision = "20260615_0034"
down_revision = "20260614_0033"
branch_labels = None
depends_on = None

GOVERNANCE = [
    sa.Column("updated_at", sa.DateTime(), nullable=True),
    sa.Column("updated_by", sa.String(200), nullable=True),
    sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    sa.Column("deleted_at", sa.DateTime(), nullable=True),
    sa.Column("deleted_by", sa.String(200), nullable=True),
    sa.Column("row_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    if "opportunity_budget_year" not in existing:
        op.create_table(
            "opportunity_budget_year",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("opportunity_id", sa.Integer(), nullable=False),
            sa.Column("fiscal_year", sa.Integer(), nullable=False),
            sa.Column("applicable_amount", sa.Numeric(18, 2), nullable=True),
            sa.Column("portion_kind", sa.String(20), nullable=True),
            sa.Column("suggested_status", sa.String(20), nullable=True),
            sa.Column("budget_status", sa.String(20), nullable=True),
            sa.Column("status_locked_at", sa.DateTime(), nullable=True),
            sa.Column("status_locked_by", sa.String(200), nullable=True),
            *GOVERNANCE,
            sa.ForeignKeyConstraint(
                ["opportunity_id"], ["opportunity.opportunity_id"], ondelete="CASCADE"
            ),
            sa.UniqueConstraint("opportunity_id", "fiscal_year", name="uq_oby_opp_year"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())
    if "opportunity_budget_year" in existing:
        op.drop_table("opportunity_budget_year")
