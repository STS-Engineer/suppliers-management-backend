"""backfill line budget_status + STP annual run-rate (audit C2 / C3)

Two data corrections for already-existing rows. New rows are handled by the
service going forward; this aligns historical data so the KPI dashboard is
correct immediately rather than only after each opportunity is next saved.

C2 — financial_line.budget_status was set once at creation and never re-synced
     when the opportunity later derived to "Budgeted". Copy the opportunity's
     (already correctly derived) budget_status onto its Active/Completed lines.

C3 — for STP opportunities (Sourcing / Technical Productivity) the headline
     expected_annual_saving had been overwritten with the multi-year EBITDA
     Period. Reset it to the year-N run-rate (saving_year_n) — a true annual
     figure — on both the opportunity and the auto-created financial line whose
     value was copied from the period total. Component lines (own saving) and
     the monthly profile (per-year escalation) are intentionally left untouched.
     Rows where saving_year_n is NULL are skipped and self-heal on next save.

Revision ID: 20260617_0036
Revises: 20260617_0035
Create Date: 2026-06-17
"""

from alembic import op
import sqlalchemy as sa

revision = "20260617_0036"
down_revision = "20260617_0035"
branch_labels = None
depends_on = None

STP_TYPES = "('Sourcing', 'Technical Productivity')"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "opportunity" not in tables or "financial_line" not in tables:
        return

    # ── C2 — sync line.budget_status to the opportunity's derived status ──────
    op.execute(
        """
        UPDATE financial_line
        SET budget_status = (
            SELECT o.budget_status FROM opportunity o
            WHERE o.opportunity_id = financial_line.opportunity_id
        )
        WHERE status IN ('Active', 'Completed')
          AND EXISTS (
            SELECT 1 FROM opportunity o
            WHERE o.opportunity_id = financial_line.opportunity_id
          )
        """
    )

    # ── C3 — reset the conflated auto-line to the year-N annual run-rate ──────
    # (Run before the opportunity update; both reference unchanged columns, but
    #  ordering line-first keeps the equality test on period_saving unambiguous.)
    op.execute(
        f"""
        UPDATE financial_line
        SET expected_annual_saving = (
                SELECT o.saving_year_n FROM opportunity o
                WHERE o.opportunity_id = financial_line.opportunity_id
            ),
            budget_value = (
                SELECT o.saving_year_n FROM opportunity o
                WHERE o.opportunity_id = financial_line.opportunity_id
            )
        WHERE EXISTS (
            SELECT 1 FROM opportunity o
            WHERE o.opportunity_id = financial_line.opportunity_id
              AND o.opportunity_type IN {STP_TYPES}
              AND o.saving_year_n IS NOT NULL
              AND o.period_saving IS NOT NULL
              AND financial_line.expected_annual_saving = o.period_saving
        )
        """
    )

    # ── C3 — reset the opportunity headline to the year-N annual run-rate ─────
    op.execute(
        f"""
        UPDATE opportunity
        SET expected_annual_saving = saving_year_n
        WHERE opportunity_type IN {STP_TYPES}
          AND saving_year_n IS NOT NULL
          AND period_saving IS NOT NULL
          AND expected_annual_saving = period_saving
        """
    )


def downgrade() -> None:
    # Data-only migration — the prior (conflated) values cannot be reconstructed
    # without re-deriving from prices/quantities, so this is intentionally a no-op.
    pass
