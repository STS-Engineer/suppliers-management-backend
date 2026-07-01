"""Add notification table

Revision ID: 20260625_0053
Revises: 20260625_0052
Create Date: 2026-06-25
"""

import sqlalchemy as sa
from alembic import op

revision = "20260625_0053"
down_revision = "20260625_0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "recipient_id",
            sa.Integer,
            sa.ForeignKey("access_identity.id_identity", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("notification_type", sa.String(50), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.String(500), nullable=True),
        sa.Column("action_url", sa.String(255), nullable=True),
        sa.Column("metadata_json", sa.Text, nullable=True),
        sa.Column("is_read", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("read_at", sa.DateTime, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_notification_recipient_id", "notification", ["recipient_id"])
    op.create_index("ix_notification_is_read", "notification", ["is_read"])


def downgrade() -> None:
    op.drop_table("notification")
