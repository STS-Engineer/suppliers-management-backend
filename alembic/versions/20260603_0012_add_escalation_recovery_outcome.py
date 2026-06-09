"""add escalation, recovery fields to financial_line and monthly_outcome to monthly_financial

Revision ID: 20260603_0012
Revises: 20260603_0011
Create Date: 2026-06-03
"""

from alembic import op
import sqlalchemy as sa

revision = "20260603_0012"
down_revision = "20260603_0011"
branch_labels = None
depends_on = None

FINANCIAL_LINE_COLS = [
    ("is_escalated",       sa.Boolean(),    "false"),
    ("escalated_at",       sa.DateTime(),   None),
    ("escalated_by",       sa.String(200),  None),
    ("escalation_reason",  sa.Text(),       None),
    ("recovery_status",    sa.String(50),   None),
    ("recovery_note",      sa.Text(),       None),
    ("recovery_updated_at", sa.DateTime(),  None),
    ("recovery_updated_by", sa.String(200), None),
]

MONTHLY_COLS = [
    ("monthly_outcome", sa.String(20), None),
]


def _add_columns(table: str, col_defs: list, existing_cols: set) -> None:
    for col_def in col_defs:
        name, col_type = col_def[0], col_def[1]
        server_default = col_def[2] if len(col_def) > 2 else None
        if name not in existing_cols:
            kwargs = {"nullable": True}
            if server_default is not None:
                kwargs["server_default"] = sa.text(server_default)
                kwargs["nullable"] = False
            op.add_column(table, sa.Column(name, col_type, **kwargs))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    if "financial_line" in existing:
        cols = {c["name"] for c in inspector.get_columns("financial_line")}
        _add_columns("financial_line", FINANCIAL_LINE_COLS, cols)

    if "monthly_financial" in existing:
        cols = {c["name"] for c in inspector.get_columns("monthly_financial")}
        _add_columns("monthly_financial", MONTHLY_COLS, cols)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    if "monthly_financial" in existing:
        cols = {c["name"] for c in inspector.get_columns("monthly_financial")}
        for name, *_ in MONTHLY_COLS:
            if name in cols:
                op.drop_column("monthly_financial", name)

    if "financial_line" in existing:
        cols = {c["name"] for c in inspector.get_columns("financial_line")}
        for name, *_ in reversed(FINANCIAL_LINE_COLS):
            if name in cols:
                op.drop_column("financial_line", name)
