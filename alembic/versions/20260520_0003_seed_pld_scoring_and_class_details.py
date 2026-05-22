"""Create PLD scoring rules and class criteria detail tables.

Revision ID: 20260520_0003
Revises: 20260520_0002
Create Date: 2026-05-20
"""

from alembic import op
import sqlalchemy as sa


revision = "20260520_0003"
down_revision = "20260520_0002"
branch_labels = None
depends_on = None


PLD_RULES = [
    ("top", "60 days end of month or +", 100),
    ("top", "60 days net", 80),
    ("top", "30 days end of month or +", 50),
    ("top", "30 days net", 30),
    ("top", "Cash in Advance", 0),
    ("lta", "3 years/+", 100),
    ("lta", "2 years", 80),
    ("lta", "1 year", 50),
    ("lta", "None/Invalid", 0),
    ("productivity", "3% or +", 100),
    ("productivity", "2% or +", 80),
    ("productivity", "1% or +", 50),
    ("productivity", "less than 1%", 30),
    ("productivity", "Neg", 0),
    ("quality_certification", "IATF / ISO9001 (cat BCD)", 100),
    ("quality_certification", "ISO9001", 50),
    ("quality_certification", "None", 0),
    ("prod_lia_ins", "2M$ or +", 100),
    ("prod_lia_ins", "1M$ or +", 50),
    ("prod_lia_ins", "None", 0),
    ("competitiveness", "Almost Best in Fam.", 80),
    ("competitiveness", "Best in Fam.", 100),
    ("competitiveness", "Ave. in Fam.", 50),
    ("competitiveness", "Less Avg", 30),
    ("competitiveness", "Not Comp.", 0),
    ("sqma", "Rejected", 0),
    ("sqma", "Signed", 100),
    ("sqma", "Signed m.res.", 80),
    ("sqma", "Signed M/Res/not sent", 30),
    ("family_coverage", "Supplier can make 1 family requirements", 0),
    ("family_coverage", "Supplier can make all the family requirements", 100),
    ("family_coverage", "Supplier can make only of few family requirements", 50),
    ("family_coverage", "Supplier can make the main family requirements", 80),
    ("geo_coverage", "1 plant is covered", 30),
    ("geo_coverage", "Main plants covered", 100),
    ("geo_coverage", "More than 50% plants are covered", 50),
    ("geo_coverage", "None", 0),
    ("cons_or_wd", "Biweekly Del.", 30),
    ("cons_or_wd", "Cons. Or Daily Deliveries", 100),
    ("cons_or_wd", "DDP or Weekly Del.", 50),
    ("cons_or_wd", "Other", 0),
    ("financial_health", "Good", 100),
    ("financial_health", "To Monitor", 50),
    ("financial_health", "At Risk", 0),
]


def upgrade() -> None:
    op.create_table(
        "pld_scoring_rules",
        sa.Column("rule_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("criteria_type", sa.String(length=255), nullable=True),
        sa.Column("score", sa.Numeric(10, 2), nullable=True),
        sa.Column("min_value", sa.String(length=255), nullable=True),
        sa.Column("max_value", sa.String(length=255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True, server_default=sa.text("true")),
    )

    op.create_table(
        "pld_class_criteria_detail",
        sa.Column("id_detail", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("id_relation", sa.Integer(), nullable=False),
        sa.Column("id_cycle", sa.Integer(), nullable=True),
        sa.Column("criteria_type", sa.String(length=100), nullable=False),
        sa.Column("selected_value", sa.String(length=255), nullable=True),
        sa.Column("score", sa.Numeric(5, 2), nullable=True),
        sa.Column("evidence_file_name", sa.String(length=255), nullable=True),
        sa.Column("validity_start_date", sa.Date(), nullable=True),
        sa.Column("validity_end_date", sa.Date(), nullable=True),
        sa.Column("signature_date", sa.Date(), nullable=True),
        sa.Column("last_update_date", sa.Date(), nullable=True),
        sa.Column("amount_value", sa.Numeric(18, 2), nullable=True),
        sa.Column("amount_currency", sa.String(length=10), nullable=True),
        sa.Column("auto_validity_end_date", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("entered_by", sa.String(length=200), nullable=True),
        sa.Column("entered_at", sa.DateTime(), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by", sa.String(length=200), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_by", sa.String(length=200), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.ForeignKeyConstraint(["id_relation"], ["supplier_site_relation.id_relation"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["id_cycle"], ["evaluation_cycle.id_cycle"], ondelete="SET NULL"),
    )
    op.create_index(
        "idx_pld_class_criteria_relation_cycle",
        "pld_class_criteria_detail",
        ["id_relation", "id_cycle"],
    )

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
                "description": f"{criteria_type} exact match score for {min_value}",
                "is_active": True,
            }
            for criteria_type, min_value, score in PLD_RULES
        ],
    )


def downgrade() -> None:
    op.drop_index("idx_pld_class_criteria_relation_cycle", table_name="pld_class_criteria_detail")
    op.drop_table("pld_class_criteria_detail")
    op.drop_table("pld_scoring_rules")
