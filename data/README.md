# Order Intake Agent — Synthetic Data Corpus

This directory holds the test corpus for the Order Intake Agent. Every document is paired with a `.expected.json` file conforming to `backend/models/ground_truth.GroundTruth`. The corpus exercises five input formats and eight distinct edge cases so that extraction quality can be measured against a fixed baseline across prompt/model variants.

## Layout

```
data/
├── masters/
│   ├── customers.json          10 trading partners, 3 segments, aliases + ship-to maps
│   └── products.json           35 canonical SKUs across fasteners / hydraulic / pneumatic
├── excel/                      3 .xlsx + 3 .expected.json
├── csv/                        2 .csv  + 2 .expected.json
├── pdf/                        3 .pdf  + 3 .expected.json
├── edi/                        2 .edi  + 2 .expected.json
└── email/                      3 .eml  + 3 .expected.json
```

The fictional seller of record is **Grafton-Reese Industrial Co.** (Twinsburg OH). All orders in this corpus are inbound to Grafton-Reese from the 10 customers in `customers.json`.

## Regeneration

Every source document is produced by a deterministic generator under `scripts/generators/`. Regeneration is byte-stable.

```bash
# Regenerate one format
uv run python -m scripts.generators.excel_glfp_weekly
uv run python -m scripts.generators.pdf_patterson_formal
uv run python -m scripts.generators.edi_glfp_850
# ... etc (one module per document)

# Regenerate all ground truth
uv run python -m scripts.emit_ground_truth

# Verify corpus integrity (pair check, schema, cross-references)
uv run python -m scripts.verify_corpus
```

## Document index

### Phase 1 — Excel

| File | Customer | PO# | Lines | Edge case |
|---|---|---|---|---|
| `excel/glfp_weekly_reorder_2026-04-14.xlsx` | GLFP (CUST-00078) | PO-2026-05847 | 12 | clean |
| `excel/hagan_reorder_2026-04-09.xlsx` | Hagan BPT (CUST-00201) | H-26189 | 7 | typos + label variations |
| `excel/ohio_valley_reorder_march_wk3.xlsx` | Ohio Valley (CUST-00294) | *(none)* | 5 | ambiguity (missing fields) |

### Phase 2 — CSV

| File | Customer | PO# | Lines | Edge case |
|---|---|---|---|---|
| `csv/ohio_valley_reorder_2026-04-08.csv` | Ohio Valley (CUST-00294) | 0452187 | 11 | clean (JDE-style export) |
| `csv/patterson_adhoc_reorder.csv` | Patterson (CUST-00042) | PO-28503 | 15 | quirky encoding (BOM, CRLF, quoted commas, summary rows, trailing blanks, per-line ship-to) |

### Phase 3 — PDF

| File | Customer | PO# | Lines | Edge case |
|---|---|---|---|---|
| `pdf/patterson_po-28491.pdf` | Patterson (CUST-00042) | PO-28491 | 22 | clean formal |
| `pdf/sterling_po-SMS-114832.pdf` | Sterling MRO (CUST-00267) | SMS-114832 | 9 | typos + label variations (portal-generated) |
| `pdf/redline_urgent_2026-04-19.pdf` | Redline (CUST-00492) | *(none)* | 1 | conflict (lead-time vs "ASAP — RIG DOWN") |

### Phase 4 — X12 850

| File | Customer | PO# | Lines | Edge case |
|---|---|---|---|---|
| `edi/patterson_850_CLE_202604191435.edi` | Patterson (CUST-00042) | PO-28492 | 5 | clean (full envelope) |
| `edi/glfp_850_GRR_202604211002.edi` | GLFP (CUST-00078) | PO-2026-05849 | 8 | minimalist envelope (no PID, no PO104 prices, no N1*BT) |

### Phase 5 — Email body-only

| File | Customer | PO# | Lines | Edge case |
|---|---|---|---|---|
| `email/chucks_hyd_reorder_2026-04-17.eml` | Chuck's Hyd (CUST-00418) | *(none)* | 3 | vague references + "same as last time" |
| `email/reese_reorder_2026-04-16.eml` | Reese & Co (CUST-00153) | *(none)* | 5 | semi-formal with typos |
| `email/birch_valley_emergency.eml` | Birch Valley (CUST-00537) | *(none)* | 2 | conflict (lead-time) + farm vocabulary |

## Edge-case matrix

| Edge case | Count | Documents |
|---|---|---|
| `clean` | 4 | glfp.xlsx, ohio_valley.csv, patterson.pdf, patterson.edi |
| `typos_label_variations` | 3 | hagan.xlsx, sterling.pdf *(plus reese.eml as `semi_formal_typos` variant)* |
| `ambiguity_missing_fields` | 1 | ohio_valley.xlsx |
| `quirky_encoding` | 1 | patterson.csv |
| `minimalist_envelope` | 1 | glfp.edi |
| `vague_references` | 1 | chucks.eml |
| `semi_formal_typos` | 1 | reese.eml |
| `conflict_lead_time` | 2 | redline.pdf, birch_valley.eml |

## Expected-routing table

| Routing decision | Count | Documents |
|---|---|---|
| `auto_process` | 9 | glfp.xlsx, hagan.xlsx, ohio_valley.csv, patterson.csv, patterson.pdf, sterling.pdf, patterson.edi, glfp.edi, reese.eml |
| `human_review` | 2 | ohio_valley.xlsx, chucks.eml |
| `conflict_resolution` | 2 | redline.pdf, birch_valley.eml |

Routing rationale:

- **`auto_process`** — every field is either present in the source or trivially derivable from the customer master. Structural quirks (CSV BOM, typo'd headers, minimalist EDI) are handled by the extractor without human input.
- **`human_review`** — at least one load-bearing field is absent or ambiguous enough that an extractor shouldn't guess. Ohio Valley's xlsx has no PO number, prices, or structured date; Chuck's email says "same as last time on price" and references items by vague description.
- **`conflict_resolution`** — the order is internally well-formed but conflicts with master data. Redline asks for a part with 4-day lead time as "ASAP rig down"; Birch Valley asks for next-day delivery on items with 2-3 day lead times.

## Schema reference

Ground-truth Pydantic models: `backend/models/ground_truth.py`.

- `GroundTruth` — one annotation per source document; carries `source_doc`, `customer_id`, `format`, `edge_case`, `po_number`, dates, `ship_to_code`, `payment_terms`, `line_items`, `known_ambiguities`, `known_conflicts`, `expected_routing`.
- `GroundTruthLineItem` — per-line expected values with `customer_ref` (what the source wrote), `canonical_sku` (resolved from master, or `null` if unresolvable), `description`, `quantity`, `unit_of_measure`, `unit_price`, `notes`.

The agent-facing extraction schema is separate and lives at `backend/models/parsed_document.py` (`ExtractedOrder`/`OrderLineItem`). Ground truth is explicitly *not* what the LLM emits — it's what an evaluator compares against.

## Integrity guarantees

`scripts/verify_corpus.py` asserts on every run:

1. Every source file has a sibling `.expected.json`; every `.expected.json` has its source.
2. Every `.expected.json` round-trips through Pydantic without loss.
3. Every `customer_id` in ground truth exists in `customers.json`.
4. Every non-null `canonical_sku` exists in `products.json`.
5. Every header-level `ship_to_code` matches one of the customer's declared ship-to location codes.
6. When a customer has `sku_aliases` and the ground-truth `customer_ref` matches an alias key, the `canonical_sku` equals the alias target.

Passing this check is a prerequisite for running any extraction evaluation against the corpus.
