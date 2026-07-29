"""add role_name to avo_member_directory

Carries each person's primary role/title (from the MCP's list_members
assignments) alongside the directory entry, so pickers can show it.

Revision ID: 20260729_0092
Revises: 20260729_0091
Create Date: 2026-07-29
"""
from alembic import op
import sqlalchemy as sa

revision = "20260729_0092"
down_revision = "20260729_0091"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "avo_member_directory",
        sa.Column("role_name", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("avo_member_directory", "role_name")
