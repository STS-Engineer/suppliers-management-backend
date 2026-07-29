"""add avo_member_directory

Local fallback cache of the AVO Carbon employee directory (people with an
email on file), synced from the AVO Carbon Central MCP's list_members tool.
Backs the gate approval Project Manager picker when the live MCP call fails.

Revision ID: 20260729_0091
Revises: 20260729_0090
Create Date: 2026-07-29
"""
from alembic import op
import sqlalchemy as sa

revision = "20260729_0091"
down_revision = "20260729_0090"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "avo_member_directory",
        sa.Column("people_id", sa.Integer(), primary_key=True),
        sa.Column("full_name", sa.String(300), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("work_unit_name", sa.String(255), nullable=True),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_avo_member_directory_email", "avo_member_directory", ["email"]
    )


def downgrade() -> None:
    op.drop_index("ix_avo_member_directory_email", table_name="avo_member_directory")
    op.drop_table("avo_member_directory")
