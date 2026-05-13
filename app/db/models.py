"""
app/db/models.py — Shared ORM Foundation
==========================================
Base classes, mixins, and enumerations shared across all features.

Feature-specific models are located in:
  app/features/{feature}/models.py

This module only exports the shared base infrastructure.
"""

from __future__ import annotations

import enum
from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List

from sqlalchemy import DateTime, String, Text, func, Integer, Boolean, ForeignKey, Numeric, CHAR, Index, UniqueConstraint, Date
from sqlalchemy.orm import  Mapped, mapped_column, relationship
from app.db.session import Base

# ---------------------------------------------------------------------------
# Base & Mixins
# ---------------------------------------------------------------------------

class TimestampMixin:
    """Adds created_at with server-side default."""
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=True
    )


class CycleDocumentMixin:
    """
    Common trio (id_cycle, id_document, id_relation) found on most
    transactional tables. FK constraints are declared on each concrete
    model because cascade rules differ.
    """
    # Forward references to be resolved in feature modules
    pass


class AuditMixin:
    """Light audit trail: who entered / when."""
    entered_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    entered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=True
    )
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# Enumerations  (extend freely — add values without touching model columns)
# ---------------------------------------------------------------------------


class DecisionStatus(str, enum.Enum):
    PENDING = "Pending"
    APPROVED = "Approved"
    REJECTED = "Rejected"


class CycleStatus(str, enum.Enum):
    DRAFT = "Draft"
    IN_PROGRESS = "In Progress"
    COMPLETED = "Completed"
    CANCELLED = "Cancelled"


class DocumentStatus(str, enum.Enum):
    UPLOADED = "Uploaded"
    VALIDATED = "Validated"
    REJECTED = "Rejected"
    ARCHIVED = "Archived"


class EscalationStatus(str, enum.Enum):
    OPEN = "Open"
    IN_PROGRESS = "In Progress"
    RESOLVED = "Resolved"
    CLOSED = "Closed"


class ValidationStatus(str, enum.Enum):
    PENDING = "Pending"
    APPROVED = "Approved"
    REJECTED = "Rejected"


class AssessmentStatus(str, enum.Enum):
    RECEIVED = "Received"
    IN_REVIEW = "In Review"
    COMPLETED = "Completed"
    REJECTED = "Rejected"


class TemplateType(str, enum.Enum):
    SELF_ASSESSMENT = "SELF_ASSESSMENT"
    AUDIT = "AUDIT"
    SURVEY = "SURVEY"


__all__ = [
    "Base",
    "TimestampMixin",
    "CycleDocumentMixin",
    "AuditMixin",
    "DecisionStatus",
    "CycleStatus",
    "DocumentStatus",
    "EscalationStatus",
    "ValidationStatus",
    "AssessmentStatus",
    "TemplateType",
]


class AvocarbonSite(Base):
    """
    Avocarbon manufacturing / purchasing site.
    Top-level geographic entity; suppliers are linked through
    supplier_site_relation.
    """
    __tablename__ = "avocarbon_site"

    id_site: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    site_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    address_line: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    active: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    # Relationships
    supplier_relations: Mapped[List["SupplierSiteRelation"]] = relationship(
        back_populates="site", cascade="all, delete-orphan", passive_deletes=True
    )
    contacts: Mapped[List["Contact"]] = relationship(
        back_populates="site", cascade="all, delete-orphan", passive_deletes=True
    )
    financial_lines: Mapped[List["FinancialLine"]] = relationship(
        foreign_keys="FinancialLine.plant_id",
        back_populates="plant",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    opportunities: Mapped[List["Opportunity"]] = relationship(
        back_populates="plant", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<AvocarbonSite id={self.id_site} name={self.site_name!r}>"


class SupplierGroup(Base):
    """
    Top-level supplier entity — the commercial/legal group.
    One group can own many supplier units (plants/entities).
    """
    __tablename__ = "supplier_group"

    id_group: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nom: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    supplier_scope: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    strategique: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    monopolistique: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    multi_site: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    directed: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    exit_supplier: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    strategic_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    supplier_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Relationships
    units: Mapped[List["SupplierUnit"]] = relationship(
        back_populates="group", cascade="all, delete-orphan", passive_deletes=True
    )
    contacts: Mapped[List["Contact"]] = relationship(
        back_populates="supplier_group", cascade="all, delete-orphan", passive_deletes=True
    )
    documents: Mapped[List["Document"]] = relationship(
        back_populates="group",
        foreign_keys="Document.id_group",
    )

    def __repr__(self) -> str:
        return f"<SupplierGroup id={self.id_group} nom={self.nom!r}>"


class SupplierUnit(TimestampMixin, Base):
    """
    A specific legal entity / manufacturing unit within a supplier group.
    Linked to Avocarbon sites through supplier_site_relation.
    """
    __tablename__ = "supplier_unit"

    id_supplier_unit: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_group: Mapped[Optional[int]] = mapped_column(
        ForeignKey("supplier_group.id_group", ondelete="CASCADE"), nullable=True
    )
    supplier_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    address_line: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    product_type: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    product_category: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    amount_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    amount_currency: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)

    # Relationships
    group: Mapped[Optional["SupplierGroup"]] = relationship(back_populates="units")
    site_relations: Mapped[List["SupplierSiteRelation"]] = relationship(
        back_populates="supplier_unit", cascade="all, delete-orphan", passive_deletes=True
    )
    certifications: Mapped[List["SupplierCertification"]] = relationship(
        back_populates="supplier_unit", cascade="all, delete-orphan", passive_deletes=True
    )
    contacts: Mapped[List["Contact"]] = relationship(
        back_populates="supplier_unit", cascade="all, delete-orphan", passive_deletes=True
    )
    documents: Mapped[List["Document"]] = relationship(
        back_populates="supplier_unit",
        foreign_keys="Document.id_supplier_unit",
    )
    opportunities: Mapped[List["Opportunity"]] = relationship(
        back_populates="supplier", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<SupplierUnit id={self.id_supplier_unit} code={self.supplier_code!r}>"


class SupplierSiteRelation(Base):
    """
    Enriched M2M join between SupplierUnit and AvocarbonSite.
    This is the *central pivot* of the entire schema — almost every
    transactional table (cycles, scorecards, assessments, documents …)
    carries id_relation pointing here.
    """
    __tablename__ = "supplier_site_relation"

    id_relation: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_site: Mapped[int] = mapped_column(
        ForeignKey("avocarbon_site.id_site", ondelete="CASCADE"), nullable=False
    )
    id_supplier_unit: Mapped[int] = mapped_column(
        ForeignKey("supplier_unit.id_supplier_unit", ondelete="CASCADE"), nullable=False
    )
    alias_1: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    buyer_owner: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    supplier_status: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    operational_grade: Mapped[Optional[str]] = mapped_column(CHAR(1), nullable=True)
    class_value: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    global_status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    evaluation_frequency: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    last_evaluation_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    next_evaluation_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Relationships
    site: Mapped["AvocarbonSite"] = relationship(back_populates="supplier_relations")
    supplier_unit: Mapped["SupplierUnit"] = relationship(back_populates="site_relations")

    evaluation_cycles: Mapped[List["EvaluationCycle"]] = relationship(
        back_populates="relation", cascade="all, delete-orphan", passive_deletes=True
    )
    score_cards: Mapped[List["ScoreCard"]] = relationship(
        back_populates="relation", cascade="all, delete-orphan", passive_deletes=True
    )
    classifications: Mapped[List["Classification"]] = relationship(
        back_populates="relation", cascade="all, delete-orphan", passive_deletes=True
    )
    status_history: Mapped[List["SupplierStatusHistory"]] = relationship(
        back_populates="relation", cascade="all, delete-orphan", passive_deletes=True
    )
    agreements: Mapped[List["SupplierAgreement"]] = relationship(
        back_populates="relation", cascade="all, delete-orphan", passive_deletes=True
    )
    documents: Mapped[List["Document"]] = relationship(
        back_populates="relation",
        foreign_keys="Document.id_relation",
    )
    contacts_via_junction: Mapped[List["ContactSiteRelation"]] = relationship(
        back_populates="relation", cascade="all, delete-orphan", passive_deletes=True
    )
    assessments: Mapped[List["SupplierAssessment"]] = relationship(
        back_populates="relation"
    )
    input_otd: Mapped[List["InputOtdMonthly"]] = relationship(back_populates="relation")
    input_quality_claims: Mapped[List["InputQualityClaims"]] = relationship(
        back_populates="relation"
    )
    input_delivery_spend: Mapped[List["InputDeliverySpend"]] = relationship(
        back_populates="relation"
    )
    scorecard_kpi_details: Mapped[List["ScorecardKpiDetail"]] = relationship(
        back_populates="relation"
    )

    def __repr__(self) -> str:
        return (
            f"<SupplierSiteRelation id={self.id_relation} "
            f"site={self.id_site} unit={self.id_supplier_unit}>"
        )


class SupplierStatusHistory(TimestampMixin, Base):
    """Immutable audit log of status / grade / class changes on a relation."""
    __tablename__ = "supplier_status_history"

    id_history: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_relation: Mapped[int] = mapped_column(
        ForeignKey("supplier_site_relation.id_relation", ondelete="CASCADE"), nullable=False
    )
    old_status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    new_status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    old_class: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    new_class: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    old_grade: Mapped[Optional[str]] = mapped_column(CHAR(1), nullable=True)
    new_grade: Mapped[Optional[str]] = mapped_column(CHAR(1), nullable=True)
    change_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    changed_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    changed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=True
    )

    # Relationships
    relation: Mapped["SupplierSiteRelation"] = relationship(back_populates="status_history")


class SupplierCertification(Base):
    """Certifications (ISO, IATF …) held by a supplier unit."""
    __tablename__ = "supplier_certification"

    id_certification: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_supplier_unit: Mapped[Optional[int]] = mapped_column(
        ForeignKey("supplier_unit.id_supplier_unit", ondelete="CASCADE"), nullable=True
    )
    certification_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    certificate_name: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    amount_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    amount_currency: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    expiry_mode: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    supplier_unit: Mapped[Optional["SupplierUnit"]] = relationship(
        back_populates="certifications"
    )


class SupplierAgreement(Base):
    """Commercial / frame agreements attached to a supplier-site relation."""
    __tablename__ = "supplier_agreement"

    id_agreement: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_relation: Mapped[Optional[int]] = mapped_column(
        ForeignKey("supplier_site_relation.id_relation", ondelete="CASCADE"), nullable=True
    )
    agreement_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    agreement_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    amount_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    amount_currency: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    location_value: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Note: value_unit column comment says "currency" — kept as-is
    value_unit: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    agreement_description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    agreement_value: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)

    # Relationships
    relation: Mapped[Optional["SupplierSiteRelation"]] = relationship(
        back_populates="agreements"
    )


# ---------------------------------------------------------------------------
# Domain 1 — Contacts
# ---------------------------------------------------------------------------


class Contact(TimestampMixin, Base):
    """
    Person attached to a supplier group, unit, or Avocarbon site.
    The three FK columns are mutually exclusive in practice.
    """
    __tablename__ = "contact"

    id_contact: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role_label: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    role_name: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    full_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    is_primary_contact: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    id_supplier_group: Mapped[Optional[int]] = mapped_column(
        ForeignKey("supplier_group.id_group", ondelete="CASCADE"), nullable=True
    )
    id_supplier_unit: Mapped[Optional[int]] = mapped_column(
        ForeignKey("supplier_unit.id_supplier_unit", ondelete="CASCADE"), nullable=True
    )
    id_site: Mapped[Optional[int]] = mapped_column(
        ForeignKey("avocarbon_site.id_site", ondelete="CASCADE"), nullable=True
    )

    # Relationships
    supplier_group: Mapped[Optional["SupplierGroup"]] = relationship(
        back_populates="contacts", foreign_keys=[id_supplier_group]
    )
    supplier_unit: Mapped[Optional["SupplierUnit"]] = relationship(
        back_populates="contacts", foreign_keys=[id_supplier_unit]
    )
    site: Mapped[Optional["AvocarbonSite"]] = relationship(
        back_populates="contacts", foreign_keys=[id_site]
    )
    site_relations: Mapped[List["ContactSiteRelation"]] = relationship(
        back_populates="contact", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<Contact id={self.id_contact} name={self.full_name!r}>"


class ContactSiteRelation(Base):
    """Junction linking a Contact to a SupplierSiteRelation."""
    __tablename__ = "contact_site_relation"
    __table_args__ = (
        UniqueConstraint("id_contact", "id_relation", name="contact_site_relation_id_contact_id_relation_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_contact: Mapped[int] = mapped_column(
        ForeignKey("contact.id_contact", ondelete="CASCADE"), nullable=False
    )
    id_relation: Mapped[int] = mapped_column(
        ForeignKey("supplier_site_relation.id_relation", ondelete="CASCADE"), nullable=False
    )

    # Relationships
    contact: Mapped["Contact"] = relationship(back_populates="site_relations")
    relation: Mapped["SupplierSiteRelation"] = relationship(
        back_populates="contacts_via_junction"
    )


# ---------------------------------------------------------------------------
# Domain 1 — Documents
# ---------------------------------------------------------------------------


class Document(TimestampMixin, Base):
    """
    Uploaded file attached to a relation, supplier unit, or group.
    Acts as a document registry for the whole platform.
    """
    __tablename__ = "document"
    __table_args__ = (
        Index("idx_document_relation", "id_relation"),
    )

    id_document: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_relation: Mapped[Optional[int]] = mapped_column(
        ForeignKey("supplier_site_relation.id_relation", ondelete="SET NULL"), nullable=True
    )
    id_supplier_unit: Mapped[Optional[int]] = mapped_column(
        ForeignKey("supplier_unit.id_supplier_unit", ondelete="SET NULL"), nullable=True
    )
    id_group: Mapped[Optional[int]] = mapped_column(
        ForeignKey("supplier_group.id_group", ondelete="SET NULL"), nullable=True
    )
    document_type: Mapped[str] = mapped_column(String(100), nullable=False)
    document_name: Mapped[str] = mapped_column(String(255), nullable=False)
    original_file_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    file_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    file_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    mime_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    file_size: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    uploaded_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    uploaded_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=True
    )
    period_start: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    period_end: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    version: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(
        String(50), server_default="'Uploaded'", nullable=False
    )
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    relation: Mapped[Optional["SupplierSiteRelation"]] = relationship(
        back_populates="documents", foreign_keys=[id_relation]
    )
    supplier_unit: Mapped[Optional["SupplierUnit"]] = relationship(
        back_populates="documents", foreign_keys=[id_supplier_unit]
    )
    group: Mapped[Optional["SupplierGroup"]] = relationship(
        back_populates="documents", foreign_keys=[id_group]
    )
    # Back-references for tables that reference documents (SET NULL — no cascade)
    assessment_templates: Mapped[List["AssessmentTemplate"]] = relationship(
        back_populates="document"
    )
    assessments: Mapped[List["SupplierAssessment"]] = relationship(
        back_populates="document"
    )

    def __repr__(self) -> str:
        return f"<Document id={self.id_document} name={self.document_name!r}>"


# ---------------------------------------------------------------------------
# Domain 2 — Evaluation & Scorecard Pipeline
# ---------------------------------------------------------------------------


class EvaluationCycle(TimestampMixin, Base):
    """
    A time-boxed evaluation period for a supplier-site relation.
    Acts as the container for scorecard, KPI inputs, approvals, etc.
    """
    __tablename__ = "evaluation_cycle"
    __table_args__ = (
        Index("idx_evaluation_cycle_relation", "id_relation"),
    )

    id_cycle: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_relation: Mapped[int] = mapped_column(
        ForeignKey("supplier_site_relation.id_relation", ondelete="CASCADE"), nullable=False
    )
    cycle_type: Mapped[str] = mapped_column(String(100), nullable=False)
    supplier_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    frequency: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    period_start: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    period_end: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    cycle_status: Mapped[str] = mapped_column(
        String(50), server_default="'Draft'", nullable=False
    )
    launched_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    launched_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    relation: Mapped["SupplierSiteRelation"] = relationship(
        back_populates="evaluation_cycles"
    )
    approvals: Mapped[List["ApprovalWorkflow"]] = relationship(
        back_populates="cycle", cascade="all, delete-orphan", passive_deletes=True
    )
    escalations: Mapped[List["Escalation"]] = relationship(
        back_populates="cycle", cascade="all, delete-orphan", passive_deletes=True
    )
    score_cards: Mapped[List["ScoreCard"]] = relationship(back_populates="cycle")
    classifications: Mapped[List["Classification"]] = relationship(back_populates="cycle")
    kpi_details: Mapped[List["ScorecardKpiDetail"]] = relationship(back_populates="cycle")
    upload_registers: Mapped[List["ScorecardUploadRegister"]] = relationship(
        back_populates="cycle"
    )
    data_quality_checks: Mapped[List["ScorecardDataQualityCheck"]] = relationship(
        back_populates="cycle"
    )
    input_otd: Mapped[List["InputOtdMonthly"]] = relationship(back_populates="cycle")
    input_quality_claims: Mapped[List["InputQualityClaims"]] = relationship(
        back_populates="cycle"
    )
    input_delivery_spend: Mapped[List["InputDeliverySpend"]] = relationship(
        back_populates="cycle"
    )
    assessments: Mapped[List["SupplierAssessment"]] = relationship(back_populates="cycle")

    def __repr__(self) -> str:
        return (
            f"<EvaluationCycle id={self.id_cycle} "
            f"type={self.cycle_type!r} status={self.cycle_status!r}>"
        )


class ApprovalWorkflow(TimestampMixin, Base):
    """Step-based approval chain for an evaluation cycle object."""
    __tablename__ = "approval_workflow"
    __table_args__ = (
        Index("idx_approval_cycle", "id_cycle"),
    )

    id_approval: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_cycle: Mapped[Optional[int]] = mapped_column(
        ForeignKey("evaluation_cycle.id_cycle", ondelete="CASCADE"), nullable=True
    )
    object_type: Mapped[str] = mapped_column(String(100), nullable=False)
    object_id: Mapped[int] = mapped_column(Integer, nullable=False)
    approval_step: Mapped[int] = mapped_column(Integer, nullable=False)
    approver_role: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    approver_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    approver_email: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    decision: Mapped[str] = mapped_column(
        String(50), server_default="'Pending'", nullable=False
    )
    decision_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    cycle: Mapped[Optional["EvaluationCycle"]] = relationship(back_populates="approvals")


class Escalation(TimestampMixin, Base):
    """Escalation record tied to a cycle for overdue or disputed items."""
    __tablename__ = "escalation"
    __table_args__ = (
        Index("idx_escalation_cycle", "id_cycle"),
    )

    id_escalation: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_cycle: Mapped[Optional[int]] = mapped_column(
        ForeignKey("evaluation_cycle.id_cycle", ondelete="CASCADE"), nullable=True
    )
    object_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    object_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    escalation_reason: Mapped[str] = mapped_column(Text, nullable=False)
    escalated_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    escalated_to_role: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    escalated_to_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    escalation_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=True
    )
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(
        String(50), server_default="'Open'", nullable=False
    )
    resolution_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    cycle: Mapped[Optional["EvaluationCycle"]] = relationship(back_populates="escalations")


class ScoreCard(AuditMixin, Base):
    """Aggregated scorecard result for a relation/cycle."""
    __tablename__ = "score_card"

    id_score_card: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_relation: Mapped[Optional[int]] = mapped_column(
        ForeignKey("supplier_site_relation.id_relation", ondelete="CASCADE"), nullable=True
    )
    scorecard_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    grade: Mapped[Optional[str]] = mapped_column(CHAR(1), nullable=True)
    id_cycle: Mapped[Optional[int]] = mapped_column(
        ForeignKey("evaluation_cycle.id_cycle", ondelete="SET NULL"), nullable=True
    )
    id_document: Mapped[Optional[int]] = mapped_column(
        ForeignKey("document.id_document", ondelete="SET NULL"), nullable=True
    )

    # Relationships
    relation: Mapped[Optional["SupplierSiteRelation"]] = relationship(
        back_populates="score_cards"
    )
    cycle: Mapped[Optional["EvaluationCycle"]] = relationship(back_populates="score_cards")
    kpi_details: Mapped[List["ScorecardKpiDetail"]] = relationship(
        back_populates="score_card", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<ScoreCard id={self.id_score_card} score={self.score} grade={self.grade}>"


class Classification(AuditMixin, Base):
    """Supplier classification result (class 1-4, score) for a cycle."""
    __tablename__ = "classification"

    id_classification: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    id_relation: Mapped[Optional[int]] = mapped_column(
        ForeignKey("supplier_site_relation.id_relation", ondelete="CASCADE"), nullable=True
    )
    classification_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    classification_score: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    class_value: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    id_cycle: Mapped[Optional[int]] = mapped_column(
        ForeignKey("evaluation_cycle.id_cycle", ondelete="SET NULL"), nullable=True
    )
    id_document: Mapped[Optional[int]] = mapped_column(
        ForeignKey("document.id_document", ondelete="SET NULL"), nullable=True
    )

    # Relationships
    relation: Mapped[Optional["SupplierSiteRelation"]] = relationship(
        back_populates="classifications"
    )
    cycle: Mapped[Optional["EvaluationCycle"]] = relationship(
        back_populates="classifications"
    )


class ScorecardKpiDetail(TimestampMixin, Base):
    """Individual KPI line within a scorecard."""
    __tablename__ = "scorecard_kpi_detail"
    __table_args__ = (
        Index("idx_scorecard_kpi_cycle", "id_cycle"),
    )

    id_kpi_detail: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_score_card: Mapped[Optional[int]] = mapped_column(
        ForeignKey("score_card.id_score_card", ondelete="CASCADE"), nullable=True
    )
    id_cycle: Mapped[Optional[int]] = mapped_column(
        ForeignKey("evaluation_cycle.id_cycle", ondelete="SET NULL"), nullable=True
    )
    id_document: Mapped[Optional[int]] = mapped_column(
        ForeignKey("document.id_document", ondelete="SET NULL"), nullable=True
    )
    id_relation: Mapped[Optional[int]] = mapped_column(
        ForeignKey("supplier_site_relation.id_relation", ondelete="SET NULL"), nullable=True
    )
    kpi_name: Mapped[str] = mapped_column(String(150), nullable=False)
    kpi_category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    kpi_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4), nullable=True)
    kpi_unit: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    kpi_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    weight: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    weighted_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    source_dataset: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    score_card: Mapped[Optional["ScoreCard"]] = relationship(back_populates="kpi_details")
    cycle: Mapped[Optional["EvaluationCycle"]] = relationship(back_populates="kpi_details")
    relation: Mapped[Optional["SupplierSiteRelation"]] = relationship(
        back_populates="scorecard_kpi_details"
    )


class ScorecardUploadRegister(TimestampMixin, Base):
    """Tracks dataset upload status (timeliness, validation) per cycle."""
    __tablename__ = "scorecard_upload_register"

    id_upload_register: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    id_cycle: Mapped[Optional[int]] = mapped_column(
        ForeignKey("evaluation_cycle.id_cycle", ondelete="SET NULL"), nullable=True
    )
    id_document: Mapped[Optional[int]] = mapped_column(
        ForeignKey("document.id_document", ondelete="SET NULL"), nullable=True
    )
    reporting_period: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    plant: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    dataset: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    owner_function: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    contact: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    upload_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    validation_status: Mapped[str] = mapped_column(
        String(50), server_default="'Pending'", nullable=False
    )
    plant_approval: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    timeliness_flag: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Relationships
    cycle: Mapped[Optional["EvaluationCycle"]] = relationship(
        back_populates="upload_registers"
    )


class ScorecardDataQualityCheck(TimestampMixin, Base):
    """Automated data-quality check result for a dataset within a cycle."""
    __tablename__ = "scorecard_data_quality_checks"

    id_check: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_cycle: Mapped[Optional[int]] = mapped_column(
        ForeignKey("evaluation_cycle.id_cycle", ondelete="SET NULL"), nullable=True
    )
    id_document: Mapped[Optional[int]] = mapped_column(
        ForeignKey("document.id_document", ondelete="SET NULL"), nullable=True
    )
    dataset: Mapped[str] = mapped_column(String(150), nullable=False)
    check_name: Mapped[str] = mapped_column(String(200), nullable=False)
    check_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metric_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4), nullable=True)
    target_operator: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    target_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4), nullable=True)
    check_status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    formula_reference: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    checked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=True
    )

    # Relationships
    cycle: Mapped[Optional["EvaluationCycle"]] = relationship(
        back_populates="data_quality_checks"
    )


class PldScoringRules(Base):
    """
    Configuration table for scoring rules.
    Extensible: add new criteria_type values without schema changes.
    """
    __tablename__ = "pld_scoring_rules"

    rule_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    criteria_type: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    score: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    min_value: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    max_value: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)


# ---------------------------------------------------------------------------
# Domain 2 — Raw Input Tables
# ---------------------------------------------------------------------------


class InputOtdMonthly(TimestampMixin, Base):
    """Monthly On-Time Delivery raw data uploaded per supplier-site-cycle."""
    __tablename__ = "input_otd_monthly"
    __table_args__ = (
        Index("idx_input_otd_monthly_cycle", "id_cycle"),
    )

    id_otd: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_cycle: Mapped[Optional[int]] = mapped_column(
        ForeignKey("evaluation_cycle.id_cycle", ondelete="SET NULL"), nullable=True
    )
    id_document: Mapped[Optional[int]] = mapped_column(
        ForeignKey("document.id_document", ondelete="SET NULL"), nullable=True
    )
    id_relation: Mapped[Optional[int]] = mapped_column(
        ForeignKey("supplier_site_relation.id_relation", ondelete="SET NULL"), nullable=True
    )
    supplier_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    supplier_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    month_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    otd_raw_value: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    otd_percent: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    plant: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)

    # Relationships
    cycle: Mapped[Optional["EvaluationCycle"]] = relationship(back_populates="input_otd")
    relation: Mapped[Optional["SupplierSiteRelation"]] = relationship(
        back_populates="input_otd"
    )


class InputQualityClaims(TimestampMixin, Base):
    """Quality claim records imported per cycle."""
    __tablename__ = "input_quality_claims"
    __table_args__ = (
        Index("idx_input_quality_claims_cycle", "id_cycle"),
    )

    id_quality_claim: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    id_cycle: Mapped[Optional[int]] = mapped_column(
        ForeignKey("evaluation_cycle.id_cycle", ondelete="SET NULL"), nullable=True
    )
    id_document: Mapped[Optional[int]] = mapped_column(
        ForeignKey("document.id_document", ondelete="SET NULL"), nullable=True
    )
    id_relation: Mapped[Optional[int]] = mapped_column(
        ForeignKey("supplier_site_relation.id_relation", ondelete="SET NULL"), nullable=True
    )
    claimed_part_reference: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    claim_number: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    claim_opening_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    claim_closing_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    claim_status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    supplier_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    supplier_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    plant: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)

    # Relationships
    cycle: Mapped[Optional["EvaluationCycle"]] = relationship(
        back_populates="input_quality_claims"
    )
    relation: Mapped[Optional["SupplierSiteRelation"]] = relationship(
        back_populates="input_quality_claims"
    )


class InputDeliverySpend(TimestampMixin, Base):
    """Delivery spend / PPM (bad parts) data per cycle."""
    __tablename__ = "input_delivery_spend"
    __table_args__ = (
        Index("idx_input_delivery_spend_cycle", "id_cycle"),
    )

    id_delivery_spend: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    id_cycle: Mapped[Optional[int]] = mapped_column(
        ForeignKey("evaluation_cycle.id_cycle", ondelete="SET NULL"), nullable=True
    )
    id_document: Mapped[Optional[int]] = mapped_column(
        ForeignKey("document.id_document", ondelete="SET NULL"), nullable=True
    )
    id_relation: Mapped[Optional[int]] = mapped_column(
        ForeignKey("supplier_site_relation.id_relation", ondelete="SET NULL"), nullable=True
    )
    part_reference: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    delivery_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    quantity: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4), nullable=True)
    purchase_price_delivery: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(18, 4), nullable=True
    )
    currency: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    supplier_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    supplier_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    bad_parts: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4), nullable=True)
    plant: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)

    # Relationships
    cycle: Mapped[Optional["EvaluationCycle"]] = relationship(
        back_populates="input_delivery_spend"
    )
    relation: Mapped[Optional["SupplierSiteRelation"]] = relationship(
        back_populates="input_delivery_spend"
    )


# ---------------------------------------------------------------------------
# Domain 3 — Supplier Self-Assessment Module  (extensible)
# ---------------------------------------------------------------------------


class AssessmentTemplate(TimestampMixin, Base):
    """
    Template definition for supplier self-assessment forms.
    Decoupled from data: field structure lives in AssessmentTemplateFieldMapping,
    making it easy to add new template types without schema changes.
    """
    __tablename__ = "assessment_template"

    id_template: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_name: Mapped[str] = mapped_column(String(255), nullable=False)
    template_type: Mapped[str] = mapped_column(
        String(100), server_default="'SELF_ASSESSMENT'", nullable=False
    )
    version: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(
        String(50), server_default="'Active'", nullable=False
    )
    id_document: Mapped[Optional[int]] = mapped_column(
        ForeignKey("document.id_document", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    document: Mapped[Optional["Document"]] = relationship(
        back_populates="assessment_templates"
    )
    field_mappings: Mapped[List["AssessmentTemplateFieldMapping"]] = relationship(
        back_populates="template", cascade="all, delete-orphan", passive_deletes=True
    )
    assessments: Mapped[List["SupplierAssessment"]] = relationship(
        back_populates="template"
    )

    def __repr__(self) -> str:
        return (
            f"<AssessmentTemplate id={self.id_template} "
            f"name={self.template_name!r} v={self.version!r}>"
        )


class AssessmentTemplateFieldMapping(TimestampMixin, Base):
    """
    Maps Excel/form fields to target DB columns for a given template.
    Enables dynamic import without code changes per template version.
    """
    __tablename__ = "assessment_template_field_mapping"

    id_mapping: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_template: Mapped[int] = mapped_column(
        ForeignKey("assessment_template.id_template", ondelete="CASCADE"), nullable=False
    )
    sheet_name: Mapped[str] = mapped_column(String(150), nullable=False)
    field_code: Mapped[str] = mapped_column(String(150), nullable=False)
    field_label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    cell_reference: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    column_reference: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    data_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    target_table: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    target_column: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    is_required: Mapped[bool] = mapped_column(
        Boolean, server_default="false", nullable=False
    )

    # Relationships
    template: Mapped["AssessmentTemplate"] = relationship(back_populates="field_mappings")
    answers: Mapped[List["SupplierAssessmentAnswer"]] = relationship(
        back_populates="mapping"
    )

    def __repr__(self) -> str:
        return (
            f"<FieldMapping id={self.id_mapping} "
            f"field={self.field_code!r} sheet={self.sheet_name!r}>"
        )


class SupplierAssessment(TimestampMixin, Base):
    """
    One completed assessment submission by a supplier, linked to a
    template, cycle, relation, and source document.
    """
    __tablename__ = "supplier_assessment"

    id_assessment: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_relation: Mapped[Optional[int]] = mapped_column(
        ForeignKey("supplier_site_relation.id_relation", ondelete="SET NULL"), nullable=True
    )
    id_template: Mapped[Optional[int]] = mapped_column(
        ForeignKey("assessment_template.id_template", ondelete="SET NULL"), nullable=True
    )
    id_document: Mapped[Optional[int]] = mapped_column(
        ForeignKey("document.id_document", ondelete="SET NULL"), nullable=True
    )
    id_cycle: Mapped[Optional[int]] = mapped_column(
        ForeignKey("evaluation_cycle.id_cycle", ondelete="SET NULL"), nullable=True
    )
    assessment_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    submitted_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(
        String(50), server_default="'Received'", nullable=False
    )
    final_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    final_grade: Mapped[Optional[str]] = mapped_column(CHAR(1), nullable=True)
    final_class: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    relation: Mapped[Optional["SupplierSiteRelation"]] = relationship(
        back_populates="assessments"
    )
    template: Mapped[Optional["AssessmentTemplate"]] = relationship(
        back_populates="assessments"
    )
    document: Mapped[Optional["Document"]] = relationship(back_populates="assessments")
    cycle: Mapped[Optional["EvaluationCycle"]] = relationship(back_populates="assessments")
    answers: Mapped[List["SupplierAssessmentAnswer"]] = relationship(
        back_populates="assessment", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        return (
            f"<SupplierAssessment id={self.id_assessment} "
            f"status={self.status!r} score={self.final_score}>"
        )


class SupplierAssessmentAnswer(TimestampMixin, Base):
    """
    Individual answer for one field within a supplier assessment.
    Stores both raw and normalized values plus validation result.
    """
    __tablename__ = "supplier_assessment_answer"

    id_answer: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_assessment: Mapped[int] = mapped_column(
        ForeignKey("supplier_assessment.id_assessment", ondelete="CASCADE"), nullable=False
    )
    id_mapping: Mapped[Optional[int]] = mapped_column(
        ForeignKey(
            "assessment_template_field_mapping.id_mapping", ondelete="SET NULL"
        ),
        nullable=True,
    )
    field_code: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    field_label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    raw_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    normalized_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    is_valid: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    validation_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    assessment: Mapped["SupplierAssessment"] = relationship(back_populates="answers")
    mapping: Mapped[Optional["AssessmentTemplateFieldMapping"]] = relationship(
        back_populates="answers"
    )

    def __repr__(self) -> str:
        return (
            f"<AssessmentAnswer id={self.id_answer} "
            f"field={self.field_code!r} valid={self.is_valid}>"
        )


# ---------------------------------------------------------------------------
# Domain 4 — Opportunity / Project / Financial Savings
# ---------------------------------------------------------------------------


class Opportunity(Base):
    """
    Cost-saving or improvement opportunity identified by purchasing.
    Drives downstream projects and financial tracking.
    """
    __tablename__ = "opportunity"

    opportunity_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    opportunity_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    opportunity_type: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    idea_owner: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    purchasing_owner: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    project_owner: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    conversion_owner: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    plant_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("avocarbon_site.id_site", ondelete="CASCADE"), nullable=True
    )
    supplier_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("supplier_unit.id_supplier_unit", ondelete="CASCADE"), nullable=True
    )
    expected_annual_saving: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    planned_start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    real_start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    duration_months: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2), nullable=True)
    results: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    budget_year: Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 0), nullable=True)
    phase_status: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    validation_decision: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status2: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    change_mode: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    assumptions_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    saving_score: Mapped[Optional[Decimal]] = mapped_column(
        "Saving_score", Numeric(10, 2), nullable=True
    )
    lead_time_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    difficulty_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    priority_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    priority_category: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Column comment: "Total time to design and achieve Opportunity"
    lead_time: Mapped[Optional[int]] = mapped_column(
        "Lead_time", Integer, nullable=True
    )

    # Relationships
    plant: Mapped[Optional["AvocarbonSite"]] = relationship(back_populates="opportunities")
    supplier: Mapped[Optional["SupplierUnit"]] = relationship(
        back_populates="opportunities"
    )
    projects: Mapped[List["Project"]] = relationship(
        back_populates="opportunity", cascade="all, delete-orphan", passive_deletes=True
    )
    financial_lines: Mapped[List["FinancialLine"]] = relationship(
        back_populates="opportunity", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<Opportunity id={self.opportunity_id} name={self.opportunity_name!r}>"


class Project(Base):
    """Implementation project derived from an Opportunity."""
    __tablename__ = "project"

    project_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("opportunity.opportunity_id", ondelete="CASCADE"), nullable=True
    )
    project_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    project_type: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    project_owner: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phase_status: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    gate_decision: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    planned_end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    actual_end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    plant_validation: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    opportunity: Mapped[Optional["Opportunity"]] = relationship(back_populates="projects")
    financial_lines: Mapped[List["FinancialLine"]] = relationship(
        back_populates="project"
    )

    def __repr__(self) -> str:
        return f"<Project id={self.project_id} name={self.project_name!r}>"


class FinancialLine(Base):
    """
    Budgeted / actual saving line within an opportunity, per plant.
    Aggregated monthly in MonthlyFinancial.
    """
    __tablename__ = "financial_line"

    financial_line_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("opportunity.opportunity_id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("project.project_id", ondelete="SET NULL"), nullable=True
    )
    plant_id: Mapped[int] = mapped_column(
        ForeignKey("avocarbon_site.id_site", ondelete="CASCADE"), nullable=False
    )
    line_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    budget_status: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    planned_start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    budget_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    real_start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    duration_months: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2), nullable=True)
    cumulated_real_saving: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    delta_vs_expected_ytd: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    delta_vs_budget_ytd: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    status: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    follower: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    forecast_eoy_current: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    forecast_eoy_last_update: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    expected_annual_saving: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(18, 2), nullable=True
    )

    # Relationships
    opportunity: Mapped["Opportunity"] = relationship(back_populates="financial_lines")
    project: Mapped[Optional["Project"]] = relationship(back_populates="financial_lines")
    plant: Mapped["AvocarbonSite"] = relationship(
        back_populates="financial_lines", foreign_keys=[plant_id]
    )
    monthly_financials: Mapped[List["MonthlyFinancial"]] = relationship(
        back_populates="financial_line", cascade="all, delete-orphan", passive_deletes=True
    )


class MonthlyFinancial(Base):
    """
    Monthly saving actuals vs expected for a financial line.
    Time-series granularity for reporting and forecasting.
    """
    __tablename__ = "monthly_financial"

    monthly_financial_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    financial_line_id: Mapped[int] = mapped_column(
        ForeignKey("financial_line.financial_line_id", ondelete="CASCADE"), nullable=False
    )
    period_month: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    expected_saving: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    actual_saving: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    cumulated_expected: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    cumulated_actual: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    delta_vs_expected: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    delta_vs_budget: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    forecast_eoy_saving: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    forecast_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    financial_line: Mapped["FinancialLine"] = relationship(
        back_populates="monthly_financials"
    )


# ---------------------------------------------------------------------------
# __all__ — public surface
# ---------------------------------------------------------------------------

__all__ = [
    "Base",
    # Mixins
    "TimestampMixin",
    "CycleDocumentMixin",
    "AuditMixin",
    # Enums
    "DecisionStatus",
    "CycleStatus",
    "DocumentStatus",
    "EscalationStatus",
    "ValidationStatus",
    "AssessmentStatus",
    "TemplateType",
    # Domain 1 — Supplier Master
    "AvocarbonSite",
    "SupplierGroup",
    "SupplierUnit",
    "SupplierSiteRelation",
    "SupplierStatusHistory",
    "SupplierCertification",
    "SupplierAgreement",
    "Contact",
    "ContactSiteRelation",
    "Document",
    # Domain 2 — Evaluation
    "EvaluationCycle",
    "ApprovalWorkflow",
    "Escalation",
    "ScoreCard",
    "Classification",
    "ScorecardKpiDetail",
    "ScorecardUploadRegister",
    "ScorecardDataQualityCheck",
    "PldScoringRules",
    "InputOtdMonthly",
    "InputQualityClaims",
    "InputDeliverySpend",
    # Domain 3 — Assessment
    "AssessmentTemplate",
    "AssessmentTemplateFieldMapping",
    "SupplierAssessment",
    "SupplierAssessmentAnswer",
    # Domain 4 — Opportunity / Financial
    "Opportunity",
    "Project",
    "FinancialLine",
    "MonthlyFinancial",
]