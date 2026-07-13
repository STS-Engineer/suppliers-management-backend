"""add saving_nature (Hard/Soft) to opportunity

Accounting nature of a saving, orthogonal to opportunity_type (the lever):
  Hard = real cost reduction recognized in P&L / EBITDA (price actually drops)
  Soft = cost avoidance (an inflationary/future cost is avoided; spend does not drop)

Left nullable with NO backfill on purpose: classifying an existing opportunity as
Hard or Soft is a business (finance) decision, not something to infer automatically.

Revision ID: 20260713_0084
Revises: 20260713_0083
Create Date: 2026-07-13 11:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260713_0084"
down_revision = "20260713_0083"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("opportunity")}
    if "saving_nature" not in columns:
        op.add_column(
            "opportunity",
            sa.Column("saving_nature", sa.String(length=10), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("opportunity")}
    if "saving_nature" in columns:
        op.drop_column("opportunity", "saving_nature")
