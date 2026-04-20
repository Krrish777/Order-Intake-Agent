"""Generate Redline Equipment's urgent 1-line PDF PO — Phase 3.3 minimal/ambiguous.

Redline Equipment Repair runs heavy hydraulic rebuilds for oilfield service rigs
out of Houston. When a rig is down, their buyer Javier Quinonez fires off a
one-line emergency PO without the usual formalities — no PO number (the
filename is the reference), "Deliver ASAP — rig down" in place of a structured
delivery date, and the failed component identified by rig serial number rather
than a standard reorder pattern.

This file is the minimum-data edge case for Phase 3. It forces the extractor to
recognize a document that is *unambiguously a purchase order* despite being
missing most of the fields a PO normally carries.

Realism anchors:

1.  **No PO number field at all.** Not blank — absent. The heading reads
    "URGENT REORDER" instead of "PURCHASE ORDER". Agent must derive a
    reference from the filename or flag for human review.
2.  **Delivery = `ASAP — RIG DOWN`** as a literal large-font callout, not a
    date field. Conflicts with the ordered item's lead time (QD coupler is
    4-day stock).
3.  **Rig serial reference in notes.** `Rig RED-07  |  Pump Assy SN 4X-H42-11798`
    — this is *customer context*, not a line item. A naive extractor might
    try to parse it as a second SKU.
4.  **Single canonical SKU** (Redline has no aliases). HYD-QCA-06-STL quick-
    disconnect coupler — a real rig-down failure mode.
5.  **Informal sign-off.** "J." (handwritten-style initials, not a formal
    signature block). Oilfield procurement culture is terse.
6.  **Houston yard ship-to** with the after-hours lockbox code preserved
    from the customer master.
7.  **No totals block, no subtotal, no tax** — one line, no ceremony.
8.  **List price** (Redline has no contract discount).

Run with: ``uv run python -m scripts.generators.pdf_redline_urgent``
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


# Single-line urgent order — QD coupler is a realistic rig-down failure part.
SKU = "HYD-QCA-06-STL"
DESCRIPTION = "QD Coupler ISO 7241-A (AG), 3/8\" Body, 3/8 NPT F, Chrome-Plated Steel"
QTY = 1
UOM = "EA"
UNIT_PRICE = 32.50  # list — no Redline contract

ORDER_DATE = "04/19/2026"  # filename date
SHIP_TO_CODE = "REDLINE-HOU"
SHIP_TO_ADDR = (
    "8300 Market Street Road, Yard gate (after-hours lockbox code 4720), Houston TX 77029"
)
BUYER_NAME = "Javier Quinonez"
BUYER_ROLE = "Parts Buyer"
BUYER_EMAIL = "j.quinonez@redlineoilfield.com"
BUYER_PHONE = "(713) 928-4410"
TERMS = "Net 30"
RIG_ID = "RED-07"
SN_REF = "4X-H42-11798"


def build_story() -> list:
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["Normal"], fontName="Helvetica", fontSize=10, leading=12)
    small = ParagraphStyle("small", parent=body, fontSize=9, leading=11)
    letterhead = ParagraphStyle("lh", parent=body, fontName="Helvetica-Bold", fontSize=14,
                                textColor=colors.HexColor("#8B1A1A"))
    dba = ParagraphStyle("dba", parent=body, fontName="Helvetica-Oblique", fontSize=9,
                         textColor=colors.HexColor("#555555"))
    urgent_banner = ParagraphStyle(
        "ub", parent=body, fontName="Helvetica-Bold", fontSize=18,
        textColor=colors.HexColor("#8B1A1A"), alignment=1, leading=22,
    )
    callout = ParagraphStyle(
        "call", parent=body, fontName="Helvetica-Bold", fontSize=16,
        textColor=colors.white, alignment=1, leading=20, backColor=colors.HexColor("#8B1A1A"),
        borderPadding=6,
    )
    label = ParagraphStyle("label", parent=body, fontName="Helvetica-Bold", fontSize=10)
    sig = ParagraphStyle("sig", parent=body, fontName="Helvetica-Oblique", fontSize=10)
    item_style = ParagraphStyle("item", parent=body, fontName="Courier", fontSize=10, leading=12)

    story: list = []

    story.append(Paragraph("REDLINE EQUIPMENT REPAIR LLC", letterhead))
    story.append(Paragraph("dba Redline Oilfield Services", dba))
    story.append(Paragraph(
        "8300 Market Street Road, Bldg C  •  Houston, TX 77029  •  (713) 928-4410",
        dba,
    ))
    story.append(Spacer(1, 14))

    story.append(Paragraph("URGENT REORDER", urgent_banner))
    story.append(Spacer(1, 12))

    # Compact metadata — no PO number field
    meta_data = [
        [Paragraph("Order Date:", label), Paragraph(ORDER_DATE, body),
         Paragraph("Buyer:", label),      Paragraph(f"{BUYER_NAME}  ({BUYER_ROLE})", body)],
        [Paragraph("Ship To:", label),    Paragraph(f"{SHIP_TO_CODE} — {SHIP_TO_ADDR}", body),
         Paragraph("Email:", label),      Paragraph(BUYER_EMAIL, body)],
        [Paragraph("Terms:", label),      Paragraph(TERMS, body),
         Paragraph("Phone:", label),      Paragraph(BUYER_PHONE, body)],
    ]
    meta_tbl = Table(meta_data, colWidths=[0.9 * inch, 2.9 * inch, 0.7 * inch, 2.5 * inch])
    meta_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(meta_tbl)
    story.append(Spacer(1, 10))

    # Callout banner — ASAP / RIG DOWN
    callout_tbl = Table(
        [[Paragraph("DELIVER ASAP — RIG DOWN", callout)]],
        colWidths=[7.0 * inch],
    )
    callout_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#8B1A1A")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(callout_tbl)
    story.append(Spacer(1, 12))

    # Single-line "table" — one item, no totals
    header = ["Item", "Description", "Qty", "UOM", "Unit Price", "Amount"]
    table_data = [
        header,
        [
            Paragraph(SKU, item_style),
            Paragraph(DESCRIPTION, body),
            str(QTY),
            UOM,
            f"${UNIT_PRICE:,.2f}",
            f"${QTY * UNIT_PRICE:,.2f}",
        ],
    ]
    line_tbl = Table(
        table_data,
        colWidths=[1.5 * inch, 2.85 * inch, 0.5 * inch, 0.45 * inch, 0.85 * inch, 0.85 * inch],
    )
    line_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#333333")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("ALIGN", (2, 1), (2, -1), "RIGHT"),
        ("ALIGN", (3, 1), (3, -1), "CENTER"),
        ("ALIGN", (4, 1), (5, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#666666")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(line_tbl)
    story.append(Spacer(1, 16))

    # Notes with rig serial reference — customer context, NOT a line item
    story.append(Paragraph("<b>Notes:</b>", body))
    story.append(Paragraph(
        f"Rig {RIG_ID}  |  Pump Assy SN {SN_REF}  —  hot-stab QD failed on 04/19 "
        "pull. Need the replacement in the yard before AM crew tomorrow. Call me on "
        "the mobile if anything holds this up, cash on the table if it has to.",
        small,
    ))
    story.append(Spacer(1, 18))

    # Informal sign-off
    story.append(Paragraph("— J.", sig))
    story.append(Paragraph(f"{BUYER_NAME}, {BUYER_ROLE}", sig))

    return story


def _on_page(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(colors.HexColor("#777777"))
    canvas.drawString(
        0.75 * inch, 0.5 * inch,
        f"Redline Oilfield Services  •  Urgent Reorder  •  {ORDER_DATE}",
    )
    canvas.drawRightString(LETTER[0] - 0.75 * inch, 0.5 * inch, f"Page {doc.page}")
    canvas.restoreState()


def main() -> Path:
    out = Path(__file__).resolve().parents[2] / "data" / "pdf" / "redline_urgent_2026-04-19.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = BaseDocTemplate(
        str(out),
        pagesize=LETTER,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.75 * inch,
        title="Redline Urgent Reorder",
        author="Redline Equipment Repair LLC",
        subject="Urgent hydraulic reorder — rig down",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="normal")
    doc.addPageTemplates([PageTemplate(id="main", frames=frame, onPage=_on_page)])
    doc.build(build_story())
    return out


if __name__ == "__main__":
    path = main()
    print(f"wrote: {path}")
