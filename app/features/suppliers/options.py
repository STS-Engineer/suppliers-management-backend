"""Supplier onboarding select options derived from the board data sheet."""

from __future__ import annotations

from typing import Any, Dict


TOP_OPTIONS = [
    {"value": "30 days net", "label": "30 days net"},
    {"value": "30 days end of month or +", "label": "30 days end of month or +"},
    {"value": "60 days net", "label": "60 days net"},
    {"value": "60 days end of month or +", "label": "60 days end of month or +"},
    {"value": "Cash in Advance", "label": "Cash in Advance"},
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
    {"value": "Signed M/Res/not sent", "label": "Signed M/Res/not sent"},
]

FAMILY_COVERAGE_OPTIONS = [
    {
        "value": "Supplier can make 1 family requirements",
        "label": "Supplier can make 1 family requirements",
    },
    {
        "value": "Supplier can make all the family requirements",
        "label": "Supplier can make all the family requirements",
    },
    {
        "value": "Supplier can make only of few family requirements",
        "label": "Supplier can make only of few family requirements",
    },
    {
        "value": "Supplier can make the main family requirements",
        "label": "Supplier can make the main family requirements",
    },
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
    {"value": "Biweekly Del.", "label": "Biweekly Del."},
    {"value": "Cons. Or Daily Deliveries", "label": "Cons. Or Daily Deliveries"},
    {"value": "DDP or Weekly Del.", "label": "DDP or Weekly Del."},
    {"value": "Other", "label": "Other"},
]

FINANCIAL_HEALTH_OPTIONS = [
    {"value": "Good", "label": "Good"},
    {"value": "To Monitor", "label": "To Monitor"},
    {"value": "At Risk", "label": "At Risk"},
]

CERTIFICATION_TYPE_OPTIONS = [
    {
        "value": "IATF / ISO9001 (cat BCD)",
        "label": "IATF / ISO9001 (cat BCD)",
    },
    {"value": "ISO9001", "label": "ISO9001"},
    {"value": "None", "label": "None"},
]

PROD_LIA_INS_OPTIONS = [
    {"value": "2M$ or +", "label": "2M$ or +"},
    {"value": "1M$ or +", "label": "1M$ or +"},
    {"value": "None", "label": "None"},
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
        "quality_certification": CERTIFICATION_TYPE_OPTIONS,
        "prod_lia_ins": PROD_LIA_INS_OPTIONS,
        "prod": PROD_OPTIONS,
    }
