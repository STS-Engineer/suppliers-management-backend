"""Add a "Requested" = 0 scoring rule for every remaining pld_scoring_rules
criteria_type (document/status requested but not yet received -- same
treatment as "None").

"top" and "prod_lia_ins" already got this in 20260707_0076; this migration
covers the rest: lta, productivity, quality_certification, competitiveness,
sqma, family_coverage, geo_coverage, cons_or_wd, financial_health.

Revision ID: 20260709_0080
Revises: 20260708_0079
Create Date: 2026-07-09
"""

from alembic import op
import sqlalchemy as sa

revision = "20260709_0080"
down_revision = "20260708_0079"
branch_labels = None
depends_on = None


_CRITERIA_TYPES = [
    "lta",
    "productivity",
    "quality_certification",
    "competitiveness",
    "sqma",
    "family_coverage",
    "geo_coverage",
    "cons_or_wd",
    "financial_health",
]


def upgrade() -> None:
    conn = op.get_bind()

    for criteria_type in _CRITERIA_TYPES:
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
            "WHERE min_value = 'Requested' AND criteria_type = ANY(:ctypes)"
        ),
        {"ctypes": _CRITERIA_TYPES},
    )
