"""Fix two cons_or_wd scoring gaps found while auditing geo_coverage:

  - "DDP or Weekly Del." was scored 0 in pld_scoring_rules, but both frontend
    components (RelationEvaluationPage.tsx, EvaluationDetailsForm.tsx) have
    always scored it 50 -- confirmed 50 is the correct value. Corrected via a
    plain UPDATE (not deactivate+insert): scoring lookups
    (SupplierRelationService) only query by criteria_type/min_value/is_active,
    never by rule_id, so there is no historical snapshot tied to this row's
    identity worth preserving -- deactivate+insert would just leave a
    redundant duplicate row for the same min_value.
  - "None" had no active scoring rule at all for cons_or_wd, even though
    relations exist with that stored value -- it was silently falling
    through to 0 via the unmatched-value fallback in
    SupplierRelationService (logs a warning) rather than an intentional
    rule. Add the missing None=0 row.

Revision ID: 20260709_0081
Revises: 20260709_0080
Create Date: 2026-07-09
"""

from alembic import op
import sqlalchemy as sa

revision = "20260709_0081"
down_revision = "20260709_0080"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # Correct the "DDP or Weekly Del." score in place (0 -> 50)
    conn.execute(
        sa.text(
            "UPDATE pld_scoring_rules SET score = 50 "
            "WHERE criteria_type = 'cons_or_wd' AND min_value = 'DDP or Weekly Del.' AND score = 0"
        )
    )

    # Add the missing "None" = 0 rule
    conn.execute(
        sa.text(
            "INSERT INTO pld_scoring_rules "
            "(criteria_type, score, min_value, max_value, description, is_active) "
            "SELECT 'cons_or_wd', 0, 'None', 'None', "
            "'cons_or_wd exact match score for None', true "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM pld_scoring_rules "
            "  WHERE criteria_type = 'cons_or_wd' AND min_value = 'None'"
            ")"
        )
    )


def downgrade() -> None:
    conn = op.get_bind()

    conn.execute(
        sa.text(
            "UPDATE pld_scoring_rules SET score = 0 "
            "WHERE criteria_type = 'cons_or_wd' AND min_value = 'DDP or Weekly Del.' AND score = 50"
        )
    )
    conn.execute(
        sa.text(
            "DELETE FROM pld_scoring_rules "
            "WHERE criteria_type = 'cons_or_wd' AND min_value = 'None'"
        )
    )
