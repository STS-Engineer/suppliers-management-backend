"""Pydantic schemas for the committee review workflow."""
from __future__ import annotations

from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, EmailStr, field_validator


# ── Inbound ────────────────────────────────────────────────────────────────

class InitiateReviewRequest(BaseModel):
    relation_id: int


class SubmitDecisionRequest(BaseModel):
    decision: str  # "approved" | "rejected"
    comments: Optional[str] = None
    suggested_supplier_status: Optional[str] = None
    suggested_strategic_mention: Optional[str] = None

    @field_validator("decision")
    @classmethod
    def validate_decision(cls, v: str) -> str:
        if v not in ("approved", "rejected"):
            raise ValueError("decision must be 'approved' or 'rejected'")
        return v


class FinalDecisionRequest(BaseModel):
    decision: str  # "approved" | "rejected"
    comments: Optional[str] = None

    @field_validator("decision")
    @classmethod
    def validate_decision(cls, v: str) -> str:
        if v not in ("approved", "rejected"):
            raise ValueError("decision must be 'approved' or 'rejected'")
        return v


class CommitteeMemberCreate(BaseModel):
    name: str
    position: str
    email: EmailStr
    is_active: bool = True


class CommitteeMemberUpdate(BaseModel):
    name: Optional[str] = None
    position: Optional[str] = None
    email: Optional[EmailStr] = None
    is_active: Optional[bool] = None


# ── Outbound ───────────────────────────────────────────────────────────────

class CommitteeMemberResponse(BaseModel):
    id_member: int
    name: str
    position: str
    email: str
    is_active: bool

    model_config = {"from_attributes": True}


class CommitteeDecisionResponse(BaseModel):
    id_decision: int
    member_email: str
    member_name: Optional[str]
    member_position: Optional[str]
    decision: Optional[str]
    comments: Optional[str]
    decided_at: Optional[datetime]
    accessed_at: Optional[datetime]
    suggested_supplier_status: Optional[str] = None
    suggested_strategic_mention: Optional[str] = None

    model_config = {"from_attributes": True}


class CommitteeReviewResponse(BaseModel):
    id_review: int
    id_relation: int
    status: str
    initiated_by: Optional[str]
    initiated_at: Optional[datetime]
    all_decided_at: Optional[datetime]
    final_decision: Optional[str]
    final_decision_by: Optional[str]
    final_decision_at: Optional[datetime]
    final_decision_comments: Optional[str]
    supplier_snapshot: Optional[dict]
    decisions: List[CommitteeDecisionResponse] = []

    # Derived helpers
    total_members: int = 0
    decided_count: int = 0
    approved_count: int = 0
    rejected_count: int = 0

    model_config = {"from_attributes": True}


class VoteFormResponse(BaseModel):
    """Payload returned by the public GET /vote/{token} endpoint."""
    id_decision: int
    id_review: int
    member_name: Optional[str]
    member_position: Optional[str]
    member_email: str
    already_decided: bool
    decision: Optional[str]
    comments: Optional[str]
    decided_at: Optional[datetime]
    token_expires_at: Optional[datetime]
    supplier_snapshot: Optional[dict]
    review_status: str
