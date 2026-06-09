"""Purchasing value management schemas."""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Reference values (single source of truth — mirrors the spec)
# ---------------------------------------------------------------------------

OPPORTUNITY_TYPES = ["Negotiation", "Sourcing", "Technical Productivity", "Cash"]

OPPORTUNITY_STATUSES = [
    "Assigned",                  # just created, Phase 0 not yet started
    "Working on it",             # Phase 0 or Phase 1+ actively in progress
    "Awaiting Validation",       # Phase 0 submitted to PM for gate review
    "Under Committee Review",    # Phase 1 submitted to sourcing committee
    "Needs Rework",              # Gate decision = Review → sent back
    "Validated",                 # Phase 0 Go applied (internal transition)
    "Stuck",                     # blocked / no progress
    "Cancelled",                 # No Go decision
    "Complete",                  # Phase 4 closure
    "Customer Refusal",          # customer rejected the change (Standard change)
]

PHASE_STATUSES = [
    "Assigned",   # before Phase 0 study starts
    "Phase 0",    # opportunity study (Purchasing)
    "Phase 1",    # feasibility study (Project Manager)
    "Phase 2",    # execution
    "Phase 3",    # deployment
    "Phase 4",    # LLC / closure
    "Closed",
]

GATE_DECISIONS = ["Go", "No Go", "Review"]

BUDGET_STATUSES = ["Budgeted", "Outside Budget"]

CHANGE_MODES = ["Standard", "Silent"]

PRIORITY_CATEGORIES = ["High", "Medium", "Low"]

PROJECT_STATUSES = ["On time", "Late", "Completed", "On hold"]

FINANCIAL_LINE_STATUSES = ["Draft", "Active", "Completed", "Cancelled"]

# ---------------------------------------------------------------------------
# PLD helpers
# ---------------------------------------------------------------------------

def compute_priority(p: Optional[float], l: Optional[float], d: Optional[float]):
    """Returns (priority_score, priority_category) or (None, None)."""
    if p is None or l is None or d is None:
        return None, None
    score = float(p) * float(l) * float(d)
    if score >= 75:
        cat = "High"
    elif score >= 25:
        cat = "Medium"
    else:
        cat = "Low"
    return round(score, 2), cat


def auto_payback_score(total_investment: Optional[float], annual_saving: Optional[float]) -> Optional[int]:
    """
    P score — auto-calculated from payback in months.
    Formula (Olivier): payback_months = (investment / annual_saving) × 12
    Thresholds (Olivier transcript 04/06/2026):
      0 months → 1 (best — no investment)
      ≤2 months → 2
      ≤4 months → 3
      ≤12 months → 4  (covers 4–12 range)
      >12 months → 5 (worst)
    """
    if annual_saving is None or annual_saving <= 0:
        return None
    if total_investment is None or total_investment <= 0:
        return 1  # No investment → payback = 0 → best score
    payback_months = (total_investment / annual_saving) * 12
    if payback_months <= 0:
        return 1
    elif payback_months <= 2:
        return 2
    elif payback_months <= 4:
        return 3
    elif payback_months <= 12:
        return 4
    else:
        return 5


def auto_leadtime_score(total_weeks: Optional[float]) -> Optional[int]:
    """
    L score — auto-calculated from Phase 1+2+3 weeks ONLY (NOT Phase 4).
    Olivier: "durée phase 1, 2 et 3" — Phase 4 LLC is after production starts.
    Converted to months (weeks / 4.33).
    Thresholds: <1 month→1, <2→2, <4→3, <6→4, ≥6→5
    """
    if total_weeks is None or total_weeks <= 0:
        return None
    months = total_weeks / 4.33
    if months < 1:
        return 1
    elif months < 2:
        return 2
    elif months < 4:
        return 3
    elif months < 6:
        return 4
    else:
        return 5


DIFFICULTY_LABELS = {
    "Easy": 1,
    "Relatively easy": 2,
    "Moderately difficult": 3,
    "Difficult": 4,
    "Very Difficult": 5,
}


def add_months(d: date, months: int) -> date:
    """Add N calendar months to a date, always landing on day 1."""
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    return date(year, month, 1)


# ---------------------------------------------------------------------------
# Opportunity schemas
# ---------------------------------------------------------------------------

class OpportunityCreateRequest(BaseModel):
    opportunity_name: str = Field(..., min_length=1, max_length=500)
    opportunity_type: str = Field(..., description="Negotiation|Sourcing|Technical Productivity|Cash")
    idea_owner: str = Field(..., description="Email of the initial pilot (buyer)")
    description: Optional[str] = None
    plant_id: Optional[int] = None
    supplier_id: Optional[int] = None
    budget_year: Optional[int] = None
    budget_status: Optional[str] = Field(None, description="Budgeted | Outside Budget")


class OpportunityUpdateRequest(BaseModel):
    """Full Phase-0 editable payload — all fields optional."""
    opportunity_name: Optional[str] = Field(None, min_length=1, max_length=500)
    description: Optional[str] = None
    # Financial estimates
    expected_annual_saving: Optional[Decimal] = Field(None, ge=0)
    cash_impact: Optional[Decimal] = None
    duration_months: Optional[int] = Field(None, ge=1, le=120)
    # Dates — each has a specific phase (see field description)
    planned_start_date: Optional[date] = Field(None, description="Phase 0 plan — locked after Go")
    execution_start_date: Optional[date] = Field(None, description="Phase 2 — when execution work began")
    real_start_date: Optional[date] = Field(None, description="Phase 3 — when savings started flowing (triggers R9 profile rebuild)")
    # Contextual
    budget_status: Optional[str] = None
    budget_year: Optional[int] = None
    change_mode: Optional[str] = Field(None, description="Standard | Silent")
    assumptions_summary: Optional[str] = None
    comments: Optional[str] = None
    plant_id: Optional[int] = None
    supplier_id: Optional[int] = None
    # Owners
    purchasing_owner: Optional[str] = None
    conversion_owner: Optional[str] = None
    # D score only — P and L are auto-calculated
    # D = Easy(1) / Relatively easy(2) / Moderately difficult(3) / Difficult(4) / Very Difficult(5)
    difficulty_score: Optional[Decimal] = Field(None, ge=1, le=5)
    # STP fields
    scope_in: Optional[str] = None
    scope_out: Optional[str] = None
    customers: Optional[str] = None
    annual_quantity_n1: Optional[int] = None
    annual_quantity_n2: Optional[int] = None
    annual_quantity_n3: Optional[int] = None
    annual_quantity_n4: Optional[int] = None
    proposed_supplier_name: Optional[str] = None
    proposed_supplier_id: Optional[int] = None
    current_price: Optional[Decimal] = None
    proposed_price: Optional[Decimal] = None
    proposed_price_n1: Optional[Decimal] = None
    proposed_price_n2: Optional[Decimal] = None
    proposed_price_n3: Optional[Decimal] = None
    incoterms_before: Optional[str] = None
    incoterms_after: Optional[str] = None
    top_days_before: Optional[int] = None
    top_days_after: Optional[int] = None
    transit_days_before: Optional[int] = None
    transit_days_after: Optional[int] = None
    # country_before not stored — read from SupplierUnit.country
    country_after: Optional[str] = None
    bonus_before: Optional[Decimal] = None
    bonus_after: Optional[Decimal] = None
    supplier_asked: Optional[bool] = None
    supplier_asked_result: Optional[str] = None
    tooling_cost: Optional[Decimal] = None
    travel_cost: Optional[Decimal] = None
    qualification_cost: Optional[Decimal] = None
    phase1_weeks: Optional[int] = None
    phase2_weeks: Optional[int] = None
    phase3_weeks: Optional[int] = None
    phase4_weeks: Optional[int] = None
    reason_productivity: Optional[bool] = None
    reason_quality: Optional[bool] = None
    reason_capacity: Optional[bool] = None
    reason_other: Optional[str] = None
    changed_by: Optional[str] = None


class GateDecisionRequest(BaseModel):
    """Go / No Go / Review at any phase gate."""
    decision: str = Field(..., description="Go | No Go | Review")
    decided_by: Optional[str] = None
    comments: Optional[str] = None
    # Required on Go for Sourcing/Technical Productivity
    project_manager: Optional[str] = Field(None, description="PM email — required on Go for project-based types")


class ValidationRequestPayload(BaseModel):
    to_emails: List[str] = Field(..., min_length=1)
    extra_cc_emails: Optional[List[str]] = None
    custom_message: Optional[str] = None
    sent_by: Optional[str] = None


class StartStudyRequest(BaseModel):
    started_by: Optional[str] = None


class SubmitForValidationRequest(BaseModel):
    """Phase 0 → send to Purchasing Manager for gate review."""
    to_emails: List[str] = Field(..., min_length=1, description="Purchasing Manager email(s)")
    cc_emails: Optional[List[str]] = None
    message: Optional[str] = None
    submitted_by: Optional[str] = None


class SubmitToCommitteeRequest(BaseModel):
    """Phase 1 → submit feasibility dossier to Sourcing Committee.
    Olivier (04/06/2026): 'je veux pas d'email là — je veux que ce soit le purchasing manager
    qui organise une réunion.' → to_emails is optional, not mandatory.
    """
    to_emails: Optional[List[str]] = Field(None, description="Optional — committee email recipients")
    cc_emails: Optional[List[str]] = None
    committee_type: Optional[str] = Field(None, description="Full Committee | Restricted Committee")
    message: Optional[str] = None
    submitted_by: Optional[str] = None


# ---------------------------------------------------------------------------
# Project schemas
# ---------------------------------------------------------------------------

class ProjectResponse(BaseModel):
    project_id: int
    opportunity_id: Optional[int] = None
    project_name: Optional[str] = None
    project_type: Optional[str] = None
    project_owner: Optional[str] = None
    phase_status: Optional[str] = None
    gate_decision: Optional[str] = None
    status: Optional[str] = None
    planned_end_date: Optional[date] = None
    actual_end_date: Optional[date] = None
    plant_validation: Optional[str] = None
    comments: Optional[str] = None
    phase_output_notes: Optional[str] = None
    off_tool_date: Optional[date] = None
    committee_review_date: Optional[date] = None
    committee_members: Optional[str] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ProjectUpdateRequest(BaseModel):
    project_owner: Optional[str] = None
    status: Optional[str] = Field(None, description="On time | Late | Completed | On hold")
    plant_validation: Optional[str] = Field(None, description="Pending | Approved | Rejected")
    planned_end_date: Optional[date] = None
    actual_end_date: Optional[date] = None
    comments: Optional[str] = None
    phase_output_notes: Optional[str] = None
    off_tool_date: Optional[date] = None
    committee_review_date: Optional[date] = None
    committee_members: Optional[str] = None
    updated_by: Optional[str] = None


# ---------------------------------------------------------------------------
# Financial Line schemas
# ---------------------------------------------------------------------------

class MonthlyFinancialResponse(BaseModel):
    monthly_financial_id: int
    financial_line_id: int
    period_month: Optional[date] = None
    expected_saving: Optional[Decimal] = None
    actual_saving: Optional[Decimal] = None
    cumulated_expected: Optional[Decimal] = None
    cumulated_actual: Optional[Decimal] = None
    delta_vs_expected: Optional[Decimal] = None
    delta_vs_budget: Optional[Decimal] = None
    forecast_eoy_saving: Optional[Decimal] = None
    forecast_comment: Optional[str] = None
    comment: Optional[str] = None
    monthly_outcome: Optional[str] = None
    # Cash tracking (Gap 3)
    cash_expected: Optional[Decimal] = None
    cash_actual: Optional[Decimal] = None
    cumulated_cash_actual: Optional[Decimal] = None

    class Config:
        from_attributes = True


class MonthlyActualUpdateRequest(BaseModel):
    actual_saving: Optional[Decimal] = None
    cash_actual: Optional[Decimal] = Field(None, description="Actual cash saving this month (Negotiation/Cash types)")
    forecast_eoy_saving: Optional[Decimal] = None
    forecast_comment: Optional[str] = None
    comment: Optional[str] = None
    monthly_outcome: Optional[str] = Field(None, description="Continue | Recover | Escalate")
    updated_by: Optional[str] = None


class AddComponentLineRequest(BaseModel):
    """Add an additional FinancialLine for a specific component/part number."""
    component_name: str = Field(..., min_length=1, description="Component description or name")
    component_pn: Optional[str] = Field(None, description="Part number (PN)")
    expected_annual_saving: Decimal = Field(..., gt=0, description="Annual saving for this component (€)")
    planned_start_date: Optional[date] = None
    duration_months: Optional[int] = Field(12, ge=1, le=120)
    added_by: Optional[str] = None


class EscalateRequest(BaseModel):
    escalation_reason: str = Field(..., min_length=3)
    escalated_by: Optional[str] = None
    # Optional: email specific recipients beyond the purchasing_owner
    extra_recipients: Optional[List[str]] = None


class RecoveryUpdateRequest(BaseModel):
    recovery_status: str = Field(..., description="Planned | In Progress | Done")
    recovery_note: Optional[str] = None
    updated_by: Optional[str] = None


class FinancialLineCompleteRequest(BaseModel):
    completed_by: Optional[str] = None
    comments: Optional[str] = None


class FinancialLineReviseBaselineRequest(BaseModel):
    revised_saving: Decimal = Field(..., gt=0, description="New expected annual saving (€)")
    note: Optional[str] = Field(None, description="Reason for revision — required for audit")
    revised_by: Optional[str] = None


class FinancialLineResponse(BaseModel):
    financial_line_id: int
    opportunity_id: int
    project_id: Optional[int] = None
    plant_id: Optional[int] = None
    line_name: Optional[str] = None
    budget_status: Optional[str] = None
    expected_annual_saving: Optional[Decimal] = None
    budget_value: Optional[Decimal] = None
    planned_start_date: Optional[date] = None
    real_start_date: Optional[date] = None
    duration_months: Optional[Decimal] = None
    cumulated_real_saving: Optional[Decimal] = None
    delta_vs_expected_ytd: Optional[Decimal] = None
    delta_vs_budget_ytd: Optional[Decimal] = None
    status: Optional[str] = None
    follower: Optional[str] = None
    forecast_eoy_current: Optional[Decimal] = None
    forecast_eoy_last_update: Optional[date] = None
    comments: Optional[str] = None
    # Per-component (Gap 2)
    component_name: Optional[str] = None
    component_pn: Optional[str] = None
    # Escalation
    is_escalated: Optional[bool] = None
    escalated_at: Optional[datetime] = None
    escalated_by: Optional[str] = None
    escalation_reason: Optional[str] = None
    # Recovery
    recovery_status: Optional[str] = None
    recovery_note: Optional[str] = None
    recovery_updated_at: Optional[datetime] = None
    monthly_financials: List[MonthlyFinancialResponse] = Field(default_factory=list)

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Opportunity response
# ---------------------------------------------------------------------------

class OpportunityResponse(BaseModel):
    opportunity_id: int
    opportunity_name: Optional[str] = None
    opportunity_type: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    phase_status: Optional[str] = None
    idea_owner: Optional[str] = None
    purchasing_owner: Optional[str] = None
    project_owner: Optional[str] = None
    conversion_owner: Optional[str] = None
    plant_id: Optional[int] = None
    plant_name: Optional[str] = None
    plant_city: Optional[str] = None
    supplier_id: Optional[int] = None
    expected_annual_saving: Optional[Decimal] = None
    cash_impact: Optional[Decimal] = None
    planned_start_date: Optional[date] = None
    real_start_date: Optional[date] = None
    duration_months: Optional[Decimal] = None
    budget_status: Optional[str] = None
    budget_year: Optional[Decimal] = None
    phase_status: Optional[str] = None
    validation_decision: Optional[str] = None
    val_date: Optional[date] = None
    study_start_date: Optional[date] = None  # when buyer clicked "Start Study"
    execution_start_date: Optional[date] = None
    change_mode: Optional[str] = None
    assumptions_summary: Optional[str] = None
    payback_score: Optional[Decimal] = None
    lead_time_score: Optional[Decimal] = None
    difficulty_score: Optional[Decimal] = None
    priority_score: Optional[Decimal] = None
    priority_category: Optional[str] = None
    comments: Optional[str] = None
    validation_request_sent_at: Optional[datetime] = None
    # STP fields
    scope_in: Optional[str] = None
    scope_out: Optional[str] = None
    customers: Optional[str] = None
    annual_quantity_n1: Optional[int] = None
    annual_quantity_n2: Optional[int] = None
    annual_quantity_n3: Optional[int] = None
    annual_quantity_n4: Optional[int] = None
    proposed_supplier_name: Optional[str] = None
    proposed_supplier_id: Optional[int] = None
    current_price: Optional[Decimal] = None
    proposed_price: Optional[Decimal] = None
    proposed_price_n1: Optional[Decimal] = None
    proposed_price_n2: Optional[Decimal] = None
    proposed_price_n3: Optional[Decimal] = None
    incoterms_before: Optional[str] = None
    incoterms_after: Optional[str] = None
    top_days_before: Optional[int] = None
    top_days_after: Optional[int] = None
    transit_days_before: Optional[int] = None
    transit_days_after: Optional[int] = None
    # country_before not stored — read from SupplierUnit.country
    country_after: Optional[str] = None
    bonus_before: Optional[Decimal] = None
    bonus_after: Optional[Decimal] = None
    supplier_asked: Optional[bool] = None
    supplier_asked_result: Optional[str] = None
    tooling_cost: Optional[Decimal] = None
    travel_cost: Optional[Decimal] = None
    qualification_cost: Optional[Decimal] = None
    total_investment: Optional[Decimal] = None
    roi_percent: Optional[Decimal] = None
    cash_inventory_gap: Optional[Decimal] = None
    cash_ap_gap: Optional[Decimal] = None
    phase1_weeks: Optional[int] = None
    phase2_weeks: Optional[int] = None
    phase3_weeks: Optional[int] = None
    phase4_weeks: Optional[int] = None
    reason_productivity: Optional[bool] = None
    reason_quality: Optional[bool] = None
    reason_capacity: Optional[bool] = None
    reason_other: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    projects: List[ProjectResponse] = Field(default_factory=list)
    financial_lines: List[FinancialLineResponse] = Field(default_factory=list)
    opp_documents: List["OpportunityDocumentResponse"] = Field(default_factory=list)

    class Config:
        from_attributes = True


class OpportunityListResponse(BaseModel):
    items: List[OpportunityResponse]
    total: int


def opportunity_to_response(opp) -> OpportunityResponse:
    """Convert an ORM Opportunity (with plant eagerly loaded) to the response schema."""
    data = OpportunityResponse.model_validate(opp)
    if opp.plant is not None:
        data.plant_name = opp.plant.site_name
        data.plant_city = opp.plant.city
    return data


# ---------------------------------------------------------------------------
# Document schemas
# ---------------------------------------------------------------------------

class OpportunityDocumentResponse(BaseModel):
    doc_id: int
    opportunity_id: int
    phase_label: Optional[str] = None
    file_name: Optional[str] = None
    original_file_name: Optional[str] = None
    file_url: Optional[str] = None
    mime_type: Optional[str] = None
    file_size: Optional[int] = None
    uploaded_by: Optional[str] = None
    notes: Optional[str] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Supplier-by-plant response
# ---------------------------------------------------------------------------

class SupplierOption(BaseModel):
    id_supplier_unit: int
    supplier_code: Optional[str] = None
    group_name: Optional[str] = None   # SupplierGroup.nom
    city: Optional[str] = None
    country: Optional[str] = None

    class Config:
        from_attributes = True
