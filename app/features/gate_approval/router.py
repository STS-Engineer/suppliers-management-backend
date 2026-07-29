"""Gate approval router — includes a public endpoint (no auth) for vote submission."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.db.models import GateApprovalVote
from app.features.gate_approval import pm_directory, schemas, service as svc_module
from app.shared.dependencies.auth import get_current_user
from app.shared.dependencies.db import get_db

router = APIRouter(prefix="/gate-approvals", tags=["gate-approvals"])


# ── Authenticated endpoints ────────────────────────────────────────────


@router.post("/opportunities/{opportunity_id}/request", response_model=dict)
async def create_approval_request(
    opportunity_id: int,
    payload: schemas.GateApprovalCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Buyer submits gate approval request to a list of approvers."""
    svc = svc_module.GateApprovalService(db)
    req = await svc.create_approval_request(
        opportunity_id=opportunity_id,
        payload=payload,
        requested_by=current_user.get("email", current_user.get("sub", "")),
    )
    await db.commit()
    return {"status": "success", "data": schemas.GateApprovalRequestResponse.model_validate(req)}


@router.post("/opportunities/{opportunity_id}/committee-request", response_model=dict)
async def create_committee_approval_request(
    opportunity_id: int,
    payload: schemas.CommitteeGateApprovalCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Submit a Phase 1-4 sourcing committee gate approval request."""
    svc = svc_module.GateApprovalService(db)
    req = await svc.create_committee_approval_request(
        opportunity_id=opportunity_id,
        payload=payload,
        requested_by=current_user.get("email", current_user.get("sub", "")),
    )
    await db.commit()
    return {"status": "success", "data": schemas.GateApprovalRequestResponse.model_validate(req)}


@router.get("/opportunities/{opportunity_id}", response_model=dict)
async def get_approval_status(
    opportunity_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Return all gate approval requests for an opportunity."""
    svc = svc_module.GateApprovalService(db)
    requests = await svc.get_approval_status(opportunity_id)
    return {
        "status": "success",
        "data": [schemas.GateApprovalRequestResponse.model_validate(r) for r in requests],
    }


@router.get("/pm-directory", response_model=dict)
async def get_pm_directory_authenticated(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Authenticated — list AVO Carbon members with an email, for approver
    pickers inside the app (e.g. sourcing committee role assignment). Same
    live-MCP-with-local-fallback lookup as the public vote-form picker."""
    data = await pm_directory.get_pm_directory(db)
    return {"status": "success", "data": schemas.PmDirectoryResponse(**data).model_dump()}


@router.post("/opportunities/{opportunity_id}/remind", response_model=dict)
async def send_reminders(
    opportunity_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Re-send approval links to approvers who have not yet recorded a decision."""
    svc = svc_module.GateApprovalService(db)
    result = await svc.send_reminders(
        opportunity_id=opportunity_id,
        requested_by=current_user.get("email", current_user.get("sub", "")),
    )
    await db.commit()
    return {"status": "success", **result}


@router.post("/requests/{request_id}/notify-pm", response_model=dict)
async def resend_pm_notification(
    request_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Manually (re)send the Project Manager handover email for an approved gate."""
    svc = svc_module.GateApprovalService(db)
    result = await svc.resend_pm_notification(request_id)
    await db.commit()
    return {"status": "success", **result}


@router.put("/opportunities/{opportunity_id}/project-manager", response_model=dict)
async def update_project_manager(
    opportunity_id: int,
    payload: schemas.ProjectManagerUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Correct/reassign the Project Manager for an opportunity (e.g. a plant
    manager typo'd the email during the gate vote) and resend the handover
    email to the corrected address."""
    svc = svc_module.GateApprovalService(db)
    result = await svc.update_project_manager(
        opportunity_id=opportunity_id,
        new_pm_email=payload.project_manager_email,
        updated_by=current_user.get("email", current_user.get("sub", "")),
    )
    await db.commit()
    return {"status": "success", **result}


# ── Public endpoints — no auth, UUID token is the identity ────────────


@router.get("/vote/{token}", response_model=dict)
async def get_vote_form(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """Public — load the approval form data by token."""
    svc = svc_module.GateApprovalService(db)
    data = await svc.get_vote_by_token(token)
    await db.commit()  # persists accessed_at set inside the service
    return {"status": "success", "data": data.model_dump()}


@router.get("/vote/{token}/pm-directory", response_model=dict)
async def get_pm_directory(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """Public — list AVO Carbon members with an email, for the plant manager's
    Project Manager picker. Live from the AVO Carbon Central MCP, falling
    back to the last successfully synced local snapshot if the MCP is down."""
    result = await db.execute(
        select(GateApprovalVote).where(GateApprovalVote.access_token == token)
    )
    if not result.scalar_one_or_none():
        raise AppException(404, "Approval link not found.", "VOTE_NOT_FOUND")

    data = await pm_directory.get_pm_directory(db)
    return {"status": "success", "data": schemas.PmDirectoryResponse(**data).model_dump()}


@router.post("/vote/{token}", response_model=dict)
async def submit_vote(
    token: str,
    payload: schemas.VoteSubmitRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Public — submit an approval decision."""
    ip = request.client.host if request.client else None
    svc = svc_module.GateApprovalService(db)
    data = await svc.submit_vote(token=token, payload=payload, ip_address=ip)
    await db.commit()
    return {"status": "success", "data": data.model_dump()}
