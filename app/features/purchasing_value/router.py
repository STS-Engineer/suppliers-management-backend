"""Purchasing value management router."""

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import select, text
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from typing import Optional
from datetime import date

from app.db.models import FinancialLine, MonthlyFinancial, Opportunity

from app.core.exceptions import AppException
from app.features.purchasing_value import schemas
from app.features.purchasing_value.schemas import opportunity_to_response
from app.features.purchasing_value.service import PurchasingValueService
from app.features.purchasing_value.kpi_service import KpiFilters, PurchasingKpiService
from app.features.purchasing_value.stp_pdf import generate_stp_pdf
from app.features.purchasing_value.full_report_pdf import generate_full_report_pdf
from app.shared.dependencies.auth import get_current_user
from app.shared.dependencies.db import get_db

router = APIRouter(prefix="/purchasing-value", tags=["purchasing-value"])

_PRIVILEGED = ["vp_conversion", "purchasing_director"]
_NON_VIEWER = ["purchasing_manager", "vp_conversion", "purchasing_director", "supplier_owner", "global_purchaser", "local_purchaser"]


def _require(current_user: dict, allowed: list[str]) -> None:
    if current_user.get("access_profile", "") not in allowed:
        raise HTTPException(status_code=403, detail="Insufficient permissions for this action.")


@router.get("/kpis", response_model=dict)
async def get_kpis(
    year: Optional[int] = None,
    plant_ids: Optional[str] = None,   # comma-separated plant IDs: "1,2,3"
    categories: Optional[str] = None,  # comma-separated: "Sourcing,Negotiation"
    buyers: Optional[str] = None,      # comma-separated emails
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    def _parse_ints(raw: Optional[str]) -> list[int]:
        result = []
        for token in (raw or "").split(","):
            token = token.strip()
            if token:
                try:
                    result.append(int(token))
                except ValueError:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Invalid plant_ids value: '{token}' is not an integer.",
                    )
        return result

    filters = KpiFilters(
        year=year,
        plant_ids=_parse_ints(plant_ids),
        categories=[c.strip() for c in categories.split(",") if c.strip()] if categories else [],
        buyer_emails=[b.strip() for b in buyers.split(",") if b.strip()] if buyers else [],
    )
    svc = PurchasingKpiService(db)
    return {"status": "success", "data": await svc.compute_all(filters)}


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
    _require(current_user, _NON_VIEWER)
    try:
        svc = PurchasingValueService(db)
        actor_email = (
            current_user.get("email")
            or current_user.get("upn")
            or current_user.get("sub")
        )
        opp = await svc.create_opportunity(payload, created_by=actor_email)
        await db.commit()
        # Re-fetch after commit — avoids stale session cache (R9 monthly rebuilds, etc.)
        fresh_opp = await svc.get_opportunity(opp.opportunity_id)
        return {"status": "success", "data": opportunity_to_response(fresh_opp)}
    except AppException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


@router.post("/opportunities/{opportunity_id}/duplicate", response_model=dict)
async def duplicate_opportunity(
    opportunity_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require(current_user, _NON_VIEWER)
    try:
        svc = PurchasingValueService(db)
        actor_email = (
            current_user.get("email")
            or current_user.get("upn")
            or current_user.get("sub")
        )
        dup = await svc.duplicate_opportunity(opportunity_id, created_by=actor_email)
        await db.commit()
        # Re-fetch after commit — same pattern as create (avoids stale session cache).
        fresh_opp = await svc.get_opportunity(dup.opportunity_id)
        return {"status": "success", "data": opportunity_to_response(fresh_opp)}
    except AppException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


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
    _require(current_user, _NON_VIEWER)
    try:
        svc = PurchasingValueService(db)
        await svc.update_opportunity(
            opportunity_id, payload, actor_role=current_user.get("access_profile")
        )
        await db.commit()
        # Re-fetch after commit — avoids stale session cache (R9 monthly rebuilds, etc.)
        fresh_opp = await svc.get_opportunity(opportunity_id)
        return {"status": "success", "data": opportunity_to_response(fresh_opp)}
    except AppException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


@router.delete("/opportunities/{opportunity_id}", response_model=dict)
async def delete_opportunity(
    opportunity_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require(current_user, ["vp_conversion"])
    try:
        svc = PurchasingValueService(db)
        actor_email = (
            current_user.get("email")
            or current_user.get("upn")
            or current_user.get("sub")
        )
        await svc.delete_opportunity(opportunity_id, deleted_by=actor_email)
        await db.commit()
        return {"status": "success", "data": {"opportunity_id": opportunity_id}}
    except AppException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


# DISABLED — Request Revision creation is turned off. purchasing_director /
# vp_conversion edit the STP baseline directly in Phase 2/3 (actor_role bypass
# in PurchasingValueService.update_opportunity); other roles get read-only
# fields with no way to submit a change. decide-stp-revision below is kept
# functional so any revision that was already pending before this was disabled
# can still be resolved. Uncomment to re-enable the request workflow.
# @router.post("/opportunities/{opportunity_id}/request-stp-revision", response_model=dict)
# async def request_stp_revision(
#     opportunity_id: int,
#     payload: schemas.STPRevisionRequestPayload,
#     db: AsyncSession = Depends(get_db),
#     current_user: dict = Depends(get_current_user),
# ):
#     """Buyer submits proposed STP price/volume changes for Director approval (Phase 2/3).
#
#     Open to any _NON_VIEWER role. purchasing_director/vp_conversion normally
#     don't need this — they edit the baseline directly via PUT /opportunities/{id}
#     (see the actor_role bypass in PurchasingValueService.update_opportunity) since
#     they're the ones who'd approve their own request anyway.
#     """
#     _require(current_user, _NON_VIEWER)
#     try:
#         svc = PurchasingValueService(db)
#         await svc.request_stp_revision(opportunity_id, payload)
#         await db.commit()
#         fresh = await svc.get_opportunity(opportunity_id)
#         return {"status": "success", "data": opportunity_to_response(fresh)}
#     except AppException:
#         await db.rollback()
#         raise
#     except Exception:
#         await db.rollback()
#         raise


@router.post("/opportunities/{opportunity_id}/decide-stp-revision", response_model=dict)
async def decide_stp_revision(
    opportunity_id: int,
    payload: schemas.STPRevisionDecisionPayload,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Purchasing Director approves or rejects a pending STP revision request.

    Restricted to _PRIVILEGED (purchasing_director, vp_conversion) — matches the
    roles that receive the request email/notification in request_stp_revision,
    and the frontend which only renders the Approve/Reject button for them.
    """
    _require(current_user, _PRIVILEGED)
    try:
        svc = PurchasingValueService(db)
        await svc.decide_stp_revision(opportunity_id, payload)
        await db.commit()
        fresh = await svc.get_opportunity(opportunity_id)
        return {"status": "success", "data": opportunity_to_response(fresh)}
    except AppException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


@router.post("/opportunities/{opportunity_id}/start-study", response_model=dict)
async def start_study(
    opportunity_id: int,
    payload: schemas.StartStudyRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require(current_user, _NON_VIEWER)
    try:
        svc = PurchasingValueService(db)
        await svc.start_study(opportunity_id, payload)
        await db.commit()
        # Re-fetch after commit — avoids stale session cache (R9 monthly rebuilds, etc.)
        fresh_opp = await svc.get_opportunity(opportunity_id)
        return {"status": "success", "data": opportunity_to_response(fresh_opp)}
    except AppException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


@router.post(
    "/opportunities/{opportunity_id}/submit-for-validation", response_model=dict
)
async def submit_for_validation(
    opportunity_id: int,
    payload: schemas.SubmitForValidationRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require(current_user, _NON_VIEWER)
    try:
        svc = PurchasingValueService(db)
        await svc.submit_for_validation(opportunity_id, payload)
        await db.commit()
        fresh_opp = await svc.get_opportunity(opportunity_id)
        return {
            "status": "success",
            "data": opportunity_to_response(fresh_opp),
            "message": "Submitted for PM validation",
        }
    except AppException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


@router.post("/opportunities/{opportunity_id}/submit-to-committee", response_model=dict)
async def submit_to_committee(
    opportunity_id: int,
    payload: schemas.SubmitToCommitteeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require(current_user, _NON_VIEWER)
    try:
        svc = PurchasingValueService(db)
        await svc.submit_to_committee(opportunity_id, payload)
        await db.commit()
        fresh_opp = await svc.get_opportunity(opportunity_id)
        return {
            "status": "success",
            "data": opportunity_to_response(fresh_opp),
            "message": "Submitted to committee",
        }
    except AppException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


@router.post("/opportunities/{opportunity_id}/gate-decision", response_model=dict)
async def apply_gate_decision(
    opportunity_id: int,
    payload: schemas.GateDecisionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require(current_user, _PRIVILEGED)
    try:
        svc = PurchasingValueService(db)
        await svc.apply_gate_decision(opportunity_id, payload)
        await db.commit()
        # Re-fetch after commit — avoids stale session cache (R9 monthly rebuilds, etc.)
        fresh_opp = await svc.get_opportunity(opportunity_id)
        return {"status": "success", "data": opportunity_to_response(fresh_opp)}
    except AppException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


@router.get("/opportunities/{opportunity_id}/phase-history", response_model=dict)
async def get_phase_history(
    opportunity_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Gate decision history with full data snapshots for an opportunity."""
    svc = PurchasingValueService(db)
    snapshots = await svc.get_phase_history(opportunity_id)
    return {
        "status": "success",
        "data": [schemas.PhaseSnapshotResponse.model_validate(s) for s in snapshots],
    }


@router.post(
    "/opportunities/{opportunity_id}/send-validation-request", response_model=dict
)
async def send_validation_request(
    opportunity_id: int,
    payload: schemas.ValidationRequestPayload,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require(current_user, _NON_VIEWER)
    try:
        svc = PurchasingValueService(db)
        await svc.send_validation_request(opportunity_id, payload)
        await db.commit()
        fresh_opp = await svc.get_opportunity(opportunity_id)
        return {
            "status": "success",
            "data": opportunity_to_response(fresh_opp),
            "message": "Validation request sent",
        }
    except AppException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


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
    _require(current_user, _NON_VIEWER)
    try:
        svc = PurchasingValueService(db)
        proj = await svc.update_project(project_id, payload)
        await db.commit()
        return {
            "status": "success",
            "data": schemas.ProjectResponse.model_validate(proj),
        }
    except AppException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


@router.post("/financial-lines/{line_id}/escalate", response_model=dict)
async def escalate_financial_line(
    line_id: int,
    payload: schemas.EscalateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require(current_user, _PRIVILEGED)
    try:
        svc = PurchasingValueService(db)
        line = await svc.escalate_financial_line(line_id, payload)
        await db.commit()
        return {
            "status": "success",
            "data": schemas.FinancialLineResponse.model_validate(line),
        }
    except AppException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


@router.post("/financial-lines/{line_id}/deescalate", response_model=dict)
async def deescalate_financial_line(
    line_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require(current_user, _PRIVILEGED)
    try:
        svc = PurchasingValueService(db)
        line = await svc.deescalate_financial_line(line_id, None)
        await db.commit()
        return {
            "status": "success",
            "data": schemas.FinancialLineResponse.model_validate(line),
        }
    except AppException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


@router.put("/financial-lines/{line_id}/recovery", response_model=dict)
async def set_recovery(
    line_id: int,
    payload: schemas.RecoveryUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require(current_user, _NON_VIEWER)
    try:
        svc = PurchasingValueService(db)
        line = await svc.set_recovery(line_id, payload)
        await db.commit()
        return {
            "status": "success",
            "data": schemas.FinancialLineResponse.model_validate(line),
        }
    except AppException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


@router.post("/opportunities/{opportunity_id}/financial-lines", response_model=dict)
async def create_component_line(
    opportunity_id: int,
    payload: schemas.AddComponentLineRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require(current_user, _NON_VIEWER)
    try:
        svc = PurchasingValueService(db)
        line = await svc.create_component_line(opportunity_id, payload)
        await db.commit()
        return {
            "status": "success",
            "data": schemas.FinancialLineResponse.model_validate(line),
        }
    except AppException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


@router.post("/financial-lines/{line_id}/rebuild-profile", response_model=dict)
async def rebuild_monthly_profile(
    line_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Rebuild monthly expected rows using equal monthly distribution (annual ÷ duration,
    escalating per STP year-window where applicable). Use this to regenerate the monthly
    profile for an existing line after its baseline or start date changed."""
    _require(current_user, _NON_VIEWER)
    try:
        from app.db.models import FinancialLine as FL
        from sqlalchemy import select

        result = await db.execute(select(FL).where(FL.financial_line_id == line_id))
        line = result.scalar_one_or_none()
        if not line:
            raise AppException(404, "Financial line not found", "NOT_FOUND")
        svc = PurchasingValueService(db)
        start = line.real_start_date or line.planned_start_date
        if start is None or line.expected_annual_saving is None:
            raise AppException(
                422,
                "Line needs planned_start_date and expected_annual_saving.",
                "MISSING_DATA",
            )
        duration = int(line.duration_months or 12)
        await svc._rebuild_monthly_profile(
            line, line.expected_annual_saving, start, duration
        )
        await svc._recalculate_ytd(line_id)
        await db.commit()
        return {
            "status": "success",
            "message": f"Rebuilt {duration} monthly rows using days-based pro-ration.",
        }
    except AppException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


@router.post("/financial-lines/{line_id}/revise-baseline", response_model=dict)
async def revise_financial_line_baseline(
    line_id: int,
    payload: schemas.FinancialLineReviseBaselineRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require(current_user, _PRIVILEGED)
    try:
        svc = PurchasingValueService(db)
        line = await svc.revise_financial_line_baseline(line_id, payload)
        await db.commit()
        return {
            "status": "success",
            "data": schemas.FinancialLineResponse.model_validate(line),
        }
    except AppException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


@router.post("/financial-lines/{line_id}/complete", response_model=dict)
async def complete_financial_line(
    line_id: int,
    payload: schemas.FinancialLineCompleteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require(current_user, _NON_VIEWER)
    try:
        svc = PurchasingValueService(db)
        line = await svc.complete_financial_line(line_id, payload)
        await db.commit()
        return {
            "status": "success",
            "data": schemas.FinancialLineResponse.model_validate(line),
        }
    except AppException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


@router.put("/monthly/{month_id}", response_model=dict)
async def update_monthly_actual(
    month_id: int,
    payload: schemas.MonthlyActualUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require(current_user, _NON_VIEWER)
    # If this month already has a recorded actual saving or cash value, it is a
    # modification/supervision action — only privileged roles (PD / VPC) may overwrite it.
    existing = await db.get(MonthlyFinancial, month_id)
    if existing and (existing.actual_saving is not None or existing.cash_actual is not None):
        _require(current_user, _PRIVILEGED)
    try:
        svc = PurchasingValueService(db)
        row = await svc.update_monthly_actual(month_id, payload)
        await db.commit()
        return {
            "status": "success",
            "data": schemas.MonthlyFinancialResponse.model_validate(row),
        }
    except AppException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


# ---------------------------------------------------------------------------
# Suppliers filtered by plant
# ---------------------------------------------------------------------------


@router.get(
    "/opportunities/{opportunity_id}/current-supplier-evaluation", response_model=dict
)
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

    # quality_certification is never trusted from the frozen snapshot -- it's the
    # one criterion backed by an independent, separately-editable record
    # (SupplierCertification) that can expire without the evaluation being re-saved.
    from app.features.supplier_relations.service import SupplierRelationService

    rel_service = SupplierRelationService(db)
    live_quality_certification = await rel_service._get_relation_quality_certification(relation)

    return {
        "status": "success",
        "data": {
            "top": evaluation.top,
            "lta": evaluation.lta,
            "productivity": evaluation.productivity,
            "quality_certification": live_quality_certification,
            "competitiveness": evaluation.competitiveness,
            "sqma": evaluation.sqma,
            "family_coverage": evaluation.family_coverage,
            "geo_coverage": evaluation.geo_coverage,
            "cons_or_wd": evaluation.cons_or_wd,
            "financial_health": evaluation.financial_health,
            "class_score": str(evaluation.class_score)
            if evaluation.class_score
            else None,
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
    ascii_name = (
        unicodedata.normalize("NFKD", raw_name)
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in ascii_name)[
        :60
    ]
    filename = f"STP_Phase{phase}_{safe_name}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/opportunities/{opportunity_id}/export-full-report")
async def export_full_report_pdf(
    opportunity_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Generate and return the Full Opportunity Report as a downloadable PDF —
    a live snapshot across all phases, available for any opportunity type."""
    import unicodedata

    svc = PurchasingValueService(db)
    opp = await svc.get_opportunity(opportunity_id)
    pdf_bytes = generate_full_report_pdf(opp)
    raw_name = opp.opportunity_name or f"opp_{opportunity_id}"
    ascii_name = (
        unicodedata.normalize("NFKD", raw_name)
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in ascii_name)[
        :60
    ]
    filename = f"FullReport_{safe_name}.pdf"
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


@router.get("/negotiation-approvers", response_model=dict)
async def get_negotiation_approvers(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """List active Purchasing Director / VP Conversion accounts for the
    Negotiation gate approver picker (single approver, either role)."""
    rows = await db.execute(
        text("""
            SELECT id_identity, full_name, email, access_profile
            FROM access_identity
            WHERE is_active = TRUE
              AND access_profile IN ('purchasing_director', 'vp_conversion')
            ORDER BY access_profile, full_name
        """)
    )
    return {
        "status": "success",
        "data": [
            {
                "id_identity": r.id_identity,
                "full_name": r.full_name,
                "email": r.email,
                "access_profile": r.access_profile,
            }
            for r in rows.fetchall()
        ],
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
    _require(current_user, _NON_VIEWER)
    try:
        svc = PurchasingValueService(db)
        doc = await svc.upload_document(
            opportunity_id, file, phase_label, notes, uploaded_by
        )
        await db.commit()
        return {
            "status": "success",
            "data": schemas.OpportunityDocumentResponse.model_validate(doc),
        }
    except AppException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


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

        # Progress: use the gap snapshot captured when the recovery cycle started.
        cum_actual = _n(line.cumulated_real_saving)
        expected = _n(line.expected_annual_saving)
        recovery_amount = _n(line.recovery_amount) if line.recovery_amount else None
        delta_ytd = _n(line.delta_vs_expected_ytd)
        baseline_gap = _n(line.recovery_baseline_gap) if line.recovery_baseline_gap else None
        effective_baseline_gap = baseline_gap if baseline_gap is not None else (
            max(-delta_ytd, 0.0) if recovery_amount is not None else None
        )
        remaining_gap = max(-delta_ytd, 0.0)
        recovered_amount = (
            max(effective_baseline_gap - remaining_gap, 0.0)
            if effective_baseline_gap is not None
            else None
        )

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

        items.append(
            {
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
                "recovery_target_date": str(line.recovery_target_date)
                if line.recovery_target_date
                else None,
                "recovery_amount": recovery_amount,
                "recovery_history": line.recovery_history,
                "recovery_updated_at": str(line.recovery_updated_at)
                if line.recovery_updated_at
                else None,
                "recovery_baseline_gap": effective_baseline_gap,
                "recovery_baseline_set_at": str(line.recovery_baseline_set_at)
                if line.recovery_baseline_set_at
                else None,
                # Computed
                "is_overdue": is_overdue,
                "days_to_target": days_to_target,
                "progress_pct": round(
                    min((recovered_amount / effective_baseline_gap) * 100, 100.0), 1
                )
                if effective_baseline_gap and effective_baseline_gap > 0
                else None,
                "is_escalated": line.is_escalated,
            }
        )

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
    _require(current_user, _NON_VIEWER)
    try:
        svc = PurchasingValueService(db)
        await svc.delete_document(doc_id)
        await db.commit()
        return {"status": "success", "message": "Document deleted"}
    except AppException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


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
    closure = await svc.get_budget_year_closure(fiscal_year)

    # Consolidated totals are in EUR (group reporting currency) — opportunities may be
    # recorded in EUR/USD/RMB/INR, so we sum the EUR-converted amounts.
    def eur(i):
        return i.get("applicable_amount_eur") or 0

    def cash_expected_eur(i):
        return i.get("cash_expected_eur") or 0

    def cash_actual_eur(i):
        return i.get("cash_actual_eur") or 0

    baseline = [i for i in items if not i.get("is_additional")]
    additional = [i for i in items if i.get("is_additional")]

    # Finance-meaningful breakdowns
    baseline_budgeted_eur = sum(eur(i) for i in baseline if i["budget_status"] == "Budgeted")
    additional_accepted_eur = sum(eur(i) for i in additional if i["budget_status"] == "Budgeted")
    total_budget_eur = baseline_budgeted_eur + additional_accepted_eur

    # Cash totals — same baseline/additional/total breakdown, mirroring the
    # savings totals above, sourced from Cash-type opportunities' monthly tracking.
    baseline_cash_expected_eur = sum(cash_expected_eur(i) for i in baseline if i["budget_status"] == "Budgeted")
    additional_cash_expected_eur = sum(cash_expected_eur(i) for i in additional if i["budget_status"] == "Budgeted")
    total_cash_expected_eur = baseline_cash_expected_eur + additional_cash_expected_eur
    baseline_cash_actual_eur = sum(cash_actual_eur(i) for i in baseline if i["budget_status"] == "Budgeted")
    additional_cash_actual_eur = sum(cash_actual_eur(i) for i in additional if i["budget_status"] == "Budgeted")
    total_cash_actual_eur = baseline_cash_actual_eur + additional_cash_actual_eur

    return {
        "status": "success",
        "data": {
            "items": items,
            "fiscal_year": fiscal_year,
            "reporting_currency": "EUR",
            "closure": closure,
            "summary": {
                "total": len(items),
                "total_baseline": len(baseline),
                "total_additional": len(additional),
                # Finance KPIs
                "baseline_budgeted_eur": round(baseline_budgeted_eur, 2),
                "additional_accepted_eur": round(additional_accepted_eur, 2),
                "total_budget_eur": round(total_budget_eur, 2),
                # Cash KPIs — Cash-type opportunities only, planned (expected) and actual
                "baseline_cash_expected_eur": round(baseline_cash_expected_eur, 2),
                "additional_cash_expected_eur": round(additional_cash_expected_eur, 2),
                "total_cash_expected_eur": round(total_cash_expected_eur, 2),
                "baseline_cash_actual_eur": round(baseline_cash_actual_eur, 2),
                "additional_cash_actual_eur": round(additional_cash_actual_eur, 2),
                "total_cash_actual_eur": round(total_cash_actual_eur, 2),
                "additional_pending": sum(1 for i in additional if i["budget_status"] == "Opportunity"),
                "additional_accepted": sum(1 for i in additional if i["budget_status"] == "Budgeted"),
                "additional_rejected": sum(1 for i in additional if i["budget_status"] == "Empty"),
                # Legacy totals kept for compatibility
                "total_applicable": round(sum(eur(i) for i in items), 2),
                "total_budgeted": round(sum(eur(i) for i in items if i["budget_status"] == "Budgeted"), 2),
                "total_opportunity": round(sum(eur(i) for i in items if i["budget_status"] == "Opportunity"), 2),
                "total_empty": round(sum(eur(i) for i in items if i["budget_status"] == "Empty"), 2),
                "total_validated": round(sum(eur(i) for i in items if i["suggested_status"] == "Validate"), 2),
                # Data-quality: non-EUR opportunities with no usable fx_rate_to_eur are
                # excluded from every *_eur total above (0.0 would look like a real
                # zero) — surfaced here so Finance can see how many rows are missing.
                "fx_missing_count": sum(1 for i in items if i.get("fx_missing")),
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
    _require(current_user, _PRIVILEGED)
    decided_by = (
        payload.decided_by
        or current_user.get("email")
        or current_user.get("upn")
        or current_user.get("sub")
    )
    try:
        svc = PurchasingValueService(db)
        result = await svc.assign_budget_year(
            fiscal_year,
            [d.model_dump(exclude_unset=True) for d in payload.decisions],
            decided_by,
        )
        await db.commit()
        return {"status": "success", "data": result}
    except AppException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


@router.get("/budget-years/{fiscal_year}/closure", response_model=dict)
async def get_budget_year_closure(
    fiscal_year: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Return the closure record for a fiscal year, or null if not yet closed."""
    svc = PurchasingValueService(db)
    closure = await svc.get_budget_year_closure(fiscal_year)
    return {"status": "success", "data": closure}


@router.post("/budget-years/{fiscal_year}/close", response_model=dict)
async def close_budget_year(
    fiscal_year: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Officially close the budget for a fiscal year (director action).

    - Creates a BudgetYearClosure record (idempotent-safe: 409 if already closed).
    - Locks all Budgeted rows for this FY.
    - From this point, any new opportunity row for this FY is flagged is_additional=True.
    """
    _require(current_user, _PRIVILEGED)
    user_email = (
        current_user.get("email")
        or current_user.get("upn")
        or current_user.get("sub")
        or "unknown"
    )
    try:
        svc = PurchasingValueService(db)
        result = await svc.close_budget_year(fiscal_year, user_email)
        await db.commit()
        return {"status": "success", "data": result}
    except AppException:
        await db.rollback()
        raise
    except IntegrityError:
        # Two concurrent "Close" requests can both pass the existence check
        # before either commits — the second one's INSERT then fails on
        # BudgetYearClosure.fiscal_year's unique constraint. Surface the same
        # clean 409 the pre-check gives, instead of a raw 500.
        await db.rollback()
        raise AppException(
            f"Budget year {fiscal_year} is already closed "
            f"(closed concurrently by another request).",
            status_code=409,
        )
    except Exception:
        await db.rollback()
        raise


@router.post("/budgets/{fiscal_year}/delta-reasons", response_model=dict)
async def update_delta_reasons(
    fiscal_year: int,
    payload: schemas.DeltaReasonUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Update delta_reason only for a set of opportunities in a fiscal year.
    Does not touch budget_status or lock timestamps."""
    _require(current_user, _PRIVILEGED)
    try:
        svc = PurchasingValueService(db)
        result = await svc.update_delta_reasons(
            fiscal_year, [d.model_dump() for d in payload.decisions]
        )
        await db.commit()
        return {"status": "success", "data": result}
    except Exception:
        await db.rollback()
        raise


# ── Opportunity Action Plans ───────────────────────────────────────────────


@router.get("/opportunities/{opportunity_id}/action-plans", response_model=dict)
async def list_action_plans(
    opportunity_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    svc = PurchasingValueService(db)
    plans = await svc.list_action_plans(opportunity_id)
    from app.features.purchasing_value.schemas import ActionPlanResponse
    return {
        "status": "success",
        "data": [ActionPlanResponse.model_validate(p).model_dump() for p in plans],
    }


@router.post("/opportunities/{opportunity_id}/action-plans", response_model=dict)
async def create_action_plan(
    opportunity_id: int,
    payload: schemas.ActionPlanCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require(current_user, _NON_VIEWER)
    user_email = (
        current_user.get("email")
        or current_user.get("upn")
        or current_user.get("sub")
        or "unknown"
    )
    try:
        svc = PurchasingValueService(db)
        plan = await svc.create_action_plan(opportunity_id, payload, user_email)
        await db.commit()
        from app.features.purchasing_value.schemas import ActionPlanResponse
        return {"status": "success", "data": ActionPlanResponse.model_validate(plan).model_dump()}
    except AppException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


@router.put("/opportunities/{opportunity_id}/action-plans/{action_plan_id}", response_model=dict)
async def update_action_plan(
    opportunity_id: int,
    action_plan_id: int,
    payload: schemas.ActionPlanUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require(current_user, _NON_VIEWER)
    user_email = (
        current_user.get("email")
        or current_user.get("upn")
        or current_user.get("sub")
        or "unknown"
    )
    try:
        svc = PurchasingValueService(db)
        plan = await svc.update_action_plan(action_plan_id, payload, user_email, opportunity_id)
        await db.commit()
        from app.features.purchasing_value.schemas import ActionPlanResponse
        return {"status": "success", "data": ActionPlanResponse.model_validate(plan).model_dump()}
    except AppException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


@router.delete("/opportunities/{opportunity_id}/action-plans/{action_plan_id}", response_model=dict)
async def delete_action_plan(
    opportunity_id: int,
    action_plan_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require(current_user, _NON_VIEWER)
    try:
        svc = PurchasingValueService(db)
        await svc.delete_action_plan(action_plan_id, opportunity_id)
        await db.commit()
        return {"status": "success", "message": "Action plan deleted"}
    except AppException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


@router.post(
    "/opportunities/{opportunity_id}/action-plans/{action_plan_id}/sync",
    response_model=dict,
)
async def sync_action_plan(
    opportunity_id: int,
    action_plan_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Push a pending action plan to the external sales-feedback API.
    Use this once ACTION_PLAN_DATABASE_URL is configured on Azure.
    """
    _require(current_user, _NON_VIEWER)
    try:
        svc = PurchasingValueService(db)
        result = await svc.sync_action_plan(action_plan_id, opportunity_id)
        await db.commit()
        return {"status": "success", "data": result}
    except AppException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


@router.get("/action-plans/all-items", response_model=dict)
async def list_all_action_items(
    responsible_email: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    opportunity_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Cross-opportunity action item feed, flattened from all plan JSONB documents.
    Optionally filtered by responsible person, action status, or specific opportunity.
    """
    svc = PurchasingValueService(db)
    items = await svc.list_all_action_items(responsible_email, status, opportunity_id)
    return {"status": "success", "data": items}


@router.post(
    "/opportunities/{opportunity_id}/action-plans/{action_plan_id}/evidence",
    response_model=dict,
)
async def upload_action_evidence(
    opportunity_id: int,
    action_plan_id: int,
    sujet_idx: int = Query(..., description="Index of the subject in plan_data.sujets"),
    action_idx: int = Query(..., description="Index of the action within that subject"),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Upload an evidence file for a specific action inside an action plan.
    The file is stored in Azure Blob and the download URL is appended to
    plan_data.sujets[sujet_idx].actions[action_idx].attachments.
    """
    _require(current_user, _NON_VIEWER)
    user_email = (
        current_user.get("email")
        or current_user.get("upn")
        or current_user.get("sub")
        or "unknown"
    )
    try:
        svc = PurchasingValueService(db)
        result = await svc.upload_action_evidence(
            action_plan_id, sujet_idx, action_idx, file, user_email, opportunity_id
        )
        await db.commit()
        return {"status": "success", "data": result}
    except AppException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


@router.delete(
    "/opportunities/{opportunity_id}/action-plans/{action_plan_id}/evidence",
    response_model=dict,
)
async def delete_action_evidence(
    opportunity_id: int,
    action_plan_id: int,
    sujet_idx: int = Query(..., description="Index of the subject in plan_data.sujets"),
    action_idx: int = Query(..., description="Index of the action within that subject"),
    blob_name: str = Query(..., description="Blob storage key of the attachment to delete"),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require(current_user, _NON_VIEWER)
    user_email = (
        current_user.get("email")
        or current_user.get("upn")
        or current_user.get("sub")
        or "unknown"
    )
    try:
        svc = PurchasingValueService(db)
        await svc.delete_action_evidence(
            action_plan_id,
            sujet_idx,
            action_idx,
            blob_name,
            user_email,
            opportunity_id,
        )
        await db.commit()
        return {"status": "success", "message": "Attachment deleted"}
    except AppException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


@router.patch("/action-plans/{action_plan_id}/item-status", response_model=dict)
async def update_action_item_status(
    action_plan_id: int,
    sujet_idx: int = Query(..., description="Index of the subject in plan_data.sujets"),
    action_idx: int = Query(..., description="Index of the action within that subject"),
    status: str = Query(..., description="New status: open | closed | blocked"),
    implementation_date: Optional[str] = Query(None, description="ISO date (YYYY-MM-DD), required when closing"),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Update a single action's status (and closed_date when closing) inside a plan's JSONB."""
    _require(current_user, _NON_VIEWER)
    user_email = (
        current_user.get("email")
        or current_user.get("upn")
        or current_user.get("sub")
        or "unknown"
    )
    try:
        svc = PurchasingValueService(db)
        result = await svc.update_action_item_status(
            action_plan_id, sujet_idx, action_idx, status, implementation_date, user_email
        )
        await db.commit()
        return {"status": "success", "data": result}
    except AppException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


@router.post("/action-plans/{action_plan_id}/items/remind", response_model=dict)
async def remind_action_item(
    action_plan_id: int,
    sujet_idx: int = Query(..., description="Index of the subject in plan_data.sujets"),
    action_idx: int = Query(..., description="Index of the action within that subject"),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Email the responsible person a reminder about one open action item."""
    _require(current_user, _NON_VIEWER)
    sent_by = (
        current_user.get("email")
        or current_user.get("upn")
        or current_user.get("sub")
        or "unknown"
    )
    svc = PurchasingValueService(db)
    try:
        result = await svc.send_action_item_reminder(
            action_plan_id, sujet_idx, action_idx, sent_by
        )
        await db.commit()
    except AppException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise
    return {"status": "success", "data": result}


@router.post("/action-plans/{action_plan_id}/items/escalate", response_model=dict)
async def escalate_action_item(
    action_plan_id: int,
    payload: schemas.EscalateActionItemRequest,
    sujet_idx: int = Query(..., description="Index of the subject in plan_data.sujets"),
    action_idx: int = Query(..., description="Index of the action within that subject"),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Email an arbitrary recipient (e.g. a manager or director) about an action item."""
    _require(current_user, _NON_VIEWER)
    escalated_by = (
        current_user.get("email")
        or current_user.get("upn")
        or current_user.get("sub")
        or "unknown"
    )
    svc = PurchasingValueService(db)
    try:
        result = await svc.send_action_item_escalation(
            action_plan_id,
            sujet_idx,
            action_idx,
            payload.recipient_email,
            payload.subject,
            payload.message,
            escalated_by,
        )
        await db.commit()
    except AppException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise
    return {"status": "success", "data": result}
