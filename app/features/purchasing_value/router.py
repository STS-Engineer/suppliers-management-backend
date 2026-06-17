"""Purchasing value management router."""

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from datetime import date

from app.db.models import FinancialLine, Opportunity

from app.core.exceptions import AppException
from app.features.purchasing_value import schemas
from app.features.purchasing_value.schemas import opportunity_to_response
from app.features.purchasing_value.service import PurchasingValueService
from app.features.purchasing_value.kpi_service import PurchasingKpiService
from app.features.purchasing_value.stp_pdf import generate_stp_pdf
from app.shared.dependencies.auth import get_current_user
from app.shared.dependencies.db import get_db

router = APIRouter(prefix="/purchasing-value", tags=["purchasing-value"])


@router.get("/kpis", response_model=dict)
async def get_kpis(
    year: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    svc = PurchasingKpiService(db)
    return {"status": "success", "data": await svc.compute_all(year)}


# ---------------------------------------------------------------------------
# Opportunities
# ---------------------------------------------------------------------------

@router.get("/opportunities", response_model=dict)
async def list_opportunities(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    svc = PurchasingValueService(db)
    items = await svc.list_opportunities()
    return {
        "status": "success",
        "data": schemas.OpportunityListResponse(
            items=[opportunity_to_response(o) for o in items],
            total=len(items),
        ),
    }


@router.post("/opportunities", response_model=dict)
async def create_opportunity(
    payload: schemas.OpportunityCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        svc = PurchasingValueService(db)
        opp = await svc.create_opportunity(payload)
        await db.commit()
        # Re-fetch after commit — avoids stale session cache (R9 monthly rebuilds, etc.)
        fresh_opp = await svc.get_opportunity(opp.opportunity_id)
        return {"status": "success", "data": opportunity_to_response(fresh_opp)}
    except AppException:
        await db.rollback(); raise
    except Exception:
        await db.rollback(); raise


@router.get("/opportunities/{opportunity_id}", response_model=dict)
async def get_opportunity(
    opportunity_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    svc = PurchasingValueService(db)
    opp = await svc.get_opportunity(opportunity_id)
    return {"status": "success", "data": opportunity_to_response(opp)}


@router.put("/opportunities/{opportunity_id}", response_model=dict)
async def update_opportunity(
    opportunity_id: int,
    payload: schemas.OpportunityUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        svc = PurchasingValueService(db)
        opp = await svc.update_opportunity(opportunity_id, payload)
        await db.commit()
        # Re-fetch after commit — avoids stale session cache (R9 monthly rebuilds, etc.)
        fresh_opp = await svc.get_opportunity(opportunity_id)
        return {"status": "success", "data": opportunity_to_response(fresh_opp)}
    except AppException:
        await db.rollback(); raise
    except Exception:
        await db.rollback(); raise


@router.post("/opportunities/{opportunity_id}/start-study", response_model=dict)
async def start_study(
    opportunity_id: int,
    payload: schemas.StartStudyRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        svc = PurchasingValueService(db)
        opp = await svc.start_study(opportunity_id, payload)
        await db.commit()
        # Re-fetch after commit — avoids stale session cache (R9 monthly rebuilds, etc.)
        fresh_opp = await svc.get_opportunity(opportunity_id)
        return {"status": "success", "data": opportunity_to_response(fresh_opp)}
    except AppException:
        await db.rollback(); raise
    except Exception:
        await db.rollback(); raise


@router.post("/opportunities/{opportunity_id}/submit-for-validation", response_model=dict)
async def submit_for_validation(
    opportunity_id: int,
    payload: schemas.SubmitForValidationRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        svc = PurchasingValueService(db)
        opp = await svc.submit_for_validation(opportunity_id, payload)
        await db.commit()
        fresh_opp = await svc.get_opportunity(opportunity_id)
        return {"status": "success", "data": opportunity_to_response(fresh_opp), "message": "Submitted for PM validation"}
    except AppException:
        await db.rollback(); raise
    except Exception:
        await db.rollback(); raise


@router.post("/opportunities/{opportunity_id}/submit-to-committee", response_model=dict)
async def submit_to_committee(
    opportunity_id: int,
    payload: schemas.SubmitToCommitteeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        svc = PurchasingValueService(db)
        opp = await svc.submit_to_committee(opportunity_id, payload)
        await db.commit()
        fresh_opp = await svc.get_opportunity(opportunity_id)
        return {"status": "success", "data": opportunity_to_response(fresh_opp), "message": "Submitted to committee"}
    except AppException:
        await db.rollback(); raise
    except Exception:
        await db.rollback(); raise


@router.post("/opportunities/{opportunity_id}/gate-decision", response_model=dict)
async def apply_gate_decision(
    opportunity_id: int,
    payload: schemas.GateDecisionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        svc = PurchasingValueService(db)
        opp = await svc.apply_gate_decision(opportunity_id, payload)
        await db.commit()
        # Re-fetch after commit — avoids stale session cache (R9 monthly rebuilds, etc.)
        fresh_opp = await svc.get_opportunity(opportunity_id)
        return {"status": "success", "data": opportunity_to_response(fresh_opp)}
    except AppException:
        await db.rollback(); raise
    except Exception:
        await db.rollback(); raise


@router.post("/opportunities/{opportunity_id}/send-validation-request", response_model=dict)
async def send_validation_request(
    opportunity_id: int,
    payload: schemas.ValidationRequestPayload,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        svc = PurchasingValueService(db)
        opp = await svc.send_validation_request(opportunity_id, payload)
        await db.commit()
        fresh_opp = await svc.get_opportunity(opportunity_id)
        return {"status": "success", "data": opportunity_to_response(fresh_opp), "message": "Validation request sent"}
    except AppException:
        await db.rollback(); raise
    except Exception:
        await db.rollback(); raise


# ---------------------------------------------------------------------------
# Monthly financial updates
# ---------------------------------------------------------------------------

@router.put("/projects/{project_id}", response_model=dict)
async def update_project(
    project_id: int,
    payload: schemas.ProjectUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        svc = PurchasingValueService(db)
        proj = await svc.update_project(project_id, payload)
        await db.commit()
        return {"status": "success", "data": schemas.ProjectResponse.model_validate(proj)}
    except AppException:
        await db.rollback(); raise
    except Exception:
        await db.rollback(); raise


@router.post("/financial-lines/{line_id}/escalate", response_model=dict)
async def escalate_financial_line(
    line_id: int,
    payload: schemas.EscalateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        svc = PurchasingValueService(db)
        line = await svc.escalate_financial_line(line_id, payload)
        await db.commit()
        return {"status": "success", "data": schemas.FinancialLineResponse.model_validate(line)}
    except AppException:
        await db.rollback(); raise
    except Exception:
        await db.rollback(); raise


@router.post("/financial-lines/{line_id}/deescalate", response_model=dict)
async def deescalate_financial_line(
    line_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        svc = PurchasingValueService(db)
        line = await svc.deescalate_financial_line(line_id, None)
        await db.commit()
        return {"status": "success", "data": schemas.FinancialLineResponse.model_validate(line)}
    except AppException:
        await db.rollback(); raise
    except Exception:
        await db.rollback(); raise


@router.put("/financial-lines/{line_id}/recovery", response_model=dict)
async def set_recovery(
    line_id: int,
    payload: schemas.RecoveryUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        svc = PurchasingValueService(db)
        line = await svc.set_recovery(line_id, payload)
        await db.commit()
        return {"status": "success", "data": schemas.FinancialLineResponse.model_validate(line)}
    except AppException:
        await db.rollback(); raise
    except Exception:
        await db.rollback(); raise


@router.post("/opportunities/{opportunity_id}/financial-lines", response_model=dict)
async def create_component_line(
    opportunity_id: int,
    payload: schemas.AddComponentLineRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        svc = PurchasingValueService(db)
        line = await svc.create_component_line(opportunity_id, payload)
        await db.commit()
        return {"status": "success", "data": schemas.FinancialLineResponse.model_validate(line)}
    except AppException:
        await db.rollback(); raise
    except Exception:
        await db.rollback(); raise


@router.post("/financial-lines/{line_id}/rebuild-profile", response_model=dict)
async def rebuild_monthly_profile(
    line_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Rebuild monthly expected rows using equal monthly distribution (annual ÷ duration,
    escalating per STP year-window where applicable). Use this to regenerate the monthly
    profile for an existing line after its baseline or start date changed."""
    try:
        from app.db.models import FinancialLine as FL
        from sqlalchemy import select
        result = await db.execute(select(FL).where(FL.financial_line_id == line_id))
        line = result.scalar_one_or_none()
        if not line:
            raise AppException(404, "Financial line not found", "NOT_FOUND")
        svc = PurchasingValueService(db)
        start = line.real_start_date or line.planned_start_date
        if not start or not line.expected_annual_saving:
            raise AppException(422, "Line needs planned_start_date and expected_annual_saving.", "MISSING_DATA")
        duration = int(line.duration_months or 12)
        await svc._rebuild_monthly_profile(line, line.expected_annual_saving, start, duration)
        await svc._recalculate_ytd(line_id)
        await db.commit()
        return {"status": "success", "message": f"Rebuilt {duration} monthly rows using equal monthly distribution."}
    except AppException:
        await db.rollback(); raise
    except Exception:
        await db.rollback(); raise


@router.post("/financial-lines/{line_id}/revise-baseline", response_model=dict)
async def revise_financial_line_baseline(
    line_id: int,
    payload: schemas.FinancialLineReviseBaselineRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        svc = PurchasingValueService(db)
        line = await svc.revise_financial_line_baseline(line_id, payload.revised_saving, payload.note, payload.revised_by)
        await db.commit()
        return {"status": "success", "data": schemas.FinancialLineResponse.model_validate(line)}
    except AppException:
        await db.rollback(); raise
    except Exception:
        await db.rollback(); raise


@router.post("/financial-lines/{line_id}/complete", response_model=dict)
async def complete_financial_line(
    line_id: int,
    payload: schemas.FinancialLineCompleteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        svc = PurchasingValueService(db)
        line = await svc.complete_financial_line(line_id, payload)
        await db.commit()
        return {"status": "success", "data": schemas.FinancialLineResponse.model_validate(line)}
    except AppException:
        await db.rollback(); raise
    except Exception:
        await db.rollback(); raise


@router.put("/monthly/{month_id}", response_model=dict)
async def update_monthly_actual(
    month_id: int,
    payload: schemas.MonthlyActualUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        svc = PurchasingValueService(db)
        row = await svc.update_monthly_actual(month_id, payload)
        await db.commit()
        return {"status": "success", "data": schemas.MonthlyFinancialResponse.model_validate(row)}
    except AppException:
        await db.rollback(); raise
    except Exception:
        await db.rollback(); raise


# ---------------------------------------------------------------------------
# Suppliers filtered by plant
# ---------------------------------------------------------------------------

@router.get("/opportunities/{opportunity_id}/current-supplier-evaluation", response_model=dict)
async def get_current_supplier_evaluation(
    opportunity_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Read the latest PldClassEvaluationInput for the current (Before) supplier linked to the opportunity."""
    from sqlalchemy import select
    from app.db.models import PldClassEvaluationInput, SupplierSiteRelation

    svc = PurchasingValueService(db)
    opp = await svc.get_opportunity(opportunity_id)

    if not opp.supplier_id or not opp.plant_id:
        return {"status": "success", "data": None}

    # Find the relation between the current supplier and the plant
    rel_result = await db.execute(
        select(SupplierSiteRelation).where(
            SupplierSiteRelation.id_supplier_unit == opp.supplier_id,
            SupplierSiteRelation.id_site == opp.plant_id,
        )
    )
    relation = rel_result.scalar_one_or_none()
    if not relation:
        return {"status": "success", "data": None}

    # Get latest class evaluation
    eval_result = await db.execute(
        select(PldClassEvaluationInput)
        .where(PldClassEvaluationInput.id_relation == relation.id_relation)
        .order_by(PldClassEvaluationInput.entered_at.desc())
    )
    evaluation = eval_result.scalars().first()
    if not evaluation:
        return {"status": "success", "data": None}

    return {
        "status": "success",
        "data": {
            "top": evaluation.top,
            "lta": evaluation.lta,
            "productivity": evaluation.productivity,
            "quality_certification": evaluation.quality_certification,
            "competitiveness": evaluation.competitiveness,
            "sqma": evaluation.sqma,
            "family_coverage": evaluation.family_coverage,
            "geo_coverage": evaluation.geo_coverage,
            "cons_or_wd": evaluation.cons_or_wd,
            "financial_health": evaluation.financial_health,
            "class_score": str(evaluation.class_score) if evaluation.class_score else None,
            "class_value": evaluation.class_value,
            "impact_score": evaluation.impact_score,
            # Also read supplier_status and grade from the relation
            "supplier_status": relation.supplier_status,
            "class_value_relation": relation.class_value,
            "operational_grade": relation.operational_grade,
            "final_grade": relation.final_grade,
            "panel_decision": relation.panel_decision,
        },
    }


@router.get("/opportunities/{opportunity_id}/export-stp")
async def export_stp_pdf(
    opportunity_id: int,
    phase: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Generate and return the STP document as a downloadable PDF.
    ``phase`` = 0 or 1 (controls the committee section heading).
    """
    import unicodedata
    svc = PurchasingValueService(db)
    opp = await svc.get_opportunity(opportunity_id)
    pdf_bytes = generate_stp_pdf(opp, phase=phase)
    raw_name = opp.opportunity_name or f"opp_{opportunity_id}"
    # Normalise to ASCII — replaces accented/special chars, drops what can't map
    ascii_name = unicodedata.normalize("NFKD", raw_name).encode("ascii", "ignore").decode("ascii")
    safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in ascii_name)[:60]
    filename = f"STP_Phase{phase}_{safe_name}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/suppliers-by-plant/{plant_id}", response_model=dict)
async def get_suppliers_by_plant(
    plant_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    svc = PurchasingValueService(db)
    units = await svc.get_suppliers_by_plant(plant_id)
    return {
        "status": "success",
        "data": [schemas.SupplierOption(**u) for u in units],
    }


# ---------------------------------------------------------------------------
# Document upload / list / delete
# ---------------------------------------------------------------------------

@router.get("/opportunities/{opportunity_id}/documents", response_model=dict)
async def list_documents(
    opportunity_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    svc = PurchasingValueService(db)
    docs = await svc.list_documents(opportunity_id)
    return {
        "status": "success",
        "data": [schemas.OpportunityDocumentResponse.model_validate(d) for d in docs],
    }


@router.post("/opportunities/{opportunity_id}/documents", response_model=dict)
async def upload_document(
    opportunity_id: int,
    file: UploadFile = File(...),
    phase_label: str = Form("General"),
    notes: Optional[str] = Form(None),
    uploaded_by: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        svc = PurchasingValueService(db)
        doc = await svc.upload_document(opportunity_id, file, phase_label, notes, uploaded_by)
        await db.commit()
        return {"status": "success", "data": schemas.OpportunityDocumentResponse.model_validate(doc)}
    except AppException:
        await db.rollback(); raise
    except Exception:
        await db.rollback(); raise


# ---------------------------------------------------------------------------
# Recovery plans — centralised tracking view
# ---------------------------------------------------------------------------

@router.get("/recovery-plans", response_model=dict)
async def get_recovery_plans(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Return all active financial lines that have a recovery plan (any status).
    Includes full opportunity + plant context and computed progress fields.
    """
    today = date.today()
    result = await db.execute(
        select(FinancialLine)
        .where(
            FinancialLine.recovery_status.isnot(None),
            FinancialLine.status == "Active",
        )
        .options(
            selectinload(FinancialLine.opportunity).selectinload(Opportunity.plant),
            selectinload(FinancialLine.monthly_financials),
            selectinload(FinancialLine.plant),
        )
        .order_by(FinancialLine.financial_line_id.desc())
    )
    lines = list(result.scalars().all())

    def _n(v):
        return float(v) if v is not None else 0.0

    items = []
    for line in lines:
        opp = line.opportunity
        plant = line.plant

        # Progress: how much has been recovered since recovery plan was set
        # Use cumulated_real_saving vs expected_annual_saving as proxy
        cum_actual = _n(line.cumulated_real_saving)
        expected = _n(line.expected_annual_saving)
        recovery_amount = _n(line.recovery_amount) if line.recovery_amount else None
        delta_ytd = _n(line.delta_vs_expected_ytd)

        # Overdue: has a target date that is in the past and status != Done
        is_overdue = (
            line.recovery_target_date is not None
            and line.recovery_target_date < today
            and line.recovery_status != "Done"
        )
        # Due soon: target date within next 30 days
        days_to_target = (
            (line.recovery_target_date - today).days
            if line.recovery_target_date and line.recovery_status != "Done"
            else None
        )

        items.append({
            "financial_line_id": line.financial_line_id,
            "line_name": line.line_name,
            "opportunity_id": opp.opportunity_id if opp else None,
            "opportunity_name": opp.opportunity_name if opp else None,
            "opportunity_type": opp.opportunity_type if opp else None,
            "plant_name": plant.site_name if plant else None,
            "follower": line.follower,
            "purchasing_owner": opp.purchasing_owner if opp else None,
            # Financial
            "expected_annual_saving": expected,
            "cumulated_real_saving": cum_actual,
            "delta_ytd": delta_ytd,
            "forecast_eoy_current": _n(line.forecast_eoy_current),
            # Recovery plan
            "recovery_status": line.recovery_status,
            "recovery_note": line.recovery_note,
            "recovery_target_date": str(line.recovery_target_date) if line.recovery_target_date else None,
            "recovery_amount": recovery_amount,
            "recovery_history": line.recovery_history,
            "recovery_updated_at": str(line.recovery_updated_at) if line.recovery_updated_at else None,
            # Computed
            "is_overdue": is_overdue,
            "days_to_target": days_to_target,
            "progress_pct": round((cum_actual / recovery_amount) * 100, 1) if recovery_amount and recovery_amount > 0 else None,
            "is_escalated": line.is_escalated,
        })

    # Summary stats for the header
    total = len(items)
    by_status = {
        "Planned": sum(1 for i in items if i["recovery_status"] == "Planned"),
        "In Progress": sum(1 for i in items if i["recovery_status"] == "In Progress"),
        "Done": sum(1 for i in items if i["recovery_status"] == "Done"),
    }
    total_amount = sum(i["recovery_amount"] for i in items if i["recovery_amount"])
    overdue_count = sum(1 for i in items if i["is_overdue"])

    return {
        "status": "success",
        "data": {
            "items": items,
            "summary": {
                "total": total,
                "by_status": by_status,
                "total_amount_to_recover": round(total_amount, 2),
                "overdue_count": overdue_count,
            },
        },
    }


@router.delete("/documents/{doc_id}", response_model=dict)
async def delete_document(
    doc_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        svc = PurchasingValueService(db)
        await svc.delete_document(doc_id)
        await db.commit()
        return {"status": "success", "message": "Document deleted"}
    except AppException:
        await db.rollback(); raise
    except Exception:
        await db.rollback(); raise


# ── Per-fiscal-year budgeting ──────────────────────────────────────────────

@router.get("/budget-years", response_model=dict)
async def list_budget_years(
    fiscal_year: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Opportunities with a budget record in the given fiscal year, for the
    budgeting page (year filter)."""
    svc = PurchasingValueService(db)
    items = await svc.list_budget_years(fiscal_year)
    # Consolidated totals are in EUR (group reporting currency) — opportunities may be
    # recorded in EUR/USD/RMB/INR, so we sum the EUR-converted amounts.
    def eur(i):
        return i.get("applicable_amount_eur") or 0
    total = sum(eur(i) for i in items)
    budgeted = sum(eur(i) for i in items if i["budget_status"] == "Budgeted")
    opportunity = sum(eur(i) for i in items if i["budget_status"] == "Opportunity")
    validated = sum(eur(i) for i in items if i["suggested_status"] == "Validate")
    return {
        "status": "success",
        "data": {
            "items": items,
            "fiscal_year": fiscal_year,
            "reporting_currency": "EUR",
            "summary": {
                "total": len(items),
                "total_applicable": round(total, 2),
                "total_budgeted": round(budgeted, 2),
                "total_opportunity": round(opportunity, 2),
                "total_validated": round(validated, 2),
            },
        },
    }


@router.post("/budgets/{fiscal_year}/assign", response_model=dict)
async def assign_budget(
    fiscal_year: int,
    payload: schemas.BudgetAssignRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Create Budget — set each chosen opportunity's per-year budget status
    (Empty / Opportunity / Budgeted) for the given fiscal year. The decision is locked
    so it survives recompute."""
    decided_by = (
        payload.decided_by
        or current_user.get("email")
        or current_user.get("upn")
        or current_user.get("sub")
    )
    try:
        svc = PurchasingValueService(db)
        result = await svc.assign_budget_year(
            fiscal_year, [d.model_dump() for d in payload.decisions], decided_by
        )
        await db.commit()
        return {"status": "success", "data": result}
    except AppException:
        await db.rollback(); raise
    except Exception:
        await db.rollback(); raise
