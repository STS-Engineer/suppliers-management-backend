"""Create contact_site_relation table.

Revision ID: 20260605_0020
Revises: 20260605_0019
Create Date: 2026-06-05

The contact_site_relation junction table links a Contact to a
SupplierSiteRelation so that each relation can have a designated
external contact (the person responsible at the supplier group side).
"""

from alembic import op
import sqlalchemy as sa


revision = "20260605_0020"
down_revision = "20260605_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "contact_site_relation",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("id_contact", sa.Integer(), nullable=False),
        sa.Column("id_relation", sa.Integer(), nullable=False),
        # GovernanceMixin columns
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by", sa.String(200), nullable=True),
        sa.Column(
            "is_deleted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_by", sa.String(200), nullable=True),
        sa.Column(
            "row_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.ForeignKeyConstraint(
            ["id_contact"], ["contact.id_contact"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["id_relation"],
            ["supplier_site_relation.id_relation"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "id_contact",
            "id_relation",
            name="contact_site_relation_id_contact_id_relation_key",
        ),
    )
    op.create_index(
        "idx_contact_site_relation_relation",
        "contact_site_relation",
        ["id_relation"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_contact_site_relation_relation",
        table_name="contact_site_relation",
    )
    op.drop_table("contact_site_relation")
