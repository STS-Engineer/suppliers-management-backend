"""add entry_mode (Standard/Bonus/Rework) to opportunity

Sub-option within opportunity_type (Olivier, call 2026-07-10):
  Bonus  (Negotiation) — single lump gain entered directly, one-time, no cash.
  Rework (Technical Productivity) — single lump gain, one-time, no incoterms/cash.
NULL = Standard (normal STP price×quantity computation).

Revision ID: 20260713_0085
Revises: 20260713_0084
Create Date: 2026-07-13 12:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260713_0085"
down_revision = "20260713_0084"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("opportunity")}
    if "entry_mode" not in columns:
        op.add_column(
            "opportunity",
            sa.Column("entry_mode", sa.String(length=20), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("opportunity")}
    if "entry_mode" in columns:
        op.drop_column("opportunity", "entry_mode")
