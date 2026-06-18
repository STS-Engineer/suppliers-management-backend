"""add pending_stp_revision to opportunity

Revision ID: 20260618_0038
Revises: 20260618_0037
Create Date: 2026-06-18 15:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "20260618_0038"
down_revision = "20260618_0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("opportunity")}
    if "pending_stp_revision" not in columns:
        op.add_column(
            "opportunity",
            sa.Column("pending_stp_revision", JSONB, nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("opportunity")}
    if "pending_stp_revision" in columns:
        op.drop_column("opportunity", "pending_stp_revision")
