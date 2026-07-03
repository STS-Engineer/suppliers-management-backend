"""Generate a "Full Opportunity Report" PDF — a live snapshot of an opportunity
across every phase (status, STP inputs, gate approval history, financial line
actuals), independent of opportunity type. Complements the STP dossier
(`stp_pdf.py`), which is a Phase 0/1 proposal document for Sourcing/Technical
Productivity types only — this report is available for any type, at any phase.

Reuses stp_pdf's palette, styles and table helpers rather than duplicating them.
"""

from __future__ import annotations

import io
from datetime import date, datetime
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from app.features.purchasing_value.stp_pdf import (
    HDR_BG,
    SUB_BG,
    W,
    _accent_cell,
    _cell,
    _fmt,
    _hdr_cell,
    _kv_table,
    _label_cell,
    _logo_flowable,
    _section_header,
    _styles,
    _tbl_style,
)


def _fdate(d) -> str:
    if d is None:
        return "—"
    try:
        return d.strftime("%d/%m/%Y") if hasattr(d, "strftime") else str(d)
    except Exception:
        return str(d)


def _fdatetime(d) -> str:
    if d is None:
        return "—"
    try:
        return d.strftime("%d/%m/%Y %H:%M") if hasattr(d, "strftime") else str(d)
    except Exception:
        return str(d)


def _make_page_template(doc, opp_name: str) -> PageTemplate:
    frame = Frame(doc.leftMargin, doc.bottomMargin,
                  doc.width, doc.height - 1.2 * cm, id="main")

    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(HDR_BG)
        canvas.rect(doc.leftMargin, A4[1] - 1.8 * cm,
                    doc.width, 1.2 * cm, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawString(doc.leftMargin + 0.3 * cm,
                          A4[1] - 1.1 * cm,
                          f"Full Opportunity Report  —  {opp_name[:65]}")
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(A4[0] - doc.rightMargin,
                               A4[1] - 1.1 * cm,
                               f"Page {doc.page}  |  {datetime.today().strftime('%d/%m/%Y')}")
        canvas.setFillColor(colors.HexColor("#888888"))
        canvas.setFont("Helvetica", 6.5)
        canvas.drawString(doc.leftMargin, 0.6 * cm, "Avocarbon — Confidential")
        canvas.restoreState()

    return PageTemplate(id="main", frames=[frame], onPage=on_page)


_DECISION_COLOR = {
    "Approved": colors.HexColor("#15803d"),
    "Go": colors.HexColor("#15803d"),
    "Rejected": colors.HexColor("#b91c1c"),
    "No Go": colors.HexColor("#b91c1c"),
    "Needs Review": colors.HexColor("#b45309"),
    "Review": colors.HexColor("#b45309"),
}


def _decision_cell(text: Optional[str]) -> Paragraph:
    color = _DECISION_COLOR.get(text or "", colors.HexColor("#475569"))
    style = _styles()["bold"]
    return Paragraph(text or "Pending…", ParagraphStyleCache.get(color, style))


class ParagraphStyleCache:
    """Tiny cache so repeated (color, base-style) combos share one ParagraphStyle."""
    _cache: dict = {}

    @classmethod
    def get(cls, color, base_style):
        key = (str(color), base_style.name)
        if key not in cls._cache:
            from reportlab.lib.styles import ParagraphStyle
            cls._cache[key] = ParagraphStyle(
                f"{base_style.name}_{len(cls._cache)}",
                parent=base_style, textColor=color,
            )
        return cls._cache[key]


def generate_full_report_pdf(opp) -> bytes:
    """Build the Full Opportunity Report PDF and return raw bytes.

    ``opp`` is a SQLAlchemy Opportunity ORM object with projects, financial_lines,
    plant and gate_approval_requests (with votes) eagerly/lazily loadable.
    """
    buf = io.BytesIO()
    st = _styles()

    doc = BaseDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=1 * cm,
        rightMargin=1 * cm,
        topMargin=2 * cm,
        bottomMargin=1.5 * cm,
    )
    doc.addPageTemplates([_make_page_template(doc, opp.opportunity_name or "")])

    story = []

    def sp(n=0.2):
        return Spacer(1, n * cm)

    risks: dict = opp.stp_risks or {}
    benefits: dict = opp.stp_benefits or {}

    # ── Logo + Title ─────────────────────────────────────────────────────
    logo = _logo_flowable()
    if logo is not None:
        story.append(logo)
        story.append(sp(0.2))
    story.append(Paragraph("Full Opportunity Report", st["title"]))
    story.append(sp(0.15))
    story.append(Paragraph(
        f"Generated {datetime.today().strftime('%d/%m/%Y')} &nbsp;&nbsp;|&nbsp;&nbsp; "
        "Live snapshot across all phases",
        st["small"],
    ))
    story.append(sp(0.4))

    # ── Status overview ─────────────────────────────────────────────────
    story.append(_section_header("Status Overview", [W]))
    story.append(_kv_table([
        ("Opportunity",     opp.opportunity_name or "—"),
        ("Type",            opp.opportunity_type or "—"),
        ("Status",          opp.status or "—"),
        ("Current Phase",   opp.phase_status or "—"),
        ("Committee Level", opp.committee_level or "—"),
        ("Main Plant",      opp.plant.site_name if opp.plant else "—"),
        ("Idea Owner",      opp.idea_owner or "—"),
        ("Purchasing Owner", opp.purchasing_owner or "—"),
        ("Project Owner (PM)", opp.project_owner or "—"),
        ("Validation date", _fdate(opp.val_date)),
    ]))
    story.append(sp())

    # ── General ──────────────────────────────────────────────────────────
    story.append(_section_header("General Information", [W]))
    story.append(_kv_table([
        ("Description",  opp.description or "—"),
        ("Why",          "  ".join(filter(None, [
            "Productivity" if opp.reason_productivity else None,
            "Quality"      if opp.reason_quality      else None,
            "Capacity"     if opp.reason_capacity      else None,
            f"Other: {opp.reason_other}" if opp.reason_other else None,
        ])) or "—"),
        ("Change mode",  opp.change_mode or "—"),
        ("Scope IN",     opp.scope_in or "—"),
        ("Scope OUT",    opp.scope_out or "—"),
        ("Customers",    opp.customers or "—"),
    ]))
    story.append(sp())

    # ── Supplier before/after ───────────────────────────────────────────
    has_supplier = bool(opp.proposed_supplier_name or opp.current_price or opp.proposed_price)
    if has_supplier:
        story.append(_section_header("Supplier Comparison — Before / After", [W]))
        cw = [5 * cm, (W - 5 * cm) / 2, (W - 5 * cm) / 2]
        rows = [
            ("Proposed Supplier", "—", opp.proposed_supplier_name or "—"),
            ("Country",           "—", opp.country_after or "—"),
            ("Incoterms",         opp.incoterms_before or "—", opp.incoterms_after or "—"),
            ("Price (€/unit)",    _fmt(opp.current_price, decimals=4), _fmt(opp.proposed_price, decimals=4)),
        ]
        header = [_hdr_cell("Field"), _hdr_cell("Before"), _hdr_cell("After")]
        data = [header] + [[_label_cell(lbl), _cell(b), _cell(a)] for lbl, b, a in rows]
        t = Table(data, colWidths=cw)
        t.setStyle(_tbl_style(header_rows=1, num_rows=len(data)))
        t.setStyle(TableStyle([("BACKGROUND", (0, 1), (0, -1), SUB_BG)]))
        story.append(t)
        story.append(sp())

    # ── Risks & Benefits ─────────────────────────────────────────────────
    has_risks = any(risks.values())
    has_benefits = bool(benefits.get("if_we_do") or benefits.get("if_not"))
    if has_risks or has_benefits:
        story.append(_section_header("Risks & Benefits", [W]))
        kv_rows = []
        if risks.get("material_indexation_before") or risks.get("material_indexation_after"):
            kv_rows.append(("Material indexation",
                             f"{risks.get('material_indexation_before') or '—'} → {risks.get('material_indexation_after') or '—'}"))
        if risks.get("exchange_rate_before") or risks.get("exchange_rate_after"):
            kv_rows.append(("Exchange rate",
                             f"{risks.get('exchange_rate_before') or '—'} → {risks.get('exchange_rate_after') or '—'}"))
        if benefits.get("if_we_do"):
            kv_rows.append(("If we do", benefits["if_we_do"]))
        if benefits.get("if_not"):
            kv_rows.append(("If we don't", benefits["if_not"]))
        if kv_rows:
            story.append(_kv_table(kv_rows))
            story.append(sp())

    # ── Investment & Savings ─────────────────────────────────────────────
    story.append(_section_header("Investment & Savings", [W]))
    roi_str = _fmt(opp.roi_percent, suffix="%") if opp.roi_percent is not None else "—"
    save_data = [
        [_hdr_cell("Metric"), _hdr_cell("Value")],
        [_cell("Total Investment"), _cell(_fmt(opp.total_investment, prefix="€"))],
        [_cell("Expected Annual Saving"), _accent_cell(_fmt(opp.expected_annual_saving, prefix="€"))],
        [_cell("Period Saving"), _accent_cell(_fmt(opp.period_saving, prefix="€"))],
        [_cell("ROI"), _accent_cell(roi_str)],
        [_cell("Cash — Inventory gap"), _cell(_fmt(opp.cash_inventory_gap, prefix="€"))],
        [_cell("Cash — AP gap"), _cell(_fmt(opp.cash_ap_gap, prefix="€"))],
    ]
    save_tbl = Table(save_data, colWidths=[W * 0.6, W * 0.4])
    save_tbl.setStyle(_tbl_style(header_rows=1, num_rows=len(save_data)))
    story.append(save_tbl)
    story.append(sp(0.2))

    by_year = opp.saving_by_year or {}
    if by_year:
        year_rows = [[_hdr_cell("Budget Year"), _hdr_cell("Est. Saving")]]
        for yr in sorted(by_year.keys()):
            year_rows.append([_cell(str(yr)), _cell(_fmt(by_year[yr], prefix="€"))])
        year_tbl = Table(year_rows, colWidths=[W * 0.45, W * 0.55])
        year_tbl.setStyle(_tbl_style(header_rows=1, num_rows=len(year_rows)))
        story.append(year_tbl)
        story.append(sp())

    # ── Planning ─────────────────────────────────────────────────────────
    story.append(_section_header("Planning", [W]))
    phase_starts: list[Optional[date]] = [None, None, None, None]
    phase_ends: list[Optional[date]] = [None, None, None, None]
    weeks = [opp.phase1_weeks, opp.phase2_weeks, opp.phase3_weeks, opp.phase4_weeks]
    cursor = opp.study_start_date or opp.planned_start_date
    for i, w in enumerate(weeks):
        phase_starts[i] = cursor
        if cursor and w:
            import datetime as _dt
            end = cursor + _dt.timedelta(weeks=float(w))
            phase_ends[i] = end
            cursor = end
        else:
            cursor = None
    plan_data = [
        [_hdr_cell("Phase"), _hdr_cell("Weeks"), _hdr_cell("Start"), _hdr_cell("End")],
        *[
            [_cell(f"Phase {i+1}"), _cell(str(weeks[i]) if weeks[i] else "—"),
             _cell(_fdate(phase_starts[i])), _cell(_fdate(phase_ends[i]))]
            for i in range(4)
        ],
        [_label_cell("Real start"), _cell(""), _cell(_fdate(opp.real_start_date)), _cell("")],
        [_label_cell("Planned end"), _cell(""), _cell(""), _cell(_fdate(opp.planned_end_date))],
    ]
    plan_tbl = Table(plan_data, colWidths=[W * 0.3, W * 0.15, W * 0.275, W * 0.275])
    plan_tbl.setStyle(_tbl_style(header_rows=1, num_rows=len(plan_data)))
    story.append(plan_tbl)
    story.append(sp())

    # ── Gate approval history ────────────────────────────────────────────
    requests = list(opp.gate_approval_requests or [])
    if requests:
        story.append(_section_header("Gate Approval History", [W]))
        for req in requests:
            req_label = (
                f"{req.phase_from or '—'} → next  ·  requested by {req.requested_by or '—'}"
                f"  ·  {_fdatetime(req.requested_at)}"
                + (f"  ·  {req.committee_level} Committee" if req.committee_level else "")
            )
            story.append(Paragraph(f"<b>{req_label}</b>", st["sub"]))
            vote_rows = [[_hdr_cell("Approver"), _hdr_cell("Role"), _hdr_cell("Decision"), _hdr_cell("Decided")]]
            for v in (req.votes or []):
                role = v.approver_role or ("Plant Manager" if v.is_plant_manager else "—")
                vote_rows.append([
                    _cell(v.approver_email or "—"),
                    _cell(role),
                    _decision_cell(v.decision),
                    _cell(_fdatetime(v.decided_at)),
                ])
            vote_tbl = Table(vote_rows, colWidths=[W * 0.35, W * 0.25, W * 0.2, W * 0.2])
            vote_tbl.setStyle(_tbl_style(header_rows=1, num_rows=len(vote_rows)))
            story.append(vote_tbl)
            outcome = req.consensus_result or req.status or "Pending"
            story.append(Paragraph(f"Outcome: <b>{outcome}</b>", st["small"]))
            story.append(sp(0.25))

    # ── Financial line actuals ───────────────────────────────────────────
    lines = list(opp.financial_lines or [])
    if lines:
        story.append(_section_header("Financial Line Actuals", [W]))
        fin_rows = [[
            _hdr_cell("Line"), _hdr_cell("Status"), _hdr_cell("Budget Status"),
            _hdr_cell("Real Start"), _hdr_cell("Cumul. Real Saving"),
        ]]
        for ln in lines:
            fin_rows.append([
                _cell(ln.line_name or ln.component_name or f"Line {ln.financial_line_id}"),
                _cell(ln.status or "—"),
                _cell(ln.budget_status or "—"),
                _cell(_fdate(ln.real_start_date)),
                _cell(_fmt(ln.cumulated_real_saving, prefix="€")),
            ])
        fin_tbl = Table(fin_rows, colWidths=[W * 0.3, W * 0.175, W * 0.175, W * 0.175, W * 0.175])
        fin_tbl.setStyle(_tbl_style(header_rows=1, num_rows=len(fin_rows)))
        story.append(fin_tbl)
        story.append(sp())

    doc.build(story)
    return buf.getvalue()
