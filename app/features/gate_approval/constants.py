"""Sourcing Committee roles and per-tier mandatory-approver mapping (Phase 1-4)."""
from __future__ import annotations

ROLE_PURCHASING_DIRECTOR = "Purchasing Director"
ROLE_PLANT_MANAGER = "Plant Manager"
ROLE_PROJECT_LEADER = "Project Leader"
ROLE_PRODUCT_LINE_MANAGER = "Product Line Manager"
ROLE_COO_VP = "COO/VP"
ROLE_CEO = "CEO"
ROLE_IDEA_OWNER = "Idea Owner"
ROLE_QUALITY = "Quality"
ROLE_ENGINEERING = "Engineering"
ROLE_FINANCE = "Finance"
ROLE_OPERATIONS = "Operations"
ROLE_SUPPLY_CHAIN = "Supply Chain"

ALL_ROLES = (
    ROLE_PURCHASING_DIRECTOR,
    ROLE_PLANT_MANAGER,
    ROLE_PROJECT_LEADER,
    ROLE_PRODUCT_LINE_MANAGER,
    ROLE_COO_VP,
    ROLE_CEO,
    ROLE_IDEA_OWNER,
    ROLE_QUALITY,
    ROLE_ENGINEERING,
    ROLE_FINANCE,
    ROLE_OPERATIONS,
    ROLE_SUPPLY_CHAIN,
)

COMMITTEE_LEVELS = ("Light", "Intermediate", "Full")

# Tier-dependent mandatory roles — applies ONLY to the Phase 1 gate, where the
# committee level is chosen. Phase 2/3/4 always use CORE_MANDATORY_ROLES below,
# regardless of which tier was picked at Phase 1.
MANDATORY_ROLES_BY_TIER = {
    "Light": [ROLE_PURCHASING_DIRECTOR, ROLE_PLANT_MANAGER, ROLE_PROJECT_LEADER],
    "Intermediate": [
        ROLE_PURCHASING_DIRECTOR,
        ROLE_PLANT_MANAGER,
        ROLE_PROJECT_LEADER,
        ROLE_PRODUCT_LINE_MANAGER,
        ROLE_COO_VP,
    ],
    "Full": [ROLE_PURCHASING_DIRECTOR, ROLE_PLANT_MANAGER, ROLE_PROJECT_LEADER, ROLE_CEO],
}

# Phase 2/3/4 mandatory roles — fixed, independent of committee tier. Everyone
# else (COO/VP, Quality, Engineering, Finance, Operations, Supply Chain) is
# optional ("if required") at these phases.
CORE_MANDATORY_ROLES = [ROLE_PURCHASING_DIRECTOR, ROLE_PLANT_MANAGER, ROLE_PROJECT_LEADER]

COMMITTEE_ELIGIBLE_PHASES = ("Phase 1", "Phase 2", "Phase 3", "Phase 4")


def mandatory_roles_for_phase(phase_status: str, tier: str) -> list[str]:
    """Mandatory approver roles for a given phase/tier combination."""
    if phase_status == "Phase 1":
        return MANDATORY_ROLES_BY_TIER[tier]
    return CORE_MANDATORY_ROLES
