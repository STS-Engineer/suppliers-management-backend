"""create email delivery history table

Revision ID: 119356af9cba
Revises: 20260521_0004
Create Date: 2026-05-22 11:24:12.169218
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "119356af9cba"
down_revision = "20260521_0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "email_delivery_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("recipient_email", sa.String(length=200), nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("delivery_status", sa.String(length=50), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_index(
        "ix_email_delivery_history_recipient_email",
        "email_delivery_history",
        ["recipient_email"],
    )

    op.create_index(
        "ix_email_delivery_history_delivery_status",
        "email_delivery_history",
        ["delivery_status"],
    )

    op.create_index(
        "ix_email_delivery_history_sent_at",
        "email_delivery_history",
        ["sent_at"],
    )


def downgrade():
    op.drop_index(
        "ix_email_delivery_history_sent_at",
        table_name="email_delivery_history",
    )

    op.drop_index(
        "ix_email_delivery_history_delivery_status",
        table_name="email_delivery_history",
    )

    op.drop_index(
        "ix_email_delivery_history_recipient_email",
        table_name="email_delivery_history",
    )

    op.drop_table("email_delivery_history")
