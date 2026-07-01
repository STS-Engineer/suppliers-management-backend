"""add relation_validation_status to supplier_site_relation

Revision ID: 20260625_0057
Revises: 20260625_0056
Create Date: 2026-06-25
"""
from alembic import op
import sqlalchemy as sa

revision = "20260625_0057"
down_revision = "20260625_0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "supplier_site_relation",
        sa.Column(
            "validation_status",
            sa.String(50),
            nullable=True,
            server_default="draft",
        ),
    )
    # Grandfather all existing relations as approved (already visible in panel)
    op.execute("UPDATE supplier_site_relation SET validation_status = 'approved'")


def downgrade() -> None:
    op.drop_column("supplier_site_relation", "validation_status")
