"""add created_by to opportunity

Revision ID: 20260708_0079
Revises: 20260707_0078
Create Date: 2026-07-08 11:30:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


revision = "20260708_0079"
down_revision = "20260707_0078"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("opportunity")}
    if "created_by" not in columns:
        op.add_column("opportunity", sa.Column("created_by", sa.String(length=200), nullable=True))

    # Best-effort backfill for historical rows so auditors don't see a blank field
    # everywhere immediately. This is inferred from existing ownership data.
    bind.execute(text("""
        UPDATE opportunity
        SET created_by = COALESCE(created_by, idea_owner, purchasing_owner, updated_by, 'system')
        WHERE created_by IS NULL
    """))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("opportunity")}
    if "created_by" in columns:
        op.drop_column("opportunity", "created_by")
