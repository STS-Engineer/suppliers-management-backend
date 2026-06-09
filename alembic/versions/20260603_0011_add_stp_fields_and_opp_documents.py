"""add STP fields, proposed supplier, and opportunity_document table

Revision ID: 20260603_0011
Revises: 20260602_0010
Create Date: 2026-06-03
"""

from alembic import op
import sqlalchemy as sa

revision = "20260603_0011"
down_revision = "20260602_0010"
branch_labels = None
depends_on = None

STP_COLUMNS = [
    ("scope_in",              sa.Text()),
    ("scope_out",             sa.Text()),
    ("customers",             sa.String(500)),
    ("annual_quantity_n1",    sa.Integer()),
    ("annual_quantity_n2",    sa.Integer()),
    ("annual_quantity_n3",    sa.Integer()),
    ("annual_quantity_n4",    sa.Integer()),
    ("proposed_supplier_name", sa.String(500)),
    ("proposed_supplier_id",  sa.Integer()),
    ("current_price",         sa.Numeric(18, 6)),
    ("proposed_price",        sa.Numeric(18, 6)),
    ("tooling_cost",          sa.Numeric(18, 2)),
    ("travel_cost",           sa.Numeric(18, 2)),
    ("qualification_cost",    sa.Numeric(18, 2)),
    ("total_investment",      sa.Numeric(18, 2)),
    ("roi_percent",           sa.Numeric(10, 2)),
    ("cash_inventory_gap",    sa.Numeric(18, 2)),
    ("cash_ap_gap",           sa.Numeric(18, 2)),
    ("phase1_weeks",          sa.Integer()),
    ("phase2_weeks",          sa.Integer()),
    ("phase3_weeks",          sa.Integer()),
    ("phase4_weeks",          sa.Integer()),
    ("reason_productivity",   sa.Boolean(), "false"),
    ("reason_quality",        sa.Boolean(), "false"),
    ("reason_capacity",       sa.Boolean(), "false"),
    ("reason_other",          sa.String(500)),
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    # ── STP fields on opportunity ─────────────────────────────────────
    if "opportunity" in existing_tables:
        existing_cols = {c["name"] for c in inspector.get_columns("opportunity")}
        for col_def in STP_COLUMNS:
            col_name = col_def[0]
            col_type = col_def[1]
            server_default = col_def[2] if len(col_def) > 2 else None
            if col_name not in existing_cols:
                kwargs = {"nullable": True}
                if server_default is not None:
                    kwargs["server_default"] = sa.text(server_default)
                    kwargs["nullable"] = False
                op.add_column("opportunity", sa.Column(col_name, col_type, **kwargs))

        # FK for proposed_supplier_id (add only if supplier_unit exists)
        if "proposed_supplier_id" not in existing_cols and "supplier_unit" in existing_tables:
            try:
                op.create_foreign_key(
                    "fk_opportunity_proposed_supplier_id",
                    "opportunity", "supplier_unit",
                    ["proposed_supplier_id"], ["id_supplier_unit"],
                    ondelete="SET NULL",
                )
            except Exception:
                pass

    # ── opportunity_document table ────────────────────────────────────
    if "opportunity_document" not in existing_tables:
        op.create_table(
            "opportunity_document",
            sa.Column("doc_id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("opportunity_id", sa.Integer(), nullable=False),
            sa.Column("phase_label", sa.String(50), nullable=True),
            sa.Column("file_name", sa.String(500), nullable=True),
            sa.Column("original_file_name", sa.String(500), nullable=True),
            sa.Column("file_url", sa.String(2000), nullable=True),
            sa.Column("mime_type", sa.String(200), nullable=True),
            sa.Column("file_size", sa.Integer(), nullable=True),
            sa.Column("uploaded_by", sa.String(200), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("current_timestamp"), nullable=True),
            sa.ForeignKeyConstraint(
                ["opportunity_id"], ["opportunity.opportunity_id"], ondelete="CASCADE"
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "opportunity_document" in existing_tables:
        op.drop_table("opportunity_document")

    if "opportunity" in existing_tables:
        existing_cols = {c["name"] for c in inspector.get_columns("opportunity")}
        for col_def in reversed(STP_COLUMNS):
            col_name = col_def[0]
            if col_name in existing_cols:
                op.drop_column("opportunity", col_name)
