"""Supplier onboarding select options derived from the board data sheet."""

from __future__ import annotations

from typing import Any, Dict


TOP_OPTIONS = [
    {"value": "15 days net", "label": "15 days net"},
    {"value": "30 days net", "label": "30 days net"},
    {"value": "30 days end of month or +", "label": "30 days end of month or +"},
    {"value": "60 days net", "label": "60 days net"},
    {"value": "60 days end of month or +", "label": "60 days end of month or +"},
    {"value": "60 days eom or +", "label": "60 days eom or +"},
    {"value": "Cash in Advance", "label": "Cash in Advance"},
    {"value": "Requested", "label": "Requested"},
]

LTA_OPTIONS = [
    {"value": "1 year", "label": "1 year"},
    {"value": "2 years", "label": "2 years"},
    {"value": "3 years/+", "label": "3 years/+"},
    {"value": "None/Invalid", "label": "None/Invalid"},
]

SQMA_OPTIONS = [
    {"value": "Rejected", "label": "Rejected"},
    {"value": "Signed", "label": "Signed"},
    {"value": "Signed m.res.", "label": "Signed m.res."},
    {"value": "Signed M.Res/not sent", "label": "Signed M.Res/not sent"},
]

FAMILY_COVERAGE_OPTIONS = [
    {"value": "100% Cov.", "label": "100% Cov."},
    {"value": "Main sub-Fam Cov.", "label": "Main sub-Fam Cov."},
    {"value": "1 sub-F or refs Cov.", "label": "1 sub-F or refs Cov."},
    {"value": "1 ref", "label": "1 ref"},
    {"value": "None", "label": "None"},
]

COMPETITIVENESS_OPTIONS = [
    {"value": "Best in Fam.", "label": "Best in Fam."},
    {"value": "Almost Best in Fam.", "label": "Almost Best in Fam."},
    {"value": "Ave. in Fam.", "label": "Ave. in Fam."},
    {"value": "Less Avg", "label": "Less Avg"},
    {"value": "Not Comp.", "label": "Not Comp."},
]

GEO_COVERAGE_OPTIONS = [
    {"value": "1 plant is covered", "label": "1 plant is covered"},
    {"value": "Main plants covered", "label": "Main plants covered"},
    {
        "value": "More than 50% plants are covered",
        "label": "More than 50% plants are covered",
    },
    {"value": "None", "label": "None"},
]

CONS_OR_WD_OPTIONS = [
    {"value": "Cons. or WD", "label": "Cons. or WD"},
    {"value": "Cons. or WD Inter. User", "label": "Cons. or WD Inter. User"},
    {"value": "Biweekly Del.", "label": "Biweekly Del."},
    {"value": "DDP or Weekly Del.", "label": "DDP or Weekly Del."},
]

FINANCIAL_HEALTH_OPTIONS = [
    {"value": "Good", "label": "Good"},
    {"value": "To Monitor", "label": "To Monitor"},
    {"value": "At Risk", "label": "At Risk"},
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
]


def get_onboarding_selection_options() -> Dict[str, Any]:
    """Return the onboarding select options payload for the frontend."""
    return {
        "top": TOP_OPTIONS,
        "lta": LTA_OPTIONS,
        "sqma": SQMA_OPTIONS,
        "family_coverage": FAMILY_COVERAGE_OPTIONS,
        "competitiveness": COMPETITIVENESS_OPTIONS,
        "geo_coverage": GEO_COVERAGE_OPTIONS,
        "cons_or_wd": CONS_OR_WD_OPTIONS,
        "financial_health": FINANCIAL_HEALTH_OPTIONS,
        "certification_types": CERTIFICATION_TYPE_OPTIONS,
        "certification_standard_types": CERTIFICATION_STANDARD_TYPE_OPTIONS,
        "cert_types_by_standard": CERT_TYPES_BY_STANDARD,
        "quality_certification": CERTIFICATION_TYPE_OPTIONS,
        "prod_lia_ins": PROD_LIA_INS_OPTIONS,
        "prod": PROD_OPTIONS,
    }
