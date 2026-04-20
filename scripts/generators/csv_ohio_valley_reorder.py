"""Generate Ohio Valley Industrial's clean CSV reorder — Phase 2.1 exemplar.

Same customer as Phase 1.3 (JD Edwards shop in Cincinnati), but a *different*
export path: their ERP also emits CSV for downstream buyers that prefer flat
files. This file is the clean-baseline CSV: strict RFC 4180, UTF-8 (no BOM),
CRLF line endings, headers on row 1, prices included, no trailing junk.

Contrast with Phase 2.2 (Patterson quirky CSV) which will carry a BOM,
trailing blank rows, SUBTOTAL/TAX/TOTAL summary rows, and fields with
embedded commas requiring quoting.

Realism anchors:

1.  **JD Edwards field naming.** All-caps, underscored, fixed-shape: `PO_NBR`,
    `LINE_NO`, `ITEM_NBR`, `QTY_REQ`, `UNIT_PRICE`, `EXT_PRICE`, `REQ_DATE`.
    Mirrors what the XE/EnterpriseOne stock-status export actually produces.
2.  **Numeric PO#**, leading-zero-padded — how JDE's DOCO counter serializes.
3.  **Short-desc truncation style** — descriptions match the 30-char short_desc
    field from the product master; reads as truncated-from-a-wider-field.
4.  **No embedded commas in descriptions.** Clean means minimum quoting surface.
    Patterson's quirky file is the one that exercises the quote-escape path.
5.  **CRLF line endings.** RFC 4180 mandates CRLF; a strictly-compliant
    JDE export honors that. Modern tooling accepts either.
6.  **Prices included.** Grafton-Reese list prices (Ohio Valley has no contract).
    Extended prices computed to the cent — not a formula, since CSV has no
    formula layer; the JDE export would have done the math server-side.

Run with: ``uv run python -m scripts.generators.csv_ohio_valley_reorder``
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Line:
    item_nbr: str
    description: str
    qty: int
    uom: str
    unit_price: float


# List prices straight from data/masters/products.json (Ohio Valley has no
# contract). Mix reflects a general-industrial weekly replenishment order:
# fasteners in bulk, pneumatic and hydraulic singletons.
LINES: tuple[Line, ...] = (
    Line("FST-HCS-050-13-200-G5Z",  "HCS 1/2-13 X 2 GR5 ZP",          500, "EA", 0.34),
    Line("FST-HCS-M10-150-40-88Z",  "HCS M10-1.5 X 40 CL8.8 ZP",      250, "EA", 0.29),
    Line("FST-SHC-025-20-125-AB",   "SHCS 1/4-20 X 1-1/4 ALY BO",     300, "EA", 0.19),
    Line("FST-HXN-050-13-G5Z",      "HEX NUT 1/2-13 GR5 ZP",          750, "EA", 0.11),
    Line("FST-FWS-038-USS-ZP",      "FLAT WASH 3/8 USS ZP",          1000, "EA", 0.04),
    Line("FST-SLW-050-MS-ZP",       "LOCK WASH 1/2 MS ZP",            500, "EA", 0.06),
    Line("FST-SMS-010-16-100-ZT3",  "SDS #10-16 X 1 HWH ZP T3",       250, "EA", 0.08),
    Line("HYD-HSE-R1-04",           "HOSE 100R1 -4 1/4 ID",           150, "FT", 2.27),
    Line("HYD-QCA-06-STL",          "QC COUPLER ISO-A -6 3/8NPT STL",   6, "EA", 32.50),
    Line("PNM-PCE-038-025N-NPB",    "PC ELL 90 3/8T X 1/4NPT NPB",     60, "EA", 5.85),
    Line("PNM-TBE-PU-025-100FT-BL", "TUBING PU 1/4OD 100FT BLUE",       4, "RL", 32.40),
)

PO_NBR = "0452187"       # JDE DOCO-style numeric counter, 7 digits
REQ_DATE = "04/22/2026"  # ~2 weeks from 2026-04-08 filename date


def build_rows() -> list[list[str]]:
    rows: list[list[str]] = [[
        "PO_NBR", "LINE_NO", "ITEM_NBR", "DESCRIPTION",
        "QTY_REQ", "UOM", "UNIT_PRICE", "EXT_PRICE", "REQ_DATE",
    ]]
    for idx, ln in enumerate(LINES, start=1):
        ext = round(ln.qty * ln.unit_price, 2)
        rows.append([
            PO_NBR,
            f"{idx:03d}",               # LINE_NO zero-padded (JDE fixed-width)
            ln.item_nbr,
            ln.description,
            str(ln.qty),
            ln.uom,
            f"{ln.unit_price:.2f}",
            f"{ext:.2f}",
            REQ_DATE,
        ])
    return rows


def main() -> Path:
    out = Path(__file__).resolve().parents[2] / "data" / "csv" / "ohio_valley_reorder_2026-04-08.csv"
    out.parent.mkdir(parents=True, exist_ok=True)

    # RFC 4180: CRLF line endings, UTF-8, no BOM. Use newline="" + explicit
    # lineterminator so csv.writer controls the terminator directly.
    with out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
        writer.writerows(build_rows())
    return out


if __name__ == "__main__":
    path = main()
    print(f"wrote: {path}")
