"""add revision_history to opportunity

Revision ID: 20260706_0075
Revises: 20260703_0074
Create Date: 2026-07-06 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "20260706_0075"
down_revision = "20260703_0074"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("opportunity")}
    if "revision_history" not in columns:
        op.add_column(
            "opportunity",
            sa.Column("revision_history", JSONB, nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("opportunity")}
    if "revision_history" in columns:
        op.drop_column("opportunity", "revision_history")
