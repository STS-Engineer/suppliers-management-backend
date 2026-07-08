"""Supplier onboarding select options derived from the board data sheet."""

from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PldScoringRules


# NOTE: the lists below back request-body VALIDATION ONLY (schemas.py's
# TOP_VALUES/etc. allow-lists used by field_validators to accept/reject a
# submitted class-evaluation value). They are intentionally separate from
# get_onboarding_selection_options() below, which drives what the frontend
# *displays* and is sourced live from pld_scoring_rules. Pydantic field
# validators run synchronously at request-parse time with no DB session, so
# validation can't query the DB inline the way the options endpoint does --
# keep these in sync with pld_scoring_rules by hand when a criteria value is
# added or changed.
# Full canonical 29-tier table (see migration 20260707_0077), plus a handful
# of pre-canonicalization aliases kept here purely so previously-valid saved
# values still pass validation (CRITERIA_VALUE_NORMALIZATION maps them onto a
# canonical tier at scoring time, but validation happens on the raw request
# value before that normalization runs).
TOP_OPTIONS = [
    {"value": "Cash at order", "label": "Cash at order"},
    {"value": "Cash in advance", "label": "Cash in advance"},
    {"value": "0 days net", "label": "0 days net"},
    {"value": "0 days end of month", "label": "0 days end of month"},
    {"value": "0 days end of month the 15", "label": "0 days end of month the 15"},
    {"value": "15 days net", "label": "15 days net"},
    {"value": "15 days end of month", "label": "15 days end of month"},
    {"value": "15 days end of month the 15", "label": "15 days end of month the 15"},
    {"value": "30 days net", "label": "30 days net"},
    {"value": "30 days end of month", "label": "30 days end of month"},
    {"value": "30 days end of month the 15", "label": "30 days end of month the 15"},
    {"value": "45 days net", "label": "45 days net"},
    {"value": "45 days end of month", "label": "45 days end of month"},
    {"value": "45 days end of month the 15", "label": "45 days end of month the 15"},
    {"value": "60 days net", "label": "60 days net"},
    {"value": "60 days end of month", "label": "60 days end of month"},
    {"value": "60 days end of month the 15", "label": "60 days end of month the 15"},
    {"value": "75 days net", "label": "75 days net"},
    {"value": "75 days end of month", "label": "75 days end of month"},
    {"value": "75 days end of month the 15", "label": "75 days end of month the 15"},
    {"value": "90 days net", "label": "90 days net"},
    {"value": "90 days end of month", "label": "90 days end of month"},
    {"value": "90 days end of month the 15", "label": "90 days end of month the 15"},
    {"value": "105 days net", "label": "105 days net"},
    {"value": "105 days end of month", "label": "105 days end of month"},
    {"value": "105 days end of month the 15", "label": "105 days end of month the 15"},
    {"value": "120 days net", "label": "120 days net"},
    {"value": "120 days end of month", "label": "120 days end of month"},
    {"value": "120 days end of month the 15", "label": "120 days end of month the 15"},
    {"value": "Requested", "label": "Requested"},
    # Pre-canonicalization aliases (see CRITERIA_VALUE_NORMALIZATION["top"])
    {"value": "30 days end of month or +", "label": "30 days end of month or +"},
    {"value": "60 days end of month or +", "label": "60 days end of month or +"},
    {"value": "60 days eom or +", "label": "60 days eom or +"},
    {"value": "45 days end of month or +", "label": "45 days end of month or +"},
    {"value": "Cash in Advance", "label": "Cash in Advance"},
]

LTA_OPTIONS = [
    {"value": "1 year", "label": "1 year"},
    {"value": "2 years", "label": "2 years"},
    {"value": "3 years/+", "label": "3 years/+"},
    {"value": "None/Invalid", "label": "None/Invalid"},
    {"value": "Requested", "label": "Requested"},
]

SQMA_OPTIONS = [
    {"value": "Rejected", "label": "Rejected"},
    {"value": "Signed", "label": "Signed"},
    {"value": "Signed m.res.", "label": "Signed m.res."},
    {"value": "Signed M.Res/not sent", "label": "Signed M.Res/not sent"},
    {"value": "Requested", "label": "Requested"},
]

FAMILY_COVERAGE_OPTIONS = [
    {"value": "100% Cov.", "label": "100% Cov."},
    {"value": "Main sub-Fam Cov.", "label": "Main sub-Fam Cov."},
    {"value": "1 sub-F or refs Cov.", "label": "1 sub-F or refs Cov."},
    {"value": "1 ref", "label": "1 ref"},
    {"value": "None", "label": "None"},
    {"value": "Requested", "label": "Requested"},
]

COMPETITIVENESS_OPTIONS = [
    {"value": "Best in Fam.", "label": "Best in Fam."},
    {"value": "Almost Best in Fam.", "label": "Almost Best in Fam."},
    {"value": "Ave. in Fam.", "label": "Ave. in Fam."},
    {"value": "Less Avg", "label": "Less Avg"},
    {"value": "Not Comp.", "label": "Not Comp."},
    {"value": "Requested", "label": "Requested"},
]

GEO_COVERAGE_OPTIONS = [
    {"value": "1 plant is covered", "label": "1 plant is covered"},
    {"value": "Main plants covered", "label": "Main plants covered"},
    {
        "value": "More than 50% plants are covered",
        "label": "More than 50% plants are covered",
    },
    {"value": "None", "label": "None"},
    {"value": "Requested", "label": "Requested"},
]

CONS_OR_WD_OPTIONS = [
    {"value": "Cons. or WD", "label": "Cons. or WD"},
    {"value": "Cons. or WD Inter. User", "label": "Cons. or WD Inter. User"},
    {"value": "Biweekly Del.", "label": "Biweekly Del."},
    {"value": "DDP or Weekly Del.", "label": "DDP or Weekly Del."},
    {"value": "None", "label": "None"},
    {"value": "Requested", "label": "Requested"},
]

FINANCIAL_HEALTH_OPTIONS = [
    {"value": "Good", "label": "Good"},
    {"value": "To Monitor", "label": "To Monitor"},
    {"value": "At Risk", "label": "At Risk"},
    {"value": "Requested", "label": "Requested"},
]

# Standard categories for certifications (standard_type field)
CERTIFICATION_STANDARD_TYPE_OPTIONS = [
    {"value": "quality", "label": "Quality"},
    {"value": "environmental", "label": "Environmental"},
    {"value": "safety", "label": "Safety & Health"},
    {"value": "energy", "label": "Energy"},
    {"value": "other", "label": "Other"},
]

# Specific certification values used for PLD quality_certification scoring.
# Kept alongside legacy values for backward compatibility with existing DB records.
CERTIFICATION_TYPE_OPTIONS = [
    {"value": "IATF 16949:2016", "label": "IATF 16949:2016"},
    {"value": "ISO 9001 (cat BCD)", "label": "ISO 9001 (cat BCD)"},
    {"value": "ISO 9001", "label": "ISO 9001"},
    {"value": "Distributor", "label": "Distributor"},
    {"value": "None", "label": "None"},
    {"value": "Requested", "label": "Requested"},
    # Legacy combined value kept for backward compatibility
    {"value": "IATF / ISO9001 (cat BCD)", "label": "IATF / ISO9001 (cat BCD) [legacy]"},
    {"value": "ISO9001", "label": "ISO9001 [legacy]"},
]

# Certification names available per standard category (drives the cascading select in the UI)
CERT_TYPES_BY_STANDARD: Dict[str, list] = {
    "quality": [
        {"value": "IATF 16949:2016", "label": "IATF 16949:2016 — Automotive Quality Management"},
        {"value": "ISO 9001 (cat BCD)", "label": "ISO 9001 (cat BCD)"},
        {"value": "ISO 9001", "label": "ISO 9001 — Quality Management System"},
        {"value": "ISO 13485", "label": "ISO 13485 — Medical Devices Quality"},
        {"value": "Distributor", "label": "Distributor (no manufacturing cert)"},
        {"value": "None", "label": "None"},
    ],
    "environmental": [
        {"value": "ISO 14001", "label": "ISO 14001 — Environmental Management"},
        {"value": "ISO 14064", "label": "ISO 14064 — Greenhouse Gas"},
        {"value": "REACH", "label": "REACH — Chemical Regulations"},
        {"value": "RoHS", "label": "RoHS — Hazardous Substances"},
        {"value": "None", "label": "None"},
    ],
    "safety": [
        {"value": "ISO 45001", "label": "ISO 45001 — Occupational Health & Safety"},
        {"value": "ITAR", "label": "ITAR — International Traffic in Arms"},
        {"value": "None", "label": "None"},
    ],
    "energy": [
        {"value": "ISO 50001", "label": "ISO 50001 — Energy Management"},
        {"value": "None", "label": "None"},
    ],
    "other": [
        {"value": "ISO/IEC 27001", "label": "ISO/IEC 27001 — Information Security"},
        {"value": "ISO 22301", "label": "ISO 22301 — Business Continuity"},
        {"value": "FSC", "label": "FSC — Forest Stewardship Council"},
        {"value": "Conflict-Free", "label": "Conflict-Free Minerals"},
        {"value": "Other", "label": "Other"},
    ],
}

PROD_LIA_INS_OPTIONS = [
    {"value": "None", "label": "None"},
    {"value": "500k€ or less", "label": "500k€ or less"},
    {"value": "1M€ or less", "label": "1M€ or less"},
    {"value": "1,5M€ or less", "label": "1,5M€ or less"},
    {"value": "1,5M€ or more", "label": "1,5M€ or more"},
    {"value": "1M€ or +", "label": "1M€ or +"},
    {"value": "2M€ or +", "label": "2M€ or +"},
    {"value": "1M$ or +", "label": "1M$ or +"},
    {"value": "2M$ or +", "label": "2M$ or +"},
    {"value": "Requested", "label": "Requested"},
]

PROD_OPTIONS = [
    {"value": "3% or +", "label": "3% or +"},
    {"value": "2% or +", "label": "2% or +"},
    {"value": "1% or +", "label": "1% or +"},
    {"value": "less than 1%", "label": "less than 1%"},
    {"value": "Neg", "label": "Neg"},
    {"value": "Requested", "label": "Requested"},
]


# Maps an onboarding option-list key to its pld_scoring_rules.criteria_type.
# Most keys match 1:1; "prod" is the historical onboarding/UI field name for
# the "productivity" scoring criterion.
_CRITERIA_TYPE_BY_OPTION_KEY = {
    "top": "top",
    "lta": "lta",
    "sqma": "sqma",
    "family_coverage": "family_coverage",
    "competitiveness": "competitiveness",
    "geo_coverage": "geo_coverage",
    "cons_or_wd": "cons_or_wd",
    "financial_health": "financial_health",
    "prod_lia_ins": "prod_lia_ins",
    "prod": "productivity",
    "quality_certification": "quality_certification",
}


async def get_onboarding_selection_options(db: AsyncSession) -> Dict[str, Any]:
    """Return the onboarding select options payload for the frontend.

    Built live from pld_scoring_rules -- the same table SupplierRelationService
    queries to score submitted evaluations (see get_class_score() /
    CRITERIA_VALUE_NORMALIZATION in supplier_relations/service.py) -- so the
    dropdown options shown to users and the scoring table backing them can
    never drift apart the way the old hardcoded TOP_OPTIONS/etc. lists did.

    Each option carries its live score alongside {value, label}, so the
    frontend can compute a class-score preview from this same response
    instead of keeping a second, separately-maintained score table.
    """
    stmt = (
        select(
            PldScoringRules.criteria_type,
            PldScoringRules.min_value,
            PldScoringRules.score,
        )
        .where(PldScoringRules.is_active.is_(True))
        .where(PldScoringRules.criteria_type.is_not(None))
        .where(PldScoringRules.min_value.is_not(None))
        .order_by(
            PldScoringRules.criteria_type,
            PldScoringRules.score.desc(),
            PldScoringRules.min_value,
        )
    )
    rows = (await db.execute(stmt)).all()

    options_by_criteria_type: Dict[str, List[Dict[str, Any]]] = {}
    for criteria_type, min_value, score in rows:
        options_by_criteria_type.setdefault(criteria_type, []).append(
            {
                "value": min_value,
                "label": min_value,
                "score": float(score) if score is not None else None,
            }
        )

    result: Dict[str, Any] = {
        key: options_by_criteria_type.get(criteria_type, [])
        for key, criteria_type in _CRITERIA_TYPE_BY_OPTION_KEY.items()
    }
    # Unit-certification document taxonomy (a different concept from the
    # quality_certification PLD scoring criterion above -- these describe
    # actual certification documents tracked per supplier unit) stay
    # hand-maintained; they have no scoring rule / min_value shape to derive from.
    result["certification_types"] = CERTIFICATION_TYPE_OPTIONS
    result["certification_standard_types"] = CERTIFICATION_STANDARD_TYPE_OPTIONS
    result["cert_types_by_standard"] = CERT_TYPES_BY_STANDARD
    return result
