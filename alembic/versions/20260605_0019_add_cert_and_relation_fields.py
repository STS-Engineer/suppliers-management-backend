"""Add certification standard fields, file upload, and relation annual spend.

Revision ID: 20260605_0019
Revises: 20260605_0018
Create Date: 2026-06-05

Changes:
- supplier_certification: add standard_type, file_name, file_url, file_size
- supplier_site_relation: add annual_spend_value, annual_spend_currency
- pld_scoring_rules: add new quality_certification scoring rows (IATF 16949:2016, ISO 9001 (cat BCD), Distributor)
"""

from alembic import op
import sqlalchemy as sa


revision = "20260605_0019"
down_revision = "20260605_0018"
branch_labels = None
depends_on = None

# New PLD scoring rows — certification_type is now the full cert name
NEW_QUALITY_CERT_RULES = [
    ("quality_certification", "IATF 16949:2016", 100),
    ("quality_certification", "ISO 9001 (cat BCD)", 100),
    ("quality_certification", "ISO 9001", 50),
    ("quality_certification", "Distributor", 0),
]


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # supplier_certification — new columns
    # -----------------------------------------------------------------------
    op.add_column(
        "supplier_certification",
        sa.Column("standard_type", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "supplier_certification",
        sa.Column("file_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "supplier_certification",
        sa.Column("file_url", sa.Text(), nullable=True),
    )
    op.add_column(
        "supplier_certification",
        sa.Column("file_size", sa.Numeric(18, 2), nullable=True),
    )

    # -----------------------------------------------------------------------
    # supplier_site_relation — new columns
    # -----------------------------------------------------------------------
    op.add_column(
        "supplier_site_relation",
        sa.Column("annual_spend_value", sa.Numeric(18, 2), nullable=True),
    )
    op.add_column(
        "supplier_site_relation",
        sa.Column("annual_spend_currency", sa.String(length=10), nullable=True),
    )

    # -----------------------------------------------------------------------
    # pld_scoring_rules — insert new quality_certification rows
    # -----------------------------------------------------------------------
    pld_scoring_rules = sa.table(
        "pld_scoring_rules",
        sa.column("criteria_type", sa.String),
        sa.column("score", sa.Numeric(10, 2)),
        sa.column("min_value", sa.String),
        sa.column("max_value", sa.String),
        sa.column("description", sa.Text),
        sa.column("is_active", sa.Boolean),
    )
    op.bulk_insert(
        pld_scoring_rules,
        [
            {
                "criteria_type": criteria_type,
                "score": score,
                "min_value": min_value,
                "max_value": min_value,
                "description": f"{criteria_type} score for {min_value}",
                "is_active": True,
            }
            for criteria_type, min_value, score in NEW_QUALITY_CERT_RULES
        ],
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM pld_scoring_rules WHERE criteria_type = 'quality_certification' "
        "AND min_value IN ('IATF 16949:2016', 'ISO 9001 (cat BCD)', 'ISO 9001', 'Distributor')"
    )

    op.drop_column("supplier_site_relation", "annual_spend_currency")
    op.drop_column("supplier_site_relation", "annual_spend_value")

    op.drop_column("supplier_certification", "file_size")
    op.drop_column("supplier_certification", "file_url")
    op.drop_column("supplier_certification", "file_name")
    op.drop_column("supplier_certification", "standard_type")
