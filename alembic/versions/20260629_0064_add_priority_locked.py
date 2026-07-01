"""add priority_locked to opportunity

Revision ID: 20260629_0064
Revises: 20260629_0063
Create Date: 2026-06-29
"""

from alembic import op
import sqlalchemy as sa

revision = "20260629_0064"
down_revision = "20260629_0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "opportunity",
        sa.Column(
            "priority_locked",
            sa.Boolean(),
            nullable=True,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("opportunity", "priority_locked")
