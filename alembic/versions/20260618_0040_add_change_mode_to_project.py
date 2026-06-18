"""add change_mode and change_mode_comment to project

Revision ID: 20260618_0040
Revises: 20260618_0039
Create Date: 2026-06-18
"""
from alembic import op
import sqlalchemy as sa

revision = "20260618_0040"
down_revision = "20260618_0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("project", sa.Column("change_mode", sa.String(50), nullable=True))
    op.add_column("project", sa.Column("change_mode_comment", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("project", "change_mode_comment")
    op.drop_column("project", "change_mode")
