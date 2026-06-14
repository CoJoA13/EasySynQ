"""Pure reportlab (Platypus) render of a Management Review's filed minutes -> a table-styled PDF.

S-mr-pack. NO DB / NO IO / NO ProblemException - the caller
(services/mgmt_review/pack.build_minutes_pdf) gathers the frozen minutes snapshot + resolved user
names + the version's sign-off signatures and passes plain data in. Deterministic via an invariant
canvasmaker (the PDF /ID + metadata are fixed) so a given released MR renders byte-identical - no
Date.now(), only stored values. Defensive on the free-form source_ref / attendee shapes (never
raises on an unexpected JSON shape)."""

from __future__ import annotations

import io
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

_MARGIN = 54.0  # aligns with the evidence-pack portfolio
_GRID = colors.HexColor("#d0d5dd")
_HEADER_BG = colors.HexColor("#f2f4f7")


def _invariant_canvas(*args: Any, **kw: Any) -> Canvas:
    """Force a deterministic PDF (/ID + metadata). Setting the key (rather than passing as a
    partial) avoids a duplicate-kwarg TypeError if reportlab forwards its own ``invariant``."""
    kw["invariant"] = 1
    return Canvas(*args, **kw)


def _summary(source_ref: Any) -> str:
    """A generic, never-raising one-cell summary of the free-form 9.3.2 source_ref."""
    if isinstance(source_ref, dict):
        return "; ".join(f"{k}: {source_ref[k]}" for k in sorted(source_ref)) or "—"
    if source_ref in (None, "", [], {}):
        return "—"
    return str(source_ref)


def _attendee_name(a: dict[str, Any], name_of: dict[str, str]) -> str:
    nm = a.get("name")
    if nm:
        return str(nm)
    uid = a.get("user_id")
    return name_of.get(str(uid), "—") if uid else "—"


def render_minutes_pdf(
    *,
    identifier: str,
    title: str,
    current_state: str,
    close_state: str | None,
    revision_label: str,
    effective_from: str | None,
    version_id: str,
    source_digest: str,
    minutes: dict[str, Any],
    name_of: dict[str, str],
    signatures: list[dict[str, Any]],
) -> bytes:
    styles = getSampleStyleSheet()
    body = ParagraphStyle("mr_body", parent=styles["BodyText"], fontSize=8.5, leading=11)
    h2 = ParagraphStyle(
        "mr_h2", parent=styles["Heading2"], fontSize=12, spaceBefore=12, spaceAfter=4
    )
    title_style = ParagraphStyle("mr_title", parent=styles["Title"], fontSize=15, spaceAfter=2)

    def p(text: str) -> Paragraph:
        return Paragraph(escape(str(text)), body)

    def grid(rows: list[list[Any]], widths_in: list[float]) -> Table:
        t = Table(rows, colWidths=[w * inch for w in widths_in], hAlign="LEFT")
        t.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.5, _GRID),
                    ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, 0), 8.5),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        return t

    def none_row() -> Table:
        return grid([[p("— none recorded —")]], [6.4])

    flow: list[Any] = [
        Paragraph("Management Review — minutes (controlled record)", title_style),
        Spacer(1, 6),
    ]

    flow.append(
        grid(
            [
                ["Identifier", p(identifier)],
                ["Title", p(title)],
                ["Period", p(minutes.get("period_label") or "—")],
                ["Review date", p(minutes.get("review_date") or "—")],
                ["State", p(current_state + (f" · {close_state}" if close_state else ""))],
                [
                    "Revision",
                    p(
                        revision_label
                        + (f" · effective {effective_from}" if effective_from else "")
                    ),
                ],
            ],
            [1.4, 5.0],
        )
    )

    flow.append(Paragraph("Attendees", h2))
    attendees = minutes.get("attendees") or []
    if attendees:
        rows = [["Name", "Role"]] + [
            [p(_attendee_name(a, name_of)), p(a.get("role") or "—")] for a in attendees
        ]
        flow.append(grid(rows, [3.2, 3.2]))
    else:
        flow.append(none_row())

    flow.append(Paragraph("Review inputs (9.3.2)", h2))
    inputs = sorted(minutes.get("inputs") or [], key=lambda r: r.get("position", 0))
    if inputs:
        rows = [["Input", "Available", "Summary"]] + [
            [
                p(ri.get("input_type") or "—"),
                p("Yes" if ri.get("available") else "No"),
                p(_summary(ri.get("source_ref"))),
            ]
            for ri in inputs
        ]
        flow.append(grid(rows, [1.7, 0.9, 3.8]))
    else:
        flow.append(none_row())

    flow.append(Paragraph("Review outputs / decisions (9.3.3)", h2))
    outputs = minutes.get("outputs") or []
    if outputs:
        rows = [["Type", "Decision / action", "Owner", "Due"]] + [
            [
                p(ro.get("output_type") or "—"),
                p(ro.get("description") or "—"),
                p(
                    name_of.get(str(ro.get("owner_user_id")), "—")
                    if ro.get("owner_user_id")
                    else "—"
                ),
                p(ro.get("due_date") or "—"),
            ]
            for ro in outputs
        ]
        flow.append(grid(rows, [1.3, 3.1, 1.4, 0.6]))
    else:
        flow.append(none_row())

    flow.append(Paragraph("Sign-off", h2))
    if signatures:
        rows = [["Signer", "Meaning", "When (UTC)", "Method"]] + [
            [
                p(s.get("signer") or "system"),
                p(s.get("meaning") or "—"),
                p(s.get("when") or "—"),
                p(s.get("method") or "—"),
            ]
            for s in signatures
        ]
        flow.append(grid(rows, [2.1, 1.3, 2.2, 0.8]))
    else:
        flow.append(none_row())

    def _footer(canvas: Canvas, _doc: Any) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#667085"))
        canvas.drawString(
            _MARGIN,
            36,
            f"Derived printable view of the filed minutes — canonical record: Management Review "
            f"version {version_id} (Rev {revision_label}).",
        )
        canvas.drawString(
            _MARGIN,
            26,
            f"Minutes source digest: {source_digest} — re-hash the application/json source blob "
            f"(RFC 8785 JCS) to verify.",
        )
        canvas.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=_MARGIN,
        rightMargin=_MARGIN,
        topMargin=_MARGIN,
        bottomMargin=_MARGIN,
        title=f"{identifier} — minutes",
    )
    doc.build(flow, onFirstPage=_footer, onLaterPages=_footer, canvasmaker=_invariant_canvas)
    return buf.getvalue()
