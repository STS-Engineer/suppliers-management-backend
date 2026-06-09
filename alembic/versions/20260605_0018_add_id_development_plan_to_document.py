"""add id_development_plan to document table for multi-file plan support

Revision ID: 20260605_0018
Revises: 20260604_0017
Create Date: 2026-06-05
"""

from alembic import op
import sqlalchemy as sa

revision = "20260605_0018"
down_revision = "20260604_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("document")}

    if "id_development_plan" not in columns:
        op.add_column(
            "document",
            sa.Column("id_development_plan", sa.Integer(), nullable=True),
        )
        op.create_foreign_key(
            "fk_document_id_development_plan",
            "document",
            "supplier_development_plan",
            ["id_development_plan"],
            ["id_development_plan"],
            ondelete="SET NULL",
        )
        op.create_index(
            "idx_document_development_plan",
            "document",
            ["id_development_plan"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("document")}

    if "id_development_plan" in columns:
        op.drop_index("idx_document_development_plan", table_name="document")
        op.drop_constraint(
            "fk_document_id_development_plan", "document", type_="foreignkey"
        )
        op.drop_column("document", "id_development_plan")
