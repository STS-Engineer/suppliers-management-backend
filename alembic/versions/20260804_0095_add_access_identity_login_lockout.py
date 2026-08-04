"""Add failed_login_attempts and locked_until to access_identity

Enables a timed lockout after repeated wrong-password sign-in
attempts instead of allowing unlimited guesses (settings.LOGIN_MAX_ATTEMPTS
/ settings.LOGIN_LOCKOUT_MINUTES).

Revision ID: 20260804_0095
Revises: 20260804_0094
Create Date: 2026-08-04
"""

import sqlalchemy as sa
from alembic import op

revision = "20260804_0095"
down_revision = "20260804_0094"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "access_identity",
        sa.Column(
            "failed_login_attempts",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "access_identity",
        sa.Column("locked_until", sa.DateTime, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("access_identity", "locked_until")
    op.drop_column("access_identity", "failed_login_attempts")
