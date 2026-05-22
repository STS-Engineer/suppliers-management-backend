"""Supplier relation schemas."""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field
from app.features.suppliers.schemas import ClassCriterionDetail, EvaluationDetailsBase


class SupplierRelationSummaryResponse(BaseModel):
    id_relation: int
    id_site: int
    id_supplier_unit: int
    relation_code: Optional[str] = None
    unit_code: Optional[str] = None
    supplier_status: Optional[str] = None
    class_value: Optional[int] = None
    operational_grade: Optional[str] = None
    final_grade: Optional[str] = None
    strategic_mention: Optional[str] = None
    panel_decision: Optional[str] = None
    last_evaluation_date: Optional[date] = None
    next_evaluation_date: Optional[date] = None
    evaluation_comments: Optional[str] = None
    created_at: Optional[datetime] = None

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
