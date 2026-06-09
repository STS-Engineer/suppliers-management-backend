"""add execution_start_date to opportunity

Revision ID: 20260603_0014
Revises: 20260603_0013
Create Date: 2026-06-03
"""

from alembic import op
import sqlalchemy as sa

revision = "20260603_0014"
down_revision = "20260603_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "opportunity" not in set(inspector.get_table_names()):
        return
    cols = {c["name"] for c in inspector.get_columns("opportunity")}
    if "execution_start_date" not in cols:
        op.add_column("opportunity", sa.Column("execution_start_date", sa.Date(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "opportunity" in set(inspector.get_table_names()):
        cols = {c["name"] for c in inspector.get_columns("opportunity")}
        if "execution_start_date" in cols:
            op.drop_column("opportunity", "execution_start_date")
