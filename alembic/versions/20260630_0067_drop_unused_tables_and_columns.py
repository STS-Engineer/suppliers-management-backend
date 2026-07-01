"""Drop unused tables and columns identified in June 2026 audit

Tables dropped (zero references in any service/router/schema):
  - electronic_signature
  - record_retention_policy
  - scorecard_upload_register
  - scorecard_data_quality_checks   (note: plural, matches __tablename__)
  - document_approval
  - document_revision

Columns dropped from existing tables:
  document:
    - file_path          (never read by any endpoint)
    - retention_code     (write-only legacy field)
    - review_due_date    (write-only legacy field)
    - expiry_date        (write-only legacy field)
    - controlled_document (write-only legacy field)
    - document_owner     (write-only legacy field)
  opportunity:
    - status2            (never populated or queried)
    - Lead_time          (never populated or queried)

Revision ID: 20260630_0067
Revises: 20260630_0066
Create Date: 2026-06-30
"""

from alembic import op
import sqlalchemy as sa

revision = "20260630_0067"
down_revision = "20260630_0066"
branch_labels = None
depends_on = None

_DROP_TABLES = [
    "electronic_signature",
    "record_retention_policy",
    "scorecard_upload_register",
    "scorecard_data_quality_checks",
    "document_approval",
    "document_revision",
]

_DROP_DOCUMENT_COLS = [
    "file_path",
    "retention_code",
    "review_due_date",
    "expiry_date",
    "controlled_document",
    "document_owner",
]

_DROP_OPPORTUNITY_COLS = [
    "status2",
    "Lead_time",
]


def _drop_if_exists(table: str) -> None:
    op.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')


def _drop_column_if_exists(table: str, column: str) -> None:
    op.execute(
        f"""
        ALTER TABLE "{table}"
        DROP COLUMN IF EXISTS "{column}"
        """
    )


def upgrade() -> None:
    # Drop tables with IF EXISTS — some may never have been created in this environment.
    # Order: child tables that FK into document first, then independent tables.
    _drop_if_exists("document_revision")
    _drop_if_exists("document_approval")
    _drop_if_exists("scorecard_data_quality_checks")   # plural — matches __tablename__
    _drop_if_exists("scorecard_upload_register")
    _drop_if_exists("record_retention_policy")
    _drop_if_exists("electronic_signature")

    # Drop unused columns from document (IF EXISTS — tolerant of missing cols)
    for col in _DROP_DOCUMENT_COLS:
        _drop_column_if_exists("document", col)

    # Drop unused columns from opportunity
    for col in _DROP_OPPORTUNITY_COLS:
        _drop_column_if_exists("opportunity", col)


def downgrade() -> None:
    # Restore opportunity columns
    with op.batch_alter_table("opportunity") as batch_op:
        batch_op.add_column(sa.Column("Lead_time", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("status2", sa.String(length=50), nullable=True))

    # Restore document columns
    with op.batch_alter_table("document") as batch_op:
        batch_op.add_column(sa.Column("document_owner", sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column("controlled_document", sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column("expiry_date", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("review_due_date", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("retention_code", sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column("file_path", sa.String(length=500), nullable=True))

    # Recreate dropped tables (empty shells — data is gone)
    op.create_table(
        "electronic_signature",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=True),
        sa.Column("signer_id", sa.Integer(), nullable=True),
        sa.Column("signed_at", sa.DateTime(), nullable=True),
        sa.Column("signature_hash", sa.String(length=500), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "record_retention_policy",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("retention_code", sa.String(length=50), nullable=True),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("retention_years", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "scorecard_upload_register",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("relation_id", sa.Integer(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(), nullable=True),
        sa.Column("uploaded_by", sa.String(length=200), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "scorecard_data_quality_checks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("register_id", sa.Integer(), nullable=True),
        sa.Column("check_result", sa.String(length=500), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "document_approval",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=True),
        sa.Column("approved_by", sa.String(length=200), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "document_revision",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=True),
        sa.Column("revision_number", sa.Integer(), nullable=True),
        sa.Column("revised_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
