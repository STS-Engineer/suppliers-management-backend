"""make opportunity_action_plan.opportunity_id nullable

Allows action plans (and quick actions) to exist without being attached to an
opportunity — a "general" action plan.

Revision ID: 20260724_0089
Revises: 20260723_0088
Create Date: 2026-07-24
"""
from alembic import op
import sqlalchemy as sa

revision = "20260724_0089"
down_revision = "20260723_0088"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "opportunity_action_plan",
        "opportunity_id",
        existing_type=sa.Integer(),
        nullable=True,
    )


def downgrade() -> None:
    # Only reversible if no standalone (NULL) plans exist.
    op.alter_column(
        "opportunity_action_plan",
        "opportunity_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
