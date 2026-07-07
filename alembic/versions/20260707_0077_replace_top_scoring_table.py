"""Replace the "top" (terms of payment) scoring table with the client's
full canonical 29-tier table (top_insurance_list.xlsx, sheet TOP).

This supersedes the partial 6-row table from migrations 20260520_0003 and
20260611_0027 -- notably "15 days net" changes from 10 to 0 per the
authoritative source. Old rows are deactivated (is_active=false), not
deleted, to preserve any historical evaluation snapshot that referenced them.

Revision ID: 20260707_0077
Revises: 20260707_0076
Create Date: 2026-07-07
"""

from alembic import op
import sqlalchemy as sa

revision = "20260707_0077"
down_revision = "20260707_0076"
branch_labels = None
depends_on = None


_TOP_ROWS = [
    ("Cash at order", 0),
    ("Cash in advance", 0),
    ("0 days net", 0),
    ("0 days end of month", 0),
    ("0 days end of month the 15", 30),
    ("15 days net", 0),
    ("15 days end of month", 30),
    ("15 days end of month the 15", 50),
    ("30 days net", 30),
    ("30 days end of month", 50),
    ("30 days end of month the 15", 80),
    ("45 days net", 50),
    ("45 days end of month", 80),
    ("45 days end of month the 15", 100),
    ("60 days net", 80),
    ("60 days end of month", 100),
    ("60 days end of month the 15", 100),
    ("75 days net", 100),
    ("75 days end of month", 100),
    ("75 days end of month the 15", 100),
    ("90 days net", 100),
    ("90 days end of month", 100),
    ("90 days end of month the 15", 100),
    ("105 days net", 100),
    ("105 days end of month", 100),
    ("105 days end of month the 15", 100),
    ("120 days net", 100),
    ("120 days end of month", 100),
    ("120 days end of month the 15", 100),
]


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(
        sa.text(
            "UPDATE pld_scoring_rules SET is_active = false "
            "WHERE criteria_type = 'top' AND min_value <> 'Requested'"
        )
    )

    for min_value, score in _TOP_ROWS:
        conn.execute(
            sa.text(
                "INSERT INTO pld_scoring_rules "
                "(criteria_type, score, min_value, max_value, description, is_active) "
                "SELECT 'top', :score, CAST(:val AS VARCHAR), CAST(:val AS VARCHAR), CAST(:descr AS VARCHAR), true "
                "WHERE NOT EXISTS ("
                "  SELECT 1 FROM pld_scoring_rules "
                "  WHERE criteria_type = 'top' AND min_value = :val AND is_active = true"
                ")"
            ),
            {"score": score, "val": min_value, "descr": f"top exact match score for {min_value}"},
        )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "DELETE FROM pld_scoring_rules WHERE criteria_type = 'top' "
            "AND min_value IN (" + ",".join(f"'{v[0]}'" for v in _TOP_ROWS) + ")"
        )
    )
    conn.execute(
        sa.text(
            "UPDATE pld_scoring_rules SET is_active = true "
            "WHERE criteria_type = 'top' AND min_value <> 'Requested'"
        )
    )
