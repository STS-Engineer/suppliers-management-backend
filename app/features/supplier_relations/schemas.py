"""Supplier relation schemas."""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field, computed_field
from app.features.suppliers.schemas import ClassCriterionDetail, EvaluationDetailsBase
from app.core.constants import EVAL_FREQUENCY_DAYS


class SupplierRelationSummaryResponse(BaseModel):
    id_relation: int
    id_site: int
    id_supplier_unit: int
    relation_code: Optional[str] = None
    unit_code: Optional[str] = None
    supplier_owner: Optional[str] = None
    supplier_status: Optional[str] = None
    class_value: Optional[int] = None
    operational_grade: Optional[str] = None
    final_grade: Optional[str] = None
    strategic_mention: Optional[str] = None
    panel_decision: Optional[str] = None
    last_evaluation_date: Optional[date] = None
    next_evaluation_date: Optional[date] = None
    evaluation_frequency: Optional[str] = None
    evaluation_comments: Optional[str] = None
    created_at: Optional[datetime] = None
    global_status: Optional[str] = None
    supplier_scope: Optional[str] = None
    is_active: bool = True

    @computed_field
    @property
    def is_overdue_for_evaluation(self) -> Optional[bool]:
        if not self.last_evaluation_date or not self.evaluation_frequency:
            return None
        days = EVAL_FREQUENCY_DAYS.get(self.evaluation_frequency)
        if days is None:
            return None
        return (date.today() - self.last_evaluation_date).days > days

    class Config:
        from_attributes = True


class SupplierStatusHistoryResponse(BaseModel):
    id_history: int
    old_status: Optional[str] = None
    new_status: Optional[str] = None
    old_class: Optional[int] = None
    new_class: Optional[int] = None
    old_grade: Optional[str] = None
    new_grade: Optional[str] = None
    old_final_grade: Optional[str] = None
    new_final_grade: Optional[str] = None
    old_strategic_mention: Optional[str] = None
    new_strategic_mention: Optional[str] = None
    old_panel_decision: Optional[str] = None
    new_panel_decision: Optional[str] = None
    change_reason: Optional[str] = None
    changed_by: Optional[str] = None
    changed_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class SupplierStatusOverridePayload(BaseModel):
    active: bool = False
    status: Optional[str] = None
    reason: Optional[str] = None
    changed_by: Optional[str] = None
    changed_at: Optional[datetime] = None
    computed_status: Optional[str] = None


class SupplierDevelopmentPlanBase(BaseModel):
    plan_title: Optional[str] = Field(None, max_length=255)
    plan_status: Optional[str] = Field(None, max_length=100)
    issue_date: Optional[date] = None
    due_date: Optional[date] = None
    submission_date: Optional[date] = None
    review_date: Optional[date] = None
    decision_date: Optional[date] = None
    reviewed_by: Optional[str] = Field(None, max_length=200)
    approved_by: Optional[str] = Field(None, max_length=200)
    rejected_by: Optional[str] = Field(None, max_length=200)
    business_hold_active: Optional[bool] = None
    escalated: Optional[bool] = None
    escalation_date: Optional[date] = None
    file_name: Optional[str] = Field(None, max_length=255)
    file_url: Optional[str] = Field(None, max_length=1000)
    file_notes: Optional[str] = None
    supplier_comments: Optional[str] = None
    internal_comments: Optional[str] = None
    # Development Plans (SB22) fields
    decision: Optional[str] = Field(None, max_length=255, description="Final decision on development plan")
    commodity: Optional[str] = Field(None, max_length=200, description="Commodity linked to development plan")
    plant: Optional[str] = Field(None, max_length=100, description="Avocarbon plant linked to development plan")


class SupplierDevelopmentPlanCreateRequest(SupplierDevelopmentPlanBase):
    sync_relation_hold_status: bool = True
    changed_by: Optional[str] = None


class SupplierDevelopmentPlanUpdateRequest(SupplierDevelopmentPlanBase):
    sync_relation_hold_status: bool = True
    changed_by: Optional[str] = None


class SupplierDevelopmentPlanSendRequest(BaseModel):
    changed_by: Optional[str] = None
    custom_message: Optional[str] = None
    to_emails: Optional[list[str]] = None
    extra_cc_emails: Optional[list[str]] = None


class SupplierDevelopmentPlanSendReminder(BaseModel):
    changed_by: Optional[str] = None
    custom_message: Optional[str] = None
    to_emails: Optional[list[str]] = None
    extra_cc_emails: Optional[list[str]] = None


class SupplierDevelopmentPlanRevisionRequest(BaseModel):
    changed_by: Optional[str] = None
    custom_message: Optional[str] = None
    to_emails: Optional[list[str]] = None
    extra_cc_emails: Optional[list[str]] = None


class SupplierDevelopmentPlanDecisionNotification(BaseModel):
    decision: str = Field(..., pattern="^(approved|rejected)$")
    changed_by: Optional[str] = None
    custom_message: Optional[str] = None
    to_emails: Optional[list[str]] = None
    extra_cc_emails: Optional[list[str]] = None


class SupplierDevelopmentPlanReviewNotificationRequest(BaseModel):
    changed_by: Optional[str] = None
    custom_message: Optional[str] = None
    to_emails: list[str] = Field(..., min_length=1)
    extra_cc_emails: Optional[list[str]] = None
    review_deadline: Optional[date] = None


class SupplierDevelopmentPlanReceivedNotificationRequest(BaseModel):
    changed_by: Optional[str] = None
    custom_message: Optional[str] = None
    to_emails: list[str] = Field(..., min_length=1)
    extra_cc_emails: Optional[list[str]] = None


class PlanDocumentResponse(BaseModel):
    id_document: int
    file_name: Optional[str] = None
    file_url: Optional[str] = None
    file_notes: Optional[str] = None
    uploaded_at: Optional[datetime] = None
    comments: Optional[str] = None

    class Config:
        from_attributes = True


class SupplierDevelopmentPlanResponse(SupplierDevelopmentPlanBase):
    id_development_plan: int
    id_relation: int
    id_document: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    is_overdue: bool = False
    days_past_due: Optional[int] = None

    class Config:
        from_attributes = True


class ClassEvaluationUpdateRequest(BaseModel):
    evaluation_date: Optional[date] = None
    class_criteria_details: dict[str, ClassCriterionDetail] = Field(default_factory=dict)
    top: Optional[str] = None
    lta: Optional[str] = None
    productivity: Optional[str] = None
    quality_certification: Optional[str] = None
    prod_lia_ins: Optional[str] = None
    competitiveness: Optional[str] = None
    sqma: Optional[str] = None
    family_coverage: Optional[str] = None
    geo_coverage: Optional[str] = None
    cons_or_wd: Optional[str] = None
    financial_health: Optional[str] = None
    impact_question_1: Optional[str] = None
    impact_question_2: Optional[str] = None
    impact_question_3: Optional[str] = None
    impact_question_4: Optional[str] = None
    impact_question_5: Optional[str] = None
    impact_question_6: Optional[str] = None
    class_score: Optional[Decimal] = Field(None, ge=0, le=100)
    class_value: Optional[int] = Field(None, ge=1, le=4)
    impact_score: Optional[int] = Field(None, ge=-30, le=30)
    strategic_mention: Optional[str] = None
    panel_decision: Optional[str] = None
    comments: Optional[str] = None
    cycle_type: str = Field(default="Class Reassessment")
    changed_by: Optional[str] = None


class OperationalEvaluationUpdateRequest(BaseModel):
    evaluation_date: Optional[date] = None
    management_system: Optional[Decimal] = Field(None, ge=0, le=100)
    customer_communication: Optional[Decimal] = Field(None, ge=0, le=100)
    development_design: Optional[Decimal] = Field(None, ge=0, le=100)
    production_manufacturing: Optional[Decimal] = Field(None, ge=0, le=100)
    quality_audits: Optional[Decimal] = Field(None, ge=0, le=100)
    suppliers_subcontractors: Optional[Decimal] = Field(None, ge=0, le=100)
    deliveries: Optional[Decimal] = Field(None, ge=0, le=100)
    environment_ethic_rules: Optional[Decimal] = Field(None, ge=0, le=100)
    operational_score: Optional[Decimal] = Field(None, ge=0, le=100)
    operational_grade: Optional[str] = Field(None, max_length=1)
    comments: Optional[str] = None
    source_type: str = Field(default="kpi", description="kpi or self_assessment")
    cycle_type: Optional[str] = Field(
        default=None,
        description="Optional explicit cycle type override",
    )
    changed_by: Optional[str] = None


class SupplierStatusOverrideRequest(BaseModel):
    supplier_status: str = Field(..., min_length=1, max_length=100)
    reason: str = Field(..., min_length=3)
    override_date: Optional[datetime] = None
    changed_by: Optional[str] = None


class EvaluationUpdateResponse(BaseModel):
    relation: SupplierRelationSummaryResponse
    cycle_id: Optional[int] = None
    score_card_id: Optional[int] = None
    classification_id: Optional[int] = None
    status_history_id: Optional[int] = None
    message: str


class EvaluationCriterionDocumentUploadResponse(BaseModel):
    relation_id: int
    criteria_type: str
    document_id: int
    document_name: str
    original_file_name: Optional[str] = None
    file_url: Optional[str] = None
    mime_type: Optional[str] = None
    file_size: Optional[Decimal] = None
    uploaded_at: Optional[datetime] = None


class EvaluationCriterionDocumentDeleteResponse(BaseModel):
    relation_id: int
    criteria_type: str
    deleted_document_id: Optional[int] = None


class DevelopmentPlanDocumentUploadResponse(BaseModel):
    relation_id: int
    plan_id: int
    document_id: int
    document_name: str
    original_file_name: Optional[str] = None
    file_url: Optional[str] = None
    mime_type: Optional[str] = None
    file_size: Optional[Decimal] = None
    uploaded_at: Optional[datetime] = None


class DevelopmentPlanRegisterRowResponse(BaseModel):
    relation: SupplierRelationSummaryResponse
    development_plan: SupplierDevelopmentPlanResponse
    site_name: Optional[str] = None
    site_city: Optional[str] = None
    site_country: Optional[str] = None
    unit_supplier_code: Optional[str] = None
    unit_code: Optional[str] = None
    group_id: Optional[int] = None
    group_name: Optional[str] = None
    group_code: Optional[str] = None


class InitialRelationEvaluationRequest(EvaluationDetailsBase):
    evaluation_date: Optional[date] = None
    changed_by: Optional[str] = None


class InitialRelationEvaluationResponse(BaseModel):
    relation: SupplierRelationSummaryResponse
    cycle_id: int
    score_card_id: Optional[int] = None
    classification_id: Optional[int] = None
    status_history_id: Optional[int] = None
    message: str


class RelationEvaluationWorkspaceResponse(EvaluationDetailsBase):
    relation: SupplierRelationSummaryResponse
    evaluation_date: Optional[date] = None
    status_history: list[SupplierStatusHistoryResponse] = Field(default_factory=list)
    computed_supplier_status: Optional[str] = None
    effective_supplier_status: Optional[str] = None
    status_override: Optional[SupplierStatusOverridePayload] = None
    development_plans: list[SupplierDevelopmentPlanResponse] = Field(default_factory=list)
    # Extended workspace fields
    unit_supplier_code: Optional[str] = None
    unit_is_active: bool = True
    unit_inactivated_at: Optional[str] = None
    reevaluation_type: Optional[str] = None  # "initial" | "preliminary" | None
    baseline_locked: bool = False
    baseline_data: Optional[dict] = None
    unit_certifications: list[dict] = Field(default_factory=list)
    evaluation_documents: list[dict] = Field(default_factory=list)
    criteria_scores: dict = Field(default_factory=dict)
    evaluation_draft: Optional[dict] = None
    relation_validation_status: Optional[str] = None
    review_comment: Optional[str] = None


# ---------------------------------------------------------------------------
# Spend by year
# ---------------------------------------------------------------------------

class SpendByYearCreate(BaseModel):
    fiscal_year: int = Field(..., ge=2000, le=2100, description="Fiscal year (e.g. 2025)")
    spend_value: Decimal = Field(..., ge=0, description="Annual spend amount")
    spend_currency: str = Field(default="EUR", max_length=10)


class SpendByYearUpsertBody(BaseModel):
    """Body for PUT /{relation_id}/spend/{fiscal_year} — fiscal_year comes from the URL."""
    spend_value: Decimal = Field(..., ge=0, description="Annual spend amount")
    spend_currency: str = Field(default="EUR", max_length=10)


class SpendByYearResponse(BaseModel):
    id_spend: int
    id_relation: int
    fiscal_year: int
    spend_value: Decimal
    spend_currency: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None

    class Config:
        from_attributes = True
