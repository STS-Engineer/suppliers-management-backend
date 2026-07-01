"""Add committee_member, committee_review, committee_decision tables.

Revision ID: 20260626_0060
Revises: 20260626_0059
Create Date: 2026-06-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260626_0060"
down_revision = "20260626_0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "committee_member",
        sa.Column("id_member", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("position", sa.String(100), nullable=False),
        sa.Column("email", sa.String(200), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.current_timestamp(),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id_member"),
        sa.UniqueConstraint("email", name="uq_committee_member_email"),
    )

    op.create_table(
        "committee_review",
        sa.Column("id_review", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("id_relation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("initiated_by", sa.String(200), nullable=True),
        sa.Column(
            "initiated_at",
            sa.DateTime(),
            server_default=sa.func.current_timestamp(),
            nullable=True,
        ),
        sa.Column("all_decided_at", sa.DateTime(), nullable=True),
        sa.Column("final_decision", sa.String(50), nullable=True),
        sa.Column("final_decision_by", sa.String(200), nullable=True),
        sa.Column("final_decision_at", sa.DateTime(), nullable=True),
        sa.Column("final_decision_comments", sa.Text(), nullable=True),
        sa.Column("supplier_snapshot", JSONB, nullable=True),
        sa.ForeignKeyConstraint(
            ["id_relation"], ["supplier_site_relation.id_relation"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id_review"),
    )
    op.create_index(
        "ix_committee_review_id_relation", "committee_review", ["id_relation"]
    )

    op.create_table(
        "committee_decision",
        sa.Column("id_decision", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("id_review", sa.Integer(), nullable=False),
        sa.Column("member_email", sa.String(200), nullable=False),
        sa.Column("member_name", sa.String(200), nullable=True),
        sa.Column("member_position", sa.String(100), nullable=True),
        sa.Column("access_token", sa.String(36), nullable=False),
        sa.Column("token_expires_at", sa.DateTime(), nullable=True),
        sa.Column("accessed_at", sa.DateTime(), nullable=True),
        sa.Column("decision", sa.String(20), nullable=True),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.ForeignKeyConstraint(
            ["id_review"], ["committee_review.id_review"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id_decision"),
        sa.UniqueConstraint("access_token", name="uq_committee_decision_token"),
    )
    op.create_index(
        "ix_committee_decision_id_review", "committee_decision", ["id_review"]
    )
    op.create_index(
        "ix_committee_decision_access_token", "committee_decision", ["access_token"]
    )


def downgrade() -> None:
    op.drop_table("committee_decision")
    op.drop_table("committee_review")
    op.drop_table("committee_member")
