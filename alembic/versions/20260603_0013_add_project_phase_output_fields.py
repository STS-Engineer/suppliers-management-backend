"""add phase output fields to project

Revision ID: 20260603_0013
Revises: 20260603_0012
Create Date: 2026-06-03
"""

from alembic import op
import sqlalchemy as sa

revision = "20260603_0013"
down_revision = "20260603_0012"
branch_labels = None
depends_on = None

NEW_COLS = [
    ("phase_output_notes",  sa.Text()),
    ("off_tool_date",       sa.Date()),
    ("committee_review_date", sa.Date()),
    ("committee_members",   sa.Text()),
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "project" not in set(inspector.get_table_names()):
        return
    existing = {c["name"] for c in inspector.get_columns("project")}
    for name, col_type in NEW_COLS:
        if name not in existing:
            op.add_column("project", sa.Column(name, col_type, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "project" not in set(inspector.get_table_names()):
        return
    existing = {c["name"] for c in inspector.get_columns("project")}
    for name, _ in reversed(NEW_COLS):
        if name in existing:
            op.drop_column("project", name)
