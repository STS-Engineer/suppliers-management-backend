"""add submitted_for_review_by and review_comment to supplier_site_relation

Revision ID: 20260626_0058
Revises: 20260625_0057
Create Date: 2026-06-26
"""
from alembic import op
import sqlalchemy as sa

revision = "20260626_0058"
down_revision = "20260625_0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "supplier_site_relation",
        sa.Column("submitted_for_review_by", sa.String(200), nullable=True),
    )
    op.add_column(
        "supplier_site_relation",
        sa.Column("review_comment", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("supplier_site_relation", "review_comment")
    op.drop_column("supplier_site_relation", "submitted_for_review_by")
