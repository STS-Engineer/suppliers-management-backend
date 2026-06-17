"""Fix option strings in pld_class_evaluation_input to match Monday.com canonical values.

The previous migration (0030) updated pld_scoring_rules, pld_class_criteria_detail,
and supplier_site_relation, but missed pld_class_evaluation_input — the table that
the evaluation workspace reads from. Data loaded via the API was stored with old
long-form strings because CRITERIA_VALUE_NORMALIZATION was mapping in the wrong direction.

Changes:
  - top:             "60 days end of month or +" → "60 days eom or +"
  - sqma:            "Signed M/Res/not sent" → "Signed M.Res/not sent"
  - family_coverage: long English → Monday short codes
  - cons_or_wd:      "Cons. Or Daily Deliveries" → "Cons. or WD"

Revision ID: 20260612_0031
Revises: 20260612_0030
Create Date: 2026-06-12
"""

from alembic import op
import sqlalchemy as sa

revision = "20260612_0031"
down_revision = "20260612_0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── TOP ──────────────────────────────────────────────────────────────────
    conn.execute(
        sa.text(
            "UPDATE pld_class_evaluation_input SET top = '60 days eom or +' "
            "WHERE top = '60 days end of month or +'"
        )
    )

    # ── SQMA ─────────────────────────────────────────────────────────────────
    conn.execute(
        sa.text(
            "UPDATE pld_class_evaluation_input SET sqma = 'Signed M.Res/not sent' "
            "WHERE sqma = 'Signed M/Res/not sent'"
        )
    )

    # ── FAMILY COVERAGE ──────────────────────────────────────────────────────
    _fc_renames = [
        ("Supplier can make all the family requirements", "100% Cov."),
        ("Supplier can make the main family requirements", "Main sub-Fam Cov."),
        ("Supplier can make only of few family requirements", "1 sub-F or refs Cov."),
        ("Supplier can make 1 family requirements", "1 ref"),
    ]
    for old, new in _fc_renames:
        conn.execute(
            sa.text(
                "UPDATE pld_class_evaluation_input SET family_coverage = :new "
                "WHERE family_coverage = :old"
            ),
            {"new": new, "old": old},
        )

    # ── CONS. OR WD ──────────────────────────────────────────────────────────
    conn.execute(
        sa.text(
            "UPDATE pld_class_evaluation_input SET cons_or_wd = 'Cons. or WD' "
            "WHERE cons_or_wd = 'Cons. Or Daily Deliveries'"
        )
    )
    # Normalize lowercase d Biweekly variant
    conn.execute(
        sa.text(
            "UPDATE pld_class_evaluation_input SET cons_or_wd = 'Biweekly Del.' "
            "WHERE cons_or_wd = 'Biweekly del.'"
        )
    )


def downgrade() -> None:
    conn = op.get_bind()

    conn.execute(
        sa.text(
            "UPDATE pld_class_evaluation_input SET top = '60 days end of month or +' "
            "WHERE top = '60 days eom or +'"
        )
    )
    conn.execute(
        sa.text(
            "UPDATE pld_class_evaluation_input SET sqma = 'Signed M/Res/not sent' "
            "WHERE sqma = 'Signed M.Res/not sent'"
        )
    )
    _fc_renames_rev = [
        ("100% Cov.", "Supplier can make all the family requirements"),
        ("Main sub-Fam Cov.", "Supplier can make the main family requirements"),
        ("1 sub-F or refs Cov.", "Supplier can make only of few family requirements"),
        ("1 ref", "Supplier can make 1 family requirements"),
    ]
    for old, new in _fc_renames_rev:
        conn.execute(
            sa.text(
                "UPDATE pld_class_evaluation_input SET family_coverage = :new "
                "WHERE family_coverage = :old"
            ),
            {"new": new, "old": old},
        )
    conn.execute(
        sa.text(
            "UPDATE pld_class_evaluation_input SET cons_or_wd = 'Cons. Or Daily Deliveries' "
            "WHERE cons_or_wd = 'Cons. or WD'"
        )
    )
    conn.execute(
        sa.text(
            "UPDATE pld_class_evaluation_input SET cons_or_wd = 'Biweekly del.' "
            "WHERE cons_or_wd = 'Biweekly Del.'"
        )
    )
