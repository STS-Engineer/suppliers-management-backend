"""Fresh supplier lifecycle baseline.

Revision ID: 20260520_0001
Revises:
Create Date: 2026-05-20
"""

from alembic import op
import sqlalchemy as sa


revision = "20260520_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "avocarbon_site",
        sa.Column("id_site", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("site_name", sa.String(length=200), nullable=True),
        sa.Column("address_line", sa.String(length=255), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("country", sa.String(length=100), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by", sa.String(length=200), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_by", sa.String(length=200), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )

    op.create_table(
        "supplier_group",
        sa.Column("id_group", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("nom", sa.String(length=200), nullable=True),
        sa.Column("supplier_scope", sa.String(length=20), nullable=True),
        sa.Column("group_supplier_owner_email", sa.String(length=200), nullable=True),
        sa.Column("multi_site", sa.Boolean(), nullable=True),
        sa.Column("exit_supplier", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("strategic_reason", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by", sa.String(length=200), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_by", sa.String(length=200), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )

    op.create_table(
        "supplier_category",
        sa.Column("id_category", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("category_key", sa.String(length=100), nullable=False),
        sa.Column("category_label", sa.String(length=100), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by", sa.String(length=200), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_by", sa.String(length=200), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.UniqueConstraint("category_key", name="uq_supplier_category_category_key"),
    )

    op.create_table(
        "supplier_group_category",
        sa.Column("id_group_category", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("id_group", sa.Integer(), nullable=False),
        sa.Column("id_category", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by", sa.String(length=200), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_by", sa.String(length=200), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.ForeignKeyConstraint(["id_group"], ["supplier_group.id_group"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["id_category"], ["supplier_category.id_category"], ondelete="CASCADE"),
        sa.UniqueConstraint("id_group", "id_category", name="uq_supplier_group_category"),
    )

    op.create_table(
        "supplier_unit",
        sa.Column("id_supplier_unit", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("id_group", sa.Integer(), nullable=True),
        sa.Column("supplier_code", sa.String(length=50), nullable=True),
        sa.Column("address_line", sa.String(length=255), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("country", sa.String(length=100), nullable=True),
        sa.Column("product_type", sa.String(length=255), nullable=True),
        sa.Column("product_category", sa.String(length=255), nullable=True),
        sa.Column("amount_value", sa.Numeric(18, 2), nullable=True),
        sa.Column("amount_currency", sa.String(length=10), nullable=True),
        sa.Column("strategique", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("monopolistique", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("directed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by", sa.String(length=200), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_by", sa.String(length=200), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.ForeignKeyConstraint(["id_group"], ["supplier_group.id_group"], ondelete="CASCADE"),
        sa.UniqueConstraint("supplier_code", name="uq_supplier_unit_supplier_code"),
    )

    op.create_table(
        "contact",
        sa.Column("id_contact", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("role_label", sa.String(length=100), nullable=True),
        sa.Column("role_name", sa.String(length=150), nullable=True),
        sa.Column("full_name", sa.String(length=200), nullable=True),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("email", sa.String(length=200), nullable=True),
        sa.Column("is_primary_contact", sa.Boolean(), nullable=True),
        sa.Column("id_supplier_group", sa.Integer(), nullable=True),
        sa.Column("id_supplier_unit", sa.Integer(), nullable=True),
        sa.Column("id_site", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by", sa.String(length=200), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_by", sa.String(length=200), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.ForeignKeyConstraint(["id_supplier_group"], ["supplier_group.id_group"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["id_supplier_unit"], ["supplier_unit.id_supplier_unit"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["id_site"], ["avocarbon_site.id_site"], ondelete="CASCADE"),
    )

    op.create_table(
        "supplier_certification",
        sa.Column("id_certification", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("id_supplier_unit", sa.Integer(), nullable=True),
        sa.Column("certification_type", sa.String(length=100), nullable=True),
        sa.Column("certificate_name", sa.String(length=150), nullable=True),
        sa.Column("amount_value", sa.Numeric(18, 2), nullable=True),
        sa.Column("amount_currency", sa.String(length=10), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("expiry_mode", sa.String(length=30), nullable=True),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by", sa.String(length=200), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_by", sa.String(length=200), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.CheckConstraint("(start_date IS NULL OR end_date IS NULL OR end_date >= start_date)", name="supplier_certification_ck_certification_dates"),
        sa.ForeignKeyConstraint(["id_supplier_unit"], ["supplier_unit.id_supplier_unit"], ondelete="CASCADE"),
    )

    op.create_table(
        "supplier_site_relation",
        sa.Column("id_relation", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("id_site", sa.Integer(), nullable=False),
        sa.Column("id_supplier_unit", sa.Integer(), nullable=False),
        sa.Column("alias_1", sa.String(length=200), nullable=True),
        sa.Column("buyer_owner", sa.String(length=200), nullable=True),
        sa.Column("supplier_status", sa.String(length=50), nullable=True),
        sa.Column("operational_grade", sa.CHAR(length=1), nullable=True),
        sa.Column("class_value", sa.Integer(), nullable=True),
        sa.Column("global_status", sa.String(length=50), nullable=True),
        sa.Column("evaluation_frequency", sa.String(length=50), nullable=True),
        sa.Column("final_grade", sa.String(length=10), nullable=True),
        sa.Column("strategic_mention", sa.String(length=50), nullable=True),
        sa.Column("panel_decision", sa.String(length=100), nullable=True),
        sa.Column("last_evaluation_date", sa.Date(), nullable=True),
        sa.Column("next_evaluation_date", sa.Date(), nullable=True),
        sa.Column("evaluation_comments", sa.Text(), nullable=True),
        sa.Column("evaluation_suggestion", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("inactivated_at", sa.DateTime(), nullable=True),
        sa.Column("last_status_change", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by", sa.String(length=200), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_by", sa.String(length=200), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.ForeignKeyConstraint(["id_site"], ["avocarbon_site.id_site"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["id_supplier_unit"], ["supplier_unit.id_supplier_unit"], ondelete="CASCADE"),
        sa.UniqueConstraint("id_site", "id_supplier_unit", name="uq_relation_site_supplier"),
    )

    op.create_table(
        "supplier_status_history",
        sa.Column("id_history", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("id_relation", sa.Integer(), nullable=False),
        sa.Column("old_status", sa.String(length=50), nullable=True),
        sa.Column("new_status", sa.String(length=50), nullable=True),
        sa.Column("old_class", sa.Integer(), nullable=True),
        sa.Column("new_class", sa.Integer(), nullable=True),
        sa.Column("old_grade", sa.CHAR(length=1), nullable=True),
        sa.Column("new_grade", sa.CHAR(length=1), nullable=True),
        sa.Column("old_final_grade", sa.String(length=10), nullable=True),
        sa.Column("new_final_grade", sa.String(length=10), nullable=True),
        sa.Column("old_strategic_mention", sa.String(length=50), nullable=True),
        sa.Column("new_strategic_mention", sa.String(length=50), nullable=True),
        sa.Column("old_panel_decision", sa.String(length=100), nullable=True),
        sa.Column("new_panel_decision", sa.String(length=100), nullable=True),
        sa.Column("change_reason", sa.Text(), nullable=True),
        sa.Column("changed_by", sa.String(length=200), nullable=True),
        sa.Column("changed_at", sa.DateTime(), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["id_relation"], ["supplier_site_relation.id_relation"], ondelete="CASCADE"),
    )

    op.create_table(
        "evaluation_cycle",
        sa.Column("id_cycle", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("id_relation", sa.Integer(), nullable=False),
        sa.Column("cycle_type", sa.String(length=100), nullable=False),
        sa.Column("supplier_type", sa.String(length=50), nullable=True),
        sa.Column("frequency", sa.String(length=50), nullable=True),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("cycle_status", sa.String(length=50), nullable=False, server_default=sa.text("'Draft'")),
        sa.Column("launched_by", sa.String(length=200), nullable=True),
        sa.Column("launched_at", sa.DateTime(), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by", sa.String(length=200), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_by", sa.String(length=200), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.ForeignKeyConstraint(["id_relation"], ["supplier_site_relation.id_relation"], ondelete="CASCADE"),
    )
    op.create_index("idx_evaluation_cycle_relation", "evaluation_cycle", ["id_relation"])

    op.create_table(
        "score_card",
        sa.Column("id_score_card", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("id_relation", sa.Integer(), nullable=True),
        sa.Column("scorecard_date", sa.Date(), nullable=True),
        sa.Column("score", sa.Numeric(5, 2), nullable=True),
        sa.Column("grade", sa.CHAR(length=1), nullable=True),
        sa.Column("id_cycle", sa.Integer(), nullable=True),
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

    op.create_table(
        "classification",
        sa.Column("id_classification", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("id_relation", sa.Integer(), nullable=True),
        sa.Column("classification_date", sa.Date(), nullable=True),
        sa.Column("classification_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("class_value", sa.Integer(), nullable=True),
        sa.Column("operational_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("operational_grade", sa.CHAR(length=1), nullable=True),
        sa.Column("final_grade", sa.String(length=10), nullable=True),
        sa.Column("impact_score", sa.Integer(), nullable=True),
        sa.Column("strategic_mention", sa.String(length=50), nullable=True),
        sa.Column("panel_decision", sa.String(length=100), nullable=True),
        sa.Column("id_cycle", sa.Integer(), nullable=True),
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

    op.create_table(
        "pld_class_evaluation_input",
        sa.Column("id_pld_input", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("id_relation", sa.Integer(), nullable=False),
        sa.Column("id_cycle", sa.Integer(), nullable=True),
        sa.Column("top", sa.String(length=255), nullable=True),
        sa.Column("lta", sa.String(length=255), nullable=True),
        sa.Column("productivity", sa.String(length=255), nullable=True),
        sa.Column("quality_certification", sa.String(length=255), nullable=True),
        sa.Column("prod_lia_ins", sa.String(length=255), nullable=True),
        sa.Column("competitiveness", sa.String(length=255), nullable=True),
        sa.Column("sqma", sa.String(length=255), nullable=True),
        sa.Column("family_coverage", sa.String(length=255), nullable=True),
        sa.Column("geo_coverage", sa.String(length=255), nullable=True),
        sa.Column("cons_or_wd", sa.String(length=255), nullable=True),
        sa.Column("financial_health", sa.String(length=255), nullable=True),
        sa.Column("class_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("class_value", sa.Integer(), nullable=True),
        sa.Column("impact_score", sa.Integer(), nullable=True),
        sa.Column("strategic_mention", sa.String(length=50), nullable=True),
        sa.Column("panel_decision", sa.String(length=100), nullable=True),
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
    op.create_index("idx_pld_class_input_relation_cycle", "pld_class_evaluation_input", ["id_relation", "id_cycle"])

    op.create_table(
        "operational_evaluation_input",
        sa.Column("id_operational_input", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("id_relation", sa.Integer(), nullable=False),
        sa.Column("id_cycle", sa.Integer(), nullable=True),
        sa.Column("source_type", sa.String(length=50), nullable=True),
        sa.Column("management_system", sa.Numeric(5, 2), nullable=True),
        sa.Column("customer_communication", sa.Numeric(5, 2), nullable=True),
        sa.Column("development_design", sa.Numeric(5, 2), nullable=True),
        sa.Column("production_manufacturing", sa.Numeric(5, 2), nullable=True),
        sa.Column("quality_audits", sa.Numeric(5, 2), nullable=True),
        sa.Column("suppliers_subcontractors", sa.Numeric(5, 2), nullable=True),
        sa.Column("deliveries", sa.Numeric(5, 2), nullable=True),
        sa.Column("environment_ethic_rules", sa.Numeric(5, 2), nullable=True),
        sa.Column("average_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("operational_grade", sa.CHAR(length=1), nullable=True),
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
    op.create_index("idx_operational_input_relation_cycle", "operational_evaluation_input", ["id_relation", "id_cycle"])

    op.create_table(
        "impact_evaluation_input",
        sa.Column("id_impact_input", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("id_relation", sa.Integer(), nullable=False),
        sa.Column("id_cycle", sa.Integer(), nullable=True),
        sa.Column("question_1", sa.String(length=50), nullable=True),
        sa.Column("question_2", sa.String(length=50), nullable=True),
        sa.Column("question_3", sa.String(length=50), nullable=True),
        sa.Column("question_4", sa.String(length=50), nullable=True),
        sa.Column("question_5", sa.String(length=50), nullable=True),
        sa.Column("question_6", sa.String(length=50), nullable=True),
        sa.Column("impact_score", sa.Integer(), nullable=True),
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
    op.create_index("idx_impact_input_relation_cycle", "impact_evaluation_input", ["id_relation", "id_cycle"])

    op.create_table(
        "assessment_template",
        sa.Column("id_template", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("template_name", sa.String(length=255), nullable=False),
        sa.Column("template_type", sa.String(length=100), nullable=False, server_default=sa.text("'SELF_ASSESSMENT'")),
        sa.Column("version", sa.String(length=50), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default=sa.text("'Active'")),
        sa.Column("created_by", sa.String(length=200), nullable=True),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by", sa.String(length=200), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_by", sa.String(length=200), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )

    op.create_table(
        "supplier_assessment",
        sa.Column("id_assessment", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("id_relation", sa.Integer(), nullable=True),
        sa.Column("id_template", sa.Integer(), nullable=True),
        sa.Column("id_cycle", sa.Integer(), nullable=True),
        sa.Column("assessment_date", sa.Date(), nullable=True),
        sa.Column("submitted_by", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default=sa.text("'Received'")),
        sa.Column("final_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("final_grade", sa.String(length=10), nullable=True),
        sa.Column("final_class", sa.Integer(), nullable=True),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by", sa.String(length=200), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_by", sa.String(length=200), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.ForeignKeyConstraint(["id_relation"], ["supplier_site_relation.id_relation"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["id_template"], ["assessment_template.id_template"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["id_cycle"], ["evaluation_cycle.id_cycle"], ondelete="SET NULL"),
    )


def downgrade() -> None:
    op.drop_table("supplier_assessment")
    op.drop_table("assessment_template")
    op.drop_index("idx_impact_input_relation_cycle", table_name="impact_evaluation_input")
    op.drop_table("impact_evaluation_input")
    op.drop_index("idx_operational_input_relation_cycle", table_name="operational_evaluation_input")
    op.drop_table("operational_evaluation_input")
    op.drop_index("idx_pld_class_input_relation_cycle", table_name="pld_class_evaluation_input")
    op.drop_table("pld_class_evaluation_input")
    op.drop_table("classification")
    op.drop_table("score_card")
    op.drop_index("idx_evaluation_cycle_relation", table_name="evaluation_cycle")
    op.drop_table("evaluation_cycle")
    op.drop_table("supplier_status_history")
    op.drop_table("supplier_site_relation")
    op.drop_table("supplier_certification")
    op.drop_table("contact")
    op.drop_table("supplier_unit")
    op.drop_table("supplier_group_category")
    op.drop_table("supplier_category")
    op.drop_table("supplier_group")
    op.drop_table("avocarbon_site")
