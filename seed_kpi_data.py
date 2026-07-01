"""
Seed script — KPI dashboard test data (FY 2026).
Run from the backend directory:  python seed_kpi_data.py

KEEPS:   avocarbon_site, user accounts, supplier/evaluation tables
CLEARS:  opportunity, financial_line, monthly_financial,
         opportunity_budget_year, project, gate_approval_request,
         gate_approval_vote, opportunity_phase_snapshot, opportunity_document
"""

import asyncio
import sys
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, ".")
from app.core.config import settings
from app.db.models import (
    FinancialLine,
    MonthlyFinancial,
    Opportunity,
    OpportunityBudgetYear,
    OpportunityPhaseSnapshot,
    Project,
)

engine = create_async_engine(settings.database_url, echo=False)
AsyncSess = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

TODAY = date(2026, 6, 19)
FY2026_START = date(2025, 12, 1)
FY2026_END_EXCL = date(2026, 12, 1)


def add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    return d.replace(year=d.year + m // 12, month=m % 12 + 1, day=1)


# ── Opportunity definitions ──────────────────────────────────────────────────
# (name, type, phase, buyer, site_id, annual_saving, start, duration_months,
#  budget_status, applicable_fraction, eoy_forecast)
OPPS = [
    # Buyer 1 — hayfa.rajhi
    ("Négociation Acier Inoxydable",       "Negotiation",           "Phase 3", "hayfa.rajhi@avocarbon.com",  1,  80_000, date(2026,  1, 1), 11, "Budgeted",    11/12,  84_000),
    ("Technical Productivity Ligne B",     "Technical Productivity","Phase 2", "hayfa.rajhi@avocarbon.com",  1,  20_000, date(2026,  4, 1),  8, "Empty",        8/12,  20_000),
    ("Sourcing Pièces Détachées",          "Sourcing",              "Phase 0", "hayfa.rajhi@avocarbon.com",  3,  18_000, date(2026,  9, 1),  3, "Empty",        3/12,  18_000),
    ("Négociation Emballages",             "Negotiation",           "Phase 2", "hayfa.rajhi@avocarbon.com",  3,  25_000, date(2026,  6, 1),  6, "Opportunity",  6/12,  25_000),

    # Buyer 2 — alice.martin
    ("Sourcing Composants Électroniques",  "Sourcing",              "Phase 3", "alice.martin@avocarbon.com", 2,  45_000, date(2026,  3, 1),  9, "Budgeted",     9/12,  42_000),
    ("Sourcing Polymères",                 "Sourcing",              "Phase 3", "alice.martin@avocarbon.com", 4,  60_000, date(2025, 12, 1), 12, "Budgeted",       1.0,  63_000),
    ("Sourcing Matières Premières",        "Sourcing",              "Phase 1", "alice.martin@avocarbon.com", 2,  35_000, date(2026,  7, 1),  5, "Empty",        5/12,  35_000),

    # Buyer 3 — pierre.dupont
    ("Négociation Transport",              "Negotiation",           "Phase 3", "pierre.dupont@avocarbon.com",5,  40_000, date(2026,  1, 1), 11, "Budgeted",    11/12,  38_000),
    ("Technical Productivity Ligne A",     "Technical Productivity","Phase 3", "pierre.dupont@avocarbon.com",6,  30_000, date(2026,  2, 1), 10, "Opportunity", 10/12,  28_000),
    ("Cash Optimization Programme",        "Cash",                  "Phase 3", "pierre.dupont@avocarbon.com",7,  15_000, date(2026,  1, 1), 11, "Opportunity",   1.0,  14_000),
]

# Realistic monthly actuals multiplier per buyer (Dec 2025 → May 2026)
# buyer → list of monthly multipliers (one per past month)
ACTUALS_PROFILE = {
    "hayfa.rajhi@avocarbon.com":   [0.92, 0.95, 1.05, 1.08, 0.88, 0.97],   # Dec→May
    "alice.martin@avocarbon.com":  [1.10, 1.08, 0.95, 0.99, 1.02, 1.06],
    "pierre.dupont@avocarbon.com": [0.78, 0.82, 0.85, 0.80, 0.88, 0.84],   # systematically below target
}


async def clear(db: AsyncSession) -> None:
    print("Clearing purchasing data …")
    for tbl in [
        "gate_approval_vote",
        "gate_approval_request",
        "monthly_financial",
        "opportunity_budget_year",
        "opportunity_phase_snapshot",
        "opportunity_document",
        "financial_line",
        "project",
        "opportunity",
    ]:
        try:
            await db.execute(text(f"DELETE FROM {tbl}"))
            print(f"  cleared {tbl}")
        except Exception as e:
            print(f"  skip {tbl}: {e}")
            await db.rollback()
    await db.commit()
    print("Done clearing.")


async def seed(db: AsyncSession) -> None:
    summary = []

    for (opp_name, opp_type, phase, buyer, site_id, annual,
         start, duration, budget_status, applic_frac, eoy_annual) in OPPS:

        # ── Opportunity ──────────────────────────────────────────────────
        real_start = start if start <= TODAY else None
        opp = Opportunity(
            opportunity_name=opp_name,
            opportunity_type=opp_type,
            phase_status=phase,
            status="Working on it",
            idea_owner=buyer,
            purchasing_owner=buyer,
            plant_id=site_id,
            expected_annual_saving=Decimal(str(annual)),
            planned_start_date=start,
            real_start_date=real_start,
            duration_months=Decimal(str(duration)),
            currency="EUR",
            validation_decision="Go" if phase not in ("Phase 0",) else None,
            is_deleted=False,
        )
        db.add(opp)
        await db.flush()

        # ── Budget year ──────────────────────────────────────────────────
        applic_amount = round(annual * applic_frac, 2)
        by = OpportunityBudgetYear(
            opportunity_id=opp.opportunity_id,
            fiscal_year=2026,
            budget_status=budget_status,
            applicable_amount=Decimal(str(applic_amount)),
            portion_kind="Total" if applic_frac >= 0.99 else "Applicable",
            suggested_status=budget_status,
        )
        db.add(by)

        # ── OpportunityPhaseSnapshot records ────────────────────────────────
        # Gate sequence: None→P0, P0→P1, P1→P2, P2→P3
        # Dates spread ~3 months apart going back from TODAY
        GATE_CHAIN = [
            (None,       "Phase 0"),
            ("Phase 0",  "Phase 1"),
            ("Phase 1",  "Phase 2"),
            ("Phase 2",  "Phase 3"),
        ]
        PHASE_DEPTH = {"Phase 0": 1, "Phase 1": 2, "Phase 2": 3, "Phase 3": 4}
        depth = PHASE_DEPTH.get(phase, 0)

        # Build per-type snapshot context
        LEVERS_BY_TYPE = {
            "Negotiation":           ["competitive bidding", "volume consolidation", "contract renegotiation"],
            "Sourcing":              ["dual sourcing", "make-vs-buy analysis", "supplier qualification"],
            "Technical Productivity":["process optimisation", "scrap reduction", "tooling improvement"],
            "Cash":                  ["payment term extension", "inventory reduction", "early payment discount"],
        }
        levers = LEVERS_BY_TYPE.get(opp_type, ["cost reduction"])

        COMMENTS_BY_TYPE = {
            "Negotiation":            "Benchmark effectué sur 3 fournisseurs; conditions de marché favorables.",
            "Sourcing":               "Analyse de marché complète; 2 nouveaux fournisseurs qualifiés.",
            "Technical Productivity": "Gains process validés sur ligne pilote; déploiement prévu.",
            "Cash":                   "Conditions de paiement renégociées; impact trésorerie positif confirmé.",
        }
        stp_comment = COMMENTS_BY_TYPE.get(opp_type, "Étude STP validée par le comité.")

        # Compute gate dates: most-recent gate ~2 months ago, each prior gate ~3 months earlier
        gate_dates = []
        for gi in range(depth):
            months_back = 2 + (depth - 1 - gi) * 3
            gate_month = TODAY.month - months_back
            gate_year = TODAY.year
            while gate_month < 1:
                gate_month += 12
                gate_year -= 1
            gate_dates.append(datetime(gate_year, gate_month, 15, 9, 0, 0))

        for gi in range(depth):
            phase_from, phase_to = GATE_CHAIN[gi]
            snap_dt = gate_dates[gi]
            snap_date_str = snap_dt.strftime("%Y-%m-%d")
            snap_dict = {
                "expected_annual_saving": annual,
                "opportunity_type":       opp_type,
                "buyer":                  buyer,
                "phase":                  phase_to,
                "stp_estimated_saving":   annual,
                "stp_study_date":         snap_date_str,
                "stp_study_scope":        f"Périmètre: {opp_name}",
                "stp_comment":            stp_comment,
                "market_analysis_done":   True,
                "savings_levers":         levers[:2] if gi == 0 else levers,
                "market_benchmark_pct":   round(88 + (gi * 5) + (annual % 10) * 0.3, 1),
            }
            snap = OpportunityPhaseSnapshot(
                opportunity_id=opp.opportunity_id,
                phase_from=phase_from,
                phase_to=phase_to,
                gate_decision="Go",
                decided_by=buyer,
                decided_at=snap_dt,
                gate_comments=stp_comment,
                opportunity_snapshot=snap_dict,
            )
            db.add(snap)

        # ── Financial line (Phase 2+ only) ───────────────────────────────
        if phase in ("Phase 0", "Phase 1"):
            summary.append((opp_name, buyer, budget_status, 0, 0, 0))
            continue

        fl = FinancialLine(
            opportunity_id=opp.opportunity_id,
            plant_id=site_id,
            status="Active",
            line_name=f"Ligne principale — {opp_name}",
            expected_annual_saving=Decimal(str(annual)),
            planned_start_date=start,
            real_start_date=real_start,
            duration_months=Decimal(str(duration)),
            follower=buyer,
            forecast_eoy_current=Decimal(str(eoy_annual)) if real_start else None,
        )
        db.add(fl)
        await db.flush()

        # ── Monthly rows ─────────────────────────────────────────────────
        monthly_exp = annual / duration
        profile = ACTUALS_PROFILE.get(buyer, [1.0] * 6)
        # Past months = Dec 2025 … May 2026 (6 months, index 0..5)
        past_months = [add_months(date(2025, 12, 1), i) for i in range(6)]

        total_actual = 0.0
        for i in range(duration):
            month = add_months(start, i)
            # Only months inside FY 2026
            if month < FY2026_START or month >= FY2026_END_EXCL:
                continue

            actual = None
            if month < TODAY.replace(day=1) and real_start and month >= real_start.replace(day=1):
                # Past month → apply profile multiplier
                idx = past_months.index(month) if month in past_months else -1
                mult = profile[idx] if idx >= 0 else profile[-1]
                actual = round(monthly_exp * mult, 2)
                total_actual += actual

            # EOY forecast on current month's row
            eoy_row = Decimal(str(eoy_annual)) if month == TODAY.replace(day=1) else None

            mf = MonthlyFinancial(
                financial_line_id=fl.financial_line_id,
                period_month=month,
                expected_saving=Decimal(str(round(monthly_exp, 2))),
                actual_saving=Decimal(str(actual)) if actual is not None else None,
                forecast_eoy_saving=eoy_row,
            )
            db.add(mf)

        # ── Project record (Phase 2 and Phase 3 only) ────────────────────
        proj = Project(
            opportunity_id=opp.opportunity_id,
            project_name=f"{opp_name} — Déploiement",
            project_owner=buyer,
            phase_status=phase,
            planned_end_date=add_months(start, duration),
            status="In Progress" if phase == "Phase 2" else "Completed",
            is_deleted=False,
        )
        db.add(proj)

        summary.append((opp_name, buyer, budget_status, annual, eoy_annual, round(total_actual, 0)))

    await db.commit()

    # ── Print expected KPI summary ────────────────────────────────────────
    print("\n" + "=" * 80)
    print("SEED COMPLETE -- Expected KPI Values (FY 2026)")
    print("=" * 80)
    print(f"{'Opportunity':<42} {'Buyer':<20} {'Status':<12} {'Expected':>10} {'EOY Fcst':>10} {'Actual YTD':>11}")
    print("-" * 80)

    total_exp = total_eoy = total_act = 0
    budgeted_exp = budgeted_eoy = 0
    total_budget = 0

    for row in summary:
        opp_name, buyer, status, annual, eoy, act = row
        short_buyer = buyer.split("@")[0]
        print(f"{opp_name[:40]:<42} {short_buyer:<20} {status:<12} {annual:>10,.0f} {eoy:>10,.0f} {act:>11,.0f}")
        total_exp += annual
        total_eoy += eoy
        total_act += act

    _, _, _, _, _, _ = summary[0]  # just ensure non-empty
    for row in summary:
        opp_name, buyer, status, annual, eoy, act = row
        for (n, t, p, b, si, ann, st, dur, bs, af, ef) in OPPS:
            if n == opp_name and bs == "Budgeted" and p not in ("Phase 0", "Phase 1"):
                budgeted_exp += annual
                budgeted_eoy += eoy
                total_budget  += round(ann * af, 2)
                break

    print("-" * 80)
    print(f"\nTotal Expected Annual (all active lines): {total_exp:,.0f} EUR")
    print(f"Total EOY Forecast    (all active lines): {total_eoy:,.0f} EUR")
    print(f"Total Actual YTD      (Dec 2025->May 2026): {total_act:,.0f} EUR")
    print(f"\nBudgeted Expected Annual:                 {budgeted_exp:,.0f} EUR")
    print(f"Budgeted EOY Forecast:                    {budgeted_eoy:,.0f} EUR")
    print(f"Total Budget (applicable_amount sum):     {total_budget:,.0f} EUR")
    print(f"\nEOY vs Budget %: {budgeted_eoy / budgeted_exp * 100:.1f}%  (target: ~100%)")
    print("=" * 80)


async def main() -> None:
    async with AsyncSess() as db:
        await clear(db)
        await seed(db)


if __name__ == "__main__":
    asyncio.run(main())
