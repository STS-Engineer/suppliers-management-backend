"""Committee review router — authenticated + public endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.committee_review import schemas
from app.features.committee_review.service import CommitteeReviewService
from app.shared.dependencies.auth import get_current_user
from app.shared.dependencies.db import get_db
from app.core.exceptions import ForbiddenError

router = APIRouter(prefix="/committee-reviews", tags=["committee-reviews"])

_VP = ["vp_conversion"]
_PRIVILEGED = ["vp_conversion", "purchasing_director"]


def _require_vp(current_user: dict) -> None:
    if current_user.get("access_profile") not in _VP:
        raise ForbiddenError("Only VP Conversion can perform this action.")


def _get_email(current_user: dict) -> str:
    return current_user.get("email") or current_user.get("sub", "")


# ── Authenticated ──────────────────────────────────────────────────────────


@router.post("", response_model=dict)
async def initiate_review(
    payload: schemas.InitiateReviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """VP Conversion initiates a committee review for a relation."""
    _require_vp(current_user)
    svc = CommitteeReviewService(db)
    review = await svc.initiate_review(
        relation_id=payload.relation_id,
        initiated_by=_get_email(current_user),
    )
    await db.commit()
    data = await svc.get_review(review.id_review)
    return {"status": "success", "data": data.model_dump()}


@router.get("/relation/{relation_id}", response_model=dict)
async def get_latest_review(
    relation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Get the most recent committee review for a relation."""
    svc = CommitteeReviewService(db)
    data = await svc.get_latest_review_for_relation(relation_id)
    return {"status": "success", "data": data.model_dump() if data else None}


@router.get("/{review_id}", response_model=dict)
async def get_review(
    review_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Get full review details including all member decisions."""
    svc = CommitteeReviewService(db)
    data = await svc.get_review(review_id)
    return {"status": "success", "data": data.model_dump()}


@router.post("/{review_id}/final-decision", response_model=dict)
async def submit_final_decision(
    review_id: int,
    payload: schemas.FinalDecisionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """VP Conversion submits the final decision after reviewing all committee responses."""
    _require_vp(current_user)
    svc = CommitteeReviewService(db)
    data = await svc.submit_final_decision(
        review_id=review_id,
        payload=payload,
        decided_by=_get_email(current_user),
    )
    await db.commit()
    return {"status": "success", "data": data.model_dump()}


# ── Committee member management ────────────────────────────────────────────


@router.get("/members/list", response_model=dict)
async def list_members(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    svc = CommitteeReviewService(db)
    members = await svc.list_members(active_only=False)
    return {
        "status": "success",
        "data": [schemas.CommitteeMemberResponse.model_validate(m) for m in members],
    }


@router.post("/members", response_model=dict)
async def create_member(
    payload: schemas.CommitteeMemberCreate,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_vp(current_user)
    svc = CommitteeReviewService(db)
    member = await svc.create_member(payload)
    await db.commit()
    return {
        "status": "success",
        "data": schemas.CommitteeMemberResponse.model_validate(member),
    }


@router.put("/members/{id_member}", response_model=dict)
async def update_member(
    id_member: int,
    payload: schemas.CommitteeMemberUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_vp(current_user)
    svc = CommitteeReviewService(db)
    member = await svc.update_member(id_member, payload)
    await db.commit()
    return {
        "status": "success",
        "data": schemas.CommitteeMemberResponse.model_validate(member),
    }


# ── Public — no auth, token is identity ───────────────────────────────────


@router.get("/vote/{token}", response_model=dict)
async def get_vote_form(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """Public — load vote form by token."""
    svc = CommitteeReviewService(db)
    data = await svc.get_vote_by_token(token)
    await db.commit()
    return {"status": "success", "data": data.model_dump()}


@router.post("/vote/{token}", response_model=dict)
async def submit_vote(
    token: str,
    payload: schemas.SubmitDecisionRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Public — submit a committee decision."""
    ip = request.client.host if request.client else None
    svc = CommitteeReviewService(db)
    data = await svc.submit_vote(token=token, payload=payload, ip_address=ip)
    await db.commit()
    return {"status": "success", "data": data.model_dump()}
