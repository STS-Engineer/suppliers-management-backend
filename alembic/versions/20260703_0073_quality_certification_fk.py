"""Replace pld_class_evaluation_input.quality_certification string with id_certification FK

quality_certification used to be a copied string snapshot of
SupplierCertification.certification_type, with no ongoing link back to the real
record -- so it could silently go stale once the backing certificate expired,
with nothing re-checking it until some unrelated write touched the same row.

Replaces it with a real foreign key so "is this still valid" becomes a trivial
end_date check on the referenced row instead of a fragile string re-match.
"Current status" reads (Class Evaluation page, Criteria Validity Tracker, public
directory, purchasing-value snapshot) now always re-derive live from the unit's
certifications rather than trusting this column at all; it only serves as a
historical audit pointer for what a past evaluation snapshot considered current.

Dev-only cutover: per project decision, no backfill of historical string values
to matching certification ids -- existing rows get id_certification = NULL,
which only affects old evaluation snapshots' cosmetic history display.

Revision ID: 20260703_0073
Revises: 20260702_0072
Create Date: 2026-07-03
"""

from alembic import op
import sqlalchemy as sa


revision = "20260703_0073"
down_revision = "20260702_0072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE "pld_class_evaluation_input" DROP COLUMN IF EXISTS "quality_certification"
        """
    )
    op.add_column(
        "pld_class_evaluation_input",
        sa.Column(
            "id_certification",
            sa.Integer(),
            sa.ForeignKey("supplier_certification.id_certification", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("pld_class_evaluation_input", "id_certification")
    op.add_column(
        "pld_class_evaluation_input",
        sa.Column("quality_certification", sa.String(length=255), nullable=True),
    )
