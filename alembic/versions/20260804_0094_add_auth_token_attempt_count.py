"""Add attempt_count to auth_token for OTP brute-force lockout

Tracks failed verify-OTP guesses against a single issued
password_reset_otp token so it can be invalidated after
settings.OTP_MAX_ATTEMPTS wrong guesses instead of being
guessable indefinitely until it expires.

Revision ID: 20260804_0094
Revises: 20260730_0093
Create Date: 2026-08-04
"""

import sqlalchemy as sa
from alembic import op

revision = "20260804_0094"
down_revision = "20260730_0093"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "auth_token",
        sa.Column(
            "attempt_count",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("auth_token", "attempt_count")
