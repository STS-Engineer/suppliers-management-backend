"""add access identity table

Revision ID: 20260525_0005
Revises: 20260521_0004
Create Date: 2026-05-25
"""

from alembic import op
import sqlalchemy as sa


revision = "20260525_0005"
down_revision = "119356af9cba"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "access_identity",
        sa.Column("id_identity", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.String(length=200), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("access_profile", sa.String(length=100), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column(
            "auth_source",
            sa.String(length=50),
            nullable=False,
            server_default=sa.text("'local'"),
        ),
        sa.Column("external_subject", sa.String(length=255), nullable=True),
        sa.Column("external_directory", sa.String(length=150), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("email", name="uq_access_identity_email"),
    )

    op.execute(
        """
        INSERT INTO access_identity (
            email,
            full_name,
            access_profile,
            password_hash,
            auth_source,
            is_active
        )
        VALUES
            (
                'olivier.grimaud@avocarbon.com',
            'Olivier GRIMAUD',
                'vp_conversion',
                '$pbkdf2-sha256$29000$0pqTMkYopdS6t/Z.zzmHkA$6UfUl5443TSAn3Fck2GQ7aXAMO.FWH4v0/ZyYDJa2Kg',
                'local',
                TRUE
            ),
            (
            'jiehua.zhang@avocarbon.com',
            'Jiehua ZHANG',
            'purchasing_director',
            '$pbkdf2-sha256$29000$0pqTMkYopdS6t/Z.zzmHkA$6UfUl5443TSAn3Fck2GQ7aXAMO.FWH4v0/ZyYDJa2Kg',
            'local',
            TRUE
            ),
            (
                'supplier.owner@avocarbon.com',
                'Supplier Owner',
                'supplier_owner',
                '$pbkdf2-sha256$29000$irE2pjSm9N6bU8oZw1hLSQ$dFou886RYGdGuPclMpio7PB/R8yMF7JL5P/CBsOOIdU',
                'local',
                TRUE
            )
        """
    )


def downgrade() -> None:
    op.drop_table("access_identity")
