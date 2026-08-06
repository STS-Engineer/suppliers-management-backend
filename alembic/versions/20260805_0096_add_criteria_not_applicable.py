"""Add not_applicable flag to pld_class_criteria_detail

Allows any of the 11 Class Evaluation criteria to be marked "Not
Applicable" so it is excluded entirely (numerator AND denominator) from
the relation's class score calculation, distinct from being left blank
or invalid (which still scores 0 and stays in the denominator).

Revision ID: 20260805_0096
Revises: 20260804_0095
Create Date: 2026-08-05
"""

import sqlalchemy as sa
from alembic import op

revision = "20260805_0096"
down_revision = "20260804_0095"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pld_class_criteria_detail",
        sa.Column("not_applicable", sa.Boolean, nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("pld_class_criteria_detail", "not_applicable")
