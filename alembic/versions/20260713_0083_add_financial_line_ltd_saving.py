"""add cumulated_real_saving_ltd to financial_line

Life-to-date (inception-to-date) realized savings, distinct from the existing
cumulated_real_saving which is now scoped to the current calendar year (YTD basis).

Revision ID: 20260713_0083
Revises: 20260710_0082
Create Date: 2026-07-13 10:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


revision = "20260713_0083"
down_revision = "20260710_0082"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("financial_line")}
    if "cumulated_real_saving_ltd" not in columns:
        op.add_column(
            "financial_line",
            sa.Column("cumulated_real_saving_ltd", sa.Numeric(18, 2), nullable=True),
        )

    # Backfill life-to-date = Σ of all (non-deleted) monthly actual_saving per line.
    bind.execute(text("""
        UPDATE financial_line fl
        SET cumulated_real_saving_ltd = sub.total
        FROM (
            SELECT financial_line_id, SUM(actual_saving) AS total
            FROM monthly_financial
            WHERE actual_saving IS NOT NULL
              AND is_deleted = false
            GROUP BY financial_line_id
        ) sub
        WHERE fl.financial_line_id = sub.financial_line_id
    """))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("financial_line")}
    if "cumulated_real_saving_ltd" in columns:
        op.drop_column("financial_line", "cumulated_real_saving_ltd")
