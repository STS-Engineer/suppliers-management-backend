"""create financial_line and monthly_financial tables

Revision ID: 20260602_0010
Revises: 20260602_0009
Create Date: 2026-06-02
"""

from alembic import op
import sqlalchemy as sa

revision = "20260602_0010"
down_revision = "20260602_0009"
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

    # ── financial_line ────────────────────────────────────────────────
    if "financial_line" not in existing:
        op.create_table(
            "financial_line",
            sa.Column("financial_line_id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("opportunity_id", sa.Integer(), nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=True),
            sa.Column("plant_id", sa.Integer(), nullable=True),
            sa.Column("line_name", sa.Text(), nullable=True),
            sa.Column("budget_status", sa.String(50), nullable=True),
            sa.Column("expected_annual_saving", sa.Numeric(18, 2), nullable=True),
            sa.Column("budget_value", sa.Numeric(18, 2), nullable=True),
            sa.Column("planned_start_date", sa.Date(), nullable=True),
            sa.Column("real_start_date", sa.Date(), nullable=True),
            sa.Column("duration_months", sa.Numeric(6, 2), nullable=True),
            sa.Column("cumulated_real_saving", sa.Numeric(18, 2), nullable=True),
            sa.Column("delta_vs_expected_ytd", sa.Numeric(18, 2), nullable=True),
            sa.Column("delta_vs_budget_ytd", sa.Numeric(18, 2), nullable=True),
            sa.Column("status", sa.String(50), nullable=True),
            sa.Column("follower", sa.String(200), nullable=True),
            sa.Column("comments", sa.Text(), nullable=True),
            sa.Column("forecast_eoy_current", sa.Numeric(18, 2), nullable=True),
            sa.Column("forecast_eoy_last_update", sa.Date(), nullable=True),
            *GOVERNANCE,
            sa.ForeignKeyConstraint(["opportunity_id"], ["opportunity.opportunity_id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["project_id"], ["project.project_id"], ondelete="SET NULL"),
        )

    # ── monthly_financial ─────────────────────────────────────────────
    if "monthly_financial" not in existing:
        op.create_table(
            "monthly_financial",
            sa.Column("monthly_financial_id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("financial_line_id", sa.Integer(), nullable=False),
            sa.Column("period_month", sa.Date(), nullable=True),
            sa.Column("expected_saving", sa.Numeric(18, 2), nullable=True),
            sa.Column("actual_saving", sa.Numeric(18, 2), nullable=True),
            sa.Column("cumulated_expected", sa.Numeric(18, 2), nullable=True),
            sa.Column("cumulated_actual", sa.Numeric(18, 2), nullable=True),
            sa.Column("delta_vs_expected", sa.Numeric(18, 2), nullable=True),
            sa.Column("delta_vs_budget", sa.Numeric(18, 2), nullable=True),
            sa.Column("comment", sa.Text(), nullable=True),
            sa.Column("forecast_eoy_saving", sa.Numeric(18, 2), nullable=True),
            sa.Column("forecast_comment", sa.Text(), nullable=True),
            *GOVERNANCE,
            sa.ForeignKeyConstraint(
                ["financial_line_id"], ["financial_line.financial_line_id"], ondelete="CASCADE"
            ),
        )
    else:
        # table exists — add forecast_comment if missing
        mf_cols = {c["name"] for c in inspector.get_columns("monthly_financial")}
        if "forecast_comment" not in mf_cols:
            op.add_column("monthly_financial", sa.Column("forecast_comment", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())
    if "monthly_financial" in existing:
        op.drop_table("monthly_financial")
    if "financial_line" in existing:
        op.drop_table("financial_line")
