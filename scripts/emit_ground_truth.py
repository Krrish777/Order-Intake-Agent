"""Emit sibling `.expected.json` ground-truth files for every corpus document.

Runs once; reruns are idempotent. Each GroundTruth is constructed inline and
validated through the Pydantic schema in `backend/models/ground_truth.py`
before being serialized. A separate `verify_corpus.py` script performs the
cross-reference integrity check (customer_id exists in customers.json,
canonical_sku exists in products.json, sibling source file exists on disk).

Run with: ``uv run python -m scripts.emit_ground_truth``
"""

from __future__ import annotations

from pathlib import Path

from backend.models.ground_truth import GroundTruth, GroundTruthLineItem


ROOT = Path(__file__).resolve().parents[1]


def _li(**kwargs) -> GroundTruthLineItem:
    return GroundTruthLineItem(**kwargs)


# ============================================================================
# Phase 1 — Excel
# ============================================================================

# 1.1 — GLFP clean weekly reorder
GT_GLFP_XLSX = GroundTruth(
    source_doc="data/excel/glfp_weekly_reorder_2026-04-14.xlsx",
    customer_id="CUST-00078",
    format="excel",
    edge_case="clean",
    po_number="PO-2026-05847",
    po_date="2026-04-14",
    required_date="2026-04-21",
    ship_to_code="GLFP-GRR-MAIN",
    payment_terms="Net 30",
    line_items=[
        _li(line_number=1,  customer_ref="GLFP-04821", canonical_sku="HYD-MJS-06-STL",
            description="JIC 37 Male Straight Adapter, -6 (9/16-18 UNF), Steel ZN",
            quantity=48, unit_of_measure="EA", unit_price=3.08),
        _li(line_number=2,  customer_ref="GLFP-04824", canonical_sku="HYD-MJE-06-STL",
            description="JIC 37 Male Elbow 90 deg, -6 (9/16-18 UNF), Forged Steel",
            quantity=25, unit_of_measure="EA", unit_price=7.82),
        _li(line_number=3,  customer_ref="GLFP-04831", canonical_sku="HYD-MJN-06-04-STL",
            description="JIC Male x NPT Male Adapter, -6 x 1/4in NPT, Steel",
            quantity=30, unit_of_measure="EA", unit_price=3.75),
        _li(line_number=4,  customer_ref="GLFP-04840", canonical_sku="HYD-ORB-06-STL",
            description="SAE ORB Male Straight, -6 (9/16-18 UNF), Buna-N, Steel",
            quantity=22, unit_of_measure="EA", unit_price=5.02),
        _li(line_number=5,  customer_ref="GLFP-05110", canonical_sku="HYD-HSE-R2-06",
            description="Hydraulic Hose SAE 100R2AT, -6 (3/8 ID), 2-Wire",
            quantity=175, unit_of_measure="FT", unit_price=3.47),
        _li(line_number=6,  customer_ref="GLFP-05115", canonical_sku="HYD-HSE-R2-08",
            description="Hydraulic Hose SAE 100R2AT, -8 (1/2 ID), 2-Wire",
            quantity=225, unit_of_measure="FT", unit_price=4.58),
        _li(line_number=7,  customer_ref="GLFP-05100", canonical_sku="HYD-HSE-R1-04",
            description="Hydraulic Hose SAE 100R1AT, -4 (1/4 ID), 1-Wire",
            quantity=125, unit_of_measure="FT", unit_price=2.05),
        _li(line_number=8,  customer_ref="GLFP-05220", canonical_sku="HYD-HFF-08-STL",
            description="Hose End Fitting, Female JIC Swivel -8, Crimp 2-Wire",
            quantity=60, unit_of_measure="EA", unit_price=6.24),
        _li(line_number=9,  customer_ref="GLFP-06010", canonical_sku="HYD-QCA-06-STL",
            description="QD Coupler ISO 7241-A (AG), -6 Body x 3/8in NPT F, Steel",
            quantity=6, unit_of_measure="EA", unit_price=29.15),
        _li(line_number=10, customer_ref="GLFP-06050", canonical_sku="HYD-CHK-08-NPT-STL",
            description="Inline Check Valve, 1/2in NPT F, 5 PSI Crack, Steel",
            quantity=4, unit_of_measure="EA", unit_price=25.80),
        _li(line_number=11, customer_ref="GLFP-11200", canonical_sku="PNM-QCI-025N-STL",
            description="QC Coupler Industrial M-Style, 1/4in NPT F, Steel",
            quantity=18, unit_of_measure="EA", unit_price=6.45),
        _li(line_number=12, customer_ref="GLFP-11210", canonical_sku="PNM-QPL-025N-STL",
            description="QC Plug Industrial M-Style, 1/4in NPT M, Hardened Steel",
            quantity=24, unit_of_measure="EA", unit_price=3.55),
    ],
    expected_routing="auto_process",
)

# 1.2 — Hagan typo + label variations
GT_HAGAN_XLSX = GroundTruth(
    source_doc="data/excel/hagan_reorder_2026-04-09.xlsx",
    customer_id="CUST-00201",
    format="excel",
    edge_case="typos_label_variations",
    po_number="H-26189",
    po_date="2026-04-09",
    required_date="2026-04-23",
    ship_to_code="HAGAN-WHL-02",
    payment_terms="Net 30",
    line_items=[
        _li(line_number=1, customer_ref="FST-HCS-050-13-200-G5Z", canonical_sku="FST-HCS-050-13-200-G5Z",
            description="Hex Cap Screw 1/2-13 x 2 GR5 Zinc",
            quantity=150, unit_of_measure="EA", unit_price=0.34),
        _li(line_number=2, customer_ref="FST-HXN-050-13-G5Z", canonical_sku="FST-HXN-050-13-G5Z",
            description="Hex Nut 1/2-13 GR5 Zinc",
            quantity=200, unit_of_measure="EA", unit_price=0.11),
        _li(line_number=3, customer_ref="FST-FWS-050-SAE-S18", canonical_sku="FST-FWS-050-SAE-S18",
            description="Flat Washer 1/2 SAE 18-8 SS",
            quantity=250, unit_of_measure="EA", unit_price=0.09),
        _li(line_number=4, customer_ref="HYD-MJS-06-STL", canonical_sku="HYD-MJS-06-STL",
            description="JIC Male Straight -6 Steel",
            quantity=24, unit_of_measure="EA", unit_price=3.42),
        _li(line_number=5, customer_ref="HYD-MJE-06-STL", canonical_sku="HYD-MJE-06-STL",
            description="JIC Male Elbow 90 -6 Steel",
            quantity=12, unit_of_measure="EA", unit_price=8.75),
        _li(line_number=6, customer_ref="HYD-ORB-06-STL", canonical_sku="HYD-ORB-06-STL",
            description="ORB Male Straight -6 Steel",
            quantity=15, unit_of_measure="EA", unit_price=5.62),
        _li(line_number=7, customer_ref="HYD-MJN-06-04-STL", canonical_sku="HYD-MJN-06-04-STL",
            description="JIC M x NPT M -6 x 1/4 Steel",
            quantity=20, unit_of_measure="EA", unit_price=4.18),
    ],
    known_ambiguities=[
        "Header typos (Desciption, Qty Ordred, Due Dte) — schema-tolerant parse required",
        "Column order swap: UOM precedes Qty — positional parsers must remap",
        "Ship-to is secondary branch (Wheeling WV), not primary Pittsburgh — verify against customer ship_to list",
        "Metadata label drift: 'PO No:' / 'po date:' / 'Ship To:' / 'payment terms' — inconsistent casing",
    ],
    expected_routing="auto_process",
)

# 1.3 — Ohio Valley ambiguity (missing PO number, prices, structured date)
GT_OVI_XLSX = GroundTruth(
    source_doc="data/excel/ohio_valley_reorder_march_wk3.xlsx",
    customer_id="CUST-00294",
    format="excel",
    edge_case="ambiguity_missing_fields",
    po_number=None,
    po_date=None,
    required_date=None,
    ship_to_code="OVI-CIN",
    payment_terms="Net 45",
    line_items=[
        _li(line_number=1, customer_ref="FST-HCS-038-16-100-G8YZ", canonical_sku="FST-HCS-038-16-100-G8YZ",
            description="HCS 3/8-16 X 1 GR8 YELLOW ZINC",
            quantity=500, unit_of_measure="EA", unit_price=None),
        _li(line_number=2, customer_ref="FST-NYL-038-16-S18", canonical_sku="FST-NYL-038-16-S18",
            description="NYLOC 3/8-16 18-8 SS",
            quantity=300, unit_of_measure="EA", unit_price=None),
        _li(line_number=3, customer_ref="PNM-PCS-025-025N-BR", canonical_sku="PNM-PCS-025-025N-BR",
            description="PUSH-CONNECT STR 1/4T X 1/4NPT BRASS",
            quantity=50, unit_of_measure="EA", unit_price=None),
        _li(line_number=4, customer_ref="PNM-QCI-025N-STL", canonical_sku="PNM-QCI-025N-STL",
            description="QC COUPLER IND 1/4 NPT F STL",
            quantity=40, unit_of_measure="EA", unit_price=None),
        _li(line_number=5, customer_ref="HYD-HSE-R2-06", canonical_sku="HYD-HSE-R2-06",
            description="HOSE 100R2AT -6 3/8 ID 2-WIRE",
            quantity=100, unit_of_measure="FT", unit_price=None),
    ],
    known_ambiguities=[
        "No explicit PO number — filename (march_wk3) is the only reference",
        "Need-by is literal string 'ASAP' — not a structured date",
        "Filename week tag ambiguous relative to calendar (recycles weekly)",
        "No unit prices in source — pricing must be looked up from vendor list",
        "Header row is at row 4 (after 3-row JDE branding block), not row 1",
        "Ship-to implied from customer master — not stated explicitly",
    ],
    expected_routing="human_review",
)


# ============================================================================
# Phase 2 — CSV
# ============================================================================

# 2.1 — Ohio Valley clean CSV
GT_OVI_CSV = GroundTruth(
    source_doc="data/csv/ohio_valley_reorder_2026-04-08.csv",
    customer_id="CUST-00294",
    format="csv",
    edge_case="clean",
    po_number="0452187",
    po_date="2026-04-08",
    required_date="2026-04-22",
    ship_to_code=None,  # CSV has no ship-to column; customer has only one ship-to
    payment_terms=None,  # Not in CSV; customer master says Net 45
    line_items=[
        _li(line_number=1,  customer_ref="FST-HCS-050-13-200-G5Z", canonical_sku="FST-HCS-050-13-200-G5Z",
            description="HCS 1/2-13 X 2 GR5 ZP",
            quantity=500, unit_of_measure="EA", unit_price=0.34),
        _li(line_number=2,  customer_ref="FST-HCS-M10-150-40-88Z", canonical_sku="FST-HCS-M10-150-40-88Z",
            description="HCS M10-1.5 X 40 CL8.8 ZP",
            quantity=250, unit_of_measure="EA", unit_price=0.29),
        _li(line_number=3,  customer_ref="FST-SHC-025-20-125-AB", canonical_sku="FST-SHC-025-20-125-AB",
            description="SHCS 1/4-20 X 1-1/4 ALY BO",
            quantity=300, unit_of_measure="EA", unit_price=0.19),
        _li(line_number=4,  customer_ref="FST-HXN-050-13-G5Z", canonical_sku="FST-HXN-050-13-G5Z",
            description="HEX NUT 1/2-13 GR5 ZP",
            quantity=750, unit_of_measure="EA", unit_price=0.11),
        _li(line_number=5,  customer_ref="FST-FWS-038-USS-ZP", canonical_sku="FST-FWS-038-USS-ZP",
            description="FLAT WASH 3/8 USS ZP",
            quantity=1000, unit_of_measure="EA", unit_price=0.04),
        _li(line_number=6,  customer_ref="FST-SLW-050-MS-ZP", canonical_sku="FST-SLW-050-MS-ZP",
            description="LOCK WASH 1/2 MS ZP",
            quantity=500, unit_of_measure="EA", unit_price=0.06),
        _li(line_number=7,  customer_ref="FST-SMS-010-16-100-ZT3", canonical_sku="FST-SMS-010-16-100-ZT3",
            description="SDS #10-16 X 1 HWH ZP T3",
            quantity=250, unit_of_measure="EA", unit_price=0.08),
        _li(line_number=8,  customer_ref="HYD-HSE-R1-04", canonical_sku="HYD-HSE-R1-04",
            description="HOSE 100R1 -4 1/4 ID",
            quantity=150, unit_of_measure="FT", unit_price=2.27),
        _li(line_number=9,  customer_ref="HYD-QCA-06-STL", canonical_sku="HYD-QCA-06-STL",
            description="QC COUPLER ISO-A -6 3/8NPT STL",
            quantity=6, unit_of_measure="EA", unit_price=32.50),
        _li(line_number=10, customer_ref="PNM-PCE-038-025N-NPB", canonical_sku="PNM-PCE-038-025N-NPB",
            description="PC ELL 90 3/8T X 1/4NPT NPB",
            quantity=60, unit_of_measure="EA", unit_price=5.85),
        _li(line_number=11, customer_ref="PNM-TBE-PU-025-100FT-BL", canonical_sku="PNM-TBE-PU-025-100FT-BL",
            description="TUBING PU 1/4OD 100FT BLUE",
            quantity=4, unit_of_measure="RL", unit_price=32.40),
    ],
    known_ambiguities=[
        "Ship-to and payment terms absent from source — derive from customer master",
    ],
    expected_routing="auto_process",
)

# 2.2 — Patterson adhoc quirky CSV
PATT_SHIPTO_BY_LINE = {
    1: "PATT-CLE-01", 2: "PATT-CLE-01", 3: "PATT-ATL-02", 4: "PATT-CLE-01",
    5: "PATT-DAL-03", 6: "PATT-ATL-02", 7: "PATT-ATL-02", 8: "PATT-CLE-01",
    9: "PATT-DAL-03", 10: "PATT-CLE-01", 11: "PATT-ATL-02", 12: "PATT-DAL-03",
    13: "PATT-ATL-02", 14: "PATT-CLE-01", 15: "PATT-DAL-03",
}
PATT_CSV_LINES = [
    # (ln, patt_item, canonical, desc, qty, uom, price)
    (1,  "887712", "FST-HCS-050-13-200-G5Z", 'Hex Cap Screw, 1/2-13 x 2", Gr 5 Zinc',                 500, "EA",  0.30),
    (2,  "887714", "FST-HCS-050-13-150-S18", 'Hex Cap Screw, 1/2-13 x 1-1/2", SS 18-8',               200, "EA",  1.12),
    (3,  "887720", "FST-HCS-038-16-100-G8YZ",'Hex Cap Screw, 3/8-16 x 1", Gr 8 Yellow Zinc',          400, "EA",  0.36),
    (4,  "887723", "FST-HXN-050-13-G5Z",     "Hex Nut, 1/2-13, Gr 5 Zinc",                            750, "EA",  0.10),
    (5,  "887745", "FST-FWS-050-SAE-S18",    'Flat Washer, 1/2" SAE, SS 18-8',                       1500, "EA",  0.08),
    (6,  "912055", "HYD-MJS-06-STL",         "JIC Male Straight, -6 (9/16-18 UNF), Steel ZN",          36, "EA",  2.98),
    (7,  "912061", "HYD-MJE-06-STL",         "JIC Male Elbow 90°, -6 (9/16-18 UNF), Forged Steel",     18, "EA",  7.66),
    (8,  "912118", "HYD-HSE-R2-06",          'Hydraulic Hose, 100R2, -6 (3/8" ID), 2-Wire',           250, "FT",  3.37),
    (9,  "912124", "HYD-HSE-R2-08",          'Hydraulic Hose, 100R2, -8 (1/2" ID), 2-Wire',           175, "FT",  4.44),
    (10, "912402", "HYD-QCA-06-STL",         'QD Coupler, ISO 7241-A, 3/8" Body, 3/8 NPT-F',            8, "EA", 28.15),
    (11, "887712", "FST-HCS-050-13-200-G5Z", 'Hex Cap Screw, 1/2-13 x 2", Gr 5 Zinc',                 300, "EA",  0.30),
    (12, "887720", "FST-HCS-038-16-100-G8YZ",'Hex Cap Screw, 3/8-16 x 1", Gr 8 Yellow Zinc',          200, "EA",  0.36),
    (13, "912118", "HYD-HSE-R2-06",          'Hydraulic Hose, 100R2, -6 (3/8" ID), 2-Wire',           150, "FT",  3.37),
    (14, "887745", "FST-FWS-050-SAE-S18",    'Flat Washer, 1/2" SAE, SS 18-8',                        500, "EA",  0.08),
    (15, "912061", "HYD-MJE-06-STL",         "JIC Male Elbow 90°, -6 (9/16-18 UNF), Forged Steel",      6, "EA",  7.66),
]
GT_PATT_CSV = GroundTruth(
    source_doc="data/csv/patterson_adhoc_reorder.csv",
    customer_id="CUST-00042",
    format="csv",
    edge_case="quirky_encoding",
    po_number="PO-28503",
    po_date=None,  # Not stated in CSV body
    required_date="2026-04-25",
    ship_to_code=None,  # Varies per line
    payment_terms="Net 45",
    line_items=[
        _li(line_number=ln, customer_ref=pi, canonical_sku=cs,
            description=desc, quantity=q, unit_of_measure=u, unit_price=p,
            notes=f"ship_to={PATT_SHIPTO_BY_LINE[ln]}")
        for ln, pi, cs, desc, q, u, p in PATT_CSV_LINES
    ],
    known_ambiguities=[
        "UTF-8 BOM prefix must be stripped before parsing first field",
        "Descriptions contain embedded inch-mark quotes and commas requiring RFC 4180 unquoting (\"\" escape)",
        "Ship-to varies per line across three Patterson locations (PATT-CLE-01, PATT-ATL-02, PATT-DAL-03)",
        "Summary rows (SUBTOTAL, TAX, TOTAL) interleaved with data — row classifier must skip non-data rows",
        "Trailing three blank (comma-only) rows must not emit phantom line items",
        "Patterson numeric aliases (887xxx, 912xxx) require customer master lookup for canonical resolution",
    ],
    expected_routing="auto_process",
)


# ============================================================================
# Phase 3 — PDF
# ============================================================================

# 3.1 — Patterson clean formal PDF (22 lines)
PATT_PDF_LINES = [
    # (ln, buyer_ref, canonical, desc, qty, uom, price)
    (1,  "887712", "FST-HCS-050-13-200-G5Z",  'Hex Cap Screw, 1/2-13 x 2", Gr 5 Zinc',             1200, "EA",  0.30),
    (2,  "887714", "FST-HCS-050-13-150-S18",  'Hex Cap Screw, 1/2-13 x 1-1/2", 18-8 SS',            300, "EA",  1.12),
    (3,  "887720", "FST-HCS-038-16-100-G8YZ", 'Hex Cap Screw, 3/8-16 x 1", Gr 8 Yellow Zinc',       800, "EA",  0.36),
    (4,  "887723", "FST-HXN-050-13-G5Z",      "Hex Nut, 1/2-13, Gr 5 Zinc",                        1500, "EA",  0.10),
    (5,  "887745", "FST-FWS-050-SAE-S18",     'Flat Washer, 1/2" SAE, 18-8 SS',                    2500, "EA",  0.08),
    (6,  "912055", "HYD-MJS-06-STL",          "JIC Male Straight, -6 (9/16-18 UNF), Steel ZN",      100, "EA",  2.98),
    (7,  "912061", "HYD-MJE-06-STL",          "JIC Male Elbow 90°, -6 (9/16-18 UNF), Forged Steel",  48, "EA",  7.66),
    (8,  "912118", "HYD-HSE-R2-06",           'Hydraulic Hose, 100R2, -6 (3/8" ID), 2-Wire',        500, "FT",  3.37),
    (9,  "912124", "HYD-HSE-R2-08",           'Hydraulic Hose, 100R2, -8 (1/2" ID), 2-Wire',        375, "FT",  4.44),
    (10, "912402", "HYD-QCA-06-STL",          'QD Coupler, ISO 7241-A, 3/8" Body, 3/8 NPT-F',        12, "EA", 28.15),
    (11, "FST-HCS-062-11-250-S316",  "FST-HCS-062-11-250-S316",  'Hex Cap Screw, 5/8-11 x 2-1/2", 316 SS',   75, "EA",  3.38),
    (12, "FST-HCS-M10-150-40-88Z",   "FST-HCS-M10-150-40-88Z",   "Hex Cap Screw, M10-1.5 x 40 mm, Cl 8.8 Zinc", 500, "EA", 0.26),
    (13, "FST-SHC-025-20-125-AB",    "FST-SHC-025-20-125-AB",    'Socket Head Cap Screw, 1/4-20 x 1-1/4", Alloy BO', 400, "EA", 0.17),
    (14, "FST-SHC-038-16-150-S18",   "FST-SHC-038-16-150-S18",   'Socket Head Cap Screw, 3/8-16 x 1-1/2", 18-8 SS', 150, "EA", 1.54),
    (15, "FST-FHC-010-24-075-AB",    "FST-FHC-010-24-075-AB",    'Flat Head Cap Screw, #10-24 x 3/4", Alloy BO', 300, "EA", 0.11),
    (16, "FST-HXN-062-11-S18",       "FST-HXN-062-11-S18",       "Hex Nut, 5/8-11, 18-8 SS",       250, "EA",  0.51),
    (17, "FST-NYL-038-16-S18",       "FST-NYL-038-16-S18",       "Nyloc Lock Nut, 3/8-16, 18-8 SS", 500, "EA", 0.30),
    (18, "FST-FWS-038-USS-ZP",       "FST-FWS-038-USS-ZP",       'Flat Washer, 3/8" USS, Zinc',    2000, "EA",  0.04),
    (19, "FST-SLW-050-MS-ZP",        "FST-SLW-050-MS-ZP",        'Split Lock Washer, 1/2" Med, Zinc', 800, "EA", 0.05),
    (20, "FST-SMS-010-16-100-ZT3",   "FST-SMS-010-16-100-ZT3",   'Self-Drilling Tek, #10-16 x 1" HWH Zinc T3', 600, "EA", 0.07),
    (21, "HYD-MJN-06-04-STL",        "HYD-MJN-06-04-STL",        'JIC Male x NPT Male, -6 x 1/4" NPT, Steel',   75, "EA", 3.66),
    (22, "HYD-ORB-06-STL",           "HYD-ORB-06-STL",           "SAE ORB Male Straight, -6, Buna-N, Steel",   60, "EA", 4.92),
]
GT_PATT_PDF = GroundTruth(
    source_doc="data/pdf/patterson_po-28491.pdf",
    customer_id="CUST-00042",
    format="pdf",
    edge_case="clean",
    po_number="PO-28491",
    po_date="2026-04-18",
    required_date="2026-05-08",
    ship_to_code="PATT-ATL-02",
    payment_terms="Net 45",
    line_items=[
        _li(line_number=ln, customer_ref=br, canonical_sku=cs,
            description=desc, quantity=q, unit_of_measure=u, unit_price=p)
        for ln, br, cs, desc, q, u, p in PATT_PDF_LINES
    ],
    expected_routing="auto_process",
)

# 3.2 — Sterling portal quirky PDF (9 lines)
GT_STER_PDF = GroundTruth(
    source_doc="data/pdf/sterling_po-SMS-114832.pdf",
    customer_id="CUST-00267",
    format="pdf",
    edge_case="typos_label_variations",
    po_number="SMS-114832",
    po_date="2026-04-15",
    required_date="2026-05-06",
    ship_to_code="STER-BHM",
    payment_terms="Net 30",
    line_items=[
        _li(line_number=1, customer_ref="FST-HCS-050-13-200-G5Z", canonical_sku="FST-HCS-050-13-200-G5Z",
            description='Hex Cap Screw 1/2-13 x 2" Gr5 Zinc',
            quantity=400, unit_of_measure="EA", unit_price=0.34),
        _li(line_number=2, customer_ref="FST-HXN-050-13-G5Z", canonical_sku="FST-HXN-050-13-G5Z",
            description="Hex Nut 1/2-13 Gr5 Zinc",
            quantity=800, unit_of_measure="EA", unit_price=0.11),
        _li(line_number=3, customer_ref="FST-FWS-050-SAE-S18", canonical_sku="FST-FWS-050-SAE-S18",
            description='Flat Washer 1/2" SAE 18-8 SS',
            quantity=600, unit_of_measure="EA", unit_price=0.09),
        _li(line_number=4, customer_ref="FST-SHC-025-20-125-AB", canonical_sku="FST-SHC-025-20-125-AB",
            description='SHCS 1/4-20 x 1-1/4" Alloy Black Oxide',
            quantity=500, unit_of_measure="EA", unit_price=0.19),
        _li(line_number=5, customer_ref="FST-SMS-010-16-100-ZT3", canonical_sku="FST-SMS-010-16-100-ZT3",
            description='Self-Drill Tek #10-16 x 1" HWH Zn T3',
            quantity=1000, unit_of_measure="EA", unit_price=0.08),
        _li(line_number=6, customer_ref="PNM-PCS-025-025N-BR", canonical_sku="PNM-PCS-025-025N-BR",
            description="Push-Connect Str 1/4 Tube x 1/4 NPT Br",
            quantity=30, unit_of_measure="EA", unit_price=4.12),
        _li(line_number=7, customer_ref="PNM-QCI-025N-STL", canonical_sku="PNM-QCI-025N-STL",
            description="QC Coupler Ind 1/4 NPT F Steel",
            quantity=20, unit_of_measure="EA", unit_price=7.20),
        _li(line_number=8, customer_ref="PNM-TBE-PU-025-100FT-BL", canonical_sku="PNM-TBE-PU-025-100FT-BL",
            description='PU Tubing 1/4" OD 100 ft roll Blue',
            quantity=3, unit_of_measure="RL", unit_price=32.40),
        _li(line_number=9, customer_ref="PNM-MUF-025N-BRZ", canonical_sku="PNM-MUF-025N-BRZ",
            description="Muffler 1/4 NPT M Sintered Bronze",
            quantity=50, unit_of_measure="EA", unit_price=3.25),
    ],
    known_ambiguities=[
        "Label drift: 'Buyer Contact' (not Buyer), 'Supplier' (not Vendor), 'Delivery By' (not Required By), 'Unit' (not UOM)",
        "Column order swap: UOM precedes Qty",
        "Remit-to block routes to 3rd-party AP processor (Axia Financial Services) — must not be confused with bill-to",
        "Portal-generated from no-reply-po@sterlingmro.com — reply-to is different address",
    ],
    expected_routing="auto_process",
)

# 3.3 — Redline urgent 1-line PDF (conflict + ambiguity)
GT_REDLINE_PDF = GroundTruth(
    source_doc="data/pdf/redline_urgent_2026-04-19.pdf",
    customer_id="CUST-00492",
    format="pdf",
    edge_case="conflict_lead_time",
    po_number=None,  # Explicitly absent
    po_date="2026-04-19",
    required_date=None,  # "ASAP — RIG DOWN"
    ship_to_code="REDLINE-HOU",
    payment_terms="Net 30",
    line_items=[
        _li(line_number=1, customer_ref="HYD-QCA-06-STL", canonical_sku="HYD-QCA-06-STL",
            description='QD Coupler ISO 7241-A (AG), 3/8" Body, 3/8 NPT F, Chrome-Plated Steel',
            quantity=1, unit_of_measure="EA", unit_price=32.50,
            notes="Referenced against Rig RED-07 / Pump Assy SN 4X-H42-11798"),
    ],
    known_ambiguities=[
        "No PO number — derive from filename (redline_urgent_2026-04-19.pdf)",
        "Delivery is literal callout 'ASAP — RIG DOWN', not a structured date",
        "Rig serial reference (RED-07, SN 4X-H42-11798) in notes is customer context, not a line item",
    ],
    known_conflicts=[
        "HYD-QCA-06-STL has 4-day lead time per master; 'ASAP' (rig down) implies same/next day — not achievable without expedite sourcing",
    ],
    expected_routing="conflict_resolution",
)


# ============================================================================
# Phase 4 — EDI
# ============================================================================

# 4.1 — Patterson full X12 850
GT_PATT_EDI = GroundTruth(
    source_doc="data/edi/patterson_850_CLE_202604191435.edi",
    customer_id="CUST-00042",
    format="edi",
    edge_case="clean",
    po_number="PO-28492",
    po_date="2026-04-19",
    required_date="2026-05-08",
    ship_to_code="PATT-CLE-01",
    payment_terms=None,  # Not in 850; determined by trading partner agreement
    line_items=[
        _li(line_number=1, customer_ref="887712", canonical_sku="FST-HCS-050-13-200-G5Z",
            description="Hex Cap Screw 1/2-13 x 2in Gr 5 Zinc",
            quantity=500, unit_of_measure="EA", unit_price=0.30),
        _li(line_number=2, customer_ref="912055", canonical_sku="HYD-MJS-06-STL",
            description="JIC Male Straight -6 9/16-18 UNF Steel ZN",
            quantity=60, unit_of_measure="EA", unit_price=2.98),
        _li(line_number=3, customer_ref="912061", canonical_sku="HYD-MJE-06-STL",
            description="JIC Male Elbow 90 -6 9/16-18 UNF Forged Steel",
            quantity=24, unit_of_measure="EA", unit_price=7.66),
        _li(line_number=4, customer_ref="912118", canonical_sku="HYD-HSE-R2-06",
            description="Hydraulic Hose 100R2 -6 3/8in ID 2-Wire",
            quantity=300, unit_of_measure="FT", unit_price=3.37),
        _li(line_number=5, customer_ref="912402", canonical_sku="HYD-QCA-06-STL",
            description="QD Coupler ISO 7241-A AG 3/8in Body 3/8 NPT-F",
            quantity=10, unit_of_measure="EA", unit_price=28.15),
    ],
    known_ambiguities=[
        "Payment terms not present in X12 850 — derived from customer master trading partner agreement (Net 45)",
    ],
    expected_routing="auto_process",
)

# 4.2 — GLFP minimalist X12 850
GT_GLFP_EDI = GroundTruth(
    source_doc="data/edi/glfp_850_GRR_202604211002.edi",
    customer_id="CUST-00078",
    format="edi",
    edge_case="minimalist_envelope",
    po_number="PO-2026-05849",
    po_date="2026-04-21",
    required_date="2026-04-28",
    ship_to_code="GLFP-GRR-MAIN",
    payment_terms=None,
    line_items=[
        _li(line_number=1, customer_ref="GLFP-04821", canonical_sku="HYD-MJS-06-STL",
            description="JIC Male Straight -6 (from master)",
            quantity=36, unit_of_measure="EA", unit_price=None),
        _li(line_number=2, customer_ref="GLFP-04824", canonical_sku="HYD-MJE-06-STL",
            description="JIC Male Elbow 90 -6 (from master)",
            quantity=18, unit_of_measure="EA", unit_price=None),
        _li(line_number=3, customer_ref="GLFP-04831", canonical_sku="HYD-MJN-06-04-STL",
            description="JIC M x NPT M -6 x 1/4 (from master)",
            quantity=24, unit_of_measure="EA", unit_price=None),
        _li(line_number=4, customer_ref="GLFP-05110", canonical_sku="HYD-HSE-R2-06",
            description="Hose 100R2 -6 3/8 ID (from master)",
            quantity=200, unit_of_measure="FT", unit_price=None),
        _li(line_number=5, customer_ref="GLFP-05115", canonical_sku="HYD-HSE-R2-08",
            description="Hose 100R2 -8 1/2 ID (from master)",
            quantity=150, unit_of_measure="FT", unit_price=None),
        _li(line_number=6, customer_ref="GLFP-05220", canonical_sku="HYD-HFF-08-STL",
            description="Hose End Fitting JIC -8 (from master)",
            quantity=40, unit_of_measure="EA", unit_price=None),
        _li(line_number=7, customer_ref="GLFP-06010", canonical_sku="HYD-QCA-06-STL",
            description="QD Coupler ISO-A -6 (from master)",
            quantity=5, unit_of_measure="EA", unit_price=None),
        _li(line_number=8, customer_ref="GLFP-11200", canonical_sku="PNM-QCI-025N-STL",
            description="QC Coupler Industrial M-Style 1/4 NPT F (from master)",
            quantity=30, unit_of_measure="EA", unit_price=None),
    ],
    known_ambiguities=[
        "No PID segments — descriptions must be resolved from products.json via canonical SKU",
        "No PO104 unit prices — pricing must be looked up from GLFP contract (not explicitly in master)",
        "No N1*BT bill-to — derive from customer master primary billing address",
        "Payment terms not present in 850 — derive from customer master (Net 30)",
    ],
    expected_routing="auto_process",
)


# ============================================================================
# Phase 5 — Email
# ============================================================================

# 5.1 — Chuck vague references
GT_CHUCK_EML = GroundTruth(
    source_doc="data/email/chucks_hyd_reorder_2026-04-17.eml",
    customer_id="CUST-00418",
    format="email",
    edge_case="vague_references",
    po_number=None,
    po_date="2026-04-17",
    required_date=None,
    ship_to_code="CHUCKS-AKR",
    payment_terms="Net 15",
    line_items=[
        _li(line_number=1, customer_ref="the 3/8 R2 hose", canonical_sku="HYD-HSE-R2-06",
            description="Hydraulic Hose 100R2 -6 (3/8\" ID) — inferred from 3/8 R2 reference",
            quantity=50, unit_of_measure="FT", unit_price=None,
            notes="Fuzzy match: 'R2' + '3/8' uniquely identifies HYD-HSE-R2-06"),
        _li(line_number=2, customer_ref="those JIC -6 elbows", canonical_sku="HYD-MJE-06-STL",
            description="JIC Male Elbow 90° -6 — inferred from 'JIC -6 elbow'",
            quantity=6, unit_of_measure="EA", unit_price=None,
            notes="Fuzzy match: only JIC -6 elbow in master is HYD-MJE-06-STL (90° male)"),
        _li(line_number=3, customer_ref="the usual 3/8 QD couplers (ISO A style)", canonical_sku="HYD-QCA-06-STL",
            description="QD Coupler ISO 7241-A -6 Body x 3/8 NPT — inferred from 'ISO A 3/8 QD'",
            quantity=2, unit_of_measure="EA", unit_price=None,
            notes="Fuzzy match on 'ISO A' + '3/8' + 'QD coupler'"),
    ],
    known_ambiguities=[
        "No PO number — email is the authoritative record",
        "No explicit required-delivery date — customer expects normal lead time",
        "'Same as last time on price' — pricing requires prior-order lookup not present in document",
        "All 3 line items referenced by vague description — requires fuzzy match against product master",
    ],
    expected_routing="human_review",
)

# 5.2 — Reese semi-formal with typos
GT_REESE_EML = GroundTruth(
    source_doc="data/email/reese_reorder_2026-04-16.eml",
    customer_id="CUST-00153",
    format="email",
    edge_case="semi_formal_typos",
    po_number=None,
    po_date="2026-04-16",
    required_date="2026-05-12",
    ship_to_code="REESE-AKR",
    payment_terms="Net 30",
    line_items=[
        _li(line_number=1, customer_ref='Grade 5 hex cap screws, 1/2-13 x 2", zinc plated',
            canonical_sku="FST-HCS-050-13-200-G5Z",
            description="Hex Cap Screw 1/2-13 x 2 GR5 Zinc",
            quantity=200, unit_of_measure="EA", unit_price=None),
        _li(line_number=2, customer_ref="hex nuts same thread (1/2-13, grade 5 zinc)",
            canonical_sku="FST-HXN-050-13-G5Z",
            description="Hex Nut 1/2-13 GR5 Zinc",
            quantity=300, unit_of_measure="EA", unit_price=None),
        _li(line_number=3, customer_ref='flat washers 1/2" SAE, 18-8 stainless',
            canonical_sku="FST-FWS-050-SAE-S18",
            description="Flat Washer 1/2 SAE 18-8 SS",
            quantity=500, unit_of_measure="EA", unit_price=None,
            notes="Customer specifies stainless (not zinc) for pump mounts — material is critical"),
        _li(line_number=4, customer_ref='1/4" R1 hose', canonical_sku="HYD-HSE-R1-04",
            description="Hydraulic Hose 100R1 -4 (1/4 ID)",
            quantity=75, unit_of_measure="FT", unit_price=None),
        _li(line_number=5, customer_ref="JIC -6 male elbows, 90 deg", canonical_sku="HYD-MJE-06-STL",
            description="JIC Male Elbow 90 -6 Steel",
            quantity=12, unit_of_measure="EA", unit_price=None),
    ],
    known_ambiguities=[
        "No PO number — email is the authoritative record",
        "Body typos (Ive, dont, seperate, recieve) — content-tolerant parser required",
        "Split-shipment preference expressed in prose — must preserve as special instruction",
        "Invoice routing (office@reesecoindustrial.com) is different from sending address",
    ],
    expected_routing="auto_process",
)

# 5.3 — Birch Valley emergency conflict
GT_BIRCH_EML = GroundTruth(
    source_doc="data/email/birch_valley_emergency.eml",
    customer_id="CUST-00537",
    format="email",
    edge_case="conflict_lead_time",
    po_number=None,
    po_date="2026-04-20",
    required_date="2026-04-21",
    ship_to_code="BIRCH-SCE",
    payment_terms="COD",
    line_items=[
        _li(line_number=1, customer_ref="grade 8s, 3/8 x 1 yellow zinc",
            canonical_sku="FST-HCS-038-16-100-G8YZ",
            description="Hex Cap Screw 3/8-16 x 1 GR8 Yellow Zinc",
            quantity=100, unit_of_measure="EA", unit_price=None,
            notes="'Grade 8s' is farm vocabulary for Grade 8 hex cap screws"),
        _li(line_number=2, customer_ref="blue tubing, quarter inch",
            canonical_sku="PNM-TBE-PU-025-100FT-BL",
            description="Polyurethane Tubing 1/4 OD 100 ft Blue",
            quantity=1, unit_of_measure="RL", unit_price=None,
            notes="'Blue tubing' is farm vocabulary for the PU pneumatic tubing"),
    ],
    known_ambiguities=[
        "No PO number",
        "Farm-operator vocabulary ('grade 8s', 'blue tubing') requires domain-aware fuzzy match",
    ],
    known_conflicts=[
        "Requested delivery 2026-04-21 is 1 day after order date; FST-HCS-038-16-100-G8YZ has 2-day lead time per master",
        "Requested delivery 2026-04-21 is 1 day after order date; PNM-TBE-PU-025-100FT-BL has 3-day lead time per master",
    ],
    expected_routing="conflict_resolution",
)


ALL_GROUND_TRUTH: tuple[GroundTruth, ...] = (
    GT_GLFP_XLSX, GT_HAGAN_XLSX, GT_OVI_XLSX,
    GT_OVI_CSV, GT_PATT_CSV,
    GT_PATT_PDF, GT_STER_PDF, GT_REDLINE_PDF,
    GT_PATT_EDI, GT_GLFP_EDI,
    GT_CHUCK_EML, GT_REESE_EML, GT_BIRCH_EML,
)


def emit_all() -> list[Path]:
    written: list[Path] = []
    for gt in ALL_GROUND_TRUTH:
        source = ROOT / gt.source_doc
        expected = source.with_suffix(".expected.json")
        expected.write_text(
            gt.model_dump_json(indent=2, exclude_none=False),
            encoding="utf-8",
        )
        written.append(expected)
    return written


def main() -> None:
    paths = emit_all()
    for p in paths:
        print(f"wrote: {p.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
