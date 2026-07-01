"""add is_active to supplier_site_relation

Revision ID: 20260629_0062
Revises: 20260626_0061
Create Date: 2026-06-29
"""
from alembic import op
import sqlalchemy as sa

revision = "20260629_0062"
down_revision = "20260626_0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "supplier_site_relation",
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("supplier_site_relation", "is_active")
