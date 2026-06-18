"""Gate approval schemas."""
from __future__ import annotations
import re
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict, field_validator

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_email(v: str) -> str:
    if not _EMAIL_RE.match(v.strip()):
        raise ValueError(f"Invalid email address: {v!r}")
    return v.strip().lower()


class GateApprovalCreateRequest(BaseModel):
    plant_manager_email: str = Field(..., description="Plant manager email — will designate the Project Manager")
    purchasing_manager_emails: List[str] = Field(default_factory=list, description="Additional approvers (purchasing manager, etc.)")
    message: Optional[str] = None

    @field_validator("plant_manager_email")
    @classmethod
    def validate_plant_email(cls, v: str) -> str:
        return _validate_email(v)

    @field_validator("purchasing_manager_emails", mode="before")
    @classmethod
    def validate_purchasing_emails(cls, v: List[str]) -> List[str]:
        return [_validate_email(e) for e in v if e and e.strip()]


class VoteSubmitRequest(BaseModel):
    decision: str = Field(..., description="Approved | Rejected | Needs Review")
    comment: Optional[str] = None
    project_manager_email: Optional[str] = None

    @field_validator("project_manager_email")
    @classmethod
    def validate_pm_email(cls, v: Optional[str]) -> Optional[str]:
        if v is None or not v.strip():
            return None
        return _validate_email(v)


class VoteResponse(BaseModel):
    vote_id: int
    approver_email: Optional[str] = None
    access_token: Optional[str] = None
    is_plant_manager: Optional[bool] = None
    decision: Optional[str] = None
    comment: Optional[str] = None
    project_manager_email: Optional[str] = None
    decided_at: Optional[datetime] = None
    accessed_at: Optional[datetime] = None
    token_expires_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class GateApprovalRequestResponse(BaseModel):
    request_id: int
    opportunity_id: int
    phase_from: Optional[str] = None
    requested_by: Optional[str] = None
    requested_at: Optional[datetime] = None
    message: Optional[str] = None
    status: Optional[str] = None
    consensus_result: Optional[str] = None
    applied_at: Optional[datetime] = None
    votes: List[VoteResponse] = []

    model_config = ConfigDict(from_attributes=True)


class PeerVote(BaseModel):
    """Minimal vote info shown to other approvers (mutual visibility)."""
    approver_email: Optional[str] = None
    is_plant_manager: Optional[bool] = None
    decision: Optional[str] = None
    decided_at: Optional[datetime] = None


class VoteFormData(BaseModel):
    """Public-facing data shown on the approval form page."""
    vote_id: int
    approver_email: Optional[str] = None
    already_decided: bool = False
    decision: Optional[str] = None
    token_expires_at: Optional[datetime] = None
    requires_project_manager: bool = False

    # All votes on the same request (mutual visibility)
    all_votes: List[PeerVote] = []

    # Identity & context
    opportunity_name: Optional[str] = None
    opportunity_type: Optional[str] = None
    phase_from: Optional[str] = None
    requested_by: Optional[str] = None
    message: Optional[str] = None
    idea_owner: Optional[str] = None
    project_owner: Optional[str] = None
    change_mode: Optional[str] = None

    # STP pricing — current vs proposed per year
    current_price: Optional[float] = None
    proposed_price: Optional[float] = None
    current_price_n1: Optional[float] = None
    proposed_price_n1: Optional[float] = None
    current_price_n2: Optional[float] = None
    proposed_price_n2: Optional[float] = None
    current_price_n3: Optional[float] = None
    proposed_price_n3: Optional[float] = None

    # Quantities
    annual_quantity_n1: Optional[float] = None
    annual_quantity_n2: Optional[float] = None
    annual_quantity_n3: Optional[float] = None

    # Savings
    saving_year_n: Optional[float] = None
    saving_year_n1: Optional[float] = None
    saving_year_n2: Optional[float] = None
    saving_year_n3: Optional[float] = None
    period_saving: Optional[float] = None
    expected_annual_saving: Optional[float] = None

    # ROI & investment
    roi_percent: Optional[float] = None
    roi_period_percent: Optional[float] = None
    total_investment: Optional[float] = None
    tooling_cost: Optional[float] = None
    travel_cost: Optional[float] = None
    qualification_cost: Optional[float] = None
    other_cost: Optional[float] = None

    # Logistics
    incoterms_before: Optional[str] = None
    incoterms_after: Optional[str] = None
    top_days_before: Optional[float] = None
    top_days_after: Optional[float] = None

    # Planning
    planned_start_date: Optional[str] = None
    planned_end_date: Optional[str] = None
    duration_months: Optional[int] = None
