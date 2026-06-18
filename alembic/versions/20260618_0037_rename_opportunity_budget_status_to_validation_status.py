"""rename opportunity budget_status to validation_status

Revision ID: 20260618_0037
Revises: 20260617_0036
Create Date: 2026-06-18 10:30:00
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260618_0037"
down_revision = "20260617_0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("opportunity")}

    if "budget_status" in columns and "validation_status" not in columns:
        op.alter_column(
            "opportunity",
            "budget_status",
            new_column_name="validation_status",
            existing_type=sa.String(length=50),
            existing_nullable=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("opportunity")}

    if "validation_status" in columns and "budget_status" not in columns:
        op.alter_column(
            "opportunity",
            "validation_status",
            new_column_name="budget_status",
            existing_type=sa.String(length=50),
            existing_nullable=True,
        )
