"""Fix option strings in pld_scoring_rules to match Monday.com canonical values.

Changes:
  - top:             "60 days end of month or +" → "60 days eom or +"
  - sqma:            "Signed M/Res/not sent" → "Signed M.Res/not sent"
  - family_coverage: long English → Monday short codes, insert "None" (score 0)
  - cons_or_wd:      "Cons. Or Daily Deliveries" → "Cons. or WD",
                     "DDP or Weekly Del." score 50 → 0,
                     insert "Cons. or WD Inter. User" (score 50)

Updates three targets per change:
  1. pld_scoring_rules       (lookup table)
  2. pld_class_criteria_detail.selected_value (historical records)
  3. supplier_site_relation  (live supplier data)

Revision ID: 20260612_0030
Revises: 20260612_0029
Create Date: 2026-06-12
"""

from alembic import op
import sqlalchemy as sa

revision = "20260612_0030"
down_revision = "20260612_0029"
branch_labels = None
depends_on = None


def _relation_columns(conn) -> set:
    """Columns actually present on supplier_site_relation.

    The criteria columns (top/sqma/family_coverage/cons_or_wd) only exist on
    some environments — on others the live values are in pld_class_evaluation_input
    (handled by migration 0031). Guard so the UPDATE doesn't fail where absent.
    """
    inspector = sa.inspect(conn)
    if "supplier_site_relation" not in inspector.get_table_names():
        return set()
    return {c["name"] for c in inspector.get_columns("supplier_site_relation")}


def upgrade() -> None:
    conn = op.get_bind()
    rel_cols = _relation_columns(conn)

    # ── TOP ──────────────────────────────────────────────────────────────────
    for table, col in [
        ("pld_scoring_rules", "min_value"),
        ("pld_scoring_rules", "max_value"),
        ("pld_class_criteria_detail", "selected_value"),
    ]:
        conn.execute(
            sa.text(
                f"UPDATE {table} SET {col} = '60 days eom or +' "
                f"WHERE criteria_type = 'top' AND {col} = '60 days end of month or +'"
            )
        )
    # Live supplier data (column is named directly, no criteria_type)
    if "top" in rel_cols:
        conn.execute(
            sa.text(
                "UPDATE supplier_site_relation SET top = '60 days eom or +' "
                "WHERE top = '60 days end of month or +'"
            )
        )

    # ── SQMA ─────────────────────────────────────────────────────────────────
    for table, col in [
        ("pld_scoring_rules", "min_value"),
        ("pld_scoring_rules", "max_value"),
        ("pld_class_criteria_detail", "selected_value"),
    ]:
        conn.execute(
            sa.text(
                f"UPDATE {table} SET {col} = 'Signed M.Res/not sent' "
                f"WHERE criteria_type = 'sqma' AND {col} = 'Signed M/Res/not sent'"
            )
        )
    if "sqma" in rel_cols:
        conn.execute(
            sa.text(
                "UPDATE supplier_site_relation SET sqma = 'Signed M.Res/not sent' "
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
        for table, col in [
            ("pld_scoring_rules", "min_value"),
            ("pld_scoring_rules", "max_value"),
            ("pld_class_criteria_detail", "selected_value"),
        ]:
            conn.execute(
                sa.text(
                    f"UPDATE {table} SET {col} = :new "
                    f"WHERE criteria_type = 'family_coverage' AND {col} = :old"
                ),
                {"new": new, "old": old},
            )
        if "family_coverage" in rel_cols:
            conn.execute(
                sa.text(
                    "UPDATE supplier_site_relation SET family_coverage = :new "
                    "WHERE family_coverage = :old"
                ),
                {"new": new, "old": old},
            )

    # Insert "None" option (score 0) for family_coverage
    conn.execute(
        sa.text(
            "INSERT INTO pld_scoring_rules "
            "(criteria_type, score, min_value, max_value, description, is_active) "
            "SELECT 'family_coverage', 0, 'None', 'None', "
            "'family_coverage exact match score for None', true "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM pld_scoring_rules "
            "  WHERE criteria_type = 'family_coverage' AND min_value = 'None'"
            ")"
        )
    )

    # ── CONS. OR WD ──────────────────────────────────────────────────────────
    for table, col in [
        ("pld_scoring_rules", "min_value"),
        ("pld_scoring_rules", "max_value"),
        ("pld_class_criteria_detail", "selected_value"),
    ]:
        conn.execute(
            sa.text(
                f"UPDATE {table} SET {col} = 'Cons. or WD' "
                f"WHERE criteria_type = 'cons_or_wd' AND {col} = 'Cons. Or Daily Deliveries'"
            )
        )
    if "cons_or_wd" in rel_cols:
        conn.execute(
            sa.text(
                "UPDATE supplier_site_relation SET cons_or_wd = 'Cons. or WD' "
                "WHERE cons_or_wd = 'Cons. Or Daily Deliveries'"
            )
        )

    # Fix "DDP or Weekly Del." score from 50 → 0 (not in Monday formulas, treated as else → 0)
    conn.execute(
        sa.text(
            "UPDATE pld_scoring_rules SET score = 0 "
            "WHERE criteria_type = 'cons_or_wd' AND min_value = 'DDP or Weekly Del.'"
        )
    )

    # Add "Cons. or WD Inter. User" (score 50) — missing from original seed
    conn.execute(
        sa.text(
            "INSERT INTO pld_scoring_rules "
            "(criteria_type, score, min_value, max_value, description, is_active) "
            "SELECT 'cons_or_wd', 50, 'Cons. or WD Inter. User', 'Cons. or WD Inter. User', "
            "'cons_or_wd exact match score for Cons. or WD Inter. User', true "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM pld_scoring_rules "
            "  WHERE criteria_type = 'cons_or_wd' AND min_value = 'Cons. or WD Inter. User'"
            ")"
        )
    )

    # Remove "Other" from cons_or_wd — not a Monday value (score 0, same as any unmatched)
    conn.execute(
        sa.text(
            "DELETE FROM pld_scoring_rules "
            "WHERE criteria_type = 'cons_or_wd' AND min_value = 'Other'"
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    rel_cols = _relation_columns(conn)

    # Reverse TOP
    for table, col in [
        ("pld_scoring_rules", "min_value"),
        ("pld_scoring_rules", "max_value"),
        ("pld_class_criteria_detail", "selected_value"),
    ]:
        conn.execute(
            sa.text(
                f"UPDATE {table} SET {col} = '60 days end of month or +' "
                f"WHERE criteria_type = 'top' AND {col} = '60 days eom or +'"
            )
        )
    if "top" in rel_cols:
        conn.execute(
            sa.text(
                "UPDATE supplier_site_relation SET top = '60 days end of month or +' "
                "WHERE top = '60 days eom or +'"
            )
        )

    # Reverse SQMA
    for table, col in [
        ("pld_scoring_rules", "min_value"),
        ("pld_scoring_rules", "max_value"),
        ("pld_class_criteria_detail", "selected_value"),
    ]:
        conn.execute(
            sa.text(
                f"UPDATE {table} SET {col} = 'Signed M/Res/not sent' "
                f"WHERE criteria_type = 'sqma' AND {col} = 'Signed M.Res/not sent'"
            )
        )
    if "sqma" in rel_cols:
        conn.execute(
            sa.text(
                "UPDATE supplier_site_relation SET sqma = 'Signed M/Res/not sent' "
                "WHERE sqma = 'Signed M.Res/not sent'"
            )
        )

    # Reverse family_coverage
    _fc_renames_rev = [
        ("100% Cov.", "Supplier can make all the family requirements"),
        ("Main sub-Fam Cov.", "Supplier can make the main family requirements"),
        ("1 sub-F or refs Cov.", "Supplier can make only of few family requirements"),
        ("1 ref", "Supplier can make 1 family requirements"),
    ]
    for old, new in _fc_renames_rev:
        for table, col in [
            ("pld_scoring_rules", "min_value"),
            ("pld_scoring_rules", "max_value"),
            ("pld_class_criteria_detail", "selected_value"),
        ]:
            conn.execute(
                sa.text(
                    f"UPDATE {table} SET {col} = :new "
                    f"WHERE criteria_type = 'family_coverage' AND {col} = :old"
                ),
                {"new": new, "old": old},
            )
        if "family_coverage" in rel_cols:
            conn.execute(
                sa.text(
                    "UPDATE supplier_site_relation SET family_coverage = :new "
                    "WHERE family_coverage = :old"
                ),
                {"new": new, "old": old},
            )
    conn.execute(
        sa.text(
            "DELETE FROM pld_scoring_rules "
            "WHERE criteria_type = 'family_coverage' AND min_value = 'None'"
        )
    )

    # Reverse cons_or_wd
    for table, col in [
        ("pld_scoring_rules", "min_value"),
        ("pld_scoring_rules", "max_value"),
        ("pld_class_criteria_detail", "selected_value"),
    ]:
        conn.execute(
            sa.text(
                f"UPDATE {table} SET {col} = 'Cons. Or Daily Deliveries' "
                f"WHERE criteria_type = 'cons_or_wd' AND {col} = 'Cons. or WD'"
            )
        )
    if "cons_or_wd" in rel_cols:
        conn.execute(
            sa.text(
                "UPDATE supplier_site_relation SET cons_or_wd = 'Cons. Or Daily Deliveries' "
                "WHERE cons_or_wd = 'Cons. or WD'"
            )
        )
    conn.execute(
        sa.text(
            "UPDATE pld_scoring_rules SET score = 50 "
            "WHERE criteria_type = 'cons_or_wd' AND min_value = 'DDP or Weekly Del.'"
        )
    )
    conn.execute(
        sa.text(
            "DELETE FROM pld_scoring_rules "
            "WHERE criteria_type = 'cons_or_wd' AND min_value = 'Cons. or WD Inter. User'"
        )
    )
    conn.execute(
        sa.text(
            "INSERT INTO pld_scoring_rules "
            "(criteria_type, score, min_value, max_value, description, is_active) "
            "VALUES ('cons_or_wd', 0, 'Other', 'Other', "
            "'cons_or_wd exact match score for Other', true)"
        )
    )
