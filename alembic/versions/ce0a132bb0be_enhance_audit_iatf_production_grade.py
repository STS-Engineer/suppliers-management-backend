"""enhance_audit_iatf_production_grade

Revision ID: ce0a132bb0be
Revises: ade5d80f331c
Create Date: 2026-05-14 09:36:35.474109
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'ce0a132bb0be'
down_revision = 'ade5d80f331c'
branch_labels = None
depends_on = None

CORE_TABLES = [
    "avocarbon_site",
    "supplier_group",
    "supplier_unit",
    "supplier_site_relation",
    "supplier_certification",
    "supplier_agreement",
    "contact",
    "document",
    "evaluation_cycle",
    "approval_workflow",
    "escalation",
    "score_card",
    "classification",
    "scorecard_kpi_detail",
    "scorecard_upload_register",
    "scorecard_data_quality_checks",
    "input_otd_monthly",
    "input_quality_claims",
    "input_delivery_spend",
    "assessment_template",
    "assessment_template_field_mapping",
    "supplier_assessment",
    "supplier_assessment_answer",
    "opportunity",
    "project",
    "financial_line",
    "monthly_financial",
]


def upgrade():
    # ---------------------------------------------------------------------
    # 1) Add production governance columns to existing tables
    # ---------------------------------------------------------------------
    for table in CORE_TABLES:
        op.add_column(table, sa.Column("updated_at", sa.DateTime(), nullable=True))
        op.add_column(table, sa.Column("updated_by", sa.String(200), nullable=True))
        op.add_column(table, sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")))
        op.add_column(table, sa.Column("deleted_at", sa.DateTime(), nullable=True))
        op.add_column(table, sa.Column("deleted_by", sa.String(200), nullable=True))
        op.add_column(table, sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"))

    # ---------------------------------------------------------------------
    # 2) Immutable audit trail
    # ---------------------------------------------------------------------
    op.create_table(
        "audit_event",
        sa.Column("id_audit_event", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_uuid", postgresql.UUID(as_uuid=False), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("table_name", sa.String(150), nullable=False),
        sa.Column("record_pk", sa.String(255), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),  # INSERT / UPDATE / DELETE / APPROVE / REJECT / IMPORT
        sa.Column("changed_by", sa.String(200), nullable=True),
        sa.Column("changed_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("old_values", postgresql.JSONB(), nullable=True),
        sa.Column("new_values", postgresql.JSONB(), nullable=True),
        sa.Column("reason_code", sa.String(150), nullable=True),
        sa.Column("reason_comment", sa.Text(), nullable=True),
        sa.Column("source_system", sa.String(150), nullable=True),
        sa.Column("source_ip", sa.String(80), nullable=True),
        sa.Column("correlation_id", sa.String(150), nullable=True),
        sa.Column("batch_id", sa.String(150), nullable=True),
        sa.Column("is_system_event", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )

    op.create_index("idx_audit_event_table_record", "audit_event", ["table_name", "record_pk"])
    op.create_index("idx_audit_event_changed_at", "audit_event", ["changed_at"])
    op.create_index("idx_audit_event_correlation", "audit_event", ["correlation_id"])

    # ---------------------------------------------------------------------
    # 3) Import batch traceability for uploaded scorecard/source data
    # ---------------------------------------------------------------------
    op.create_table(
        "import_batch",
        sa.Column("id_import_batch", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("batch_uuid", postgresql.UUID(as_uuid=False), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("source_name", sa.String(200), nullable=False),
        sa.Column("source_type", sa.String(100), nullable=True),
        sa.Column("id_document", sa.Integer(), sa.ForeignKey("document.id_document", ondelete="SET NULL"), nullable=True),
        sa.Column("uploaded_by", sa.String(200), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="'Pending'"),
        sa.Column("records_total", sa.Integer(), nullable=True),
        sa.Column("records_inserted", sa.Integer(), nullable=True),
        sa.Column("records_rejected", sa.Integer(), nullable=True),
        sa.Column("file_hash_sha256", sa.String(64), nullable=True),
        sa.Column("validation_summary", postgresql.JSONB(), nullable=True),
        sa.Column("error_details", postgresql.JSONB(), nullable=True),
        sa.Column("comments", sa.Text(), nullable=True),
    )

    op.create_index("idx_import_batch_document", "import_batch", ["id_document"])
    op.create_index("idx_import_batch_uuid", "import_batch", ["batch_uuid"], unique=True)

    # Link raw input rows to import batch
    for table in ["input_otd_monthly", "input_quality_claims", "input_delivery_spend"]:
        op.add_column(table, sa.Column("id_import_batch", sa.BigInteger(), sa.ForeignKey("import_batch.id_import_batch", ondelete="SET NULL"), nullable=True))
        op.add_column(table, sa.Column("source_row_number", sa.Integer(), nullable=True))
        op.add_column(table, sa.Column("source_row_hash", sa.String(64), nullable=True))
        op.create_index(f"idx_{table}_import_batch", table, ["id_import_batch"])

    # ---------------------------------------------------------------------
    # 4) Document control / revision control
    # ---------------------------------------------------------------------
    op.add_column("document", sa.Column("document_owner", sa.String(200), nullable=True))
    op.add_column("document", sa.Column("controlled_document", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("document", sa.Column("retention_code", sa.String(100), nullable=True))
    op.add_column("document", sa.Column("review_due_date", sa.Date(), nullable=True))
    op.add_column("document", sa.Column("expiry_date", sa.Date(), nullable=True))
    op.add_column("document", sa.Column("file_hash_sha256", sa.String(64), nullable=True))
    op.add_column("document", sa.Column("storage_provider", sa.String(100), nullable=True))
    op.add_column("document", sa.Column("storage_object_key", sa.Text(), nullable=True))
    op.add_column("document", sa.Column("superseded_by_document_id", sa.Integer(), sa.ForeignKey("document.id_document", ondelete="SET NULL"), nullable=True))

    op.create_table(
        "document_revision",
        sa.Column("id_document_revision", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("id_document", sa.Integer(), sa.ForeignKey("document.id_document", ondelete="CASCADE"), nullable=False),
        sa.Column("revision_code", sa.String(50), nullable=False),
        sa.Column("revision_date", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("changed_by", sa.String(200), nullable=True),
        sa.Column("change_reason", sa.Text(), nullable=True),
        sa.Column("file_hash_sha256", sa.String(64), nullable=True),
        sa.Column("file_url", sa.Text(), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
    )

    op.create_table(
        "document_approval",
        sa.Column("id_document_approval", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("id_document", sa.Integer(), sa.ForeignKey("document.id_document", ondelete="CASCADE"), nullable=False),
        sa.Column("approval_step", sa.Integer(), nullable=False),
        sa.Column("approver_role", sa.String(150), nullable=True),
        sa.Column("approver_email", sa.String(200), nullable=True),
        sa.Column("decision", sa.String(50), nullable=False, server_default="'Pending'"),
        sa.Column("decision_at", sa.DateTime(), nullable=True),
        sa.Column("decision_comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
    )

    op.create_index("idx_document_revision_document", "document_revision", ["id_document"])
    op.create_index("idx_document_approval_document", "document_approval", ["id_document"])

    # ---------------------------------------------------------------------
    # 5) Retention policy
    # ---------------------------------------------------------------------
    op.create_table(
        "record_retention_policy",
        sa.Column("id_retention_policy", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("retention_code", sa.String(100), nullable=False),
        sa.Column("record_category", sa.String(150), nullable=False),
        sa.Column("retention_years", sa.Integer(), nullable=False),
        sa.Column("legal_hold_required", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.UniqueConstraint("retention_code", name="uq_retention_policy_code"),
    )

    # ---------------------------------------------------------------------
    # 6) Electronic signatures / approval proof
    # ---------------------------------------------------------------------
    op.create_table(
        "electronic_signature",
        sa.Column("id_signature", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("signed_object_type", sa.String(100), nullable=False),
        sa.Column("signed_object_id", sa.String(255), nullable=False),
        sa.Column("signature_meaning", sa.String(150), nullable=False),  # approval, rejection, review, release
        sa.Column("signed_by", sa.String(200), nullable=False),
        sa.Column("signed_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("signature_hash", sa.String(128), nullable=True),
        sa.Column("authentication_method", sa.String(100), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("source_ip", sa.String(80), nullable=True),
    )

    op.create_index("idx_signature_object", "electronic_signature", ["signed_object_type", "signed_object_id"])

    # ---------------------------------------------------------------------
    # 7) Supplier development / CAPA / action plan
    # ---------------------------------------------------------------------
    op.create_table(
        "supplier_action_plan",
        sa.Column("id_action_plan", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("id_relation", sa.Integer(), sa.ForeignKey("supplier_site_relation.id_relation", ondelete="SET NULL"), nullable=True),
        sa.Column("id_cycle", sa.Integer(), sa.ForeignKey("evaluation_cycle.id_cycle", ondelete="SET NULL"), nullable=True),
        sa.Column("id_document", sa.Integer(), sa.ForeignKey("document.id_document", ondelete="SET NULL"), nullable=True),
        sa.Column("trigger_type", sa.String(100), nullable=False),  # poor OTD, claim, audit finding, expired cert
        sa.Column("trigger_reference", sa.String(255), nullable=True),
        sa.Column("problem_statement", sa.Text(), nullable=False),
        sa.Column("containment_action", sa.Text(), nullable=True),
        sa.Column("root_cause", sa.Text(), nullable=True),
        sa.Column("corrective_action", sa.Text(), nullable=True),
        sa.Column("preventive_action", sa.Text(), nullable=True),
        sa.Column("owner", sa.String(200), nullable=True),
        sa.Column("supplier_owner", sa.String(200), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="'Open'"),
        sa.Column("effectiveness_check_required", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("effectiveness_result", sa.String(100), nullable=True),
        sa.Column("closed_by", sa.String(200), nullable=True),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by", sa.String(200), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )

    op.create_table(
        "supplier_action_plan_task",
        sa.Column("id_task", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("id_action_plan", sa.BigInteger(), sa.ForeignKey("supplier_action_plan.id_action_plan", ondelete="CASCADE"), nullable=False),
        sa.Column("task_description", sa.Text(), nullable=False),
        sa.Column("task_owner", sa.String(200), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="'Open'"),
        sa.Column("completion_evidence_document_id", sa.Integer(), sa.ForeignKey("document.id_document", ondelete="SET NULL"), nullable=True),
        sa.Column("completed_by", sa.String(200), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("comments", sa.Text(), nullable=True),
    )

    op.create_index("idx_action_plan_relation", "supplier_action_plan", ["id_relation"])
    op.create_index("idx_action_plan_cycle", "supplier_action_plan", ["id_cycle"])
    op.create_index("idx_action_plan_status", "supplier_action_plan", ["status"])

    # ---------------------------------------------------------------------
    # 8) User-role assignment for accountability
    # ---------------------------------------------------------------------
    op.create_table(
        "user_role_assignment",
        sa.Column("id_user_role", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_email", sa.String(200), nullable=False),
        sa.Column("role_name", sa.String(150), nullable=False),
        sa.Column("scope_type", sa.String(100), nullable=True),  # site, supplier, global
        sa.Column("scope_id", sa.String(100), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.UniqueConstraint("user_email", "role_name", "scope_type", "scope_id", name="uq_user_role_scope"),
    )

    # ---------------------------------------------------------------------
    # 9) Safer business constraints
    # Keep statuses flexible, no enums.
    # ---------------------------------------------------------------------
    op.create_check_constraint(
        "ck_score_card_score_range",
        "score_card",
        "(score IS NULL OR (score >= 0 AND score <= 100))",
    )
    op.create_check_constraint(
        "ck_classification_score_range",
        "classification",
        "(classification_score IS NULL OR (classification_score >= 0 AND classification_score <= 100))",
    )
    op.create_check_constraint(
        "ck_supplier_assessment_final_score_range",
        "supplier_assessment",
        "(final_score IS NULL OR (final_score >= 0 AND final_score <= 100))",
    )
    op.create_check_constraint(
        "ck_certification_dates",
        "supplier_certification",
        "(start_date IS NULL OR end_date IS NULL OR end_date >= start_date)",
    )
    op.create_check_constraint(
        "ck_cycle_dates",
        "evaluation_cycle",
        "(period_start IS NULL OR period_end IS NULL OR period_end >= period_start)",
    )

    # Useful indexes
    op.create_index("idx_supplier_unit_code", "supplier_unit", ["supplier_code"])
    op.create_index("idx_supplier_group_nom", "supplier_group", ["nom"])
    op.create_index("idx_relation_site_supplier", "supplier_site_relation", ["id_site", "id_supplier_unit"])
    op.create_index("idx_document_type_status", "document", ["document_type", "status"])
    op.create_index("idx_document_expiry", "document", ["expiry_date"])
    op.create_index("idx_certification_expiry", "supplier_certification", ["end_date"])
    op.create_index("idx_scorecard_relation_cycle", "score_card", ["id_relation", "id_cycle"])
    op.create_index("idx_classification_relation_cycle", "classification", ["id_relation", "id_cycle"])

    # ---------------------------------------------------------------------
    # 10) PostgreSQL automatic updated_at / row_version + audit triggers
    # ---------------------------------------------------------------------
    op.execute("""
    CREATE EXTENSION IF NOT EXISTS pgcrypto;
    """)

    op.execute("""
    CREATE OR REPLACE FUNCTION set_update_metadata()
    RETURNS trigger AS $$
    BEGIN
        NEW.updated_at = CURRENT_TIMESTAMP;
        NEW.row_version = COALESCE(OLD.row_version, 0) + 1;
        IF NEW.updated_by IS NULL THEN
            NEW.updated_by = NULLIF(current_setting('app.user_email', true), '');
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)

    op.execute("""
    CREATE OR REPLACE FUNCTION write_audit_event()
    RETURNS trigger AS $$
    DECLARE
        pk_value TEXT;
        actor TEXT;
    BEGIN
        actor := NULLIF(current_setting('app.user_email', true), '');

        IF TG_OP = 'DELETE' THEN
            pk_value := COALESCE(
                OLD.id_site::TEXT,
                OLD.id_group::TEXT,
                OLD.id_supplier_unit::TEXT,
                OLD.id_relation::TEXT,
                OLD.id_document::TEXT,
                OLD.id_cycle::TEXT,
                OLD.id_score_card::TEXT,
                OLD.id_classification::TEXT,
                OLD.id_assessment::TEXT,
                OLD.opportunity_id::TEXT,
                OLD.project_id::TEXT,
                OLD.financial_line_id::TEXT,
                OLD.monthly_financial_id::TEXT,
                OLD.id::TEXT
            );

            INSERT INTO audit_event(table_name, record_pk, action, changed_by, old_values)
            VALUES (TG_TABLE_NAME, COALESCE(pk_value, 'unknown'), TG_OP, actor, to_jsonb(OLD));

            RETURN OLD;
        ELSE
            pk_value := COALESCE(
                NEW.id_site::TEXT,
                NEW.id_group::TEXT,
                NEW.id_supplier_unit::TEXT,
                NEW.id_relation::TEXT,
                NEW.id_document::TEXT,
                NEW.id_cycle::TEXT,
                NEW.id_score_card::TEXT,
                NEW.id_classification::TEXT,
                NEW.id_assessment::TEXT,
                NEW.opportunity_id::TEXT,
                NEW.project_id::TEXT,
                NEW.financial_line_id::TEXT,
                NEW.monthly_financial_id::TEXT,
                NEW.id::TEXT
            );

            INSERT INTO audit_event(table_name, record_pk, action, changed_by, old_values, new_values)
            VALUES (
                TG_TABLE_NAME,
                COALESCE(pk_value, 'unknown'),
                TG_OP,
                actor,
                CASE WHEN TG_OP = 'UPDATE' THEN to_jsonb(OLD) ELSE NULL END,
                to_jsonb(NEW)
            );

            RETURN NEW;
        END IF;
    END;
    $$ LANGUAGE plpgsql;
    """)

    for table in CORE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_update_metadata ON {table}")

        op.execute(f"""
        CREATE TRIGGER trg_{table}_update_metadata
        BEFORE UPDATE ON {table}
        FOR EACH ROW
        EXECUTE FUNCTION set_update_metadata()
        """)

        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_audit ON {table}")

        op.execute(f"""
        CREATE TRIGGER trg_{table}_audit
        AFTER INSERT OR UPDATE OR DELETE ON {table}
        FOR EACH ROW
        EXECUTE FUNCTION write_audit_event()
        """)


def downgrade():
    for table in CORE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_audit ON {table};")
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_update_metadata ON {table};")

    op.execute("DROP FUNCTION IF EXISTS write_audit_event();")
    op.execute("DROP FUNCTION IF EXISTS set_update_metadata();")

    op.drop_index("idx_classification_relation_cycle", table_name="classification")
    op.drop_index("idx_scorecard_relation_cycle", table_name="score_card")
    op.drop_index("idx_certification_expiry", table_name="supplier_certification")
    op.drop_index("idx_document_expiry", table_name="document")
    op.drop_index("idx_document_type_status", table_name="document")
    op.drop_index("idx_relation_site_supplier", table_name="supplier_site_relation")
    op.drop_index("idx_supplier_group_nom", table_name="supplier_group")
    op.drop_index("idx_supplier_unit_code", table_name="supplier_unit")

    op.drop_constraint("ck_cycle_dates", "evaluation_cycle", type_="check")
    op.drop_constraint("ck_certification_dates", "supplier_certification", type_="check")
    op.drop_constraint("ck_supplier_assessment_final_score_range", "supplier_assessment", type_="check")
    op.drop_constraint("ck_classification_score_range", "classification", type_="check")
    op.drop_constraint("ck_score_card_score_range", "score_card", type_="check")

    op.drop_table("user_role_assignment")
    op.drop_table("supplier_action_plan_task")
    op.drop_table("supplier_action_plan")
    op.drop_table("electronic_signature")
    op.drop_table("record_retention_policy")
    op.drop_table("document_approval")
    op.drop_table("document_revision")

    for table in ["input_otd_monthly", "input_quality_claims", "input_delivery_spend"]:
        op.drop_index(f"idx_{table}_import_batch", table_name=table)
        op.drop_column(table, "source_row_hash")
        op.drop_column(table, "source_row_number")
        op.drop_column(table, "id_import_batch")

    op.drop_table("import_batch")
    op.drop_table("audit_event")

    op.drop_column("document", "superseded_by_document_id")
    op.drop_column("document", "storage_object_key")
    op.drop_column("document", "storage_provider")
    op.drop_column("document", "file_hash_sha256")
    op.drop_column("document", "expiry_date")
    op.drop_column("document", "review_due_date")
    op.drop_column("document", "retention_code")
    op.drop_column("document", "controlled_document")
    op.drop_column("document", "document_owner")

    for table in reversed(CORE_TABLES):
        op.drop_column(table, "row_version")
        op.drop_column(table, "deleted_by")
        op.drop_column(table, "deleted_at")
        op.drop_column(table, "is_deleted")
        op.drop_column(table, "updated_by")
        op.drop_column(table, "updated_at")