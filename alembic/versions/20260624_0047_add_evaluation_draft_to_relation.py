"""add evaluation_draft jsonb column to supplier_site_relation

Revision ID: 20260624_0047
Revises: 20260623_0006_alter_delta_reason_to_array
Create Date: 2026-06-24
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260624_0047"
down_revision = "20260623_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "supplier_site_relation",
        sa.Column("evaluation_draft", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("supplier_site_relation", "evaluation_draft")
