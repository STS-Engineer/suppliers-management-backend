"""
app/db/models.py — Shared ORM Foundation
==========================================

"""

from __future__ import annotations

import enum
from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List

from sqlalchemy import (
    DateTime,
    String,
    Text,
    func,
    text,
    Integer,
    BigInteger,
    Boolean,
    ForeignKey,
    Numeric,
    CHAR,
    Index,
    UniqueConstraint,
    Date,
    inspect,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import JSONB, UUID, ARRAY
from app.db.session import Base

# ---------------------------------------------------------------------------
# Base & Mixins
# ---------------------------------------------------------------------------


def _format_business_code(prefix: str, raw_id: Optional[int]) -> Optional[str]:
    if raw_id is None:
        return None
    return f"{prefix}-{raw_id:06d}"


class TimestampMixin:
    """Adds created_at with server-side default."""

    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=True
    )


class AuditMixin:
    """Light audit trail: who entered / when."""

    entered_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    entered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=True
    )
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class GovernanceMixin:
    """
    Production governance columns added by the IATF/audit upgrade migration.
    """

    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, server_default="false", nullable=False
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    deleted_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    row_version: Mapped[int] = mapped_column(
        Integer, server_default="1", nullable=False
    )


# ---------------------------------------------------------------------------
# Enumerations
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


class AvocarbonSite(GovernanceMixin, Base):
    __tablename__ = "avocarbon_site"

    id_site: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    site_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    address_line: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    active: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

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


class SupplierGroup(GovernanceMixin, Base):
    __tablename__ = "supplier_group"

    id_group: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nom: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    supplier_scope: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    group_supplier_owner_email: Mapped[Optional[str]] = mapped_column(
        String(200), nullable=True
    )
    multi_site: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    validation_status: Mapped[str] = mapped_column(
        String(20), server_default="approved", nullable=False
    )

    units: Mapped[List["SupplierUnit"]] = relationship(
        back_populates="group", cascade="all, delete-orphan", passive_deletes=True
    )
    contacts: Mapped[List["Contact"]] = relationship(
        back_populates="supplier_group",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    documents: Mapped[List["Document"]] = relationship(
        back_populates="group",
        foreign_keys="Document.id_group",
    )

    @property
    def supplier_owner(self) -> Optional[str]:
        return self.group_supplier_owner_email

    @supplier_owner.setter
    def supplier_owner(self, value: Optional[str]) -> None:
        self.group_supplier_owner_email = value

    @property
    def group_code(self) -> Optional[str]:
        return _format_business_code("GRP", self.id_group)

    @property
    def commodities(self) -> List[str]:
        """Commodities aggregated (read-only) from this group's supplier units."""
        state = inspect(self)
        if "units" in state.unloaded:
            return []
        seen: dict[str, str] = {}
        for unit in self.units:
            if not unit.commodity:
                continue
            for raw in unit.commodity.split(","):
                value = raw.strip()
                if not value:
                    continue
                key = value.lower()
                if key not in seen:
                    seen[key] = value
        return sorted(seen.values(), key=str.lower)

    def __repr__(self) -> str:
        return f"<SupplierGroup id={self.id_group} nom={self.nom!r}>"


class SupplierUnit(TimestampMixin, GovernanceMixin, Base):
    __tablename__ = "supplier_unit"
    __table_args__ = (
        UniqueConstraint("id_group", "supplier_name", name="uq_supplier_unit_group_code"),
    )

    id_supplier_unit: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    id_group: Mapped[Optional[int]] = mapped_column(
        ForeignKey("supplier_group.id_group", ondelete="CASCADE"), nullable=True
    )
    supplier_name: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    address_line: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # Product classification (comma-separated multi-value)
    commodity: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    family: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    sub_family: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    product_line: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # Additional unit info
    website: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    carbon_footprint: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    green_electricity_pct: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    continent: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    area: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    strategique: Mapped[bool] = mapped_column(
        Boolean, server_default="false", nullable=False
    )
    monopolistique: Mapped[bool] = mapped_column(
        Boolean, server_default="false", nullable=False
    )
    directed: Mapped[bool] = mapped_column(
        Boolean, server_default="false", nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, server_default="true", nullable=False
    )
    inactivated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    group: Mapped[Optional["SupplierGroup"]] = relationship(back_populates="units")
    site_relations: Mapped[List["SupplierSiteRelation"]] = relationship(
        back_populates="supplier_unit",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    carbon_footprints: Mapped[List["SupplierCarbonFootprint"]] = relationship(
        back_populates="supplier_unit",
    )
    certifications: Mapped[List["SupplierCertification"]] = relationship(
        back_populates="supplier_unit",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    contacts: Mapped[List["Contact"]] = relationship(
        back_populates="supplier_unit",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    documents: Mapped[List["Document"]] = relationship(
        back_populates="supplier_unit",
        foreign_keys="Document.id_supplier_unit",
    )
    opportunities: Mapped[List["Opportunity"]] = relationship(
        back_populates="supplier",
        foreign_keys="Opportunity.supplier_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def unit_code(self) -> Optional[str]:
        return _format_business_code("UNT", self.id_supplier_unit)

    def __repr__(self) -> str:
        return f"<SupplierUnit id={self.id_supplier_unit} name={self.supplier_name!r}>"


class SupplierSiteRelation(GovernanceMixin, Base):
    __tablename__ = "supplier_site_relation"
    # FIX: added unique constraint to enforce one relation per (site, supplier_unit) pair.
    # The DB constraint is created in the fix migration; declared here for ORM awareness.
    __table_args__ = (
        UniqueConstraint(
            "id_site", "id_supplier_unit", name="uq_relation_site_supplier"
        ),
    )

    id_relation: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    id_site: Mapped[int] = mapped_column(
        ForeignKey("avocarbon_site.id_site", ondelete="CASCADE"), nullable=False
    )
    id_supplier_unit: Mapped[int] = mapped_column(
        ForeignKey("supplier_unit.id_supplier_unit", ondelete="CASCADE"), nullable=False
    )
    alias_1: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    buyer_owner: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    annual_spend_value: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    supplier_status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    operational_grade: Mapped[Optional[str]] = mapped_column(CHAR(1), nullable=True)
    class_value: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    global_status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    evaluation_frequency: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )
    final_grade: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    strategic_mention: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    panel_decision: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    last_evaluation_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    next_evaluation_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    evaluation_comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    evaluation_suggestion: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    last_eval_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, server_default="true", nullable=False
    )
    inactivated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_status_change: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )
    evaluation_draft: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True
    )
    validation_status: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, server_default="draft"
    )
    submitted_for_review_by: Mapped[Optional[str]] = mapped_column(
        String(200), nullable=True
    )
    review_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    site: Mapped["AvocarbonSite"] = relationship(back_populates="supplier_relations")
    supplier_unit: Mapped["SupplierUnit"] = relationship(
        back_populates="site_relations"
    )
    evaluation_cycles: Mapped[List["EvaluationCycle"]] = relationship(
        back_populates="relation", cascade="all, delete-orphan", passive_deletes=True
    )
    score_cards: Mapped[List["ScoreCard"]] = relationship(
        back_populates="relation", cascade="all, delete-orphan", passive_deletes=True
    )
    classifications: Mapped[List["Classification"]] = relationship(
        back_populates="relation", cascade="all, delete-orphan", passive_deletes=True
    )
    pld_class_inputs: Mapped[List["PldClassEvaluationInput"]] = relationship(
        back_populates="relation", cascade="all, delete-orphan", passive_deletes=True
    )
    operational_inputs: Mapped[List["OperationalEvaluationInput"]] = relationship(
        back_populates="relation", cascade="all, delete-orphan", passive_deletes=True
    )
    impact_inputs: Mapped[List["ImpactEvaluationInput"]] = relationship(
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

    @property
    def supplier_scope(self) -> Optional[str]:
        return self.global_status

    @supplier_scope.setter
    def supplier_scope(self, value: Optional[str]) -> None:
        self.global_status = value

    @property
    def supplier_owner(self) -> Optional[str]:
        return self.buyer_owner

    @supplier_owner.setter
    def supplier_owner(self, value: Optional[str]) -> None:
        self.buyer_owner = value

    @property
    def relation_code(self) -> Optional[str]:
        return _format_business_code("REL", self.id_relation)

    @property
    def unit_code(self) -> Optional[str]:
        return _format_business_code("UNT", self.id_supplier_unit)

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
    action_plans: Mapped[List["SupplierActionPlan"]] = relationship(
        back_populates="relation"
    )
    development_plans: Mapped[List["SupplierDevelopmentPlan"]] = relationship(
        back_populates="relation", cascade="all, delete-orphan", passive_deletes=True
    )
    carbon_footprints: Mapped[List["SupplierCarbonFootprint"]] = relationship(
        back_populates="relation",
    )
    committee_reviews: Mapped[List["CommitteeReview"]] = relationship(
        back_populates="relation", cascade="all, delete-orphan", passive_deletes=True
    )
    spend_by_year: Mapped[List["SupplierSpendByYear"]] = relationship(
        back_populates="relation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="SupplierSpendByYear.fiscal_year.desc()",
    )

    def __repr__(self) -> str:
        return (
            f"<SupplierSiteRelation id={self.id_relation} "
            f"site={self.id_site} unit={self.id_supplier_unit}>"
        )


class SupplierSpendByYear(Base):
    """Annual purchasing spend for a supplier-site relation, tracked per fiscal year."""

    __tablename__ = "supplier_spend_by_year"
    __table_args__ = (
        UniqueConstraint("id_relation", "fiscal_year", name="uq_spend_relation_year"),
    )

    id_spend: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_relation: Mapped[int] = mapped_column(
        ForeignKey("supplier_site_relation.id_relation", ondelete="CASCADE"),
        nullable=False,
    )
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    spend_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    spend_currency: Mapped[str] = mapped_column(String(10), nullable=False, default="EUR")
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=True
    )
    created_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    relation: Mapped["SupplierSiteRelation"] = relationship(back_populates="spend_by_year")


class SupplierCarbonFootprint(Base):
    """Carbon footprint data per supplier entity × plant × year (from SB8 board)."""
    __tablename__ = "supplier_carbon_footprint"

    id_carbon_footprint: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_supplier_unit: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("supplier_unit.id_supplier_unit"), nullable=True)
    id_relation: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("supplier_site_relation.id_relation"), nullable=True)
    year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    carbon_fp_grade: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    purchase_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    weighted_footprint: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 6), nullable=True)
    production_fp_grade: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    transport_impact: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    global_fp_impact: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    supplier_origin: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    supplier_continent: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    site_location: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    site_continent: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=func.current_timestamp(), nullable=True)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    supplier_unit: Mapped[Optional["SupplierUnit"]] = relationship(back_populates="carbon_footprints")
    relation: Mapped[Optional["SupplierSiteRelation"]] = relationship(back_populates="carbon_footprints")


class SupplierStatusHistory(TimestampMixin, Base):
    __tablename__ = "supplier_status_history"

    id_history: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    id_relation: Mapped[int] = mapped_column(
        ForeignKey("supplier_site_relation.id_relation", ondelete="CASCADE"),
        nullable=False,
    )
    old_status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    new_status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    old_class: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    new_class: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    old_grade: Mapped[Optional[str]] = mapped_column(CHAR(1), nullable=True)
    new_grade: Mapped[Optional[str]] = mapped_column(CHAR(1), nullable=True)
    old_final_grade: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    new_final_grade: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    old_strategic_mention: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )
    new_strategic_mention: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )
    old_panel_decision: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )
    new_panel_decision: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )
    change_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    changed_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    changed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=True
    )

    relation: Mapped["SupplierSiteRelation"] = relationship(
        back_populates="status_history"
    )


class SupplierDevelopmentPlan(TimestampMixin, GovernanceMixin, Base):
    __tablename__ = "supplier_development_plan"

    id_development_plan: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    id_relation: Mapped[int] = mapped_column(
        ForeignKey("supplier_site_relation.id_relation", ondelete="CASCADE"),
        nullable=False,
    )
    id_document: Mapped[Optional[int]] = mapped_column(
        ForeignKey("document.id_document", ondelete="SET NULL"), nullable=True
    )
    plan_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    plan_status: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    issue_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    submission_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    review_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    decision_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    approved_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    rejected_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    business_hold_active: Mapped[Optional[bool]] = mapped_column(
        Boolean, nullable=True
    )
    escalated: Mapped[bool] = mapped_column(
        Boolean, server_default="false", nullable=False
    )
    escalation_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    file_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    file_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    file_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    supplier_comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    internal_comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    decision: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    commodity: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    plant: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    relation: Mapped["SupplierSiteRelation"] = relationship(
        back_populates="development_plans"
    )
    document: Mapped[Optional["Document"]] = relationship(
        foreign_keys="[SupplierDevelopmentPlan.id_document]"
    )


class SupplierCertification(GovernanceMixin, Base):
    """
    Certifications (ISO, IATF …) held by a supplier unit.

    FIX: removed document-control columns (document_owner, controlled_document,
    retention_code, review_due_date, expiry_date, file_hash_sha256,
    storage_provider, storage_object_key, superseded_by_document_id) that
    were declared in the ORM but never created in the DB by any migration.
    Document control belongs on the Document model.
    """

    __tablename__ = "supplier_certification"

    id_certification: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    id_supplier_unit: Mapped[Optional[int]] = mapped_column(
        ForeignKey("supplier_unit.id_supplier_unit", ondelete="CASCADE"), nullable=True
    )
    standard_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    certification_type: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )
    certificate_name: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    amount_value: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    amount_currency: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    expiry_mode: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    file_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    file_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    file_size: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)

    supplier_unit: Mapped[Optional["SupplierUnit"]] = relationship(
        back_populates="certifications"
    )


class SupplierAgreement(GovernanceMixin, Base):
    __tablename__ = "supplier_agreement"

    id_agreement: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    id_relation: Mapped[Optional[int]] = mapped_column(
        ForeignKey("supplier_site_relation.id_relation", ondelete="CASCADE"),
        nullable=True,
    )
    agreement_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    agreement_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    amount_value: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    amount_currency: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    location_value: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    value_unit: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    agreement_description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    agreement_value: Mapped[Optional[float]] = mapped_column(
        Numeric(18, 6), nullable=True
    )

    relation: Mapped[Optional["SupplierSiteRelation"]] = relationship(
        back_populates="agreements"
    )


# ---------------------------------------------------------------------------
# Domain 1 — Contacts
# ---------------------------------------------------------------------------


class Contact(TimestampMixin, GovernanceMixin, Base):
    __tablename__ = "contact"

    id_contact: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
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


class ContactSiteRelation(GovernanceMixin, Base):
    __tablename__ = "contact_site_relation"
    __table_args__ = (
        UniqueConstraint(
            "id_contact",
            "id_relation",
            name="contact_site_relation_id_contact_id_relation_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_contact: Mapped[int] = mapped_column(
        ForeignKey("contact.id_contact", ondelete="CASCADE"), nullable=False
    )
    id_relation: Mapped[int] = mapped_column(
        ForeignKey("supplier_site_relation.id_relation", ondelete="CASCADE"),
        nullable=False,
    )

    contact: Mapped["Contact"] = relationship(back_populates="site_relations")
    relation: Mapped["SupplierSiteRelation"] = relationship(
        back_populates="contacts_via_junction"
    )


# ---------------------------------------------------------------------------
# Domain 1 — Documents
# ---------------------------------------------------------------------------


class Document(TimestampMixin, GovernanceMixin, Base):
    __tablename__ = "document"
    __table_args__ = (Index("idx_document_relation", "id_relation"),)

    id_document: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    id_relation: Mapped[Optional[int]] = mapped_column(
        ForeignKey("supplier_site_relation.id_relation", ondelete="SET NULL"),
        nullable=True,
    )
    id_supplier_unit: Mapped[Optional[int]] = mapped_column(
        ForeignKey("supplier_unit.id_supplier_unit", ondelete="SET NULL"), nullable=True
    )
    id_group: Mapped[Optional[int]] = mapped_column(
        ForeignKey("supplier_group.id_group", ondelete="SET NULL"), nullable=True
    )
    document_type: Mapped[str] = mapped_column(String(100), nullable=False)
    document_name: Mapped[str] = mapped_column(String(255), nullable=False)
    original_file_name: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
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
    # FIX: server_default must not wrap the string in extra single-quotes.
    # SQLAlchemy already emits the surrounding quotes for string literals;
    # the previous "server_default=\"'Uploaded'\"" would produce DEFAULT ''Uploaded''.
    status: Mapped[str] = mapped_column(
        String(50), server_default="Uploaded", nullable=False
    )
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    file_hash_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    storage_provider: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    storage_object_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    id_development_plan: Mapped[Optional[int]] = mapped_column(
        ForeignKey("supplier_development_plan.id_development_plan", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    superseded_by_document_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("document.id_document", ondelete="SET NULL"), nullable=True
    )

    relation: Mapped[Optional["SupplierSiteRelation"]] = relationship(
        back_populates="documents", foreign_keys=[id_relation]
    )
    supplier_unit: Mapped[Optional["SupplierUnit"]] = relationship(
        back_populates="documents", foreign_keys=[id_supplier_unit]
    )
    group: Mapped[Optional["SupplierGroup"]] = relationship(
        back_populates="documents", foreign_keys=[id_group]
    )
    assessment_templates: Mapped[List["AssessmentTemplate"]] = relationship(
        back_populates="document"
    )
    assessments: Mapped[List["SupplierAssessment"]] = relationship(
        back_populates="document"
    )
    import_batches: Mapped[List["ImportBatch"]] = relationship(
        back_populates="document"
    )
    superseded_by_document: Mapped[Optional["Document"]] = relationship(
        "Document",
        remote_side="Document.id_document",
        foreign_keys="Document.superseded_by_document_id",
    )

    def __repr__(self) -> str:
        return f"<Document id={self.id_document} name={self.document_name!r}>"


# ---------------------------------------------------------------------------
# Domain 2 — Evaluation & Scorecard Pipeline
# ---------------------------------------------------------------------------


class EvaluationCycle(TimestampMixin, GovernanceMixin, Base):
    __tablename__ = "evaluation_cycle"
    __table_args__ = (Index("idx_evaluation_cycle_relation", "id_relation"),)

    id_cycle: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_relation: Mapped[int] = mapped_column(
        ForeignKey("supplier_site_relation.id_relation", ondelete="CASCADE"),
        nullable=False,
    )
    cycle_type: Mapped[str] = mapped_column(String(100), nullable=False)
    supplier_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    frequency: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    period_start: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    period_end: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    # FIX: removed extra quotes from server_default
    cycle_status: Mapped[str] = mapped_column(
        String(50), server_default="Draft", nullable=False
    )
    launched_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    launched_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

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
    classifications: Mapped[List["Classification"]] = relationship(
        back_populates="cycle"
    )
    pld_class_inputs: Mapped[List["PldClassEvaluationInput"]] = relationship(
        back_populates="cycle"
    )
    operational_inputs: Mapped[List["OperationalEvaluationInput"]] = relationship(
        back_populates="cycle"
    )
    impact_inputs: Mapped[List["ImpactEvaluationInput"]] = relationship(
        back_populates="cycle"
    )
    kpi_details: Mapped[List["ScorecardKpiDetail"]] = relationship(
        back_populates="cycle"
    )
    input_otd: Mapped[List["InputOtdMonthly"]] = relationship(back_populates="cycle")
    input_quality_claims: Mapped[List["InputQualityClaims"]] = relationship(
        back_populates="cycle"
    )
    input_delivery_spend: Mapped[List["InputDeliverySpend"]] = relationship(
        back_populates="cycle"
    )
    assessments: Mapped[List["SupplierAssessment"]] = relationship(
        back_populates="cycle"
    )
    action_plans: Mapped[List["SupplierActionPlan"]] = relationship(
        back_populates="cycle"
    )

    def __repr__(self) -> str:
        return (
            f"<EvaluationCycle id={self.id_cycle} "
            f"type={self.cycle_type!r} status={self.cycle_status!r}>"
        )


class ApprovalWorkflow(TimestampMixin, GovernanceMixin, Base):
    __tablename__ = "approval_workflow"
    __table_args__ = (Index("idx_approval_cycle", "id_cycle"),)

    id_approval: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    id_cycle: Mapped[Optional[int]] = mapped_column(
        ForeignKey("evaluation_cycle.id_cycle", ondelete="CASCADE"), nullable=True
    )
    object_type: Mapped[str] = mapped_column(String(100), nullable=False)
    object_id: Mapped[int] = mapped_column(Integer, nullable=False)
    approval_step: Mapped[int] = mapped_column(Integer, nullable=False)
    approver_role: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    approver_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    approver_email: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    # FIX: removed extra quotes from server_default
    decision: Mapped[str] = mapped_column(
        String(50), server_default="Pending", nullable=False
    )
    decision_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    cycle: Mapped[Optional["EvaluationCycle"]] = relationship(
        back_populates="approvals"
    )


class Escalation(TimestampMixin, GovernanceMixin, Base):
    __tablename__ = "escalation"
    __table_args__ = (Index("idx_escalation_cycle", "id_cycle"),)

    id_escalation: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
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
    # FIX: removed extra quotes from server_default
    status: Mapped[str] = mapped_column(
        String(50), server_default="Open", nullable=False
    )
    resolution_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    cycle: Mapped[Optional["EvaluationCycle"]] = relationship(
        back_populates="escalations"
    )


class ScoreCard(AuditMixin, GovernanceMixin, Base):
    __tablename__ = "score_card"

    id_score_card: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    id_relation: Mapped[Optional[int]] = mapped_column(
        ForeignKey("supplier_site_relation.id_relation", ondelete="CASCADE"),
        nullable=True,
    )
    scorecard_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    grade: Mapped[Optional[str]] = mapped_column(CHAR(1), nullable=True)
    id_cycle: Mapped[Optional[int]] = mapped_column(
        ForeignKey("evaluation_cycle.id_cycle", ondelete="SET NULL"), nullable=True
    )

    relation: Mapped[Optional["SupplierSiteRelation"]] = relationship(
        back_populates="score_cards"
    )
    cycle: Mapped[Optional["EvaluationCycle"]] = relationship(
        back_populates="score_cards"
    )
    kpi_details: Mapped[List["ScorecardKpiDetail"]] = relationship(
        back_populates="score_card", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        return (
            f"<ScoreCard id={self.id_score_card} score={self.score} grade={self.grade}>"
        )


class Classification(AuditMixin, GovernanceMixin, Base):
    __tablename__ = "classification"

    id_classification: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    id_relation: Mapped[Optional[int]] = mapped_column(
        ForeignKey("supplier_site_relation.id_relation", ondelete="CASCADE"),
        nullable=True,
    )
    classification_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    classification_score: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    class_value: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    operational_score: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    operational_grade: Mapped[Optional[str]] = mapped_column(CHAR(1), nullable=True)
    final_grade: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    impact_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    strategic_mention: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    panel_decision: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    id_cycle: Mapped[Optional[int]] = mapped_column(
        ForeignKey("evaluation_cycle.id_cycle", ondelete="SET NULL"), nullable=True
    )

    relation: Mapped[Optional["SupplierSiteRelation"]] = relationship(
        back_populates="classifications"
    )
    cycle: Mapped[Optional["EvaluationCycle"]] = relationship(
        back_populates="classifications"
    )


class PldClassEvaluationInput(AuditMixin, GovernanceMixin, Base):
    __tablename__ = "pld_class_evaluation_input"
    __table_args__ = (
        Index("idx_pld_class_input_relation_cycle", "id_relation", "id_cycle"),
    )

    id_pld_input: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    id_relation: Mapped[int] = mapped_column(
        ForeignKey("supplier_site_relation.id_relation", ondelete="CASCADE"),
        nullable=False,
    )
    id_cycle: Mapped[Optional[int]] = mapped_column(
        ForeignKey("evaluation_cycle.id_cycle", ondelete="SET NULL"), nullable=True
    )
    top: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    lta: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    productivity: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Historical pointer to the certification considered current when this evaluation
    # snapshot was recorded. NOT the source of truth for "current status" displays --
    # those always re-derive live from SupplierCertification (see
    # SupplierRelationService._get_best_quality_cert_for_unit) so an expired cert can
    # never keep inflating a score just because nobody re-saved the evaluation.
    id_certification: Mapped[Optional[int]] = mapped_column(
        ForeignKey("supplier_certification.id_certification", ondelete="SET NULL"),
        nullable=True,
    )
    prod_lia_ins: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    competitiveness: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    sqma: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    family_coverage: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    geo_coverage: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    cons_or_wd: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    financial_health: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    class_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    class_value: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    impact_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    strategic_mention: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    panel_decision: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    relation: Mapped["SupplierSiteRelation"] = relationship(
        back_populates="pld_class_inputs"
    )
    certification: Mapped[Optional["SupplierCertification"]] = relationship()
    cycle: Mapped[Optional["EvaluationCycle"]] = relationship(
        back_populates="pld_class_inputs"
    )


class PldClassCriteriaDetail(AuditMixin, GovernanceMixin, Base):
    __tablename__ = "pld_class_criteria_detail"
    __table_args__ = (
        Index("idx_pld_class_criteria_relation_cycle", "id_relation", "id_cycle"),
    )

    id_detail: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    id_relation: Mapped[int] = mapped_column(
        ForeignKey("supplier_site_relation.id_relation", ondelete="CASCADE"),
        nullable=False,
    )
    id_cycle: Mapped[Optional[int]] = mapped_column(
        ForeignKey("evaluation_cycle.id_cycle", ondelete="SET NULL"), nullable=True
    )
    id_document: Mapped[Optional[int]] = mapped_column(
        ForeignKey("document.id_document", ondelete="SET NULL"), nullable=True
    )
    criteria_type: Mapped[str] = mapped_column(String(100), nullable=False)
    selected_value: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    evidence_file_name: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    validity_start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    validity_end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    signature_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    last_update_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    amount_value: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    amount_currency: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    auto_validity_end_date: Mapped[bool] = mapped_column(
        Boolean, server_default="false", nullable=False
    )


class OperationalEvaluationInput(AuditMixin, GovernanceMixin, Base):
    __tablename__ = "operational_evaluation_input"
    __table_args__ = (
        Index("idx_operational_input_relation_cycle", "id_relation", "id_cycle"),
    )

    id_operational_input: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    id_relation: Mapped[int] = mapped_column(
        ForeignKey("supplier_site_relation.id_relation", ondelete="CASCADE"),
        nullable=False,
    )
    id_cycle: Mapped[Optional[int]] = mapped_column(
        ForeignKey("evaluation_cycle.id_cycle", ondelete="SET NULL"), nullable=True
    )
    source_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    management_system: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    customer_communication: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    development_design: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    production_manufacturing: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    quality_audits: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    suppliers_subcontractors: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    deliveries: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    environment_ethic_rules: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    average_score: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    operational_grade: Mapped[Optional[str]] = mapped_column(CHAR(1), nullable=True)

    relation: Mapped["SupplierSiteRelation"] = relationship(
        back_populates="operational_inputs"
    )
    cycle: Mapped[Optional["EvaluationCycle"]] = relationship(
        back_populates="operational_inputs"
    )


class ImpactEvaluationInput(AuditMixin, GovernanceMixin, Base):
    __tablename__ = "impact_evaluation_input"
    __table_args__ = (
        Index("idx_impact_input_relation_cycle", "id_relation", "id_cycle"),
    )

    id_impact_input: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    id_relation: Mapped[int] = mapped_column(
        ForeignKey("supplier_site_relation.id_relation", ondelete="CASCADE"),
        nullable=False,
    )
    id_cycle: Mapped[Optional[int]] = mapped_column(
        ForeignKey("evaluation_cycle.id_cycle", ondelete="SET NULL"), nullable=True
    )
    question_1: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    question_2: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    question_3: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    question_4: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    question_5: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    question_6: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    impact_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    relation: Mapped["SupplierSiteRelation"] = relationship(
        back_populates="impact_inputs"
    )
    cycle: Mapped[Optional["EvaluationCycle"]] = relationship(
        back_populates="impact_inputs"
    )


class ScorecardKpiDetail(TimestampMixin, GovernanceMixin, Base):
    __tablename__ = "scorecard_kpi_detail"
    __table_args__ = (Index("idx_scorecard_kpi_cycle", "id_cycle"),)

    id_kpi_detail: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
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
        ForeignKey("supplier_site_relation.id_relation", ondelete="SET NULL"),
        nullable=True,
    )
    kpi_name: Mapped[str] = mapped_column(String(150), nullable=False)
    kpi_category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    kpi_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4), nullable=True)
    kpi_unit: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    kpi_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    weight: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    weighted_score: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    source_dataset: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    score_card: Mapped[Optional["ScoreCard"]] = relationship(
        back_populates="kpi_details"
    )
    cycle: Mapped[Optional["EvaluationCycle"]] = relationship(
        back_populates="kpi_details"
    )
    relation: Mapped[Optional["SupplierSiteRelation"]] = relationship(
        back_populates="scorecard_kpi_details"
    )


class PldScoringRules(Base):
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


class InputOtdMonthly(TimestampMixin, GovernanceMixin, Base):
    __tablename__ = "input_otd_monthly"
    __table_args__ = (Index("idx_input_otd_monthly_cycle", "id_cycle"),)

    id_otd: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_cycle: Mapped[Optional[int]] = mapped_column(
        ForeignKey("evaluation_cycle.id_cycle", ondelete="SET NULL"), nullable=True
    )
    id_document: Mapped[Optional[int]] = mapped_column(
        ForeignKey("document.id_document", ondelete="SET NULL"), nullable=True
    )
    id_relation: Mapped[Optional[int]] = mapped_column(
        ForeignKey("supplier_site_relation.id_relation", ondelete="SET NULL"),
        nullable=True,
    )
    supplier_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    supplier_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    month_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    otd_raw_value: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    otd_percent: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    plant: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    # FIX: BigInteger to match import_batch.id_import_batch PK type
    id_import_batch: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("import_batch.id_import_batch", ondelete="SET NULL"),
        nullable=True,
    )
    source_row_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source_row_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    cycle: Mapped[Optional["EvaluationCycle"]] = relationship(
        back_populates="input_otd"
    )
    relation: Mapped[Optional["SupplierSiteRelation"]] = relationship(
        back_populates="input_otd"
    )
    import_batch: Mapped[Optional["ImportBatch"]] = relationship(
        back_populates="input_otd_rows"
    )


class InputQualityClaims(TimestampMixin, GovernanceMixin, Base):
    __tablename__ = "input_quality_claims"
    __table_args__ = (Index("idx_input_quality_claims_cycle", "id_cycle"),)

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
        ForeignKey("supplier_site_relation.id_relation", ondelete="SET NULL"),
        nullable=True,
    )
    claimed_part_reference: Mapped[Optional[str]] = mapped_column(
        String(150), nullable=True
    )
    claim_number: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    claim_opening_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    claim_closing_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    claim_status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    supplier_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    supplier_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    plant: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    # FIX: BigInteger to match import_batch.id_import_batch PK type
    id_import_batch: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("import_batch.id_import_batch", ondelete="SET NULL"),
        nullable=True,
    )
    source_row_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source_row_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    cycle: Mapped[Optional["EvaluationCycle"]] = relationship(
        back_populates="input_quality_claims"
    )
    relation: Mapped[Optional["SupplierSiteRelation"]] = relationship(
        back_populates="input_quality_claims"
    )
    import_batch: Mapped[Optional["ImportBatch"]] = relationship(
        back_populates="input_quality_claim_rows"
    )


class InputDeliverySpend(TimestampMixin, GovernanceMixin, Base):
    __tablename__ = "input_delivery_spend"
    __table_args__ = (Index("idx_input_delivery_spend_cycle", "id_cycle"),)

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
        ForeignKey("supplier_site_relation.id_relation", ondelete="SET NULL"),
        nullable=True,
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
    # FIX: BigInteger to match import_batch.id_import_batch PK type
    id_import_batch: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("import_batch.id_import_batch", ondelete="SET NULL"),
        nullable=True,
    )
    source_row_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source_row_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    cycle: Mapped[Optional["EvaluationCycle"]] = relationship(
        back_populates="input_delivery_spend"
    )
    relation: Mapped[Optional["SupplierSiteRelation"]] = relationship(
        back_populates="input_delivery_spend"
    )
    import_batch: Mapped[Optional["ImportBatch"]] = relationship(
        back_populates="input_delivery_spend_rows"
    )


# ---------------------------------------------------------------------------
# Domain 3 — Supplier Self-Assessment Module
# ---------------------------------------------------------------------------


class AssessmentTemplate(TimestampMixin, GovernanceMixin, Base):
    __tablename__ = "assessment_template"

    id_template: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    template_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # FIX: removed extra quotes from server_default
    template_type: Mapped[str] = mapped_column(
        String(100), server_default="SELF_ASSESSMENT", nullable=False
    )
    version: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # FIX: removed extra quotes from server_default
    status: Mapped[str] = mapped_column(
        String(50), server_default="Active", nullable=False
    )
    id_document: Mapped[Optional[int]] = mapped_column(
        ForeignKey("document.id_document", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

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


class AssessmentTemplateFieldMapping(TimestampMixin, GovernanceMixin, Base):
    __tablename__ = "assessment_template_field_mapping"

    id_mapping: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    id_template: Mapped[int] = mapped_column(
        ForeignKey("assessment_template.id_template", ondelete="CASCADE"),
        nullable=False,
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

    template: Mapped["AssessmentTemplate"] = relationship(
        back_populates="field_mappings"
    )
    answers: Mapped[List["SupplierAssessmentAnswer"]] = relationship(
        back_populates="mapping"
    )

    def __repr__(self) -> str:
        return (
            f"<FieldMapping id={self.id_mapping} "
            f"field={self.field_code!r} sheet={self.sheet_name!r}>"
        )


class SupplierAssessment(TimestampMixin, GovernanceMixin, Base):
    __tablename__ = "supplier_assessment"

    id_assessment: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    id_relation: Mapped[Optional[int]] = mapped_column(
        ForeignKey("supplier_site_relation.id_relation", ondelete="SET NULL"),
        nullable=True,
    )
    id_template: Mapped[Optional[int]] = mapped_column(
        ForeignKey("assessment_template.id_template", ondelete="SET NULL"),
        nullable=True,
    )
    id_document: Mapped[Optional[int]] = mapped_column(
        ForeignKey("document.id_document", ondelete="SET NULL"), nullable=True
    )
    id_cycle: Mapped[Optional[int]] = mapped_column(
        ForeignKey("evaluation_cycle.id_cycle", ondelete="SET NULL"), nullable=True
    )
    assessment_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    submitted_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    # FIX: removed extra quotes from server_default
    status: Mapped[str] = mapped_column(
        String(50), server_default="Received", nullable=False
    )
    final_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    final_grade: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    final_class: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    relation: Mapped[Optional["SupplierSiteRelation"]] = relationship(
        back_populates="assessments"
    )
    template: Mapped[Optional["AssessmentTemplate"]] = relationship(
        back_populates="assessments"
    )
    document: Mapped[Optional["Document"]] = relationship(back_populates="assessments")
    cycle: Mapped[Optional["EvaluationCycle"]] = relationship(
        back_populates="assessments"
    )
    answers: Mapped[List["SupplierAssessmentAnswer"]] = relationship(
        back_populates="assessment", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        return (
            f"<SupplierAssessment id={self.id_assessment} "
            f"status={self.status!r} score={self.final_score}>"
        )


class SupplierAssessmentAnswer(TimestampMixin, GovernanceMixin, Base):
    __tablename__ = "supplier_assessment_answer"

    id_answer: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    id_assessment: Mapped[int] = mapped_column(
        ForeignKey("supplier_assessment.id_assessment", ondelete="CASCADE"),
        nullable=False,
    )
    id_mapping: Mapped[Optional[int]] = mapped_column(
        ForeignKey("assessment_template_field_mapping.id_mapping", ondelete="SET NULL"),
        nullable=True,
    )
    field_code: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    field_label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    raw_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    normalized_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    is_valid: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    validation_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

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
# Domain 3b — Production Governance / IATF Evidence
# ---------------------------------------------------------------------------


class AuditEvent(Base):
    __tablename__ = "audit_event"
    __table_args__ = (
        Index("idx_audit_event_table_record", "table_name", "record_pk"),
        Index("idx_audit_event_changed_at", "changed_at"),
        Index("idx_audit_event_correlation", "correlation_id"),
    )

    id_audit_event: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    event_uuid: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False), server_default=func.gen_random_uuid(), nullable=False
    )
    table_name: Mapped[str] = mapped_column(String(150), nullable=False)
    record_pk: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    changed_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False
    )
    old_values: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    new_values: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    reason_code: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    reason_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_system: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    source_ip: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    correlation_id: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    batch_id: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    is_system_event: Mapped[bool] = mapped_column(
        Boolean, server_default="false", nullable=False
    )


class ImportBatch(Base):
    __tablename__ = "import_batch"
    __table_args__ = (
        Index("idx_import_batch_document", "id_document"),
        Index("idx_import_batch_uuid", "batch_uuid", unique=True),
    )

    id_import_batch: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    batch_uuid: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False), server_default=func.gen_random_uuid(), nullable=False
    )
    source_name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    id_document: Mapped[Optional[int]] = mapped_column(
        ForeignKey("document.id_document", ondelete="SET NULL"), nullable=True
    )
    uploaded_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # FIX: removed extra quotes from server_default
    status: Mapped[str] = mapped_column(
        String(50), server_default="Pending", nullable=False
    )
    records_total: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    records_inserted: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    records_rejected: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    file_hash_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    validation_summary: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    error_details: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    document: Mapped[Optional["Document"]] = relationship(
        back_populates="import_batches"
    )
    input_otd_rows: Mapped[List["InputOtdMonthly"]] = relationship(
        back_populates="import_batch"
    )
    input_quality_claim_rows: Mapped[List["InputQualityClaims"]] = relationship(
        back_populates="import_batch"
    )
    input_delivery_spend_rows: Mapped[List["InputDeliverySpend"]] = relationship(
        back_populates="import_batch"
    )


class SupplierActionPlan(GovernanceMixin, Base):
    """
    FIX: now inherits GovernanceMixin instead of duplicating columns inline.
    The fix migration adds the missing deleted_at, deleted_by, row_version columns.
    The manually declared updated_at, updated_by, is_deleted are removed in favour
    of the mixin which also provides deleted_at, deleted_by, row_version.
    """

    __tablename__ = "supplier_action_plan"
    __table_args__ = (
        Index("idx_action_plan_relation", "id_relation"),
        Index("idx_action_plan_cycle", "id_cycle"),
        Index("idx_action_plan_status", "status"),
    )

    id_action_plan: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    id_relation: Mapped[Optional[int]] = mapped_column(
        ForeignKey("supplier_site_relation.id_relation", ondelete="SET NULL"),
        nullable=True,
    )
    id_cycle: Mapped[Optional[int]] = mapped_column(
        ForeignKey("evaluation_cycle.id_cycle", ondelete="SET NULL"), nullable=True
    )
    id_document: Mapped[Optional[int]] = mapped_column(
        ForeignKey("document.id_document", ondelete="SET NULL"), nullable=True
    )
    trigger_type: Mapped[str] = mapped_column(String(100), nullable=False)
    trigger_reference: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    problem_statement: Mapped[str] = mapped_column(Text, nullable=False)
    containment_action: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    root_cause: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    corrective_action: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    preventive_action: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    owner: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    supplier_owner: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    # FIX: removed extra quotes from server_default
    status: Mapped[str] = mapped_column(
        String(50), server_default="Open", nullable=False
    )
    effectiveness_check_required: Mapped[bool] = mapped_column(
        Boolean, server_default="true", nullable=False
    )
    effectiveness_result: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )
    closed_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False
    )

    relation: Mapped[Optional["SupplierSiteRelation"]] = relationship(
        back_populates="action_plans"
    )
    cycle: Mapped[Optional["EvaluationCycle"]] = relationship(
        back_populates="action_plans"
    )
    document: Mapped[Optional["Document"]] = relationship()
    tasks: Mapped[List["SupplierActionPlanTask"]] = relationship(
        back_populates="action_plan", cascade="all, delete-orphan", passive_deletes=True
    )


class SupplierActionPlanTask(Base):
    __tablename__ = "supplier_action_plan_task"

    id_task: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    id_action_plan: Mapped[int] = mapped_column(
        ForeignKey("supplier_action_plan.id_action_plan", ondelete="CASCADE"),
        nullable=False,
    )
    task_description: Mapped[str] = mapped_column(Text, nullable=False)
    task_owner: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    # FIX: removed extra quotes from server_default
    status: Mapped[str] = mapped_column(
        String(50), server_default="Open", nullable=False
    )
    completion_evidence_document_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("document.id_document", ondelete="SET NULL"), nullable=True
    )
    completed_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    action_plan: Mapped["SupplierActionPlan"] = relationship(back_populates="tasks")
    completion_evidence_document: Mapped[Optional["Document"]] = relationship()


class UserRoleAssignment(Base):
    __tablename__ = "user_role_assignment"
    __table_args__ = (
        UniqueConstraint(
            "user_email",
            "role_name",
            "scope_type",
            "scope_id",
            name="uq_user_role_scope",
        ),
    )

    id_user_role: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    user_email: Mapped[str] = mapped_column(String(200), nullable=False)
    role_name: Mapped[str] = mapped_column(String(150), nullable=False)
    scope_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    scope_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    valid_from: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    valid_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, server_default="true", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False
    )


# ---------------------------------------------------------------------------
# Domain 4 — Opportunity / Project / Financial Savings
# ---------------------------------------------------------------------------


class Opportunity(GovernanceMixin, Base):
    __tablename__ = "opportunity"

    opportunity_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=True
    )
    created_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    opportunity_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    opportunity_type: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    idea_owner: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    purchasing_owner: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    project_owner: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    conversion_owner: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    committee_level: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # Light | Intermediate | Full — chosen at the Phase 1 gate, locked for Phase 2-4
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
    duration_months: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(6, 2), nullable=True
    )
    budget_year: Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 0), nullable=True)
    phase_status: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    validation_decision: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    change_mode: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Transaction currency of the monetary figures + rate to the group reporting
    # currency (EUR). Consolidated views convert via amount × fx_rate_to_eur.
    currency: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    fx_rate_to_eur: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6), nullable=True)
    assumptions_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # PLD prioritization — P × L × D scoring (1–5 each, max 125)
    payback_score: Mapped[Optional[Decimal]] = mapped_column(
        "payback_score", Numeric(10, 2), nullable=True
    )
    lead_time_score: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    difficulty_score: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    priority_score: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    priority_category: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    priority_locked: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True, default=False)
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cash_impact: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    validation_request_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    validation_request_sent_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    # Derived workflow maturity / validation tracking. This is distinct from the
    # manual per-fiscal-year budget decision stored on OpportunityBudgetYear.
    validation_status: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )
    budget_confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    budget_confirmed_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    val_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    # Computed end date: planned_start_date + duration_months
    planned_end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    # Study start date = when buyer clicked "Start Study" (Assigned → Working on it)
    study_start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    # Phase 2: when execution work began (tooling ordered, supplier contacted)
    execution_start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    # Phase 3: when savings actually started flowing (PPAP done, Longrun parts in production)
    # real_start_date already exists above — this is the trigger for R9 monthly profile rebuild
    # STP / scope fields
    scope_in: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    scope_out: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    customers: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    annual_quantity_n1: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    annual_quantity_n2: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    annual_quantity_n3: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    annual_quantity_n4: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Supplier before/after (for Sourcing)
    proposed_supplier_name: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    proposed_supplier_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("supplier_unit.id_supplier_unit", ondelete="SET NULL"), nullable=True
    )
    current_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6), nullable=True)
    proposed_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6), nullable=True)
    # Price projections N+1, N+2, N+3
    proposed_price_n1: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6), nullable=True)
    proposed_price_n2: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6), nullable=True)
    proposed_price_n3: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6), nullable=True)
    # Supplier logistics details (Before / After for STP)
    # Incoterms, TOP, Transit — not stored anywhere else in the DB → stored here
    incoterms_before: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    incoterms_after: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    place_of_incoterms_before: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    place_of_incoterms_after: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    top_days_before: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    top_days_after: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    transit_days_before: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    transit_days_after: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # country_before is NOT stored — read from SupplierUnit.country via supplier_id
    # country_after is for the new supplier not yet in the panel
    country_after: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    bonus_before: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    bonus_after: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    # Initial step: has the current supplier been formally asked?
    supplier_asked: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    supplier_asked_result: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Investment costs
    tooling_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    travel_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    qualification_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    total_investment: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    # ROI per Excel "format STP rev 1.2": full year F51 = (D51-D45)/D51, period F52 = (D52-D41)/D41
    roi_percent: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    roi_period_percent: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    # EBITDA savings "Period" (Excel D52) — 4-year saving over quantities N1..N4
    period_saving: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    # Estimated saving per year (Excel D52 broken out). saving_year_n == expected_annual_saving
    # (year N, incl. bonus); the four columns sum to period_saving.
    saving_year_n: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    saving_year_n1: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    saving_year_n2: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    saving_year_n3: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    # Calendar-year prorated estimate {"2026": 1234.56, ...}, anchored on planned_start_date.
    # A mid-year start puts a partial amount in the first calendar year, remainder rolls forward.
    saving_by_year: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    # Cash savings components — auto-computed (Excel D55/D56)
    cash_inventory_gap: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    cash_ap_gap: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    # Consignment (Yes/No) — used in inventory gap formula
    consignment_before: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    consignment_after: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    # Before-prices for years N+1, N+2, N+3 (current supplier price evolution)
    current_price_n1: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6), nullable=True)
    current_price_n2: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6), nullable=True)
    current_price_n3: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6), nullable=True)
    # 4th investment cost line ("Other")
    other_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    # Risks — JSONB: keys material_indexation/exchange_rate/local_content/quality/other (before+after)
    # plus spec questions: material_same_spec / same_tooling / same_dimension (Yes/No)
    stp_risks: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    # Benefits narrative — JSONB: keys if_we_do / if_not
    stp_benefits: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    # Planning (weeks per phase)
    phase1_weeks: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    phase2_weeks: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    phase3_weeks: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    phase4_weeks: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Why checkboxes
    reason_productivity: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    reason_quality: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    reason_capacity: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    reason_other: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # Excel Phase 0 row 8 — secondary Avocarbon plants impacted (free text, comma-separated)
    secondary_plants: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # STP revision approval — JSONB stores a pending director-approval request while
    # current values remain active.  Cleared on approve or reject.
    pending_stp_revision: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    # Structured audit trail of every committed baseline correction (Revise Baseline,
    # post-actuals). Each entry: {revised_at, revised_by, note, opportunity_type,
    # previous_fields, new_fields, previous_computed, new_computed}. Append-only,
    # newest last — never rewritten or trimmed, so it's a permanent history.
    revision_history: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)

    plant: Mapped[Optional["AvocarbonSite"]] = relationship(
        back_populates="opportunities"
    )
    supplier: Mapped[Optional["SupplierUnit"]] = relationship(
        back_populates="opportunities",
        foreign_keys=[supplier_id],
    )
    proposed_supplier: Mapped[Optional["SupplierUnit"]] = relationship(
        foreign_keys=[proposed_supplier_id],
    )
    projects: Mapped[List["Project"]] = relationship(
        back_populates="opportunity", cascade="all, delete-orphan", passive_deletes=True
    )
    financial_lines: Mapped[List["FinancialLine"]] = relationship(
        back_populates="opportunity", cascade="all, delete-orphan", passive_deletes=True
    )
    opp_documents: Mapped[List["OpportunityDocument"]] = relationship(
        back_populates="opportunity", cascade="all, delete-orphan", passive_deletes=True
    )
    budget_years: Mapped[List["OpportunityBudgetYear"]] = relationship(
        back_populates="opportunity", cascade="all, delete-orphan", passive_deletes=True
    )

    phase_snapshots: Mapped[List["OpportunityPhaseSnapshot"]] = relationship(
        back_populates="opportunity", cascade="all, delete-orphan", passive_deletes=True
    )

    gate_approval_requests: Mapped[List["GateApprovalRequest"]] = relationship(
        "GateApprovalRequest",
        back_populates="opportunity",
        cascade="all, delete-orphan",
        order_by="GateApprovalRequest.requested_at.desc()",
        lazy="selectin",
    )

    action_plans: Mapped[List["OpportunityActionPlan"]] = relationship(
        "OpportunityActionPlan",
        back_populates="opportunity",
        cascade="all, delete-orphan",
        order_by="OpportunityActionPlan.created_at.desc()",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Opportunity id={self.opportunity_id} name={self.opportunity_name!r}>"


class OpportunityPhaseSnapshot(GovernanceMixin, Base):
    """Immutable audit record captured at every gate decision (Go / No Go / Review)."""

    __tablename__ = "opportunity_phase_snapshot"

    snapshot_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("opportunity.opportunity_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    phase_from: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    phase_to: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    gate_decision: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    decided_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    gate_comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    opportunity_snapshot: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    opportunity: Mapped["Opportunity"] = relationship(back_populates="phase_snapshots")


class GateApprovalRequest(GovernanceMixin, Base):
    """One per gate submission — groups all approver votes."""
    __tablename__ = "gate_approval_request"

    request_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("opportunity.opportunity_id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    phase_from: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    requested_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    requested_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, default="Pending")
    # Pending | Completed | Expired
    consensus_result: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # Go | No Go | Review — set when all votes in
    committee_level: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # Light | Intermediate | Full — null for Phase 0 requests
    applied_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    opportunity_snapshot: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    votes: Mapped[List["GateApprovalVote"]] = relationship(
        "GateApprovalVote",
        back_populates="request",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    opportunity: Mapped["Opportunity"] = relationship(
        "Opportunity", back_populates="gate_approval_requests", lazy="selectin"
    )


class GateApprovalVote(GovernanceMixin, Base):
    """One per approver per gate approval request."""
    __tablename__ = "gate_approval_vote"

    vote_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("gate_approval_request.request_id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    approver_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    access_token: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, unique=True, index=True)
    token_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    accessed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    is_plant_manager: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True, default=False)
    approver_role: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # Business role for Phase 1-4 committee votes (e.g. "Purchasing Director") — null for Phase 0 votes
    decision: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # Approved | Rejected | Needs Review
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    project_manager_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    request: Mapped["GateApprovalRequest"] = relationship(
        "GateApprovalRequest", back_populates="votes"
    )


class OpportunityActionPlan(Base):
    """Action plan linked to an opportunity phase, mirrored to the enterprise action plan service."""

    __tablename__ = "opportunity_action_plan"

    action_plan_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("opportunity.opportunity_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    phase_status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    plan_title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    plan_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    # Full PlanV2 payload stored locally so reads don't require a call to the external API
    plan_data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    external_push_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # "ok" | "failed" | "pending"
    external_push_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    opportunity: Mapped["Opportunity"] = relationship(
        "Opportunity", back_populates="action_plans"
    )


class Project(GovernanceMixin, Base):
    __tablename__ = "project"

    project_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
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
    # Phase outputs
    phase_output_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    off_tool_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    committee_review_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    committee_members: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Change type — per-phase value set in the Project tab
    change_mode: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    change_mode_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    opportunity: Mapped[Optional["Opportunity"]] = relationship(
        back_populates="projects"
    )
    financial_lines: Mapped[List["FinancialLine"]] = relationship(
        back_populates="project"
    )

    def __repr__(self) -> str:
        return f"<Project id={self.project_id} name={self.project_name!r}>"


class FinancialLine(GovernanceMixin, Base):
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
    budget_value: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    real_start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    duration_months: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(6, 2), nullable=True
    )
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
    forecast_eoy_last_update: Mapped[Optional[date]] = mapped_column(
        Date, nullable=True
    )
    expected_annual_saving: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    # Per-component tracking (Gap 2 — one line per part number)
    component_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    component_pn: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    # Escalation
    is_escalated: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    escalated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    escalated_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    escalation_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Recovery
    recovery_status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    recovery_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    recovery_target_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    recovery_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    recovery_history: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    recovery_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    recovery_updated_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    recovery_baseline_gap: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    recovery_baseline_set_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )
    # H2 — cooldown: tracks when the last "missing actuals" delay-alert email was sent
    delay_alert_last_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    opportunity: Mapped["Opportunity"] = relationship(back_populates="financial_lines")
    project: Mapped[Optional["Project"]] = relationship(
        back_populates="financial_lines"
    )
    plant: Mapped["AvocarbonSite"] = relationship(
        back_populates="financial_lines", foreign_keys=[plant_id]
    )
    monthly_financials: Mapped[List["MonthlyFinancial"]] = relationship(
        back_populates="financial_line",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class MonthlyFinancial(GovernanceMixin, Base):
    __tablename__ = "monthly_financial"

    monthly_financial_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    financial_line_id: Mapped[int] = mapped_column(
        ForeignKey("financial_line.financial_line_id", ondelete="CASCADE"),
        nullable=False,
    )
    period_month: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    expected_saving: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    actual_saving: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    cumulated_expected: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    cumulated_actual: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    delta_vs_expected: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    delta_vs_budget: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    forecast_eoy_saving: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    forecast_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Monthly review outcome: Continue / Recover / Escalate
    monthly_outcome: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # Gap 3 — Cash monthly tracking (for Negotiation / Cash type opportunities)
    cash_expected: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    cash_actual: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    cumulated_cash_actual: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)

    financial_line: Mapped["FinancialLine"] = relationship(
        back_populates="monthly_financials"
    )


class OpportunityDocument(TimestampMixin, Base):
    """File attached to an opportunity — Phase 0/1/2/3/4 or general."""

    __tablename__ = "opportunity_document"

    doc_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("opportunity.opportunity_id", ondelete="CASCADE"), nullable=False
    )
    phase_label: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    file_name: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    original_file_name: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    file_url: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
    mime_type: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    file_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    uploaded_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    opportunity: Mapped["Opportunity"] = relationship(back_populates="opp_documents")


class OpportunityBudgetYear(GovernanceMixin, Base):
    """One budget record per opportunity per fiscal year.

    The opportunity's annual saving is pro-rata-split across the fiscal years its
    project runs (see compute_budget_year_portions). `budget_status` is the per-year
    buyer decision (Empty | Opportunity | Budgeted) and is NEVER overwritten by the
    recompute; `suggested_status` is the phase-derived default. Source of truth for
    the budgeting module; `Opportunity.validation_status` is a derived rollup.
    """

    __tablename__ = "opportunity_budget_year"
    __table_args__ = (
        UniqueConstraint("opportunity_id", "fiscal_year", name="uq_oby_opp_year"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("opportunity.opportunity_id", ondelete="CASCADE"), nullable=False
    )
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    # Pro-rata saving landing in this fiscal year
    applicable_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    # "Applicable" (partial start) | "Total" (full year) | "Residual" (partial tail)
    portion_kind: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # Phase-derived default ("Opportunity" pre-Phase 3, "Budgeted" Phase 3+)
    suggested_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # Buyer decision — overrides the suggestion, never clobbered by recompute
    budget_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    status_locked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status_locked_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    # Explanation of delta between EOY forecast and budget baseline for KPI chart
    # Allowed values: see DELTA_REASON_VALUES in schemas.py; multi-valued (TEXT[])
    delta_reason: Mapped[Optional[list]] = mapped_column(ARRAY(Text), nullable=True)
    # True when this row was created AFTER the budget for fiscal_year was officially closed.
    # Such rows represent post-closure additions ("Additional Opportunity") and are shown
    # separately in the Budgeting page to preserve the historical baseline integrity.
    is_additional: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )

    opportunity: Mapped["Opportunity"] = relationship(back_populates="budget_years")


class BudgetYearClosure(Base):
    """Records when a director officially closes a fiscal-year budget.

    Once a FY is closed, any new OpportunityBudgetYear row created for that FY is
    automatically flagged `is_additional = True` so Finance can track post-closure
    additions separately from the original committed baseline.
    """

    __tablename__ = "budget_year_closure"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    closed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    closed_by: Mapped[str] = mapped_column(String(200), nullable=False)


class EmailDeliveryHistory(Base):
    __tablename__ = "email_delivery_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    recipient_email: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        index=True,
    )

    subject: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    delivery_status: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        index=True,
    )

    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )


# ---------------------------------------------------------------------------
# __all__  (single definition — FIX: removed the duplicate at top of file)
# ---------------------------------------------------------------------------

__all__ = [
    "Base",
    # Mixins
    "TimestampMixin",
    "AuditMixin",
    "GovernanceMixin",  # FIX: was missing from the original bottom __all__
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
    "SupplierDevelopmentPlan",
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
    "PldClassEvaluationInput",
    "PldClassCriteriaDetail",
    "OperationalEvaluationInput",
    "ImpactEvaluationInput",
    "ScorecardKpiDetail",
    "PldScoringRules",
    "InputOtdMonthly",
    "InputQualityClaims",
    "InputDeliverySpend",
    # Domain 3 — Assessment
    "AssessmentTemplate",
    "AssessmentTemplateFieldMapping",
    "SupplierAssessment",
    "SupplierAssessmentAnswer",
    # Domain 3b — Governance / IATF Evidence
    "AuditEvent",
    "ImportBatch",
    "SupplierActionPlan",
    "SupplierActionPlanTask",
    "UserRoleAssignment",
    # Domain 4 — Opportunity / Financial
    "Opportunity",
    "Project",
    "FinancialLine",
    "MonthlyFinancial",
    # Domain 5 — Committee Review
    "CommitteeMember",
    "CommitteeReview",
    "CommitteeDecision",
]


# ---------------------------------------------------------------------------
# Domain 5 — Committee Review Workflow
# ---------------------------------------------------------------------------


class CommitteeMember(Base):
    """Configurable list of committee members who vote on panel decisions."""
    __tablename__ = "committee_member"

    id_member: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    position: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=True
    )


class CommitteeReview(Base):
    """One review process per relation per committee cycle."""
    __tablename__ = "committee_review"

    id_review: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_relation: Mapped[int] = mapped_column(
        ForeignKey("supplier_site_relation.id_relation", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # pending | in_progress | completed
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    initiated_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    initiated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=True
    )
    all_decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # approved | rejected
    final_decision: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    final_decision_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    final_decision_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    final_decision_comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # snapshot of supplier info sent with emails
    supplier_snapshot: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    relation: Mapped["SupplierSiteRelation"] = relationship(
        "SupplierSiteRelation", back_populates="committee_reviews"
    )
    decisions: Mapped[List["CommitteeDecision"]] = relationship(
        "CommitteeDecision",
        back_populates="review",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class CommitteeDecision(Base):
    """One decision record per committee member per review."""
    __tablename__ = "committee_decision"

    id_decision: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_review: Mapped[int] = mapped_column(
        ForeignKey("committee_review.id_review", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    member_email: Mapped[str] = mapped_column(String(200), nullable=False)
    member_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    member_position: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # UUID token for secure public link
    access_token: Mapped[str] = mapped_column(String(36), nullable=False, unique=True, index=True)
    token_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    accessed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # approved | rejected | None (pending)
    decision: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    suggested_supplier_status: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    suggested_strategic_mention: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    review: Mapped["CommitteeReview"] = relationship(
        "CommitteeReview", back_populates="decisions"
    )

