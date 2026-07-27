"""Single source of truth for auto-created supplier development (improvement) plans.

Implements procedure **C2Pr3 §8 "Consequences of evaluations"**: the *operational
evaluation letter* (A/B/C/D) drives the mandatory improvement plan, NOT the
Red/Orange/Green status.

    C -> supplier must submit an improvement plan immediately and exit grade C
         within 6 months.
    D -> supplier must submit an improvement plan, meet grade B within 6 months
         and exit grade D within 3 months.

Grades A and B only *may* propose a plan, so they never trigger auto-creation.

Both auto-creation paths (batch evaluation upload and relation re-evaluation)
import from this module so they stay aligned: same trigger, same due dates,
same de-duplication rule and the same buyer alert.
"""

from __future__ import annotations

import calendar
from datetime import date

# Operational grades that make an improvement plan mandatory (C2Pr3 §8).
MANDATORY_PLAN_GRADES = frozenset({"C", "D"})

# A plan in one of these statuses is finished; anything else counts as still
# "in flight" and blocks the creation of a duplicate plan for the same relation.
TERMINAL_PLAN_STATUSES = frozenset({"approved", "closed", "cancelled", "rejected"})


def _normalize_grade(operational_grade: str | None) -> str:
    return (operational_grade or "").strip().upper()


def requires_development_plan(operational_grade: str | None) -> bool:
    """True when the operational grade makes an improvement plan mandatory."""
    return _normalize_grade(operational_grade) in MANDATORY_PLAN_GRADES


def is_active_plan_status(status: str | None) -> bool:
    """True when a plan in this status still blocks creating another one."""
    return (status or "").strip().lower() not in TERMINAL_PLAN_STATUSES


def add_months(start: date, months: int) -> date:
    """start + months calendar months, clamped to the target month's last day."""
    target = start.month - 1 + months
    year = start.year + target // 12
    month = target % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def plan_exit_months(operational_grade: str | None) -> int:
    """Months allowed to exit the grade: D -> 3, C -> 6 (C2Pr3 §8)."""
    return 3 if _normalize_grade(operational_grade) == "D" else 6


def plan_due_date(operational_grade: str | None, evaluation_date: date) -> date:
    """Due date for the improvement plan, in calendar months from evaluation."""
    return add_months(evaluation_date, plan_exit_months(operational_grade))


def plan_title(operational_grade: str | None) -> str:
    if _normalize_grade(operational_grade) == "D":
        return "Improvement Plan — Grade D (exit within 3 months)"
    return "Improvement Plan — Grade C (exit within 6 months)"


def plan_internal_note(operational_grade: str | None) -> str:
    if _normalize_grade(operational_grade) == "D":
        return (
            "Supplier must submit an improvement plan targeting Grade B "
            "within 6 months and exit Grade D within 3 months."
        )
    return (
        "Supplier must submit an improvement plan immediately "
        "to exit Grade C within 6 months."
    )


def _grade_label(operational_grade: str | None) -> str:
    return (
        "D (Exit within 3 months)"
        if _normalize_grade(operational_grade) == "D"
        else "C (Exit within 6 months)"
    )


def grade_alert_email(
    *,
    operational_grade: str | None,
    supplier_display: str,
    site_display: str,
    evaluation_date: date,
    plan_due: date,
) -> tuple[str, str]:
    """Build the (subject, body_html) buyer alert for a mandatory-plan grade.

    Shared verbatim by the batch and re-evaluation paths so both notifications
    look identical.
    """
    grade = _normalize_grade(operational_grade)
    grade_label = _grade_label(operational_grade)
    subject = (
        f"[Action Required] Grade {grade} Supplier — "
        f"{supplier_display} · {site_display}"
    )
    body_html = f"""
<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto">
  <div style="background:#7f1d1d;padding:20px 28px;border-radius:8px 8px 0 0">
    <h1 style="color:#fff;margin:0;font-size:18px">Supplier Risk Alert — Grade {grade}</h1>
    <p style="color:#fca5a5;margin:4px 0 0;font-size:13px">Avocarbon · Supplier Management</p>
  </div>
  <div style="background:#f8fafc;padding:24px 28px;border:1px solid #e2e8f0;border-top:none">
    <p style="margin:0 0 16px;font-size:14px;color:#1e293b">
      A development plan has been automatically created for a Grade {grade} supplier
      requiring your immediate attention.
    </p>
    <table style="width:100%;border-collapse:collapse;margin-bottom:20px;font-size:13px">
      <tr style="background:#fee2e2"><td style="padding:8px 12px;font-weight:bold;width:40%">Supplier</td><td style="padding:8px 12px">{supplier_display}</td></tr>
      <tr><td style="padding:8px 12px;font-weight:bold">Plant</td><td style="padding:8px 12px">{site_display}</td></tr>
      <tr style="background:#fee2e2"><td style="padding:8px 12px;font-weight:bold">Grade</td><td style="padding:8px 12px;color:#991b1b;font-weight:bold">{grade_label}</td></tr>
      <tr><td style="padding:8px 12px;font-weight:bold">Evaluation date</td><td style="padding:8px 12px">{evaluation_date.isoformat()}</td></tr>
      <tr style="background:#fee2e2"><td style="padding:8px 12px;font-weight:bold">Plan due date</td><td style="padding:8px 12px;color:#991b1b;font-weight:bold">{plan_due.isoformat()}</td></tr>
    </table>
    <p style="margin:0;font-size:13px;color:#475569">
      Please send the development plan request to the supplier and monitor their response.
      Review the supplier workspace in Avocarbon Supplier Management for full details.
    </p>
  </div>
  <div style="background:#f1f5f9;padding:12px 28px;border-radius:0 0 8px 8px;border:1px solid #e2e8f0;border-top:none">
    <p style="color:#94a3b8;font-size:11px;margin:0">Avocarbon Supplier Management Platform — automated alert</p>
  </div>
</div>"""
    return subject, body_html
