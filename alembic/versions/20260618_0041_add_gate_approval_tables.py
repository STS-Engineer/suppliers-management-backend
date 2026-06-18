"""add gate_approval_request and gate_approval_vote tables

Revision ID: 20260618_0041
Revises: 20260618_0040
Create Date: 2026-06-18
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260618_0041"
down_revision = "20260618_0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gate_approval_request",
        sa.Column("request_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("opportunity_id", sa.Integer(), nullable=False),
        sa.Column("phase_from", sa.String(50), nullable=True),
        sa.Column("requested_by", sa.String(255), nullable=True),
        sa.Column("requested_at", sa.DateTime(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=True),
        sa.Column("consensus_result", sa.String(20), nullable=True),
        sa.Column("applied_at", sa.DateTime(), nullable=True),
        sa.Column("opportunity_snapshot", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by", sa.String(255), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_by", sa.String(255), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.PrimaryKeyConstraint("request_id"),
        sa.ForeignKeyConstraint(["opportunity_id"], ["opportunity.opportunity_id"], ondelete="CASCADE"),
    )
    op.create_index("ix_gate_approval_request_opportunity_id", "gate_approval_request", ["opportunity_id"])

    op.create_table(
        "gate_approval_vote",
        sa.Column("vote_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("approver_email", sa.String(255), nullable=True),
        sa.Column("access_token", sa.String(36), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(), nullable=True),
        sa.Column("accessed_at", sa.DateTime(), nullable=True),
        sa.Column("decision", sa.String(20), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by", sa.String(255), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_by", sa.String(255), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.PrimaryKeyConstraint("vote_id"),
        sa.ForeignKeyConstraint(["request_id"], ["gate_approval_request.request_id"], ondelete="CASCADE"),
    )
    op.create_index("ix_gate_approval_vote_request_id", "gate_approval_vote", ["request_id"])
    op.create_index("ix_gate_approval_vote_access_token", "gate_approval_vote", ["access_token"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_gate_approval_vote_access_token", table_name="gate_approval_vote")
    op.drop_index("ix_gate_approval_vote_request_id", table_name="gate_approval_vote")
    op.drop_table("gate_approval_vote")
    op.drop_index("ix_gate_approval_request_opportunity_id", table_name="gate_approval_request")
    op.drop_table("gate_approval_request")
