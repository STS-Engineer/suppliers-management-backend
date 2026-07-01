"""Suppliers Pydantic schemas."""
from typing import Dict, List, Optional
from datetime import date, datetime
from decimal import Decimal
import re
from pydantic import BaseModel, Field, field_validator, model_validator

from app.features.suppliers.options import (
    CERTIFICATION_TYPE_OPTIONS,
    CONS_OR_WD_OPTIONS,
    FAMILY_COVERAGE_OPTIONS,
    FINANCIAL_HEALTH_OPTIONS,
    LTA_OPTIONS,
    PROD_LIA_INS_OPTIONS,
    PROD_OPTIONS,
    SQMA_OPTIONS,
    COMPETITIVENESS_OPTIONS,
    GEO_COVERAGE_OPTIONS,
    TOP_OPTIONS,
)


def _allowed_values(options):
    return {option["value"] for option in options}


EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _normalize_optional_email(value: Optional[str], field_name: str) -> Optional[str]:
    if value is None:
        return None
    trimmed = value.strip()
    if trimmed == "":
        return None
    if not EMAIL_PATTERN.match(trimmed):
        raise ValueError(f"{field_name} must be a valid email address")
    return trimmed


def _normalize_optional_string(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


TOP_VALUES = _allowed_values(TOP_OPTIONS)
LTA_VALUES = _allowed_values(LTA_OPTIONS)
SQMA_VALUES = _allowed_values(SQMA_OPTIONS)
FAMILY_COVERAGE_VALUES = _allowed_values(FAMILY_COVERAGE_OPTIONS)
CONS_OR_WD_VALUES = _allowed_values(CONS_OR_WD_OPTIONS)
FINANCIAL_HEALTH_VALUES = _allowed_values(FINANCIAL_HEALTH_OPTIONS)
CERTIFICATION_TYPE_VALUES = _allowed_values(CERTIFICATION_TYPE_OPTIONS)
COMPETITIVENESS_VALUES = _allowed_values(COMPETITIVENESS_OPTIONS)
GEO_COVERAGE_VALUES = _allowed_values(GEO_COVERAGE_OPTIONS)
PROD_LIA_INS_VALUES = _allowed_values(PROD_LIA_INS_OPTIONS)
PROD_VALUES = _allowed_values(PROD_OPTIONS)
STRATEGIC_MENTION_VALUES = {"strategic", "monopolistic", "directed", "none"}
PANEL_DECISION_VALUES = {
    "panel_add",
    "panel_add_exec_committee",
    "panel_add_committee_validated",
    "panel_reject",
}

# Operational class allowed values (A-D)
OPERATIONAL_CLASS_VALUES = {"A", "B", "C", "D"}


# ============================================================================
# SupplierGroup Schemas
# ============================================================================

class SupplierGroupBase(BaseModel):
    """Base supplier group schema."""
    nom: Optional[str] = Field(None, max_length=200, description="Supplier group name")
    supplier_scope: Optional[str] = Field(None, max_length=20, description="Scope of supplier (local/regional/global)")
    supplier_owner: Optional[str] = Field(None, max_length=200, description="Default supplier owner email for global groups")
    strategique: Optional[bool] = Field(None, description="Legacy compatibility flag now applied at unit level")
    monopolistique: Optional[bool] = Field(None, description="Legacy compatibility flag now applied at unit level")
    multi_site: Optional[bool] = Field(None, description="Does this supplier operate multiple sites?")
    directed: Optional[bool] = Field(False, description="Legacy compatibility flag now applied at unit level")
    exit_supplier: Optional[bool] = Field(False, description="Is this supplier in exit status?")
    strategic_reason: Optional[str] = Field(None, description="Reason for strategic classification")
    supplier_type: Optional[str | List[str]] = Field(None, description="Supplier category or categories")

    @field_validator("supplier_owner")
    @classmethod
    def validate_supplier_owner_email(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_optional_email(value, "supplier_owner")


class SupplierGroupCreate(SupplierGroupBase):
    """Schema for creating a new supplier group."""
    nom: str = Field(..., max_length=200, description="Supplier group name (required)")
    supplier_scope: str = Field(..., max_length=20, description="Scope of supplier: local, regional, or global (required)")


class SupplierGroupUpdate(SupplierGroupBase):
    """Schema for updating a supplier group."""
    nom: Optional[str] = Field(None, max_length=200)


class SupplierGroupResponse(SupplierGroupBase):
    """Response schema for supplier group."""
    id_group: int
    group_code: Optional[str] = None
    validation_status: str = "approved"

    class Config:
        from_attributes = True


class PendingValidationItem(BaseModel):
    """Summary of a supplier group awaiting purchasing-manager validation."""
    group_id: int
    group_name: Optional[str]
    group_code: Optional[str]
    validation_status: str
    unit_id: int
    unit_code: Optional[str]
    unit_country: Optional[str]
    relation_id: int
    site_id: int
    site_name: Optional[str]
    supplier_scope: Optional[str]
    supplier_owner: Optional[str]
    created_at: Optional[datetime]

    class Config:
        from_attributes = True


class ValidationDecisionRequest(BaseModel):
    comment: Optional[str] = Field(None, max_length=500)


# ============================================================================
# SupplierUnit Schemas
# ============================================================================

def _coerce_decimal(value: object) -> Optional[Decimal]:
    """Convert empty string or whitespace to None; otherwise parse as Decimal."""
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    return Decimal(str(value))


class SupplierUnitBase(BaseModel):
    """Base supplier unit schema."""
    supplier_code: Optional[str] = Field(None, max_length=50, description="Unique supplier code")
    address_line: Optional[str] = Field(None, max_length=255, description="Street address")
    city: Optional[str] = Field(None, max_length=100, description="City")
    country: Optional[str] = Field(None, max_length=100, description="Country")
    continent: Optional[str] = Field(None, max_length=100, description="Continent of supplier location")
    area: Optional[str] = Field(None, max_length=100, description="Geographic area / region")
    product_type: Optional[str] = Field(None, max_length=255, description="Type of products supplied (legacy)")
    product_category: Optional[str] = Field(None, max_length=255, description="Product category (legacy)")
    # Product classification — stored comma-separated, accept list or string
    commodity: Optional[str] = Field(None, max_length=500, description="Commodity / purchasing category (comma-separated)")
    family: Optional[str] = Field(None, max_length=500, description="Product family (comma-separated)")
    sub_family: Optional[str] = Field(None, max_length=500, description="Product sub-family (comma-separated)")
    product_line: Optional[str] = Field(None, max_length=500, description="Product line (comma-separated)")
    website: Optional[str] = Field(None, max_length=500, description="Supplier unit website URL")
    # Environmental / GHG data (from SB6 board)
    carbon_footprint: Optional[str] = Field(None, max_length=100, description="Annual carbon footprint (tCO2e)")
    green_electricity_pct: Optional[str] = Field(None, max_length=10, description="Green electricity share (%)")
    amount_value: Optional[Decimal] = Field(None, description="Annual spend value")
    amount_currency: Optional[str] = Field(None, max_length=10, description="Currency code (USD, EUR, etc.)")
    strategique: Optional[bool] = Field(False, description="Is this unit strategic?")
    monopolistique: Optional[bool] = Field(False, description="Is this unit monopolistic?")
    directed: Optional[bool] = Field(False, description="Is this unit directed?")

    @field_validator("amount_value", mode="before")
    @classmethod
    def coerce_unit_decimals(cls, v: object) -> Optional[Decimal]:
        return _coerce_decimal(v)

    @field_validator("commodity", "family", "sub_family", "product_line", mode="before")
    @classmethod
    def coerce_list_to_csv(cls, v: object) -> Optional[str]:
        """Accept a list of strings and join as CSV, or pass through a plain string."""
        if v is None:
            return None
        if isinstance(v, list):
            joined = ",".join(str(x).strip() for x in v if str(x).strip())
            return joined if joined else None
        return str(v) if v != "" else None

    @field_validator(
        "supplier_code",
        "address_line",
        "city",
        "country",
        "continent",
        "area",
        "product_type",
        "product_category",
        "website",
        "carbon_footprint",
        "green_electricity_pct",
        "amount_currency",
        mode="before",
    )
    @classmethod
    def normalize_unit_strings(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_optional_string(value)


class SupplierUnitCreate(SupplierUnitBase):
    """Schema for creating a new supplier unit."""
    id_group: Optional[int] = Field(None, description="Parent supplier group ID")
    supplier_code: str = Field(..., max_length=50, description="Unique supplier code (required)")


class SupplierUnitUpdate(SupplierUnitBase):
    """Schema for updating a supplier unit."""
    supplier_code: Optional[str] = Field(None, max_length=50)
    is_active: Optional[bool] = Field(None, description="Active status of the unit")


class SupplierUnitResponse(SupplierUnitBase):
    """Response schema for supplier unit."""
    id_supplier_unit: int
    id_group: Optional[int]
    unit_code: Optional[str] = None
    is_active: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ============================================================================
# Supplier Certification Schemas
# ============================================================================

class SupplierCertificationBase(BaseModel):
    """Base supplier certification schema."""
    standard_type: Optional[str] = Field(None, max_length=50, description="Standard category: quality, environmental, safety, energy, other")
    certification_type: Optional[str] = Field(None, max_length=100, description="Specific certification name (e.g., IATF 16949:2016, ISO 9001 (cat BCD))")
    certificate_name: Optional[str] = Field(None, max_length=150, description="Free-text certificate reference or issuer")
    amount_value: Optional[Decimal] = Field(None, description="Cost/value of certification")
    amount_currency: Optional[str] = Field(None, max_length=10, description="Currency code")
    start_date: Optional[date] = Field(None, description="Certificate start date (YYYY-MM-DD)")
    end_date: Optional[date] = Field(None, description="Certificate expiry date (YYYY-MM-DD)")
    expiry_mode: Optional[str] = Field(None, max_length=30, description="How expiry is handled")
    comments: Optional[str] = Field(None, description="Additional notes")
    file_name: Optional[str] = Field(None, max_length=255, description="Uploaded certificate file name")
    file_url: Optional[str] = Field(None, description="URL to the uploaded certificate file")
    file_size: Optional[Decimal] = Field(None, description="File size in bytes")

    @field_validator("amount_value", "file_size", mode="before")
    @classmethod
    def coerce_cert_decimal(cls, v: object) -> Optional[Decimal]:
        return _coerce_decimal(v)

    @model_validator(mode="after")
    def validate_certification_dates(self):
        if (
            self.start_date is not None
            and self.end_date is not None
            and self.end_date < self.start_date
        ):
            raise ValueError("end_date must be on or after start_date")
        return self


class SupplierCertificationCreate(SupplierCertificationBase):
    """Schema for creating a supplier certification."""
    standard_type: str = Field(..., max_length=50, description="Standard category (required): quality, environmental, safety, energy, other")


class SupplierCertificationUpdate(BaseModel):
    """Partial update for a supplier certification — only provided fields are changed."""
    certification_type: Optional[str] = Field(None, max_length=100)
    certificate_name: Optional[str] = Field(None, max_length=150)
    start_date: Optional[date] = Field(None)
    end_date: Optional[date] = Field(None)
    comments: Optional[str] = Field(None)
    file_name: Optional[str] = Field(None, max_length=255)
    file_url: Optional[str] = Field(None)

    @model_validator(mode="after")
    def validate_dates(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class SupplierCertificationResponse(SupplierCertificationBase):
    """Response schema for supplier certification."""
    id_certification: int
    id_supplier_unit: Optional[int]

    class Config:
        from_attributes = True


# ============================================================================
# Contact Schemas
# ============================================================================

class ContactBase(BaseModel):
    """Base contact schema."""
    role_label: Optional[str] = Field(None, max_length=100, description="Contact role label (e.g., 'Quality Manager')")
    role_name: Optional[str] = Field(None, max_length=150, description="Detailed role name")
    full_name: Optional[str] = Field(None, max_length=200, description="Full name of contact")
    phone: Optional[str] = Field(None, max_length=50, description="Phone number")
    email: Optional[str] = Field(None, max_length=200, description="Email address")
    is_primary_contact: Optional[bool] = Field(False, description="Is this the primary contact?")

    @field_validator("email")
    @classmethod
    def validate_contact_email(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_optional_email(value, "email")


class ContactCreate(ContactBase):
    """Schema for creating a contact."""
    full_name: str = Field(..., max_length=200, description="Full name (required)")
    email: Optional[str] = Field(None, max_length=200)


class ContactResponse(ContactBase):
    """Response schema for contact."""
    id_contact: int
    id_supplier_group: Optional[int]
    id_supplier_unit: Optional[int]
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


# ============================================================================
# Combined Response Schemas
# ============================================================================

class SupplierDetailResponse(SupplierGroupResponse):
    """Detailed supplier response including units."""
    units: List[SupplierUnitResponse] = Field(default_factory=list, description="Associated supplier units")
    contacts: List[ContactResponse] = Field(default_factory=list, description="Associated contacts")


class CreateSupplierRequest(BaseModel):
    """Request schema for creating a complete supplier (group + unit)."""
    group: SupplierGroupCreate = Field(..., description="Supplier group details")
    unit: SupplierUnitCreate = Field(..., description="Supplier unit details")
    contacts: List[ContactCreate] = Field(default_factory=list, description="Initial contacts")
    certifications: List[SupplierCertificationCreate] = Field(default_factory=list, description="Certifications")


# Backward compatibility schemas
class SupplierBase(BaseModel):
    """Base supplier schema (deprecated - use SupplierGroupBase)."""
    pass


class SupplierCreate(SupplierBase):
    """Supplier creation schema (deprecated - use CreateSupplierRequest)."""
    pass


class SupplierUpdate(BaseModel):
    """Supplier update schema (deprecated - use SupplierGroupUpdate)."""
    pass


class SupplierResponse(SupplierBase):
    """Supplier response schema (deprecated - use SupplierDetailResponse)."""
    class Config:
        from_attributes = True


# ============================================================================
# Complete Onboarding Workflow Schemas
# ============================================================================

class EvaluationDetailsBase(BaseModel):
    """Base evaluation details schema."""
    class_criteria_details: Dict[str, "ClassCriterionDetail"] = Field(
        default_factory=dict,
        description="Additional evidence and validity details for each of the 11 class criteria.",
    )
    comments: Optional[str] = Field(None, description="Additional evaluation comments")
    impact: Optional[int] = Field(
        None,
        ge=1,
        le=4,
        description="Legacy class field kept for backward compatibility with older onboarding payloads.",
    )
    impact_score: Optional[int] = Field(
        None,
        ge=-30,
        le=30,
        description="Supplier impact score derived from the 6 impact questions.",
    )
    class_value: Optional[int] = Field(None, ge=1, le=4, description="Class evaluation value: 1-4")
    class_score: Optional[Decimal] = Field(None, ge=0, le=100, description="Class evaluation score")
    operational_class: Optional[str] = Field(None, description="Operational Evaluation Class: A-D")
    operational_grade: Optional[str] = Field(None, description="Operational evaluation grade: A-D")
    operational_score: Optional[Decimal] = Field(None, ge=0, le=100, description="Operational evaluation score")
    strategic_mention: Optional[str] = Field(None, description="Strategic mention: strategic, monopolistic, directed, none")
    panel_decision: Optional[str] = Field(None, description="Panel decision code")
    suggestion: Optional[str] = Field(None, description="Legacy suggestion field mapped to panel decision")
    management_system: Optional[Decimal] = Field(None, ge=0, le=100, description="Operational evaluation score")
    customer_communication: Optional[Decimal] = Field(None, ge=0, le=100, description="Operational evaluation score")
    development_design: Optional[Decimal] = Field(None, ge=0, le=100, description="Operational evaluation score")
    production_manufacturing: Optional[Decimal] = Field(None, ge=0, le=100, description="Operational evaluation score")
    quality_audits: Optional[Decimal] = Field(None, ge=0, le=100, description="Operational evaluation score")
    suppliers_subcontractors: Optional[Decimal] = Field(None, ge=0, le=100, description="Operational evaluation score")
    deliveries: Optional[Decimal] = Field(None, ge=0, le=100, description="Operational evaluation score")
    environment_ethic_rules: Optional[Decimal] = Field(None, ge=0, le=100, description="Operational evaluation score")
    impact_question_1: Optional[str] = Field(None, description="Impact question 1 result")
    impact_question_2: Optional[str] = Field(None, description="Impact question 2 result")
    impact_question_3: Optional[str] = Field(None, description="Impact question 3 result")
    impact_question_4: Optional[str] = Field(None, description="Impact question 4 result")
    impact_question_5: Optional[str] = Field(None, description="Impact question 5 result")
    impact_question_6: Optional[str] = Field(None, description="Impact question 6 result")
    top: Optional[str] = Field(None, description="TOP payment term selection")
    lta: Optional[str] = Field(None, description="LTA selection")
    sqma: Optional[str] = Field(None, description="SQMA selection")
    quality_certification: Optional[str] = Field(None, description="Quality certification selection")
    family_coverage: Optional[str] = Field(None, description="Family coverage selection")
    competitiveness: Optional[str] = Field(None, description="Competitiveness selection")
    geo_coverage: Optional[str] = Field(None, description="Geo coverage selection")
    cons_or_wd: Optional[str] = Field(None, description="Consignment or WD selection")
    financial_health: Optional[str] = Field(None, description="Financial health selection")
    prod_lia_ins: Optional[str] = Field(None, description="Production liaison inspection selection")
    prod: Optional[str] = Field(None, description="Production selection")

    @field_validator("top")
    @classmethod
    def validate_top(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        _top_map = {
            "60 days end of month or +": "60 days eom or +",
            "60 days eom or+": "60 days eom or +",
        }
        value = _top_map.get(value, value)
        if value not in TOP_VALUES:
            return value  # pass through unknown legacy values
        return value

    @field_validator("lta")
    @classmethod
    def validate_lta(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        _lta_map = {"None": "None/Invalid", "none": "None/Invalid", "invalid": "None/Invalid"}
        value = _lta_map.get(value, value)
        if value not in LTA_VALUES:
            return value  # pass through unknown legacy values
        return value

    @field_validator("sqma")
    @classmethod
    def validate_sqma(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        _sqma_map = {
            "Signed M/Res/not sent": "Signed M.Res/not sent",
            "signed m/res/not sent": "Signed M.Res/not sent",
        }
        value = _sqma_map.get(value, value)
        if value not in SQMA_VALUES:
            return value  # pass through unknown legacy values
        return value

    @field_validator("quality_certification")
    @classmethod
    def validate_quality_certification(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            return value  # pass through — certification values vary too much across boards
        return value

    @field_validator("operational_class")
    @classmethod
    def validate_operational_class(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        v = value.upper()
        if v not in OPERATIONAL_CLASS_VALUES:
            raise ValueError(f"operational_class must be one of: {', '.join(sorted(OPERATIONAL_CLASS_VALUES))}")
        return v

    @field_validator("operational_grade")
    @classmethod
    def validate_operational_grade(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        v = value.upper()
        if v not in OPERATIONAL_CLASS_VALUES:
            raise ValueError(f"operational_grade must be one of: {', '.join(sorted(OPERATIONAL_CLASS_VALUES))}")
        return v

    @field_validator("family_coverage")
    @classmethod
    def validate_family_coverage(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        _fc_map = {
            # Old long English → Monday short codes
            "Supplier can make all the family requirements": "100% Cov.",
            "Supplier can make the main family requirements": "Main sub-Fam Cov.",
            "Supplier can make only of few family requirements": "1 sub-F or refs Cov.",
            "Supplier can make 1 family requirements": "1 ref",
            # Old short aliases
            "100% cov.": "100% Cov.",
            "Main Fam.": "Main sub-Fam Cov.",
            "main fam.": "Main sub-Fam Cov.",
            "1 Family": "1 ref",
            "1 family": "1 ref",
            "Few Fam.": "1 sub-F or refs Cov.",
            "few fam.": "1 sub-F or refs Cov.",
        }
        value = _fc_map.get(value, value)
        if value not in FAMILY_COVERAGE_VALUES:
            return value  # pass through unknown legacy values
        return value

    @field_validator("competitiveness")
    @classmethod
    def validate_competitiveness(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in COMPETITIVENESS_VALUES:
            return value  # pass through legacy values
        return value

    @field_validator("geo_coverage")
    @classmethod
    def validate_geo_coverage(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in GEO_COVERAGE_VALUES:
            return value  # pass through legacy values
        return value

    @field_validator("cons_or_wd")
    @classmethod
    def validate_cons_or_wd(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        _cwd_map = {
            # Old canonical long form → Monday short code
            "Cons. Or Daily Deliveries": "Cons. or WD",
            "Cons. or daily deliveries": "Cons. or WD",
            "cons. or wd": "Cons. or WD",
            # Normalize lowercase d variant from formulas.py
            "Biweekly del.": "Biweekly Del.",
            "biweekly del.": "Biweekly Del.",
            # Case variants
            "DDP or weekly del.": "DDP or Weekly Del.",
            "ddp or weekly del.": "DDP or Weekly Del.",
        }
        value = _cwd_map.get(value, value)
        if value not in CONS_OR_WD_VALUES:
            return value  # pass through unknown legacy values
        return value

    @field_validator("financial_health")
    @classmethod
    def validate_financial_health(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in FINANCIAL_HEALTH_VALUES:
            return value  # pass through legacy values
        return value

    @field_validator("prod_lia_ins")
    @classmethod
    def validate_prod_lia_ins(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        _pli_map = {
            "2M€ or +": "2M$ or +",
            "2m€ or +": "2M$ or +",
            "1M€ or +": "1M$ or +",
            "1m€ or +": "1M$ or +",
            "2M$ or+": "2M$ or +",
            "1M$ or+": "1M$ or +",
        }
        value = _pli_map.get(value, value)
        if value not in PROD_LIA_INS_VALUES:
            return value  # pass through unknown legacy values
        return value

    @field_validator("prod")
    @classmethod
    def validate_prod(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in PROD_VALUES:
            return value  # pass through legacy values
        return value

    @field_validator("strategic_mention")
    @classmethod
    def validate_strategic_mention(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        parts = [p.strip().lower() for p in value.split(",") if p.strip()]
        if not parts:
            return None
        # Normalize known values; pass through unknowns to avoid crashing on legacy DB data
        known = [p for p in parts if p in STRATEGIC_MENTION_VALUES]
        unknown = [p for p in parts if p not in STRATEGIC_MENTION_VALUES]
        if unknown and not known:
            return value  # all unknown — return as-is
        return ",".join(sorted(known)) if known else value

    @field_validator("panel_decision")
    @classmethod
    def validate_panel_decision(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        v = value.lower()
        if v not in PANEL_DECISION_VALUES:
            return value  # pass through legacy values
        return v


class ClassCriterionDetail(BaseModel):
    document_id: Optional[int] = None
    document_name: Optional[str] = None
    document_url: Optional[str] = None
    document_mime_type: Optional[str] = None
    document_size: Optional[Decimal] = None
    evidence_file_name: Optional[str] = Field(
        None, max_length=255, description="Attached file name or document reference"
    )
    validity_start_date: Optional[date] = None
    validity_end_date: Optional[date] = None
    signature_date: Optional[date] = None
    last_update_date: Optional[date] = None
    amount_value: Optional[Decimal] = Field(None, ge=0)
    amount_currency: Optional[str] = Field(None, max_length=10)
    auto_validity_end_date: bool = False
    comments: Optional[str] = None
    score: Optional[Decimal] = Field(None, ge=-5, le=100)

    @model_validator(mode="after")
    def validate_dates(self) -> "ClassCriterionDetail":
        if (
            self.validity_start_date is not None
            and self.validity_end_date is not None
            and self.validity_end_date < self.validity_start_date
        ):
            raise ValueError("validity_end_date must be on or after validity_start_date")
        return self


EvaluationDetailsBase.model_rebuild()


class CompleteSupplierOnboardingRequest(BaseModel):
    """Request schema for complete supplier onboarding workflow."""
    group: SupplierGroupCreate = Field(..., description="Supplier group details (required)")
    unit: SupplierUnitCreate = Field(..., description="Supplier unit details (required)")
    contacts: List[ContactCreate] = Field(default_factory=list, description="Group-level contacts (at least one recommended)")
    unit_contacts: List[ContactCreate] = Field(default_factory=list, description="Unit-level contacts (quality manager, sales contact, etc.)")
    certifications: List[SupplierCertificationCreate] = Field(default_factory=list, description="Certifications")
    evaluation: Optional[EvaluationDetailsBase] = Field(None, description="Initial evaluation details")

    # Onboarding configuration
    site_id: int = Field(..., description="Avocarbon site ID to link supplier to (required)")
    supplier_scope: str = Field(..., description="Classification: 'global', 'strategic', or 'local' (required)")
    supplier_owner: str = Field(..., max_length=200, description="Relation-level supplier owner (overrides group default if provided)")
    annual_spend_value: Optional[Decimal] = Field(None, description="Annual spend for this unit-plant relation")
    annual_spend_currency: Optional[str] = Field(None, max_length=10, description="Currency for annual spend")
    template_id: Optional[int] = Field(None, description="Optional assessment template ID (defaults to first active template)")


class CreateUnitCompleteRequest(BaseModel):
    """Create a unit with its contacts and certifications in one atomic call."""
    unit: SupplierUnitCreate
    contacts: List[ContactCreate] = Field(default_factory=list)
    certifications: List[SupplierCertificationCreate] = Field(default_factory=list)


class SupplierSiteRelationCreate(BaseModel):
    """Request schema for creating a supplier-site relation."""
    supplier_scope: Optional[str] = Field(None, description="Classification: 'global', 'strategic', or 'local'")
    supplier_owner: Optional[str] = Field(None, max_length=200, description="Relation-level supplier owner override (email). Falls back to group owner if not set.")
    annual_spend_value: Optional[Decimal] = Field(None, description="Annual spend for this unit-plant relation")
    annual_spend_currency: Optional[str] = Field(None, max_length=10, description="Currency for annual spend")

    @field_validator("annual_spend_value", mode="before")
    @classmethod
    def coerce_relation_spend(cls, v: object) -> Optional[Decimal]:
        return _coerce_decimal(v)
    operational_grade: Optional[str] = Field(None, max_length=1, description="Grade: A-D")
    class_value: Optional[int] = Field(None, ge=1, le=4, description="Numeric value: 1-4")
    final_grade: Optional[str] = Field(None, description="Combined final grade, for example A2")
    strategic_mention: Optional[str] = Field(None, description="Strategic mention code")
    panel_decision: Optional[str] = Field(None, description="Panel decision code")
    evaluation_frequency: Optional[str] = Field(None, description="Evaluation frequency (e.g., 'Quarterly', 'Annual')")
    supplier_status: Optional[str] = Field(None, max_length=10, description="Status of this relation")
    alias_1: Optional[str] = Field(None, max_length=200, description="Alias for this relation")
    evaluation_comments: Optional[str] = Field(None, description="Initial evaluation comments")
    evaluation_suggestion: Optional[str] = Field(None, max_length=255, description="Initial evaluation suggestion")
    # Supplier Panel (SB1) fields
    transport_mode: Optional[str] = Field(None, max_length=100, description="Transport mode (e.g., Sea, Air, Road)")
    transit_days: Optional[int] = Field(None, description="Transit time in days")
    incoterm_place: Optional[str] = Field(None, max_length=200, description="Incoterm and delivery place")
    real_ap_days: Optional[int] = Field(None, description="Real accounts payable days")
    real_ap_days_validated: Optional[int] = Field(None, description="Validated AP days")
    consignment: Optional[bool] = Field(None, description="Consignment stock agreement")
    preferred_dev_supplier: Optional[bool] = Field(None, description="Preferred development supplier flag")
    data_validity: Optional[str] = Field(None, max_length=50, description="Data validity status from SB1")
    quality_cert_required: Optional[str] = Field(None, max_length=200, description="Required quality certification")
    delivery_status: Optional[str] = Field(None, max_length=50, description="Delivery status")
    req_ap_date: Optional[date] = Field(None, description="Required accounts payable date")


class SupplierSiteRelationResponse(BaseModel):
    """Response schema for supplier-site relation."""
    id_relation: int
    id_site: int
    id_supplier_unit: int
    relation_code: Optional[str] = None
    unit_code: Optional[str] = None
    sb1_item_name: Optional[str] = None
    supplier_scope: Optional[str] = None
    supplier_owner: Optional[str] = None
    annual_spend_value: Optional[Decimal] = None
    annual_spend_currency: Optional[str] = None
    operational_grade: Optional[str] = None
    class_value: Optional[int] = None
    evaluation_frequency: Optional[str] = None
    final_grade: Optional[str] = None
    strategic_mention: Optional[str] = None
    panel_decision: Optional[str] = None
    supplier_status: Optional[str] = None
    alias_1: Optional[str] = None
    global_status: Optional[str] = None
    created_at: Optional[datetime] = None
    last_evaluation_date: Optional[datetime] = None
    next_evaluation_date: Optional[datetime] = None
    inactivated_at: Optional[datetime] = None
    last_status_change: Optional[datetime] = None
    evaluation_comments: Optional[str] = None
    evaluation_suggestion: Optional[str] = None
    # Supplier Panel (SB1) fields
    last_eval_score: Optional[Decimal] = None
    transport_mode: Optional[str] = None
    transit_days: Optional[int] = None
    incoterm_place: Optional[str] = None
    real_ap_days: Optional[int] = None
    real_ap_days_validated: Optional[int] = None
    consignment: Optional[bool] = None
    preferred_dev_supplier: Optional[bool] = None
    data_validity: Optional[str] = None
    quality_cert_required: Optional[str] = None
    delivery_status: Optional[str] = None
    req_ap_date: Optional[date] = None

    class Config:
        from_attributes = True


# ============================================================================
# Carbon Footprint Schema (SB8)
# ============================================================================

class SupplierCarbonFootprintResponse(BaseModel):
    """Response schema for supplier carbon footprint record (SB8 board)."""
    id_carbon_footprint: int
    id_supplier_unit: Optional[int] = None
    id_relation: Optional[int] = None
    year: Optional[int] = None
    carbon_fp_grade: Optional[str] = None
    purchase_amount: Optional[Decimal] = None
    weighted_footprint: Optional[Decimal] = None
    production_fp_grade: Optional[str] = None
    transport_impact: Optional[Decimal] = None
    global_fp_impact: Optional[Decimal] = None
    supplier_origin: Optional[str] = None
    supplier_continent: Optional[str] = None
    site_location: Optional[str] = None
    site_continent: Optional[str] = None
    supplier_unit_code: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class CarbonFootprintUpdateRequest(BaseModel):
    """Partial update for a carbon footprint record."""
    year: Optional[int] = None
    carbon_fp_grade: Optional[str] = None
    purchase_amount: Optional[Decimal] = None
    weighted_footprint: Optional[Decimal] = None
    production_fp_grade: Optional[str] = None
    transport_impact: Optional[Decimal] = None
    global_fp_impact: Optional[Decimal] = None
    supplier_origin: Optional[str] = None
    supplier_continent: Optional[str] = None
    site_location: Optional[str] = None
    site_continent: Optional[str] = None


class CarbonFootprintCreateRequest(BaseModel):
    """Create a new carbon footprint record."""
    id_supplier_unit: Optional[int] = None
    id_relation: Optional[int] = None
    year: Optional[int] = None
    carbon_fp_grade: Optional[str] = None
    purchase_amount: Optional[Decimal] = None
    weighted_footprint: Optional[Decimal] = None
    production_fp_grade: Optional[str] = None
    transport_impact: Optional[Decimal] = None
    global_fp_impact: Optional[Decimal] = None
    supplier_origin: Optional[str] = None
    supplier_continent: Optional[str] = None
    site_location: Optional[str] = None
    site_continent: Optional[str] = None


class InitialUnitEvaluationRequest(EvaluationDetailsBase):
    """Initial SBA evaluation payload for a supplier unit."""
    changed_by: Optional[str] = Field(
        None, description="User or system performing the evaluation save"
    )


class InitialUnitEvaluationResponse(BaseModel):
    """Response for a unit baseline evaluation creation."""
    unit_id: int
    relation_id: int
    cycle_id: int
    score_card_id: Optional[int] = None
    classification_id: Optional[int] = None
    status_history_id: Optional[int] = None
    final_grade: Optional[str] = None
    class_value: Optional[int] = None
    operational_grade: Optional[str] = None
    panel_decision: Optional[str] = None


class UnitEvaluationSummaryResponse(BaseModel):
    """Latest known evaluation state for a supplier unit."""
    unit_id: int
    relation_id: Optional[int] = None
    class_value: Optional[int] = None
    class_score: Optional[Decimal] = None
    operational_grade: Optional[str] = None
    operational_score: Optional[Decimal] = None
    final_grade: Optional[str] = None
    strategic_mention: Optional[str] = None
    panel_decision: Optional[str] = None
    impact_score: Optional[int] = None
    last_evaluation_date: Optional[date] = None
    evaluation_comments: Optional[str] = None
    site_relations_count: int = 0


class OnboardingEmailStatus(BaseModel):
    """Email notification status."""
    creation_notification: bool = Field(description="Supplier creation email sent")
    owner_assignment: bool = Field(description="Owner assignment email sent")
    assessment_template: bool = Field(description="Assessment template email sent")
    prequalification_launch: bool = Field(description="Prequalification launch email sent")


class OnboardingRelationInfo(BaseModel):
    """Supplier-site relation information."""
    relation_id: int = Field(description="Supplier-site relation ID")
    site_id: int = Field(description="Avocarbon site ID")
    supplier_scope: str = Field(description="Classification (global/strategic/local)")
    supplier_owner: str = Field(description="Assigned supplier owner")


class OnboardingPrequalificationInfo(BaseModel):
    """Prequalification cycle and assessment information."""
    cycle_id: Optional[int] = Field(None, description="Evaluation cycle ID")
    assessment_id: Optional[int] = Field(None, description="Self-assessment ID")
    template_id: Optional[int] = Field(None, description="Assessment template ID")


class OnboardingSupplierInfo(BaseModel):
    """Created supplier information."""
    group_id: int = Field(description="Supplier group ID")
    group_name: str = Field(description="Supplier group name")
    unit_id: int = Field(description="Supplier unit ID")
    unit_code: str = Field(description="Supplier unit code")


class OnboardingContactInfo(BaseModel):
    """Contact information."""
    id: int = Field(description="Contact ID")
    name: str = Field(description="Contact name")
    email: Optional[str] = Field(None, description="Contact email")


class OnboardingContactsInfo(BaseModel):
    """Contacts summary."""
    primary_contact: OnboardingContactInfo = Field(description="Primary contact details")
    total_contacts: int = Field(description="Total number of contacts created")


class CompleteSupplierOnboardingResponse(BaseModel):
    """Response schema for complete onboarding workflow."""
    status: str = Field(description="Operation status (success/error)")
    supplier: OnboardingSupplierInfo = Field(description="Created supplier details")
    relation: OnboardingRelationInfo = Field(description="Supplier-site relation details")
    contacts: OnboardingContactsInfo = Field(description="Contact information")
    prequalification: OnboardingPrequalificationInfo = Field(description="Prequalification details")
    emails: OnboardingEmailStatus = Field(description="Email notification status")
    message: str = Field(description="Summary message")


class OnboardingSelectionOptionsResponse(BaseModel):
    """Selectable onboarding values sourced from the board data."""

    top: List[dict[str, str]]
    lta: List[dict[str, str]]
    sqma: List[dict[str, str]]
    family_coverage: List[dict[str, str]]
    cons_or_wd: List[dict[str, str]]
    financial_health: List[dict[str, str]]
    certification_types: List[dict[str, str]]
    certification_standard_types: List[dict[str, str]]
    cert_types_by_standard: dict
    prod_lia_ins: List[dict[str, str]]
    prod: List[dict[str, str]]

