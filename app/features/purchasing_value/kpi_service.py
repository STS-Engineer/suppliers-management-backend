"""KPI computation service for the purchasing value dashboard.

Key formulas (per Olivier GRIMAUD transcript 2026-06-03):
- Expected savings spread evenly across the active months: annual / duration_months
  (each month equal; partial months are NOT day-prorated — periods land on day 1).
- Alerts only from planned_start_date onwards (months before = expected 0)
- Budget status is per budget_year, resetable annually
- All consolidated figures are converted to EUR (group reporting currency) using the
  opportunity's fx_rate_to_eur before being summed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import FinancialLine, GateApprovalRequest, Opportunity, Project
from app.features.purchasing_value.schemas import budget_year_bounds, budget_year_for_date


def _n(v) -> float:
    if v is None:
        return 0.0
    return float(v)


def _pct(num: float, denom: float) -> Optional[float]:
    if denom == 0:
        return None
    return round((num / denom) * 100, 1)


@dataclass
class KpiFilters:
    """Multi-dimensional filter for the KPI dashboard.

    All lists default to empty = "no filter" (= include everything).
    """
    year: Optional[int] = None
    plant_ids: list[int] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    buyer_emails: list[str] = field(default_factory=list)


class PurchasingKpiService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def compute_all(self, filters: Optional[KpiFilters] = None) -> dict:
        # Terminal / inactive phases — opportunities in these states are no longer
        # contributing to the live pipeline and must be excluded from all forward-looking
        # KPIs (program value lifetime, open pipeline count, validated opps, etc.).
        # "Closed" = deliberately terminated (No Go or Phase 4 complete).
        # "Stuck" = blocked with no progress — still technically open but not actionable.
        INACTIVE_PHASES: frozenset = frozenset({"Closed", "Stuck", "Cancelled"})

        today = date.today()
        active_budget_year = budget_year_for_date(today)
        _f = filters or KpiFilters()
        current_year = _f.year or active_budget_year
        budget_start, budget_end_exclusive = budget_year_bounds(current_year)
        budget_end_inclusive = budget_end_exclusive - timedelta(days=1)
        if current_year < active_budget_year:
            ytd_cutoff = budget_end_inclusive
        elif current_year == active_budget_year:
            ytd_cutoff = min(today, budget_end_inclusive)
        else:
            # Future fiscal year: budget_start is ahead of today, so no rows can
            # satisfy both _in_budget_year() and period_month <= cutoff.
            # Use day-before-FY-start to make the intent explicit.
            ytd_cutoff = budget_start - timedelta(days=1)
        current_month_start = today.replace(day=1)

        def _in_budget_year(period: date) -> bool:
            return budget_start <= period < budget_end_exclusive

        def _add_months(d: date, months: int) -> date:
            """Add months to a date without external deps."""
            m = d.month - 1 + months
            return d.replace(year=d.year + m // 12, month=m % 12 + 1, day=1)

        def _line_overlaps_fy(line) -> bool:
            """True if the financial line's active period overlaps the selected fiscal year."""
            start = line.real_start_date or line.planned_start_date
            if start is None:
                return True  # no date info → always include
            line_start = start.replace(day=1)
            if line_start >= budget_end_exclusive:
                return False  # line starts after this FY ends
            if line.duration_months:
                line_end = _add_months(line_start, int(line.duration_months))
                if line_end <= budget_start:
                    return False  # line ended before this FY started
            return True

        # ── Load data ────────────────────────────────────────────────────
        lines_result = await self.db.execute(
            select(FinancialLine)
            .where(
                FinancialLine.status.in_(["Active", "Completed"]),
                FinancialLine.is_deleted.is_(False),
            )
            .options(
                selectinload(FinancialLine.monthly_financials),
                selectinload(FinancialLine.opportunity),
                selectinload(FinancialLine.plant),
            )
        )
        all_lines: list[FinancialLine] = list(lines_result.scalars().all())
        # Strip soft-deleted monthly rows (selectinload bypasses SQL-level filters)
        for _line in all_lines:
            if _line.monthly_financials:
                _line.monthly_financials = [
                    m for m in _line.monthly_financials if not m.is_deleted
                ]
        # KPI scope: Active AND Completed lines whose period overlaps the selected FY.
        # Completed lines continue to generate realized savings after project closure
        # and must be included for KPI totals, monthly chart, by_plant, and by_type
        # to tell a consistent story.
        kpi_lines = [
            line for line in all_lines
            if line.status in ("Active", "Completed") and _line_overlaps_fy(line)
        ]
        # Keep active_lines (Active-only) for alert detection (missing updates, escalation)
        # where Completed lines are no longer actionable.
        active_lines = [line for line in kpi_lines if line.status == "Active"]

        opps_result = await self.db.execute(
            select(Opportunity)
            .where(Opportunity.is_deleted.is_(False))
            .options(
                selectinload(Opportunity.plant),
                selectinload(Opportunity.budget_years),
            )
        )
        all_opps: list[Opportunity] = list(opps_result.scalars().all())

        projs_result = await self.db.execute(
            select(Project).where(Project.is_deleted.is_(False))
        )
        all_projects: list[Project] = list(projs_result.scalars().all())

        gate_result = await self.db.execute(
            select(GateApprovalRequest).where(
                GateApprovalRequest.is_deleted.is_(False),
                GateApprovalRequest.status == "Completed",
                GateApprovalRequest.consensus_result.in_(["Go", "No Go", "Review"]),
            )
        )
        all_gate_requests: list[GateApprovalRequest] = list(gate_result.scalars().all())

        # lines_by_opp: all lines (for conversion-rate check — needs lifetime actuals)
        # kpi_lines_by_opp: FY-scoped lines (for by_type financial figures)
        lines_by_opp: dict[int, list[FinancialLine]] = {}
        for line in all_lines:
            lines_by_opp.setdefault(line.opportunity_id, []).append(line)
        kpi_lines_by_opp: dict[int, list[FinancialLine]] = {}
        for line in kpi_lines:
            kpi_lines_by_opp.setdefault(line.opportunity_id, []).append(line)

        # ── Currency → EUR (group reporting currency) ─────────────────────
        # Opportunities may be booked in EUR/USD/RMB/INR; every consolidated figure
        # converts to EUR via the opportunity's fx_rate_to_eur. The rate lives on the
        # opportunity, so it is mapped onto each line for row-level (flattened) sums.
        def _rate(line) -> float:
            opp = line.opportunity
            if opp is None:
                return 1.0
            # EUR is the reporting currency → always 1, regardless of any stale stored
            # rate (defensive: heals bad data without needing a migration).
            if (opp.currency or "EUR") == "EUR":
                return 1.0
            # Non-EUR with no usable rate: return 0.0 so the line contributes nothing
            # to EUR totals rather than being silently converted at 1:1.  The caller
            # sees the line in non_eur_missing_rate so the exclusion is transparent.
            r = float(opp.fx_rate_to_eur) if opp.fx_rate_to_eur else 0.0
            return r if r > 0 else 0.0

        rate_by_line: dict[int, float] = {
            line.financial_line_id: _rate(line) for line in all_lines
        }

        # EOY forecast falls back to the expected baseline when no manual forecast has
        # been entered yet (a line is not "forecasting zero" — it is forecasting plan).
        def _eoy(line) -> float:
            if line.forecast_eoy_current is not None:
                return _n(line.forecast_eoy_current)
            return _n(line.expected_annual_saving)

        # Data-quality: non-EUR lines with no usable rate are excluded from EUR totals
        # (0-rated by _rate).  Surface the count so Finance knows how much pipeline
        # is missing from the consolidated figures.
        non_eur_missing_rate = len(
            [
                line
                for line in all_lines
                if line.opportunity
                and (line.opportunity.currency or "EUR") != "EUR"
                and not (
                    line.opportunity.fx_rate_to_eur
                    and float(line.opportunity.fx_rate_to_eur) > 0
                )
            ]
        )

        # ── Committed budget (source of truth = per-fiscal-year decision) ──
        # "Budgeted" means the director committed the opportunity to THIS year's budget
        # via Create Budget (OpportunityBudgetYear.budget_status == "Budgeted" for the
        # selected fiscal year) — NOT the execution-maturity flag. The budget amount is
        # the per-year applicable portion (EUR), matching the Budgeting page exactly.
        def _opp_rate(o) -> float:
            if o is None or (o.currency or "EUR") == "EUR":
                return 1.0
            r = float(o.fx_rate_to_eur) if o.fx_rate_to_eur else 0.0
            return r if r > 0 else 0.0

        committed_budget_by_opp: dict[int, float] = {}
        # Full budget status per opp for the current FY (used in EOY-by-status breakdown)
        budget_status_by_opp: dict[int, str] = {}
        # Delta reason per committed opp (for "by reason" stacked chart in by_plant)
        delta_reason_by_opp: dict[int, list] = {}
        for o in all_opps:
            # Closed/Stuck opps are no longer active — their budget commitment must not
            # inflate total_budget, budgeted_expected_annual, or eoy_vs_budget_pct.
            # budget_status_by_opp still records their status for the EOY-by-status chart
            # (which visualises the historical breakdown), but they are excluded from all
            # financial aggregates that drive KPI comparisons.
            is_inactive = o.phase_status in INACTIVE_PHASES or o.phase_status is None
            for by in (o.budget_years or []):
                if by.fiscal_year == current_year:
                    status = by.budget_status or "Empty"
                    budget_status_by_opp[o.opportunity_id] = status
                    if status == "Budgeted" and not is_inactive:
                        committed_budget_by_opp[o.opportunity_id] = (
                            _n(by.applicable_amount) * _opp_rate(o)
                        )
                        if by.delta_reason:
                            delta_reason_by_opp[o.opportunity_id] = list(by.delta_reason)
        committed_opp_ids = set(committed_budget_by_opp)

        # "Opportunity" = validated (Go decision) but not yet committed to budget this FY.
        # These contribute to total savings but are not in the committed budget denominator.
        opportunity_pipeline_by_opp: dict[int, float] = {}
        for o in all_opps:
            # Exclude Closed and Stuck — a Closed opp is terminated, a Stuck opp is
            # not progressing. Neither should inflate the "pipeline to commit" total.
            if o.phase_status in INACTIVE_PHASES or o.phase_status is None:
                continue
            for by in (o.budget_years or []):
                if by.fiscal_year == current_year and (by.budget_status or "") == "Opportunity":
                    opportunity_pipeline_by_opp[o.opportunity_id] = (
                        _n(by.applicable_amount) * _opp_rate(o)
                    )
        opportunity_opp_ids = set(opportunity_pipeline_by_opp)

        # ── Available filter options (full dataset, before any filter) ────
        _plants_seen: dict[int, str] = {}
        for ln in kpi_lines:
            if ln.plant_id and ln.plant:
                _plants_seen[ln.plant_id] = ln.plant.site_name
        _available_filters = {
            "plants": [
                {"id": k, "name": v}
                for k, v in sorted(_plants_seen.items(), key=lambda x: x[1])
            ],
            "categories": sorted({o.opportunity_type for o in all_opps if o.opportunity_type}),
            "buyers": sorted({o.idea_owner for o in all_opps if o.idea_owner}),
        }

        # ── Apply multi-dimensional filters (reassign working sets) ───────
        _plant_set = set(_f.plant_ids)
        _cat_set   = set(_f.categories)
        _buyer_set = set(_f.buyer_emails)

        if _plant_set or _cat_set or _buyer_set:
            kpi_lines = [
                ln for ln in kpi_lines
                if (not _plant_set or ln.plant_id in _plant_set)
                and (not _cat_set   or (ln.opportunity and ln.opportunity.opportunity_type in _cat_set))
                and (not _buyer_set or (ln.opportunity and ln.opportunity.idea_owner in _buyer_set))
            ]
            active_lines = [line for line in kpi_lines if line.status == "Active"]
            all_opps = [
                o for o in all_opps
                if (not _cat_set   or (o.opportunity_type or "") in _cat_set)
                and (not _buyer_set or (o.idea_owner or "") in _buyer_set)
            ]

        # ── FORECAST KPIs ─────────────────────────────────────────────────

        total_eoy_forecast = sum(_eoy(line) * _rate(line) for line in kpi_lines)
        # Budgeted lines = lines whose opportunity is committed "Budgeted" this year.
        budgeted_lines = [
            line for line in kpi_lines if line.opportunity_id in committed_opp_ids
        ]
        # Total budget = the committed per-year applicable amount (not line baselines).
        total_budget = sum(committed_budget_by_opp.values())
        total_expected = sum(_n(line.expected_annual_saving) * _rate(line) for line in kpi_lines)
        budgeted_expected_annual = sum(
            _n(line.expected_annual_saving) * _rate(line) for line in budgeted_lines
        )

        budgeted_eoy_forecast = sum(_eoy(line) * _rate(line) for line in budgeted_lines)

        # Forecast Outperformance: a committed line whose EOY forecast (EUR) exceeds its
        # expected annual saving. Both figures are full-year annuals → comparable units.
        # (applicable_amount is FY pro-rata and must NOT be the comparator here.)
        over_budget_lines = [
            line
            for line in budgeted_lines
            if _n(line.expected_annual_saving) * _rate(line) > 0
            and _eoy(line) * _rate(line) > _n(line.expected_annual_saving) * _rate(line)
        ]
        over_budget_count = len(over_budget_lines)
        over_budget_amount = sum(
            _eoy(line) * _rate(line) - _n(line.expected_annual_saving) * _rate(line)
            for line in over_budget_lines
        )

        # EOY vs Budget: both figures must be full-year annuals to be comparable —
        # total_budget is FY pro-rata (applicable_amount) and must NOT be the
        # denominator here (same reasoning as the Forecast Outperformance check
        # above, and service.py's delta_eoy_budget for the Budgeting page).
        eoy_vs_budget_pct = _pct(budgeted_eoy_forecast, budgeted_expected_annual)
        eoy_vs_expected_pct = _pct(total_eoy_forecast, total_expected)

        # Forecast drift: latest forecast_eoy_saving vs previous month's
        # Use the last two months with a non-null forecast_eoy_saving
        all_monthly_with_forecast = sorted(
            [
                (
                    r.period_month,
                    _n(r.forecast_eoy_saving) * rate_by_line.get(r.financial_line_id, 0.0),
                )
                for line in kpi_lines
                for r in line.monthly_financials
                if r.forecast_eoy_saving is not None
                and r.period_month
                and _in_budget_year(r.period_month)
            ],
            key=lambda x: x[0],
            reverse=True,
        )
        forecast_drift: Optional[float] = None
        if len(all_monthly_with_forecast) >= 2:
            latest_period = all_monthly_with_forecast[0][0]
            latest_sum = sum(
                v for (pm, v) in all_monthly_with_forecast if pm == latest_period
            )
            prev_period = None
            for (pm, v) in all_monthly_with_forecast:
                if pm != latest_period:
                    prev_period = pm
                    break
            if prev_period:
                prev_sum = sum(
                    v for (pm, v) in all_monthly_with_forecast if pm == prev_period
                )
                forecast_drift = round(latest_sum - prev_sum, 2)

        # ── EFFECTIVENESS KPIs ────────────────────────────────────────────

        validated_opps = [
            o
            for o in all_opps
            if o.validation_decision == "Go"
        ]
        converted_opps = [
            o
            for o in validated_opps
            if any(
                _n(line.cumulated_real_saving) > 0
                for line in lines_by_opp.get(o.opportunity_id, [])
            )
        ]
        conversion_rate_pct = _pct(len(converted_opps), len(validated_opps))

        # YTD rows = in selected year, not in the future, and AFTER savings start date
        # Olivier: months before planned_start_date are expected 0 — don't count them in KPIs
        def ytd_rows_for(lines):
            rows = []
            for line in lines:
                savings_start = line.real_start_date or line.planned_start_date
                for r in line.monthly_financials:
                    if not r.period_month:
                        continue
                    if not _in_budget_year(r.period_month):
                        continue
                    if r.period_month > ytd_cutoff:
                        continue
                    # Only count months from savings start date
                    if savings_start and r.period_month < savings_start.replace(day=1):
                        continue
                    rows.append(r)
            return rows

        ytd_rows = ytd_rows_for(kpi_lines)
        actual_ytd = sum(
            _n(r.actual_saving) * rate_by_line.get(r.financial_line_id, 0.0)
            for r in ytd_rows
            if r.actual_saving is not None
        )
        expected_ytd = sum(
            _n(r.expected_saving) * rate_by_line.get(r.financial_line_id, 0.0)
            for r in ytd_rows
        )

        budgeted_ytd_rows = ytd_rows_for(
            [line for line in kpi_lines if line.opportunity_id in committed_opp_ids]
        )
        budget_actual_ytd = sum(
            _n(r.actual_saving) * rate_by_line.get(r.financial_line_id, 0.0)
            for r in budgeted_ytd_rows
            if r.actual_saving is not None
        )
        budget_expected_ytd = sum(
            _n(r.expected_saving) * rate_by_line.get(r.financial_line_id, 0.0)
            for r in budgeted_ytd_rows
        )

        actual_vs_expected_ytd_pct = _pct(actual_ytd, expected_ytd)
        actual_vs_budget_ytd_pct = _pct(budget_actual_ytd, budget_expected_ytd)

        # Fixed-date KPI: total actual savings since Jan 1 2026 (calendar-year reference from Monday board).
        # Uses all_lines (not kpi_lines) — FY-agnostic, covers any line with actuals on/after that date.
        _jan_2026 = date(2026, 1, 1)
        total_saving_from_jan2026 = round(
            sum(
                _n(r.actual_saving) * rate_by_line.get(r.financial_line_id, 0.0)
                for line in all_lines
                for r in line.monthly_financials
                if r.actual_saving is not None
                and r.period_month
                and r.period_month >= _jan_2026
                and r.period_month <= ytd_cutoff
            ),
            2,
        )

        # ── EFFICIENCY KPIs ───────────────────────────────────────────────

        # Per-phase gate go rates — sourced from completed GateApprovalRequest decisions.
        # Phase 0 = initial validation gate; Phase 1/2/3 = subsequent progression gates.
        _GATE_PHASES = ["Phase 0", "Phase 1", "Phase 2", "Phase 3"]
        gate_go_rates: list[dict] = []
        for _phase in _GATE_PHASES:
            _decided = [r for r in all_gate_requests if r.phase_from == _phase]
            _go = [r for r in _decided if r.consensus_result == "Go"]
            _no_go = [r for r in _decided if r.consensus_result == "No Go"]
            gate_go_rates.append({
                "phase": _phase,
                "decided": len(_decided),
                "go": len(_go),
                "no_go": len(_no_go),
                "rate": _pct(len(_go), len(_decided)),
            })

        # Backward-compat scalar kept in kpis dict
        _p0 = gate_go_rates[0]
        phase0_go_rate_pct = _p0["rate"]
        phase0_decided = [r for r in all_gate_requests if r.phase_from == "Phase 0"]
        phase0_go = [r for r in phase0_decided if r.consensus_result == "Go"]

        active_projects = [
            p for p in all_projects if p.status in ("On time", "Late", "On hold")
        ]
        on_time_projects = [p for p in active_projects if p.status == "On time"]
        project_on_time_rate_pct = _pct(len(on_time_projects), len(active_projects))

        # Reference month for update coverage:
        #   past FY  → last month of that FY
        #   current  → previous closed month (current month is in progress, not yet closeable)
        #   future   → first month of that FY (no data yet → coverage = 0%)
        if current_year < active_budget_year:
            _update_ref_month = budget_end_inclusive.replace(day=1)
        elif current_year == active_budget_year:
            _update_ref_month = (current_month_start - timedelta(days=1)).replace(day=1)
        else:
            _update_ref_month = budget_start
        lines_with_current_update = [
            line
            for line in active_lines  # active_lines only — matches the denominator; Completed lines need no further updates
            if any(
                r.period_month
                and r.period_month.year == _update_ref_month.year
                and r.period_month.month == _update_ref_month.month
                and r.actual_saving is not None
                for r in line.monthly_financials
            )
        ]
        monthly_update_pct = _pct(len(lines_with_current_update), len(active_lines))

        scored_opps = [
            o
            for o in all_opps
            if o.priority_score and o.phase_status not in INACTIVE_PHASES and o.phase_status is not None
        ]
        avg_priority = (
            round(sum(_n(o.priority_score) for o in scored_opps) / len(scored_opps), 1)
            if scored_opps
            else None
        )

        # ── MONTHLY SAVINGS (for chart) ───────────────────────────────────
        # expected_saving uses equal distribution: annual / duration_months (not days-based).

        monthly_map: dict[str, dict] = {}
        for line in kpi_lines:
            savings_start = line.real_start_date or line.planned_start_date
            for row in line.monthly_financials:
                if not row.period_month:
                    continue
                if not _in_budget_year(row.period_month):
                    continue
                # Skip months before savings start (expected = 0 per Olivier)
                if savings_start and row.period_month < savings_start.replace(day=1):
                    continue
                key = row.period_month.strftime("%Y-%m")
                if key not in monthly_map:
                    monthly_map[key] = {
                        "period": key,
                        "expected": 0.0,
                        "actual": 0.0,
                        "budget": 0.0,
                        "eoy_forecast": 0.0,
                    }
                monthly_map[key]["expected"] += _n(row.expected_saving) * _rate(line)
                if row.actual_saving is not None:
                    monthly_map[key]["actual"] += _n(row.actual_saving) * _rate(line)
                if line.opportunity_id in committed_opp_ids:
                    monthly_map[key]["budget"] += _n(row.expected_saving) * _rate(line)
                if row.forecast_eoy_saving is not None:
                    monthly_map[key]["eoy_forecast"] += (
                        _n(row.forecast_eoy_saving) * _rate(line)
                    )

        monthly_actuals = sorted(monthly_map.values(), key=lambda x: x["period"])

        # ── YEAR-SPLIT (equal monthly distribution) ───────────────────────
        # For each financial line, split expected annual saving across calendar years
        # by booking annual/duration to each active month (not day-prorated)
        year_split_map: dict[int, dict] = {}
        for line in kpi_lines:
            if not line.planned_start_date or not line.expected_annual_saving:
                continue
            start = line.real_start_date or line.planned_start_date
            duration = int(line.duration_months or 12)
            annual = _n(line.expected_annual_saving) * _rate(line)
            # Monthly expected = annual / duration (consistent with service formula)
            # Handles one-shot (duration=1: full amount in single month) and recurring
            monthly_exp = annual / duration if duration > 0 else 0.0
            for i in range(duration):
                from app.features.purchasing_value.schemas import (
                    add_months as _add_months,
                )

                period = _add_months(start, i)
                yr = period.year
                if yr not in year_split_map:
                    year_split_map[yr] = {"year": yr, "expected": 0.0, "actual": 0.0}
                year_split_map[yr]["expected"] += monthly_exp
            # Add actuals by year
            for row in line.monthly_financials:
                if row.period_month and row.actual_saving is not None:
                    yr = row.period_month.year
                    if yr not in year_split_map:
                        year_split_map[yr] = {
                            "year": yr,
                            "expected": 0.0,
                            "actual": 0.0,
                        }
                    year_split_map[yr]["actual"] += _n(row.actual_saving) * _rate(line)

        year_split = [
            {
                **d,
                "expected": round(d["expected"], 2),
                "actual": round(d["actual"], 2),
                "ytd_rate_pct": _pct(d["actual"], d["expected"]),
            }
            for d in sorted(year_split_map.values(), key=lambda x: x["year"])
        ]

        # ── BY PLANT ──────────────────────────────────────────────────────

        plant_map: dict[int, dict] = {}
        for line in kpi_lines:
            pid = line.plant_id
            if pid is None:
                continue
            plant_name = line.plant.site_name if line.plant else f"Plant {pid}"
            if pid not in plant_map:
                plant_map[pid] = {
                    "plant_id": pid,
                    "plant_name": plant_name,
                    "expected_annual": 0.0,
                    "budget_value": 0.0,
                    "actual_ytd": 0.0,
                    "expected_ytd": 0.0,
                    "eoy_forecast": 0.0,
                    "budgeted_eoy": 0.0,
                    "budgeted_expected_annual": 0.0,
                    # EOY breakdown by budget status for the bar chart
                    "eoy_by_status": {"Budgeted": 0.0, "Opportunity": 0.0, "Empty": 0.0},
                    "opp_count": set(),
                    "type_breakdown": {},
                    # EOY and budget breakdown by opportunity type (for grouped/stacked bar charts)
                    "eoy_by_type": {},
                    "budgeted_eoy_by_type": {},
                    "budgeted_expected_annual_by_type": {},
                    # EOY and budget breakdown by delta_reason (for "main reason" stacked chart)
                    "budgeted_eoy_by_reason": {},
                    "budgeted_expected_annual_by_reason": {},
                }
            plant_map[pid]["expected_annual"] += _n(line.expected_annual_saving) * _rate(line)
            # "budget_value" = committed FY pro-rata (for informational display only,
            # NOT used as the denominator for eoy_vs_budget_pct — see comment below).
            plant_map[pid]["budget_value"] += committed_budget_by_opp.get(
                line.opportunity_id, 0.0
            )
            plant_map[pid]["eoy_forecast"] += _eoy(line) * _rate(line)
            if line.opportunity_id in committed_opp_ids:
                plant_map[pid]["budgeted_eoy"] += _eoy(line) * _rate(line)
                plant_map[pid]["budgeted_expected_annual"] += _n(line.expected_annual_saving) * _rate(line)
            # EOY split by budget status (Budgeted / Opportunity / Empty)
            opp_status = budget_status_by_opp.get(line.opportunity_id, "Empty")
            plant_map[pid]["eoy_by_status"][opp_status] = (
                plant_map[pid]["eoy_by_status"].get(opp_status, 0.0)
                + _eoy(line) * _rate(line)
            )
            plant_map[pid]["opp_count"].add(line.opportunity_id)

            # FIX: use ytd_rows_for to properly filter by year AND <= today
            line_ytd = ytd_rows_for([line])
            plant_map[pid]["actual_ytd"] += sum(
                _n(r.actual_saving) for r in line_ytd if r.actual_saving is not None
            ) * _rate(line)
            plant_map[pid]["expected_ytd"] += sum(
                _n(r.expected_saving) for r in line_ytd
            ) * _rate(line)

            opp = line.opportunity
            if opp:
                t = opp.opportunity_type or "Unknown"
                plant_map[pid]["type_breakdown"][t] = plant_map[pid][
                    "type_breakdown"
                ].get(t, 0.0) + _n(line.expected_annual_saving) * _rate(line)
                plant_map[pid]["eoy_by_type"][t] = (
                    plant_map[pid]["eoy_by_type"].get(t, 0.0) + _eoy(line) * _rate(line)
                )
                if line.opportunity_id in committed_opp_ids:
                    plant_map[pid]["budgeted_eoy_by_type"][t] = (
                        plant_map[pid]["budgeted_eoy_by_type"].get(t, 0.0) + _eoy(line) * _rate(line)
                    )
                    plant_map[pid]["budgeted_expected_annual_by_type"][t] = (
                        plant_map[pid]["budgeted_expected_annual_by_type"].get(t, 0.0)
                        + _n(line.expected_annual_saving) * _rate(line)
                    )
                    _reasons = delta_reason_by_opp.get(line.opportunity_id) or ["As planned"]
                    _share = 1.0 / len(_reasons)
                    for _reason in _reasons:
                        plant_map[pid]["budgeted_eoy_by_reason"][_reason] = (
                            plant_map[pid]["budgeted_eoy_by_reason"].get(_reason, 0.0)
                            + _eoy(line) * _rate(line) * _share
                        )
                        plant_map[pid]["budgeted_expected_annual_by_reason"][_reason] = (
                            plant_map[pid]["budgeted_expected_annual_by_reason"].get(_reason, 0.0)
                            + _n(line.expected_annual_saving) * _rate(line) * _share
                        )

        by_plant = []
        for d in sorted(
            plant_map.values(), key=lambda x: x["expected_annual"], reverse=True
        ):
            # FIX: conversion rate = actual YTD / expected YTD (not annual)
            # This correctly shows "are we on track relative to what should have been earned so far?"
            ytd_rate = _pct(d["actual_ytd"], d["expected_ytd"])
            # delta_ytd = how much more/less than expected YTD
            delta_ytd = round(d["actual_ytd"] - d["expected_ytd"], 2)
            by_plant.append(
                {
                    "plant_id": d["plant_id"],
                    "plant_name": d["plant_name"],
                    "expected_annual": round(d["expected_annual"], 2),
                    "budget_value": round(d["budget_value"], 2),
                    "actual_ytd": round(d["actual_ytd"], 2),
                    "expected_ytd": round(d["expected_ytd"], 2),
                    "delta_ytd": delta_ytd,
                    "eoy_forecast": round(d["eoy_forecast"], 2),
                    "opp_count": len(d["opp_count"]),
                    "type_breakdown": d["type_breakdown"],
                    "ytd_rate_pct": ytd_rate,
                    "eoy_by_status": {
                        k: round(v, 2) for k, v in d["eoy_by_status"].items()
                    },
                    # EOY vs Budget: budgeted EOY / budgeted expected annual (both annual → comparable)
                    "eoy_vs_budget_pct": _pct(d["budgeted_eoy"], d["budgeted_expected_annual"])
                    if d["budgeted_expected_annual"]
                    else None,
                    "eoy_vs_expected_pct": _pct(d["eoy_forecast"], d["expected_annual"])
                    if d["expected_annual"]
                    else None,
                    "eoy_by_type": {k: round(v, 2) for k, v in d["eoy_by_type"].items()},
                    # delta = budgeted EOY forecast − budgeted expected annual (both annual figures)
                    # positive → outperforming budget, negative → below budget
                    "delta_eoy_budget_by_type": {
                        t: round(
                            d["budgeted_eoy_by_type"].get(t, 0.0)
                            - d["budgeted_expected_annual_by_type"].get(t, 0.0),
                            2,
                        )
                        for t in set(
                            list(d["budgeted_eoy_by_type"].keys())
                            + list(d["budgeted_expected_annual_by_type"].keys())
                        )
                    },
                    # delta by delta_reason — "As planned" when no reason is set
                    "delta_eoy_budget_by_reason": {
                        r: round(
                            d["budgeted_eoy_by_reason"].get(r, 0.0)
                            - d["budgeted_expected_annual_by_reason"].get(r, 0.0),
                            2,
                        )
                        for r in set(
                            list(d["budgeted_eoy_by_reason"].keys())
                            + list(d["budgeted_expected_annual_by_reason"].keys())
                        )
                    },
                }
            )

        # ── BY SUPPLIER (Top 10 by projected EOY savings) ────────────────
        supplier_map: dict[str, dict] = {}
        for line in kpi_lines:
            opp = line.opportunity
            sup_name = (opp.proposed_supplier_name if opp and opp.proposed_supplier_name else None) or "Unknown"
            opp_type = (opp.opportunity_type if opp else None) or "Unknown"
            if sup_name not in supplier_map:
                supplier_map[sup_name] = {
                    "supplier_name": sup_name,
                    "opp_ids": set(),
                    "eoy_forecast": 0.0,
                    "expected_annual": 0.0,
                    "actual_ytd": 0.0,
                    "eoy_by_type": {},
                }
            sm = supplier_map[sup_name]
            sm["opp_ids"].add(line.opportunity_id)
            sm["eoy_forecast"] += _eoy(line) * _rate(line)
            sm["expected_annual"] += _n(line.expected_annual_saving) * _rate(line)
            sm["eoy_by_type"][opp_type] = (
                sm["eoy_by_type"].get(opp_type, 0.0) + _eoy(line) * _rate(line)
            )
            for row in ytd_rows_for([line]):
                if row.actual_saving is not None:
                    sm["actual_ytd"] += _n(row.actual_saving) * _rate(line)

        by_supplier = sorted(
            [
                {
                    "supplier_name": sm["supplier_name"],
                    "opp_count": len(sm["opp_ids"]),
                    "eoy_forecast": round(sm["eoy_forecast"], 2),
                    "expected_annual": round(sm["expected_annual"], 2),
                    "actual_ytd": round(sm["actual_ytd"], 2),
                    "eoy_by_type": {k: round(v, 2) for k, v in sm["eoy_by_type"].items()},
                }
                for sm in supplier_map.values()
                if sm["supplier_name"] != "Unknown"
            ],
            key=lambda x: x["eoy_forecast"],
            reverse=True,
        )[:10]

        # ── C2 — BY SAVING TYPE (STP run-rate vs Flat annual) ────────────
        # STP types (Sourcing / Technical Productivity): the comparable unit is the
        # Year-N run-rate (saving_year_n), NOT expected_annual_saving which may hold
        # the multi-year EBITDA Period in legacy rows.  Flat types (Negotiation / Cash)
        # use expected_annual_saving as a true annual figure.
        STP_TYPES  = {"Sourcing", "Technical Productivity"}
        FLAT_TYPES = {"Negotiation", "Cash"}

        def _stp_annual(line) -> float:
            opp = line.opportunity
            if opp and opp.saving_year_n is not None:
                return _n(opp.saving_year_n) * _rate(line)
            return _n(line.expected_annual_saving) * _rate(line)

        stp_lines  = [ln for ln in active_lines if (ln.opportunity and (ln.opportunity.opportunity_type or "") in STP_TYPES)]
        flat_lines = [ln for ln in active_lines if (ln.opportunity and (ln.opportunity.opportunity_type or "") in FLAT_TYPES)]

        stp_ytd_rows  = ytd_rows_for(stp_lines)
        flat_ytd_rows = ytd_rows_for(flat_lines)

        by_saving_type = {
            "stp": {
                "label": "STP Run-rate (Year N)",
                "types": sorted(STP_TYPES),
                "line_count": len(stp_lines),
                "expected_annual": round(sum(_stp_annual(ln) for ln in stp_lines), 2),
                "actual_ytd": round(sum(
                    _n(r.actual_saving) * rate_by_line.get(r.financial_line_id, 0.0)
                    for r in stp_ytd_rows if r.actual_saving is not None
                ), 2),
                "expected_ytd": round(sum(
                    _n(r.expected_saving) * rate_by_line.get(r.financial_line_id, 0.0)
                    for r in stp_ytd_rows
                ), 2),
                "eoy_forecast": round(sum(_eoy(ln) * _rate(ln) for ln in stp_lines), 2),
                "program_value_lifetime": round(sum(
                    _n(ln.opportunity.period_saving) * _rate(ln)
                    for ln in stp_lines
                    if ln.opportunity and ln.opportunity.period_saving
                ), 2),
            },
            "flat": {
                "label": "Flat Annual Saving (Negotiation / Cash)",
                "types": sorted(FLAT_TYPES),
                "line_count": len(flat_lines),
                "expected_annual": round(sum(_n(ln.expected_annual_saving) * _rate(ln) for ln in flat_lines), 2),
                "actual_ytd": round(sum(
                    _n(r.actual_saving) * rate_by_line.get(r.financial_line_id, 0.0)
                    for r in flat_ytd_rows if r.actual_saving is not None
                ), 2),
                "expected_ytd": round(sum(
                    _n(r.expected_saving) * rate_by_line.get(r.financial_line_id, 0.0)
                    for r in flat_ytd_rows
                ), 2),
                "eoy_forecast": round(sum(_eoy(ln) * _rate(ln) for ln in flat_lines), 2),
                "program_value_lifetime": None,
            },
        }
        # Attach ytd attainment %
        for seg in by_saving_type.values():
            seg["actual_vs_expected_ytd_pct"] = _pct(seg["actual_ytd"], seg["expected_ytd"])

        # ── D2 — CASH TRACKING ───────────────────────────────────────────
        # cash_actual / cash_expected are distinct monthly fields on Cash-type lines.
        # They were being captured in the DB but never surfaced in the KPI payload —
        # Finance was blind to Cash attainment in the dashboard.
        cash_lines = [
            ln for ln in active_lines
            if (ln.opportunity and (ln.opportunity.opportunity_type or "") == "Cash")
        ]
        cash_ytd_rows = [
            (r, rate_by_line.get(r.financial_line_id, 0.0))
            for ln in cash_lines
            for r in ln.monthly_financials
            if r.period_month
            and _in_budget_year(r.period_month)
            and r.period_month <= ytd_cutoff
            and (
                (ln.real_start_date or ln.planned_start_date) is None
                or r.period_month >= (ln.real_start_date or ln.planned_start_date).replace(day=1)
            )
        ]
        _cash_actual_ytd   = round(sum(_n(r.cash_actual)   * fx for r, fx in cash_ytd_rows if r.cash_actual   is not None), 2)
        _cash_expected_ytd = round(sum(_n(r.cash_expected) * fx for r, fx in cash_ytd_rows), 2)
        cash_kpis = {
            "line_count":           len(cash_lines),
            "expected_annual":      round(sum(_n(ln.expected_annual_saving) * _rate(ln) for ln in cash_lines), 2),
            "cash_actual_ytd":      _cash_actual_ytd,
            "cash_expected_ytd":    _cash_expected_ytd,
            "cash_attainment_pct":  _pct(_cash_actual_ytd, _cash_expected_ytd),
            "saving_actual_ytd":    round(sum(
                _n(r.actual_saving) * fx
                for r, fx in cash_ytd_rows if r.actual_saving is not None
            ), 2),
        }

        # ── BY TYPE ───────────────────────────────────────────────────────

        type_map: dict[str, dict] = {}
        for opp in all_opps:
            t = opp.opportunity_type or "Unknown"
            if t not in type_map:
                type_map[t] = {
                    "type": t,
                    "opp_count": 0,
                    "validated_count": 0,
                    "expected_annual": 0.0,
                    "actual_ytd": 0.0,
                    "expected_ytd": 0.0,
                    "eoy_forecast": 0.0,
                }
            type_map[t]["opp_count"] += 1
            if opp.validation_decision == "Go":
                type_map[t]["validated_count"] += 1

            for line in kpi_lines_by_opp.get(opp.opportunity_id, []):
                type_map[t]["expected_annual"] += _n(line.expected_annual_saving) * _rate(line)
                type_map[t]["eoy_forecast"] += _eoy(line) * _rate(line)
                line_ytd = ytd_rows_for([line])
                type_map[t]["actual_ytd"] += sum(
                    _n(r.actual_saving) for r in line_ytd if r.actual_saving is not None
                ) * _rate(line)
                type_map[t]["expected_ytd"] += sum(
                    _n(r.expected_saving) for r in line_ytd
                ) * _rate(line)

        by_type = []
        for d in sorted(
            type_map.values(), key=lambda x: x["expected_annual"], reverse=True
        ):
            ytd_rate = _pct(d["actual_ytd"], d["expected_ytd"])
            delta_ytd = round(d["actual_ytd"] - d["expected_ytd"], 2)
            by_type.append(
                {
                    **d,
                    "expected_annual": round(d["expected_annual"], 2),
                    "actual_ytd": round(d["actual_ytd"], 2),
                    "expected_ytd": round(d["expected_ytd"], 2),
                    "delta_ytd": delta_ytd,
                    "eoy_forecast": round(d["eoy_forecast"], 2),
                    "ytd_rate_pct": ytd_rate,
                    "eoy_vs_expected_pct": _pct(d["eoy_forecast"], d["expected_annual"])
                    if d["expected_annual"]
                    else None,
                }
            )

        # ── ALERTS ───────────────────────────────────────────────────────

        late_projects = [
            {
                "project_id": p.project_id,
                "project_name": p.project_name,
                "project_owner": p.project_owner,
                "phase_status": p.phase_status,
                "status": p.status,
                "planned_end_date": str(p.planned_end_date)
                if p.planned_end_date
                else None,
            }
            for p in all_projects
            if p.status == "Late"
        ]

        missing_updates = []
        for line in active_lines:
            savings_start = line.real_start_date or line.planned_start_date
            missing_months = [
                r.period_month.strftime("%b %Y")
                for r in line.monthly_financials
                if r.period_month
                and _in_budget_year(r.period_month)
                and r.period_month < current_month_start
                and r.actual_saving is None
                # Only flag months after savings actually started
                and (
                    savings_start is None
                    or r.period_month >= savings_start.replace(day=1)
                )
            ]
            if missing_months:
                opp = line.opportunity
                missing_updates.append(
                    {
                        "financial_line_id": line.financial_line_id,
                        "line_name": line.line_name,
                        "opportunity_name": opp.opportunity_name if opp else None,
                        "follower": line.follower,
                        "missing_months": missing_months,
                        "missing_count": len(missing_months),
                    }
                )
        missing_updates.sort(key=lambda x: x["missing_count"], reverse=True)

        escalated = [
            {
                "financial_line_id": line.financial_line_id,
                "line_name": line.line_name,
                "escalation_reason": line.escalation_reason,
                "escalated_at": str(line.escalated_at) if line.escalated_at else None,
                "escalated_by": line.escalated_by,
                "opportunity_name": line.opportunity.opportunity_name
                if line.opportunity
                else None,
                "delta_ytd": round(_n(line.delta_vs_expected_ytd), 2),
            }
            for line in active_lines
            if line.is_escalated
        ]

        # Estimated saving by calendar year — Phase-0 opportunity estimates, prorated
        # on planned_start_date (reflects N+1..N+3 prices). Additive; separate from the
        # execution-line `year_split` above which tracks post-Go monthly actuals.
        estimate_by_year: dict[str, float] = {}
        for o in all_opps:
            if (o.currency or "EUR") == "EUR":
                fx = 1.0
            else:
                fx = float(o.fx_rate_to_eur) if o.fx_rate_to_eur else 0.0
                if fx <= 0:
                    fx = 0.0
            if fx == 0.0 and (o.currency or "EUR") != "EUR":
                continue  # missing FX — exclude from EUR estimate rather than 1:1
            for yr, amt in (o.saving_by_year or {}).items():
                estimate_by_year[yr] = (
                    estimate_by_year.get(yr, 0.0) + float(amt or 0) * fx
                )
        estimate_saving_by_year = [
            {"year": int(y), "expected": round(v, 2)}
            for y, v in sorted(estimate_by_year.items())
        ]

        # Program value (lifetime) — the multi-year EBITDA Period (period_saving) summed
        # in EUR across the OPEN pipeline. This is the cumulative value of the savings
        # programmes, distinct from the annual run-rate in total_expected_annual. (C3 —
        # the lifetime figure lives here, not conflated into the annual KPI.)
        program_value_lifetime = 0.0
        for o in all_opps:
            if o.period_saving and o.phase_status not in INACTIVE_PHASES and o.phase_status is not None:
                if (o.currency or "EUR") == "EUR":
                    fx = 1.0
                else:
                    fx = float(o.fx_rate_to_eur) if o.fx_rate_to_eur else 0.0
                    if fx <= 0:
                        fx = 0.0
                if fx == 0.0 and (o.currency or "EUR") != "EUR":
                    continue  # missing FX — exclude from EUR total rather than 1:1
                program_value_lifetime += float(o.period_saving) * fx

        # ── BY BUYER (P4 — Team & Governance) ────────────────────────────
        buyer_map: dict[str, dict] = {}
        for line in kpi_lines:
            opp = line.opportunity
            buyer_email = (opp.idea_owner if opp else None) or "Unknown"
            if buyer_email not in buyer_map:
                buyer_map[buyer_email] = {
                    "buyer_email": buyer_email,
                    "buyer_name": (
                        buyer_email.split("@")[0].replace(".", " ").title()
                        if "@" in buyer_email else buyer_email
                    ),
                    "opp_ids": set(),
                    "plant_ids": set(),
                    "categories": set(),
                    "expected_annual": 0.0,
                    "actual_ytd": 0.0,
                    "expected_ytd": 0.0,
                    "eoy_forecast": 0.0,
                    "budget_value": 0.0,
                    "budgeted_eoy": 0.0,
                    "budgeted_expected_annual": 0.0,
                    "escalated_count": 0,
                }
            bm = buyer_map[buyer_email]
            bm["opp_ids"].add(line.opportunity_id)
            if line.plant_id:
                bm["plant_ids"].add(line.plant_id)
            if opp and opp.opportunity_type:
                bm["categories"].add(opp.opportunity_type)
            bm["expected_annual"] += _n(line.expected_annual_saving) * _rate(line)
            bm["eoy_forecast"]    += _eoy(line) * _rate(line)
            bm["budget_value"]    += committed_budget_by_opp.get(line.opportunity_id, 0.0)
            if line.opportunity_id in committed_opp_ids:
                bm["budgeted_eoy"]              += _eoy(line) * _rate(line)
                bm["budgeted_expected_annual"]  += _n(line.expected_annual_saving) * _rate(line)
            if line.is_escalated:
                bm["escalated_count"] += 1
            for row in ytd_rows_for([line]):
                fx = _rate(line)
                bm["expected_ytd"] += _n(row.expected_saving) * fx
                if row.actual_saving is not None:
                    bm["actual_ytd"] += _n(row.actual_saving) * fx

        by_buyer = sorted(
            [
                {
                    "buyer_email": bm["buyer_email"],
                    "buyer_name": bm["buyer_name"],
                    "opp_count": len(bm["opp_ids"]),
                    "plant_count": len(bm["plant_ids"]),
                    "categories": sorted(bm["categories"]),
                    "expected_annual": round(bm["expected_annual"], 2),
                    "actual_ytd": round(bm["actual_ytd"], 2),
                    "expected_ytd": round(bm["expected_ytd"], 2),
                    "delta_ytd": round(bm["actual_ytd"] - bm["expected_ytd"], 2),
                    "eoy_forecast": round(bm["eoy_forecast"], 2),
                    "budget_value": round(bm["budget_value"], 2),
                    "ytd_rate_pct": _pct(bm["actual_ytd"], bm["expected_ytd"]),
                    "eoy_vs_budget_pct": _pct(bm["budgeted_eoy"], bm["budgeted_expected_annual"])
                    if bm["budgeted_expected_annual"] else None,
                    "escalated_count": bm["escalated_count"],
                }
                for bm in buyer_map.values()
            ],
            key=lambda x: x["expected_annual"],
            reverse=True,
        )

        # Active filter summary to echo back to the client
        _active_filters = {
            "year": current_year,
            "plant_ids": list(_plant_set),
            "categories": list(_cat_set),
            "buyer_emails": list(_buyer_set),
        }

        return {
            "year": current_year,
            "computed_at": datetime.utcnow().isoformat(),
            "reporting_currency": "EUR",
            "active_filters": _active_filters,
            "available_filters": _available_filters,
            "kpis": {
                # Forecast
                "eoy_forecast_total": round(total_eoy_forecast, 2),
                "eoy_vs_budget_pct": eoy_vs_budget_pct,
                "eoy_vs_expected_pct": eoy_vs_expected_pct,
                "forecast_drift": forecast_drift,
                # Effectiveness
                "actual_ytd_total": round(actual_ytd, 2),
                "expected_ytd_total": round(expected_ytd, 2),
                "actual_vs_expected_ytd_pct": actual_vs_expected_ytd_pct,
                "actual_vs_budget_ytd_pct": actual_vs_budget_ytd_pct,
                "total_expected_annual": round(total_expected, 2),
                "program_value_lifetime": round(program_value_lifetime, 2),
                "budgeted_expected_annual": round(budgeted_expected_annual, 2),
                "total_budget": round(total_budget, 2),
                "over_budget_count": over_budget_count,
                "over_budget_amount": round(over_budget_amount, 2),
                "conversion_rate_pct": conversion_rate_pct,
                "validated_opp_count": len(validated_opps),
                "converted_opp_count": len(converted_opps),
                # Efficiency
                "phase0_go_rate_pct": phase0_go_rate_pct,
                "phase0_decided_count": len(phase0_decided),
                "phase0_go_count": len(phase0_go),
                "project_on_time_rate_pct": project_on_time_rate_pct,
                "monthly_update_pct": monthly_update_pct,
                # Portfolio quality
                "avg_priority_score": avg_priority,
                # Counts
                "active_lines_count": len(kpi_lines),
                "open_pipeline_count": len(
                    [o for o in all_opps if o.phase_status not in INACTIVE_PHASES and o.phase_status is not None]
                ),
                "escalated_count": len(escalated),
                "late_projects_count": len(late_projects),
                "missing_update_lines": len(missing_updates),
                # Data quality — non-EUR lines summed at 1:1 for lack of an FX rate
                "non_eur_missing_rate": non_eur_missing_rate,
                # Validated-but-not-yet-committed pipeline (budget_status == "Opportunity")
                "opportunity_pipeline_amount": round(sum(opportunity_pipeline_by_opp.values()), 2),
                "opportunity_pipeline_count": len(opportunity_opp_ids),
                # Fixed-date total: actual savings since Jan 1 2026 (Monday board calendar-year reference)
                "total_saving_from_jan2026": total_saving_from_jan2026,
            },
            "gate_go_rates": gate_go_rates,
            "monthly_actuals": monthly_actuals,
            "year_split": year_split,
            "estimate_saving_by_year": estimate_saving_by_year,
            "by_saving_type": by_saving_type,
            "cash_kpis": cash_kpis,
            "by_plant": by_plant,
            "by_supplier": by_supplier,
            "by_type": by_type,
            "by_buyer": by_buyer,
            "late_projects": late_projects,
            "missing_updates": missing_updates[:10],
            "escalated": escalated,
        }
