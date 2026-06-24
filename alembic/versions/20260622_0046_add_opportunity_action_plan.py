"""add opportunity_action_plan table

Revision ID: 20260622_0046
Revises: 20260619_0045
Create Date: 2026-06-22
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260622_0046"
down_revision = "20260619_0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "opportunity_action_plan",
        sa.Column("action_plan_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "opportunity_id",
            sa.Integer(),
            sa.ForeignKey("opportunity.opportunity_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("phase_status", sa.String(50), nullable=True),
        sa.Column("plan_title", sa.Text(), nullable=True),
        sa.Column("plan_code", sa.String(100), nullable=True),
        sa.Column("plan_data", JSONB(), nullable=True),
        sa.Column("external_push_status", sa.String(20), nullable=True),
        sa.Column("external_push_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by", sa.String(255), nullable=True),
    )
    op.create_index(
        "idx_opp_action_plan_opp_id",
        "opportunity_action_plan",
        ["opportunity_id"],
    )
    op.create_index(
        "idx_opp_action_plan_code",
        "opportunity_action_plan",
        ["plan_code"],
    )


def downgrade() -> None:
    op.drop_index("idx_opp_action_plan_code", table_name="opportunity_action_plan")
    op.drop_index("idx_opp_action_plan_opp_id", table_name="opportunity_action_plan")
    op.drop_table("opportunity_action_plan")
