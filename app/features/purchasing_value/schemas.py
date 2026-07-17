"""Purchasing value management schemas."""

from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Dict, Optional, List, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, computed_field, field_validator, model_validator


# ---------------------------------------------------------------------------
# STP nested JSON schemas
# ---------------------------------------------------------------------------

class STPRisks(BaseModel):
    """Stored as a single JSONB column — stp_risks."""
    material_indexation_before: Optional[str] = None   # Yes / No
    material_indexation_after: Optional[str] = None    # Yes / No
    material_indexation_desc: Optional[str] = None
    exchange_rate_before: Optional[str] = None         # Yes / No
    exchange_rate_after: Optional[str] = None          # Yes / No
    exchange_rate_desc: Optional[str] = None
    local_content_before: Optional[str] = None        # Yes / No
    local_content_after: Optional[str] = None         # Yes / No
    local_content_desc: Optional[str] = None
    quality_before: Optional[str] = None              # Yes / No
    quality_after: Optional[str] = None               # Yes / No
    quality_desc: Optional[str] = None
    other_before: Optional[str] = None                # Yes / No
    other_after: Optional[str] = None                 # Yes / No
    other_desc: Optional[str] = None
    # Spec questions — Yes / No / N/A
    material_same_spec: Optional[str] = None
    same_tooling: Optional[str] = None
    same_dimension: Optional[str] = None
    same_process: Optional[str] = None


class STPBenefits(BaseModel):
    """Stored as a single JSONB column — stp_benefits."""
    if_we_do: Optional[str] = None
    if_not: Optional[str] = None

# ---------------------------------------------------------------------------
# Reference values (single source of truth — mirrors the spec)
# ---------------------------------------------------------------------------

OPPORTUNITY_TYPES = ["Negotiation", "Sourcing", "Technical Productivity"]

# Accounting nature of a saving (orthogonal to the lever in opportunity_type):
#   Hard = real cost reduction, recognized in P&L / EBITDA (price actually drops)
#   Soft = cost avoidance (an inflationary/future cost is avoided; spend does not drop)
SAVING_NATURES = ["Hard", "Soft"]

# Entry mode = a sub-option WITHIN an opportunity_type that changes how the saving
# is captured (Olivier, call 2026-07-10):
#   Standard = normal STP price×quantity computation
#   Bonus    = single lump gain entered directly (Negotiation only; duration 1 month,
#              no quantities/prices, no cash)
#   Rework   = single lump gain entered directly (Technical Productivity only; e.g.
#              scrap parts reworked into usable; no incoterms / payment terms / cash)
ENTRY_MODES = ["Standard", "Bonus", "Rework"]
# Which entry_mode is allowed on which opportunity_type.
ENTRY_MODE_TYPE = {"Bonus": "Negotiation", "Rework": "Technical Productivity"}


def opp_entry_mode(opp) -> str:
    """entry_mode of an opportunity, defaulting to 'Standard' (incl. legacy NULL)."""
    return getattr(opp, "entry_mode", None) or "Standard"


def is_direct_gain(opp) -> bool:
    """Bonus / Rework: a single lump gain entered directly, not derived from a
    price×quantity grid, realized one-time over a single month."""
    return opp_entry_mode(opp) in ("Bonus", "Rework")


def _validate_entry_mode(value, opportunity_type):
    """Normalize + validate an entry_mode. "" / "Standard" collapse to None.
    When opportunity_type is known (create), enforce Bonus↔Negotiation and
    Rework↔Technical Productivity. Raises ValueError on an invalid combination."""
    if value in (None, "", "Standard"):
        return None
    if value not in ENTRY_MODES:
        raise ValueError(f"entry_mode must be one of {ENTRY_MODES}")
    required = ENTRY_MODE_TYPE.get(value)
    if opportunity_type is not None and required and opportunity_type != required:
        raise ValueError(f"entry_mode '{value}' is only allowed on {required} opportunities")
    return value

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

BUDGET_STATUSES = ["Budgeted", "Empty"]

# Per-fiscal-year budgeting status — DERIVED from validation, not set manually
# Validation dimension (auto, phase-derived) lives in suggested_status:
VALIDATION_STATUSES = ["In progress", "Validate"]
# Budget-commitment dimension (manual, set in Create Budget) lives in budget_status.
# 3 levels per Olivier's model: rien / opportunité / budgeted.
BUDGET_YEAR_STATUSES = ["Empty", "Opportunity", "Budgeted"]

CHANGE_MODES = ["Standard", "Silent"]

# Transaction currencies (per STP workbook "Data" sheet). EUR is the group
# reporting currency — consolidated totals convert to EUR via fx_rate_to_eur.
CURRENCIES = ["EUR", "USD", "RMB", "INR"]

PRIORITY_CATEGORIES = ["High", "Medium", "Low"]

PROJECT_STATUSES = ["On time", "Late", "Completed", "On hold"]

FINANCIAL_LINE_STATUSES = ["Draft", "Active", "Completed", "Cancelled"]

# ---------------------------------------------------------------------------
# PLD helpers
# ---------------------------------------------------------------------------

def compute_priority(p: Optional[float], lead: Optional[float], d: Optional[float]):
    """Returns (priority_score, priority_category) or (None, None)."""
    if p is None or lead is None or d is None:
        return None, None
    score = float(p) * float(lead) * float(d)
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


def compute_stp_financials(opp) -> dict:
    """
    Reproduce the financial formulas of the Excel workbook "format STP rev 1.2"
    (Phase 0 sheet). Cell mapping to Opportunity columns:
      E26/G26 = current_price / proposed_price        D13/D14 = annual_quantity_n1/n2
      E27-E29 = current_price_n1..n3                  G13/G14 = annual_quantity_n3/n4
      G27-G29 = proposed_price_n1..n3                 E30/G30 = bonus_before / bonus_after
      D41 = tooling_cost   D45 = total costs          E23/G23 = consignment before/after
      E24/G24 = TOP days   E25/G25 = transit days

    Formulas (verbatim from the workbook):
      Full year (D51)     = (E26-G26)*D13 + E30-G30
      Period    (D52)     = D13*(E26-G26) + D14*(E27-G27) + G13*(E28-G28) + G14*(E29-G29) + E30-G30
      ROI full            = full_year_saving / total_investment * 100
      ROI period          = period_saving    / total_investment * 100
        (Purchasing-director rule 17/06/2026: gain ÷ TOTAL investment — tooling +
         travel + qualification + other — not tooling alone; ×100 for a %.)
      Inventory gap (D55) = IF(E23="Yes",0,(E25+14)*AVG/360)*E26 - IF(G23="Yes",0,(G25+14)*AVG/360)*G26
      AP gap        (D56) = -AVG*(E24*E26 - G24*G26)/360
      with AVG = AVERAGE(N1..N4) (blanks ignored, like Excel AVERAGE)

    Empty cells count as 0 (Excel blank semantics). Returns None per metric when
    the minimum inputs (both base prices + qty N1) are missing. ROIs are ×100 (%).
    """
    def f(v) -> float:
        return float(v) if v is not None else 0.0

    def rnd0(v):
        return (0.0 if abs(round(v, 2)) < 0.005 else round(v, 2)) if v is not None else None

    # Bonus / Rework: a single lump gain entered directly in expected_annual_saving,
    # realized one-time (year N only). No price grid, no cash. ROI still uses the
    # investment total (a rework can carry a cost) when present.
    if is_direct_gain(opp):
        gain = f(opp.expected_annual_saving)
        total_inv = f(opp.tooling_cost) + f(opp.travel_cost) + f(opp.qualification_cost) + f(opp.other_cost)
        roi = gain / total_inv * 100 if (gain and total_inv > 0) else None
        g = rnd0(gain) if gain else None
        return {
            "full_year_saving": g,
            "period_saving": g,
            "roi_full_year_pct": rnd0(roi),
            "roi_period_pct": rnd0(roi),
            "inventory_gap": None,
            "ap_gap": None,
            "saving_per_year": [g, None, None, None],
        }

    price_before = [f(opp.current_price), f(opp.current_price_n1),
                    f(opp.current_price_n2), f(opp.current_price_n3)]
    price_after = [f(opp.proposed_price), f(opp.proposed_price_n1),
                   f(opp.proposed_price_n2), f(opp.proposed_price_n3)]
    qty = [f(opp.annual_quantity_n1), f(opp.annual_quantity_n2),
           f(opp.annual_quantity_n3), f(opp.annual_quantity_n4)]
    bonus_delta = f(opp.bonus_before) - f(opp.bonus_after)

    has_base = opp.current_price is not None and opp.proposed_price is not None
    full_year = period = None
    # Per-year saving (Excel D52 broken out). Year N includes the bonus delta and
    # equals full_year; years N+1..N+3 are pure price×qty. The four sum to period.
    saving_per_year = [None, None, None, None]
    if has_base and opp.annual_quantity_n1:
        full_year = (price_before[0] - price_after[0]) * qty[0] + bonus_delta
        period = sum(q * (b - a) for q, b, a in zip(qty, price_before, price_after)) + bonus_delta
        saving_per_year = [
            (price_before[i] - price_after[i]) * qty[i] + (bonus_delta if i == 0 else 0.0)
            for i in range(4)
        ]

    # ROI = gain ÷ TOTAL investment × 100 (purchasing-director rule 17/06/2026).
    total_investment = f(opp.tooling_cost) + f(opp.travel_cost) + f(opp.qualification_cost) + f(opp.other_cost)
    roi_full = full_year / total_investment * 100 if (full_year is not None and total_investment > 0) else None
    roi_period = period / total_investment * 100 if (period is not None and total_investment > 0) else None

    present_qty = [q for q in (opp.annual_quantity_n1, opp.annual_quantity_n2,
                               opp.annual_quantity_n3, opp.annual_quantity_n4) if q is not None]
    avg_qty = sum(float(q) for q in present_qty) / len(present_qty) if present_qty else None

    # Cash (inventory + AP gap) comes from changes in logistics / payment terms
    # (transit, TOP days, consignment). It applies to every standard type — including
    # Negotiation, which can renegotiate payment terms / consignment (decision
    # 2026-07-13). Bonus / Rework (one-time gains) are already excluded above.
    inventory_gap = ap_gap = None
    if avg_qty is not None and has_base:
        inv_before = 0.0 if opp.consignment_before == "Yes" else (f(opp.transit_days_before) + 14) * avg_qty / 360
        inv_after = 0.0 if opp.consignment_after == "Yes" else (f(opp.transit_days_after) + 14) * avg_qty / 360
        inventory_gap = inv_before * price_before[0] - inv_after * price_after[0]
        ap_gap = -avg_qty * (f(opp.top_days_before) * price_before[0]
                             - f(opp.top_days_after) * price_after[0]) / 360

    # round to 2dp; snap negative-zero / sub-cent magnitudes to 0.0 (avoids "-0" display)
    def rnd(v):
        return (0.0 if abs(round(v, 2)) < 0.005 else round(v, 2)) if v is not None else None
    return {
        "full_year_saving": rnd(full_year),
        "period_saving": rnd(period),
        "roi_full_year_pct": rnd(roi_full),
        "roi_period_pct": rnd(roi_period),
        "inventory_gap": rnd(inventory_gap),
        "ap_gap": rnd(ap_gap),
        "saving_per_year": [rnd(v) for v in saving_per_year],
    }


def add_months(d: date, months: int) -> date:
    """Add N calendar months to a date, always landing on day 1."""
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    return date(year, month, 1)


def add_months_preserve_day(d: date, months: int) -> date:
    """Add N calendar months while preserving the day when possible.

    If the target month has fewer days (e.g. 31st -> February), clamp to the
    last valid day of that month. This is used by the budgeting split because
    the budget window is now prorated by actual days, not whole months only.
    """
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    day = min(d.day, monthrange(year, month)[1])
    return date(year, month, day)


def budget_year_for_date(d: date) -> int:
    """Budget year label for a date.

    Business rule: Budget year N = 1 January N → 31 December N (calendar year).
    """
    return d.year


def budget_year_bounds(fiscal_year: int) -> tuple[date, date]:
    """Return [start, end_exclusive) of a budget year (calendar year)."""
    return date(fiscal_year, 1, 1), date(fiscal_year + 1, 1, 1)


def recommend_savings_start_date(opp):
    """Suggested savings-start date = study/planned anchor + planned phase 1–3 weeks
    (study + feasibility + deployment, i.e. when deployment completes). Phase 4
    (LLC/closure) is excluded — savings flow once parts are in production.

    This is only a recommendation the user can apply to Planned Start; it is NOT
    used directly in any financial calculation. Returns a date or None.
    """
    anchor = getattr(opp, "study_start_date", None) or getattr(opp, "planned_start_date", None)
    if anchor is None:
        return None
    weeks = sum(int(getattr(opp, f"phase{i}_weeks", 0) or 0) for i in (1, 2, 3))
    return anchor + timedelta(weeks=weeks) if weeks > 0 else anchor


def compute_savings_start_date(opp):
    """The date savings actually start flowing — the single anchor used by the
    monthly profile, planned-end, calendar-year split and budgeting.

    Priority:
      1. real_start_date  — the actual start once entered (Phase 3+).
      2. planned_start_date — the user's explicit estimate of when savings start.
         This is the source of truth; the user can set it in Phase 0–2 and the UI
         offers recommend_savings_start_date() as a suggested default.
      3. study_start + planned phase 1–3 weeks — fallback estimate when no Planned
         Start has been entered yet.

    Returns a date or None.
    """
    if getattr(opp, "real_start_date", None) is not None:
        return opp.real_start_date
    if getattr(opp, "planned_start_date", None) is not None:
        return opp.planned_start_date
    return recommend_savings_start_date(opp)


def compute_budget_year_portions(per_year_savings, savings_start, duration_months=None) -> list:
    """Split savings across budget years using day-level prorata.

    `per_year_savings` is a list of annual gross savings — the STP per-year windows
    [year N, N+1, N+2, N+3] (escalating with the negotiated prices), or a single
    repeated annual figure when no STP prices exist. Each annual window runs for
    12 calendar months from `savings_start`; the whole stream is capped at
    `duration_months` (and at the number of windows available).

    Budget year rule: year N = 01 Jan N -> 31 Dec N (calendar year). Allocation is
    done by overlap in actual days, not by whole months. This means a start on 15 June
    allocates only the exact days from 15 June to 31 December into that budget year.

    portion_kind per budget year: "Total" if it receives a full budget year,
    "Applicable" for the partial first year, "Residual" for the partial tail.

    Returns [{"fiscal_year": int, "amount": float, "kind": str}], sorted by year.
    Empty when inputs are missing.

    All internal arithmetic uses Decimal to avoid IEEE 754 float accumulation drift.
    A residual plug on the last row guarantees sum(amounts) == round(sum(windows), 2)
    to the cent — same reconciliation contract as _rounded_series().
    """
    if savings_start is None or not per_year_savings:
        return []
    windows = [Decimal(str(s)) for s in per_year_savings if s is not None]
    if not windows:
        return []
    max_months = 12 * len(windows)
    months = min(int(duration_months), max_months) if duration_months else max_months
    if months <= 0:
        return []

    TWO_DP = Decimal("0.01")

    overall_end = add_months_preserve_day(savings_start, months)
    amount: dict[int, Decimal] = {}
    dcount: dict[int, int] = {}

    for idx, annual in enumerate(windows):
        window_start = add_months_preserve_day(savings_start, idx * 12)
        window_end = add_months_preserve_day(savings_start, (idx + 1) * 12)
        if window_start >= overall_end:
            break

        effective_end = min(window_end, overall_end)
        window_days = (window_end - window_start).days
        if window_days <= 0 or effective_end <= window_start:
            continue

        cursor = window_start
        while cursor < effective_end:
            fy = budget_year_for_date(cursor)
            _, fy_end = budget_year_bounds(fy)
            slice_end = min(effective_end, fy_end)
            days = (slice_end - cursor).days
            if days > 0:
                prorated = annual * Decimal(days) / Decimal(window_days)
                amount[fy] = amount.get(fy, Decimal("0")) + prorated
                dcount[fy] = dcount.get(fy, 0) + days
            cursor = slice_end

    years = sorted(amount.keys())
    out = []
    for idx, yr in enumerate(years):
        fy_start, fy_end = budget_year_bounds(yr)
        fy_days = (fy_end - fy_start).days
        if dcount[yr] >= fy_days:
            kind = "Total"
        elif idx == 0:
            kind = "Applicable"
        else:
            kind = "Residual"
        out.append({"fiscal_year": yr, "amount": amount[yr].quantize(TWO_DP), "kind": kind})

    # Residual plug: absorb any rounding drift into the last row.
    # Target is the rounded sum of monthly amounts ACTUALLY flowed (not
    # the sum of all annual windows, which would be wrong when duration_months
    # truncates the period short of the full window count).
    target = sum(amount.values()).quantize(TWO_DP)
    computed = sum(r["amount"] for r in out)
    residual = target - computed
    if residual != 0 and out:
        out[-1]["amount"] = (out[-1]["amount"] + residual).quantize(TWO_DP)

    # Return as float for backward compatibility with callers that expect float.
    for r in out:
        r["amount"] = float(r["amount"])
    return out


def compute_saving_by_calendar_year(opp) -> dict:
    """Estimated STP saving allocated to each budget year (calendar year Jan–Dec)."""
    per_year = compute_stp_financials(opp)["saving_per_year"]
    duration = getattr(opp, "duration_months", None)
    rows = compute_budget_year_portions(
        per_year, compute_savings_start_date(opp), int(duration) if duration else None
    )
    return {str(r["fiscal_year"]): r["amount"] for r in rows}


def compute_saving_to_budget_per_year(opp) -> list:
    """"Saving à budgéter" — the year-over-year price DROP, per STP year [N, N+1, N+2, N+3].

    Business rule (Olivier, call 2026-07-10): what actually gets budgeted each year is
    only the *incremental* new gain vs the previous year, NOT the full run-rate saving
    reconducted every year:
      - Year N   = the full first-year saving (price gap × qty, incl. the bonus delta).
      - Year N+k = (proposed_price_{k-1} − proposed_price_k) × quantity_k.

    So when the negotiated price is flat across years the increment is 0 for N+1..N+3
    (even if the quantity changes), and the whole opportunity is budgeted in year N only.
    Negatives are allowed (no clamp) — a price that goes back up simply budgets negative.

    This differs from compute_stp_financials()["saving_per_year"] (the full per-year
    run-rate, summing to `period_saving` = the "value of opportunity"). Returns a
    4-element list; entries are None when the base inputs are missing.
    """
    fin = compute_stp_financials(opp)
    per_year = fin["saving_per_year"]  # [full year N, N+1, N+2, N+3]

    def f(v) -> float:
        return float(v) if v is not None else 0.0

    proposed = [f(opp.proposed_price), f(opp.proposed_price_n1),
                f(opp.proposed_price_n2), f(opp.proposed_price_n3)]
    qty = [f(opp.annual_quantity_n1), f(opp.annual_quantity_n2),
           f(opp.annual_quantity_n3), f(opp.annual_quantity_n4)]

    def rnd(v):
        return (0.0 if abs(round(v, 2)) < 0.005 else round(v, 2)) if v is not None else None

    to_budget = [None, None, None, None]
    if per_year[0] is not None:
        to_budget[0] = per_year[0]  # full year-N saving (already rounded)
        for i in range(1, 4):
            to_budget[i] = rnd((proposed[i - 1] - proposed[i]) * qty[i])
    return to_budget


def compute_saving_to_budget_by_year(opp) -> dict:
    """"Saving à budgéter" allocated to each calendar year (Jan–Dec), start-date aware.

    Reuses compute_budget_year_portions() but feeds it the INCREMENTAL per-year windows
    (compute_saving_to_budget_per_year) instead of the full run-rate savings, so a
    mid-year start still splits each 12-month window across calendar years (half/half),
    while a flat price collapses the whole budget onto year N. This is the value that
    drives the per-fiscal-year budget rows.
    """
    per_year = compute_saving_to_budget_per_year(opp)
    duration = getattr(opp, "duration_months", None)
    rows = compute_budget_year_portions(
        per_year, compute_savings_start_date(opp), int(duration) if duration else None
    )
    return {str(r["fiscal_year"]): r["amount"] for r in rows}


def compute_duration_months(opp) -> Optional[int]:
    """Derive the budgeting duration from whether the negotiated price changes.

    Coherence rule (Olivier, call 2026-07-10): a price that stays flat cannot be
    budgeted beyond its first 12-month window, so:
      - flat proposed price across N..N+3            -> 12 months
      - price still changing up to year N+k          -> (k + 1) × 12 months
    (flat -> 12, one change -> 24, ... four-year change -> 48). Returns None when the
    STP base inputs are missing (non-STP opp keeps its manually-entered duration).
    """
    # Bonus / Rework are one-time gains realized in a single month.
    if is_direct_gain(opp):
        return 1

    has_base = opp.current_price is not None and opp.proposed_price is not None
    if not (has_base and opp.annual_quantity_n1):
        return None

    def f(v) -> float:
        return float(v) if v is not None else 0.0

    proposed = [f(opp.proposed_price), f(opp.proposed_price_n1),
                f(opp.proposed_price_n2), f(opp.proposed_price_n3)]
    last_change = 0
    for i in range(1, 4):
        if abs(proposed[i] - proposed[i - 1]) >= 0.005:
            last_change = i
    return (last_change + 1) * 12


# ---------------------------------------------------------------------------
# Opportunity schemas
# ---------------------------------------------------------------------------

class OpportunityCreateRequest(BaseModel):
    opportunity_name: str = Field(..., min_length=1, max_length=500)
    opportunity_type: str = Field(..., description="Negotiation|Sourcing|Technical Productivity")
    saving_nature: Optional[str] = Field(
        None, description="Hard (cost reduction, P&L impact) | Soft (cost avoidance)"
    )
    entry_mode: Optional[str] = Field(
        None, description="Standard | Bonus (Negotiation) | Rework (Technical Productivity)"
    )
    idea_owner: str = Field(..., description="Email of the initial pilot (buyer)")
    description: Optional[str] = None
    plant_id: Optional[int] = None
    supplier_id: Optional[int] = None
    budget_year: Optional[int] = None
    budget_status: Optional[str] = Field(
        None, description="Deprecated compatibility field; ignored by the backend."
    )

    @field_validator("opportunity_type")
    @classmethod
    def validate_opportunity_type(cls, v: str) -> str:
        if v not in OPPORTUNITY_TYPES:
            raise ValueError(f"opportunity_type must be one of {OPPORTUNITY_TYPES}")
        return v

    @field_validator("saving_nature")
    @classmethod
    def validate_saving_nature(cls, v: Optional[str]) -> Optional[str]:
        if v not in (None, "") and v not in SAVING_NATURES:
            raise ValueError(f"saving_nature must be one of {SAVING_NATURES}")
        return v or None

    @model_validator(mode="after")
    def validate_entry_mode(self) -> "OpportunityCreateRequest":
        self.entry_mode = _validate_entry_mode(self.entry_mode, self.opportunity_type)
        return self


class OpportunityUpdateRequest(BaseModel):
    """Full Phase-0 editable payload — all fields optional."""
    opportunity_name: Optional[str] = Field(None, min_length=1, max_length=500)
    saving_nature: Optional[str] = Field(
        None, description="Hard (cost reduction, P&L impact) | Soft (cost avoidance)"
    )
    entry_mode: Optional[str] = Field(
        None, description="Standard | Bonus (Negotiation) | Rework (Technical Productivity)"
    )
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
    budget_status: Optional[str] = Field(
        None, description="Deprecated compatibility field; ignored by the backend."
    )
    budget_year: Optional[int] = None
    change_mode: Optional[str] = Field(None, description="Standard | Silent")
    currency: Optional[str] = Field(None, description="EUR | USD | RMB | INR")
    fx_rate_to_eur: Optional[Decimal] = Field(None, ge=0, description="Rate to convert this currency to EUR")

    @model_validator(mode="after")
    def check_fx_rate_when_currency_set(self) -> "OpportunityUpdateRequest":
        if self.currency and self.currency != "EUR":
            if self.fx_rate_to_eur is None or self.fx_rate_to_eur <= 0:
                raise ValueError(
                    f"fx_rate_to_eur must be provided and > 0 when currency is '{self.currency}'. "
                    f"Example: for USD enter 0.920000 (meaning 1 {self.currency} = 0.92 EUR)."
                )
        return self

    @field_validator("saving_nature")
    @classmethod
    def validate_saving_nature(cls, v: Optional[str]) -> Optional[str]:
        if v not in (None, "") and v not in SAVING_NATURES:
            raise ValueError(f"saving_nature must be one of {SAVING_NATURES}")
        return v or None

    @field_validator("entry_mode")
    @classmethod
    def validate_entry_mode(cls, v: Optional[str]) -> Optional[str]:
        # "" = not provided (no change). "Standard" is kept so the service can clear
        # the mode; type-consistency (Bonus↔Negotiation…) is enforced in the service.
        if v == "":
            return None
        if v is not None and v not in ENTRY_MODES:
            raise ValueError(f"entry_mode must be one of {ENTRY_MODES}")
        return v
    assumptions_summary: Optional[str] = None
    comments: Optional[str] = None
    plant_id: Optional[int] = None
    supplier_id: Optional[int] = None
    # Owners
    purchasing_owner: Optional[str] = None
    conversion_owner: Optional[str] = None
    # PLD scores — P and L are auto-calculated but can be manually overridden
    # D = Easy(1) / Relatively easy(2) / Moderately difficult(3) / Difficult(4) / Very Difficult(5)
    payback_score: Optional[Decimal] = Field(None, ge=1, le=5)
    lead_time_score: Optional[Decimal] = Field(None, ge=1, le=5)
    difficulty_score: Optional[Decimal] = Field(None, ge=1, le=5)
    # Priority override — when True the computed P×L×D category is bypassed
    priority_locked: Optional[bool] = None
    priority_category_override: Optional[str] = None  # "High" | "Medium" | "Low" | ""
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
    place_of_incoterms_before: Optional[str] = None
    place_of_incoterms_after: Optional[str] = None
    top_days_before: Optional[int] = None
    top_days_after: Optional[int] = None
    transit_days_before: Optional[int] = None
    transit_days_after: Optional[int] = None
    # country_before not stored — read from SupplierUnit.country
    country_after: Optional[str] = None
    bonus_before: Optional[Decimal] = None
    bonus_after: Optional[Decimal] = None
    consignment_before: Optional[str] = None
    consignment_after: Optional[str] = None
    # Before-prices for years N+1/N+2/N+3 (current supplier price evolution)
    current_price_n1: Optional[Decimal] = None
    current_price_n2: Optional[Decimal] = None
    current_price_n3: Optional[Decimal] = None
    supplier_asked: Optional[bool] = None
    supplier_asked_result: Optional[str] = None
    tooling_cost: Optional[Decimal] = None
    travel_cost: Optional[Decimal] = None
    qualification_cost: Optional[Decimal] = None
    other_cost: Optional[Decimal] = None
    stp_risks: Optional[STPRisks] = None
    stp_benefits: Optional[STPBenefits] = None
    phase1_weeks: Optional[int] = None
    phase2_weeks: Optional[int] = None
    phase3_weeks: Optional[int] = None
    phase4_weeks: Optional[int] = None
    reason_productivity: Optional[bool] = None
    reason_quality: Optional[bool] = None
    reason_capacity: Optional[bool] = None
    reason_other: Optional[str] = None
    secondary_plants: Optional[str] = None
    changed_by: Optional[str] = None


class STPRevisionRequestPayload(BaseModel):
    """Buyer submits proposed STP price/volume changes for Director approval.

    Approvers are resolved server-side from users with role purchasing_director
    or vp_conversion — not chosen by the requester.
    """
    note: str = Field(..., min_length=1, description="Justification — mandatory for audit trail")
    requested_by: Optional[str] = None
    # Proposed STP baseline fields (any subset — only provided fields are changed)
    current_price:      Optional[Decimal] = None
    proposed_price:     Optional[Decimal] = None
    current_price_n1:   Optional[Decimal] = None
    current_price_n2:   Optional[Decimal] = None
    current_price_n3:   Optional[Decimal] = None
    proposed_price_n1:  Optional[Decimal] = None
    proposed_price_n2:  Optional[Decimal] = None
    proposed_price_n3:  Optional[Decimal] = None
    annual_quantity_n1: Optional[int] = None
    annual_quantity_n2: Optional[int] = None
    annual_quantity_n3: Optional[int] = None
    annual_quantity_n4: Optional[int] = None
    bonus_before:       Optional[Decimal] = None
    bonus_after:        Optional[Decimal] = None


class STPRevisionDecisionPayload(BaseModel):
    """Director approves or rejects a pending STP revision request."""
    decision: str = Field(..., description="Approved | Rejected")
    decided_by: Optional[str] = None
    note: Optional[str] = None


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
    change_mode: Optional[str] = None
    change_mode_comment: Optional[str] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class PhaseSnapshotResponse(BaseModel):
    snapshot_id: int
    opportunity_id: int
    phase_from: Optional[str] = None
    phase_to: Optional[str] = None
    gate_decision: Optional[str] = None
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None
    gate_comments: Optional[str] = None
    opportunity_snapshot: Optional[dict] = None

    model_config = ConfigDict(from_attributes=True)


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
    change_mode: Optional[str] = Field(None, description="Standard | Silent")
    change_mode_comment: Optional[str] = None
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
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None

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
    expected_annual_saving: Decimal = Field(..., ge=0, description="Annual saving for this component (€)")
    planned_start_date: Optional[date] = None
    duration_months: Optional[int] = Field(12, ge=1, le=120)
    added_by: Optional[str] = None


class EscalateRequest(BaseModel):
    escalation_reason: str = Field(..., min_length=3)
    escalated_by: Optional[str] = None
    # Optional: email specific recipients beyond the purchasing_owner
    extra_recipients: Optional[List[str]] = None


class EscalateActionItemRequest(BaseModel):
    recipient_email: EmailStr
    subject: str = Field(..., min_length=3)
    message: Optional[str] = None


class RecoveryUpdateRequest(BaseModel):
    recovery_status: Literal["Planned", "In Progress", "Done"] = Field(
        ..., description="Planned | In Progress | Done"
    )
    recovery_note: Optional[str] = None
    recovery_target_date: Optional[date] = None
    recovery_amount: Optional[float] = Field(None, ge=0)
    updated_by: Optional[str] = None


class FinancialLineCompleteRequest(BaseModel):
    completed_by: Optional[str] = None
    comments: Optional[str] = None


class FinancialLineReviseBaselineRequest(BaseModel):
    """Correct the committed baseline of a line that already has actuals (Phase 3+).

    Sourcing/Technical Productivity: provide the STP fields that changed
    (price/quantity/bonus — any subset); expected saving, ROI and cash figures
    are recomputed from them via the same engine as the rest of the app.
    Negotiation/Cash (no price/quantity breakdown): provide revised_saving instead.
    """
    note: str = Field(..., min_length=1, description="Reason for revision — required for audit")
    revised_by: Optional[str] = None
    # Negotiation / Cash types only
    revised_saving: Optional[Decimal] = Field(None, ge=0, description="New expected annual saving (€) — Negotiation/Cash types only")
    # Sourcing / Technical Productivity types only (any subset)
    current_price:      Optional[Decimal] = None
    proposed_price:     Optional[Decimal] = None
    current_price_n1:   Optional[Decimal] = None
    current_price_n2:   Optional[Decimal] = None
    current_price_n3:   Optional[Decimal] = None
    proposed_price_n1:  Optional[Decimal] = None
    proposed_price_n2:  Optional[Decimal] = None
    proposed_price_n3:  Optional[Decimal] = None
    annual_quantity_n1: Optional[int] = None
    annual_quantity_n2: Optional[int] = None
    annual_quantity_n3: Optional[int] = None
    annual_quantity_n4: Optional[int] = None
    bonus_before:       Optional[Decimal] = None
    bonus_after:        Optional[Decimal] = None


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
    cumulated_real_saving_ltd: Optional[Decimal] = None
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
    recovery_target_date: Optional[date] = None
    recovery_amount: Optional[Decimal] = None
    recovery_history: Optional[str] = None
    recovery_updated_at: Optional[datetime] = None
    recovery_baseline_gap: Optional[Decimal] = None
    recovery_baseline_set_at: Optional[datetime] = None
    monthly_financials: List[MonthlyFinancialResponse] = Field(default_factory=list)

    @computed_field
    @property
    def pacing_status(self) -> Optional[str]:
        """Cadence automatique (règle Monday Action Status) : 'Late' si le réalisé
        cumulé est en retard sur l'attendu YTD (delta_vs_expected_ytd < 0), sinon
        'On time'. Dérivé — n'écrase pas le statut manuel du projet."""
        if self.delta_vs_expected_ytd is None:
            return None
        return "Late" if self.delta_vs_expected_ytd < 0 else "On time"

    class Config:
        from_attributes = True


# Ordered list of Monday.com delta-reason values (displayed as dropdown options).
DELTA_REASON_VALUES: list[str] = [
    "As planned",
    "NTS",
    "Inventory issue",
    "Higher productivity / Volume",
    "Inventory reduction",
    "Check Data",
    "Lower volume / Late start",
    "Cancel & Replace",
    "Stuck",
    "Budget Mist",
    "Price increase",
    "Strategy change",
    "Supplier Issue",
    "Recovery",
    "Late action",
    "RM Not Available",
]


def _validate_delta_reasons(value: Optional[List[str]]) -> Optional[List[str]]:
    if value is None:
        return None
    invalid = [reason for reason in value if reason not in DELTA_REASON_VALUES]
    if invalid:
        raise ValueError(
            "Invalid delta_reason value(s): " + ", ".join(sorted(set(invalid)))
        )
    return value


class BudgetDecision(BaseModel):
    opportunity_id: int
    budget_status: Literal["Empty", "Opportunity", "Budgeted"] = Field(
        ..., description="Empty | Opportunity | Budgeted"
    )
    is_additional: Optional[bool] = Field(
        None,
        description="Explicitly move this row in/out of the Additional bucket, "
        "independent of budget_status. None = leave unchanged.",
    )
    delta_reason: Optional[List[str]] = Field(
        None,
        description="Reasons explaining the EOY/budget delta (multi-value). "
        f"Allowed values: {', '.join(DELTA_REASON_VALUES)}",
    )

    _delta_reason_validator = field_validator("delta_reason")(_validate_delta_reasons)


class DeltaReasonDecision(BaseModel):
    opportunity_id: int
    delta_reason: Optional[List[str]] = None

    _delta_reason_validator = field_validator("delta_reason")(_validate_delta_reasons)


class DeltaReasonUpdateRequest(BaseModel):
    """Lightweight update of delta_reason only — does not touch budget_status or lock timestamps."""
    decisions: List[DeltaReasonDecision] = Field(default_factory=list)


class BudgetAssignRequest(BaseModel):
    """Create-Budget decisions for a fiscal year. Each entry sets one opportunity's
    per-year budget status to rien (Empty) / opportunité (Opportunity) / budgeted
    (Budgeted). Rows not listed are left unchanged."""
    decisions: List[BudgetDecision] = Field(default_factory=list)
    decided_by: Optional[str] = None


class BudgetYearResponse(BaseModel):
    id: int
    fiscal_year: int
    applicable_amount: Optional[Decimal] = None
    portion_kind: Optional[str] = None
    suggested_status: Optional[str] = None
    budget_status: Optional[str] = None
    is_additional: Optional[bool] = None
    status_locked_at: Optional[datetime] = None
    status_locked_by: Optional[str] = None
    delta_reason: Optional[List[str]] = None

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Opportunity response
# ---------------------------------------------------------------------------

class OpportunityResponse(BaseModel):
    opportunity_id: int
    opportunity_name: Optional[str] = None
    opportunity_type: Optional[str] = None
    saving_nature: Optional[str] = None
    entry_mode: Optional[str] = None
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
    validation_status: Optional[str] = None
    budget_year: Optional[Decimal] = None
    budget_confirmed_at: Optional[datetime] = None
    budget_confirmed_by: Optional[str] = None
    planned_end_date: Optional[date] = None
    phase_status: Optional[str] = None
    validation_decision: Optional[str] = None
    val_date: Optional[date] = None
    study_start_date: Optional[date] = None
    execution_start_date: Optional[date] = None
    change_mode: Optional[str] = None
    currency: Optional[str] = None
    fx_rate_to_eur: Optional[Decimal] = None
    assumptions_summary: Optional[str] = None
    payback_score: Optional[Decimal] = None
    lead_time_score: Optional[Decimal] = None
    difficulty_score: Optional[Decimal] = None
    priority_score: Optional[Decimal] = None
    priority_category: Optional[str] = None
    priority_locked: Optional[bool] = None
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
    place_of_incoterms_before: Optional[str] = None
    place_of_incoterms_after: Optional[str] = None
    top_days_before: Optional[int] = None
    top_days_after: Optional[int] = None
    transit_days_before: Optional[int] = None
    transit_days_after: Optional[int] = None
    # country_before not stored — read from SupplierUnit.country
    country_after: Optional[str] = None
    bonus_before: Optional[Decimal] = None
    bonus_after: Optional[Decimal] = None
    consignment_before: Optional[str] = None
    consignment_after: Optional[str] = None
    current_price_n1: Optional[Decimal] = None
    current_price_n2: Optional[Decimal] = None
    current_price_n3: Optional[Decimal] = None
    supplier_asked: Optional[bool] = None
    supplier_asked_result: Optional[str] = None
    tooling_cost: Optional[Decimal] = None
    travel_cost: Optional[Decimal] = None
    qualification_cost: Optional[Decimal] = None
    other_cost: Optional[Decimal] = None
    total_investment: Optional[Decimal] = None
    roi_percent: Optional[Decimal] = None
    roi_period_percent: Optional[Decimal] = None
    period_saving: Optional[Decimal] = None
    saving_year_n: Optional[Decimal] = None
    saving_year_n1: Optional[Decimal] = None
    saving_year_n2: Optional[Decimal] = None
    saving_year_n3: Optional[Decimal] = None
    saving_by_year: Optional[Dict[str, Decimal]] = None
    # Computed in opportunity_to_response (not ORM columns):
    #  - value_of_opportunity: total multi-year gain (= period_saving, the "value of opportunity")
    #  - saving_to_budget_by_year: incremental year-over-year price drop, per calendar year (what is budgeted)
    value_of_opportunity: Optional[Decimal] = None
    saving_to_budget_by_year: Optional[Dict[str, Decimal]] = None
    cash_inventory_gap: Optional[Decimal] = None
    cash_ap_gap: Optional[Decimal] = None
    secondary_plants: Optional[str] = None
    stp_risks: Optional[STPRisks] = None
    stp_benefits: Optional[STPBenefits] = None
    phase1_weeks: Optional[int] = None
    phase2_weeks: Optional[int] = None
    phase3_weeks: Optional[int] = None
    phase4_weeks: Optional[int] = None
    reason_productivity: Optional[bool] = None
    reason_quality: Optional[bool] = None
    reason_capacity: Optional[bool] = None
    reason_other: Optional[str] = None
    created_at: Optional[datetime] = None
    created_by: Optional[str] = None
    updated_at: Optional[datetime] = None
    # STP revision approval — non-null when a Director-approval request is pending
    pending_stp_revision: Optional[dict] = None
    # Structured, append-only audit trail of committed baseline corrections
    # (Revise Baseline, post-actuals) — see Opportunity.revision_history.
    revision_history: Optional[list] = None
    projects: List[ProjectResponse] = Field(default_factory=list)
    financial_lines: List[FinancialLineResponse] = Field(default_factory=list)
    budget_years: List[BudgetYearResponse] = Field(default_factory=list)
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
    # "Value of opportunity" = total multi-year gain (period_saving); "saving à budgéter"
    # = the incremental year-over-year drop that actually feeds the budget rows.
    data.value_of_opportunity = opp.period_saving
    stb = compute_saving_to_budget_by_year(opp)
    data.saving_to_budget_by_year = (
        {k: Decimal(str(v)) for k, v in stb.items()} if stb else None
    )
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
    supplier_name: Optional[str] = None
    group_name: Optional[str] = None   # SupplierGroup.nom
    city: Optional[str] = None
    country: Optional[str] = None

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Action Plan schemas (mirrors the external PlanV2 / SujetNodeV2 / ActionNodeV2)
# ---------------------------------------------------------------------------

class ActionNodeV2(BaseModel):
    titre: str = Field(..., min_length=1)
    description: Optional[str] = None
    responsable: Optional[str] = None
    email_responsable: Optional[str] = None
    kpi: Optional[str] = None
    demandeur: Optional[str] = None
    email_demandeur: Optional[str] = None
    status: Optional[str] = "open"
    priorite: Optional[int] = Field(None, ge=0)
    due_date: Optional[date] = None
    closed_date: Optional[date] = None
    ordre: Optional[int] = None
    importance: Optional[str] = None
    urgency: Optional[str] = None
    escalation_level: Optional[int] = Field(0, ge=0, le=3)
    priority_index: Optional[int] = Field(None, ge=0, le=100)
    estimated_duration_days: Optional[int] = Field(None, ge=0)
    attachments: List[dict] = Field(default_factory=list)
    sous_actions: List["ActionNodeV2"] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


ActionNodeV2.model_rebuild()


class SujetNodeV2(BaseModel):
    titre: str = Field(..., min_length=1)
    code: Optional[str] = None
    description: Optional[str] = None
    inserted_by: Optional[str] = None
    responsable: Optional[str] = None
    email_responsable: Optional[str] = None
    sous_sujets: List["SujetNodeV2"] = Field(default_factory=list)
    actions: List[ActionNodeV2] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


SujetNodeV2.model_rebuild()


class ActionPlanCreateRequest(BaseModel):
    plan_title: str = Field(..., min_length=1)
    phase_status: Optional[str] = None
    plan_code: Optional[str] = None
    responsable: Optional[str] = None
    email_responsable: Optional[str] = None
    demandeur: Optional[str] = None
    email_demandeur: Optional[str] = None
    sujets: List[SujetNodeV2] = Field(default_factory=list)


class ActionPlanUpdateRequest(BaseModel):
    plan_title: Optional[str] = None
    phase_status: Optional[str] = None
    responsable: Optional[str] = None
    email_responsable: Optional[str] = None
    demandeur: Optional[str] = None
    email_demandeur: Optional[str] = None
    sujets: Optional[List[SujetNodeV2]] = None


class ActionPlanResponse(BaseModel):
    action_plan_id: int
    opportunity_id: int
    phase_status: Optional[str] = None
    plan_title: Optional[str] = None
    plan_code: Optional[str] = None
    plan_data: Optional[dict] = None
    external_push_status: Optional[str] = None
    external_push_error: Optional[str] = None
    created_at: Optional[datetime] = None
    created_by: Optional[str] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
