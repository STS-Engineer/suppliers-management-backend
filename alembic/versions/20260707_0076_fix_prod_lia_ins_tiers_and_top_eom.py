"""Fix prod_lia_ins scoring tiers and a dead "top" scoring rule.

Changes:
  - prod_lia_ins: replace the ambiguous 2-tier $/€ mix (None=0, 1M=50, 2M=100)
    with the client-confirmed 4-tier scale: None=0, "500k€ or less"=30,
    "1M€ or less"=50, "1,5M€ or less"=80, "1,5M€ or more"=100. Old rows are
    deactivated (is_active=false), not deleted, to preserve any historical
    evaluation snapshot that referenced them.
  - top: "60 days end of month or +" was stored as the scoring key, but
    CRITERIA_VALUE_NORMALIZATION always normalizes input to "60 days eom
    or +" before lookup -- so that stored rule was never actually reachable.
    Add the canonical "60 days eom or +" = 100 row (same score).
  - top / prod_lia_ins: add a "Requested" = 0 rule for both (document
    requested but not yet received -- same treatment as "None").

Revision ID: 20260707_0076
Revises: 20260706_0075
Create Date: 2026-07-07
"""

from alembic import op
import sqlalchemy as sa

revision = "20260707_0076"
down_revision = "20260706_0075"
branch_labels = None
depends_on = None


_NEW_PROD_LIA_INS_ROWS = [
    ("None", 0),
    ("500k€ or less", 30),
    ("1M€ or less", 50),
    ("1,5M€ or less", 80),
    ("1,5M€ or more", 100),
]


def upgrade() -> None:
    conn = op.get_bind()

    # Deactivate the old ambiguous prod_lia_ins tiers ($/€ mix, only 2 non-zero tiers)
    conn.execute(
        sa.text(
            "UPDATE pld_scoring_rules SET is_active = false "
            "WHERE criteria_type = 'prod_lia_ins' "
            "AND min_value IN ('None', '1M$ or +', '2M$ or +', '1M€ or +', '2M€ or +')"
        )
    )

    for min_value, score in _NEW_PROD_LIA_INS_ROWS:
        conn.execute(
            sa.text(
                "INSERT INTO pld_scoring_rules "
                "(criteria_type, score, min_value, max_value, description, is_active) "
                "SELECT 'prod_lia_ins', :score, CAST(:val AS VARCHAR), CAST(:val AS VARCHAR), CAST(:descr AS VARCHAR), true "
                "WHERE NOT EXISTS ("
                "  SELECT 1 FROM pld_scoring_rules "
                "  WHERE criteria_type = 'prod_lia_ins' AND min_value = :val AND is_active = true"
                ")"
            ),
            {"score": score, "val": min_value, "descr": f"prod_lia_ins exact match score for {min_value}"},
        )

    # Fix the dead "60 days eom or +" top rule (canonical form after normalization)
    conn.execute(
        sa.text(
            "INSERT INTO pld_scoring_rules "
            "(criteria_type, score, min_value, max_value, description, is_active) "
            "SELECT 'top', 100, '60 days eom or +', '60 days eom or +', "
            "'top exact match score for 60 days eom or +', true "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM pld_scoring_rules "
            "  WHERE criteria_type = 'top' AND min_value = '60 days eom or +'"
            ")"
        )
    )

    # "Requested" scores as 0 (not yet provided), same as "None"
    for criteria_type in ("top", "prod_lia_ins"):
        conn.execute(
            sa.text(
                "INSERT INTO pld_scoring_rules "
                "(criteria_type, score, min_value, max_value, description, is_active) "
                "SELECT CAST(:ctype AS VARCHAR), 0, 'Requested', 'Requested', CAST(:descr AS VARCHAR), true "
                "WHERE NOT EXISTS ("
                "  SELECT 1 FROM pld_scoring_rules "
                "  WHERE criteria_type = :ctype AND min_value = 'Requested'"
                ")"
            ),
            {"ctype": criteria_type, "descr": f"{criteria_type} exact match score for Requested (not yet provided)"},
        )


def downgrade() -> None:
    conn = op.get_bind()

    conn.execute(
        sa.text(
            "DELETE FROM pld_scoring_rules "
            "WHERE criteria_type = 'prod_lia_ins' "
            "AND min_value IN ('500k€ or less', '1M€ or less', '1,5M€ or less', '1,5M€ or more')"
        )
    )
    conn.execute(
        sa.text(
            "UPDATE pld_scoring_rules SET is_active = true "
            "WHERE criteria_type = 'prod_lia_ins' "
            "AND min_value IN ('None', '1M$ or +', '2M$ or +', '1M€ or +', '2M€ or +')"
        )
    )
    conn.execute(
        sa.text(
            "DELETE FROM pld_scoring_rules "
            "WHERE criteria_type = 'top' AND min_value = '60 days eom or +'"
        )
    )
    conn.execute(
        sa.text(
            "DELETE FROM pld_scoring_rules "
            "WHERE min_value = 'Requested' AND criteria_type IN ('top', 'prod_lia_ins')"
        )
    )
