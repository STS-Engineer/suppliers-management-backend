"""add study_start_date to opportunity

Revision ID: 20260604_0017
Revises: 20260603_0016
Create Date: 2026-06-04
"""

from alembic import op
import sqlalchemy as sa

revision = "20260604_0017"
down_revision = "20260603_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "opportunity" not in set(inspector.get_table_names()):
        return
    cols = {c["name"] for c in inspector.get_columns("opportunity")}
    if "study_start_date" not in cols:
        op.add_column("opportunity", sa.Column("study_start_date", sa.Date(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "opportunity" in set(inspector.get_table_names()):
        cols = {c["name"] for c in inspector.get_columns("opportunity")}
        if "study_start_date" in cols:
            op.drop_column("opportunity", "study_start_date")
