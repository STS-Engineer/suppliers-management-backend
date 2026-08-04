"""add DB-level audit trigger for direct-edit tracking

Adds a generic AFTER INSERT/UPDATE/DELETE trigger (fn_audit_event) that
writes into the existing audit_event table independently of the FastAPI
service layer's _record_audit_event calls — this is what catches direct
psql/pgAdmin edits that bypass the app entirely. Attached to the
supplier relation tables (already partially audited by the app) plus
the purchasing-value financial tables, which today have no audit trail
at all, app-level or DB-level.

Note: changed_by is populated from session_user, which is only useful
for attribution once each person connects with their own Postgres
login rather than one shared login.

Revision ID: 20260730_0093
Revises: 20260729_0092
Create Date: 2026-07-30
"""
from alembic import op

revision = "20260730_0093"
down_revision = "20260729_0092"
branch_labels = None
depends_on = None

AUDITED_TABLES = {
    "supplier_group": "id_group",
    "supplier_unit": "id_supplier_unit",
    "supplier_site_relation": "id_relation",
    "opportunity": "opportunity_id",
    "financial_line": "financial_line_id",
    "monthly_financial": "monthly_financial_id",
    "opportunity_budget_year": "id",
}

TRIGGER_FUNCTION = """
CREATE OR REPLACE FUNCTION fn_audit_event() RETURNS trigger AS $$
DECLARE
    pk_column text := TG_ARGV[0];
    pk_value text;
    old_json jsonb;
    new_json jsonb;
BEGIN
    IF TG_OP = 'DELETE' THEN
        old_json := to_jsonb(OLD);
        pk_value := old_json ->> pk_column;
    ELSE
        new_json := to_jsonb(NEW);
        pk_value := new_json ->> pk_column;
        IF TG_OP = 'UPDATE' THEN
            old_json := to_jsonb(OLD);
        END IF;
    END IF;

    INSERT INTO audit_event (
        table_name, record_pk, action, changed_by,
        old_values, new_values, source_system, is_system_event
    ) VALUES (
        TG_TABLE_NAME, pk_value, TG_OP, session_user,
        old_json, new_json, 'db_trigger', false
    );

    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.execute(TRIGGER_FUNCTION)
    for table, pk_column in AUDITED_TABLES.items():
        op.execute(
            f"""
            CREATE TRIGGER trg_audit_{table}
            AFTER INSERT OR UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION fn_audit_event('{pk_column}');
            """
        )


def downgrade() -> None:
    for table in AUDITED_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_audit_{table} ON {table};")
    op.execute("DROP FUNCTION IF EXISTS fn_audit_event();")
