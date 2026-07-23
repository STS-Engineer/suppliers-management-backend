"""add project-manager notification tracking to gate_approval_request

Records, per gate, that the designated Project Manager was notified once the
whole panel approved (Go): who was notified, when, and whether the email was
sent or failed. Lets the UI confirm the PM received the handover at each phase.

Revision ID: 20260723_0088
Revises: 20260722_0087
Create Date: 2026-07-23 12:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260723_0088"
down_revision = "20260722_0087"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("gate_approval_request")}
    if "pm_notified_email" not in columns:
        op.add_column(
            "gate_approval_request",
            sa.Column("pm_notified_email", sa.String(length=255), nullable=True),
        )
    if "pm_notified_at" not in columns:
        op.add_column(
            "gate_approval_request",
            sa.Column("pm_notified_at", sa.DateTime(), nullable=True),
        )
    if "pm_notification_status" not in columns:
        op.add_column(
            "gate_approval_request",
            sa.Column("pm_notification_status", sa.String(length=20), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("gate_approval_request")}
    for col in ("pm_notification_status", "pm_notified_at", "pm_notified_email"):
        if col in columns:
            op.drop_column("gate_approval_request", col)
