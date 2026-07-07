"""Add place_of_incoterms_before/after to opportunity (STP Incoterms place field).

Revision ID: 20260707_0078
Revises: 20260707_0077
Create Date: 2026-07-07
"""

from alembic import op
import sqlalchemy as sa

revision = "20260707_0078"
down_revision = "20260707_0077"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("opportunity") as batch_op:
        batch_op.add_column(sa.Column("place_of_incoterms_before", sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column("place_of_incoterms_after", sa.String(length=200), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("opportunity") as batch_op:
        batch_op.drop_column("place_of_incoterms_after")
        batch_op.drop_column("place_of_incoterms_before")
