"""add opportunity_phase_snapshot table

Revision ID: 20260618_0039
Revises: 20260618_0038
Create Date: 2026-06-18
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260618_0039"
down_revision = "20260618_0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "opportunity_phase_snapshot",
        sa.Column("snapshot_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("opportunity_id", sa.Integer(), nullable=False),
        sa.Column("phase_from", sa.String(50), nullable=True),
        sa.Column("phase_to", sa.String(50), nullable=True),
        sa.Column("gate_decision", sa.String(20), nullable=True),
        sa.Column("decided_by", sa.String(255), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("gate_comments", sa.Text(), nullable=True),
        sa.Column("opportunity_snapshot", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("updated_by", sa.String(255), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=True, default=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_by", sa.String(255), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=True, default=1),
        sa.PrimaryKeyConstraint("snapshot_id"),
        sa.ForeignKeyConstraint(
            ["opportunity_id"], ["opportunity.opportunity_id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_opp_phase_snapshot_opportunity_id",
        "opportunity_phase_snapshot",
        ["opportunity_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_opp_phase_snapshot_opportunity_id", table_name="opportunity_phase_snapshot")
    op.drop_table("opportunity_phase_snapshot")
