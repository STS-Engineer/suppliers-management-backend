"""add document link to supplier development plan

Revision ID: 20260602_0007
Revises: 20260529_0006
Create Date: 2026-06-02
"""

from alembic import op
import sqlalchemy as sa


revision = "20260602_0007"
down_revision = "20260529_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("supplier_development_plan")}

    if "id_document" not in columns:
        op.add_column(
            "supplier_development_plan",
            sa.Column("id_document", sa.Integer(), nullable=True),
        )
        op.create_foreign_key(
            "fk_supplier_development_plan_id_document_document",
            "supplier_development_plan",
            "document",
            ["id_document"],
            ["id_document"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("supplier_development_plan")}

    if "id_document" in columns:
        op.drop_constraint(
            "fk_supplier_development_plan_id_document_document",
            "supplier_development_plan",
            type_="foreignkey",
        )
        op.drop_column("supplier_development_plan", "id_document")
