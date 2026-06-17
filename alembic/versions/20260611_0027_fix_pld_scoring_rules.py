"""Fix PLD scoring rules to match Monday.com formulas.

Changes:
  - family_coverage: "Supplier can make the main family requirements"  80 → 50
  - family_coverage: "Supplier can make only of few family requirements" 50 → 30
  - sqma:            "Signed m.res."                                    80 → 50
  - top:             add "15 days net" = 10
  - prod_lia_ins:    add "2M€ or +" = 100 and "1M€ or +" = 50
  - class thresholds: documented only (enforced in service.py)

Revision ID: 20260611_0027
Revises: 20260610_0026
Create Date: 2026-06-11
"""

from alembic import op
import sqlalchemy as sa

revision = "20260611_0027"
down_revision = "20260610_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # Fix family_coverage intermediate scores
    conn.execute(
        sa.text(
            "UPDATE pld_scoring_rules SET score = 50 "
            "WHERE criteria_type = 'family_coverage' "
            "AND min_value = 'Supplier can make the main family requirements'"
        )
    )
    conn.execute(
        sa.text(
            "UPDATE pld_scoring_rules SET score = 30 "
            "WHERE criteria_type = 'family_coverage' "
            "AND min_value = 'Supplier can make only of few family requirements'"
        )
    )

    # Fix sqma "Signed m.res." score
    conn.execute(
        sa.text(
            "UPDATE pld_scoring_rules SET score = 50 "
            "WHERE criteria_type = 'sqma' AND min_value = 'Signed m.res.'"
        )
    )

    # Add missing top rule
    conn.execute(
        sa.text(
            "INSERT INTO pld_scoring_rules "
            "(criteria_type, score, min_value, max_value, description, is_active) "
            "SELECT 'top', 10, '15 days net', '15 days net', "
            "'top exact match score for 15 days net', true "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM pld_scoring_rules "
            "  WHERE criteria_type = 'top' AND min_value = '15 days net'"
            ")"
        )
    )

    # Add euro variants for prod_lia_ins
    conn.execute(
        sa.text(
            "INSERT INTO pld_scoring_rules "
            "(criteria_type, score, min_value, max_value, description, is_active) "
            "SELECT 'prod_lia_ins', 100, '2M€ or +', '2M€ or +', "
            "'prod_lia_ins exact match score for 2M€ or +', true "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM pld_scoring_rules "
            "  WHERE criteria_type = 'prod_lia_ins' AND min_value = '2M€ or +'"
            ")"
        )
    )
    conn.execute(
        sa.text(
            "INSERT INTO pld_scoring_rules "
            "(criteria_type, score, min_value, max_value, description, is_active) "
            "SELECT 'prod_lia_ins', 50, '1M€ or +', '1M€ or +', "
            "'prod_lia_ins exact match score for 1M€ or +', true "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM pld_scoring_rules "
            "  WHERE criteria_type = 'prod_lia_ins' AND min_value = '1M€ or +'"
            ")"
        )
    )


def downgrade() -> None:
    conn = op.get_bind()

    conn.execute(
        sa.text(
            "UPDATE pld_scoring_rules SET score = 80 "
            "WHERE criteria_type = 'family_coverage' "
            "AND min_value = 'Supplier can make the main family requirements'"
        )
    )
    conn.execute(
        sa.text(
            "UPDATE pld_scoring_rules SET score = 50 "
            "WHERE criteria_type = 'family_coverage' "
            "AND min_value = 'Supplier can make only of few family requirements'"
        )
    )
    conn.execute(
        sa.text(
            "UPDATE pld_scoring_rules SET score = 80 "
            "WHERE criteria_type = 'sqma' AND min_value = 'Signed m.res.'"
        )
    )
    conn.execute(
        sa.text(
            "DELETE FROM pld_scoring_rules "
            "WHERE criteria_type = 'top' AND min_value = '15 days net'"
        )
    )
    conn.execute(
        sa.text(
            "DELETE FROM pld_scoring_rules "
            "WHERE criteria_type = 'prod_lia_ins' AND min_value IN ('2M€ or +', '1M€ or +')"
        )
    )
