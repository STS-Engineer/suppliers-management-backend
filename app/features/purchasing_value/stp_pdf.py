"""Generate a PDF of the STP (Sourcing & Technical Productivity) document.

Layout mirrors the Excel template "format STP rev 1.2":
  Phase 0 sheet  →  pass phase=0
  Phase 1 sheet  →  pass phase=1  (same data, different committee section)

Returns raw PDF bytes; callers write them to a temp file or stream them directly.
"""

from __future__ import annotations

import io
from datetime import date, datetime
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
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

# ---------------------------------------------------------------------------
# Colour palette (mirrors Excel light-blue/grey header style)
# ---------------------------------------------------------------------------
HDR_BG = colors.HexColor("#1F3864")   # dark navy — section titles
HDR_FG = colors.white
SUB_BG = colors.HexColor("#D6E4F0")   # light blue — row labels
ROW_ALT = colors.HexColor("#F7FBFF")  # very light blue — alternating rows
BORDER = colors.HexColor("#BDD7EE")

W = A4[0] - 2 * cm   # usable page width


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt(val, prefix="", suffix="", decimals=2) -> str:
    if val is None:
        return "—"
    try:
        f = float(val)
        return f"{prefix}{f:,.{decimals}f}{suffix}"
    except (TypeError, ValueError):
        return str(val)


def _yn(val: Optional[str]) -> str:
    return val if val else "—"


def _header_row(text: str, cols: int = 3) -> list:
    return [Paragraph(f"<b>{text}</b>", _styles()["hdr"]), *[""] * (cols - 1)]


def _styles():
    ss = getSampleStyleSheet()
    return {
        "normal": ParagraphStyle("N", parent=ss["Normal"], fontSize=7.5, leading=10),
        "small":  ParagraphStyle("S", parent=ss["Normal"], fontSize=6.5, leading=9),
        "bold":   ParagraphStyle("B", parent=ss["Normal"], fontSize=7.5, leading=10,
                                 fontName="Helvetica-Bold"),
        "hdr":    ParagraphStyle("H", parent=ss["Normal"], fontSize=8, leading=10,
                                 textColor=HDR_FG, fontName="Helvetica-Bold"),
        "title":  ParagraphStyle("T", parent=ss["Normal"], fontSize=13, leading=16,
                                 fontName="Helvetica-Bold"),
        "sub":    ParagraphStyle("Su", parent=ss["Normal"], fontSize=9, leading=12,
                                 fontName="Helvetica-Bold", textColor=colors.HexColor("#1F3864")),
    }


def _tbl_style(header_rows: int = 1, has_alt: bool = True, num_rows: int = 0) -> TableStyle:
    cmds = [
        # header rows
        ("BACKGROUND",  (0, 0), (-1, header_rows - 1), HDR_BG),
        ("TEXTCOLOR",   (0, 0), (-1, header_rows - 1), HDR_FG),
        ("FONTNAME",    (0, 0), (-1, header_rows - 1), "Helvetica-Bold"),
        ("FONTSIZE",    (0, 0), (-1, -1), 7.5),
        ("LEADING",     (0, 0), (-1, -1), 10),
        ("GRID",        (0, 0), (-1, -1), 0.3, BORDER),
        ("VALIGN",      (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",  (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    if has_alt and num_rows > header_rows:
        for i in range(header_rows, num_rows, 2):
            cmds.append(("ROWBACKGROUNDS", (0, i), (-1, i), [ROW_ALT]))
    return TableStyle(cmds)


def _label_col_style() -> TableStyle:
    """Make the first column light-blue label style."""
    return TableStyle([
        ("BACKGROUND", (0, 1), (0, -1), SUB_BG),
        ("FONTNAME",   (0, 1), (0, -1), "Helvetica-Bold"),
    ])


def _p(text: str, style="normal") -> Paragraph:
    s = _styles()[style]
    return Paragraph(str(text) if text else "—", s)


def _cell(text, bold=False) -> Paragraph:
    return _p(str(text) if text is not None else "—", "bold" if bold else "normal")


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _section_header(title: str, col_widths: list[float]) -> Table:
    t = Table([[Paragraph(f"<b>{title}</b>", _styles()["hdr"])]],
              colWidths=[sum(col_widths)])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), HDR_BG),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
    ]))
    return t


def _kv_table(rows: list[tuple[str, str]], col_widths=None) -> Table:
    """Two-column key→value table."""
    cw = col_widths or [5 * cm, W - 5 * cm]
    data = [[_cell(k, bold=True), _cell(v)] for k, v in rows]
    t = Table(data, colWidths=cw)
    t.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (0, -1), SUB_BG),
        ("GRID",        (0, 0), (-1, -1), 0.3, BORDER),
        ("FONTSIZE",    (0, 0), (-1, -1), 7.5),
        ("LEADING",     (0, 0), (-1, -1), 10),
        ("VALIGN",      (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",  (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        *[("ROWBACKGROUNDS", (0, i), (-1, i), [ROW_ALT]) for i in range(1, len(data), 2)],
    ]))
    return t


def _before_after_table(
    rows: list[tuple[str, str, str]],   # (label, before, after)
    col_widths=None,
) -> Table:
    """Three-column label | before | after table."""
    cw = col_widths or [5 * cm, (W - 5 * cm) / 2, (W - 5 * cm) / 2]
    header = [_cell("Field", bold=True), _cell("Before", bold=True), _cell("After", bold=True)]
    data = [header] + [[_cell(lbl, bold=True), _cell(b), _cell(a)] for lbl, b, a in rows]
    t = Table(data, colWidths=cw)
    t.setStyle(_tbl_style(header_rows=1, num_rows=len(data)))
    t.setStyle(_label_col_style())
    return t


# ---------------------------------------------------------------------------
# Page template (header + footer)
# ---------------------------------------------------------------------------

def _make_page_template(doc, opp_name: str, phase: int) -> PageTemplate:
    frame = Frame(doc.leftMargin, doc.bottomMargin,
                  doc.width, doc.height - 1.2 * cm, id="main")

    def on_page(canvas, doc):
        canvas.saveState()
        # Top header bar
        canvas.setFillColor(HDR_BG)
        canvas.rect(doc.leftMargin, A4[1] - 1.8 * cm,
                    doc.width, 1.2 * cm, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawString(doc.leftMargin + 0.3 * cm,
                          A4[1] - 1.1 * cm,
                          f"STP Phase {phase}  —  {opp_name[:70]}")
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(A4[0] - doc.rightMargin,
                               A4[1] - 1.1 * cm,
                               f"Page {doc.page}  |  {datetime.today().strftime('%d/%m/%Y')}")
        # Footer
        canvas.setFillColor(colors.HexColor("#888888"))
        canvas.setFont("Helvetica", 6.5)
        canvas.drawString(doc.leftMargin, 0.6 * cm, "Avocarbon — Confidential")
        canvas.restoreState()

    return PageTemplate(id="main", frames=[frame], onPage=on_page)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_stp_pdf(opp, phase: int = 0) -> bytes:
    """Build the STP PDF for the given opportunity and return raw bytes.

    ``opp`` is a SQLAlchemy Opportunity ORM object (all relations eagerly loaded).
    ``phase`` is 0 or 1 — controls the committee section header.
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
    doc.addPageTemplates([_make_page_template(doc, opp.opportunity_name or "", phase)])

    story = []
    def sp(n=0.2):
        return Spacer(1, n * cm)

    # ── Shorthand accessors ──────────────────────────────────────────────────
    risks: dict    = opp.stp_risks    or {}
    benefits: dict = opp.stp_benefits or {}

    # ── Title ────────────────────────────────────────────────────────────────
    story.append(Paragraph(
        f"STP — Sourcing and Technical Productivity &nbsp;&nbsp;|&nbsp;&nbsp; Phase {phase}",
        st["title"],
    ))
    story.append(sp(0.15))
    story.append(Paragraph(
        f"Rev 1.2 &nbsp;&nbsp;|&nbsp;&nbsp; Author: Olivier Grimaud &nbsp;&nbsp;"
        f"|&nbsp;&nbsp; Date: {datetime.today().strftime('%d/%m/%Y')}",
        st["small"],
    ))
    story.append(sp(0.4))

    # ── General ──────────────────────────────────────────────────────────────
    story.append(_section_header("General Information", [W]))
    story.append(_kv_table([
        ("Opportunity",  opp.opportunity_name or "—"),
        ("Type",         opp.opportunity_type or "—"),
        ("Main Avocarbon Plant", opp.plant.site_name if opp.plant else "—"),
        ("Secondary plants",     opp.secondary_plants or "—"),
        ("Description",  opp.description or "—"),
        ("Why",          "  ".join(filter(None, [
            "Productivity" if opp.reason_productivity else None,
            "Quality"      if opp.reason_quality      else None,
            "Capacity"     if opp.reason_capacity      else None,
            f"Other: {opp.reason_other}" if opp.reason_other else None,
        ])) or "—"),
        ("Change mode",  opp.change_mode or "—"),
    ]))
    story.append(sp())

    # ── Scope ────────────────────────────────────────────────────────────────
    story.append(_section_header("Scope & Customers", [W]))
    story.append(_kv_table([
        ("Scope IN",   opp.scope_in  or "—"),
        ("Scope OUT",  opp.scope_out or "—"),
        ("Customers",  opp.customers or "—"),
    ]))
    story.append(sp())

    # ── Annual quantities ────────────────────────────────────────────────────
    story.append(_section_header("Annual Quantities", [W]))
    cw4 = [W / 4] * 4
    qty_data = [
        [_cell("N1", bold=True), _cell("N2", bold=True),
         _cell("N3", bold=True), _cell("N4", bold=True)],
        [_cell(_fmt(opp.annual_quantity_n1, decimals=0)),
         _cell(_fmt(opp.annual_quantity_n2, decimals=0)),
         _cell(_fmt(opp.annual_quantity_n3, decimals=0)),
         _cell(_fmt(opp.annual_quantity_n4, decimals=0))],
    ]
    qty_tbl = Table(qty_data, colWidths=cw4)
    qty_tbl.setStyle(_tbl_style(header_rows=1, has_alt=False))
    story.append(qty_tbl)
    story.append(sp())

    # ── Initial step ─────────────────────────────────────────────────────────
    story.append(_section_header("Initial Step", [W]))
    asked = "Yes" if opp.supplier_asked else ("No" if opp.supplier_asked is not None else "—")
    story.append(_kv_table([
        ("Has the current supplier been formally given a chance to decrease the price?", asked),
        ("Result", opp.supplier_asked_result or "—"),
    ], col_widths=[10 * cm, W - 10 * cm]))
    story.append(sp())

    # ── Before / After supplier comparison ───────────────────────────────────
    story.append(_section_header("Supplier Comparison — Before / After", [W]))
    story.append(_before_after_table([
        ("Supplier",          opp.opportunity_name and "Current supplier" or "—",
                              opp.proposed_supplier_name or "—"),
        ("Country",           "From panel",          opp.country_after or "—"),
        ("Incoterms",         opp.incoterms_before or "—", opp.incoterms_after or "—"),
        ("Consignment",       _yn(opp.consignment_before), _yn(opp.consignment_after)),
        ("TOP (days)",        _fmt(opp.top_days_before, decimals=0),
                              _fmt(opp.top_days_after, decimals=0)),
        ("Transit time (days)", _fmt(opp.transit_days_before, decimals=0),
                                _fmt(opp.transit_days_after, decimals=0)),
        ("Price (€/unit)",    _fmt(opp.current_price, decimals=4),
                              _fmt(opp.proposed_price, decimals=4)),
        ("Price N+1",         _fmt(opp.current_price_n1, decimals=4),
                              _fmt(opp.proposed_price_n1, decimals=4)),
        ("Price N+2",         _fmt(opp.current_price_n2, decimals=4),
                              _fmt(opp.proposed_price_n2, decimals=4)),
        ("Price N+3",         _fmt(opp.current_price_n3, decimals=4),
                              _fmt(opp.proposed_price_n3, decimals=4)),
        ("Bonus / business link", _fmt(opp.bonus_before), _fmt(opp.bonus_after)),
    ]))
    story.append(sp())

    # ── Risks ────────────────────────────────────────────────────────────────
    story.append(_section_header("Risks", [W]))
    story.append(_before_after_table([
        ("Material indexation", risks.get("material_indexation_before") or "—",
                                risks.get("material_indexation_after") or "—"),
        ("Exchange rate",       risks.get("exchange_rate_before") or "—",
                                risks.get("exchange_rate_after") or "—"),
        ("Local content",       risks.get("local_content_before") or "—",
                                risks.get("local_content_after") or "—"),
        ("Quality",             risks.get("quality_before") or "—",
                                risks.get("quality_after") or "—"),
        ("Other",               risks.get("other_before") or "—",
                                risks.get("other_after") or "—"),
    ]))
    story.append(sp(0.2))
    # Spec questions
    spec_data = [
        [_cell("Specification question", bold=True), _cell("Answer", bold=True)],
        [_cell("Will material spec & appearance be strictly the same?"),
         _cell(_yn(risks.get("material_same_spec")))],
        [_cell("Is the same tooling used?"),
         _cell(_yn(risks.get("same_tooling")))],
        [_cell("Will dimensions and appearance be the same?"),
         _cell(_yn(risks.get("same_dimension")))],
    ]
    spec_tbl = Table(spec_data, colWidths=[W * 0.75, W * 0.25])
    spec_tbl.setStyle(_tbl_style(header_rows=1, num_rows=len(spec_data)))
    story.append(spec_tbl)
    story.append(sp())

    # ── Benefits ─────────────────────────────────────────────────────────────
    story.append(_section_header("Benefits", [W]))
    story.append(_kv_table([
        ("If we do",    benefits.get("if_we_do") or "—"),
        ("If we don't", benefits.get("if_not")   or "—"),
    ]))
    story.append(sp())

    # ── Investment costs ─────────────────────────────────────────────────────
    story.append(_section_header("Investment Costs", [W]))
    cost_data = [
        [_cell("Item", bold=True), _cell("Amount (€)", bold=True)],
        [_cell("Tooling"),       _cell(_fmt(opp.tooling_cost))],
        [_cell("Travel"),        _cell(_fmt(opp.travel_cost))],
        [_cell("Qualification"), _cell(_fmt(opp.qualification_cost))],
        [_cell("Other"),         _cell(_fmt(opp.other_cost))],
        [_cell("Total", bold=True), _cell(_fmt(opp.total_investment), )],
    ]
    cost_tbl = Table(cost_data, colWidths=[W * 0.6, W * 0.4])
    cost_tbl.setStyle(_tbl_style(header_rows=1, num_rows=len(cost_data)))
    # Bold total row
    cost_tbl.setStyle(TableStyle([
        ("FONTNAME",    (0, 5), (-1, 5), "Helvetica-Bold"),
        ("BACKGROUND",  (0, 5), (-1, 5), SUB_BG),
    ]))
    story.append(cost_tbl)
    story.append(sp())

    # ── EBITDA savings — Excel D51/D52 with ROI F51/F52 ─────────────────────
    story.append(_section_header("EBITDA Savings", [W]))
    roi_full_str = _fmt(opp.roi_percent, suffix="%") if opp.roi_percent is not None else "—"
    roi_period_str = _fmt(opp.roi_period_percent, suffix="%") if opp.roi_period_percent is not None else "—"
    saving_data = [
        [_cell("Metric", bold=True), _cell("Value", bold=True), _cell("ROI", bold=True)],
        [_cell("Full year (1st)"),
         _cell(_fmt(opp.expected_annual_saving, prefix="€")),
         _cell(roi_full_str)],
        [_cell("Period (N1–N4)"),
         _cell(_fmt(opp.period_saving, prefix="€")),
         _cell(roi_period_str)],
        [_cell("Duration"),
         _cell(f"{opp.duration_months or '—'} months"), _cell("")],
    ]
    saving_tbl = Table(saving_data, colWidths=[W * 0.45, W * 0.35, W * 0.20])
    saving_tbl.setStyle(_tbl_style(header_rows=1, num_rows=len(saving_data)))
    story.append(saving_tbl)
    story.append(sp(0.2))

    # Estimated saving by budget year (01 Dec -> 30 Nov, start-date prorated)
    by_year = opp.saving_by_year or {}
    if by_year:
        year_rows = [[_cell("Budget Year", bold=True), _cell("Est. Saving", bold=True)]]
        for yr in sorted(by_year.keys()):
            year_rows.append([_cell(str(yr)), _cell(_fmt(by_year[yr], prefix="€"))])
        year_tbl = Table(year_rows, colWidths=[W * 0.45, W * 0.55])
        year_tbl.setStyle(_tbl_style(header_rows=1, num_rows=len(year_rows)))
        story.append(year_tbl)
        story.append(sp(0.2))

    # ── Cash savings ─────────────────────────────────────────────────────────
    story.append(_section_header("Cash Savings", [W]))
    cash_data = [
        [_cell("Metric", bold=True), _cell("Value (€)", bold=True)],
        [_cell("Est. Inventory gap"), _cell(_fmt(opp.cash_inventory_gap))],
        [_cell("Est. AP gap"),        _cell(_fmt(opp.cash_ap_gap))],
    ]
    cash_tbl = Table(cash_data, colWidths=[W * 0.6, W * 0.4])
    cash_tbl.setStyle(_tbl_style(header_rows=1, num_rows=len(cash_data)))
    story.append(cash_tbl)
    story.append(sp())

    # ── Planning ─────────────────────────────────────────────────────────────
    story.append(_section_header("Estimated Planning", [W]))

    # Compute phase start/end dates from study_start_date + cumulative weeks
    phase_starts: list[Optional[date]] = [None, None, None, None]
    phase_ends:   list[Optional[date]] = [None, None, None, None]
    weeks = [opp.phase1_weeks, opp.phase2_weeks, opp.phase3_weeks, opp.phase4_weeks]
    cursor = opp.study_start_date or opp.planned_start_date
    for i, w in enumerate(weeks):
        phase_starts[i] = cursor
        if cursor and w:
            import datetime as _dt
            end = cursor + _dt.timedelta(weeks=w)
            phase_ends[i] = end
            cursor = end
        else:
            cursor = None

    def _fdate(d) -> str:
        if d is None:
            return "—"
        try:
            return d.strftime("%d/%m/%Y") if hasattr(d, "strftime") else str(d)
        except Exception:
            return str(d)

    plan_data = [
        [_cell("Phase", bold=True), _cell("Weeks", bold=True),
         _cell("Start", bold=True), _cell("End", bold=True)],
        *[
            [_cell(f"Phase {i+1}"),
             _cell(str(weeks[i]) if weeks[i] else "—"),
             _cell(_fdate(phase_starts[i])),
             _cell(_fdate(phase_ends[i]))]
            for i in range(4)
        ],
        [_cell("Savings start", bold=True),
         _cell(""),
         _cell(_fdate(opp.planned_start_date)),
         _cell("")],
    ]
    plan_tbl = Table(plan_data, colWidths=[W * 0.3, W * 0.15, W * 0.275, W * 0.275])
    plan_tbl.setStyle(_tbl_style(header_rows=1, num_rows=len(plan_data)))
    story.append(plan_tbl)
    story.append(sp())

    # ── Gate / committee section ─────────────────────────────────────────────
    gate_title = "Phase 0 Gate — Committee Decision" if phase == 0 else "Phase 1 Gate — Committee Review"
    story.append(_section_header(gate_title, [W]))
    participants = "CEO · Purchasing · COO/Plant Manager · Sales" if phase == 1 \
        else "Plant Manager · Purchasing"

    gate_rows = [
        ("Type of change",  opp.change_mode or "—"),
        ("Participants",    participants),
    ]
    if phase == 1 and opp.projects:
        proj = opp.projects[0]
        gate_rows += [
            ("Committee review date",  _fdate(proj.committee_review_date)),
            ("Committee members",      proj.committee_members or "—"),
            ("PM",                     opp.project_owner or "—"),
        ]
    else:
        gate_rows += [
            ("Selected PM for Phase 1", opp.project_owner or "—"),
        ]
    story.append(_kv_table(gate_rows))

    # ── Build PDF ────────────────────────────────────────────────────────────
    doc.build(story)
    return buf.getvalue()
