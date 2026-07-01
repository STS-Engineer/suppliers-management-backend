"""Add suggested_supplier_status and suggested_strategic_mention to committee_decision

Revision ID: 20260626_0061
Revises: 20260626_0060
Create Date: 2026-06-26
"""
from alembic import op
import sqlalchemy as sa

revision = "20260626_0061"
down_revision = "20260626_0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "committee_decision",
        sa.Column("suggested_supplier_status", sa.String(100), nullable=True),
    )
    op.add_column(
        "committee_decision",
        sa.Column("suggested_strategic_mention", sa.String(200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("committee_decision", "suggested_strategic_mention")
    op.drop_column("committee_decision", "suggested_supplier_status")
