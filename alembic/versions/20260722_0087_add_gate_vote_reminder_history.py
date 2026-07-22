"""add reminder history (count + last_reminded_at) to gate_approval_vote

Records the reminder action as history: each time a pending approver is nudged
(GateApprovalService.send_reminders), reminder_count is incremented and
last_reminded_at is stamped. Lets the UI show "reminded N times, last on …"
and gives an audit trail of who was chased and when.

Revision ID: 20260722_0087
Revises: 20260713_0086
Create Date: 2026-07-22 12:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260722_0087"
down_revision = "20260713_0086"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("gate_approval_vote")}
    if "reminder_count" not in columns:
        op.add_column(
            "gate_approval_vote",
            sa.Column(
                "reminder_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )
    if "last_reminded_at" not in columns:
        op.add_column(
            "gate_approval_vote",
            sa.Column("last_reminded_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("gate_approval_vote")}
    if "last_reminded_at" in columns:
        op.drop_column("gate_approval_vote", "last_reminded_at")
    if "reminder_count" in columns:
        op.drop_column("gate_approval_vote", "reminder_count")
