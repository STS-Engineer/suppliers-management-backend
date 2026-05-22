"""Add document and audit tables for criteria evidence.

Revision ID: 20260521_0004
Revises: 20260520_0003
Create Date: 2026-05-21
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260521_0004"
down_revision = "20260520_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document",
        sa.Column("id_document", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "id_relation",
            sa.Integer(),
            sa.ForeignKey("supplier_site_relation.id_relation", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "id_supplier_unit",
            sa.Integer(),
            sa.ForeignKey("supplier_unit.id_supplier_unit", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "id_group",
            sa.Integer(),
            sa.ForeignKey("supplier_group.id_group", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("document_type", sa.String(length=100), nullable=False),
        sa.Column("document_name", sa.String(length=255), nullable=False),
        sa.Column("original_file_name", sa.String(length=255), nullable=True),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("file_url", sa.Text(), nullable=True),
        sa.Column("mime_type", sa.String(length=100), nullable=True),
        sa.Column("file_size", sa.Numeric(18, 2), nullable=True),
        sa.Column("uploaded_by", sa.String(length=200), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(), nullable=True, server_default=sa.func.current_timestamp()),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("version", sa.String(length=50), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default=sa.text("'Uploaded'")),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column("document_owner", sa.String(length=200), nullable=True),
        sa.Column("controlled_document", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("retention_code", sa.String(length=100), nullable=True),
        sa.Column("review_due_date", sa.Date(), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("file_hash_sha256", sa.String(length=64), nullable=True),
        sa.Column("storage_provider", sa.String(length=100), nullable=True),
        sa.Column("storage_object_key", sa.Text(), nullable=True),
        sa.Column(
            "superseded_by_document_id",
            sa.Integer(),
            sa.ForeignKey("document.id_document", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.func.current_timestamp()),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by", sa.String(length=200), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_by", sa.String(length=200), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )
    op.create_index("idx_document_relation", "document", ["id_relation"], unique=False)

    op.create_table(
        "audit_event",
        sa.Column("id_audit_event", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "event_uuid",
            postgresql.UUID(as_uuid=False),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("table_name", sa.String(length=150), nullable=False),
        sa.Column("record_pk", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("changed_by", sa.String(length=200), nullable=True),
        sa.Column("changed_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("old_values", postgresql.JSONB(), nullable=True),
        sa.Column("new_values", postgresql.JSONB(), nullable=True),
        sa.Column("reason_code", sa.String(length=150), nullable=True),
        sa.Column("reason_comment", sa.Text(), nullable=True),
        sa.Column("source_system", sa.String(length=150), nullable=True),
        sa.Column("source_ip", sa.String(length=80), nullable=True),
        sa.Column("correlation_id", sa.String(length=150), nullable=True),
        sa.Column("batch_id", sa.String(length=150), nullable=True),
        sa.Column("is_system_event", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_index("idx_audit_event_table_record", "audit_event", ["table_name", "record_pk"], unique=False)
    op.create_index("idx_audit_event_changed_at", "audit_event", ["changed_at"], unique=False)
    op.create_index("idx_audit_event_correlation", "audit_event", ["correlation_id"], unique=False)

    op.create_table(
        "document_revision",
        sa.Column("id_document_revision", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "id_document",
            sa.Integer(),
            sa.ForeignKey("document.id_document", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision_code", sa.String(length=50), nullable=False),
        sa.Column("revision_date", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("changed_by", sa.String(length=200), nullable=True),
        sa.Column("change_reason", sa.Text(), nullable=True),
        sa.Column("file_hash_sha256", sa.String(length=64), nullable=True),
        sa.Column("file_url", sa.Text(), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
    )
    op.create_index("idx_document_revision_document", "document_revision", ["id_document"], unique=False)

    op.create_table(
        "document_approval",
        sa.Column("id_document_approval", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "id_document",
            sa.Integer(),
            sa.ForeignKey("document.id_document", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("approval_step", sa.Integer(), nullable=False),
        sa.Column("approver_role", sa.String(length=150), nullable=True),
        sa.Column("approver_email", sa.String(length=200), nullable=True),
        sa.Column("decision", sa.String(length=50), nullable=False, server_default=sa.text("'Pending'")),
        sa.Column("decision_at", sa.DateTime(), nullable=True),
        sa.Column("decision_comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
    )
    op.create_index("idx_document_approval_document", "document_approval", ["id_document"], unique=False)

    op.add_column(
        "pld_class_criteria_detail",
        sa.Column("id_document", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_pld_class_criteria_detail_id_document_document",
        "pld_class_criteria_detail",
        "document",
        ["id_document"],
        ["id_document"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_pld_class_criteria_detail_id_document_document",
        "pld_class_criteria_detail",
        type_="foreignkey",
    )
    op.drop_column("pld_class_criteria_detail", "id_document")
    op.drop_index("idx_document_approval_document", table_name="document_approval")
    op.drop_table("document_approval")
    op.drop_index("idx_document_revision_document", table_name="document_revision")
    op.drop_table("document_revision")
    op.drop_index("idx_audit_event_correlation", table_name="audit_event")
    op.drop_index("idx_audit_event_changed_at", table_name="audit_event")
    op.drop_index("idx_audit_event_table_record", table_name="audit_event")
    op.drop_table("audit_event")
    op.drop_index("idx_document_relation", table_name="document")
    op.drop_table("document")
