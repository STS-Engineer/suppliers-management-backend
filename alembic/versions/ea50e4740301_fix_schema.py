"""fix_schema_issues

Revision ID: ea50e4740301
Revises: ce0a132bb0be
Create Date: 2026-05-14

Fixes:
  1. Audit trigger PK resolution covers all tables
  2. Triggers applied to new tables created in previous migration
  3. supplier_site_relation unique constraint
  4. supplier_action_plan missing governance columns
  5. SupplierCertification orphan document-control columns dropped
"""
from alembic import op
import sqlalchemy as sa

revision = 'ea50e4740301'
down_revision = 'ce0a132bb0be'
branch_labels = None
depends_on = None

NEW_AUDITED_TABLES = [
    "supplier_action_plan",
    "document_revision",
    "document_approval",
    "import_batch",
]


def upgrade():
    # ------------------------------------------------------------------
    # 1. Replace write_audit_event with complete PK resolution
    # ------------------------------------------------------------------
    op.execute("""
    CREATE OR REPLACE FUNCTION write_audit_event()
    RETURNS trigger AS $$
    DECLARE
        pk_col  TEXT   := TG_ARGV[0];
        pk_value TEXT;
        actor   TEXT;
    BEGIN
        actor := NULLIF(current_setting('app.user_email', true), '');

        IF TG_OP = 'DELETE' THEN
            EXECUTE format('SELECT ($1).%I::TEXT', pk_col) INTO pk_value USING OLD;
            INSERT INTO audit_event(table_name, record_pk, action, changed_by, old_values)
            VALUES (TG_TABLE_NAME, COALESCE(pk_value, 'unknown'), TG_OP, actor, to_jsonb(OLD));
            RETURN OLD;
        ELSE
            EXECUTE format('SELECT ($1).%I::TEXT', pk_col) INTO pk_value USING NEW;
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

    # Map every table in CORE_TABLES to its actual PK column name,
    # then recreate the audit trigger passing the PK column as an argument.
    core_table_pks = {
        "avocarbon_site":                    "id_site",
        "supplier_group":                    "id_group",
        "supplier_unit":                     "id_supplier_unit",
        "supplier_site_relation":            "id_relation",
        "supplier_certification":            "id_certification",
        "supplier_agreement":                "id_agreement",
        "contact":                           "id_contact",
        "document":                          "id_document",
        "evaluation_cycle":                  "id_cycle",
        "approval_workflow":                 "id_approval",
        "escalation":                        "id_escalation",
        "score_card":                        "id_score_card",
        "classification":                    "id_classification",
        "scorecard_kpi_detail":              "id_kpi_detail",
        "scorecard_upload_register":         "id_upload_register",
        "scorecard_data_quality_checks":     "id_check",
        "input_otd_monthly":                 "id_otd",
        "input_quality_claims":              "id_quality_claim",
        "input_delivery_spend":              "id_delivery_spend",
        "assessment_template":               "id_template",
        "assessment_template_field_mapping": "id_mapping",
        "supplier_assessment":               "id_assessment",
        "supplier_assessment_answer":        "id_answer",
        "opportunity":                       "opportunity_id",
        "project":                           "project_id",
        "financial_line":                    "financial_line_id",
        "monthly_financial":                 "monthly_financial_id",
    }

    for table, pk_col in core_table_pks.items():
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_audit ON {table}")
        op.execute(f"""
        CREATE TRIGGER trg_{table}_audit
        AFTER INSERT OR UPDATE OR DELETE ON {table}
        FOR EACH ROW
        EXECUTE FUNCTION write_audit_event('{pk_col}')
        """)

    # ------------------------------------------------------------------
    # 2. Apply audit + update_metadata triggers to new tables
    # ------------------------------------------------------------------
    new_table_pks = {
        "supplier_action_plan": "id_action_plan",
        "document_revision":    "id_document_revision",
        "document_approval":    "id_document_approval",
        "import_batch":         "id_import_batch",
    }

    for table, pk_col in new_table_pks.items():
        # update_metadata only applies to tables that have updated_at / row_version
        if table in ("supplier_action_plan", "import_batch"):
            # import_batch has no updated_at/row_version — skip metadata trigger
            if table == "supplier_action_plan":
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
        EXECUTE FUNCTION write_audit_event('{pk_col}')
        """)

    # ------------------------------------------------------------------
    # 3. Unique constraint on supplier_site_relation
    #    Run the duplicate check first (see instructions below).
    # ------------------------------------------------------------------
    op.create_unique_constraint(
        "uq_relation_site_supplier",
        "supplier_site_relation",
        ["id_site", "id_supplier_unit"],
    )

    # ------------------------------------------------------------------
    # 4. supplier_action_plan — add missing governance columns
    #    (deleted_at, deleted_by, row_version are not present because the
    #     table was created inline in the previous migration without using
    #     the loop that adds governance columns to CORE_TABLES)
    # ------------------------------------------------------------------
    op.add_column("supplier_action_plan",
        sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.add_column("supplier_action_plan",
        sa.Column("deleted_by", sa.String(200), nullable=True))
    op.add_column("supplier_action_plan",
        sa.Column("row_version", sa.Integer(), nullable=False,
                  server_default="1"))

    # ------------------------------------------------------------------
    # 5. Drop orphan document-control columns from supplier_certification
    #    These columns exist in the ORM model but were never created in
    #    the DB by any migration, so PostgreSQL will raise an error if the
    #    app tries to SELECT/INSERT them. Removing from the ORM is the fix;
    #    this step is a safety drop in case a partial migration created them.
    #    If the drop fails with "column does not exist" that is fine — means
    #    the DB is already clean. Wrap each in a try/except in a raw
    #    connection if you prefer; here we use IF EXISTS via raw SQL.
    # ------------------------------------------------------------------
    orphan_cols = [
        "document_owner",
        "controlled_document",
        "retention_code",
        "review_due_date",
        "expiry_date",
        "file_hash_sha256",
        "storage_provider",
        "storage_object_key",
        "superseded_by_document_id",
    ]
    for col in orphan_cols:
        op.execute(
            f"ALTER TABLE supplier_certification DROP COLUMN IF EXISTS {col}"
        )

    # ------------------------------------------------------------------
    # 6. Fix id_import_batch FK column type on input tables
    #    Change Integer -> BigInteger to match import_batch PK
    # ------------------------------------------------------------------
    for table in ("input_otd_monthly", "input_quality_claims", "input_delivery_spend"):
        op.alter_column(
            table, "id_import_batch",
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=True,
        )


def downgrade():
    # Reverse 6
    for table in ("input_otd_monthly", "input_quality_claims", "input_delivery_spend"):
        op.alter_column(
            table, "id_import_batch",
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=True,
        )

    # Reverse 5 — nothing to restore, columns were orphans

    # Reverse 4
    op.drop_column("supplier_action_plan", "row_version")
    op.drop_column("supplier_action_plan", "deleted_by")
    op.drop_column("supplier_action_plan", "deleted_at")

    # Reverse 3
    op.drop_constraint("uq_relation_site_supplier", "supplier_site_relation",
                       type_="unique")

    # Reverse 2 — drop triggers on new tables
    for table in ("supplier_action_plan", "document_revision",
                  "document_approval", "import_batch"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_audit ON {table}")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_supplier_action_plan_update_metadata "
        "ON supplier_action_plan"
    )

    # Reverse 1 — restore original (broken) write_audit_event
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
                OLD.id_site::TEXT, OLD.id_group::TEXT, OLD.id_supplier_unit::TEXT,
                OLD.id_relation::TEXT, OLD.id_document::TEXT, OLD.id_cycle::TEXT,
                OLD.id_score_card::TEXT, OLD.id_classification::TEXT,
                OLD.id_assessment::TEXT, OLD.opportunity_id::TEXT,
                OLD.project_id::TEXT, OLD.financial_line_id::TEXT,
                OLD.monthly_financial_id::TEXT, OLD.id::TEXT
            );
            INSERT INTO audit_event(table_name, record_pk, action, changed_by, old_values)
            VALUES (TG_TABLE_NAME, COALESCE(pk_value,'unknown'), TG_OP, actor, to_jsonb(OLD));
            RETURN OLD;
        ELSE
            pk_value := COALESCE(
                NEW.id_site::TEXT, NEW.id_group::TEXT, NEW.id_supplier_unit::TEXT,
                NEW.id_relation::TEXT, NEW.id_document::TEXT, NEW.id_cycle::TEXT,
                NEW.id_score_card::TEXT, NEW.id_classification::TEXT,
                NEW.id_assessment::TEXT, NEW.opportunity_id::TEXT,
                NEW.project_id::TEXT, NEW.financial_line_id::TEXT,
                NEW.monthly_financial_id::TEXT, NEW.id::TEXT
            );
            INSERT INTO audit_event(table_name, record_pk, action, changed_by, old_values, new_values)
            VALUES (TG_TABLE_NAME, COALESCE(pk_value,'unknown'), TG_OP, actor,
                    CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE NULL END, to_jsonb(NEW));
            RETURN NEW;
        END IF;
    END;
    $$ LANGUAGE plpgsql;
    """)