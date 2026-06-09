"""add supplier development plan table

Revision ID: 20260529_0006
Revises: 20260525_0005
Create Date: 2026-05-29
"""

from alembic import op
import sqlalchemy as sa


revision = "20260529_0006"
down_revision = "20260525_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "supplier_development_plan",
        sa.Column("id_development_plan", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("id_relation", sa.Integer(), nullable=False),
        sa.Column("id_document", sa.Integer(), nullable=True),
        sa.Column("plan_title", sa.String(length=255), nullable=True),
        sa.Column("plan_status", sa.String(length=100), nullable=True),
        sa.Column("issue_date", sa.Date(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("submission_date", sa.Date(), nullable=True),
        sa.Column("review_date", sa.Date(), nullable=True),
        sa.Column("decision_date", sa.Date(), nullable=True),
        sa.Column("reviewed_by", sa.String(length=200), nullable=True),
        sa.Column("approved_by", sa.String(length=200), nullable=True),
        sa.Column("rejected_by", sa.String(length=200), nullable=True),
        sa.Column("business_hold_active", sa.Boolean(), nullable=True),
        sa.Column("escalated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("escalation_date", sa.Date(), nullable=True),
        sa.Column("file_name", sa.String(length=255), nullable=True),
        sa.Column("file_url", sa.String(length=1000), nullable=True),
        sa.Column("file_notes", sa.Text(), nullable=True),
        sa.Column("supplier_comments", sa.Text(), nullable=True),
        sa.Column("internal_comments", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by", sa.String(length=200), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_by", sa.String(length=200), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.ForeignKeyConstraint(["id_relation"], ["supplier_site_relation.id_relation"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["id_document"], ["document.id_document"], ondelete="SET NULL"),
    )
    op.create_index(
        "idx_supplier_development_plan_relation",
        "supplier_development_plan",
        ["id_relation"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_supplier_development_plan_relation",
        table_name="supplier_development_plan",
    )
    op.drop_table("supplier_development_plan")
