"""Gate approval router — includes a public endpoint (no auth) for vote submission."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.gate_approval import schemas, service as svc_module
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
