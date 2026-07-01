"""Add auth flows: registration_status, auth_token, auth_audit_log

Revision ID: 20260625_0052
Revises: 20260624_0051
Create Date: 2026-06-25
"""

import sqlalchemy as sa
from alembic import op

revision = "20260625_0052"
down_revision = "20260624_0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- access_identity: add registration_status column ------------------
    op.add_column(
        "access_identity",
        sa.Column(
            "registration_status",
            sa.String(50),
            nullable=False,
            server_default="active",
        ),
    )
    # All pre-existing rows are fully active accounts.
    op.execute(
        "UPDATE access_identity SET registration_status = 'active' "
        "WHERE registration_status = 'active'"
    )

    # -- auth_token -------------------------------------------------------
    op.create_table(
        "auth_token",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "identity_id",
            sa.Integer,
            sa.ForeignKey("access_identity.id_identity", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("token_type", sa.String(50), nullable=False),
        sa.Column("expires_at", sa.DateTime, nullable=False),
        sa.Column("used_at", sa.DateTime, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_auth_token_identity_id", "auth_token", ["identity_id"])
    op.create_index("ix_auth_token_token_type", "auth_token", ["token_type"])

    # -- auth_audit_log ---------------------------------------------------
    op.create_table(
        "auth_audit_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("email", sa.String(200), nullable=False),
        sa.Column(
            "identity_id",
            sa.Integer,
            sa.ForeignKey("access_identity.id_identity", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("actor_email", sa.String(200), nullable=True),
        sa.Column("details", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_auth_audit_log_email", "auth_audit_log", ["email"])
    op.create_index("ix_auth_audit_log_event_type", "auth_audit_log", ["event_type"])
    op.create_index("ix_auth_audit_log_identity_id", "auth_audit_log", ["identity_id"])


def downgrade() -> None:
    op.drop_table("auth_audit_log")
    op.drop_table("auth_token")
    op.drop_column("access_identity", "registration_status")
