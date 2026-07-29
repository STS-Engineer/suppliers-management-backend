"""add access_identity.approved_by

Tracks which approver's email approved an account request, so the Approved
requests tab can be scoped to "approved by me".

Revision ID: 20260729_0090
Revises: 20260724_0089
Create Date: 2026-07-29
"""
from alembic import op
import sqlalchemy as sa

revision = "20260729_0090"
down_revision = "20260724_0089"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "access_identity",
        sa.Column("approved_by", sa.String(200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("access_identity", "approved_by")
