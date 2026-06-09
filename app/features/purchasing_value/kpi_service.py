"""KPI computation service for the purchasing value dashboard.

Key formulas (per Olivier GRIMAUD transcript 2026-06-03):
- Expected savings pro-rated by DAYS: annual × days_in_month / 365  (not ÷12)
- Alerts only from planned_start_date onwards (months before = expected 0)
- Budget status is per budget_year, resetable annually
"""

from __future__ import annotations

import calendar
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import FinancialLine, MonthlyFinancial, Opportunity, Project


def _n(v) -> float:
    if v is None:
        return 0.0
    return float(v)


def _pct(num: float, denom: float) -> Optional[float]:
    if denom == 0:
        return None
    return round((num / denom) * 100, 1)


class PurchasingKpiService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def compute_all(self, year: Optional[int] = None) -> dict:
        current_year = year or datetime.utcnow().year
        today = date.today()
        current_month_start = today.replace(day=1)

        # ── Load data ────────────────────────────────────────────────────
        lines_result = await self.db.execute(
            select(FinancialLine)
            .where(FinancialLine.status.in_(["Active", "Completed"]))
            .options(
                selectinload(FinancialLine.monthly_financials),
                selectinload(FinancialLine.opportunity),
                selectinload(FinancialLine.plant),
            )
        )
        all_lines: list[FinancialLine] = list(lines_result.scalars().all())
        active_lines = [l for l in all_lines if l.status == "Active"]

        opps_result = await self.db.execute(
            select(Opportunity)
            .where(Opportunity.is_deleted == False)
            .options(selectinload(Opportunity.plant))
        )
        all_opps: list[Opportunity] = list(opps_result.scalars().all())

        projs_result = await self.db.execute(select(Project))
        all_projects: list[Project] = list(projs_result.scalars().all())

        # Build lookup: opportunity_id → list of lines (fixes O(n²))
        lines_by_opp: dict[int, list[FinancialLine]] = {}
        for line in all_lines:
            lines_by_opp.setdefault(line.opportunity_id, []).append(line)

        # ── FORECAST KPIs ─────────────────────────────────────────────────

        total_eoy_forecast = sum(_n(l.forecast_eoy_current) for l in active_lines)
        total_budget = sum(_n(l.budget_value) for l in active_lines if l.budget_status == "Budgeted")
        total_expected = sum(_n(l.expected_annual_saving) for l in active_lines)

        budgeted_eoy_forecast = sum(_n(l.forecast_eoy_current) for l in active_lines if l.budget_status == "Budgeted")

        eoy_vs_budget_pct = _pct(budgeted_eoy_forecast, total_budget)
        eoy_vs_expected_pct = _pct(total_eoy_forecast, total_expected)

        # Forecast drift: latest forecast_eoy_saving vs previous month's
        # Use the last two months with a non-null forecast_eoy_saving
        all_monthly_with_forecast = sorted(
            [r for l in active_lines for r in l.monthly_financials if r.forecast_eoy_saving is not None and r.period_month],
            key=lambda r: r.period_month, reverse=True
        )
        forecast_drift: Optional[float] = None
        if len(all_monthly_with_forecast) >= 2:
            latest_sum = sum(_n(r.forecast_eoy_saving) for r in all_monthly_with_forecast
                             if r.period_month == all_monthly_with_forecast[0].period_month)
            prev_month = None
            for r in all_monthly_with_forecast:
                if r.period_month != all_monthly_with_forecast[0].period_month:
                    prev_month = r.period_month
                    break
            if prev_month:
                prev_sum = sum(_n(r.forecast_eoy_saving) for r in all_monthly_with_forecast if r.period_month == prev_month)
                forecast_drift = round(latest_sum - prev_sum, 2)

        # ── EFFECTIVENESS KPIs ────────────────────────────────────────────

        validated_opps = [o for o in all_opps if o.validation_decision == "Go" and o.phase_status != "Closed"]
        converted_opps = [
            o for o in validated_opps
            if any(_n(l.cumulated_real_saving) > 0 for l in lines_by_opp.get(o.opportunity_id, []))
        ]
        conversion_rate_pct = _pct(len(converted_opps), len(validated_opps))

        # YTD rows = in selected year, not in the future, and AFTER savings start date
        # Olivier: months before planned_start_date are expected 0 — don't count them in KPIs
        def ytd_rows_for(lines):
            rows = []
            for l in lines:
                savings_start = l.real_start_date or l.planned_start_date
                for r in l.monthly_financials:
                    if not r.period_month:
                        continue
                    if r.period_month.year != current_year:
                        continue
                    if r.period_month > today:
                        continue
                    # Only count months from savings start date
                    if savings_start and r.period_month < savings_start.replace(day=1):
                        continue
                    rows.append(r)
            return rows

        ytd_rows = ytd_rows_for(active_lines)
        actual_ytd = sum(_n(r.actual_saving) for r in ytd_rows if r.actual_saving is not None)
        expected_ytd = sum(_n(r.expected_saving) for r in ytd_rows)

        budgeted_ytd_rows = ytd_rows_for([l for l in active_lines if l.budget_status == "Budgeted"])
        budget_actual_ytd = sum(_n(r.actual_saving) for r in budgeted_ytd_rows if r.actual_saving is not None)
        budget_expected_ytd = sum(_n(r.expected_saving) for r in budgeted_ytd_rows)

        actual_vs_expected_ytd_pct = _pct(actual_ytd, expected_ytd)
        actual_vs_budget_ytd_pct = _pct(budget_actual_ytd, budget_expected_ytd)

        # ── EFFICIENCY KPIs ───────────────────────────────────────────────

        # Phase 0 Go Rate — FIX: denominator = all opps that have had any gate decision
        phase0_decided = [o for o in all_opps if o.validation_decision in ("Go", "No Go", "Review")]
        phase0_go = [o for o in phase0_decided if o.validation_decision == "Go"]
        phase0_go_rate_pct = _pct(len(phase0_go), len(phase0_decided))

        active_projects = [p for p in all_projects if p.status in ("On time", "Late", "On hold")]
        on_time_projects = [p for p in active_projects if p.status == "On time"]
        project_on_time_rate_pct = _pct(len(on_time_projects), len(active_projects))

        lines_with_current_update = [
            l for l in active_lines
            if any(
                r.period_month
                and r.period_month.year == today.year
                and r.period_month.month == today.month
                and r.actual_saving is not None
                for r in l.monthly_financials
            )
        ]
        monthly_update_pct = _pct(len(lines_with_current_update), len(active_lines))

        scored_opps = [o for o in all_opps if o.priority_score and o.phase_status not in ("Closed", None)]
        avg_priority = round(
            sum(_n(o.priority_score) for o in scored_opps) / len(scored_opps), 1
        ) if scored_opps else None

        # ── MONTHLY SAVINGS (for chart) ───────────────────────────────────
        # Note: expected_saving in monthly rows already uses days-based pro-ration
        # (annual × days_in_month / 365) since the service fix.
        # For existing rows generated with ÷12, use /rebuild-profile endpoint to correct them.

        monthly_map: dict[str, dict] = {}
        for line in all_lines:
            savings_start = line.real_start_date or line.planned_start_date
            for row in line.monthly_financials:
                if not row.period_month:
                    continue
                # Skip months before savings start (expected = 0 per Olivier)
                if savings_start and row.period_month < savings_start.replace(day=1):
                    continue
                key = row.period_month.strftime("%Y-%m")
                if key not in monthly_map:
                    monthly_map[key] = {"period": key, "expected": 0.0, "actual": 0.0, "budget": 0.0, "eoy_forecast": 0.0}
                monthly_map[key]["expected"] += _n(row.expected_saving)
                if row.actual_saving is not None:
                    monthly_map[key]["actual"] += _n(row.actual_saving)
                if line.budget_status == "Budgeted":
                    monthly_map[key]["budget"] += _n(row.expected_saving)
                if row.forecast_eoy_saving is not None:
                    monthly_map[key]["eoy_forecast"] += _n(row.forecast_eoy_saving)

        monthly_actuals = sorted(monthly_map.values(), key=lambda x: x["period"])

        # ── YEAR-SPLIT (days-based) ───────────────────────────────────────
        # For each financial line, split expected annual saving across calendar years
        # using actual days: saving_year_N = annual × days_in_year_N / 365
        year_split_map: dict[int, dict] = {}
        for line in all_lines:
            if not line.planned_start_date or not line.expected_annual_saving:
                continue
            start = line.real_start_date or line.planned_start_date
            duration = int(line.duration_months or 12)
            annual = _n(line.expected_annual_saving)
            # Monthly expected = annual / duration (consistent with service formula)
            # Handles one-shot (duration=1: full amount in single month) and recurring
            monthly_exp = annual / duration if duration > 0 else 0.0
            for i in range(duration):
                from app.features.purchasing_value.schemas import add_months as _add_months
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
                        year_split_map[yr] = {"year": yr, "expected": 0.0, "actual": 0.0}
                    year_split_map[yr]["actual"] += _n(row.actual_saving)

        year_split = [
            {**d, "expected": round(d["expected"], 2), "actual": round(d["actual"], 2),
             "ytd_rate_pct": _pct(d["actual"], d["expected"])}
            for d in sorted(year_split_map.values(), key=lambda x: x["year"])
        ]

        # ── BY PLANT ──────────────────────────────────────────────────────

        plant_map: dict[int, dict] = {}
        for line in all_lines:
            pid = line.plant_id
            if pid is None:
                continue
            plant_name = line.plant.site_name if line.plant else f"Plant {pid}"
            if pid not in plant_map:
                plant_map[pid] = {
                    "plant_id": pid, "plant_name": plant_name,
                    "expected_annual": 0.0, "budget_value": 0.0,
                    "actual_ytd": 0.0, "expected_ytd": 0.0,  # ← both needed for correct %
                    "eoy_forecast": 0.0,
                    "opp_count": set(), "type_breakdown": {},
                }
            plant_map[pid]["expected_annual"] += _n(line.expected_annual_saving)
            plant_map[pid]["budget_value"] += _n(line.budget_value)
            plant_map[pid]["eoy_forecast"] += _n(line.forecast_eoy_current)
            plant_map[pid]["opp_count"].add(line.opportunity_id)

            # FIX: use ytd_rows_for to properly filter by year AND <= today
            line_ytd = ytd_rows_for([line])
            plant_map[pid]["actual_ytd"] += sum(_n(r.actual_saving) for r in line_ytd if r.actual_saving is not None)
            plant_map[pid]["expected_ytd"] += sum(_n(r.expected_saving) for r in line_ytd)

            opp = line.opportunity
            if opp:
                t = opp.opportunity_type or "Unknown"
                plant_map[pid]["type_breakdown"][t] = plant_map[pid]["type_breakdown"].get(t, 0.0) + _n(line.expected_annual_saving)

        by_plant = []
        for d in sorted(plant_map.values(), key=lambda x: x["expected_annual"], reverse=True):
            # FIX: conversion rate = actual YTD / expected YTD (not annual)
            # This correctly shows "are we on track relative to what should have been earned so far?"
            ytd_rate = _pct(d["actual_ytd"], d["expected_ytd"])
            # delta_ytd = how much more/less than expected YTD
            delta_ytd = round(d["actual_ytd"] - d["expected_ytd"], 2)
            by_plant.append({
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
                "ytd_rate_pct": ytd_rate,                              # actual YTD / expected YTD
                "eoy_vs_budget_pct": _pct(d["eoy_forecast"], d["budget_value"]) if d["budget_value"] else None,
                "eoy_vs_expected_pct": _pct(d["eoy_forecast"], d["expected_annual"]) if d["expected_annual"] else None,
            })

        # ── BY TYPE ───────────────────────────────────────────────────────

        type_map: dict[str, dict] = {}
        for opp in all_opps:
            t = opp.opportunity_type or "Unknown"
            if t not in type_map:
                type_map[t] = {"type": t, "opp_count": 0, "validated_count": 0,
                               "expected_annual": 0.0, "actual_ytd": 0.0, "expected_ytd": 0.0, "eoy_forecast": 0.0}
            type_map[t]["opp_count"] += 1
            if opp.validation_decision == "Go":
                type_map[t]["validated_count"] += 1

            # FIX: use lookup dict instead of O(n²) loop
            for line in lines_by_opp.get(opp.opportunity_id, []):
                type_map[t]["expected_annual"] += _n(line.expected_annual_saving)
                type_map[t]["eoy_forecast"] += _n(line.forecast_eoy_current)
                line_ytd = ytd_rows_for([line])
                type_map[t]["actual_ytd"] += sum(_n(r.actual_saving) for r in line_ytd if r.actual_saving is not None)
                type_map[t]["expected_ytd"] += sum(_n(r.expected_saving) for r in line_ytd)

        by_type = []
        for d in sorted(type_map.values(), key=lambda x: x["expected_annual"], reverse=True):
            ytd_rate = _pct(d["actual_ytd"], d["expected_ytd"])
            delta_ytd = round(d["actual_ytd"] - d["expected_ytd"], 2)
            by_type.append({
                **d,
                "expected_annual": round(d["expected_annual"], 2),
                "actual_ytd": round(d["actual_ytd"], 2),
                "expected_ytd": round(d["expected_ytd"], 2),
                "delta_ytd": delta_ytd,
                "eoy_forecast": round(d["eoy_forecast"], 2),
                "ytd_rate_pct": ytd_rate,
                "eoy_vs_expected_pct": _pct(d["eoy_forecast"], d["expected_annual"]) if d["expected_annual"] else None,
            })

        # ── ALERTS ───────────────────────────────────────────────────────

        late_projects = [
            {"project_id": p.project_id, "project_name": p.project_name,
             "project_owner": p.project_owner, "phase_status": p.phase_status,
             "status": p.status, "planned_end_date": str(p.planned_end_date) if p.planned_end_date else None}
            for p in all_projects if p.status == "Late"
        ]

        missing_updates = []
        for line in active_lines:
            savings_start = line.real_start_date or line.planned_start_date
            missing_months = [
                r.period_month.strftime("%b %Y")
                for r in line.monthly_financials
                if r.period_month
                and r.period_month < current_month_start
                and r.actual_saving is None
                # Only flag months after savings actually started
                and (savings_start is None or r.period_month >= savings_start.replace(day=1))
            ]
            if missing_months:
                opp = line.opportunity
                missing_updates.append({
                    "financial_line_id": line.financial_line_id,
                    "line_name": line.line_name,
                    "opportunity_name": opp.opportunity_name if opp else None,
                    "follower": line.follower,
                    "missing_months": missing_months,
                    "missing_count": len(missing_months),
                })
        missing_updates.sort(key=lambda x: x["missing_count"], reverse=True)

        escalated = [
            {"financial_line_id": line.financial_line_id,
             "line_name": line.line_name,
             "escalation_reason": line.escalation_reason,
             "escalated_at": str(line.escalated_at) if line.escalated_at else None,
             "escalated_by": line.escalated_by,
             "opportunity_name": line.opportunity.opportunity_name if line.opportunity else None,
             "delta_ytd": round(_n(line.delta_vs_expected_ytd), 2)}
            for line in active_lines if line.is_escalated
        ]

        return {
            "year": current_year,
            "computed_at": datetime.utcnow().isoformat(),
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
                "total_budget": round(total_budget, 2),
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
                "active_lines_count": len(active_lines),
                "open_pipeline_count": len([o for o in all_opps if o.phase_status not in ("Closed", None)]),
                "escalated_count": len(escalated),
                "late_projects_count": len(late_projects),
                "missing_update_lines": len(missing_updates),
            },
            "monthly_actuals": monthly_actuals,
            "year_split": year_split,
            "by_plant": by_plant,
            "by_type": by_type,
            "late_projects": late_projects,
            "missing_updates": missing_updates[:10],
            "escalated": escalated,
        }
