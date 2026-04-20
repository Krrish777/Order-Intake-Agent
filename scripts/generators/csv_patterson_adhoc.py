"""Generate Patterson's one-off adhoc CSV reorder — Phase 2.2 quirky variant.

Patterson is a large_distributor that normally routes through X12 850 over the
GXS VAN. This file is an *adhoc* CSV — the kind of thing their procurement team
exports when their EDI pipeline is broken or when the order spans multiple
ship-to locations that don't fit their EDI workflow. The resulting file carries
the fingerprints of Windows Excel "Save As CSV":

1.  **UTF-8 BOM** (`EF BB BF`) at the start — Excel's Windows default.
2.  **CRLF line endings** throughout.
3.  **Trailing three blank rows** after the totals — Excel preserves the used
    range even when rows are visually empty.
4.  **SUBTOTAL / TAX / TOTAL summary rows** at the bottom — not structured
    fields, but rows with mostly-empty cells and a label+amount at the right.
5.  **Quoted fields with embedded commas and inch-mark quotes** — e.g.
    `"Hex Cap Screw, 1/2-13 x 2"", Gr 5 Zinc"`. Exercises the RFC 4180
    quote-escape (`""` inside a quoted field).
6.  **Patterson numeric SKU aliases** (`887712`, `912055`) rather than
    canonical Grafton-Reese SKUs. Agent must resolve via `customers.json`.
7.  **Ship-to varies per line** — three locations (CLE, ATL, DAL) interleaved.
    Honors Patterson's documented "ship-to location code required on every PO
    line" rule.
8.  **Contract pricing** — 10-15% off Grafton-Reese list with natural per-SKU
    variance (not a flat %). Small-unit fasteners see smaller % discounts.

Run with: ``uv run python -m scripts.generators.csv_patterson_adhoc``
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Line:
    patterson_item: str
    description: str
    qty: int
    uom: str
    unit_price: float
    ship_to: str


# Patterson contract pricing — deliberate per-SKU variance, not a flat %.
# Small-dollar fasteners see smaller discounts (penny-rounding floor);
# larger hydraulic items see closer to 13-14% (Patterson's Hydraulic-category
# rebate pressure). All prices derived from products.json list, then discounted
# individually to the cent.
LINES: tuple[Line, ...] = (
    Line("887712", 'Hex Cap Screw, 1/2-13 x 2", Gr 5 Zinc',                500, "EA",  0.30, "PATT-CLE-01"),
    Line("887714", 'Hex Cap Screw, 1/2-13 x 1-1/2", SS 18-8',              200, "EA",  1.12, "PATT-CLE-01"),
    Line("887720", 'Hex Cap Screw, 3/8-16 x 1", Gr 8 Yellow Zinc',         400, "EA",  0.36, "PATT-ATL-02"),
    Line("887723", "Hex Nut, 1/2-13, Gr 5 Zinc",                           750, "EA",  0.10, "PATT-CLE-01"),
    Line("887745", 'Flat Washer, 1/2" SAE, SS 18-8',                      1500, "EA",  0.08, "PATT-DAL-03"),
    Line("912055", "JIC Male Straight, -6 (9/16-18 UNF), Steel ZN",         36, "EA",  2.98, "PATT-ATL-02"),
    Line("912061", "JIC Male Elbow 90°, -6 (9/16-18 UNF), Forged Steel",    18, "EA",  7.66, "PATT-ATL-02"),
    Line("912118", 'Hydraulic Hose, 100R2, -6 (3/8" ID), 2-Wire',          250, "FT",  3.37, "PATT-CLE-01"),
    Line("912124", 'Hydraulic Hose, 100R2, -8 (1/2" ID), 2-Wire',          175, "FT",  4.44, "PATT-DAL-03"),
    Line("912402", 'QD Coupler, ISO 7241-A, 3/8" Body, 3/8 NPT-F',           8, "EA", 28.15, "PATT-CLE-01"),
    Line("887712", 'Hex Cap Screw, 1/2-13 x 2", Gr 5 Zinc',                300, "EA",  0.30, "PATT-ATL-02"),
    Line("887720", 'Hex Cap Screw, 3/8-16 x 1", Gr 8 Yellow Zinc',         200, "EA",  0.36, "PATT-DAL-03"),
    Line("912118", 'Hydraulic Hose, 100R2, -6 (3/8" ID), 2-Wire',          150, "FT",  3.37, "PATT-ATL-02"),
    Line("887745", 'Flat Washer, 1/2" SAE, SS 18-8',                       500, "EA",  0.08, "PATT-CLE-01"),
    Line("912061", "JIC Male Elbow 90°, -6 (9/16-18 UNF), Forged Steel",     6, "EA",  7.66, "PATT-DAL-03"),
)

PO_NBR = "PO-28503"
BUYER = "Susan McCreary"
REQ_DATE = "04/25/2026"
TERMS = "Net 45"


def build_rows() -> list[list[str]]:
    rows: list[list[str]] = []

    header = [
        "Customer PO", "Line #", "Patterson Item", "Description",
        "Qty", "UOM", "Unit Price", "Extended Price", "Ship To Loc",
        "Req Date", "Buyer", "Terms",
    ]
    rows.append(header)

    subtotal = 0.0
    for idx, ln in enumerate(LINES, start=1):
        ext = round(ln.qty * ln.unit_price, 2)
        subtotal = round(subtotal + ext, 2)
        rows.append([
            PO_NBR,
            str(idx),
            ln.patterson_item,
            ln.description,          # csv.QUOTE_MINIMAL will quote + escape inch marks
            str(ln.qty),
            ln.uom,
            f"{ln.unit_price:.2f}",
            f"{ext:.2f}",
            ln.ship_to,
            REQ_DATE,
            BUYER,
            TERMS,
        ])

    # Summary rows — labels sit in the Extended Price-adjacent columns, emulating
    # Excel's "save as CSV" dump where totals land in whatever column the user
    # typed them into. Left-side columns are empty strings.
    blank_prefix = [""] * 6
    rows.append(blank_prefix + ["SUBTOTAL", f"{subtotal:.2f}", "", "", "", ""])
    rows.append(blank_prefix + ["TAX (resale exempt)", "0.00", "", "", "", ""])
    total = subtotal  # resale exempt
    rows.append(blank_prefix + ["TOTAL", f"{total:.2f}", "", "", "", ""])

    # Three trailing blank rows — Excel's used-range artifact. Empty lists write
    # just a CRLF; we use a row of empty strings so the file still has the
    # correct column count on those lines (more Excel-authentic than bare CRLFs).
    for _ in range(3):
        rows.append([""] * len(header))

    return rows


def main() -> Path:
    out = Path(__file__).resolve().parents[2] / "data" / "csv" / "patterson_adhoc_reorder.csv"
    out.parent.mkdir(parents=True, exist_ok=True)

    # Write BOM + CRLF-terminated UTF-8 content. utf-8-sig + newline="" gives us
    # the Windows-Excel shape: BOM prefix, csv.writer controlling CRLF directly.
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
        writer.writerows(build_rows())
    return out


if __name__ == "__main__":
    path = main()
    print(f"wrote: {path}")
