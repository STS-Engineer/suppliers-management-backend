"""fix purchasing value fields: rename Saving_score, add budget_status, val_date

Revision ID: 20260602_0009
Revises: 20260602_0008
Create Date: 2026-06-02
"""

from alembic import op
import sqlalchemy as sa


revision = "20260602_0009"
down_revision = "20260602_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if "opportunity" not in existing_tables:
        return  # table was not created yet — nothing to migrate

    columns = {col["name"] for col in inspector.get_columns("opportunity")}

    # Rename Saving_score -> payback_score
    if "Saving_score" in columns and "payback_score" not in columns:
        op.alter_column("opportunity", "Saving_score", new_column_name="payback_score")
    elif "Saving_score" not in columns and "payback_score" not in columns:
        op.add_column(
            "opportunity", sa.Column("payback_score", sa.Numeric(10, 2), nullable=True)
        )

    # Add budget_status
    if "budget_status" not in columns:
        op.add_column(
            "opportunity", sa.Column("budget_status", sa.String(50), nullable=True)
        )

    # Add val_date
    if "val_date" not in columns:
        op.add_column("opportunity", sa.Column("val_date", sa.Date(), nullable=True))

    # Ensure forecast_comment exists on monthly_financial
    if "monthly_financial" in existing_tables:
        mf_cols = {col["name"] for col in inspector.get_columns("monthly_financial")}
        if "forecast_comment" not in mf_cols:
            op.add_column(
                "monthly_financial",
                sa.Column("forecast_comment", sa.Text(), nullable=True),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if "opportunity" in existing_tables:
        columns = {col["name"] for col in inspector.get_columns("opportunity")}
        if "val_date" in columns:
            op.drop_column("opportunity", "val_date")
        if "budget_status" in columns:
            op.drop_column("opportunity", "budget_status")
        if "payback_score" in columns:
            op.alter_column(
                "opportunity", "payback_score", new_column_name="Saving_score"
            )
