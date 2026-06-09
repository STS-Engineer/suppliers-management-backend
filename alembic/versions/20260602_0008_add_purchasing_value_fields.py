"""add purchasing value fields to opportunity

Revision ID: 20260602_0008
Revises: 20260602_0007
Create Date: 2026-06-02
"""

from alembic import op
import sqlalchemy as sa


revision = "20260602_0008"
down_revision = "20260602_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Create opportunity table if it doesn't exist yet (idempotent guard)
    existing_tables = inspector.get_table_names()
    if "opportunity" not in existing_tables:
        op.create_table(
            "opportunity",
            sa.Column("opportunity_id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("opportunity_name", sa.Text(), nullable=True),
            sa.Column("opportunity_type", sa.String(255), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("status", sa.String(255), nullable=True),
            sa.Column("idea_owner", sa.String(255), nullable=True),
            sa.Column("purchasing_owner", sa.String(255), nullable=True),
            sa.Column("project_owner", sa.String(255), nullable=True),
            sa.Column("conversion_owner", sa.String(255), nullable=True),
            sa.Column("plant_id", sa.Integer(), nullable=True),
            sa.Column("supplier_id", sa.Integer(), nullable=True),
            sa.Column("expected_annual_saving", sa.Numeric(18, 2), nullable=True),
            sa.Column("planned_start_date", sa.Date(), nullable=True),
            sa.Column("real_start_date", sa.Date(), nullable=True),
            sa.Column("duration_months", sa.Numeric(6, 2), nullable=True),
            sa.Column("results", sa.Numeric(18, 2), nullable=True),
            sa.Column("budget_year", sa.Numeric(4, 0), nullable=True),
            sa.Column("phase_status", sa.String(255), nullable=True),
            sa.Column("validation_decision", sa.String(255), nullable=True),
            sa.Column("status2", sa.String(255), nullable=True),
            sa.Column("change_mode", sa.String(255), nullable=True),
            sa.Column("assumptions_summary", sa.Text(), nullable=True),
            sa.Column("Saving_score", sa.Numeric(10, 2), nullable=True),
            sa.Column("lead_time_score", sa.Numeric(10, 2), nullable=True),
            sa.Column("difficulty_score", sa.Numeric(10, 2), nullable=True),
            sa.Column("priority_score", sa.Numeric(10, 2), nullable=True),
            sa.Column("priority_category", sa.String(255), nullable=True),
            sa.Column("comments", sa.Text(), nullable=True),
            sa.Column("Lead_time", sa.Integer(), nullable=True),
            sa.Column("cash_impact", sa.Numeric(18, 2), nullable=True),
            sa.Column("validation_request_sent_at", sa.DateTime(), nullable=True),
            sa.Column("validation_request_sent_by", sa.String(200), nullable=True),
            # governance columns
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by", sa.String(200), nullable=True),
            sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_by", sa.String(200), nullable=True),
            sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("current_timestamp"), nullable=True),
            sa.PrimaryKeyConstraint("opportunity_id"),
        )
        op.create_table(
            "project",
            sa.Column("project_id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("opportunity_id", sa.Integer(), nullable=True),
            sa.Column("project_name", sa.Text(), nullable=True),
            sa.Column("project_type", sa.String(255), nullable=True),
            sa.Column("project_owner", sa.String(255), nullable=True),
            sa.Column("phase_status", sa.String(255), nullable=True),
            sa.Column("gate_decision", sa.String(255), nullable=True),
            sa.Column("status", sa.String(255), nullable=True),
            sa.Column("planned_end_date", sa.Date(), nullable=True),
            sa.Column("actual_end_date", sa.Date(), nullable=True),
            sa.Column("plant_validation", sa.String(255), nullable=True),
            sa.Column("comments", sa.Text(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by", sa.String(200), nullable=True),
            sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_by", sa.String(200), nullable=True),
            sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("current_timestamp"), nullable=True),
            sa.ForeignKeyConstraint(["opportunity_id"], ["opportunity.opportunity_id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("project_id"),
        )
    else:
        # Table already exists — add only the new columns
        columns = {col["name"] for col in inspector.get_columns("opportunity")}
        if "cash_impact" not in columns:
            op.add_column("opportunity", sa.Column("cash_impact", sa.Numeric(18, 2), nullable=True))
        if "validation_request_sent_at" not in columns:
            op.add_column("opportunity", sa.Column("validation_request_sent_at", sa.DateTime(), nullable=True))
        if "validation_request_sent_by" not in columns:
            op.add_column("opportunity", sa.Column("validation_request_sent_by", sa.String(200), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if "opportunity" in existing_tables:
        columns = {col["name"] for col in inspector.get_columns("opportunity")}
        if "validation_request_sent_by" in columns:
            op.drop_column("opportunity", "validation_request_sent_by")
        if "validation_request_sent_at" in columns:
            op.drop_column("opportunity", "validation_request_sent_at")
        if "cash_impact" in columns:
            op.drop_column("opportunity", "cash_impact")
