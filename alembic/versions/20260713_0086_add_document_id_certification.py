"""link documents to a specific certification (multiple files per cert)

A certification can accumulate several files over time (renewals: the supplier
sends a new certificate each period). The single supplier_certification.file_url
column can't hold that history, so documents gain an id_certification FK:
one certification -> many document rows. supplier_certification.file_url stays
as the denormalized "latest file" pointer for backward compatibility.

Revision ID: 20260713_0086
Revises: 20260713_0085
Create Date: 2026-07-13 13:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260713_0086"
down_revision = "20260713_0085"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("document")}
    if "id_certification" not in columns:
        op.add_column(
            "document",
            sa.Column("id_certification", sa.Integer(), nullable=True),
        )
        op.create_foreign_key(
            "fk_document_certification",
            "document",
            "supplier_certification",
            ["id_certification"],
            ["id_certification"],
            ondelete="SET NULL",
        )
    indexes = {ix["name"] for ix in inspector.get_indexes("document")}
    if "idx_document_certification" not in indexes:
        op.create_index(
            "idx_document_certification", "document", ["id_certification"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    indexes = {ix["name"] for ix in inspector.get_indexes("document")}
    if "idx_document_certification" in indexes:
        op.drop_index("idx_document_certification", table_name="document")
    columns = {col["name"] for col in inspector.get_columns("document")}
    if "id_certification" in columns:
        fks = {fk["name"] for fk in inspector.get_foreign_keys("document")}
        if "fk_document_certification" in fks:
            op.drop_constraint(
                "fk_document_certification", "document", type_="foreignkey"
            )
        op.drop_column("document", "id_certification")
