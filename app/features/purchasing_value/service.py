"""Purchasing value management service — full business logic."""

from __future__ import annotations

from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.db.models import FinancialLine, MonthlyFinancial, Opportunity, OpportunityDocument, Project, SupplierSiteRelation, SupplierUnit, SupplierGroup
import calendar

from app.features.purchasing_value.schemas import (
    EscalateRequest,
    FinancialLineCompleteRequest,
    GateDecisionRequest,
    MonthlyActualUpdateRequest,
    OpportunityCreateRequest,
    OpportunityUpdateRequest,
    RecoveryUpdateRequest,
    StartStudyRequest,
    STPBenefits,
    STPRisks,
    SubmitForValidationRequest,
    SubmitToCommitteeRequest,
    ValidationRequestPayload,
    add_months,
    compute_priority,
    auto_payback_score,
    auto_leadtime_score,
    DIFFICULTY_LABELS,
)
from app.shared.utils.email.email_service import send_email, send_email_with_attachment
from app.shared.utils.blob_storage import upload_opportunity_document, delete_blob, _extract_blob_name
from app.features.purchasing_value.stp_pdf import generate_stp_pdf

# Phase progression order
PHASE_ORDER = ["Assigned", "Phase 0", "Phase 1", "Phase 2", "Phase 3", "Phase 4", "Closed"]

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
            .where(Opportunity.is_deleted == False)
            .options(
                selectinload(Opportunity.projects),
                selectinload(Opportunity.financial_lines).selectinload(FinancialLine.monthly_financials),
                selectinload(Opportunity.opp_documents),
                selectinload(Opportunity.plant),
            )
            .order_by(Opportunity.opportunity_id.desc())
        )
        return list(result.scalars().all())

    async def get_opportunity(self, opportunity_id: int) -> Opportunity:
        result = await self.db.execute(
            select(Opportunity)
            .where(Opportunity.opportunity_id == opportunity_id, Opportunity.is_deleted == False)
            .options(
                selectinload(Opportunity.projects),
                selectinload(Opportunity.financial_lines).selectinload(FinancialLine.monthly_financials),
                selectinload(Opportunity.opp_documents),
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
            raise AppException(404, "Financial line not found", "FINANCIAL_LINE_NOT_FOUND")
        return line

    async def get_monthly_row(self, month_id: int) -> MonthlyFinancial:
        result = await self.db.execute(
            select(MonthlyFinancial).where(MonthlyFinancial.monthly_financial_id == month_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise AppException(404, "Monthly row not found", "MONTHLY_ROW_NOT_FOUND")
        return row

    # ------------------------------------------------------------------
    # Create opportunity
    # ------------------------------------------------------------------

    async def create_opportunity(self, payload: OpportunityCreateRequest) -> Opportunity:
        if payload.opportunity_type not in ["Negotiation", "Sourcing", "Technical Productivity", "Cash"]:
            raise AppException(422, f"Invalid type. Must be one of: Negotiation, Sourcing, Technical Productivity, Cash", "INVALID_TYPE")

        opp = Opportunity(
            opportunity_name=payload.opportunity_name,
            opportunity_type=payload.opportunity_type,
            idea_owner=payload.idea_owner,
            description=payload.description,
            plant_id=payload.plant_id,
            supplier_id=payload.supplier_id,
            budget_year=payload.budget_year,
            budget_status=payload.budget_status or "Outside Budget",
            status="Assigned",
            phase_status="Phase 0",
            validation_decision=None,
        )
        self.db.add(opp)
        await self.db.flush()
        await self.db.refresh(opp, ["projects", "financial_lines", "opp_documents", "plant"])
        return opp

    # ------------------------------------------------------------------
    # Update Phase 0 fields
    # ------------------------------------------------------------------

    async def update_opportunity(self, opportunity_id: int, payload: OpportunityUpdateRequest) -> Opportunity:
        opp = await self.get_opportunity(opportunity_id)

        if opp.phase_status == "Closed":
            raise AppException(422, "Closed opportunities cannot be edited.", "WRONG_PHASE")

        _set_if(opp, "opportunity_name", payload.opportunity_name)
        _set_if(opp, "description", payload.description)
        _set_if(opp, "expected_annual_saving", payload.expected_annual_saving)
        _set_if(opp, "cash_impact", payload.cash_impact)
        _set_if(opp, "duration_months", payload.duration_months)

        # Track planned_start_date change — rebuild profile if Phase 0/1 and date shifted
        old_planned_start = opp.planned_start_date
        _set_if(opp, "planned_start_date", payload.planned_start_date)
        planned_start_changed = (
            payload.planned_start_date is not None
            and payload.planned_start_date != old_planned_start
            and opp.phase_status in ("Phase 0", "Phase 1", "Assigned")
        )

        # R9 — if real_start_date changes (Phase 3), rebuild monthly profile
        old_real_start = opp.real_start_date
        _set_if(opp, "execution_start_date", payload.execution_start_date)
        _set_if(opp, "real_start_date", payload.real_start_date)
        real_start_changed = (
            payload.real_start_date is not None
            and payload.real_start_date != old_real_start
        )

        # Budget confirmation — validate email domain + track + notify
        old_budget_status = opp.budget_status
        budget_just_confirmed = False
        if payload.budget_status is not None and payload.budget_status != old_budget_status:
            if payload.budget_status == "Budgeted":
                confirmer = payload.changed_by or ""
                if not confirmer.lower().endswith("@avocarbon.com"):
                    raise AppException(
                        422,
                        "Budget confirmation requires an @avocarbon.com email address.",
                        "INVALID_CONFIRMER_EMAIL",
                    )
            _set_if(opp, "budget_status", payload.budget_status)
            opp.budget_confirmed_at = datetime.utcnow()
            opp.budget_confirmed_by = payload.changed_by
            budget_just_confirmed = payload.budget_status == "Budgeted"
        else:
            _set_if(opp, "budget_status", payload.budget_status)

        effective_budget_status = payload.budget_status if payload.budget_status is not None else opp.budget_status
        effective_budget_year = payload.budget_year if payload.budget_year is not None else opp.budget_year
        if effective_budget_status == "Budgeted" and effective_budget_year is None:
            raise AppException(
                422,
                "Enter the Budget Year before saving an opportunity with Budget Status set to Budgeted.",
                "BUDGET_YEAR_REQUIRED",
            )

        _set_if(opp, "budget_year", payload.budget_year)
        _set_if(opp, "change_mode", payload.change_mode)
        _set_if(opp, "assumptions_summary", payload.assumptions_summary)
        _set_if(opp, "comments", payload.comments)
        _set_if(opp, "plant_id", payload.plant_id)
        _set_if(opp, "supplier_id", payload.supplier_id)
        _set_if(opp, "purchasing_owner", payload.purchasing_owner)
        _set_if(opp, "conversion_owner", payload.conversion_owner)
        # D score — manual dropdown (Easy/Relatively easy/Moderately difficult/Difficult/Very Difficult)
        if payload.difficulty_score is not None:
            _set_if(opp, "difficulty_score", payload.difficulty_score)

        # P score — auto-calculated from investment ÷ monthly saving
        auto_p = auto_payback_score(
            float(opp.total_investment or 0) if opp.total_investment else None,
            float(opp.expected_annual_saving) if opp.expected_annual_saving else None,
        )
        if auto_p is not None:
            opp.payback_score = Decimal(str(auto_p))

        # L score — Phase 1+2+3 ONLY per Olivier: "durée phase 1, 2 et 3"
        # Phase 4 LLC happens AFTER production starts → not part of lead time
        total_weeks = sum(filter(None, [
            opp.phase1_weeks, opp.phase2_weeks, opp.phase3_weeks
        ]))
        auto_l = auto_leadtime_score(float(total_weeks) if total_weeks else None)
        if auto_l is not None:
            opp.lead_time_score = Decimal(str(auto_l))

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
        # Auto-compute investment total & ROI (all 4 cost lines)
        costs = [
            float(opp.tooling_cost or 0),
            float(opp.travel_cost or 0),
            float(opp.qualification_cost or 0),
            float(opp.other_cost or 0),
        ]
        total = sum(costs)
        if total > 0:
            opp.total_investment = Decimal(str(total))
            if opp.expected_annual_saving:
                opp.roi_percent = Decimal(str(round((float(opp.expected_annual_saving) / total) * 100, 2)))

        # Auto-compute PLD priority
        p_score, p_cat = compute_priority(opp.payback_score, opp.lead_time_score, opp.difficulty_score)
        if p_score is not None:
            opp.priority_score = Decimal(str(p_score))
            opp.priority_category = p_cat

        opp.updated_at = datetime.utcnow()
        opp.updated_by = payload.changed_by

        # Auto-compute planned_end_date: last day of the final month in the period
        # e.g. start=Oct, duration=1  → 31 Oct
        #      start=Oct, duration=12 → 30 Sep next year
        if opp.planned_start_date and opp.duration_months:
            last_month_start = add_months(opp.planned_start_date, int(opp.duration_months) - 1)
            last_day = calendar.monthrange(last_month_start.year, last_month_start.month)[1]
            computed_end = last_month_start.replace(day=last_day)
            opp.planned_end_date = computed_end
            # Sync to linked project if not yet set
            for proj in opp.projects:
                if proj.planned_end_date is None:
                    proj.planned_end_date = computed_end
                    proj.updated_at = datetime.utcnow()

        # Phase 0/1 — rebuild monthly profile if planned_start_date changed (no actuals yet)
        if planned_start_changed and opp.financial_lines:
            duration = int(opp.duration_months or 12)
            for line in opp.financial_lines:
                if line.status == "Active":
                    line.planned_start_date = payload.planned_start_date
                    await self._rebuild_monthly_profile(
                        line, opp.expected_annual_saving or Decimal("0"),
                        payload.planned_start_date, duration
                    )
                    await self._recalculate_ytd(line.financial_line_id)

        # R9 — rebuild monthly profile if real_start_date shifted (Phase 3)
        if real_start_changed and opp.financial_lines:
            new_start = payload.real_start_date
            duration = int(opp.duration_months or 12)
            for line in opp.financial_lines:
                if line.status == "Active":
                    await self._rebuild_monthly_profile(line, opp.expected_annual_saving or Decimal("0"), new_start, duration)
                    await self._recalculate_ytd(line.financial_line_id)

        await self.db.flush()
        await self.db.refresh(opp, ["projects", "financial_lines", "opp_documents", "plant"])

        # Send budget confirmation email
        if budget_just_confirmed:
            recipients = list(filter(None, [
                opp.budget_confirmed_by,
                opp.purchasing_owner,
            ]))
            # deduplicate while preserving order
            seen: set = set()
            recipients = [r for r in recipients if not (r in seen or seen.add(r))]
            if recipients:
                try:
                    await send_email(
                        subject=f"[Budget Confirmed] {opp.opportunity_name}",
                        recipients=recipients,
                        body_html=_build_budget_confirmed_email(opp),
                    )
                except Exception:
                    pass

        return opp

    # ------------------------------------------------------------------
    # Gate decision — core workflow engine
    # ------------------------------------------------------------------

    async def apply_gate_decision(self, opportunity_id: int, payload: GateDecisionRequest) -> Opportunity:
        if payload.decision not in ("Go", "No Go", "Review"):
            raise AppException(422, "Decision must be Go, No Go, or Review.", "INVALID_DECISION")

        opp = await self.get_opportunity(opportunity_id)
        now = datetime.utcnow()

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

        # ------ REVIEW → needs rework, buyer/PM must resubmit ------
        elif payload.decision == "Review":
            opp.status = "Needs Rework"
            if payload.comments:
                opp.comments = (opp.comments or "") + f"\n[Review — {datetime.utcnow().strftime('%Y-%m-%d')} by {payload.decided_by or 'reviewer'}] {payload.comments}"

        # ------ GO → advance phase ------
        else:
            current_phase = opp.phase_status or "Phase 0"

            if current_phase in ("Assigned", "Phase 0"):
                # Phase 0 Go: validate the opportunity
                opp.phase_status = "Phase 1"
                opp.status = "Working on it"
                opp.val_date = now.date()
                if payload.comments:
                    opp.comments = (opp.comments or "") + f"\n[Phase 0 Go] {payload.comments}"

                # R1 — auto-create FinancialLine only if none exists yet
                # (guard: Review → rework → Go cycle must not create duplicates)
                if not opp.financial_lines:
                    await self._create_financial_line(opp)

                # R2 — create Project for Sourcing / Technical Productivity
                if opp.opportunity_type not in NO_PROJECT_TYPES:
                    if not payload.project_manager:
                        raise AppException(422, "project_manager email is required for this opportunity type.", "PM_REQUIRED")
                    opp.project_owner = payload.project_manager
                    if not opp.projects:
                        await self._create_project(opp, payload.project_manager)

            elif current_phase == "Phase 1":
                opp.phase_status = "Phase 2"
                opp.status = "Working on it"
                if payload.comments:
                    opp.comments = (opp.comments or "") + f"\n[Phase 1 Go] {payload.comments}"

            elif current_phase == "Phase 2":
                opp.phase_status = "Phase 3"
                opp.status = "Working on it"

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

        await self.db.flush()
        await self.db.refresh(opp, ["projects", "financial_lines", "opp_documents", "plant"])
        return opp

    # ------------------------------------------------------------------
    # Start Phase 0 study (Assigned → Working on it)
    # ------------------------------------------------------------------

    async def start_study(self, opportunity_id: int, payload: StartStudyRequest) -> Opportunity:
        opp = await self.get_opportunity(opportunity_id)
        if opp.status != "Assigned":
            raise AppException(422, "Only Assigned opportunities can be started.", "WRONG_STATUS")
        opp.status = "Working on it"
        opp.phase_status = "Phase 0"
        opp.study_start_date = datetime.utcnow().date()  # Olivier: "ça me valide la date de l'opportunité"
        opp.updated_at = datetime.utcnow()
        opp.updated_by = payload.started_by
        opp.comments = (opp.comments or "") + f"\n[Phase 0 started by {payload.started_by or 'system'} on {datetime.utcnow().strftime('%Y-%m-%d')}]"
        await self.db.flush()
        await self.db.refresh(opp, ["projects", "financial_lines", "opp_documents", "plant"])
        return opp

    # ------------------------------------------------------------------
    # Submit for PM validation (Phase 0 → Awaiting Validation)
    # ------------------------------------------------------------------

    async def submit_for_validation(self, opportunity_id: int, payload: SubmitForValidationRequest) -> Opportunity:
        opp = await self.get_opportunity(opportunity_id)
        if opp.status not in ("Working on it", "Needs Rework"):
            raise AppException(422, "Opportunity must be 'Working on it' to submit for validation.", "WRONG_STATUS")
        if opp.phase_status != "Phase 0":
            raise AppException(422, "Only Phase 0 opportunities can be submitted for PM validation.", "WRONG_PHASE")

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
                import tempfile, os
                body = _build_phase0_submit_email(opp, payload.message, payload.committee_type if hasattr(payload, "committee_type") else None)
                pdf_bytes = generate_stp_pdf(opp, phase=0)
                safe = (opp.opportunity_name or "STP").replace(" ", "_")[:50]
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", prefix=f"STP_Phase0_{safe}_") as tmp:
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
            except Exception:
                pass

        await self.db.flush()
        await self.db.refresh(opp, ["projects", "financial_lines", "opp_documents", "plant"])
        return opp

    # ------------------------------------------------------------------
    # Submit to Sourcing Committee (Phase 1 → Under Committee Review)
    # ------------------------------------------------------------------

    async def submit_to_committee(self, opportunity_id: int, payload: SubmitToCommitteeRequest) -> Opportunity:
        opp = await self.get_opportunity(opportunity_id)
        if opp.phase_status != "Phase 1":
            raise AppException(422, "Only Phase 1 opportunities can be submitted to committee.", "WRONG_PHASE")
        if opp.status not in ("Working on it", "Needs Rework"):
            raise AppException(422, "Opportunity must be 'Working on it' to submit to committee.", "WRONG_STATUS")

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
                import tempfile, os
                body = _build_committee_email(opp, payload.message, committee)
                pdf_bytes = generate_stp_pdf(opp, phase=1)
                safe = (opp.opportunity_name or "STP").replace(" ", "_")[:50]
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", prefix=f"STP_Phase1_{safe}_") as tmp:
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
            except Exception:
                pass

        await self.db.flush()
        await self.db.refresh(opp, ["projects", "financial_lines", "opp_documents", "plant"])
        return opp

    # ------------------------------------------------------------------
    # Send validation-request email (Phase 0 → before gate)
    # ------------------------------------------------------------------

    async def send_validation_request(self, opportunity_id: int, payload: ValidationRequestPayload) -> Opportunity:
        opp = await self.get_opportunity(opportunity_id)

        body_html = _build_validation_email(opp, payload.custom_message)
        try:
            await send_email(
                subject=f"[Validation Request] {opp.opportunity_name}",
                recipients=payload.to_emails,
                body_html=body_html,
                cc=payload.extra_cc_emails or [],
            )
        except Exception:
            pass  # don't block the flow — email is best-effort

        opp.validation_request_sent_at = datetime.utcnow()
        opp.validation_request_sent_by = payload.sent_by
        opp.updated_at = datetime.utcnow()
        opp.updated_by = payload.sent_by

        await self.db.flush()
        await self.db.refresh(opp, ["projects", "financial_lines", "opp_documents", "plant"])
        return opp

    # ------------------------------------------------------------------
    # Update monthly actual + EOY forecast  (R4, R11)
    # ------------------------------------------------------------------

    async def update_monthly_actual(self, month_id: int, payload: MonthlyActualUpdateRequest) -> MonthlyFinancial:
        row = await self.get_monthly_row(month_id)
        line = await self.get_financial_line(row.financial_line_id)
        opp = await self.get_opportunity(line.opportunity_id)

        if opp.phase_status != "Phase 3":
            raise AppException(
                422,
                "Monthly financial rows can only be edited while the opportunity is in Phase 3.",
                "MONTHLY_ROWS_LOCKED_OUTSIDE_PHASE_3",
            )

        _set_if(row, "actual_saving", payload.actual_saving)
        _set_if(row, "cash_actual", payload.cash_actual)

        # EOY Forecast validation: must be ≥ cumulated actual
        # Olivier (04/06/2026): "si tu as mis Actual 200, elle peut pas avoir une end of
        # qui soit moins de 200 puisqu'elle a déjà 200"
        if payload.forecast_eoy_saving is not None:
            # Get current cumulated actual (after setting new actual above)
            cum_actual = float(row.cumulated_actual) if row.cumulated_actual else float(row.actual_saving or 0)
            new_forecast = float(payload.forecast_eoy_saving)
            if new_forecast < cum_actual:
                raise AppException(
                    422,
                    f"EOY Forecast ({new_forecast:.0f}€) cannot be less than cumulated actual ({cum_actual:.0f}€). "
                    f"You have already realized {cum_actual:.0f}€ — the full-year projection must be at least that amount.",
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
            recipients = list(filter(None, [opp.purchasing_owner, opp.conversion_owner]))
            if recipients:
                try:
                    await send_email(
                        subject=f"[ESCALATION] Monthly review — {opp.opportunity_name}",
                        recipients=recipients,
                        body_html=_build_escalation_email(opp, line, line.escalation_reason),
                    )
                except Exception:
                    pass

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
        recipients = list(filter(None, [
            opp.purchasing_owner,
            opp.conversion_owner,
        ] + (payload.extra_recipients or [])))

        if recipients:
            try:
                await send_email(
                    subject=f"[ESCALATION] Opportunity: {opp.opportunity_name}",
                    recipients=recipients,
                    body_html=_build_escalation_email(opp, line, payload.escalation_reason),
                )
            except Exception:
                pass

        await self.db.flush()
        await self.db.refresh(line, ["monthly_financials"])
        return line

    async def deescalate_financial_line(self, line_id: int, updated_by: Optional[str]) -> FinancialLine:
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

    async def set_recovery(self, line_id: int, payload: RecoveryUpdateRequest) -> FinancialLine:
        line = await self.get_financial_line(line_id)
        now = datetime.utcnow()

        # Snapshot previous state into history before overwriting
        if line.recovery_status:
            amount_str = f"€{float(line.recovery_amount):,.0f}" if line.recovery_amount else "—"
            target_str = str(line.recovery_target_date) if line.recovery_target_date else "—"
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
        self, line: FinancialLine, updated_row: MonthlyFinancial, updated_by: Optional[str]
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
            select(MonthlyFinancial)
            .where(
                MonthlyFinancial.financial_line_id == line.financial_line_id,
                MonthlyFinancial.period_month >= savings_start.replace(day=1),
                MonthlyFinancial.period_month < today.replace(day=1),
                MonthlyFinancial.actual_saving == None,
            )
        )
        missing_rows = result.scalars().all()
        if not missing_rows:
            return

        opp = await self.get_opportunity(line.opportunity_id)
        recipients = list(filter(None, [opp.purchasing_owner, opp.conversion_owner]))
        if not recipients:
            return

        months_missing = [r.period_month.strftime("%b %Y") if r.period_month else "?" for r in missing_rows]
        try:
            await send_email(
                subject=f"[Alert] Missing savings data — {opp.opportunity_name}",
                recipients=recipients,
                body_html=_build_delay_alert_email(opp, line, months_missing),
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _create_financial_line(self, opp: Opportunity) -> FinancialLine:
        line_name = f"{opp.opportunity_name}"
        duration = int(opp.duration_months or 12)
        start = opp.planned_start_date or date.today().replace(day=1)
        annual = opp.expected_annual_saving or Decimal("0")
        # Cash monthly expected (for Negotiation/Cash type)
        cash_annual = opp.cash_impact if opp.opportunity_type in ("Negotiation", "Cash") else None

        line = FinancialLine(
            opportunity_id=opp.opportunity_id,
            plant_id=opp.plant_id,
            line_name=line_name,
            component_name="Default",  # user can add more lines per component
            budget_status=opp.budget_status or "Outside Budget",
            expected_annual_saving=annual,
            budget_value=annual,
            planned_start_date=start,
            duration_months=Decimal(str(duration)),
            status="Active",
            follower=opp.conversion_owner or opp.purchasing_owner,
        )
        self.db.add(line)
        await self.db.flush()

        await self._generate_monthly_profile(line, annual, start, duration, cash_annual=cash_annual)
        return line

    async def create_component_line(self, opportunity_id: int, payload) -> FinancialLine:
        """Gap 2 — add a component-specific FinancialLine to an existing opportunity."""
        opp = await self.get_opportunity(opportunity_id)
        if opp.validation_decision != "Go":
            raise AppException(422, "Can only add component lines after Phase 0 Go.", "NOT_VALIDATED")

        start = payload.planned_start_date or opp.planned_start_date or date.today().replace(day=1)
        duration = payload.duration_months or int(opp.duration_months or 12)

        line = FinancialLine(
            opportunity_id=opportunity_id,
            plant_id=opp.plant_id,
            line_name=f"{payload.component_name} ({payload.component_pn or 'no PN'})",
            component_name=payload.component_name,
            component_pn=payload.component_pn,
            budget_status=opp.budget_status or "Outside Budget",
            expected_annual_saving=payload.expected_annual_saving,
            budget_value=payload.expected_annual_saving,
            planned_start_date=start,
            duration_months=Decimal(str(duration)),
            status="Active",
            follower=opp.conversion_owner or opp.purchasing_owner,
        )
        self.db.add(line)
        await self.db.flush()

        await self._generate_monthly_profile(line, payload.expected_annual_saving, start, duration)
        line.updated_by = payload.added_by
        await self.db.flush()
        await self.db.refresh(line, ["monthly_financials"])
        return line

    async def _rebuild_monthly_profile(
        self,
        line: FinancialLine,
        annual_saving: Decimal,
        new_start: date,
        duration_months: int,
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
                MonthlyFinancial.actual_saving == None,  # only delete rows with no actual
            )
        )
        empty_rows = result.scalars().all()
        for row in empty_rows:
            await self.db.delete(row)
        await self.db.flush()

        # Find the latest month that already has actuals
        result2 = await self.db.execute(
            select(MonthlyFinancial).where(
                MonthlyFinancial.financial_line_id == line.financial_line_id,
                MonthlyFinancial.actual_saving != None,
                MonthlyFinancial.period_month >= new_start,
            ).order_by(MonthlyFinancial.period_month.desc())
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
            new_rows = []
            for i in range(months_remaining):
                period = add_months(rebuild_start, i)
                new_rows.append(MonthlyFinancial(
                    financial_line_id=line.financial_line_id,
                    period_month=period,
                    expected_saving=self._monthly_expected(annual_saving, months_remaining),
                ))
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
        if not has_escalated_rows and line.escalation_reason and line.escalation_reason.startswith("Auto-escalated from monthly review"):
            line.is_escalated = False
            line.escalated_at = None
            line.escalated_by = None
            line.escalation_reason = None

        await self.db.flush()

    def _monthly_expected(self, annual: Decimal, duration_months: int) -> Decimal:
        """
        Monthly expected = annual / 12  (annual saving is a per-year rate).
        For sub-annual projects (duration < 12) divide by duration so the full
        amount lands in the available months (one-shot rebate / short negotiation).
        """
        if duration_months <= 0:
            return Decimal("0")
        divisor = min(duration_months, 12)
        return round(annual / Decimal(str(divisor)), 2)

    async def _generate_monthly_profile(
        self,
        line: FinancialLine,
        annual_saving: Decimal,
        start_date: date,
        duration_months: int,
        cash_annual: Optional[Decimal] = None,
    ) -> None:
        """Create one MonthlyFinancial row per month."""
        monthly = self._monthly_expected(annual_saving, duration_months)
        cash_monthly = self._monthly_expected(cash_annual, duration_months) if cash_annual else None
        rows: List[MonthlyFinancial] = []
        for i in range(duration_months):
            period = add_months(start_date, i)
            rows.append(
                MonthlyFinancial(
                    financial_line_id=line.financial_line_id,
                    period_month=period,
                    expected_saving=monthly,
                    cash_expected=cash_monthly,
                )
            )
        self.db.add_all(rows)
        await self.db.flush()

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
    # Document management
    # ------------------------------------------------------------------

    async def revise_financial_line_baseline(
        self, line_id: int, revised_saving: Decimal, note: Optional[str], revised_by: Optional[str]
    ) -> FinancialLine:
        """Phase 1 or Phase 3 — revise expected_annual_saving, rebuild monthly profile, keep budget_value."""
        line = await self.get_financial_line(line_id)
        if line.status != "Active":
            raise AppException(422, "Can only revise an active financial line.", "LINE_NOT_ACTIVE")

        old_saving = line.expected_annual_saving or Decimal("0")
        line.expected_annual_saving = revised_saving
        # budget_value stays unchanged — it is the original budget commitment
        line.comments = (line.comments or "") + (
            f"\n[Baseline revised {datetime.utcnow().strftime('%Y-%m-%d')} by {revised_by or 'system'}] "
            f"€{old_saving:,.0f} → €{revised_saving:,.0f}. Reason: {note or 'N/A'}"
        )
        line.updated_at = datetime.utcnow()
        line.updated_by = revised_by

        # Rebuild monthly profile with days-based pro-ration
        duration = int(line.duration_months or 12)
        start = line.real_start_date or line.planned_start_date or date.today().replace(day=1)
        await self._rebuild_monthly_profile(line, revised_saving, start, duration)
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

        for field in ("project_owner", "status", "plant_validation", "planned_end_date",
                      "actual_end_date", "comments", "phase_output_notes",
                      "off_tool_date", "committee_review_date", "committee_members"):
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
                except Exception:
                    pass
        await self.db.delete(doc)
        await self.db.flush()

    # ------------------------------------------------------------------
    # Suppliers by plant
    # ------------------------------------------------------------------

    async def get_suppliers_by_plant(self, plant_id: int) -> list:
        result = await self.db.execute(
            select(SupplierUnit)
            .join(SupplierSiteRelation, SupplierSiteRelation.id_supplier_unit == SupplierUnit.id_supplier_unit)
            .where(
                SupplierSiteRelation.id_site == plant_id,
                SupplierUnit.is_deleted == False,
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

        delta_vs_expected_ytd = sum(actual or 0) - sum(expected)
                                for all months from project start up to today.
        Missing actuals (null) count as 0 so gaps are never hidden.
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
                row.cumulated_actual = cum_act
                row.delta_vs_expected = row.actual_saving - (row.expected_saving or Decimal("0"))

            # YTD delta: all past months, null actual counts as 0 (gap stays visible)
            if row.period_month and row.period_month <= today_first:
                ytd_exp += row.expected_saving or Decimal("0")
                ytd_act += row.actual_saving if row.actual_saving is not None else Decimal("0")

        # Also accumulate cash actuals (Gap 3)
        cum_cash = Decimal("0")
        for row in rows:
            if row.cash_actual is not None:
                cum_cash += row.cash_actual
                row.cumulated_cash_actual = cum_cash

        # Push totals back to the FinancialLine header
        line_result = await self.db.execute(
            select(FinancialLine).where(FinancialLine.financial_line_id == financial_line_id)
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
    saving = f"€{opp.expected_annual_saving:,.0f}" if opp.expected_annual_saving else "N/A"
    plant = opp.plant.site_name if opp.plant else "N/A"
    budget_year = str(int(opp.budget_year)) if opp.budget_year else "N/A"
    confirmer = opp.budget_confirmed_by or "N/A"
    confirmed_at = opp.budget_confirmed_at.strftime("%d %b %Y %H:%M") if opp.budget_confirmed_at else "N/A"
    end_date = opp.planned_end_date.strftime("%d %b %Y") if opp.planned_end_date else "N/A"
    start_date = opp.planned_start_date.strftime("%d %b %Y") if opp.planned_start_date else "N/A"
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
    actual = f"€{line.cumulated_real_saving:,.0f}" if line.cumulated_real_saving else "€0"
    expected = f"€{line.expected_annual_saving:,.0f}" if line.expected_annual_saving else "N/A"
    delta = f"€{line.delta_vs_expected_ytd:,.0f}" if line.delta_vs_expected_ytd else "N/A"
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


def _build_delay_alert_email(opp: Opportunity, line: FinancialLine, months_missing: list) -> str:
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


def _build_phase0_submit_email(opp: Opportunity, message: Optional[str], committee_type=None) -> str:
    saving = f"€{opp.expected_annual_saving:,.0f}" if opp.expected_annual_saving else "N/A"
    cash = f"€{opp.cash_impact:,.0f}" if opp.cash_impact else "N/A"
    pld = f"{opp.priority_score} ({opp.priority_category})" if opp.priority_score else "N/A"
    extra = f"<p style='color:#374151;font-style:italic'>{message}</p>" if message else ""
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
          <tr><td style="background:#f5f8fc;font-weight:600;padding:8px 12px">Change Mode</td><td style="padding:8px 12px;border-bottom:1px solid #eef1f6">{opp.change_mode or 'To be confirmed'}</td></tr>
          <tr><td style="background:#f5f8fc;font-weight:600;padding:8px 12px">Plant</td><td style="padding:8px 12px">{opp.plant.site_name if opp.plant else 'N/A'}</td></tr>
        </table>
        {f'<div style="background:#f5f8fc;padding:12px;border-radius:6px;font-size:12px"><strong>Assumptions:</strong> {opp.assumptions_summary}</div>' if opp.assumptions_summary else ''}
        <p style="color:#6b7280;font-size:11px;margin-top:24px">Please apply your decision (Go / No Go / Review) in the Purchasing Value Management system.<br>Avocarbon · Purchasing</p>
      </div>
    </body></html>"""


def _build_committee_email(opp: Opportunity, message: Optional[str], committee_type: str) -> str:
    saving = f"€{opp.expected_annual_saving:,.0f}" if opp.expected_annual_saving else "N/A"
    pld = f"{opp.priority_score} ({opp.priority_category})" if opp.priority_score else "N/A"
    extra = f"<p style='color:#374151;font-style:italic'>{message}</p>" if message else ""
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
          <tr><td style="background:#eff6ff;font-weight:600;padding:8px 12px">Change Mode</td><td style="padding:8px 12px">{opp.change_mode or 'To be confirmed'}</td></tr>
        </table>
        {f'<div style="background:#eff6ff;padding:12px;border-radius:6px;font-size:12px"><strong>Assumptions:</strong> {opp.assumptions_summary}</div>' if opp.assumptions_summary else ''}
        <p style="color:#6b7280;font-size:11px;margin-top:24px">Please record your decision (Go / No Go / Review) in the Purchasing Value Management system.<br>Avocarbon · Purchasing</p>
      </div>
    </body></html>"""


def _build_validation_email(opp: Opportunity, custom_message: Optional[str]) -> str:
    saving = f"€{opp.expected_annual_saving:,.0f}" if opp.expected_annual_saving else "N/A"
    cash = f"€{opp.cash_impact:,.0f}" if opp.cash_impact else "N/A"
    duration = f"{opp.duration_months} months" if opp.duration_months else "N/A"
    pld = "N/A"
    if opp.priority_score:
        pld = f"{opp.priority_score} ({opp.priority_category})"
    extra = f"<p style='color:#374151'><em>{custom_message}</em></p>" if custom_message else ""

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
          <tr><td style="background:#f5f8fc;font-weight:600;padding:8px 12px">Change Mode</td><td style="padding:8px 12px">{opp.change_mode or 'TBD'}</td></tr>
        </table>
        {f'<p style="background:#f5f8fc;padding:12px;border-radius:6px;font-size:12px"><strong>Assumptions:</strong> {opp.assumptions_summary}</p>' if opp.assumptions_summary else ''}
        <p style="color:#6b7280;font-size:11px;margin-top:24px">Avocarbon · Purchasing Value Management</p>
      </div>
    </body></html>
    """
