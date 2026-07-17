"""Purchasing value management service — full business logic."""

from __future__ import annotations

import calendar
import copy
import logging
from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import Optional, List

from sqlalchemy import select, inspect as sa_inspect
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import PANEL_ACTIVE_DECISIONS
from app.core.exceptions import AppException
from app.features.auth.models import AccessIdentity
from app.features.notifications.models import Notification
from app.db.models import (
    FinancialLine,
    MonthlyFinancial,
    Opportunity,
    OpportunityBudgetYear,
    OpportunityDocument,
    OpportunityPhaseSnapshot,
    Project,
    SupplierGroup,
    SupplierSiteRelation,
    SupplierUnit,
)
from app.features.purchasing_value.schemas import (
    EscalateRequest,
    FinancialLineCompleteRequest,
    FinancialLineReviseBaselineRequest,
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
    compute_saving_to_budget_per_year,
    compute_duration_months,
    is_direct_gain,
    ENTRY_MODE_TYPE,
    compute_savings_start_date,
    compute_budget_year_portions,
    auto_payback_score,
    auto_leadtime_score,
    budget_year_bounds,
    add_months_preserve_day,
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

    async def delete_opportunity(
        self, opportunity_id: int, deleted_by: Optional[str] = None
    ) -> None:
        """Soft-delete an opportunity (is_deleted=True), consistent with how every
        read path filters opportunities. Hard delete is avoided so historical
        financial lines, documents, and gate decisions stay intact."""
        opp = await self.get_opportunity(opportunity_id)
        opp.is_deleted = True
        opp.deleted_at = datetime.utcnow()
        opp.deleted_by = deleted_by
        await self.db.flush()

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
        self, payload: OpportunityCreateRequest, created_by: Optional[str] = None
    ) -> Opportunity:
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
            saving_nature=payload.saving_nature,
            entry_mode=payload.entry_mode,
            created_by=created_by,
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

    async def duplicate_opportunity(
        self, opportunity_id: int, created_by: Optional[str] = None
    ) -> Opportunity:
        """Create a fresh Phase-0 draft from an existing opportunity.

        Copies the DEFINITION (type, saving nature, STP prices/quantities/scope,
        logistics, investment, scores, risks/benefits, reasons) but RESETS identity,
        workflow status, every date and all execution history. Children (financial
        lines, budget years, projects, documents, gate requests, snapshots, action
        plans) are NOT copied — the duplicate starts clean at Phase 0 so it can be
        re-scoped and re-validated on its own."""
        src = (
            await self.db.execute(
                select(Opportunity).where(Opportunity.opportunity_id == opportunity_id)
            )
        ).scalar_one_or_none()
        if src is None:
            raise AppException(404, "Opportunity not found.", "OPP_NOT_FOUND")

        # Never carried over — these define a brand-new draft's own lifecycle.
        RESET = {
            "opportunity_id", "opportunity_name", "created_at", "created_by",
            "status", "phase_status", "validation_status", "validation_decision",
            "budget_year", "budget_confirmed_at", "budget_confirmed_by",
            "committee_level",
            "val_date", "planned_start_date", "planned_end_date", "study_start_date",
            "execution_start_date", "real_start_date",
            "validation_request_sent_at", "validation_request_sent_by",
            "pending_stp_revision", "revision_history",
            # Scope is specific to the source opportunity — the copy must be re-scoped.
            "scope_in", "scope_out",
        }
        data = {}
        for col in (c.key for c in sa_inspect(Opportunity).mapper.column_attrs):
            if col in RESET:
                continue
            val = getattr(src, col)
            # Deep-copy JSONB (dict/list) so the two rows never share a mutable object.
            data[col] = copy.deepcopy(val) if isinstance(val, (dict, list)) else val

        dup = Opportunity(
            **data,
            opportunity_name=f"{src.opportunity_name or 'Opportunity'} (copy)",
            created_by=created_by,
            status="Assigned",
            phase_status="Phase 0",
            validation_status="Empty",
            validation_decision=None,
        )
        self.db.add(dup)
        await self.db.flush()
        await self.db.refresh(
            dup,
            ["projects", "financial_lines", "opp_documents", "budget_years", "plant"],
        )
        return dup

    # ------------------------------------------------------------------
    # Update Phase 0 fields
    # ------------------------------------------------------------------

    async def update_opportunity(
        self, opportunity_id: int, payload: OpportunityUpdateRequest,
        actor_role: Optional[str] = None,
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
                "cash_impact",
            )
        else:
            baseline_fields = ("expected_annual_saving", "cash_impact")

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

        # Same lock, triggered earlier — a "Budgeted" opportunity is committed to a
        # fiscal-year budget (see the Budgeting page) even before any actual has
        # been recorded. The UI already greys out these fields once Budgeted
        # (see `isBudgeted`/`locked` in PurchasingValuePage.tsx); this closes the
        # gap where a direct API call could still silently change the committed
        # saving/cash baseline without going through the audited Revise-Baseline
        # flow.
        if (
            opp.validation_status == "Budgeted"
            and not line_has_actuals
            and _baseline_change_attempted()
        ):
            raise AppException(
                422,
                "This opportunity is committed to a budget (Budgeted) — the saving/"
                "cash baseline is locked. Use Revise Baseline (audited and "
                "reviewed) to change it; baseline figures cannot be edited "
                "silently once budgeted.",
                "BASELINE_LOCKED_BUDGETED",
            )

        # ── STP revision approval gate (Phase 2/3) ──────────────────────────
        # Role split, enforced here AND mirrored on the frontend (stpReadOnly /
        # canEditStpDirectly in PurchasingValuePage.tsx):
        #   - purchasing_director / vp_conversion: `actor_role` bypasses the gate
        #     below entirely — they edit price/quantity/bonus fields directly via
        #     this same endpoint (normal Save Changes), no approval workflow,
        #     because they ARE the approvers.
        #   - every other non-viewer role: baseline is read-only here; any
        #     attempted change to a baseline field raises STP_REQUIRES_APPROVAL,
        #     and the caller must use POST /request-stp-revision instead, which
        #     notifies every purchasing_director/vp_conversion by email + in-app
        #     Notification (see request_stp_revision) for one of them to decide
        #     via POST /decide-stp-revision.
        # This only applies to Phase 2/3 — Phase 0/1 stays freely editable for
        # everyone (checked implicitly: baseline_fields are locked only from
        # Phase 2 onward, see the phase check below).
        if (
            opp.opportunity_type in ("Sourcing", "Technical Productivity")
            and opp.phase_status in ("Phase 2", "Phase 3")
            and not line_has_actuals   # Phase 3 with actuals already caught above
            and _baseline_change_attempted()
            and actor_role not in ("purchasing_director", "vp_conversion")
        ):
            raise AppException(
                422,
                "STP baseline changes in Phase 2 and Phase 3 require Director approval. "
                "Use 'Request Revision' to submit the proposed values for sign-off — "
                "current figures remain active until the Director approves.",
                "STP_REQUIRES_APPROVAL",
            )

        # Even a Director/VP can't silently overwrite the baseline while someone
        # else's revision request is awaiting their own decision — they must
        # explicitly Approve/Reject it via decide_stp_revision first, so the
        # requester's proposal + audit trail isn't bypassed without a record.
        if opp.pending_stp_revision and _baseline_change_attempted():
            raise AppException(
                422,
                "A revision request is already pending for this opportunity. "
                "Approve or reject it before editing the baseline directly.",
                "REVISION_ALREADY_PENDING",
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
        _set_if(opp, "saving_nature", payload.saving_nature)
        # entry_mode (Bonus/Rework sub-mode). "Standard" clears it back to normal STP;
        # None = not provided (no change). Enforce that the mode matches the type.
        if payload.entry_mode is not None:
            mode = None if payload.entry_mode == "Standard" else payload.entry_mode
            required = ENTRY_MODE_TYPE.get(mode) if mode else None
            if required and opp.opportunity_type != required:
                raise AppException(
                    422,
                    f"entry_mode '{mode}' is only allowed on {required} opportunities.",
                    "ENTRY_MODE_TYPE_MISMATCH",
                )
            opp.entry_mode = mode
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
        # Guard: real_start_date is immutable once the opportunity is committed to a
        # locked budget row. Changing it would silently shift the pro-rata split and
        # invalidate the director's historical commitment.
        if (
            payload.real_start_date is not None
            and payload.real_start_date != old_real_start
        ):
            locked_row = (
                await self.db.execute(
                    select(OpportunityBudgetYear).where(
                        OpportunityBudgetYear.opportunity_id == opp.opportunity_id,
                        OpportunityBudgetYear.status_locked_at.is_not(None),
                        OpportunityBudgetYear.is_deleted.is_(False),
                    )
                )
            ).scalars().first()
            if locked_row is not None:
                raise AppException(
                    "real_start_date cannot be modified: this opportunity is locked in a "
                    "committed budget. Contact your purchasing director to unlock.",
                    status_code=422,
                )
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
            if payload.fx_rate_to_eur is not None:
                # Compare as Decimal to avoid float/Decimal precision mismatch
                # (e.g. Decimal("1.15") != 1.15 in Python float arithmetic).
                new_rate = Decimal(str(payload.fx_rate_to_eur))
                current_rate = opp.fx_rate_to_eur if opp.fx_rate_to_eur is not None else Decimal("1")
                if new_rate != current_rate:
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

        # Final-state guard: non-EUR opportunity must have a valid rate in the DB after
        # this update, even if currency was set in a previous request and fx_rate was
        # never populated. Catching this here prevents silent 1:1 fallback in KPI rollups.
        if (opp.currency or "EUR") != "EUR":
            if not opp.fx_rate_to_eur or opp.fx_rate_to_eur <= 0:
                raise AppException(
                    422,
                    f"A valid FX rate to EUR is required for {opp.currency} opportunities. "
                    f"Set fx_rate_to_eur (e.g. 0.920000 for USD) before saving.",
                    "FX_RATE_REQUIRED",
                )

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
        _set_if(opp, "place_of_incoterms_before", payload.place_of_incoterms_before)
        _set_if(opp, "place_of_incoterms_after", payload.place_of_incoterms_after)
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

        # Bonus / Rework: the saving is a single lump gain (entered in
        # expected_annual_saving), not a price×quantity grid. Clear the grid,
        # logistics and cash inputs so no stale value lingers or feeds a formula —
        # the gain is realized one-time over a single month.
        if is_direct_gain(opp):
            opp.annual_quantity_n1 = opp.annual_quantity_n2 = None
            opp.annual_quantity_n3 = opp.annual_quantity_n4 = None
            opp.current_price = opp.current_price_n1 = None
            opp.current_price_n2 = opp.current_price_n3 = None
            opp.proposed_price = opp.proposed_price_n1 = None
            opp.proposed_price_n2 = opp.proposed_price_n3 = None
            opp.incoterms_before = opp.incoterms_after = None
            opp.top_days_before = opp.top_days_after = None
            opp.transit_days_before = opp.transit_days_after = None
            opp.consignment_before = opp.consignment_after = None
            opp.cash_inventory_gap = opp.cash_ap_gap = opp.cash_impact = None

        # Completeness of the 4-year price/quantity grid is enforced on the FRONTEND
        # (Phase 0/1 submit guard) for new STP entries — Olivier, call 2026-07-10 — so a
        # buyer fills every year before saving. The backend stays permissive here so
        # legacy/partial data, single-year deals and programmatic flows (revise-baseline,
        # gate decisions, STP revision) keep working; the negative-saving guard below
        # still blocks corrupt price grids.

        # STP financials — exact formulas from Excel "format STP rev 1.2" (D51/D52/F51/F52/D55/D56)
        stp_fin = compute_stp_financials(opp)
        # D4 — guard: price_after > price_before inverts the saving to a cost increase.
        # Reject early so corrupted data never reaches the DB.
        _neg_years = [
            (f"N+{i}" if i > 0 else "N", float(v))
            for i, v in enumerate(stp_fin.get("saving_per_year") or [])
            if v is not None and float(v) < 0
        ]
        if _neg_years:
            detail = ", ".join(f"Year {lbl}: {amt:,.0f} €" for lbl, amt in _neg_years)
            raise AppException(
                422,
                f"STP saving is negative for: {detail}. "
                "Proposed price exceeds current price — please review before saving.",
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
        # Duration is derived from whether the negotiated price still changes: a flat
        # price cannot be budgeted beyond 12 months; each further year of price change
        # extends it by 12 (Olivier, call 2026-07-10). Only override for STP opps that
        # carry the price/qty base — non-STP opps keep their manual duration.
        derived_duration = compute_duration_months(opp)
        if derived_duration is not None:
            opp.duration_months = derived_duration
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
        if payload.payback_score is not None:
            # Manual override takes priority over auto-calculation
            opp.payback_score = Decimal(str(payload.payback_score))
        elif auto_p is not None:
            opp.payback_score = Decimal(str(auto_p))

        # L score — Phase 1+2+3 ONLY per Olivier: "durée phase 1, 2 et 3"
        # Phase 4 LLC happens AFTER production starts → not part of lead time
        total_weeks = sum(
            filter(None, [opp.phase1_weeks, opp.phase2_weeks, opp.phase3_weeks])
        )
        auto_l = auto_leadtime_score(float(total_weeks) if total_weeks else None)
        if payload.lead_time_score is not None:
            # Manual override takes priority over auto-calculation
            opp.lead_time_score = Decimal(str(payload.lead_time_score))
        elif auto_l is not None:
            opp.lead_time_score = Decimal(str(auto_l))

        # Priority lock — buyer can force the category regardless of P×L×D
        if payload.priority_locked is not None:
            opp.priority_locked = payload.priority_locked
        if payload.priority_category_override is not None:
            if payload.priority_category_override:
                opp.priority_category = payload.priority_category_override
        # Auto-compute PLD priority only when not manually locked
        if not opp.priority_locked:
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
        "description",
        "validation_decision", "idea_owner", "project_owner",
        "budget_year", "supplier_id", "plant_id",
        "change_mode",               # Standard | Silent — per-phase value at gate time
        # Dates
        "planned_start_date", "real_start_date",
        "planned_end_date",          # computed: planned_start + duration_months
        "val_date",                  # date of Phase 0 Go
        "duration_months",
        # Scope
        "scope_in", "scope_out", "customers",
        # STP price baseline
        "current_price", "proposed_price",
        "current_price_n1", "current_price_n2", "current_price_n3",
        "proposed_price_n1", "proposed_price_n2", "proposed_price_n3",
        # Quantities
        "annual_quantity_n1", "annual_quantity_n2", "annual_quantity_n3", "annual_quantity_n4",
        # Supplier before/after (for Sourcing)
        "proposed_supplier_name", "country_after",
        "supplier_asked", "supplier_asked_result",
        # Logistics
        "incoterms_before", "incoterms_after",
        "place_of_incoterms_before", "place_of_incoterms_after",
        "top_days_before", "top_days_after",
        "transit_days_before", "transit_days_after",
        "bonus_before", "bonus_after",
        "consignment_before", "consignment_after",
        # Risks & benefits
        "stp_risks", "stp_benefits",
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
        self,
        opportunity_id: int,
        payload: GateDecisionRequest,
        _via_gate_approval: bool = False,
    ) -> Opportunity:
        if payload.decision not in ("Go", "No Go", "Review"):
            raise AppException(
                422, "Decision must be Go, No Go, or Review.", "INVALID_DECISION"
            )

        opp = await self.get_opportunity(opportunity_id)
        now = datetime.utcnow()
        phase_before = opp.phase_status or "Phase 0"

        # FX gate check — block Go on non-EUR opportunity without a valid rate.
        # A missing rate would silently count all savings at 1:1, distorting every
        # EUR-consolidated KPI, budget total, and monthly chart after this point.
        if payload.decision == "Go" and (opp.currency or "EUR") != "EUR":
            if not opp.fx_rate_to_eur or float(opp.fx_rate_to_eur) <= 0:
                raise AppException(
                    422,
                    f"Cannot apply a Go decision: the opportunity uses {opp.currency} "
                    f"but has no FX rate to EUR. Set fx_rate_to_eur in the opportunity "
                    f"settings before submitting for gate validation.",
                    "FX_RATE_REQUIRED_FOR_GATE",
                )

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

            # Opportunity-level derived state must reflect cancellation immediately.
            # _sync_budget_years() is skipped for No Go (it would overwrite the Empty
            # rows we just set), so we update these two fields directly here.
            opp.validation_status = "Empty"
            opp.budget_year = None

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

            # Phases 1-4 are material: financial lines, deployment, and close-out.
            # A single privileged user must NOT advance them without a quorum vote.
            # The _via_gate_approval flag is set only by GateApprovalService._check_consensus,
            # so direct API calls (e.g. from the UI gate-decision endpoint) are blocked.
            if current_phase in ("Phase 1", "Phase 2", "Phase 3", "Phase 4") and not _via_gate_approval:
                raise AppException(
                    422,
                    f"{current_phase} transitions require a completed gate approval "
                    "workflow. Submit a gate approval request and wait for the panel to vote — "
                    "the phase advances automatically once quorum is reached.",
                    "GATE_APPROVAL_REQUIRED",
                )

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
                if opp.opportunity_type == "Negotiation":
                    # Negotiation skips Phase 2 (no deployment step) — jump
                    # straight to Phase 3 and perform its side effect (the
                    # financial line) inline, automatically, with no separate
                    # Phase 3 approval request.
                    opp.phase_status = "Phase 3"
                    opp.status = "Working on it"
                    if not opp.financial_lines:
                        await self._create_financial_line(opp)
                    if payload.comments:
                        opp.comments = (
                            opp.comments or ""
                        ) + f"\n[Phase 1 Go — Negotiation, skips Phase 2] {payload.comments}"
                else:
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

        # Phase change shifts the suggested per-year status (e.g. → "Budgeted" at Phase 3).
        # No Go is excluded: the reset loop above already zeroed and unlocked all budget rows;
        # calling _sync_budget_years would overwrite "Empty" back to "Opportunity" because
        # status_locked_at was just cleared.
        if payload.decision != "No Go":
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
        today = now.date()
        new_cycle = line.recovery_status in (None, "Done") and payload.recovery_status in (
            "Planned",
            "In Progress",
        )

        # --- Baseline gap from real monthly data, not the stale denormalized delta ---
        # Sum expected vs actual for all past months from savings start, so the
        # baseline is consistent even if _recalculate_ytd hasn't run yet.
        savings_start = line.real_start_date or line.planned_start_date
        if savings_start:
            past_rows = [
                m for m in (line.monthly_financials or [])
                if m.period_month is not None
                and m.period_month >= savings_start.replace(day=1)
                and m.period_month < today.replace(day=1)
            ]
            sum_expected = sum(float(m.expected_saving or 0) for m in past_rows)
            sum_actual = sum(float(m.actual_saving or 0) for m in past_rows)
            computed_gap = Decimal(str(round(max(0.0, sum_expected - sum_actual), 2)))
        else:
            computed_gap = Decimal("0")

        # --- Validation 1 : date cible ---
        # A target date in the past has no operational meaning — the plan is already
        # overdue before it starts, so it would immediately appear as a false alert.
        if payload.recovery_target_date is not None and payload.recovery_status != "Done":
            if payload.recovery_target_date < today:
                raise AppException(
                    422,
                    f"La date cible ({payload.recovery_target_date}) est dans le passé. "
                    "Choisissez une date future pour le plan de recovery.",
                    "RECOVERY_TARGET_IN_PAST",
                )
            # Target must not go beyond the opportunity's own savings horizon.
            opp_result = await self.db.execute(
                select(Opportunity).where(Opportunity.opportunity_id == line.opportunity_id)
            )
            opp = opp_result.scalar_one_or_none()
            if opp and opp.planned_start_date and opp.duration_months:
                opp_end = add_months(opp.planned_start_date, int(opp.duration_months))
                if payload.recovery_target_date > opp_end:
                    raise AppException(
                        422,
                        f"La date cible ({payload.recovery_target_date}) dépasse "
                        f"la fin prévue de l'opportunité ({opp_end}). "
                        "Ajustez la durée de l'opportunité ou choisissez une date antérieure.",
                        "RECOVERY_TARGET_AFTER_OPP_END",
                    )

        # --- Validation 2 : montant vs gap réel ---
        # Catching "recovery_amount = 10 € for a 500 000 € gap" at the gate.
        # Rules applied only on active plans (not Done) when an amount is explicitly sent.
        if (
            payload.recovery_status in ("Planned", "In Progress")
            and "recovery_amount" in payload.model_fields_set
            and payload.recovery_amount is not None
        ):
            amount = float(payload.recovery_amount)
            if amount <= 0:
                raise AppException(
                    422,
                    "Le montant du plan de recovery doit être supérieur à 0 € "
                    "pour un plan Planned ou In Progress.",
                    "RECOVERY_AMOUNT_ZERO",
                )
            gap_float = float(computed_gap)
            # Threshold: amount must cover at least 1 % of a significant gap (> 1 000 €).
            # Below that ratio the plan is cosmetic and misleads the tracking dashboard.
            if gap_float > 1000.0 and amount < gap_float * 0.01:
                raise AppException(
                    422,
                    f"Le montant de recovery ({amount:,.0f} €) représente moins de 1 % "
                    f"du gap réel ({gap_float:,.0f} €). "
                    "Merci de saisir un montant crédible — au moins "
                    f"{gap_float * 0.01:,.0f} €.",
                    "RECOVERY_AMOUNT_TOO_LOW",
                )

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
        if new_cycle:
            # Use the real-data baseline, not the stale denormalized delta
            line.recovery_baseline_gap = computed_gap
            line.recovery_baseline_set_at = now
        if "recovery_note" in payload.model_fields_set:
            line.recovery_note = payload.recovery_note
        if "recovery_target_date" in payload.model_fields_set:
            line.recovery_target_date = payload.recovery_target_date
        if "recovery_amount" in payload.model_fields_set:
            line.recovery_amount = (
                Decimal(str(payload.recovery_amount))
                if payload.recovery_amount is not None
                else None
            )
        line.recovery_updated_at = now
        line.recovery_updated_by = payload.updated_by
        line.updated_at = now
        line.updated_by = payload.updated_by
        # If recovery is done, clear escalation — but only when the active
        # escalation is the one this recovery plan was opened for (the
        # auto-escalation raised by the monthly review, same convention as
        # the R9 rebuild's own auto-clear). A manually-set escalation for an
        # unrelated reason must not be silently cleared as a side effect of
        # closing a recovery plan — use deescalate_financial_line for that.
        if (
            payload.recovery_status == "Done"
            and line.is_escalated
            and line.escalation_reason
            and line.escalation_reason.startswith("Auto-escalated from monthly review")
        ):
            line.is_escalated = False
            line.escalated_at = None
            line.escalated_by = None
            line.escalation_reason = None
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
        Olivier: months before planned_start_date are expected to be 0 — no alert for those.
        H2 cooldown: at most one alert per 7 days per financial line to avoid inbox spam."""
        if line.status != "Active":
            return
        # Cooldown — don't re-alert within 7 days of the last sent alert
        if (
            line.delay_alert_last_sent_at is not None
            and datetime.utcnow() - line.delay_alert_last_sent_at < timedelta(days=7)
        ):
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
            line.delay_alert_last_sent_at = datetime.utcnow()
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

    # Phases where savings are confirmed and real_start_date is expected to be set.
    _BUDGET_ELIGIBLE_PHASES = {"Phase 3", "Phase 4", "Closed"}

    def _is_budget_eligible(self, opp: Opportunity) -> bool:
        """Option A eligibility, extended:
        - Phase 3 / Phase 4 / Closed with a confirmed real_start_date (savings
          timing is known), OR
        - Phase 2 with execution_start_date entered — execution has started so
          the opportunity is firm enough to budget, even though savings haven't
          begun yet. compute_savings_start_date()'s existing fallback chain
          (real_start_date -> planned_start_date -> study+weeks estimate) will
          pro-rate these using planned_start_date since real_start_date is None;
          once Phase 3 sets real_start_date, the anchor switches automatically
          on the next _sync_budget_years run."""
        if opp.phase_status in self._BUDGET_ELIGIBLE_PHASES and opp.real_start_date is not None:
            return True
        if opp.phase_status == "Phase 2" and opp.execution_start_date is not None:
            return True
        return False

    async def _closed_fiscal_years(self) -> set[int]:
        """Return the set of fiscal years that have been officially closed."""
        from app.db.models import BudgetYearClosure
        result = await self.db.execute(select(BudgetYearClosure))
        return {row.fiscal_year for row in result.scalars().all()}

    async def _sync_budget_years(self, opp: Opportunity) -> None:
        """Recompute the per-fiscal-year budget rows from the SAME per-year savings as
        the STP calendar-year estimate (escalating windows), anchored on the savings
        start and capped at duration_months. When no STP prices exist, fall back to
        the flat expected_annual_saving repeated across the duration. Status is fully
        derived from validation (_validation_status) — no manual override.

        Budget eligibility rule (Option A, extended) — see _is_budget_eligible:
        - opp.real_start_date confirmed AND phase_status in Phase 3/Phase 4/Closed, OR
        - opp.execution_start_date entered AND phase_status == Phase 2
        Opps that don't meet this are not eligible for budget rows. Any unlocked
        rows previously created are cleaned up.

        is_additional: a new row created for a fiscal year that is already closed
        (BudgetYearClosure exists) is flagged is_additional=True so Finance can
        distinguish post-closure additions from the original committed baseline.

        IMPORTANT: do not rely on `opp.budget_years` being preloaded. In async
        SQLAlchemy, touching an unloaded relationship here can trigger an implicit
        lazy-load outside the greenlet context (`MissingGreenlet`). Query the rows
        explicitly instead so the recompute path is safe in API, tests and batch
        flows alike."""
        # Locked (FOR UPDATE) to match assign_budget_year/close_budget_year —
        # without this, two concurrent syncs for the same opportunity (e.g. a
        # gate decision and a metadata save landing close together) can both
        # read "no row for FY X" and both attempt to insert, surfacing as an
        # unhandled IntegrityError on the unique (opportunity_id, fiscal_year)
        # constraint instead of serializing cleanly.
        existing_rows = (
            (
                await self.db.execute(
                    select(OpportunityBudgetYear)
                    .where(
                        OpportunityBudgetYear.opportunity_id == opp.opportunity_id,
                        OpportunityBudgetYear.is_deleted.is_(False),
                    )
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )

        # If opp is not budget-eligible, clean up any unlocked rows and stop.
        if not self._is_budget_eligible(opp):
            for row in existing_rows:
                if row.status_locked_at is None:
                    await self.db.delete(row)
            await self.db.flush()
            return

        duration = int(opp.duration_months or 0)
        anchor = compute_savings_start_date(opp)
        # Budget the INCREMENTAL year-over-year price drop ("saving à budgéter"), not the
        # full run-rate saving reconducted every year (Olivier, call 2026-07-10). A flat
        # price collapses the whole budget onto year N; a falling price adds each year's
        # extra drop. compute_budget_year_portions still handles mid-year start prorata.
        per_year = compute_saving_to_budget_per_year(opp)
        if any(v is not None for v in per_year):
            windows = per_year  # STP incremental per-year savings to budget
        elif opp.expected_annual_saving is not None:
            # Sans prix STP, on ne "reconduit" PAS l'annuel chaque année : une économie
            # récurrente à prix plat est budgétée UNE fois (année de démarrage), cohérent
            # avec l'incrémental ci-dessus (décision "saving à budgéter", Olivier 2026-07-10)
            # et avec les imports Monday (année N pleine, exercices suivants = 0). Reconduire
            # double-compterait un deal pluriannuel sur plusieurs budgets.
            windows = [float(opp.expected_annual_saving)]
        else:
            windows = []
        portions = compute_budget_year_portions(windows, anchor, duration or None)
        status = self._validation_status(opp)
        closed_fys = await self._closed_fiscal_years()

        existing = {by.fiscal_year: by for by in existing_rows}
        seen = set()

        # Tracks the effective budget_status of every row processed so far in this
        # sync (both pre-existing and newly created), so a later fiscal year can see
        # whether an earlier one is already committed. Seeded from locked rows that
        # aren't part of `portions` (shouldn't normally happen, but stay defensive).
        status_by_fy: dict[int, str] = {
            fy: row.budget_status for fy, row in existing.items()
        }

        for p in sorted(portions, key=lambda p: p["fiscal_year"]):
            fy = p["fiscal_year"]
            seen.add(fy)
            amt = Decimal(str(p["amount"]))
            is_add = fy in closed_fys
            # Once the opportunity has been committed ("Budgeted") for an earlier
            # fiscal year, a later, still-open year it spills into defaults to
            # "Budgeted" too — it follows the same commitment automatically instead
            # of sitting as an undecided "Opportunity" until its own Create-Budget
            # round. It stays unlocked, so it can still be freely changed until that
            # year's own budget is closed. This never applies to an "additional"
            # (already-closed) year — those always need an explicit Finance decision.
            default_status = (
                "Budgeted"
                if not is_add
                and any(
                    f < fy and s == "Budgeted" for f, s in status_by_fy.items()
                )
                else "Opportunity"
            )
            row = existing.get(fy)
            if row is not None:
                # A LOCKED row is a frozen commitment — a director Create-Budget
                # decision OR an imported baseline (e.g. SB12, status_locked_by set at
                # import). Its committed figures (applicable_amount, portion_kind,
                # budget_status) must never be silently recomputed. Previously only
                # budget_status was protected while applicable_amount/portion_kind were
                # overwritten unconditionally (this line), so an incremental re-sync on
                # the next save erased the imported baseline (e.g. 1,105,760 -> 901,528).
                # Freeze the whole committed row when locked; refresh only the advisory
                # suggested_status. (Mirrors the "never mutate a locked row" rule already
                # applied to stale rows below.)
                if row.status_locked_at is None:
                    row.applicable_amount = amt
                    row.portion_kind = p["kind"]
                    row.budget_status = default_status
                row.suggested_status = status
                status_by_fy[fy] = row.budget_status
            else:
                new_row = OpportunityBudgetYear(
                    opportunity_id=opp.opportunity_id,
                    fiscal_year=fy,
                    applicable_amount=amt,
                    portion_kind=p["kind"],
                    suggested_status=status,
                    budget_status=default_status,
                    is_additional=is_add,
                )
                self.db.add(new_row)
                status_by_fy[fy] = default_status

        # Stale rows (duration shrank / dates cleared) — drop only if not locked.
        # A director-committed row (status_locked_at set) must never be silently
        # deleted OR mutated — its applicable_amount is a frozen commitment, so
        # leave it exactly as-is rather than zeroing it out; a stale locked row
        # is a signal for Finance to review, not a value the sync should touch.
        for fy, row in existing.items():
            if fy not in seen and row.status_locked_at is None:
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
        budgeting page.

        Budget eligibility filter (Option A, extended) — see _is_budget_eligible:
        Phase 3 / Phase 4 / Completed opportunities with a confirmed real_start_date,
        plus Phase 2 opportunities with execution_start_date already entered.
        """
        result = await self.db.execute(
            select(OpportunityBudgetYear)
            .where(
                OpportunityBudgetYear.fiscal_year == fiscal_year,
                OpportunityBudgetYear.is_deleted.is_(False),
            )
            .options(
                selectinload(OpportunityBudgetYear.opportunity).selectinload(
                    Opportunity.plant
                ),
                selectinload(OpportunityBudgetYear.opportunity)
                .selectinload(Opportunity.financial_lines)
                .selectinload(FinancialLine.monthly_financials),
            )
            .order_by(OpportunityBudgetYear.id)
        )
        items = []
        for r in result.scalars().all():
            opp = r.opportunity
            if opp is None or opp.is_deleted:
                continue
            # B1 — exclude cancelled opportunities so directors never see
            # phantom budget commitments on dead projects.
            if opp.status == "Cancelled":
                continue
            # Budget eligibility — see _is_budget_eligible.
            if not self._is_budget_eligible(opp):
                continue
            # Enrich with financial line data — sum ALL active+completed lines
            # (an opp may have multiple component lines; first-only would under-count)
            # EUR is always 1:1; a non-EUR opp with no valid rate gets 0.0 (excluded
            # from EUR totals) rather than a silent 1:1 fallback — matches
            # kpi_service.py's _rate()/_opp_rate() and the FX_RATE_REQUIRED guards
            # in this file, which all avoid distorting EUR-consolidated figures.
            if (opp.currency or "EUR") == "EUR":
                fx = 1.0
            else:
                fx = float(opp.fx_rate_to_eur) if opp.fx_rate_to_eur and opp.fx_rate_to_eur > 0 else 0.0
            # A non-EUR opp with no usable rate must not silently report EUR
            # figures as 0 — that's indistinguishable from a genuine zero. Every
            # *_eur field below is left None when this is true, and the count is
            # surfaced in the summary (see router.py) so Finance can see how much
            # is missing, matching kpi_service.py's non_eur_missing_rate pattern.
            fx_missing = (opp.currency or "EUR") != "EUR" and fx <= 0
            contributing_lines = [
                fl for fl in (opp.financial_lines or [])
                if fl.status in ("Active", "Completed") and not fl.is_deleted
            ]

            def _n(v):
                return float(v) if v is not None else None

            # Monthly-tracked figures scoped to this fiscal year — cash_expected/
            # cash_actual and actual_saving are monthly fields on MonthlyFinancial,
            # available regardless of opportunity_type (not limited to the "Cash"
            # type). Used to pair each FY column with its own Expected/Actual,
            # mirroring the Saving FY / Cash FY split shown on the Budgeting page.
            cash_expected_eur = None
            cash_actual_eur = None
            saving_actual_fy_eur = None
            if contributing_lines:
                fy_start, fy_end_exclusive = budget_year_bounds(fiscal_year)
                fy_months = [
                    m
                    for fl in contributing_lines
                    for m in (fl.monthly_financials or [])
                    if m.period_month and fy_start <= m.period_month < fy_end_exclusive
                ]
                raw_cash_expected = sum(_n(m.cash_expected) or 0.0 for m in fy_months)
                raw_cash_actual = sum(
                    _n(m.cash_actual) or 0.0 for m in fy_months if m.cash_actual is not None
                )
                cash_expected_eur = (
                    round(raw_cash_expected * fx, 2)
                    if raw_cash_expected and not fx_missing
                    else None
                )
                cash_actual_eur = (
                    round(raw_cash_actual * fx, 2)
                    if any(m.cash_actual is not None for m in fy_months) and not fx_missing
                    else None
                )
                raw_saving_actual_fy = sum(
                    _n(m.actual_saving) or 0.0 for m in fy_months if m.actual_saving is not None
                )
                saving_actual_fy_eur = (
                    round(raw_saving_actual_fy * fx, 2)
                    if any(m.actual_saving is not None for m in fy_months) and not fx_missing
                    else None
                )

            eoy_forecast_eur = None
            expected_annual_saving_eur = None
            actual_ytd_eur = None
            delta_ytd_eur = None
            delta_eoy_budget = None

            if contributing_lines:
                raw_eoy = sum(
                    _n(fl.forecast_eoy_current) if fl.forecast_eoy_current is not None
                    else (_n(fl.expected_annual_saving) or 0.0)
                    for fl in contributing_lines
                )
                raw_exp = sum(_n(fl.expected_annual_saving) or 0.0 for fl in contributing_lines)
                raw_actual = sum(_n(fl.cumulated_real_saving) or 0.0 for fl in contributing_lines)
                raw_delta_ytd = sum(_n(fl.delta_vs_expected_ytd) or 0.0 for fl in contributing_lines)
                eoy_forecast_eur = round(raw_eoy * fx, 2) if raw_eoy and not fx_missing else None
                expected_annual_saving_eur = (
                    round(raw_exp * fx, 2) if raw_exp and not fx_missing else None
                )
                actual_ytd_eur = round(raw_actual * fx, 2) if raw_actual and not fx_missing else None
                delta_ytd_eur = (
                    round(raw_delta_ytd * fx, 2) if raw_delta_ytd and not fx_missing else None
                )
                # Δ EOY − Budget: both annual figures → meaningful comparison
                # (applicable_amount is FY pro-rata; using it as denominator would mix units)
                if eoy_forecast_eur is not None and expected_annual_saving_eur is not None:
                    delta_eoy_budget = round(eoy_forecast_eur - expected_annual_saving_eur, 2)

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
                    "applicable_amount_eur": round(float(r.applicable_amount) * fx, 2)
                    if r.applicable_amount is not None and not fx_missing
                    else None,
                    # "Value of Opportunity" = total multi-year gain, shown alongside
                    # the per-year "à budgéter" so a 0 / small budget year (flat price)
                    # is understandable in context. New STP opps carry it in
                    # period_saving; SB12-imported opps have period_saving=None and store
                    # the total in expected_annual_saving instead -> fall back to it.
                    "value_of_opportunity_eur": round(
                        float(opp.period_saving if opp.period_saving is not None
                              else opp.expected_annual_saving) * fx, 2)
                    if (opp.period_saving is not None or opp.expected_annual_saving is not None)
                    and not fx_missing
                    else None,
                    "fx_missing": fx_missing,
                    "portion_kind": r.portion_kind,
                    "suggested_status": r.suggested_status,
                    "budget_status": r.budget_status,
                    "is_additional": bool(r.is_additional),
                    "status_locked_at": r.status_locked_at.isoformat()
                    if r.status_locked_at
                    else None,
                    "status_locked_by": r.status_locked_by,
                    "plant_id": opp.plant_id,
                    "delta_reason": list(r.delta_reason) if r.delta_reason else [],
                    "eoy_forecast_eur": eoy_forecast_eur,
                    "expected_annual_saving_eur": expected_annual_saving_eur,
                    "actual_ytd_eur": actual_ytd_eur,
                    "delta_ytd_eur": delta_ytd_eur,
                    "delta_eoy_budget": delta_eoy_budget,
                    "saving_actual_fy_eur": saving_actual_fy_eur,
                    "cash_expected_eur": cash_expected_eur,
                    "cash_actual_eur": cash_actual_eur,
                    "real_start_date": opp.real_start_date.isoformat()
                    if opp.real_start_date
                    else None,
                    "execution_start_date": opp.execution_start_date.isoformat()
                    if opp.execution_start_date
                    else None,
                    "planned_start_date": opp.planned_start_date.isoformat()
                    if opp.planned_start_date
                    else None,
                    # Opportunity has no created_at column — study_start_date (set
                    # when the buyer clicks "Start Study", Phase 0 kickoff) is the
                    # closest proxy for "when this opportunity began".
                    "created_at": opp.created_at.isoformat()
                    if opp.created_at
                    else None,
                    "duration_months": int(opp.duration_months)
                    if opp.duration_months is not None
                    else None,
                }
            )
        return items

    async def update_delta_reasons(self, fiscal_year: int, decisions: list) -> dict:
        """Update delta_reason only — does not touch budget_status or lock timestamps."""
        reason_by_opp: dict[int, list] = {
            d["opportunity_id"]: d.get("delta_reason") or []
            for d in decisions
            if d.get("opportunity_id") is not None
        }
        rows = (
            await self.db.execute(
                select(OpportunityBudgetYear).where(
                    OpportunityBudgetYear.fiscal_year == fiscal_year,
                    OpportunityBudgetYear.is_deleted.is_(False),
                )
            )
        ).scalars().all()
        updated = 0
        for r in rows:
            if r.opportunity_id in reason_by_opp:
                r.delta_reason = reason_by_opp[r.opportunity_id] or None
                updated += 1
        await self.db.flush()
        return {"updated": updated}

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
        # Explicit manual override of the Additional bucket, independent of
        # budget_status — lets a director flag/unflag "Additional" even on an
        # opportunity in a still-open fiscal year, not just automatically at
        # closure. None entries (key absent or null) mean "leave unchanged".
        is_additional_by_opp = {
            d["opportunity_id"]: d["is_additional"]
            for d in (decisions or [])
            if d.get("opportunity_id") is not None and d.get("is_additional") is not None
        }
        # Preserve existing delta reasons unless the assign payload explicitly
        # includes a replacement. Create Budget usually updates status only.
        delta_reason_by_opp = {
            d["opportunity_id"]: d.get("delta_reason")
            for d in (decisions or [])
            if d.get("opportunity_id") is not None and "delta_reason" in d
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
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )

        # A closed fiscal year's baseline (non-additional) rows are the frozen
        # historical commitment Finance already reported on — they must not be
        # re-decided through this endpoint after closure. Additional rows are
        # explicitly exempt: accepting/rejecting post-closure additions is the
        # whole point of the "Additional Opportunities" flow.
        year_is_closed = fiscal_year in await self._closed_fiscal_years()

        now = datetime.utcnow()
        counts = {"Empty": 0, "Opportunity": 0, "Budgeted": 0}
        for r in rows:
            opp = r.opportunity
            if opp is None or opp.is_deleted:
                continue
            # H4 — never lock budget on cancelled or closed opportunities
            if opp.status == "Cancelled" or opp.phase_status == "Closed":
                continue
            new_status = by_opp.get(opp.opportunity_id)
            if new_status is None:
                continue
            incoming_is_additional = is_additional_by_opp.get(opp.opportunity_id)
            effective_is_additional = (
                incoming_is_additional
                if incoming_is_additional is not None
                else r.is_additional
            )
            if year_is_closed and not effective_is_additional:
                raise AppException(
                    409,
                    f"Fiscal year {fiscal_year} is closed — baseline budget rows "
                    "are locked and cannot be re-decided. Only additional "
                    "(post-closure) opportunities can be accepted/rejected.",
                    "BUDGET_YEAR_CLOSED",
                )
            r.budget_status = new_status
            if incoming_is_additional is not None:
                r.is_additional = incoming_is_additional
            r.status_locked_at = now
            r.status_locked_by = decided_by
            if opp.opportunity_id in delta_reason_by_opp:
                r.delta_reason = delta_reason_by_opp[opp.opportunity_id]
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

    async def close_budget_year(self, fiscal_year: int, user_email: str) -> dict:
        """Officially close the budget for a fiscal year.

        Creates a BudgetYearClosure record (one per FY, unique constraint prevents
        double-close). Locks all Budgeted rows for this FY that are not yet locked
        (rows already locked by assign_budget_year keep their original timestamp).
        From this point on, any new OpportunityBudgetYear row created for this FY by
        _sync_budget_years will be flagged is_additional=True.
        """
        from app.db.models import BudgetYearClosure
        existing_closure = (
            await self.db.execute(
                select(BudgetYearClosure).where(BudgetYearClosure.fiscal_year == fiscal_year)
            )
        ).scalar_one_or_none()
        if existing_closure is not None:
            raise AppException(
                f"Budget year {fiscal_year} is already closed "
                f"(closed on {existing_closure.closed_at.date()} by {existing_closure.closed_by}).",
                status_code=409,
            )

        now = datetime.utcnow()
        closure = BudgetYearClosure(fiscal_year=fiscal_year, closed_at=now, closed_by=user_email)
        self.db.add(closure)

        # Lock all Budgeted rows not yet locked
        rows = (
            await self.db.execute(
                select(OpportunityBudgetYear)
                .where(
                    OpportunityBudgetYear.fiscal_year == fiscal_year,
                    OpportunityBudgetYear.is_deleted.is_(False),
                    OpportunityBudgetYear.budget_status == "Budgeted",
                    OpportunityBudgetYear.status_locked_at.is_(None),
                )
                .with_for_update()
            )
        ).scalars().all()

        for row in rows:
            row.status_locked_at = now
            row.status_locked_by = user_email

        await self.db.flush()
        return {
            "fiscal_year": fiscal_year,
            "closed_at": now.isoformat(),
            "closed_by": user_email,
            "newly_locked": len(rows),
        }

    async def get_budget_year_closure(self, fiscal_year: int) -> Optional[dict]:
        """Return the closure record for a fiscal year, or None if not closed."""
        from app.db.models import BudgetYearClosure
        row = (
            await self.db.execute(
                select(BudgetYearClosure).where(BudgetYearClosure.fiscal_year == fiscal_year)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return {
            "fiscal_year": row.fiscal_year,
            "closed_at": row.closed_at.isoformat(),
            "closed_by": row.closed_by,
        }

    async def _rebuild_monthly_profile(
        self,
        line: FinancialLine,
        annual_saving: Decimal,
        new_start: date,
        duration_months: int,
        is_period_total: bool = False,
        windows: Optional[list] = None,
        cash_annual: Optional[Decimal] = None,
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
            # Day-level prorated across the FULL nominal duration from new_start (same
            # engine as _generate_monthly_profile / _day_prorated_ideals), then sliced
            # to just the tail being rebuilt — guarantees a revised line's remaining
            # months reconcile with the Budgeting page exactly like a freshly-created
            # grid would, instead of the old flat per-month math.
            full_ideals = self._day_prorated_ideals(
                new_start, duration_months, is_period_total, windows, annual_saving
            )
            tail_ideals = full_ideals[base_offset : base_offset + months_remaining]
            monthlies = self._rounded_series(tail_ideals)

            cash_series = None
            if cash_annual:
                # One-shot cash lives only in month 0 of the full duration (see
                # _one_shot_cash_ideals) — if that month already has actuals
                # (base_offset > 0), it was preserved and must not be repeated here.
                full_cash_ideals = self._one_shot_cash_ideals(duration_months, cash_annual)
                tail_cash_ideals = full_cash_ideals[base_offset : base_offset + months_remaining]
                cash_series = self._rounded_series(tail_cash_ideals)

            new_rows = []
            for i in range(months_remaining):
                period = add_months(rebuild_start, i)
                new_rows.append(
                    MonthlyFinancial(
                        financial_line_id=line.financial_line_id,
                        period_month=period,
                        expected_saving=monthlies[i],
                        cash_expected=cash_series[i] if cash_series else None,
                    )
                )
            self.db.add_all(new_rows)

        # Update the financial line real_start_date and duration — keeps
        # line.duration_months from going stale relative to the opportunity (see
        # _ensure_monthly_rows, which does the same sync on the initial grid).
        line.real_start_date = new_start
        line.duration_months = Decimal(str(duration_months))

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

    @staticmethod
    def _day_prorated_ideals(
        start_date: date,
        duration_months: int,
        is_period_total: bool,
        windows: Optional[list],
        annual: Decimal,
    ) -> List[float]:
        """UNROUNDED monthly ideals, day-level prorated across EVERY month (not just
        the first) — mirrors compute_budget_year_portions's day-count math
        (schemas.py), sliced by calendar month instead of fiscal year, so a monthly
        grid summed by calendar year reconciles exactly with the Budgeting page's
        Saving/Cash FY figures (OpportunityBudgetYear.applicable_amount).

        The duration is split into consecutive 12-month "windows" anchored on
        `start_date` (day preserved via add_months_preserve_day, so a window
        genuinely spans 365/366 days, not 12 calendar-month boundaries). Each
        window is spread at a uniform daily rate = window_amount / NOMINAL
        window_days (the full 12-month span, even when the window is cut short
        by `duration_months`). STP escalating windows get one window each
        (`windows`); otherwise `annual` repeats every 12-month window. A
        period-total (STP-period types with no escalation) is treated as a
        single window spanning the whole duration (never truncated). When
        `duration_months` isn't an exact multiple of a window's span, the final
        window is truncated: its target is scaled down to
        window_amount * (truncated_days / nominal_days) — otherwise the last
        row would absorb the FULL window_amount into only a few months,
        overpaying the tail by roughly the truncation ratio. The last row of
        each (possibly truncated) window absorbs whatever remains so that
        window's rows sum exactly to its (possibly scaled) target (same
        reconciliation contract as compute_budget_year_portions /
        _rounded_series)."""
        if duration_months <= 0:
            return []

        if is_period_total and not windows:
            window_amounts = [annual]
            window_spans = [duration_months]
        elif is_period_total and windows:
            window_amounts = [Decimal(str(w)) for w in windows]
            window_spans = [12] * len(window_amounts)
        else:
            n_windows = -(-duration_months // 12)  # ceil
            window_amounts = [annual] * n_windows
            window_spans = [12] * n_windows

        ideals = [0.0] * duration_months
        month_offset = 0
        for w_amount, w_span in zip(window_amounts, window_spans):
            span = min(w_span, duration_months - month_offset)
            if span <= 0:
                break
            window_start = add_months_preserve_day(start_date, month_offset)
            nominal_window_end = add_months_preserve_day(start_date, month_offset + w_span)
            truncated_window_end = add_months_preserve_day(start_date, month_offset + span)
            nominal_window_days = (nominal_window_end - window_start).days
            truncated_window_days = (truncated_window_end - window_start).days
            last_row = month_offset + span - 1
            if nominal_window_days <= 0 or truncated_window_days <= 0:
                month_offset += span
                continue
            window_target = (
                w_amount * Decimal(truncated_window_days) / Decimal(nominal_window_days)
            )
            allocated = Decimal("0")
            for i in range(month_offset, month_offset + span):
                if i == last_row:
                    ideals[i] += float(window_target - allocated)
                    break
                cal_month_start = add_months(start_date, i)
                cal_month_end = add_months(start_date, i + 1)
                overlap_start = max(cal_month_start, window_start)
                overlap_end = min(cal_month_end, truncated_window_end)
                days = (overlap_end - overlap_start).days
                if days <= 0:
                    continue
                share = w_amount * Decimal(days) / Decimal(nominal_window_days)
                ideals[i] += float(share)
                allocated += share
            month_offset += span
        return ideals

    @staticmethod
    def _one_shot_cash_ideals(duration_months: int, cash_annual: Decimal) -> List[float]:
        """`cash_impact` is realized once, in the fiscal year of the real start —
        never spread across the deal's duration. Booking the full amount in the
        first month (which belongs to exactly one fiscal year) and 0 everywhere
        else means every later FY's cash total is naturally 0, with no proration."""
        if duration_months <= 0:
            return []
        return [float(cash_annual)] + [0.0] * (duration_months - 1)

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
        """Create one MonthlyFinancial row per month. Expected saving is day-level
        prorated across every month via _day_prorated_ideals — see that method for
        the reconciliation contract with the Budgeting page.

        `cash_annual`, unlike `annual_saving`, is never spread — it is realized once,
        booked entirely in the first month (the real start), with every later month
        (and therefore every later fiscal year) at 0. See _one_shot_cash_ideals."""
        monthlies = self._rounded_series(
            self._day_prorated_ideals(
                start_date, duration_months, is_period_total, windows, annual_saving
            )
        )
        cash_series = (
            self._rounded_series(
                self._one_shot_cash_ideals(duration_months, cash_annual)
            )
            if cash_annual
            else None
        )
        rows: List[MonthlyFinancial] = []
        for i in range(duration_months):
            period = add_months(start_date, i)
            rows.append(
                MonthlyFinancial(
                    financial_line_id=line.financial_line_id,
                    period_month=period,
                    expected_saving=monthlies[i],
                    cash_expected=cash_series[i] if cash_series else None,
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
        # Keep the line's duration in sync with the opportunity's current value —
        # it's otherwise only ever set once at line creation and would go stale if
        # opp.duration_months is edited afterward, silently desyncing any later
        # rebuild (Revise Baseline / STP revision) that reads line.duration_months.
        line.duration_months = Decimal(str(duration_months))
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
    #
    # DISABLED: request_stp_revision is currently unreachable — its router
    # endpoint (POST /opportunities/{id}/request-stp-revision) is commented out
    # in purchasing_value/router.py, so no new revision request (and therefore
    # no email/notification fan-out) can be created. decide_stp_revision is
    # kept live to resolve any request that was already pending beforehand.
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

        Who calls this: any non-viewer role (purchasing_manager, supplier_owner,
        global_purchaser, local_purchaser) — i.e. everyone EXCEPT purchasing_director
        and vp_conversion, who edit the STP baseline directly instead (see the
        STP_REQUIRES_APPROVAL gate in `update_opportunity`) since they ARE the
        approvers and don't need to ask themselves for permission.

        Only callable in Phase 2/3 (checked below) — Phase 0/1 baseline is freely
        editable by everyone, no revision workflow needed there.

        Current values remain active — nothing changes on the opportunity yet.
        Proposed values are stored in `pending_stp_revision` JSONB (only the fields
        the buyer actually filled in); a savings-impact preview is computed and
        included so the Director/VP can assess it before deciding.

        Notification fan-out (both happen here, not deferred): every ACTIVE
        AccessIdentity currently holding purchasing_director or vp_conversion is
        resolved by role (not a free-text email the requester types in) and gets
        BOTH an email (`_build_stp_revision_request_email`) AND an in-app
        Notification (`stp_revision_request`) linking back to this opportunity.
        Approvers added/removed after this point are not retroactively notified —
        the fan-out is a snapshot taken at request time.
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
        _neg_preview = [
            (f"N+{i}" if i > 0 else "N", float(v))
            for i, v in enumerate(preview_fin.get("saving_per_year") or [])
            if v is not None and float(v) < 0
        ]
        if _neg_preview:
            detail = ", ".join(f"Year {lbl}: {amt:,.0f} €" for lbl, amt in _neg_preview)
            raise AppException(
                422,
                f"The proposed values produce negative savings for: {detail}. "
                "Proposed price exceeds current price — please review before submitting.",
                "STP_NEGATIVE_SAVING",
            )

        # Approvers are resolved by role, not chosen by the requester — anyone
        # holding purchasing_director or vp_conversion at request time is notified.
        approvers_stmt = select(AccessIdentity).where(
            AccessIdentity.access_profile.in_(["purchasing_director", "vp_conversion"]),
            AccessIdentity.is_active.is_(True),
        )
        approvers = list((await self.db.execute(approvers_stmt)).scalars().all())
        approver_emails = [a.email for a in approvers if a.email]

        now = datetime.utcnow()
        per_year = preview_fin.get("saving_per_year") or [None, None, None, None]
        opp.pending_stp_revision = {
            "requested_by":    payload.requested_by,
            "requested_at":    now.isoformat(),
            "director_emails": approver_emails,
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

        # Notify Directors/VP Conversion by email + in-app notification
        if approver_emails:
            try:
                body = _build_stp_revision_request_email(opp, payload, opp.pending_stp_revision["computed_preview"])
                await send_email(
                    subject=f"[STP Revision Approval] {opp.opportunity_name}",
                    recipients=approver_emails,
                    body_html=body,
                )
            except Exception as exc:
                logger.warning("STP revision request email failed for opp %s: %s", opportunity_id, exc)

        for identity in approvers:
            self.db.add(Notification(
                recipient_id=identity.id_identity,
                notification_type="stp_revision_request",
                title=f"STP revision to approve: {opp.opportunity_name}",
                body=f"{payload.requested_by or 'A buyer'} requested a change to the STP baseline. Justification: {payload.note}",
                action_url=f"/purchasing-value?opp={opportunity_id}",
            ))

        await self.db.flush()
        await self.db.refresh(opp, ["projects", "financial_lines", "opp_documents", "budget_years", "plant"])
        return opp

    async def decide_stp_revision(
        self,
        opportunity_id: int,
        payload: STPRevisionDecisionPayload,
    ) -> Opportunity:
        """Purchasing Director approves or rejects a pending STP revision request.

        Who calls this: ONLY purchasing_director / vp_conversion — enforced at the
        router (`_PRIVILEGED` in purchasing_value/router.py) and mirrored on the
        frontend (Approve/Reject button + modal only rendered for these roles).
        Any other role hitting this endpoint gets a 403 before this method runs.

        Approved  → proposed values applied, STP financials recomputed, monthly
                    profile rebuilt (if financial line active and no actuals yet).
        Rejected  → pending revision discarded, current values unchanged.
        Both      → audit entry appended to opp.comments; the ORIGINAL requester
                    (not the approvers) gets an email + in-app Notification of
                    the decision, symmetric with the fan-out in request_stp_revision.
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
            # ROI and cash fields must also be refreshed — they depend on the same
            # STP formulas and are stale without this block.
            if stp_fin["roi_full_year_pct"] is not None:
                opp.roi_percent = Decimal(str(stp_fin["roi_full_year_pct"]))
            if stp_fin["roi_period_pct"] is not None:
                opp.roi_period_percent = Decimal(str(stp_fin["roi_period_pct"]))
            if stp_fin["inventory_gap"] is not None:
                opp.cash_inventory_gap = Decimal(str(stp_fin["inventory_gap"]))
            if stp_fin["ap_gap"] is not None:
                opp.cash_ap_gap = Decimal(str(stp_fin["ap_gap"]))
            if stp_fin["inventory_gap"] is not None or stp_fin["ap_gap"] is not None:
                opp.cash_impact = Decimal(str(round(
                    (stp_fin["inventory_gap"] or 0.0) + (stp_fin["ap_gap"] or 0.0), 2
                )))

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
                            fl.real_start_date, int(opp.duration_months or 12),
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

            try:
                identity_result = await self.db.execute(
                    select(AccessIdentity).where(AccessIdentity.email.ilike(requester_email))
                )
                identity = identity_result.scalar_one_or_none()
                if identity:
                    self.db.add(Notification(
                        recipient_id=identity.id_identity,
                        notification_type="stp_revision_decision",
                        title=f"STP revision {payload.decision.lower()}: {opp.opportunity_name}",
                        body=f"Your requested STP revision was {payload.decision.lower()} by {payload.decided_by or 'the Director'}.",
                        action_url=f"/purchasing-value?opp={opportunity_id}",
                    ))
            except Exception:
                pass  # Non-blocking — email notification already covers delivery

        await self.db.flush()
        await self.db.refresh(opp, ["projects", "financial_lines", "opp_documents", "budget_years", "plant"])
        return opp

    # ------------------------------------------------------------------
    # Document management
    # ------------------------------------------------------------------

    async def revise_financial_line_baseline(
        self,
        line_id: int,
        payload: FinancialLineReviseBaselineRequest,
    ) -> FinancialLine:
        """Correct the committed STP baseline for a line that ALREADY has actuals
        (Phase 3+) — the only path to change price/quantity/bonus (or the flat
        annual saving for Negotiation/Cash) once real savings have started, since
        update_opportunity's direct-edit is unconditionally blocked by the actuals
        governance lock there (BASELINE_LOCKED_ACTUALS) — even for
        purchasing_director/vp_conversion. Restricted to _PRIVILEGED at the router.

        Unlike the old version (which just scaled a typed-in annual number), this
        recomputes through the SAME engine as the rest of the app:
          - Sourcing/Technical Productivity: apply the proposed price/quantity/
            bonus fields, recompute via compute_stp_financials (period_saving,
            saving_year_n..n3, ROI, cash gaps) — mirrors decide_stp_revision's
            Approved branch exactly, so results stay consistent everywhere the
            opportunity's figures are displayed (STP tab, PDFs, KPIs).
          - Negotiation/Cash (no price/quantity breakdown): apply revised_saving
            directly to expected_annual_saving.
        Then:
          - Rebuild the monthly profile via _rebuild_monthly_profile, which
            preserves every already-entered actual and regenerates only the
            remaining (unactualized) months from the corrected figures.
          - Re-sync ALL fiscal-year OpportunityBudgetYear rows via
            _sync_budget_years — the old version never did this, leaving the
            Budgeting page and KPIs stale after a revision.
          - Append a structured entry to opp.revision_history (JSONB array,
            append-only) with a before/after snapshot of both the raw fields and
            the computed results, so every committed-baseline correction is
            permanently queryable — not just a line in a growing comment string.
        """
        line = await self.get_financial_line(line_id)
        if line.status != "Active":
            raise AppException(
                422, "Can only revise an active financial line.", "LINE_NOT_ACTIVE"
            )
        opp = await self.get_opportunity(line.opportunity_id)
        if opp.phase_status == "Closed":
            raise AppException(
                422,
                "Cannot revise the baseline of a Closed opportunity.",
                "OPPORTUNITY_CLOSED",
            )

        is_stp = self._is_period(opp)

        def _snapshot_computed() -> dict:
            return {
                "expected_annual_saving": float(opp.expected_annual_saving) if opp.expected_annual_saving is not None else None,
                "period_saving": float(opp.period_saving) if opp.period_saving is not None else None,
                "roi_percent": float(opp.roi_percent) if opp.roi_percent is not None else None,
                "roi_period_percent": float(opp.roi_period_percent) if opp.roi_period_percent is not None else None,
                "cash_impact": float(opp.cash_impact) if opp.cash_impact is not None else None,
            }

        previous_fields = (
            {f: float(getattr(opp, f)) if getattr(opp, f) is not None else None for f in self._STP_BASELINE_FIELDS}
            if is_stp
            else {"expected_annual_saving": float(opp.expected_annual_saving) if opp.expected_annual_saving is not None else None}
        )
        previous_computed = _snapshot_computed()

        if is_stp:
            proposed: dict = {}
            for field in self._STP_BASELINE_FIELDS:
                val = getattr(payload, field, None)
                if val is not None:
                    proposed[field] = float(val) if isinstance(val, Decimal) else val
            if not proposed:
                raise AppException(
                    422,
                    "At least one STP field (price, quantity, bonus) must be provided.",
                    "NO_FIELDS_PROVIDED",
                )
            for field, value in proposed.items():
                setattr(opp, field, Decimal(str(value)) if isinstance(value, (int, float)) else value)

            # Recompute STP financials — identical chain to decide_stp_revision's Approved branch.
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
            if stp_fin["roi_full_year_pct"] is not None:
                opp.roi_percent = Decimal(str(stp_fin["roi_full_year_pct"]))
            if stp_fin["roi_period_pct"] is not None:
                opp.roi_period_percent = Decimal(str(stp_fin["roi_period_pct"]))
            if stp_fin["inventory_gap"] is not None:
                opp.cash_inventory_gap = Decimal(str(stp_fin["inventory_gap"]))
            if stp_fin["ap_gap"] is not None:
                opp.cash_ap_gap = Decimal(str(stp_fin["ap_gap"]))
            if stp_fin["inventory_gap"] is not None or stp_fin["ap_gap"] is not None:
                opp.cash_impact = Decimal(str(round(
                    (stp_fin["inventory_gap"] or 0.0) + (stp_fin["ap_gap"] or 0.0), 2
                )))
            new_fields = proposed
        else:
            if payload.revised_saving is None:
                raise AppException(
                    422,
                    "revised_saving is required for Negotiation/Cash opportunities.",
                    "MISSING_REVISED_SAVING",
                )
            opp.expected_annual_saving = payload.revised_saving
            new_fields = {"expected_annual_saving": float(payload.revised_saving)}

        # Rebuild the monthly profile for THIS line — preserves every actual already
        # entered, regenerates only the remaining (unactualized) months.
        # budget_value is untouched — it is the original budget commitment, distinct
        # from the corrected expected/estimated saving.
        # Duration comes from the opportunity, not the line — line.duration_months
        # is only synced on rebuild (see _ensure_monthly_rows/_rebuild_monthly_profile)
        # and could otherwise be stale if opp.duration_months changed since.
        duration = int(opp.duration_months or 12)
        start = line.real_start_date or line.planned_start_date or date.today().replace(day=1)
        cash_annual = (
            opp.cash_impact if opp.opportunity_type in ("Negotiation", "Cash") else None
        )
        await self._rebuild_monthly_profile(
            line, opp.expected_annual_saving or Decimal("0"), start, duration,
            is_period_total=is_stp,
            windows=self._stp_year_windows(opp) if is_stp else None,
            cash_annual=cash_annual,
        )
        await self._recalculate_ytd(line_id)

        # Propagate the correction to every fiscal-year budget row (current AND
        # future years) so the Budgeting page and KPIs never go stale.
        await self._sync_budget_years(opp)

        now = datetime.utcnow()
        history_entry = {
            "revised_at": now.isoformat(),
            "revised_by": payload.revised_by,
            "note": payload.note,
            "opportunity_type": opp.opportunity_type,
            "financial_line_id": line.financial_line_id,
            "previous_fields": previous_fields,
            "new_fields": new_fields,
            "previous_computed": previous_computed,
            "new_computed": _snapshot_computed(),
        }
        # Reassign (don't mutate in place) so SQLAlchemy's change tracking picks it up.
        opp.revision_history = (opp.revision_history or []) + [history_entry]

        line.comments = (line.comments or "") + (
            f"\n[Baseline revised {now.strftime('%Y-%m-%d')} by {payload.revised_by or 'system'}] "
            f"Reason: {payload.note}"
        )
        line.updated_at = now
        line.updated_by = payload.revised_by
        opp.updated_at = now
        opp.updated_by = payload.revised_by

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
            .join(SupplierGroup, SupplierGroup.id_group == SupplierUnit.id_group)
            .where(
                SupplierSiteRelation.id_site == plant_id,
                SupplierSiteRelation.validation_status == "approved",
                SupplierSiteRelation.panel_decision.in_(PANEL_ACTIVE_DECISIONS),
                SupplierSiteRelation.is_active.is_(True),
                SupplierSiteRelation.is_deleted.is_(False),
                SupplierUnit.is_active.is_(True),
                SupplierUnit.is_deleted.is_(False),
                SupplierGroup.is_active.is_(True),
                SupplierGroup.is_deleted.is_(False),
            )
            .options(selectinload(SupplierUnit.group))
            .order_by(SupplierUnit.id_supplier_unit)
            .distinct()
        )
        units = result.scalars().all()
        return [
            {
                "id_supplier_unit": u.id_supplier_unit,
                "supplier_name": u.supplier_name,
                "group_name": u.group.nom if u.group else None,
                "city": u.city,
                "country": u.country,
            }
            for u in units
        ]

    # -----------------------------------------------------------------------
    # Action Plan methods
    # -----------------------------------------------------------------------


    async def list_action_plans(self, opportunity_id: int):
        from app.db.models import OpportunityActionPlan
        result = await self.db.execute(
            select(OpportunityActionPlan)
            .where(OpportunityActionPlan.opportunity_id == opportunity_id)
            .order_by(OpportunityActionPlan.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_action_plan(self, action_plan_id: int, expected_opportunity_id: Optional[int] = None):
        from app.db.models import OpportunityActionPlan
        result = await self.db.execute(
            select(OpportunityActionPlan).where(
                OpportunityActionPlan.action_plan_id == action_plan_id
            )
        )
        plan = result.scalar_one_or_none()
        if plan is None:
            raise AppException("Action plan not found", status_code=404)
        # Routes are nested under /opportunities/{opportunity_id}/action-plans/{action_plan_id}
        # but action_plan_id alone is a valid, sufficient lookup key — without this
        # check a mismatched pair (typo, stale link, or wrong opportunity_id) would
        # silently operate on a different opportunity's plan.
        if expected_opportunity_id is not None and plan.opportunity_id != expected_opportunity_id:
            raise AppException(
                404,
                "Action plan not found for this opportunity.",
                "ACTION_PLAN_OPPORTUNITY_MISMATCH",
            )
        return plan

    @staticmethod
    def _validate_closed_actions(sujets: list) -> None:
        """Enforce the same close-out rule everywhere an action's status can be
        set to "closed": a closed_date is required.
        Mirrors the check in update_action_item_status so the bulk plan
        create/update path can't bypass it."""
        for sujet in sujets:
            for action in sujet.get("actions", []):
                if action.get("status") != "closed":
                    continue
                if not action.get("closed_date"):
                    raise AppException(
                        422,
                        f"Implementation date is required to close action '{action.get('titre', '')}'.",
                        "IMPLEMENTATION_DATE_REQUIRED",
                    )

    @staticmethod
    def _log_action_event(action: dict, event: str, by: str, **details) -> None:
        """Append a timestamped entry to an action's audit trail (IATF traceability:
        every status change, reminder, escalation and attachment must be recorded)."""
        entry = {
            "event": event,
            "by": by,
            "at": datetime.utcnow().isoformat(),
            **details,
        }
        action.setdefault("history", []).append(entry)

    async def create_action_plan(self, opportunity_id: int, payload, user_email: str):
        from app.db.models import OpportunityActionPlan

        # Verify opportunity exists
        await self.get_opportunity(opportunity_id)

        plan_code = payload.plan_code or f"SM-OPP-{opportunity_id}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"

        def _strip_none(obj):
            if isinstance(obj, dict):
                return {k: _strip_none(v) for k, v in obj.items() if v is not None}
            if isinstance(obj, list):
                return [_strip_none(i) for i in obj]
            return obj

        # plan_data is the exact payload that will be sent to the external API on sync.
        # Format matches POST /api/v2/plans expected by sales-feedback service.
        plan_data = _strip_none({
            "version": "2.0",
            "plan_code": plan_code,
            "plan_title": payload.plan_title,
            "inserted_by": user_email,
            "responsable": payload.responsable,
            "email_responsable": payload.email_responsable,
            "demandeur": payload.demandeur,
            "email_demandeur": payload.email_demandeur,
            "sujets": [s.model_dump(mode="json") for s in payload.sujets],
        })
        self._validate_closed_actions(plan_data["sujets"])

        # External push disabled — stored locally, sync via POST .../sync when ready.
        # TODO: re-enable once ACTION_PLAN_DATABASE_URL is configured on Azure.
        # push_status, push_error = await self._push_to_external(plan_data)
        push_status = "pending"
        push_error = None

        now = datetime.utcnow()
        plan = OpportunityActionPlan(
            opportunity_id=opportunity_id,
            phase_status=payload.phase_status,
            plan_title=payload.plan_title,
            plan_code=plan_code,
            plan_data=plan_data,
            external_push_status=push_status,
            external_push_error=push_error,
            created_at=now,
            created_by=user_email,
            updated_at=now,
            updated_by=user_email,
        )
        self.db.add(plan)
        await self.db.flush()
        return plan

    async def update_action_plan(self, action_plan_id: int, payload, user_email: str, opportunity_id: Optional[int] = None):
        plan = await self.get_action_plan(action_plan_id, opportunity_id)

        if payload.plan_title is not None:
            plan.plan_title = payload.plan_title
        if payload.phase_status is not None:
            plan.phase_status = payload.phase_status

        existing = dict(plan.plan_data or {})

        def _strip_none(obj):
            if isinstance(obj, dict):
                return {k: _strip_none(v) for k, v in obj.items() if v is not None}
            if isinstance(obj, list):
                return [_strip_none(i) for i in obj]
            return obj

        if payload.responsable is not None:
            existing["responsable"] = payload.responsable
        if payload.email_responsable is not None:
            existing["email_responsable"] = payload.email_responsable
        if payload.demandeur is not None:
            existing["demandeur"] = payload.demandeur
        if payload.email_demandeur is not None:
            existing["email_demandeur"] = payload.email_demandeur
        if payload.sujets is not None:
            existing["sujets"] = _strip_none([s.model_dump(mode="json") for s in payload.sujets])
            self._validate_closed_actions(existing["sujets"])
        if payload.plan_title is not None:
            existing["plan_title"] = payload.plan_title

        plan.plan_data = existing
        flag_modified(plan, "plan_data")
        plan.updated_at = datetime.utcnow()
        plan.updated_by = user_email

        # External push disabled — mark as pending for future sync.
        # TODO: re-enable once ACTION_PLAN_DATABASE_URL is configured on Azure.
        # push_status, push_error = await self._push_to_external(existing)
        plan.external_push_status = "pending"
        plan.external_push_error = None

        await self.db.flush()
        return plan

    async def sync_action_plan(self, action_plan_id: int, opportunity_id: Optional[int] = None) -> dict:
        """Push a locally stored action plan to the external sales-feedback API.
        Call POST .../action-plans/{id}/sync once ACTION_PLAN_DATABASE_URL is configured.
        Returns {"status": "ok"} or raises AppException on failure.
        """
        import httpx
        from app.core.config import settings

        plan = await self.get_action_plan(action_plan_id, opportunity_id)
        if not plan.plan_data:
            raise AppException(400, "Plan has no data to sync.", "NO_PLAN_DATA")

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{settings.ACTION_PLAN_API_URL}/api/v2/plans",
                    json=plan.plan_data,
                )
            if resp.status_code in (200, 201):
                plan.external_push_status = "ok"
                plan.external_push_error = None
                await self.db.flush()
                return {"status": "ok", "external_response": resp.json()}
            else:
                plan.external_push_status = "failed"
                plan.external_push_error = resp.text[:500]
                await self.db.flush()
                raise AppException(502, f"External API error {resp.status_code}: {resp.text[:200]}", "SYNC_FAILED")
        except AppException:
            raise
        except Exception as exc:
            plan.external_push_status = "failed"
            plan.external_push_error = str(exc)[:500]
            await self.db.flush()
            raise AppException(502, f"Could not reach external API: {exc}", "SYNC_UNREACHABLE")

    async def delete_action_plan(self, action_plan_id: int, opportunity_id: Optional[int] = None) -> None:
        plan = await self.get_action_plan(action_plan_id, opportunity_id)
        await self.db.delete(plan)
        await self.db.flush()

    async def list_all_action_items(
        self,
        responsible_email: Optional[str] = None,
        status: Optional[str] = None,
        opportunity_id: Optional[int] = None,
        viewer_email: Optional[str] = None,
        viewer_role: Optional[str] = None,
    ) -> list[dict]:
        """Flatten all action plan actions across all opportunities into a single list.

        Each item represents one action inside a sujet, enriched with plan + opportunity context.
        Useful for the cross-opp action management dashboard grouped by responsible person.
        """
        from app.db.models import OpportunityActionPlan

        q = select(OpportunityActionPlan)
        if opportunity_id:
            q = q.where(OpportunityActionPlan.opportunity_id == opportunity_id)
        result = await self.db.execute(q)
        plans = list(result.scalars().all())

        opp_ids = {p.opportunity_id for p in plans}
        if opp_ids:
            opps_result = await self.db.execute(
                select(Opportunity).where(
                    Opportunity.opportunity_id.in_(opp_ids),
                    Opportunity.is_deleted.is_(False),
                )
            )
            opp_by_id: dict[int, Opportunity] = {o.opportunity_id: o for o in opps_result.scalars().all()}
        else:
            opp_by_id = {}

        items: list[dict] = []
        for plan in plans:
            if not plan.plan_data:
                continue
            opp = opp_by_id.get(plan.opportunity_id)
            opp_name = (opp.opportunity_name or f"Opp #{plan.opportunity_id}") if opp else f"Opp #{plan.opportunity_id}"
            plan_resp_email = plan.plan_data.get("email_responsable")
            plan_resp_name = plan.plan_data.get("responsable")

            for s_idx, sujet in enumerate(plan.plan_data.get("sujets", [])):
                for a_idx, action in enumerate(sujet.get("actions", [])):
                    act_email = action.get("email_responsable") or plan_resp_email
                    act_name = action.get("responsable") or plan_resp_name
                    if responsible_email and act_email != responsible_email:
                        continue
                    if status and action.get("status") != status:
                        continue
                    items.append({
                        "plan_id": plan.action_plan_id,
                        "plan_code": plan.plan_code,
                        "plan_title": plan.plan_title,
                        "plan_created_at": plan.created_at.isoformat() if plan.created_at else None,
                        "plan_created_by": plan.created_by,
                        "plan_updated_at": plan.updated_at.isoformat() if plan.updated_at else None,
                        "plan_updated_by": plan.updated_by,
                        "opportunity_id": plan.opportunity_id,
                        "opportunity_name": opp_name,
                        "opp_phase": opp.phase_status if opp else None,
                        "sujet_idx": s_idx,
                        "action_idx": a_idx,
                        "sujet_titre": sujet.get("titre"),
                        "action_titre": action.get("titre"),
                        "action_status": action.get("status", "open"),
                        "due_date": action.get("due_date"),
                        "closed_date": action.get("closed_date"),
                        "responsible_name": act_name,
                        "responsible_email": act_email,
                        "attachments": action.get("attachments", []),
                        "attachment_count": len(action.get("attachments", [])),
                        "description": action.get("description"),
                        "history": action.get("history", []),
                        "last_reminded_at": action.get("last_reminded_at"),
                        "last_reminded_to": action.get("last_reminded_to"),
                        "last_escalated_at": action.get("last_escalated_at"),
                        "last_escalated_to": action.get("last_escalated_to"),
                        "last_escalated_by": action.get("last_escalated_by"),
                        # Whether THIS viewer may close the action / delete its evidence
                        # (responsible person, a manager role, or a related owner).
                        "can_manage": self._action_can_manage(
                            action, plan, opp, viewer_email, viewer_role
                        ),
                    })

        items.sort(key=lambda x: (x["responsible_email"] or "zzz", x["due_date"] or "9999"))
        return items

    # Roles that may manage ANY action (close it, delete its evidence) regardless of
    # who the responsible person is.
    _ACTION_MANAGER_ROLES = {"purchasing_manager", "purchasing_director", "vp_conversion"}

    @staticmethod
    def _action_can_manage(action, plan, opp, user_email, actor_role) -> bool:
        """Who may close an action or delete its evidence: a manager role, the action's
        responsible person, or a related owner of the opportunity (purchasing / conversion
        / project / idea owner, or the plan creator). Everyone else is read-only for
        these operations (they can still view and upload)."""
        if actor_role in PurchasingValueService._ACTION_MANAGER_ROLES:
            return True
        email = (user_email or "").strip().lower()
        if not email:
            return False
        plan_data = plan.plan_data or {}
        resp = (
            action.get("email_responsable")
            or plan_data.get("email_responsable")
            or ""
        ).strip().lower()
        if email == resp:
            return True
        related = set()
        if opp is not None:
            for e in (opp.purchasing_owner, opp.conversion_owner,
                      opp.project_owner, opp.idea_owner):
                if e:
                    related.add(e.strip().lower())
        if plan.created_by:
            related.add(plan.created_by.strip().lower())
        return email in related

    async def _assert_action_can_manage(self, plan, action, user_email, actor_role) -> None:
        opp = (
            await self.db.execute(
                select(Opportunity).where(Opportunity.opportunity_id == plan.opportunity_id)
            )
        ).scalar_one_or_none()
        if not self._action_can_manage(action, plan, opp, user_email, actor_role):
            raise AppException(
                403,
                "Only the action's responsible person, a manager, or a related owner "
                "can close it or delete its evidence.",
                "ACTION_NOT_AUTHORIZED",
            )

    async def upload_action_evidence(
        self,
        action_plan_id: int,
        sujet_idx: int,
        action_idx: int,
        file,
        user_email: str,
        opportunity_id: Optional[int] = None,
    ) -> dict:
        """Upload a file as evidence for a specific action and append it to the JSONB attachments."""
        from app.shared.utils.blob_storage import upload_opportunity_document

        plan = await self.get_action_plan(action_plan_id, opportunity_id)
        data = dict(plan.plan_data or {})
        sujets = data.get("sujets", [])

        if sujet_idx >= len(sujets):
            raise AppException(404, "Subject index out of range.", "SUJET_NOT_FOUND")
        actions = sujets[sujet_idx].get("actions", [])
        if action_idx >= len(actions):
            raise AppException(404, "Action index out of range.", "ACTION_NOT_FOUND")

        upload_result = await upload_opportunity_document(
            file=file,
            opportunity_id=plan.opportunity_id,
            phase_label="action-plan",
        )
        attachment = {
            **upload_result,
            "uploaded_by": user_email,
            "uploaded_at": datetime.utcnow().isoformat(),
        }

        if "attachments" not in actions[action_idx]:
            actions[action_idx]["attachments"] = []
        actions[action_idx]["attachments"].append(attachment)
        self._log_action_event(
            actions[action_idx],
            "attachment_added",
            user_email,
            filename=attachment.get("filename"),
        )

        plan.plan_data = data
        flag_modified(plan, "plan_data")
        plan.updated_at = datetime.utcnow()
        plan.updated_by = user_email
        await self.db.flush()
        return attachment

    async def delete_action_evidence(
        self,
        action_plan_id: int,
        sujet_idx: int,
        action_idx: int,
        blob_name: str,
        user_email: str,
        opportunity_id: Optional[int] = None,
        actor_role: Optional[str] = None,
    ) -> None:
        """Delete one evidence attachment from a specific action."""
        plan = await self.get_action_plan(action_plan_id, opportunity_id)
        data = dict(plan.plan_data or {})
        sujets = data.get("sujets", [])

        if sujet_idx >= len(sujets):
            raise AppException(404, "Subject index out of range.", "SUJET_NOT_FOUND")
        actions = sujets[sujet_idx].get("actions", [])
        if action_idx >= len(actions):
            raise AppException(404, "Action index out of range.", "ACTION_NOT_FOUND")

        # Only the responsible / a manager / a related owner may delete evidence.
        await self._assert_action_can_manage(plan, actions[action_idx], user_email, actor_role)

        # Evidence of a CLOSED action is frozen — deletion would break the audit of a
        # completed action. Reopen it first if a file really must be removed.
        if actions[action_idx].get("status") == "closed":
            raise AppException(
                409,
                "This action is closed — its evidence can no longer be deleted. "
                "Reopen the action first if a file must be removed.",
                "ACTION_CLOSED_EVIDENCE_LOCKED",
            )

        attachments = actions[action_idx].get("attachments", [])
        match = next((att for att in attachments if att.get("blob_name") == blob_name), None)
        if not match:
            raise AppException(404, "Attachment not found.", "ATTACHMENT_NOT_FOUND")

        try:
            await delete_blob(blob_name)
        except Exception as exc:
            logger.warning("Blob delete failed for %s: %s", blob_name, exc)

        actions[action_idx]["attachments"] = [
            att for att in attachments if att.get("blob_name") != blob_name
        ]
        self._log_action_event(
            actions[action_idx],
            "attachment_removed",
            user_email,
            filename=match.get("filename"),
        )

        plan.plan_data = data
        flag_modified(plan, "plan_data")
        plan.updated_at = datetime.utcnow()
        plan.updated_by = user_email
        await self.db.flush()

    async def update_action_item_status(
        self,
        action_plan_id: int,
        sujet_idx: int,
        action_idx: int,
        status: str,
        implementation_date: Optional[str],
        user_email: str,
        actor_role: Optional[str] = None,
    ) -> dict:
        """Update the status of a single action inside a plan's JSONB. Sets closed_date when closing."""
        valid_statuses = {"open", "closed", "blocked"}
        if status not in valid_statuses:
            raise AppException(400, f"Invalid status. Must be one of: {', '.join(sorted(valid_statuses))}.", "INVALID_STATUS")

        plan = await self.get_action_plan(action_plan_id)
        data = dict(plan.plan_data or {})
        sujets = data.get("sujets", [])

        if sujet_idx >= len(sujets):
            raise AppException(404, "Subject index out of range.", "SUJET_NOT_FOUND")
        actions = sujets[sujet_idx].get("actions", [])
        if action_idx >= len(actions):
            raise AppException(404, "Action index out of range.", "ACTION_NOT_FOUND")

        # Only the responsible / a manager / a related owner may change an action's status.
        await self._assert_action_can_manage(plan, actions[action_idx], user_email, actor_role)

        previous_status = actions[action_idx].get("status", "open")

        if status == "closed":
            if not implementation_date:
                raise AppException(
                    422,
                    "Implementation date is required to close an action.",
                    "IMPLEMENTATION_DATE_REQUIRED",
                )
            actions[action_idx]["closed_date"] = implementation_date
        else:
            actions[action_idx].pop("closed_date", None)
        actions[action_idx]["status"] = status
        self._log_action_event(
            actions[action_idx],
            "status_changed",
            user_email,
            from_status=previous_status,
            to_status=status,
            closed_date=implementation_date if status == "closed" else None,
        )

        plan.plan_data = data
        flag_modified(plan, "plan_data")
        plan.updated_at = datetime.utcnow()
        plan.updated_by = user_email
        await self.db.flush()
        return actions[action_idx]

    async def send_action_item_reminder(
        self,
        action_plan_id: int,
        sujet_idx: int,
        action_idx: int,
        sent_by: str,
    ) -> dict:
        """Email the responsible person a reminder about one open action item."""
        plan = await self.get_action_plan(action_plan_id)
        data = dict(plan.plan_data or {})
        sujets = data.get("sujets", [])

        if sujet_idx >= len(sujets):
            raise AppException(404, "Subject index out of range.", "SUJET_NOT_FOUND")
        sujet = sujets[sujet_idx]
        actions = sujet.get("actions", [])
        if action_idx >= len(actions):
            raise AppException(404, "Action index out of range.", "ACTION_NOT_FOUND")
        action = actions[action_idx]

        recipient = action.get("email_responsable") or data.get("email_responsable")
        if not recipient:
            raise AppException(
                422,
                "This action has no responsible person's email to remind.",
                "NO_RESPONSIBLE_EMAIL",
            )

        opp_result = await self.db.execute(
            select(Opportunity).where(Opportunity.opportunity_id == plan.opportunity_id)
        )
        opp = opp_result.scalar_one_or_none()
        opp_name = opp.opportunity_name if opp else f"Opportunity #{plan.opportunity_id}"

        due_date = action.get("due_date")
        title = action.get("titre") or "Untitled action"
        responsible_name = action.get("responsable") or data.get("responsable") or ""

        html = f"""
<div style="font-family:Inter,Arial,sans-serif;max-width:520px;margin:0 auto;padding:24px">
  <h2 style="color:#b45309;font-size:18px;margin-bottom:4px">Action Plan Reminder</h2>
  <p style="color:#64748b;font-size:13px;margin-top:0">
    <strong>{sent_by}</strong> is reminding you about an open action item{f" for {responsible_name}" if responsible_name else ""}.
  </p>
  <table style="width:100%;border-collapse:collapse;margin:16px 0">
    <tr>
      <td style="padding:4px 12px 4px 0;color:#64748b;font-size:13px">Opportunity</td>
      <td style="padding:4px 0;font-size:13px;font-weight:600">{opp_name}</td>
    </tr>
    <tr>
      <td style="padding:4px 12px 4px 0;color:#64748b;font-size:13px">Action</td>
      <td style="padding:4px 0;font-size:13px;font-weight:600">{title}</td>
    </tr>
    <tr>
      <td style="padding:4px 12px 4px 0;color:#64748b;font-size:13px">Status</td>
      <td style="padding:4px 0;font-size:13px;font-weight:600">{action.get("status", "open")}</td>
    </tr>
    <tr>
      <td style="padding:4px 12px 4px 0;color:#64748b;font-size:13px">Due date</td>
      <td style="padding:4px 0;font-size:13px;font-weight:600">{due_date or "—"}</td>
    </tr>
  </table>
  <p style="font-size:12px;color:#94a3b8;margin-top:20px">
    Please update this action's status once completed.
  </p>
</div>"""

        await send_email(
            subject=f"[Reminder] Action Plan — {title} ({opp_name})",
            recipients=[recipient],
            body_html=html,
        )

        now = datetime.utcnow().isoformat()
        action["last_reminded_at"] = now
        action["last_reminded_by"] = sent_by
        action["last_reminded_to"] = recipient
        self._log_action_event(action, "reminder_sent", sent_by, to=recipient)

        plan.plan_data = data
        flag_modified(plan, "plan_data")
        plan.updated_at = datetime.utcnow()
        await self.db.flush()
        return {"reminded": recipient}

    async def send_action_item_escalation(
        self,
        action_plan_id: int,
        sujet_idx: int,
        action_idx: int,
        recipient_email: str,
        subject: str,
        message: Optional[str],
        escalated_by: str,
    ) -> dict:
        """Email an arbitrary recipient (e.g. a manager) about an action item."""
        plan = await self.get_action_plan(action_plan_id)
        data = dict(plan.plan_data or {})
        sujets = data.get("sujets", [])

        if sujet_idx >= len(sujets):
            raise AppException(404, "Subject index out of range.", "SUJET_NOT_FOUND")
        sujet = sujets[sujet_idx]
        actions = sujet.get("actions", [])
        if action_idx >= len(actions):
            raise AppException(404, "Action index out of range.", "ACTION_NOT_FOUND")
        action = actions[action_idx]

        opp_result = await self.db.execute(
            select(Opportunity).where(Opportunity.opportunity_id == plan.opportunity_id)
        )
        opp = opp_result.scalar_one_or_none()
        opp_name = opp.opportunity_name if opp else f"Opportunity #{plan.opportunity_id}"

        due_date = action.get("due_date")
        title = action.get("titre") or "Untitled action"
        responsible_name = action.get("responsable") or data.get("responsable") or ""
        responsible_email = action.get("email_responsable") or data.get("email_responsable") or ""

        html = f"""
<div style="font-family:Inter,Arial,sans-serif;max-width:520px;margin:0 auto;padding:24px">
  <h2 style="color:#be123c;font-size:18px;margin-bottom:4px">Action Plan Escalation</h2>
  <p style="color:#64748b;font-size:13px;margin-top:0">
    <strong>{escalated_by}</strong> is escalating an action item to you.
  </p>
  {f'<p style="font-size:13px;color:#334155;white-space:pre-wrap">{message}</p>' if message else ""}
  <table style="width:100%;border-collapse:collapse;margin:16px 0">
    <tr>
      <td style="padding:4px 12px 4px 0;color:#64748b;font-size:13px">Opportunity</td>
      <td style="padding:4px 0;font-size:13px;font-weight:600">{opp_name}</td>
    </tr>
    <tr>
      <td style="padding:4px 12px 4px 0;color:#64748b;font-size:13px">Action</td>
      <td style="padding:4px 0;font-size:13px;font-weight:600">{title}</td>
    </tr>
    <tr>
      <td style="padding:4px 12px 4px 0;color:#64748b;font-size:13px">Responsible</td>
      <td style="padding:4px 0;font-size:13px;font-weight:600">{responsible_name or responsible_email or "—"}</td>
    </tr>
    <tr>
      <td style="padding:4px 12px 4px 0;color:#64748b;font-size:13px">Status</td>
      <td style="padding:4px 0;font-size:13px;font-weight:600">{action.get("status", "open")}</td>
    </tr>
    <tr>
      <td style="padding:4px 12px 4px 0;color:#64748b;font-size:13px">Due date</td>
      <td style="padding:4px 0;font-size:13px;font-weight:600">{due_date or "—"}</td>
    </tr>
  </table>
</div>"""

        await send_email(
            subject=subject,
            recipients=[recipient_email],
            body_html=html,
        )

        now = datetime.utcnow().isoformat()
        action["last_escalated_at"] = now
        action["last_escalated_by"] = escalated_by
        action["last_escalated_to"] = recipient_email
        self._log_action_event(
            action,
            "escalation_sent",
            escalated_by,
            to=recipient_email,
            subject=subject,
        )

        plan.plan_data = data
        flag_modified(plan, "plan_data")
        plan.updated_at = datetime.utcnow()
        await self.db.flush()
        return {"escalated_to": recipient_email}

    async def _recalculate_ytd(self, financial_line_id: int) -> None:
        """Recalculate monthly cumulated fields AND push totals back to FinancialLine.

        Aligned with the Monday SB12 board (calendar-year pilotage — finance &
        purchasing steer per calendar year Jan–Dec):
        - The cumulated columns reset at each January so every calendar year
          accumulates independently (matches Monday's per-year monthly grid).
        - cumulated_real_saving = Σ of the CURRENT calendar year's actuals only
          (not lifetime), matching Monday's "Cum. Real Sav." = Σ of the 12 year
          columns.
        - cumulated_real_saving_ltd = Σ of ALL actuals across every year
          (life-to-date / inception-to-date), for total-value & conversion views.
        - delta_vs_expected_ytd = Σ(actual) − Σ(expected) over the elapsed months
          of the CURRENT calendar year. Expected counts for EVERY elapsed month;
          a month with no actual entered contributes 0 realized, so a late/unentered
          month surfaces as a shortfall instead of silently disappearing.
        Cash cumulation is left all-time (out of scope; cash redesign deferred).
        """
        result = await self.db.execute(
            select(MonthlyFinancial)
            .where(MonthlyFinancial.financial_line_id == financial_line_id)
            .order_by(MonthlyFinancial.period_month)
        )
        rows = list(result.scalars().all())
        today = date.today()
        today_first = today.replace(day=1)
        current_year = today.year

        cum_exp = Decimal("0")
        cum_act = Decimal("0")
        year_cursor = None

        cy_real = Decimal("0")   # current calendar-year realized (YTD basis)
        ltd_real = Decimal("0")  # life-to-date realized (all years, never reset)
        ytd_exp = Decimal("0")
        ytd_act = Decimal("0")

        for row in rows:
            pm = row.period_month
            # Reset the cumulative columns at each calendar-year boundary.
            if pm and pm.year != year_cursor:
                year_cursor = pm.year
                cum_exp = Decimal("0")
                cum_act = Decimal("0")

            cum_exp += row.expected_saving or Decimal("0")
            row.cumulated_expected = cum_exp
            if row.actual_saving is not None:
                cum_act += row.actual_saving
                ltd_real += row.actual_saving
                row.delta_vs_expected = row.actual_saving - (row.expected_saving or Decimal("0"))
            else:
                row.delta_vs_expected = None
            # Always write cumulated_actual so gap rows don't show stale values
            row.cumulated_actual = cum_act if cum_act else None

            # Current calendar-year metrics
            if pm and pm.year == current_year:
                if row.actual_saving is not None:
                    cy_real += row.actual_saving
                # YTD: every elapsed month counts its expected; missing actual = 0
                if pm <= today_first:
                    ytd_exp += row.expected_saving or Decimal("0")
                    ytd_act += row.actual_saving or Decimal("0")

        # Also accumulate cash actuals (Gap 3) — kept all-time (out of scope)
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
            line.cumulated_real_saving = cy_real
            line.cumulated_real_saving_ltd = ltd_real
            line.delta_vs_expected_ytd = ytd_act - ytd_exp

        await self.db.flush()


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _set_if(obj, attr: str, value) -> None:
    """Set attribute only when value is not None."""
    if value is not None:
        setattr(obj, attr, value)


def _build_escalation_email(opp: Opportunity, line: FinancialLine, reason: str) -> str:
    # FinancialLine amounts are stored in the opportunity's native currency —
    # no FX conversion happens before this point — so label with opp.currency
    # instead of assuming EUR.
    cur = opp.currency or "EUR"
    actual = (
        f"{cur}{line.cumulated_real_saving:,.0f}" if line.cumulated_real_saving else f"{cur}0"
    )
    expected = (
        f"{cur}{line.expected_annual_saving:,.0f}" if line.expected_annual_saving else "N/A"
    )
    delta = (
        f"{cur}{line.delta_vs_expected_ytd:,.0f}" if line.delta_vs_expected_ytd else "N/A"
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
    subtitle_color = "#d1fae5" if payload.decision == "Approved" else "#fee2e2"
    icon  = "✅" if payload.decision == "Approved" else "❌"
    preview = pending.get("computed_preview", {})
    def _fmt(v): return f"€{v:,.0f}" if v is not None else "N/A"
    return f"""
    <html><body style="font-family:Arial,sans-serif;color:#111827;max-width:640px;margin:0 auto">
      <div style="background:{color};padding:20px 24px;border-radius:8px 8px 0 0">
        <h2 style="color:#fff;margin:0;font-size:18px">{icon} STP Revision {payload.decision}</h2>
        <p style="color:{subtitle_color};margin:4px 0 0;font-size:13px">{opp.opportunity_name}</p>
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
