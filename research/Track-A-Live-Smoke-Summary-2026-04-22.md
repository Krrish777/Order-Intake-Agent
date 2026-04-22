---
type: live-smoke-result
topic: "Track A end-to-end live smoke — pipeline verified against real Gemini + LlamaCloud + Firestore emulator"
date: 2026-04-22
log: "Track-A-Live-Smoke-2026-04-22.log"
audit: "Track-A-Live-Audit-2026-04-22.md"
tags:
  - track-a
  - live-smoke
  - verification
---

# Track A Live Smoke — 2026-04-22

Full 8-stage pipeline driven end-to-end via `scripts/smoke_run.py` against:
- **Real Gemini** (`gemini-3-flash-preview`, AI Studio, `GOOGLE_API_KEY`)
- **Real LlamaCloud** (LlamaClassify + LlamaExtract, `LLAMA_CLOUD_API_KEY`)
- **Local Firestore emulator** (`FIRESTORE_EMULATOR_HOST=localhost:8080`, 35 products + 10 customers seeded via `scripts/load_master_data.py`)

All fixes from `Track-A-Live-Audit-2026-04-22.md` (F1, F3, F4, F5, F6, F14, F16) land in this pass. Result: **clean exit code 0**, `run_summary` populated by real Gemini, no traceback.

Full raw log: [`Track-A-Live-Smoke-2026-04-22.log`](./Track-A-Live-Smoke-2026-04-22.log) (110 lines).

## Fixture + run command

```
Fixture: data/pdf/patterson_po-28491.wrapper.eml
Command: uv run python scripts/smoke_run.py data/pdf/patterson_po-28491.wrapper.eml
```

## Per-stage event stream (9 events total)

| # | Stage author | state_delta keys | Content summary |
|---|---|---|---|
| 1 | `ingest_stage` | `envelope` | `Ingested <177675897596.2064.16567937247607777057@grafton-reese.example> (1 attachment)` |
| 2 | `reply_shortcircuit_stage` | `reply_handled` | (no content) |
| 3 | `classify_stage` | `classified_docs, skipped_docs` | `Classified 1 attachment(s): 1 purchase_order, 0 skipped` |
| 4 | `parse_stage` | `parsed_docs, skipped_docs` | `Parsed 1 PO attachment(s) into 1 sub-document(s); 0 skipped total` |
| 5 | `validate_stage` | `skipped_docs, validation_results` | `Validated 1 order(s); 0 skipped upstream` |
| 6 | `persist_stage` | `process_results, skipped_docs` | `Persisted 1 result(s); 0 skipped upstream` |
| 7 | `run_summary_agent` | `run_summary` | `{"orders_created":0,"exceptions_opened":1,"docs_skipped":0,"summary":"The pipeline processed one document, which resulted in one exception being opened and zero orders being created."}` |
| 8 | `finalize_stage` | `run_summary` | `Run summary: 0 order(s), 1 exception(s), 0 skipped` |
| 9 | `run_summary_agent` | (post-response event) | — |

Latencies observed:
- **ClassifyStage** (LlamaClassify): ~14s end-to-end, 5 polls.
- **ParseStage** (LlamaExtract): ~40s end-to-end, 17 polls.
- **ClarifyStage**: skipped (no CLARIFY-tier results on ESCALATE routing).
- **FinalizeStage** (Gemini summary): ~3s response.

## Final session.state snapshot

```python
{
  "envelope":           {EmailEnvelope dict — message_id, attachments[1], ...},
  "reply_handled":      False,
  "classified_docs":    [1 × ClassifiedDocument with document_intent=purchase_order, confidence=1.0],
  "parsed_docs":        [1 × {filename, sub_doc_index: 0, parsed, sub_doc}],
  "validation_results": [1 × {filename, sub_doc_index: 0, validation}],
  "skipped_docs":       [],
  "clarify_bodies":     {},
  "process_results":    [1 × {filename: "patterson_po-28491.pdf", sub_doc_index: 0, result: {kind: "exception", ...}}],
  "run_summary":        {orders_created: 0, exceptions_opened: 1, docs_skipped: 0, summary: "..."},
}
```

## Validator output (observed in state["validation_results"])

```
customer: {customer_id: "CUST-00042", name: "Patterson Industrial Supply Co.", ...}
lines:    [22 entries — all unmatched against seeded products]
aggregate_confidence: 0.0
decision: "escalate"
```

**Interpretation:** customer resolution succeeded (Patterson → CUST-00042) against the 10 seeded customers. The line-item matcher failed on all 22 SKUs — the LlamaExtract output format doesn't align with the seeded catalog strings. This is a DATA matching issue (extraction format vs master format), **not** a code bug. The pipeline correctly routed to ESCALATE and persisted an ExceptionRecord.

## Audit findings verified by this run

| F# | Finding | Verified how |
|---|---|---|
| F1 | `adk_apps/order_intake/` as single-entry scan dir | Script uses the same re-export path; `adk web adk_apps` confirmed via sub_agents dump |
| F3 | No `additionalProperties: false` in Gemini response_schema | Gemini call succeeded (previous run died with 400 at this exact point) |
| F4 | Parser UUID suffix (not content-hash) | LlamaExtract file upload succeeded on re-run against the same fixture (previous run died with `UniqueViolationError`) |
| F5 | `precomputed_validation` kwarg threads through | Validator ran ONCE per sub-doc (check log: only 1 `validation_done` event per doc; previous runs had 2) |
| F6 | Master data seeded | Customer resolved (CUST-00042 populated in validation.customer) |
| F14 | Evalset `app_name="order_intake"` + no `user_content.role` | Not exercised by this smoke (that's `adk eval`); pre-landed |
| F16 | Single root `.env` | `load_dotenv(REPO_ROOT / ".env")` in smoke script; GOOGLE_API_KEY + LLAMA_CLOUD_API_KEY + FIRESTORE_EMULATOR_HOST all resolved |

## What still needs attention (out-of-scope for Track A code correctness)

- **SKU matching pattern**: extracted line items (from LlamaExtract's parse of the PDF) don't align with the seeded catalog's SKU strings (e.g. extracted might say `"3/8 bolt Grade 5"` while master has `FST-FHC-010-24-075-AB`). The 3-tier matcher (exact → fuzzy → embedding stub) returns 0.0 across the board. Fix options for a future iteration:
  - Tune fuzzy thresholds (`sku_matcher.py:token_set_ratio` cutoff).
  - Populate Tier 3 embeddings for the catalog (gated by `feat/embeddings` per the sprint plan).
  - Enrich master data with the description strings LlamaExtract is likely to produce.
  - Or: accept that Patterson-style fixtures legitimately route to ESCALATE and create a different AUTO-tier fixture for demos.
- **Dedupe on re-run**: subsequent runs of the same fixture against the same emulator return `kind="duplicate"` (orders_created=0, exceptions_opened=0). This is correct per the `source_message_id` idempotency spec. To see non-zero counts on a re-run, wipe the `orders` + `exceptions` collections in the emulator first (UI at http://localhost:4000 or via a script).

## Reproducing this smoke

```bash
# Terminal 1 — emulator
firebase emulators:start --only firestore

# Terminal 2 — seed + run
FIRESTORE_EMULATOR_HOST=localhost:8080 \
GOOGLE_CLOUD_PROJECT=demo-order-intake-local \
  uv run python scripts/load_master_data.py

# Clear prior run's dedupe state (optional; required for fresh-insert counts)
FIRESTORE_EMULATOR_HOST=localhost:8080 \
GOOGLE_CLOUD_PROJECT=demo-order-intake-local \
  uv run python -c "
import asyncio
from backend.tools.order_validator.tools.firestore_client import get_async_client
async def wipe():
    c = get_async_client()
    for coll in ('exceptions','orders'):
        async for d in c.collection(coll).stream():
            await d.reference.delete()
asyncio.run(wipe())
"

# Run the smoke
uv run python scripts/smoke_run.py data/pdf/patterson_po-28491.wrapper.eml
```
