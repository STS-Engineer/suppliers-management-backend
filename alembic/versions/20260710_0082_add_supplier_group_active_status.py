"""add is_active/inactivated_at to supplier_group

Revision ID: 20260710_0082
Revises: 20260709_0081
Create Date: 2026-07-10 09:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260710_0082"
down_revision = "20260709_0081"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("supplier_group")}
    if "is_active" not in columns:
        op.add_column(
            "supplier_group",
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        )
    if "inactivated_at" not in columns:
        op.add_column(
            "supplier_group",
            sa.Column("inactivated_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("supplier_group")}
    if "inactivated_at" in columns:
        op.drop_column("supplier_group", "inactivated_at")
    if "is_active" in columns:
        op.drop_column("supplier_group", "is_active")
