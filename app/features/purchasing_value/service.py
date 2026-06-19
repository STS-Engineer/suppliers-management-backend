"""Purchasing value management service — full business logic."""

from __future__ import annotations

import calendar
import logging
from datetime import datetime, date
from decimal import Decimal
from math import ceil
from typing import Optional, List

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.db.models import (
    FinancialLine,
    MonthlyFinancial,
    Opportunity,
    OpportunityBudgetYear,
    OpportunityDocument,
    OpportunityPhaseSnapshot,
    Project,
    SupplierSiteRelation,
    SupplierUnit,
)
from app.features.purchasing_value.schemas import (
    EscalateRequest,
    FinancialLineCompleteRequest,
    GateDecisionRequest,
    MonthlyActualUpdateRequest,
    OpportunityCreateRequest,
    OpportunityUpdateRequest,
    RecoveryUpdateRequest,
    StartStudyRequest,
    SubmitForValidationRequest,
    SubmitToCommitteeRequest,
    STPRevisionDecisionPayload,
    STPRevisionRequestPayload,
    ValidationRequestPayload,
    add_months,
    compute_priority,
    compute_stp_financials,
    compute_saving_by_calendar_year,
    compute_savings_start_date,
    compute_budget_year_portions,
    auto_payback_score,
    auto_leadtime_score,
)
from app.shared.utils.email.email_service import send_email, send_email_with_attachment
from app.shared.utils.blob_storage import (
    upload_opportunity_document,
    delete_blob,
    _extract_blob_name,
)
from app.features.purchasing_value.stp_pdf import generate_stp_pdf

logger = logging.getLogger(__name__)

# Phase progression order
PHASE_ORDER = [
    "Assigned",
    "Phase 0",
    "Phase 1",
    "Phase 2",
    "Phase 3",
    "Phase 4",
    "Closed",
]

# Types that never create a project
NO_PROJECT_TYPES = {"Negotiation", "Cash"}


class PurchasingValueService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def list_opportunities(self) -> List[Opportunity]:
        result = await self.db.execute(
            select(Opportunity)
            .where(Opportunity.is_deleted.is_(False))
            .options(
                selectinload(Opportunity.projects),
                selectinload(Opportunity.financial_lines).selectinload(
                    FinancialLine.monthly_financials
                ),
                selectinload(Opportunity.opp_documents),
                selectinload(Opportunity.budget_years),
                selectinload(Opportunity.plant),
            )
            .order_by(Opportunity.opportunity_id.desc())
        )
        return list(result.scalars().all())

    async def get_opportunity(self, opportunity_id: int) -> Opportunity:
        result = await self.db.execute(
            select(Opportunity)
            .where(
                Opportunity.opportunity_id == opportunity_id,
                Opportunity.is_deleted.is_(False),
            )
            .options(
                selectinload(Opportunity.projects),
                selectinload(Opportunity.financial_lines).selectinload(
                    FinancialLine.monthly_financials
                ),
                selectinload(Opportunity.opp_documents),
                selectinload(Opportunity.budget_years),
                selectinload(Opportunity.plant),
            )
        )
        opp = result.scalar_one_or_none()
        if opp is None:
            raise AppException(404, "Opportunity not found", "OPPORTUNITY_NOT_FOUND")
        return opp

    async def get_financial_line(self, line_id: int) -> FinancialLine:
        result = await self.db.execute(
            select(FinancialLine)
            .where(FinancialLine.financial_line_id == line_id)
            .options(selectinload(FinancialLine.monthly_financials))
        )
        line = result.scalar_one_or_none()
        if line is None:
            raise AppException(
                404, "Financial line not found", "FINANCIAL_LINE_NOT_FOUND"
            )
        return line

    async def get_monthly_row(self, month_id: int) -> MonthlyFinancial:
        result = await self.db.execute(
            select(MonthlyFinancial).where(
                MonthlyFinancial.monthly_financial_id == month_id
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise AppException(404, "Monthly row not found", "MONTHLY_ROW_NOT_FOUND")
        return row

    # ------------------------------------------------------------------
    # Create opportunity
    # ------------------------------------------------------------------

    async def create_opportunity(
        self, payload: OpportunityCreateRequest
    ) -> Opportunity:
        if payload.opportunity_type not in [
            "Negotiation",
            "Sourcing",
            "Technical Productivity",
            "Cash",
        ]:
            raise AppException(
                422,
                "Invalid type. Must be one of: Negotiation, Sourcing, Technical Productivity, Cash",
                "INVALID_TYPE",
            )

        # Every opportunity must be tied to a plant — it is budgeted, supplier-evaluated
        # and KPI-rolled-up per plant. Required for all types (server-side too, so the
        # API isn't an open path to unallocatable opportunities).
        if not payload.plant_id:
            raise AppException(
                422,
                "Plant is required to create an opportunity.",
                "PLANT_REQUIRED",
            )

        opp = Opportunity(
            opportunity_name=payload.opportunity_name,
            opportunity_type=payload.opportunity_type,
            idea_owner=payload.idea_owner,
            description=payload.description,
            plant_id=payload.plant_id,
            supplier_id=payload.supplier_id,
            budget_year=payload.budget_year,
            validation_status="Empty",
            status="Assigned",
            phase_status="Phase 0",
            validation_decision=None,
        )
        self.db.add(opp)
        await self.db.flush()
        await self.db.refresh(
            opp,
            ["projects", "financial_lines", "opp_documents", "budget_years", "plant"],
        )
        return opp

    # ------------------------------------------------------------------
    # Update Phase 0 fields
    # ------------------------------------------------------------------

    async def update_opportunity(
        self, opportunity_id: int, payload: OpportunityUpdateRequest
    ) -> Opportunity:
        opp = await self.get_opportunity(opportunity_id)

        if opp.phase_status == "Closed":
            raise AppException(
                422, "Closed opportunities cannot be edited.", "WRONG_PHASE"
            )

        # ── Governance lock ──────────────────────────────────────────────
        # Once a financial line carries realized actuals, the saving baseline is
        # COMMITTED. The figures that determine it may then change ONLY through the
        # audited Revise-Baseline action (which carries a reviewer/validation step),
        # never via a silent edit here — otherwise the goalposts could be moved after
        # realization and inflate attainment. Non-financial fields stay editable.
        if opp.opportunity_type in ("Sourcing", "Technical Productivity"):
            baseline_fields = (
                "current_price", "proposed_price",
                "proposed_price_n1", "proposed_price_n2", "proposed_price_n3",
                "current_price_n1", "current_price_n2", "current_price_n3",
                "annual_quantity_n1", "annual_quantity_n2",
                "annual_quantity_n3", "annual_quantity_n4",
                "bonus_before", "bonus_after",
            )
        else:
            baseline_fields = ("expected_annual_saving",)

        def _baseline_change_attempted() -> bool:
            for f in baseline_fields:
                new_val = getattr(payload, f, None)
                if new_val is not None and new_val != getattr(opp, f, None):
                    return True
            return False

        line_has_actuals = any(
            m.actual_saving is not None
            for fl in opp.financial_lines
            if fl.status in ("Active", "Completed")
            for m in (fl.monthly_financials or [])
        )
        if line_has_actuals and _baseline_change_attempted():
            raise AppException(
                422,
                "This opportunity already has realized actuals — the saving baseline "
                "is locked. Use Revise Baseline (audited and reviewed) to change it; "
                "baseline figures cannot be edited silently.",
                "BASELINE_LOCKED_ACTUALS",
            )

        # STP revision approval gate — Phase 2/3 requires Director sign-off before
        # baseline figures can change.  Current values remain active while the request
        # is pending; the buyer must use POST /request-stp-revision instead.
        if (
            opp.opportunity_type in ("Sourcing", "Technical Productivity")
            and opp.phase_status in ("Phase 2", "Phase 3")
            and not line_has_actuals   # Phase 3 with actuals already caught above
            and _baseline_change_attempted()
        ):
            raise AppException(
                422,
                "STP baseline changes in Phase 2 and Phase 3 require Director approval. "
                "Use 'Request Revision' to submit the proposed values for sign-off — "
                "current figures remain active until the Director approves.",
                "STP_REQUIRES_APPROVAL",
            )

        # The STP is locked while it is awaiting a gate decision (submitted to a PM /
        # committee), so the approved document cannot be silently changed out from
        # under the reviewer. Returning it for rework moves the status off these values
        # and unlocks editing again.
        if opp.status in ("Awaiting Validation", "Under Committee Review") and _baseline_change_attempted():
            raise AppException(
                422,
                "This STP is awaiting a gate decision and is locked — it must stay "
                "identical to the version sent to the reviewer. Wait for the decision, "
                "or have it returned for rework before changing the figures.",
                "STP_LOCKED_PENDING_APPROVAL",
            )

        # Snapshot the expected saving BEFORE any mutation (direct edit or STP
        # recompute) so we can detect a baseline change and keep the monthly grid in
        # sync while it is still safe to do so (see the regen block below).
        old_expected_saving = opp.expected_annual_saving

        _set_if(opp, "opportunity_name", payload.opportunity_name)
        _set_if(opp, "description", payload.description)
        _set_if(opp, "expected_annual_saving", payload.expected_annual_saving)
        _set_if(opp, "cash_impact", payload.cash_impact)
        _set_if(opp, "duration_months", payload.duration_months)

        # Track planned_start_date change — rebuild profile if the date shifted and
        # savings have not started yet (Phase 0–2; Phase 3+ uses real_start_date / R9).
        # Planned Start is the user's estimate of the real savings-start date.
        old_planned_start = opp.planned_start_date
        _set_if(opp, "planned_start_date", payload.planned_start_date)
        planned_start_changed = (
            payload.planned_start_date is not None
            and payload.planned_start_date != old_planned_start
            and opp.phase_status in ("Assigned", "Phase 0", "Phase 1", "Phase 2")
        )

        # R9 — if real_start_date changes (Phase 3), rebuild monthly profile
        old_real_start = opp.real_start_date
        _set_if(opp, "execution_start_date", payload.execution_start_date)
        _set_if(opp, "real_start_date", payload.real_start_date)
        real_start_changed = (
            payload.real_start_date is not None
            and payload.real_start_date != old_real_start
        )

        # Validation status / budget year are DERIVED from workflow state (see
        # _sync_budget_years) — no manual setting. Any compatibility
        # budget_status/budget_year sent by the client is ignored.
        _set_if(opp, "change_mode", payload.change_mode)

        # C1 — FX freeze: currency and fx_rate_to_eur are immutable once a director has
        # committed a "Budgeted" row or once the first actual saving has been recorded.
        # Changing the rate after commitment would retroactively reprice all historical
        # EUR-consolidated figures without any audit trail.
        def _fx_is_locked() -> bool:
            if any(
                getattr(by, "budget_status", None) == "Budgeted"
                for by in (opp.budget_years or [])
            ):
                return True
            return any(
                m.actual_saving is not None
                for fl in (opp.financial_lines or [])
                if fl.status in ("Active", "Completed")
                for m in (fl.monthly_financials or [])
            )

        if _fx_is_locked():
            if payload.currency is not None and payload.currency != (opp.currency or "EUR"):
                raise AppException(
                    422,
                    "Currency is frozen after the first budget commitment or actual saving "
                    "is recorded. It cannot be changed without voiding the financial audit trail.",
                    "CURRENCY_LOCKED",
                )
            if payload.fx_rate_to_eur is not None and payload.fx_rate_to_eur != opp.fx_rate_to_eur:
                raise AppException(
                    422,
                    "FX rate is frozen after the first budget commitment or actual saving "
                    "is recorded. Historical EUR-consolidated figures must remain stable.",
                    "FX_RATE_LOCKED",
                )
        else:
            _set_if(opp, "currency", payload.currency)
            _set_if(opp, "fx_rate_to_eur", payload.fx_rate_to_eur)

        # EUR is the reporting currency — its rate is always 1. Force it so a stale rate
        # left over from a previous currency can never distort consolidated EUR totals.
        if (opp.currency or "EUR") == "EUR":
            opp.fx_rate_to_eur = Decimal("1")
        _set_if(opp, "assumptions_summary", payload.assumptions_summary)
        _set_if(opp, "comments", payload.comments)
        _set_if(opp, "plant_id", payload.plant_id)
        _set_if(opp, "supplier_id", payload.supplier_id)
        _set_if(opp, "purchasing_owner", payload.purchasing_owner)
        _set_if(opp, "conversion_owner", payload.conversion_owner)
        # D score — manual dropdown (Easy/Relatively easy/Moderately difficult/Difficult/Very Difficult)
        if payload.difficulty_score is not None:
            _set_if(opp, "difficulty_score", payload.difficulty_score)

        # STP fields
        _set_if(opp, "scope_in", payload.scope_in)
        _set_if(opp, "scope_out", payload.scope_out)
        _set_if(opp, "customers", payload.customers)
        _set_if(opp, "annual_quantity_n1", payload.annual_quantity_n1)
        _set_if(opp, "annual_quantity_n2", payload.annual_quantity_n2)
        _set_if(opp, "annual_quantity_n3", payload.annual_quantity_n3)
        _set_if(opp, "annual_quantity_n4", payload.annual_quantity_n4)
        _set_if(opp, "proposed_supplier_name", payload.proposed_supplier_name)
        _set_if(opp, "proposed_supplier_id", payload.proposed_supplier_id)
        _set_if(opp, "current_price", payload.current_price)
        _set_if(opp, "proposed_price", payload.proposed_price)
        _set_if(opp, "proposed_price_n1", payload.proposed_price_n1)
        _set_if(opp, "proposed_price_n2", payload.proposed_price_n2)
        _set_if(opp, "proposed_price_n3", payload.proposed_price_n3)
        _set_if(opp, "incoterms_before", payload.incoterms_before)
        _set_if(opp, "incoterms_after", payload.incoterms_after)
        _set_if(opp, "top_days_before", payload.top_days_before)
        _set_if(opp, "top_days_after", payload.top_days_after)
        _set_if(opp, "transit_days_before", payload.transit_days_before)
        _set_if(opp, "transit_days_after", payload.transit_days_after)
        _set_if(opp, "country_after", payload.country_after)
        _set_if(opp, "bonus_before", payload.bonus_before)
        _set_if(opp, "bonus_after", payload.bonus_after)
        _set_if(opp, "consignment_before", payload.consignment_before)
        _set_if(opp, "consignment_after", payload.consignment_after)
        _set_if(opp, "current_price_n1", payload.current_price_n1)
        _set_if(opp, "current_price_n2", payload.current_price_n2)
        _set_if(opp, "current_price_n3", payload.current_price_n3)
        if payload.supplier_asked is not None:
            opp.supplier_asked = payload.supplier_asked
        _set_if(opp, "supplier_asked_result", payload.supplier_asked_result)
        _set_if(opp, "tooling_cost", payload.tooling_cost)
        _set_if(opp, "travel_cost", payload.travel_cost)
        _set_if(opp, "qualification_cost", payload.qualification_cost)
        _set_if(opp, "other_cost", payload.other_cost)
        if payload.stp_risks is not None:
            opp.stp_risks = payload.stp_risks.model_dump()
        if payload.stp_benefits is not None:
            opp.stp_benefits = payload.stp_benefits.model_dump()
        _set_if(opp, "phase1_weeks", payload.phase1_weeks)
        _set_if(opp, "phase2_weeks", payload.phase2_weeks)
        _set_if(opp, "phase3_weeks", payload.phase3_weeks)
        _set_if(opp, "phase4_weeks", payload.phase4_weeks)
        if payload.reason_productivity is not None:
            opp.reason_productivity = payload.reason_productivity
        if payload.reason_quality is not None:
            opp.reason_quality = payload.reason_quality
        if payload.reason_capacity is not None:
            opp.reason_capacity = payload.reason_capacity
        _set_if(opp, "reason_other", payload.reason_other)
        _set_if(opp, "secondary_plants", payload.secondary_plants)
        _set_if(opp, "gate_conditions", payload.gate_conditions)
        # Auto-compute investment total (all 4 cost lines)
        costs = [
            float(opp.tooling_cost or 0),
            float(opp.travel_cost or 0),
            float(opp.qualification_cost or 0),
            float(opp.other_cost or 0),
        ]
        total = sum(costs)
        if total > 0:
            opp.total_investment = Decimal(str(total))

        # STP financials — exact formulas from Excel "format STP rev 1.2" (D51/D52/F51/F52/D55/D56)
        stp_fin = compute_stp_financials(opp)
        # D4 — guard: price_after > price_before inverts the saving to a cost increase.
        # Reject early so corrupted data never reaches the DB.
        _year_n_raw = (stp_fin.get("saving_per_year") or [None])[0]
        if _year_n_raw is not None and float(_year_n_raw) < 0:
            raise AppException(
                422,
                f"Year-N saving is negative ({float(_year_n_raw):,.0f} €) — "
                "price_after exceeds price_before for at least one STP component. "
                "Please review the prices before saving.",
                "STP_NEGATIVE_SAVING",
            )
        if stp_fin["period_saving"] is not None:
            # The multi-year EBITDA Period (sum of years N..N+3) lives in period_saving.
            opp.period_saving = Decimal(str(stp_fin["period_saving"]))
            # Headline expected_annual_saving is the YEAR-N run-rate — a TRUE annual
            # figure, directly comparable with Negotiation/Cash opps. Aggregating the
            # period total here would add a 4-year sum to per-year figures. (Audit C3.)
            year_n = stp_fin["saving_per_year"][0]
            if year_n is not None:
                opp.expected_annual_saving = Decimal(str(year_n))
        for idx, attr in enumerate(
            ("saving_year_n", "saving_year_n1", "saving_year_n2", "saving_year_n3")
        ):
            yr = stp_fin["saving_per_year"][idx]
            setattr(opp, attr, Decimal(str(yr)) if yr is not None else None)
        # Calendar-year prorated estimate (start-date-aware) — {"2026": ..., ...}
        opp.saving_by_year = compute_saving_by_calendar_year(opp) or None
        if stp_fin["roi_full_year_pct"] is not None:
            opp.roi_percent = Decimal(str(stp_fin["roi_full_year_pct"]))
        if stp_fin["roi_period_pct"] is not None:
            opp.roi_period_percent = Decimal(str(stp_fin["roi_period_pct"]))
        if stp_fin["inventory_gap"] is not None:
            opp.cash_inventory_gap = Decimal(str(stp_fin["inventory_gap"]))
        if stp_fin["ap_gap"] is not None:
            opp.cash_ap_gap = Decimal(str(stp_fin["ap_gap"]))
        # Cash Impact = Inventory gap + AP gap (auto, read-only for STP types)
        if stp_fin["inventory_gap"] is not None or stp_fin["ap_gap"] is not None:
            opp.cash_impact = Decimal(
                str(
                    round(
                        (stp_fin["inventory_gap"] or 0.0) + (stp_fin["ap_gap"] or 0.0),
                        2,
                    )
                )
            )

        # P score — payback uses the 1st-YEAR run-rate (saving_year_n), not the
        # multi-year EBITDA Period now held in expected_annual_saving. Non-STP opps
        # have no saving_year_n, so fall back to expected_annual_saving (true annual).
        payback_annual = (
            opp.saving_year_n
            if opp.saving_year_n is not None
            else opp.expected_annual_saving
        )
        auto_p = auto_payback_score(
            float(opp.total_investment or 0) if opp.total_investment else None,
            float(payback_annual) if payback_annual else None,
        )
        if auto_p is not None:
            opp.payback_score = Decimal(str(auto_p))

        # L score — Phase 1+2+3 ONLY per Olivier: "durée phase 1, 2 et 3"
        # Phase 4 LLC happens AFTER production starts → not part of lead time
        total_weeks = sum(
            filter(None, [opp.phase1_weeks, opp.phase2_weeks, opp.phase3_weeks])
        )
        auto_l = auto_leadtime_score(float(total_weeks) if total_weeks else None)
        if auto_l is not None:
            opp.lead_time_score = Decimal(str(auto_l))

        # Auto-compute PLD priority
        p_score, p_cat = compute_priority(
            opp.payback_score, opp.lead_time_score, opp.difficulty_score
        )
        if p_score is not None:
            opp.priority_score = Decimal(str(p_score))
            opp.priority_category = p_cat

        opp.updated_at = datetime.utcnow()
        opp.updated_by = payload.changed_by

        # Auto-compute planned_end_date: last day of the final month in the period
        # e.g. start=Oct, duration=1  → 31 Oct
        #      start=Oct, duration=12 → 30 Sep next year
        if opp.planned_start_date and opp.duration_months:
            last_month_start = add_months(
                opp.planned_start_date, int(opp.duration_months) - 1
            )
            last_day = calendar.monthrange(
                last_month_start.year, last_month_start.month
            )[1]
            computed_end = last_month_start.replace(day=last_day)
            opp.planned_end_date = computed_end
            # Sync to linked project if not yet set
            for proj in opp.projects:
                if proj.planned_end_date is None:
                    proj.planned_end_date = computed_end
                    proj.updated_at = datetime.utcnow()

        # Keep the planned start in sync on the line (used as a reference / fallback),
        # but do NOT build rows from it — the tracking grid is anchored on the real
        # start only (see below). No rows exist before Phase 3, so nothing to rebuild.
        if planned_start_changed and opp.financial_lines:
            for line in opp.financial_lines:
                if line.status == "Active":
                    line.planned_start_date = payload.planned_start_date

        # Real start entered/changed (Phase 3) — generate the monthly tracking grid
        # ONCE from the real start. Rows are (re)generated only while no actuals have
        # been entered yet; once any actual exists the grid is immutable (no rebuild),
        # so realized savings can never be silently deleted.
        if real_start_changed and opp.financial_lines:
            new_start = payload.real_start_date
            duration = int(opp.duration_months or 12)
            for line in opp.financial_lines:
                if line.status == "Active":
                    await self._ensure_monthly_rows(line, opp, new_start, duration)
                    await self._recalculate_ytd(line.financial_line_id)

        # Expected saving changed (direct edit or STP price/qty recompute) without a
        # start change: keep the monthly grid in sync. _ensure_monthly_rows regenerates
        # ONLY while no actuals exist yet, so a saving correction in Phase 2 / early
        # Phase 3 flows through automatically; once actuals are entered the grid is
        # immutable here (re-baselining a live line is the Revise-Baseline tool's job).
        saving_changed = opp.expected_annual_saving != old_expected_saving
        if saving_changed and not real_start_changed and opp.financial_lines:
            duration = int(opp.duration_months or 12)
            new_annual = opp.expected_annual_saving or Decimal("0")
            for line in opp.financial_lines:
                if line.status != "Active":
                    continue
                # Committed lines (any actuals) are locked — only Revise may change
                # them; the governance check above already blocks the inputs anyway.
                if any(
                    m.actual_saving is not None for m in (line.monthly_financials or [])
                ):
                    continue
                if line.monthly_financials:
                    # Grid exists (Phase 3, pre-actuals) — regenerate (also re-syncs the
                    # baseline inside _ensure_monthly_rows).
                    anchor = line.real_start_date or compute_savings_start_date(opp)
                    if anchor:
                        await self._ensure_monthly_rows(line, opp, anchor, duration)
                        await self._recalculate_ytd(line.financial_line_id)
                else:
                    # No grid yet (Phase 2) — just keep the line baseline in sync.
                    line.expected_annual_saving = new_annual
                    line.budget_value = new_annual

        # Per-fiscal-year budgeting records (start-date prorated, override-preserving)
        await self._sync_budget_years(opp)

        await self.db.flush()
        await self.db.refresh(
            opp,
            ["projects", "financial_lines", "opp_documents", "budget_years", "plant"],
        )

        return opp

    # ------------------------------------------------------------------
    # Gate decision — core workflow engine
    # ------------------------------------------------------------------

    _STP_SNAPSHOT_FIELDS: tuple = (
        # Identity & status
        "opportunity_name", "opportunity_type", "phase_status", "status",
        "validation_decision", "idea_owner", "project_owner",
        "budget_year", "supplier_id", "plant_id",
        "change_mode",               # Standard | Silent — per-phase value at gate time
        # Dates
        "planned_start_date", "real_start_date",
        "planned_end_date",          # computed: planned_start + duration_months
        "val_date",                  # date of Phase 0 Go
        "duration_months",
        # STP price baseline
        "current_price", "proposed_price",
        "current_price_n1", "current_price_n2", "current_price_n3",
        "proposed_price_n1", "proposed_price_n2", "proposed_price_n3",
        # Quantities
        "annual_quantity_n1", "annual_quantity_n2", "annual_quantity_n3", "annual_quantity_n4",
        # Logistics
        "incoterms_before", "incoterms_after",
        "top_days_before", "top_days_after",
        "transit_days_before", "transit_days_after",
        "bonus_before", "bonus_after",
        "consignment_before", "consignment_after",
        # Costs
        "tooling_cost", "travel_cost", "qualification_cost", "other_cost",
        # Savings & ROI calculations
        "saving_year_n", "saving_year_n1", "saving_year_n2", "saving_year_n3",
        "period_saving",
        "saving_by_year",            # JSONB — prorated by calendar year
        "expected_annual_saving",
        "roi_percent", "roi_period_percent",
        "total_investment",
        # Cash
        "cash_impact", "cash_inventory_gap", "cash_ap_gap",
    )

    def _build_opportunity_snapshot(self, opp: "Opportunity") -> dict:
        result: dict = {}
        for field in self._STP_SNAPSHOT_FIELDS:
            val = getattr(opp, field, None)
            if val is None:
                continue
            if hasattr(val, "isoformat"):
                result[field] = val.isoformat()
            elif hasattr(val, "__round__"):
                result[field] = float(val)
            else:
                result[field] = val
        return result

    async def apply_gate_decision(
        self, opportunity_id: int, payload: GateDecisionRequest
    ) -> Opportunity:
        if payload.decision not in ("Go", "No Go", "Review"):
            raise AppException(
                422, "Decision must be Go, No Go, or Review.", "INVALID_DECISION"
            )

        opp = await self.get_opportunity(opportunity_id)
        now = datetime.utcnow()
        phase_before = opp.phase_status or "Phase 0"

        # Store only Phase 0 gate decision in validation_decision (the primary Go/No Go)
        # Later phases record their decision in comments to preserve Phase 0 outcome
        if (opp.phase_status or "Phase 0") in ("Assigned", "Phase 0"):
            opp.validation_decision = payload.decision
        opp.updated_at = now
        opp.updated_by = payload.decided_by

        # ------ NO GO → close opportunity ------
        if payload.decision == "No Go":
            opp.status = "Cancelled"
            opp.phase_status = "Closed"
            if payload.comments:
                opp.comments = (opp.comments or "") + f"\n[No Go] {payload.comments}"

            # B1 — zero out / unlock all per-year budget rows so a cancelled project
            # never inflates committed-budget totals on the Budgeting page.
            # Locked rows (director decision) are reset — the commitment is voided.
            for by in list(opp.budget_years or []):
                by.applicable_amount = Decimal("0")
                by.budget_status = "Empty"
                by.status_locked_at = None
                by.status_locked_by = None
                by.updated_at = now

            # B1/B3 — cascade to financial lines and recovery plans.
            for fl in list(opp.financial_lines or []):
                if fl.status == "Active":
                    fl.status = "Cancelled"
                    fl.updated_at = now
                    fl.updated_by = payload.decided_by
                # B3 — auto-close any open recovery plan so it stops appearing as
                # overdue on the Recovery Plans page.
                if fl.recovery_status and fl.recovery_status != "Done":
                    if fl.recovery_history:
                        fl.recovery_history = (fl.recovery_history + "\n").lstrip()
                    entry = (
                        f"[{now.strftime('%Y-%m-%d')} by system] "
                        f"Auto-closed — opportunity cancelled (No Go)."
                    )
                    fl.recovery_history = (
                        (fl.recovery_history or "") + entry
                    ).strip()
                    fl.recovery_status = "Done"
                    fl.updated_at = now

        # ------ REVIEW → needs rework, buyer/PM must resubmit ------
        elif payload.decision == "Review":
            opp.status = "Needs Rework"
            if payload.comments:
                opp.comments = (
                    (opp.comments or "")
                    + f"\n[Review — {datetime.utcnow().strftime('%Y-%m-%d')} by {payload.decided_by or 'reviewer'}] {payload.comments}"
                )

        # ------ GO → advance phase ------
        else:
            current_phase = opp.phase_status or "Phase 0"

            if current_phase in ("Assigned", "Phase 0"):
                # Phase 0 Go: validate the opportunity
                opp.phase_status = "Phase 1"
                opp.status = "Working on it"
                opp.val_date = now.date()
                if payload.comments:
                    opp.comments = (
                        opp.comments or ""
                    ) + f"\n[Phase 0 Go] {payload.comments}"

                # R2 — create Project for Sourcing / Technical Productivity
                if opp.opportunity_type not in NO_PROJECT_TYPES:
                    if not payload.project_manager:
                        raise AppException(
                            422,
                            "project_manager email is required for this opportunity type.",
                            "PM_REQUIRED",
                        )
                    opp.project_owner = payload.project_manager
                    if not opp.projects:
                        await self._create_project(opp, payload.project_manager)

            elif current_phase == "Phase 1":
                opp.phase_status = "Phase 2"
                opp.status = "Working on it"
                if payload.comments:
                    opp.comments = (
                        opp.comments or ""
                    ) + f"\n[Phase 1 Go] {payload.comments}"

            elif current_phase == "Phase 2":
                opp.phase_status = "Phase 3"
                opp.status = "Working on it"
                # Financial line is created here — Phase 2 validated → deployment.
                # Monthly rows are generated later, once the real start date is set
                # in Phase 3. Guard against duplicates on a Review → rework → Go cycle.
                if not opp.financial_lines:
                    await self._create_financial_line(opp)

            elif current_phase == "Phase 3":
                opp.phase_status = "Phase 4"
                opp.status = "Working on it"

            elif current_phase == "Phase 4":
                opp.phase_status = "Closed"
                opp.status = "Complete"

            # Advance linked project phase too
            for project in opp.projects:
                if project.phase_status != "Closed":
                    project.phase_status = opp.phase_status
                    project.gate_decision = "Go"
                    project.updated_at = now
                    project.updated_by = payload.decided_by

        # Phase change shifts the suggested per-year status (e.g. → "Budgeted" at Phase 3)
        await self._sync_budget_years(opp)

        _snap = OpportunityPhaseSnapshot(
            opportunity_id=opp.opportunity_id,
            phase_from=phase_before,
            phase_to=opp.phase_status,
            gate_decision=payload.decision,
            decided_by=payload.decided_by,
            decided_at=now,
            gate_comments=payload.comments,
            opportunity_snapshot=self._build_opportunity_snapshot(opp),
            created_at=now,
            created_by=payload.decided_by,
        )
        self.db.add(_snap)

        await self.db.flush()
        await self.db.refresh(
            opp,
            ["projects", "financial_lines", "opp_documents", "budget_years", "plant"],
        )
        return opp

    async def get_phase_history(self, opportunity_id: int) -> list:
        from sqlalchemy import select as _select
        res = await self.db.execute(
            _select(OpportunityPhaseSnapshot)
            .where(OpportunityPhaseSnapshot.opportunity_id == opportunity_id)
            .order_by(OpportunityPhaseSnapshot.decided_at)
        )
        return res.scalars().all()

    # ------------------------------------------------------------------
    # Start Phase 0 study (Assigned → Working on it)
    # ------------------------------------------------------------------

    async def start_study(
        self, opportunity_id: int, payload: StartStudyRequest
    ) -> Opportunity:
        opp = await self.get_opportunity(opportunity_id)
        if opp.status != "Assigned":
            raise AppException(
                422, "Only Assigned opportunities can be started.", "WRONG_STATUS"
            )
        opp.status = "Working on it"
        opp.phase_status = "Phase 0"
        opp.study_start_date = (
            datetime.utcnow().date()
        )  # Olivier: "ça me valide la date de l'opportunité"
        opp.updated_at = datetime.utcnow()
        opp.updated_by = payload.started_by
        opp.comments = (
            (opp.comments or "")
            + f"\n[Phase 0 started by {payload.started_by or 'system'} on {datetime.utcnow().strftime('%Y-%m-%d')}]"
        )
        await self.db.flush()
        await self.db.refresh(
            opp,
            ["projects", "financial_lines", "opp_documents", "budget_years", "plant"],
        )
        return opp

    # ------------------------------------------------------------------
    # Submit for PM validation (Phase 0 → Awaiting Validation)
    # ------------------------------------------------------------------

    async def submit_for_validation(
        self, opportunity_id: int, payload: SubmitForValidationRequest
    ) -> Opportunity:
        opp = await self.get_opportunity(opportunity_id)
        # A1 — phase-agnostic: allow re-submission from any phase when returned for
        # rework. The PM re-reviews whatever phase the project is currently at.
        # Guard: only in-progress or rework states may submit — not terminal states.
        allowed_statuses = ("Working on it", "Needs Rework")
        if opp.status not in allowed_statuses:
            raise AppException(
                422,
                f"Opportunity must be in one of {allowed_statuses} to submit for validation "
                f"(current status: '{opp.status}').",
                "WRONG_STATUS",
            )
        if opp.phase_status in ("Closed", "Assigned"):
            raise AppException(
                422,
                "Closed or unstarted opportunities cannot be submitted for validation.",
                "WRONG_PHASE",
            )

        opp.status = "Awaiting Validation"
        opp.validation_request_sent_at = datetime.utcnow()
        opp.validation_request_sent_by = payload.submitted_by
        opp.updated_at = datetime.utcnow()
        opp.updated_by = payload.submitted_by
        opp.comments = (opp.comments or "") + (
            f"\n[Submitted for PM validation by {payload.submitted_by or 'system'} on {datetime.utcnow().strftime('%Y-%m-%d')}]"
        )

        if payload.to_emails:
            try:
                body = _build_phase0_submit_email(opp, payload.message, None)
                non_stp = opp.opportunity_type in ("Negotiation", "Cash")
                if non_stp:
                    await send_email(
                        subject=f"[Phase 0 Review] Opportunity: {opp.opportunity_name}",
                        recipients=payload.to_emails,
                        body_html=body,
                        cc=payload.cc_emails or [],
                    )
                else:
                    import tempfile
                    import os

                    pdf_bytes = generate_stp_pdf(opp, phase=0)
                    safe = (opp.opportunity_name or "opp").replace(" ", "_")[:50]
                    with tempfile.NamedTemporaryFile(
                        delete=False, suffix=".pdf", prefix=f"STP_Phase0_{safe}_"
                    ) as tmp:
                        tmp.write(pdf_bytes)
                        tmp_path = tmp.name
                    try:
                        await send_email_with_attachment(
                            subject=f"[Phase 0 Review] Opportunity: {opp.opportunity_name}",
                            recipients=payload.to_emails,
                            body_html=body,
                            cc=payload.cc_emails or [],
                            attachment_path=tmp_path,
                            attachment_filename=f"STP_Phase0_{safe}.pdf",
                        )
                    finally:
                        os.unlink(tmp_path)
            except Exception as exc:
                logger.warning("Phase-0 submit email failed for opp %s: %s", opportunity_id, exc)

        await self.db.flush()
        await self.db.refresh(
            opp,
            ["projects", "financial_lines", "opp_documents", "budget_years", "plant"],
        )
        return opp

    # ------------------------------------------------------------------
    # Submit to Sourcing Committee (Phase 1 → Under Committee Review)
    # ------------------------------------------------------------------

    async def submit_to_committee(
        self, opportunity_id: int, payload: SubmitToCommitteeRequest
    ) -> Opportunity:
        opp = await self.get_opportunity(opportunity_id)
        if opp.phase_status != "Phase 1":
            raise AppException(
                422,
                "Only Phase 1 opportunities can be submitted to committee.",
                "WRONG_PHASE",
            )
        if opp.status not in ("Working on it", "Needs Rework"):
            raise AppException(
                422,
                "Opportunity must be 'Working on it' to submit to committee.",
                "WRONG_STATUS",
            )

        opp.status = "Under Committee Review"
        opp.updated_at = datetime.utcnow()
        opp.updated_by = payload.submitted_by
        committee = payload.committee_type or "Sourcing Committee"
        opp.comments = (opp.comments or "") + (
            f"\n[Submitted to {committee} by {payload.submitted_by or 'system'} on {datetime.utcnow().strftime('%Y-%m-%d')}]"
        )

        # Olivier: "je veux pas d'email là — je veux que le PM organise une réunion"
        # Email is optional: only sent if to_emails explicitly provided
        if payload.to_emails:
            try:
                body = _build_committee_email(opp, payload.message, committee)
                non_stp = opp.opportunity_type in ("Negotiation", "Cash")
                if non_stp:
                    await send_email(
                        subject=f"[Committee Review] Feasibility Study — {opp.opportunity_name}",
                        recipients=payload.to_emails,
                        body_html=body,
                        cc=payload.cc_emails or [],
                    )
                else:
                    import tempfile
                    import os

                    pdf_bytes = generate_stp_pdf(opp, phase=1)
                    safe = (opp.opportunity_name or "opp").replace(" ", "_")[:50]
                    with tempfile.NamedTemporaryFile(
                        delete=False, suffix=".pdf", prefix=f"STP_Phase1_{safe}_"
                    ) as tmp:
                        tmp.write(pdf_bytes)
                        tmp_path = tmp.name
                    try:
                        await send_email_with_attachment(
                            subject=f"[Committee Review] Feasibility Study — {opp.opportunity_name}",
                            recipients=payload.to_emails,
                            body_html=body,
                            cc=payload.cc_emails or [],
                            attachment_path=tmp_path,
                            attachment_filename=f"STP_Phase1_{safe}.pdf",
                        )
                    finally:
                        os.unlink(tmp_path)
            except Exception as exc:
                logger.warning("Committee email failed for opp %s: %s", opportunity_id, exc)

        await self.db.flush()
        await self.db.refresh(
            opp,
            ["projects", "financial_lines", "opp_documents", "budget_years", "plant"],
        )
        return opp

    # ------------------------------------------------------------------
    # Send validation-request email (Phase 0 → before gate)
    # ------------------------------------------------------------------

    async def send_validation_request(
        self, opportunity_id: int, payload: ValidationRequestPayload
    ) -> Opportunity:
        opp = await self.get_opportunity(opportunity_id)

        body_html = _build_validation_email(opp, payload.custom_message)
        try:
            await send_email(
                subject=f"[Validation Request] {opp.opportunity_name}",
                recipients=payload.to_emails,
                body_html=body_html,
                cc=payload.extra_cc_emails or [],
            )
        except Exception as exc:
            logger.warning("Validation-request email failed for opp %s: %s", opportunity_id, exc)

        opp.validation_request_sent_at = datetime.utcnow()
        opp.validation_request_sent_by = payload.sent_by
        opp.updated_at = datetime.utcnow()
        opp.updated_by = payload.sent_by

        await self.db.flush()
        await self.db.refresh(
            opp,
            ["projects", "financial_lines", "opp_documents", "budget_years", "plant"],
        )
        return opp

    # ------------------------------------------------------------------
    # Update monthly actual + EOY forecast  (R4, R11)
    # ------------------------------------------------------------------

    async def update_monthly_actual(
        self, month_id: int, payload: MonthlyActualUpdateRequest
    ) -> MonthlyFinancial:
        row = await self.get_monthly_row(month_id)
        line = await self.get_financial_line(row.financial_line_id)
        opp = await self.get_opportunity(line.opportunity_id)

        # Actuals can be captured for as long as the financial line is live and the
        # opportunity has reached execution. Savings frequently keep flowing through
        # Phase 4 (LLC) and after closure, so the lock follows the LINE's active life,
        # not the opp's current gate phase — otherwise the bulk of the realization
        # period could never be recorded once the gate advances. (Audit H2)
        if line.status != "Active":
            raise AppException(
                422,
                "Monthly actuals can only be edited while the financial line is Active.",
                "LINE_NOT_ACTIVE",
            )
        if opp.phase_status in ("Assigned", "Phase 0", "Phase 1", "Phase 2"):
            raise AppException(
                422,
                "Monthly actuals can only be edited once the opportunity reaches execution (Phase 3+).",
                "MONTHLY_ROWS_LOCKED_BEFORE_EXECUTION",
            )

        old_actual = row.actual_saving  # capture before overwrite for cumulated estimate
        _set_if(row, "actual_saving", payload.actual_saving)
        _set_if(row, "cash_actual", payload.cash_actual)

        # EOY Forecast validation: must be ≥ new cumulated actual
        # Olivier (04/06/2026): "si tu as mis Actual 200, elle peut pas avoir une end of
        # qui soit moins de 200 puisqu'elle a déjà 200"
        if payload.forecast_eoy_saving is not None:
            # Approximate new cumulated by adjusting old cumulated by the change in this month's actual
            old_cum = float(row.cumulated_actual or 0)
            old_act_val = float(old_actual or 0)
            new_act_val = float(row.actual_saving or 0)
            approx_new_cum = old_cum - old_act_val + new_act_val
            new_forecast = float(payload.forecast_eoy_saving)
            if new_forecast < approx_new_cum:
                raise AppException(
                    422,
                    f"EOY Forecast ({new_forecast:.0f}€) cannot be less than cumulated actual ({approx_new_cum:.0f}€). "
                    f"You have already realized {approx_new_cum:.0f}€ — the full-year projection must be at least that amount.",
                    "EOY_FORECAST_BELOW_ACTUAL",
                )
            _set_if(row, "forecast_eoy_saving", payload.forecast_eoy_saving)
        _set_if(row, "forecast_comment", payload.forecast_comment)
        _set_if(row, "comment", payload.comment)
        _set_if(row, "monthly_outcome", payload.monthly_outcome)
        row.updated_at = datetime.utcnow()
        row.updated_by = payload.updated_by

        await self._recalculate_ytd(row.financial_line_id)

        if payload.forecast_eoy_saving is not None:
            line.forecast_eoy_current = payload.forecast_eoy_saving
            line.forecast_eoy_last_update = datetime.utcnow().date()

        # Delay detection (R5): past month with no actual → alert
        await self._check_and_alert_delay(line, row, payload.updated_by)

        # Issue #3: monthly_outcome = "Escalate" → auto-escalate the financial line
        if payload.monthly_outcome == "Escalate" and not line.is_escalated:
            line.is_escalated = True
            line.escalated_at = datetime.utcnow()
            line.escalated_by = payload.updated_by
            line.escalation_reason = (
                f"Auto-escalated from monthly review "
                f"({row.period_month.strftime('%b %Y') if row.period_month else 'N/A'}): "
                f"actual={row.actual_saving}, expected={row.expected_saving}"
            )
            recipients = list(
                filter(None, [opp.purchasing_owner, opp.conversion_owner])
            )
            if recipients:
                try:
                    await send_email(
                        subject=f"[ESCALATION] Monthly review — {opp.opportunity_name}",
                        recipients=recipients,
                        body_html=_build_escalation_email(
                            opp, line, line.escalation_reason
                        ),
                    )
                except Exception as exc:
                    logger.warning("Auto-escalation email failed for line %s: %s", row.financial_line_id, exc)

        # monthly_outcome = "Recover" → prompt recovery (advisory — user fills details in UI)
        # No auto-action needed, recovery_status is set manually via /recovery endpoint

        await self.db.flush()
        await self.db.refresh(row)
        return row

    # ------------------------------------------------------------------
    # Escalation
    # ------------------------------------------------------------------

    async def escalate_financial_line(
        self, line_id: int, payload: EscalateRequest
    ) -> FinancialLine:
        line = await self.get_financial_line(line_id)
        now = datetime.utcnow()
        line.is_escalated = True
        line.escalated_at = now
        line.escalated_by = payload.escalated_by
        line.escalation_reason = payload.escalation_reason
        line.updated_at = now
        line.updated_by = payload.escalated_by

        # Get purchasing owner from opportunity for email
        opp = await self.get_opportunity(line.opportunity_id)
        recipients = list(
            filter(
                None,
                [
                    opp.purchasing_owner,
                    opp.conversion_owner,
                ]
                + (payload.extra_recipients or []),
            )
        )

        if recipients:
            try:
                await send_email(
                    subject=f"[ESCALATION] Opportunity: {opp.opportunity_name}",
                    recipients=recipients,
                    body_html=_build_escalation_email(
                        opp, line, payload.escalation_reason
                    ),
                )
            except Exception as exc:
                logger.warning("Manual escalation email failed for line %s: %s", line_id, exc)

        await self.db.flush()
        await self.db.refresh(line, ["monthly_financials"])
        return line

    async def deescalate_financial_line(
        self, line_id: int, updated_by: Optional[str]
    ) -> FinancialLine:
        line = await self.get_financial_line(line_id)
        line.is_escalated = False
        line.escalated_at = None
        line.escalated_by = None
        line.escalation_reason = None
        line.updated_at = datetime.utcnow()
        line.updated_by = updated_by
        await self.db.flush()
        await self.db.refresh(line, ["monthly_financials"])
        return line

    # ------------------------------------------------------------------
    # Recovery
    # ------------------------------------------------------------------

    async def set_recovery(
        self, line_id: int, payload: RecoveryUpdateRequest
    ) -> FinancialLine:
        line = await self.get_financial_line(line_id)
        now = datetime.utcnow()

        # Snapshot previous state into history before overwriting
        if line.recovery_status:
            amount_str = (
                f"€{float(line.recovery_amount):,.0f}" if line.recovery_amount else "—"
            )
            target_str = (
                str(line.recovery_target_date) if line.recovery_target_date else "—"
            )
            note_str = f'"{line.recovery_note}"' if line.recovery_note else "—"
            entry = (
                f"[{now.strftime('%Y-%m-%d')} by {payload.updated_by or 'system'}] "
                f"Status: {line.recovery_status} | Amount: {amount_str} | "
                f"Target: {target_str} | Note: {note_str}"
            )
            existing = line.recovery_history or ""
            line.recovery_history = (existing + "\n" + entry).strip()

        line.recovery_status = payload.recovery_status
        line.recovery_note = payload.recovery_note
        if payload.recovery_target_date is not None:
            line.recovery_target_date = payload.recovery_target_date
        if payload.recovery_amount is not None:
            line.recovery_amount = Decimal(str(payload.recovery_amount))
        line.recovery_updated_at = now
        line.recovery_updated_by = payload.updated_by
        line.updated_at = now
        line.updated_by = payload.updated_by
        # If recovery is done, clear escalation
        if payload.recovery_status == "Done":
            line.is_escalated = False
        await self.db.flush()
        await self.db.refresh(line, ["monthly_financials"])
        return line

    # ------------------------------------------------------------------
    # Complete financial line
    # ------------------------------------------------------------------

    async def complete_financial_line(
        self, line_id: int, payload: FinancialLineCompleteRequest
    ) -> FinancialLine:
        line = await self.get_financial_line(line_id)
        line.status = "Completed"
        line.updated_at = datetime.utcnow()
        line.updated_by = payload.completed_by
        if payload.comments:
            line.comments = (line.comments or "") + f"\n[Completed] {payload.comments}"
        await self.db.flush()
        await self.db.refresh(line, ["monthly_financials"])
        return line

    # ------------------------------------------------------------------
    # Delay detection helper
    # ------------------------------------------------------------------

    async def _check_and_alert_delay(
        self,
        line: FinancialLine,
        updated_row: MonthlyFinancial,
        updated_by: Optional[str],
    ) -> None:
        """Alert purchasing owner if a past month (after savings start date) has no actual.
        Olivier: months before planned_start_date are expected to be 0 — no alert for those."""
        if line.status != "Active":
            return
        today = date.today()
        # Only alert for months from savings start onwards (not before)
        savings_start = line.real_start_date or line.planned_start_date
        if savings_start is None:
            return
        result = await self.db.execute(
            select(MonthlyFinancial).where(
                MonthlyFinancial.financial_line_id == line.financial_line_id,
                MonthlyFinancial.period_month >= savings_start.replace(day=1),
                MonthlyFinancial.period_month < today.replace(day=1),
                MonthlyFinancial.actual_saving.is_(None),
            )
        )
        missing_rows = result.scalars().all()
        if not missing_rows:
            return

        opp = await self.get_opportunity(line.opportunity_id)
        recipients = list(filter(None, [opp.purchasing_owner, opp.conversion_owner]))
        if not recipients:
            return

        months_missing = [
            r.period_month.strftime("%b %Y") if r.period_month else "?"
            for r in missing_rows
        ]
        try:
            await send_email(
                subject=f"[Alert] Missing savings data — {opp.opportunity_name}",
                recipients=recipients,
                body_html=_build_delay_alert_email(opp, line, months_missing),
            )
        except Exception as exc:
            logger.warning("Delay-alert email failed for line %s: %s", line.financial_line_id, exc)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validation_status(opp: Opportunity) -> str:
        """Budgeting status — DERIVED from validation, never set manually.
        'Validate' once Phase 3 is validated (real start date entered, or the
        opportunity has moved to Phase 4 / Closed); otherwise 'In progress' (still
        an opportunity, before Phase 3 is confirmed)."""
        if opp.real_start_date is not None or opp.phase_status in ("Phase 4", "Closed"):
            return "Validate"
        return "In progress"

    async def _sync_budget_years(self, opp: Opportunity) -> None:
        """Recompute the per-fiscal-year budget rows from the SAME per-year savings as
        the STP calendar-year estimate (escalating windows), anchored on the savings
        start and capped at duration_months. When no STP prices exist, fall back to
        the flat expected_annual_saving repeated across the duration. Status is fully
        derived from validation (_validation_status) — no manual override.

        IMPORTANT: do not rely on `opp.budget_years` being preloaded. In async
        SQLAlchemy, touching an unloaded relationship here can trigger an implicit
        lazy-load outside the greenlet context (`MissingGreenlet`). Query the rows
        explicitly instead so the recompute path is safe in API, tests and batch
        flows alike."""
        duration = int(opp.duration_months or 0)
        anchor = compute_savings_start_date(opp)
        per_year = compute_stp_financials(opp)["saving_per_year"]
        if any(v is not None for v in per_year):
            windows = per_year  # STP escalating per-year savings
        elif opp.expected_annual_saving is not None:
            n_years = max(1, ceil(duration / 12)) if duration else 1
            windows = [float(opp.expected_annual_saving)] * n_years
        else:
            windows = []
        portions = compute_budget_year_portions(windows, anchor, duration or None)
        status = self._validation_status(opp)
        existing_rows = (
            (
                await self.db.execute(
                    select(OpportunityBudgetYear).where(
                        OpportunityBudgetYear.opportunity_id == opp.opportunity_id,
                        OpportunityBudgetYear.is_deleted.is_(False),
                    )
                )
            )
            .scalars()
            .all()
        )
        existing = {by.fiscal_year: by for by in existing_rows}
        seen = set()

        for p in portions:
            fy = p["fiscal_year"]
            seen.add(fy)
            amt = Decimal(str(p["amount"]))
            row = existing.get(fy)
            if row is not None:
                row.applicable_amount = amt
                row.portion_kind = p["kind"]
                row.suggested_status = status
                # Preserve a manual Create-Budget decision (locked); otherwise the row
                # defaults to "Opportunity" (a forecast gain in the pipeline) until the
                # director commits it to "Budgeted" or sets it to "Empty".
                if row.status_locked_at is None:
                    row.budget_status = "Opportunity"
            else:
                self.db.add(
                    OpportunityBudgetYear(
                        opportunity_id=opp.opportunity_id,
                        fiscal_year=fy,
                        applicable_amount=amt,
                        portion_kind=p["kind"],
                        suggested_status=status,
                        budget_status="Opportunity",
                    )
                )

        # Stale rows (duration shrank / dates cleared) — drop only if not locked.
        # A director-committed row (status_locked_at set) must never be silently deleted.
        for fy, row in existing.items():
            if fy not in seen:
                if row.status_locked_at is not None:
                    row.applicable_amount = Decimal("0")
                else:
                    await self.db.delete(row)

        await self.db.flush()

        final_rows = (
            (
                await self.db.execute(
                    select(OpportunityBudgetYear).where(
                        OpportunityBudgetYear.opportunity_id == opp.opportunity_id,
                        OpportunityBudgetYear.is_deleted.is_(False),
                    )
                )
            )
            .scalars()
            .all()
        )

        # Opportunity-level validation status is DERIVED from the same validation
        # state — no manual toggle. "Validate" → Budgeted (KPIs/baseline-lock);
        # else Empty.
        opp.validation_status = "Budgeted" if status == "Validate" else "Empty"
        if final_rows:
            opp.budget_year = Decimal(
                str(min(by.fiscal_year for by in final_rows))
            )
        else:
            opp.budget_year = None

        # Propagate the derived validation status onto the opportunity's financial lines.
        # line.budget_status is a denormalized copy set once at creation; without this
        # it never flips to "Budgeted" and every budgeted-track KPI reads empty. Query
        # the lines directly so a line just created in the same gate flow (not yet on
        # opp.financial_lines) is still synced. (Audit C2 — self-heals existing rows.)
        lines_result = await self.db.execute(
            select(FinancialLine).where(
                FinancialLine.opportunity_id == opp.opportunity_id,
                FinancialLine.status.in_(["Active", "Completed"]),
            )
        )
        for line in lines_result.scalars().all():
            line.budget_status = opp.validation_status
        await self.db.flush()

    async def _create_financial_line(self, opp: Opportunity) -> FinancialLine:
        line_name = f"{opp.opportunity_name}"
        duration = int(opp.duration_months or 12)
        # Anchor on when savings actually flow (after the phases), not the project
        # start — keeps the monthly profile + KPI year-split consistent with the
        # budgeting estimate. Real start (Phase 3) overrides this later via R9.
        start = (
            compute_savings_start_date(opp)
            or opp.planned_start_date
            or date.today().replace(day=1)
        )
        annual = opp.expected_annual_saving or Decimal("0")

        line = FinancialLine(
            opportunity_id=opp.opportunity_id,
            plant_id=opp.plant_id,
            line_name=line_name,
            component_name="Default",
            budget_status=opp.validation_status or "Empty",
            expected_annual_saving=annual,
            budget_value=annual,
            planned_start_date=start,
            duration_months=Decimal(str(duration)),
            status="Active",
            follower=opp.conversion_owner or opp.purchasing_owner,
        )
        self.db.add(line)
        await self.db.flush()

        # Monthly rows are NOT generated here. The line is created at Phase 2 Go, but
        # the tracking grid is built once — from the REAL start date entered in Phase 3
        # (see update_opportunity → _ensure_monthly_rows). This keeps the baseline
        # anchored on when savings actually flow and removes the destructive rebuild.
        return line

    async def create_component_line(
        self, opportunity_id: int, payload
    ) -> FinancialLine:
        """Gap 2 — add a component-specific FinancialLine to an existing opportunity.

        DISABLED by policy: the canonical model is one opportunity = one financial line
        (the STP estimate is single-price, and the KPI dashboard aggregates per line,
        so a second line the opportunity drawer cannot render would desync the views).
        Re-enable only as part of a full per-component build (per-PN STP inputs + a
        multi-line drawer). See audit follow-up #3.
        """
        opp = await self.get_opportunity(opportunity_id)
        if opp.validation_decision != "Go":
            raise AppException(
                422, "Can only add component lines after Phase 0 Go.", "NOT_VALIDATED"
            )

        # One financial line per opportunity. Block adding a second active line.
        existing_active = [
            fl for fl in opp.financial_lines if fl.status in ("Active", "Completed")
        ]
        if existing_active:
            raise AppException(
                422,
                "This opportunity already has a financial line. The current model is "
                "one financial line per opportunity (one STP = one opportunity = one "
                "line). Multi-component tracking is not enabled.",
                "ONE_LINE_PER_OPPORTUNITY",
            )

        start = (
            payload.planned_start_date
            or compute_savings_start_date(opp)
            or opp.planned_start_date
            or date.today().replace(day=1)
        )
        duration = payload.duration_months or int(opp.duration_months or 12)

        line = FinancialLine(
            opportunity_id=opportunity_id,
            plant_id=opp.plant_id,
            line_name=f"{payload.component_name} ({payload.component_pn or 'no PN'})",
            component_name=payload.component_name,
            component_pn=payload.component_pn,
            budget_status=opp.validation_status or "Empty",
            expected_annual_saving=payload.expected_annual_saving,
            budget_value=payload.expected_annual_saving,
            planned_start_date=start,
            duration_months=Decimal(str(duration)),
            status="Active",
            follower=opp.conversion_owner or opp.purchasing_owner,
        )
        self.db.add(line)
        await self.db.flush()

        await self._generate_monthly_profile(
            line, payload.expected_annual_saving, start, duration
        )
        line.updated_by = payload.added_by
        await self.db.flush()
        await self.db.refresh(line, ["monthly_financials"])
        return line

    # ------------------------------------------------------------------
    # Per-fiscal-year budgeting (status is derived, read-only)
    # ------------------------------------------------------------------

    async def list_budget_years(self, fiscal_year: int) -> list:
        """Flattened opportunity+budget-year rows for a given fiscal year, for the
        budgeting page."""
        result = await self.db.execute(
            select(OpportunityBudgetYear)
            .where(
                OpportunityBudgetYear.fiscal_year == fiscal_year,
                OpportunityBudgetYear.is_deleted.is_(False),
            )
            .options(
                selectinload(OpportunityBudgetYear.opportunity).selectinload(
                    Opportunity.plant
                )
            )
            .order_by(OpportunityBudgetYear.id)
        )
        items = []
        for r in result.scalars().all():
            opp = r.opportunity
            if opp is None or opp.is_deleted:
                continue
            # B1 — exclude cancelled / closed opportunities so directors never see
            # phantom budget commitments on dead projects.
            if opp.status == "Cancelled" or opp.phase_status == "Closed":
                continue
            items.append(
                {
                    "id": r.id,
                    "opportunity_id": opp.opportunity_id,
                    "opportunity_name": opp.opportunity_name,
                    "opportunity_type": opp.opportunity_type,
                    "plant_name": opp.plant.site_name if opp.plant else None,
                    "purchasing_owner": opp.purchasing_owner,
                    "phase_status": opp.phase_status,
                    "fiscal_year": r.fiscal_year,
                    "applicable_amount": float(r.applicable_amount)
                    if r.applicable_amount is not None
                    else None,
                    "currency": opp.currency or "EUR",
                    "fx_rate_to_eur": float(opp.fx_rate_to_eur) if opp.fx_rate_to_eur is not None else 1.0,
                    "applicable_amount_eur": round(
                        float(r.applicable_amount) * float(opp.fx_rate_to_eur or 1), 2
                    )
                    if r.applicable_amount is not None
                    else None,
                    "portion_kind": r.portion_kind,
                    "suggested_status": r.suggested_status,
                    "budget_status": r.budget_status,
                    "status_locked_at": r.status_locked_at.isoformat()
                    if r.status_locked_at
                    else None,
                    "status_locked_by": r.status_locked_by,
                }
            )
        return items

    async def assign_budget_year(
        self, fiscal_year: int, decisions: list, decided_by: Optional[str]
    ) -> dict:
        """Create-Budget decisions for a fiscal year (Option B — forward planning).

        `decisions` is a list of {opportunity_id, budget_status} with budget_status in
        Empty / Opportunity / Budgeted. Each listed row's per-year budget status is set
        and LOCKED so the per-save recompute (_sync_budget_years) won't revert it. Rows
        not listed are left unchanged. The validation state (suggested_status) is never
        touched — it stays the Validated / Forecast badge. Returns counts per status.
        """
        valid = {"Empty", "Opportunity", "Budgeted"}
        by_opp = {
            d["opportunity_id"]: d["budget_status"]
            for d in (decisions or [])
            if d.get("budget_status") in valid
        }
        rows = (
            (
                await self.db.execute(
                    select(OpportunityBudgetYear)
                    .where(
                        OpportunityBudgetYear.fiscal_year == fiscal_year,
                        OpportunityBudgetYear.is_deleted.is_(False),
                    )
                    .options(selectinload(OpportunityBudgetYear.opportunity))
                )
            )
            .scalars()
            .all()
        )

        now = datetime.utcnow()
        counts = {"Empty": 0, "Opportunity": 0, "Budgeted": 0}
        for r in rows:
            opp = r.opportunity
            if opp is None or opp.is_deleted:
                continue
            new_status = by_opp.get(opp.opportunity_id)
            if new_status is None:
                continue
            r.budget_status = new_status
            r.status_locked_at = now
            r.status_locked_by = decided_by
            counts[new_status] += 1

        await self.db.flush()

        # B2 — re-sync FinancialLine.budget_status immediately after a Create-Budget
        # decision so KPIs read the correct budgeted flag without waiting for the next
        # opportunity save.  Deduplicated: one _sync per opp even if multiple rows.
        synced_opp_ids: set = set()
        for r in rows:
            opp = r.opportunity
            if opp is None or opp.is_deleted:
                continue
            if by_opp.get(opp.opportunity_id) is None:
                continue
            if opp.opportunity_id not in synced_opp_ids:
                await self._sync_budget_years(opp)
                synced_opp_ids.add(opp.opportunity_id)

        return {"fiscal_year": fiscal_year, "counts": counts}

    async def _rebuild_monthly_profile(
        self,
        line: FinancialLine,
        annual_saving: Decimal,
        new_start: date,
        duration_months: int,
        is_period_total: bool = False,
        windows: Optional[list] = None,
    ) -> None:
        """R9 — rebuild the monthly profile from the real start date.

        Rows before the new start date no longer make business sense, so they are
        removed even if they previously contained actuals. From the new start date
        onward, entered actuals are preserved and only empty rows are regenerated.
        """
        # Remove any rows that sit before the new real start date.
        result = await self.db.execute(
            select(MonthlyFinancial).where(
                MonthlyFinancial.financial_line_id == line.financial_line_id,
                MonthlyFinancial.period_month < new_start,
            )
        )
        obsolete_rows = result.scalars().all()
        for row in obsolete_rows:
            await self.db.delete(row)
        await self.db.flush()

        result = await self.db.execute(
            select(MonthlyFinancial).where(
                MonthlyFinancial.financial_line_id == line.financial_line_id,
                MonthlyFinancial.actual_saving.is_(None),  # only delete rows with no actual
            )
        )
        empty_rows = result.scalars().all()
        for row in empty_rows:
            await self.db.delete(row)
        await self.db.flush()

        # Find the latest month that already has actuals
        result2 = await self.db.execute(
            select(MonthlyFinancial)
            .where(
                MonthlyFinancial.financial_line_id == line.financial_line_id,
                MonthlyFinancial.actual_saving.is_not(None),
                MonthlyFinancial.period_month >= new_start,
            )
            .order_by(MonthlyFinancial.period_month.desc())
        )
        last_actual = result2.scalars().first()

        # Start new rows from: the month after the last actual, or new_start (whichever is later)
        if last_actual and last_actual.period_month:
            rebuild_start = add_months(last_actual.period_month, 1)
            if new_start > rebuild_start:
                rebuild_start = new_start
        else:
            rebuild_start = new_start

        # How many months remain
        end_month = add_months(new_start, duration_months)
        months_remaining = 0
        cursor = rebuild_start
        while cursor < end_month:
            months_remaining += 1
            cursor = add_months(cursor, 1)

        if months_remaining > 0:
            # Window index must reflect each month's position from the savings start
            # (new_start), even though the rebuilt tail begins at rebuild_start (which
            # may be later when actuals are preserved).
            base_offset = 0
            cursor = new_start
            while cursor < rebuild_start:
                base_offset += 1
                cursor = add_months(cursor, 1)
            # Flat fallback (no windows) spreads over the remaining months.
            flat_annual_duration = (
                duration_months if is_period_total else months_remaining
            )
            monthlies = self._rounded_series(
                [
                    self._ideal_for_offset(
                        base_offset + i, windows, annual_saving, flat_annual_duration, is_period_total
                    )
                    for i in range(months_remaining)
                ]
            )
            new_rows = []
            for i in range(months_remaining):
                period = add_months(rebuild_start, i)
                new_rows.append(
                    MonthlyFinancial(
                        financial_line_id=line.financial_line_id,
                        period_month=period,
                        expected_saving=monthlies[i],
                    )
                )
            self.db.add_all(new_rows)

        # Update the financial line real_start_date
        line.real_start_date = new_start

        # If the rows that caused an auto-escalation were removed, clear the stale flag.
        result3 = await self.db.execute(
            select(MonthlyFinancial).where(
                MonthlyFinancial.financial_line_id == line.financial_line_id,
                MonthlyFinancial.monthly_outcome == "Escalate",
            )
        )
        has_escalated_rows = result3.scalars().first() is not None
        if (
            not has_escalated_rows
            and line.escalation_reason
            and line.escalation_reason.startswith("Auto-escalated from monthly review")
        ):
            line.is_escalated = False
            line.escalated_at = None
            line.escalated_by = None
            line.escalation_reason = None

        await self.db.flush()

    @staticmethod
    def _is_period(opp: Opportunity) -> bool:
        """STP types carry the multi-year EBITDA Period in expected_annual_saving."""
        return opp.opportunity_type in ("Sourcing", "Technical Productivity")

    @staticmethod
    def _stp_year_windows(opp: Opportunity) -> list:
        """Escalating per-year savings [Year N, N+1, N+2, N+3] derived from the STP
        prices/quantities — the SAME figures the Overview calendar-year split and the
        budget rows use (compute_stp_financials → saving_per_year). Building the monthly
        profile from these keeps the Financial tab consistent with the Overview.
        Empty for non-STP types (they have no per-year escalation)."""
        if opp.opportunity_type not in ("Sourcing", "Technical Productivity"):
            return []
        per_year = compute_stp_financials(opp).get("saving_per_year") or []
        return [float(w) for w in per_year if w is not None]

    def _ideal_for_offset(
        self,
        offset: int,
        windows: Optional[list],
        annual: Decimal,
        duration_months: int,
        is_period_total: bool,
    ) -> float:
        """UNROUNDED expected saving for the month `offset` months after the savings
        start (see _rounded_series for how these are rounded so they tie out).

        - STP with per-year windows: each 12-month window runs at window/12, so the
          monthly amount escalates year over year. Months past the last window → 0.
        - Otherwise (flat): a per-year rate (annual/12, or /duration when <12), or a
          period total with no window breakdown (period/duration).
        """
        if is_period_total and windows:
            yi = offset // 12
            if yi >= len(windows):
                return 0.0
            return float(windows[yi]) / 12.0
        if duration_months <= 0:
            return 0.0
        divisor = duration_months if is_period_total else min(duration_months, 12)
        return float(annual) / divisor

    @staticmethod
    def _rounded_series(ideals: List[float]) -> List[Decimal]:
        """Round each monthly amount to 2 decimals, but make the LAST month absorb the
        rounding residual so the series sums EXACTLY to round(sum(ideals), 2). This
        guarantees the monthly profile ties to the cent against its baseline (finance
        reconciliation requirement)."""
        if not ideals:
            return []
        out = [Decimal(str(round(v, 2))) for v in ideals]
        target = Decimal(str(round(sum(ideals), 2)))
        residual = target - sum(out)
        if residual != 0:
            out[-1] = (out[-1] + residual).quantize(Decimal("0.01"))
        return out

    def _monthly_expected(
        self, annual: Decimal, duration_months: int, is_period_total: bool = False
    ) -> Decimal:
        """Flat monthly expected (no per-year escalation). Used for cash rows and as
        the fallback when no STP per-year windows exist. See _expected_for_offset for
        the escalating STP profile."""
        if duration_months <= 0:
            return Decimal("0")
        divisor = duration_months if is_period_total else min(duration_months, 12)
        return round(annual / Decimal(str(divisor)), 2)

    async def _generate_monthly_profile(
        self,
        line: FinancialLine,
        annual_saving: Decimal,
        start_date: date,
        duration_months: int,
        cash_annual: Optional[Decimal] = None,
        is_period_total: bool = False,
        windows: Optional[list] = None,
    ) -> None:
        """Create one MonthlyFinancial row per month. Expected saving escalates per
        12-month STP window when `windows` is given; cash stays flat."""
        cash_monthly = (
            self._monthly_expected(cash_annual, duration_months)
            if cash_annual
            else None
        )
        monthlies = self._rounded_series(
            [
                self._ideal_for_offset(i, windows, annual_saving, duration_months, is_period_total)
                for i in range(duration_months)
            ]
        )
        rows: List[MonthlyFinancial] = []
        for i in range(duration_months):
            period = add_months(start_date, i)
            rows.append(
                MonthlyFinancial(
                    financial_line_id=line.financial_line_id,
                    period_month=period,
                    expected_saving=monthlies[i],
                    cash_expected=cash_monthly,
                )
            )
        self.db.add_all(rows)
        await self.db.flush()

    async def _ensure_monthly_rows(
        self,
        line: FinancialLine,
        opp: Opportunity,
        start_date: date,
        duration_months: int,
    ) -> None:
        """Build the monthly tracking grid once, anchored on the REAL start date.

        Sets the line's real start, then generates rows ONLY while the line has no
        actuals yet (so a mistyped start can still be corrected before any realization
        is recorded). Once any actual_saving exists the grid is left untouched —
        realized savings are never deleted. This replaces the old destructive R9
        rebuild: rows are created once, from the date savings actually start flowing.
        """
        line.real_start_date = start_date
        has_actuals = any(
            m.actual_saving is not None for m in (line.monthly_financials or [])
        )
        if has_actuals:
            return
        # No actuals yet → the baseline is still free (nothing committed). Keep the
        # line's baseline and budget in sync with the opportunity's current expected
        # saving before regenerating the grid, so the line/KPIs never go stale.
        new_annual = opp.expected_annual_saving or Decimal("0")
        line.expected_annual_saving = new_annual
        line.budget_value = new_annual
        # Drop any previously generated (empty) rows, then build from the real start.
        for m in list(line.monthly_financials or []):
            await self.db.delete(m)
        await self.db.flush()
        cash_annual = (
            opp.cash_impact
            if opp.opportunity_type in ("Negotiation", "Cash")
            else None
        )
        await self._generate_monthly_profile(
            line,
            opp.expected_annual_saving or Decimal("0"),
            start_date,
            duration_months,
            cash_annual=cash_annual,
            is_period_total=self._is_period(opp),
            windows=self._stp_year_windows(opp),
        )

    async def _create_project(self, opp: Opportunity, pm_email: str) -> Project:
        project = Project(
            opportunity_id=opp.opportunity_id,
            project_name=opp.opportunity_name,
            project_type=opp.opportunity_type,
            project_owner=pm_email,
            phase_status="Phase 1",
            gate_decision="Go",
            status="On time",
            plant_validation="Pending",
            comments=opp.comments,
        )
        self.db.add(project)
        await self.db.flush()
        return project

    # ------------------------------------------------------------------
    # STP revision approval (Phase 2 / Phase 3)
    # ------------------------------------------------------------------

    _STP_BASELINE_FIELDS = (
        "current_price", "proposed_price",
        "current_price_n1", "current_price_n2", "current_price_n3",
        "proposed_price_n1", "proposed_price_n2", "proposed_price_n3",
        "annual_quantity_n1", "annual_quantity_n2", "annual_quantity_n3", "annual_quantity_n4",
        "bonus_before", "bonus_after",
    )

    async def request_stp_revision(
        self,
        opportunity_id: int,
        payload: STPRevisionRequestPayload,
    ) -> Opportunity:
        """Buyer submits proposed STP price/volume changes for Purchasing Director approval.

        Current values remain active.  Proposed values are stored in
        `pending_stp_revision` JSONB; a preview of the resulting savings is computed
        and included so the Director can assess the impact before deciding.
        """
        opp = await self.get_opportunity(opportunity_id)

        if opp.opportunity_type not in ("Sourcing", "Technical Productivity"):
            raise AppException(422, "STP revision approval only applies to STP opportunity types.", "NOT_STP_TYPE")
        if opp.phase_status not in ("Phase 2", "Phase 3"):
            raise AppException(422, "STP revision approval is only available in Phase 2 and Phase 3.", "WRONG_PHASE")
        if opp.pending_stp_revision:
            raise AppException(409, "A revision request is already pending for this opportunity. The Director must decide before a new request can be submitted.", "REVISION_ALREADY_PENDING")

        # Collect only the fields actually provided by the buyer
        proposed: dict = {}
        for field in self._STP_BASELINE_FIELDS:
            val = getattr(payload, field, None)
            if val is not None:
                proposed[field] = float(val) if isinstance(val, Decimal) else val

        if not proposed:
            raise AppException(422, "At least one STP baseline field must be provided in the revision request.", "NO_FIELDS_PROVIDED")

        # Compute a savings preview by overlaying the proposed values on the current opportunity
        import types as _types
        proxy = _types.SimpleNamespace(**{
            f: getattr(opp, f) for f in self._STP_BASELINE_FIELDS + (
                "consignment_before", "consignment_after",
                "top_days_before", "top_days_after",
                "transit_days_before", "transit_days_after",
                "tooling_cost", "travel_cost", "qualification_cost", "other_cost",
                "opportunity_type",
            ) if hasattr(opp, f)
        })
        for field, value in proposed.items():
            setattr(proxy, field, Decimal(str(value)) if isinstance(value, (int, float)) else value)

        preview_fin = compute_stp_financials(proxy)
        preview_year_n = preview_fin.get("saving_per_year", [None])[0]
        if preview_year_n is not None and float(preview_year_n) < 0:
            raise AppException(
                422,
                f"The proposed values produce a negative Year-N saving ({float(preview_year_n):,.0f} €) — "
                "proposed_price exceeds current_price. Please review before submitting.",
                "STP_NEGATIVE_SAVING",
            )

        now = datetime.utcnow()
        per_year = preview_fin.get("saving_per_year") or [None, None, None, None]
        opp.pending_stp_revision = {
            "requested_by":    payload.requested_by,
            "requested_at":    now.isoformat(),
            "director_email":  payload.director_email,
            "note":            payload.note,
            "proposed_fields": proposed,
            "current_snapshot": {
                f: float(getattr(opp, f)) if getattr(opp, f) is not None else None
                for f in self._STP_BASELINE_FIELDS
            },
            "computed_preview": {
                "saving_year_n":  float(per_year[0]) if per_year[0] is not None else None,
                "saving_year_n1": float(per_year[1]) if per_year[1] is not None else None,
                "saving_year_n2": float(per_year[2]) if per_year[2] is not None else None,
                "saving_year_n3": float(per_year[3]) if per_year[3] is not None else None,
                "period_saving":  float(preview_fin["period_saving"]) if preview_fin.get("period_saving") is not None else None,
            },
        }
        opp.updated_at = now
        opp.updated_by = payload.requested_by

        # Notify Director by email
        try:
            body = _build_stp_revision_request_email(opp, payload, opp.pending_stp_revision["computed_preview"])
            await send_email(
                subject=f"[STP Revision Approval] {opp.opportunity_name}",
                recipients=[payload.director_email],
                body_html=body,
            )
        except Exception as exc:
            logger.warning("STP revision request email failed for opp %s: %s", opportunity_id, exc)

        await self.db.flush()
        await self.db.refresh(opp, ["projects", "financial_lines", "opp_documents", "budget_years", "plant"])
        return opp

    async def decide_stp_revision(
        self,
        opportunity_id: int,
        payload: STPRevisionDecisionPayload,
    ) -> Opportunity:
        """Purchasing Director approves or rejects a pending STP revision request.

        Approved  → proposed values applied, STP financials recomputed, monthly
                    profile rebuilt (if financial line active and no actuals yet).
        Rejected  → pending revision discarded, current values unchanged.
        Both      → audit entry appended to opp.comments, requester notified by email.
        """
        if payload.decision not in ("Approved", "Rejected"):
            raise AppException(422, "Decision must be 'Approved' or 'Rejected'.", "INVALID_DECISION")

        opp = await self.get_opportunity(opportunity_id)
        if not opp.pending_stp_revision:
            raise AppException(404, "No pending STP revision found for this opportunity.", "NO_PENDING_REVISION")

        pending = opp.pending_stp_revision
        now = datetime.utcnow()
        stamp = f"[{now.strftime('%Y-%m-%d')} by {payload.decided_by or 'Director'}]"

        if payload.decision == "Approved":
            # Apply proposed field values to the opportunity
            for field, value in (pending.get("proposed_fields") or {}).items():
                if value is not None:
                    setattr(opp, field, Decimal(str(value)) if isinstance(value, (int, float)) else value)

            # Recompute STP financials — same chain as update_opportunity
            stp_fin = compute_stp_financials(opp)
            if stp_fin["period_saving"] is not None:
                opp.period_saving = Decimal(str(stp_fin["period_saving"]))
                year_n = stp_fin["saving_per_year"][0]
                if year_n is not None:
                    opp.expected_annual_saving = Decimal(str(year_n))
            for idx, attr in enumerate(("saving_year_n", "saving_year_n1", "saving_year_n2", "saving_year_n3")):
                yr = stp_fin["saving_per_year"][idx]
                setattr(opp, attr, Decimal(str(yr)) if yr is not None else None)
            opp.saving_by_year = compute_saving_by_calendar_year(opp) or None

            # Rebuild monthly profile if Phase 3, line is active, and no actuals yet
            if opp.phase_status == "Phase 3":
                for fl in (opp.financial_lines or []):
                    if fl.status != "Active":
                        continue
                    has_actuals = any(m.actual_saving is not None for m in (fl.monthly_financials or []))
                    if not has_actuals and fl.real_start_date:
                        stp_windows = self._stp_year_windows(opp)
                        await self._rebuild_monthly_profile(
                            fl, opp.expected_annual_saving or Decimal("0"),
                            fl.real_start_date, int(fl.duration_months or 12),
                            is_period_total=True, windows=stp_windows,
                        )

            opp.comments = (opp.comments or "") + (
                f"\n{stamp} STP revision APPROVED. "
                f"Reason: {payload.note or 'N/A'}. "
                f"Proposed by: {pending.get('requested_by', 'unknown')}."
            )
        else:
            opp.comments = (opp.comments or "") + (
                f"\n{stamp} STP revision REJECTED. "
                f"Reason: {payload.note or 'N/A'}. "
                f"Proposed by: {pending.get('requested_by', 'unknown')}."
            )

        opp.pending_stp_revision = None
        opp.updated_at = now
        opp.updated_by = payload.decided_by

        # Notify requester
        requester_email = pending.get("requested_by")
        if requester_email and "@" in requester_email:
            try:
                body = _build_stp_revision_decision_email(opp, payload, pending)
                await send_email(
                    subject=f"[STP Revision {payload.decision}] {opp.opportunity_name}",
                    recipients=[requester_email],
                    body_html=body,
                )
            except Exception as exc:
                logger.warning("STP revision decision email failed for opp %s: %s", opportunity_id, exc)

        await self.db.flush()
        await self.db.refresh(opp, ["projects", "financial_lines", "opp_documents", "budget_years", "plant"])
        return opp

    # ------------------------------------------------------------------
    # Document management
    # ------------------------------------------------------------------

    async def revise_financial_line_baseline(
        self,
        line_id: int,
        revised_saving: Decimal,
        note: Optional[str],
        revised_by: Optional[str],
    ) -> FinancialLine:
        """Phase 1 or Phase 3 — revise expected_annual_saving, rebuild monthly profile, keep budget_value."""
        line = await self.get_financial_line(line_id)
        if line.status != "Active":
            raise AppException(
                422, "Can only revise an active financial line.", "LINE_NOT_ACTIVE"
            )

        old_saving = line.expected_annual_saving or Decimal("0")
        line.expected_annual_saving = revised_saving
        # budget_value stays unchanged — it is the original budget commitment
        line.comments = (line.comments or "") + (
            f"\n[Baseline revised {datetime.utcnow().strftime('%Y-%m-%d')} by {revised_by or 'system'}] "
            f"€{old_saving:,.0f} → €{revised_saving:,.0f}. Reason: {note or 'N/A'}"
        )
        line.updated_at = datetime.utcnow()
        line.updated_by = revised_by

        duration = int(line.duration_months or 12)
        start = (
            line.real_start_date
            or line.planned_start_date
            or date.today().replace(day=1)
        )
        # D1 — preserve STP escalating window structure on revision.
        # For STP types the per-year windows are derived from the stored STP formulas
        # (prices × quantities). Passing is_period_total=True + windows keeps the
        # Year-N / N+1 / N+2 / N+3 escalation intact after a revision, instead of
        # collapsing it to a flat annual/12 profile that under-states later years.
        # For flat types (Negotiation / Cash) the existing flat behaviour is preserved.
        opp_result = await self.db.execute(
            select(Opportunity).where(Opportunity.opportunity_id == line.opportunity_id)
        )
        opp = opp_result.scalar_one_or_none()
        is_stp = self._is_period(opp) if opp else False
        stp_windows = self._stp_year_windows(opp) if is_stp else None
        await self._rebuild_monthly_profile(
            line, revised_saving, start, duration,
            is_period_total=is_stp,
            windows=stp_windows,
        )
        await self._recalculate_ytd(line_id)

        await self.db.flush()
        await self.db.refresh(line, ["monthly_financials"])
        return line

    async def update_project(self, project_id: int, payload) -> "Project":
        result = await self.db.execute(
            select(Project).where(Project.project_id == project_id)
        )
        proj = result.scalar_one_or_none()
        if proj is None:
            raise AppException(404, "Project not found", "PROJECT_NOT_FOUND")

        for field in (
            "project_owner",
            "status",
            "plant_validation",
            "planned_end_date",
            "actual_end_date",
            "comments",
            "phase_output_notes",
            "off_tool_date",
            "committee_review_date",
            "committee_members",
        ):
            val = getattr(payload, field, None)
            if val is not None:
                setattr(proj, field, val)

        proj.updated_at = datetime.utcnow()
        proj.updated_by = payload.updated_by
        await self.db.flush()
        await self.db.refresh(proj)
        return proj

    async def list_documents(self, opportunity_id: int) -> List[OpportunityDocument]:
        await self.get_opportunity(opportunity_id)  # 404 guard
        result = await self.db.execute(
            select(OpportunityDocument)
            .where(OpportunityDocument.opportunity_id == opportunity_id)
            .order_by(OpportunityDocument.created_at.desc())
        )
        return list(result.scalars().all())

    async def upload_document(
        self,
        opportunity_id: int,
        file,
        phase_label: str,
        notes: Optional[str],
        uploaded_by: Optional[str],
    ) -> OpportunityDocument:
        await self.get_opportunity(opportunity_id)
        upload = await upload_opportunity_document(file, opportunity_id, phase_label)
        doc = OpportunityDocument(
            opportunity_id=opportunity_id,
            phase_label=phase_label or "General",
            file_name=upload.get("file_name"),
            original_file_name=upload.get("original_file_name"),
            file_url=upload.get("file_url"),
            mime_type=upload.get("mime_type"),
            file_size=upload.get("file_size"),
            uploaded_by=uploaded_by,
            notes=notes,
        )
        self.db.add(doc)
        await self.db.flush()
        await self.db.refresh(doc)
        return doc

    async def delete_document(self, doc_id: int) -> None:
        result = await self.db.execute(
            select(OpportunityDocument).where(OpportunityDocument.doc_id == doc_id)
        )
        doc = result.scalar_one_or_none()
        if doc is None:
            raise AppException(404, "Document not found", "DOC_NOT_FOUND")
        if doc.file_url:
            blob_name = _extract_blob_name(doc.file_url)
            if blob_name:
                try:
                    await delete_blob(blob_name)
                except Exception as exc:
                    logger.warning("Blob delete failed for %s: %s", blob_name, exc)
        await self.db.delete(doc)
        await self.db.flush()

    # ------------------------------------------------------------------
    # Suppliers by plant
    # ------------------------------------------------------------------

    async def get_suppliers_by_plant(self, plant_id: int) -> list:
        result = await self.db.execute(
            select(SupplierUnit)
            .join(
                SupplierSiteRelation,
                SupplierSiteRelation.id_supplier_unit == SupplierUnit.id_supplier_unit,
            )
            .where(
                SupplierSiteRelation.id_site == plant_id,
                SupplierUnit.is_deleted.is_(False),
            )
            .options(selectinload(SupplierUnit.group))
            .order_by(SupplierUnit.id_supplier_unit)
        )
        units = result.scalars().all()
        return [
            {
                "id_supplier_unit": u.id_supplier_unit,
                "supplier_code": u.supplier_code,
                "group_name": u.group.nom if u.group else None,
                "city": u.city,
                "country": u.country,
            }
            for u in units
        ]

    async def _recalculate_ytd(self, financial_line_id: int) -> None:
        """Recalculate monthly cumulated fields AND push totals back to FinancialLine.

        delta_vs_expected_ytd = sum(actual) - sum(expected) for past months where
        actual_saving IS NOT NULL. Months with no data entered are excluded so that
        "not yet entered" is not silently treated as zero savings.
        cumulated_real_saving = total actuals entered (all time).
        """
        result = await self.db.execute(
            select(MonthlyFinancial)
            .where(MonthlyFinancial.financial_line_id == financial_line_id)
            .order_by(MonthlyFinancial.period_month)
        )
        rows = list(result.scalars().all())
        today_first = date.today().replace(day=1)

        cum_exp = Decimal("0")
        cum_act = Decimal("0")
        ytd_exp = Decimal("0")
        ytd_act = Decimal("0")

        for row in rows:
            cum_exp += row.expected_saving or Decimal("0")
            row.cumulated_expected = cum_exp
            if row.actual_saving is not None:
                cum_act += row.actual_saving
                row.delta_vs_expected = row.actual_saving - (row.expected_saving or Decimal("0"))
            else:
                row.delta_vs_expected = None
            # Always write cumulated_actual so gap rows don't show stale values
            row.cumulated_actual = cum_act if cum_act else None

            # YTD delta: only count months where actual data was entered
            if row.period_month and row.period_month <= today_first and row.actual_saving is not None:
                ytd_exp += row.expected_saving or Decimal("0")
                ytd_act += row.actual_saving

        # Also accumulate cash actuals (Gap 3)
        cum_cash = Decimal("0")
        for row in rows:
            if row.cash_actual is not None:
                cum_cash += row.cash_actual
                row.cumulated_cash_actual = cum_cash

        # Push totals back to the FinancialLine header
        line_result = await self.db.execute(
            select(FinancialLine).where(
                FinancialLine.financial_line_id == financial_line_id
            )
        )
        line = line_result.scalar_one_or_none()
        if line is not None:
            line.cumulated_real_saving = cum_act
            line.delta_vs_expected_ytd = ytd_act - ytd_exp

        await self.db.flush()


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _build_budget_confirmed_email(opp: Opportunity) -> str:
    saving = (
        f"€{opp.expected_annual_saving:,.0f}" if opp.expected_annual_saving else "N/A"
    )
    plant = opp.plant.site_name if opp.plant else "N/A"
    budget_year = str(int(opp.budget_year)) if opp.budget_year else "N/A"
    confirmer = opp.budget_confirmed_by or "N/A"
    confirmed_at = (
        opp.budget_confirmed_at.strftime("%d %b %Y %H:%M")
        if opp.budget_confirmed_at
        else "N/A"
    )
    end_date = (
        opp.planned_end_date.strftime("%d %b %Y") if opp.planned_end_date else "N/A"
    )
    start_date = (
        opp.planned_start_date.strftime("%d %b %Y") if opp.planned_start_date else "N/A"
    )
    return f"""
    <html><body style="font-family:Arial,sans-serif;color:#111827;max-width:620px;margin:0 auto">
      <div style="background:#065f46;padding:20px 24px;border-radius:8px 8px 0 0">
        <h2 style="color:#fff;margin:0;font-size:18px">✅ Opportunity Budgeted</h2>
        <p style="color:#6ee7b7;margin:4px 0 0;font-size:13px">Purchasing Value Management — Budget Confirmation</p>
      </div>
      <div style="padding:24px;border:1px solid #d1fae5;border-top:none;border-radius:0 0 8px 8px">
        <p>The following opportunity has been <strong>confirmed as Budgeted</strong> for {budget_year}:</p>
        <table style="border-collapse:collapse;width:100%;font-size:13px;margin:16px 0">
          <tr><td style="background:#ecfdf5;font-weight:600;padding:8px 12px;width:38%">Opportunity</td><td style="padding:8px 12px;border-bottom:1px solid #d1fae5">{opp.opportunity_name}</td></tr>
          <tr><td style="background:#ecfdf5;font-weight:600;padding:8px 12px">Type</td><td style="padding:8px 12px;border-bottom:1px solid #d1fae5">{opp.opportunity_type}</td></tr>
          <tr><td style="background:#ecfdf5;font-weight:600;padding:8px 12px">Plant</td><td style="padding:8px 12px;border-bottom:1px solid #d1fae5">{plant}</td></tr>
          <tr><td style="background:#ecfdf5;font-weight:600;padding:8px 12px">Expected Annual Saving</td><td style="padding:8px 12px;border-bottom:1px solid #d1fae5;font-weight:700;color:#065f46">{saving}</td></tr>
          <tr><td style="background:#ecfdf5;font-weight:600;padding:8px 12px">Budget Year</td><td style="padding:8px 12px;border-bottom:1px solid #d1fae5">{budget_year}</td></tr>
          <tr><td style="background:#ecfdf5;font-weight:600;padding:8px 12px">Planned Start → End</td><td style="padding:8px 12px;border-bottom:1px solid #d1fae5">{start_date} → {end_date}</td></tr>
          <tr><td style="background:#ecfdf5;font-weight:600;padding:8px 12px">Confirmed by</td><td style="padding:8px 12px;border-bottom:1px solid #d1fae5">{confirmer}</td></tr>
          <tr><td style="background:#ecfdf5;font-weight:600;padding:8px 12px">Confirmed at</td><td style="padding:8px 12px">{confirmed_at} UTC</td></tr>
        </table>
        <p style="color:#6b7280;font-size:11px;margin-top:24px">Avocarbon · Purchasing Value Management</p>
      </div>
    </body></html>
    """


def _set_if(obj, attr: str, value) -> None:
    """Set attribute only when value is not None."""
    if value is not None:
        setattr(obj, attr, value)


def _build_escalation_email(opp: Opportunity, line: FinancialLine, reason: str) -> str:
    actual = (
        f"€{line.cumulated_real_saving:,.0f}" if line.cumulated_real_saving else "€0"
    )
    expected = (
        f"€{line.expected_annual_saving:,.0f}" if line.expected_annual_saving else "N/A"
    )
    delta = (
        f"€{line.delta_vs_expected_ytd:,.0f}" if line.delta_vs_expected_ytd else "N/A"
    )
    return f"""
    <html><body style="font-family:Arial,sans-serif;color:#111827;max-width:620px;margin:0 auto">
      <div style="background:#dc2626;padding:20px 24px;border-radius:8px 8px 0 0">
        <h2 style="color:#fff;margin:0;font-size:18px">⚠ Escalation — Purchasing Value</h2>
      </div>
      <div style="padding:24px;border:1px solid #fecaca;border-top:none;border-radius:0 0 8px 8px">
        <p>The following opportunity has been escalated and requires management attention:</p>
        <table style="border-collapse:collapse;width:100%;font-size:13px;margin:16px 0">
          <tr><td style="background:#fef2f2;font-weight:600;padding:8px 12px;width:38%">Opportunity</td><td style="padding:8px 12px;border-bottom:1px solid #fee2e2">{opp.opportunity_name}</td></tr>
          <tr><td style="background:#fef2f2;font-weight:600;padding:8px 12px">Type</td><td style="padding:8px 12px;border-bottom:1px solid #fee2e2">{opp.opportunity_type}</td></tr>
          <tr><td style="background:#fef2f2;font-weight:600;padding:8px 12px">Expected Annual</td><td style="padding:8px 12px;border-bottom:1px solid #fee2e2">{expected}</td></tr>
          <tr><td style="background:#fef2f2;font-weight:600;padding:8px 12px">Actual YTD</td><td style="padding:8px 12px;border-bottom:1px solid #fee2e2">{actual}</td></tr>
          <tr><td style="background:#fef2f2;font-weight:600;padding:8px 12px">Delta YTD</td><td style="padding:8px 12px;color:#dc2626;font-weight:700;border-bottom:1px solid #fee2e2">{delta}</td></tr>
          <tr><td style="background:#fef2f2;font-weight:600;padding:8px 12px">Escalation reason</td><td style="padding:8px 12px">{reason}</td></tr>
        </table>
        <p style="color:#6b7280;font-size:11px">Avocarbon · Purchasing Value Management</p>
      </div>
    </body></html>
    """


def _build_delay_alert_email(
    opp: Opportunity, line: FinancialLine, months_missing: list
) -> str:
    months_str = ", ".join(months_missing)
    return f"""
    <html><body style="font-family:Arial,sans-serif;color:#111827;max-width:620px;margin:0 auto">
      <div style="background:#d97706;padding:20px 24px;border-radius:8px 8px 0 0">
        <h2 style="color:#fff;margin:0;font-size:18px">⚠ Missing Savings Data Alert</h2>
      </div>
      <div style="padding:24px;border:1px solid #fde68a;border-top:none;border-radius:0 0 8px 8px">
        <p>Monthly actual savings have not been entered for the following periods on <strong>{opp.opportunity_name}</strong>:</p>
        <p style="background:#fffbeb;padding:12px;border-radius:6px;font-weight:600;color:#92400e">{months_str}</p>
        <p>Please update the financial tracking as soon as possible to keep the monthly review accurate.</p>
        <p style="color:#6b7280;font-size:11px">Avocarbon · Purchasing Value Management</p>
      </div>
    </body></html>
    """


def _build_phase0_submit_email(
    opp: Opportunity, message: Optional[str], committee_type=None
) -> str:
    saving = (
        f"€{opp.expected_annual_saving:,.0f}" if opp.expected_annual_saving else "N/A"
    )
    cash = f"€{opp.cash_impact:,.0f}" if opp.cash_impact else "N/A"
    pld = (
        f"{opp.priority_score} ({opp.priority_category})"
        if opp.priority_score
        else "N/A"
    )
    extra = (
        f"<p style='color:#374151;font-style:italic'>{message}</p>" if message else ""
    )
    return f"""
    <html><body style="font-family:Arial,sans-serif;color:#111827;max-width:620px;margin:0 auto">
      <div style="background:#1e3a5f;padding:20px 24px;border-radius:8px 8px 0 0">
        <h2 style="color:#fff;margin:0;font-size:18px">Phase 0 Gate Review Request</h2>
        <p style="color:#93c5fd;margin:4px 0 0;font-size:13px">Opportunity Study — Purchasing</p>
      </div>
      <div style="padding:24px;border:1px solid #dde3ec;border-top:none;border-radius:0 0 8px 8px">
        <p>A Phase 0 Opportunity Study has been completed and requires your <strong>Go / No Go / Review</strong> decision:</p>
        {extra}
        <table style="border-collapse:collapse;width:100%;font-size:13px;margin:16px 0">
          <tr><td style="background:#f5f8fc;font-weight:600;padding:8px 12px;width:38%">Opportunity</td><td style="padding:8px 12px;border-bottom:1px solid #eef1f6">{opp.opportunity_name}</td></tr>
          <tr><td style="background:#f5f8fc;font-weight:600;padding:8px 12px">Type</td><td style="padding:8px 12px;border-bottom:1px solid #eef1f6">{opp.opportunity_type}</td></tr>
          <tr><td style="background:#f5f8fc;font-weight:600;padding:8px 12px">Pilot (Idea owner)</td><td style="padding:8px 12px;border-bottom:1px solid #eef1f6">{opp.idea_owner}</td></tr>
          <tr><td style="background:#f5f8fc;font-weight:600;padding:8px 12px">Est. Annual Saving</td><td style="padding:8px 12px;border-bottom:1px solid #eef1f6">{saving}</td></tr>
          <tr><td style="background:#f5f8fc;font-weight:600;padding:8px 12px">Cash Impact</td><td style="padding:8px 12px;border-bottom:1px solid #eef1f6">{cash}</td></tr>
          <tr><td style="background:#f5f8fc;font-weight:600;padding:8px 12px">PLD Priority</td><td style="padding:8px 12px;border-bottom:1px solid #eef1f6">{pld}</td></tr>
          <tr><td style="background:#f5f8fc;font-weight:600;padding:8px 12px">Change Mode</td><td style="padding:8px 12px;border-bottom:1px solid #eef1f6">{opp.change_mode or "To be confirmed"}</td></tr>
          <tr><td style="background:#f5f8fc;font-weight:600;padding:8px 12px">Plant</td><td style="padding:8px 12px">{opp.plant.site_name if opp.plant else "N/A"}</td></tr>
        </table>
        {f'<div style="background:#f5f8fc;padding:12px;border-radius:6px;font-size:12px"><strong>Assumptions:</strong> {opp.assumptions_summary}</div>' if opp.assumptions_summary else ""}
        <p style="color:#6b7280;font-size:11px;margin-top:24px">Please apply your decision (Go / No Go / Review) in the Purchasing Value Management system.<br>Avocarbon · Purchasing</p>
      </div>
    </body></html>"""


def _build_committee_email(
    opp: Opportunity, message: Optional[str], committee_type: str
) -> str:
    saving = (
        f"€{opp.expected_annual_saving:,.0f}" if opp.expected_annual_saving else "N/A"
    )
    pld = (
        f"{opp.priority_score} ({opp.priority_category})"
        if opp.priority_score
        else "N/A"
    )
    extra = (
        f"<p style='color:#374151;font-style:italic'>{message}</p>" if message else ""
    )
    pm = opp.project_owner or opp.purchasing_owner or "N/A"
    return f"""
    <html><body style="font-family:Arial,sans-serif;color:#111827;max-width:620px;margin:0 auto">
      <div style="background:#1d4ed8;padding:20px 24px;border-radius:8px 8px 0 0">
        <h2 style="color:#fff;margin:0;font-size:18px">Phase 1 Gate Review — {committee_type}</h2>
        <p style="color:#bfdbfe;margin:4px 0 0;font-size:13px">Feasibility Study Review — CEO · COO · Plant Manager · CDP · Purchasing</p>
      </div>
      <div style="padding:24px;border:1px solid #dbeafe;border-top:none;border-radius:0 0 8px 8px">
        <p>The Phase 1 Feasibility Study for the following opportunity has been completed and requires your <strong>Go / No Go / Review</strong> committee decision:</p>
        {extra}
        <table style="border-collapse:collapse;width:100%;font-size:13px;margin:16px 0">
          <tr><td style="background:#eff6ff;font-weight:600;padding:8px 12px;width:38%">Opportunity</td><td style="padding:8px 12px;border-bottom:1px solid #dbeafe">{opp.opportunity_name}</td></tr>
          <tr><td style="background:#eff6ff;font-weight:600;padding:8px 12px">Type</td><td style="padding:8px 12px;border-bottom:1px solid #dbeafe">{opp.opportunity_type}</td></tr>
          <tr><td style="background:#eff6ff;font-weight:600;padding:8px 12px">Project Manager</td><td style="padding:8px 12px;border-bottom:1px solid #dbeafe">{pm}</td></tr>
          <tr><td style="background:#eff6ff;font-weight:600;padding:8px 12px">Est. Annual Saving</td><td style="padding:8px 12px;border-bottom:1px solid #dbeafe">{saving}</td></tr>
          <tr><td style="background:#eff6ff;font-weight:600;padding:8px 12px">PLD Priority</td><td style="padding:8px 12px;border-bottom:1px solid #dbeafe">{pld}</td></tr>
          <tr><td style="background:#eff6ff;font-weight:600;padding:8px 12px">Change Mode</td><td style="padding:8px 12px">{opp.change_mode or "To be confirmed"}</td></tr>
        </table>
        {f'<div style="background:#eff6ff;padding:12px;border-radius:6px;font-size:12px"><strong>Assumptions:</strong> {opp.assumptions_summary}</div>' if opp.assumptions_summary else ""}
        <p style="color:#6b7280;font-size:11px;margin-top:24px">Please record your decision (Go / No Go / Review) in the Purchasing Value Management system.<br>Avocarbon · Purchasing</p>
      </div>
    </body></html>"""


def _build_stp_revision_request_email(opp: Opportunity, payload, preview: dict) -> str:
    def _fmt(v): return f"€{v:,.0f}" if v is not None else "N/A"
    rows = ""
    labels = {
        "current_price": "Current Price (Year N)", "proposed_price": "Proposed Price (Year N)",
        "current_price_n1": "Current Price N+1", "proposed_price_n1": "Proposed Price N+1",
        "annual_quantity_n1": "Qty Year N", "annual_quantity_n2": "Qty Year N+1",
        "bonus_before": "Bonus Before", "bonus_after": "Bonus After",
    }
    for field, label in labels.items():
        val = payload.proposed_fields.get(field) if hasattr(payload, "proposed_fields") else getattr(payload, field, None)
        if val is not None:
            rows += f'<tr><td style="background:#fefce8;font-weight:600;padding:8px 12px;width:40%">{label}</td><td style="padding:8px 12px;border-bottom:1px solid #fef08a">{val}</td></tr>'
    return f"""
    <html><body style="font-family:Arial,sans-serif;color:#111827;max-width:640px;margin:0 auto">
      <div style="background:#d97706;padding:20px 24px;border-radius:8px 8px 0 0">
        <h2 style="color:#fff;margin:0;font-size:18px">STP Revision Request — Director Approval Required</h2>
        <p style="color:#fef3c7;margin:4px 0 0;font-size:13px">{opp.opportunity_name} · {opp.phase_status}</p>
      </div>
      <div style="padding:24px;border:1px solid #fde68a;border-top:none;border-radius:0 0 8px 8px">
        <p>A buyer has submitted a revision of the STP baseline and requires your <strong>approval</strong> before the new values take effect.</p>
        <p><strong>Justification :</strong> {payload.note}</p>
        <p><strong>Requested by :</strong> {payload.requested_by or "N/A"}</p>
        <h3 style="font-size:13px;margin:20px 0 8px;color:#92400e">Proposed Changes</h3>
        <table style="border-collapse:collapse;width:100%;font-size:13px">{rows}</table>
        <h3 style="font-size:13px;margin:20px 0 8px;color:#92400e">Savings Impact Preview</h3>
        <table style="border-collapse:collapse;width:100%;font-size:13px;margin-bottom:16px">
          <tr><td style="background:#fefce8;font-weight:600;padding:8px 12px;width:40%">Year N (run-rate)</td><td style="padding:8px 12px;border-bottom:1px solid #fef08a">{_fmt(preview.get("saving_year_n"))}</td></tr>
          <tr><td style="background:#fefce8;font-weight:600;padding:8px 12px">Year N+1</td><td style="padding:8px 12px;border-bottom:1px solid #fef08a">{_fmt(preview.get("saving_year_n1"))}</td></tr>
          <tr><td style="background:#fefce8;font-weight:600;padding:8px 12px">Period Saving (N→N+3)</td><td style="padding:8px 12px;font-weight:700">{_fmt(preview.get("period_saving"))}</td></tr>
        </table>
        <p>Please log in to Purchasing Value Management and <strong>Approve or Reject</strong> this revision.</p>
        <p style="color:#6b7280;font-size:11px;margin-top:24px">Avocarbon · Purchasing Value Management</p>
      </div>
    </body></html>"""


def _build_stp_revision_decision_email(opp: Opportunity, payload, pending: dict) -> str:
    color = "#16a34a" if payload.decision == "Approved" else "#dc2626"
    icon  = "✅" if payload.decision == "Approved" else "❌"
    preview = pending.get("computed_preview", {})
    def _fmt(v): return f"€{v:,.0f}" if v is not None else "N/A"
    return f"""
    <html><body style="font-family:Arial,sans-serif;color:#111827;max-width:640px;margin:0 auto">
      <div style="background:{color};padding:20px 24px;border-radius:8px 8px 0 0">
        <h2 style="color:#fff;margin:0;font-size:18px">{icon} STP Revision {payload.decision}</h2>
        <p style="color:#d1fae5 if payload.decision == 'Approved' else #fee2e2;margin:4px 0 0;font-size:13px">{opp.opportunity_name}</p>
      </div>
      <div style="padding:24px;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 8px 8px">
        <p>Your STP revision request for <strong>{opp.opportunity_name}</strong> has been <strong>{payload.decision}</strong>.</p>
        <p><strong>Director decision by :</strong> {payload.decided_by or "Director"}</p>
        <p><strong>Reason :</strong> {payload.note or "N/A"}</p>
        {'<p style="color:#16a34a;font-weight:600">The proposed values have been applied. The monthly savings profile has been updated accordingly.</p>' if payload.decision == "Approved" else '<p style="color:#dc2626;font-weight:600">The current values remain unchanged.</p>'}
        <h3 style="font-size:13px;margin:20px 0 8px">Savings Preview (submitted)</h3>
        <table style="border-collapse:collapse;width:100%;font-size:13px">
          <tr><td style="background:#f9fafb;font-weight:600;padding:8px 12px;width:40%">Year N (run-rate)</td><td style="padding:8px 12px;border-bottom:1px solid #e5e7eb">{_fmt(preview.get("saving_year_n"))}</td></tr>
          <tr><td style="background:#f9fafb;font-weight:600;padding:8px 12px">Period Saving</td><td style="padding:8px 12px">{_fmt(preview.get("period_saving"))}</td></tr>
        </table>
        <p style="color:#6b7280;font-size:11px;margin-top:24px">Avocarbon · Purchasing Value Management</p>
      </div>
    </body></html>"""

def _build_validation_email(opp: Opportunity, custom_message: Optional[str]) -> str:
    saving = (
        f"€{opp.expected_annual_saving:,.0f}" if opp.expected_annual_saving else "N/A"
    )
    cash = f"€{opp.cash_impact:,.0f}" if opp.cash_impact else "N/A"
    duration = f"{opp.duration_months} months" if opp.duration_months else "N/A"
    pld = "N/A"
    if opp.priority_score:
        pld = f"{opp.priority_score} ({opp.priority_category})"
    extra = (
        f"<p style='color:#374151'><em>{custom_message}</em></p>"
        if custom_message
        else ""
    )

    return f"""
    <html><body style="font-family:Arial,sans-serif;color:#111827;max-width:620px;margin:0 auto">
      <div style="background:#1e3a5f;padding:20px 24px;border-radius:8px 8px 0 0">
        <h2 style="color:#fff;margin:0;font-size:18px">Purchasing — Validation Request (Phase 0)</h2>
      </div>
      <div style="padding:24px;border:1px solid #dde3ec;border-top:none;border-radius:0 0 8px 8px">
        <p>The following opportunity requires your Go / No Go decision before moving to Phase 1:</p>
        {extra}
        <table style="border-collapse:collapse;width:100%;margin:16px 0;font-size:13px">
          <tr><td style="background:#f5f8fc;font-weight:600;padding:8px 12px;width:38%">Name</td><td style="padding:8px 12px;border-bottom:1px solid #eef1f6">{opp.opportunity_name}</td></tr>
          <tr><td style="background:#f5f8fc;font-weight:600;padding:8px 12px">Type</td><td style="padding:8px 12px;border-bottom:1px solid #eef1f6">{opp.opportunity_type}</td></tr>
          <tr><td style="background:#f5f8fc;font-weight:600;padding:8px 12px">Pilot</td><td style="padding:8px 12px;border-bottom:1px solid #eef1f6">{opp.idea_owner}</td></tr>
          <tr><td style="background:#f5f8fc;font-weight:600;padding:8px 12px">Est. Annual Saving</td><td style="padding:8px 12px;border-bottom:1px solid #eef1f6">{saving}</td></tr>
          <tr><td style="background:#f5f8fc;font-weight:600;padding:8px 12px">Cash Impact</td><td style="padding:8px 12px;border-bottom:1px solid #eef1f6">{cash}</td></tr>
          <tr><td style="background:#f5f8fc;font-weight:600;padding:8px 12px">Duration</td><td style="padding:8px 12px;border-bottom:1px solid #eef1f6">{duration}</td></tr>
          <tr><td style="background:#f5f8fc;font-weight:600;padding:8px 12px">PLD Priority</td><td style="padding:8px 12px;border-bottom:1px solid #eef1f6">{pld}</td></tr>
          <tr><td style="background:#f5f8fc;font-weight:600;padding:8px 12px">Change Mode</td><td style="padding:8px 12px">{opp.change_mode or "TBD"}</td></tr>
        </table>
        {f'<p style="background:#f5f8fc;padding:12px;border-radius:6px;font-size:12px"><strong>Assumptions:</strong> {opp.assumptions_summary}</p>' if opp.assumptions_summary else ""}
        <p style="color:#6b7280;font-size:11px;margin-top:24px">Avocarbon · Purchasing Value Management</p>
      </div>
    </body></html>
    """
