"""Add validation_status to supplier_group

Revision ID: 20260625_0054
Revises: 20260625_0053
Create Date: 2026-06-25
"""

from alembic import op
import sqlalchemy as sa

revision = "20260625_0054"
down_revision = "20260625_0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing suppliers are already in the system → approved by default
    op.add_column(
        "supplier_group",
        sa.Column(
            "validation_status",
            sa.String(20),
            nullable=False,
            server_default="approved",
        ),
    )


def downgrade() -> None:
    op.drop_column("supplier_group", "validation_status")
