---
type: sprint-status
topic: "Order Intake Agent — Status vs Glacis Spec"
sprint: 1
parent: "Order-Intake-Sprint-Overview.md"
date: 2026-04-20
last_updated: 2026-04-22 (Track P write side landed: backend/persistence/ — OrderStore + ExceptionStore + IntakeCoordinator with full snapshot OrderRecord, single-doc ExceptionRecord lifecycle, source_message_id idempotency, find_pending_clarify composite index. 35 new unit + 10 new integration tests; full suite at 235 passed)
tags:
  - sprint
  - status
  - gap-analysis
---

# Order Intake Agent — Status vs Glacis Spec

Snapshot taken 2026-04-20 at end of planning session. Maps every stage of the Glacis reference architecture (`research/Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Overview.md`) to current build state and remaining work.

## Status table

| Glacis stage | What spec says | What we have | What's left |
|---|---|---|---|
| **1. Signal ingestion** | Gmail watch → Pub/Sub → attachment download | Fixtures ✓ + 4/4 format wrappers (PDF/CSV/XLSX/EDI) ✓ + clarify-reply fixture ✓ + `backend/ingestion/` (`EmailEnvelope` + `parse_eml`) ✓ + `scripts/inject_email.py` CLI ✓ | Wrap remaining 6 non-`.eml` fixtures (iterative, non-blocking). Gmail push deferred to later sprint. |
| **2a. Classification** | LLM classifier (intent) + rules (format) | `backend/tools/document_classifier/` — LlamaClassify intent + deterministic format ✓ | Nothing. Done. |
| **2b. Extraction** | Gemini multimodal → structured JSON | `backend/tools/document_parser/` — LlamaExtract → `ParsedDocument` ✓ | Nothing. Done. |
| **2c. Validation** | SKU + price + quantity + credit + inventory + delivery + duplicate | `backend/tools/order_validator/` ✓ — `OrderValidator` orchestrator + 6 tools (master_data_repo, sku_matcher, customer_resolver, price_check, qty_check, firestore_client) + `scorer` + `router`. 180 unit tests green. Credit/inventory/delivery/duplicate dropped per cut-list. | Nothing. Done. |
| **2d. Enrichment (item matching)** | Exact → fuzzy → embedding | 3-tier ladder in `sku_matcher.py` ✓ — Tier 1 exact + alias, Tier 2 rapidfuzz `token_set_ratio` over `short_description`, Tier 3 embedding stub falls through cleanly. | Nothing. Tier 3 fills in when `feat/embeddings` lands. |
| **3. Decision layer** | Auto ≥0.95 / Clarify 0.80–0.95 / Escalate <0.80 | `scorer.aggregate` + `router.decide` ✓ — thresholds + `RoutingDecision` enum live in `backend/models/validation_result.py`. | Nothing. Done. |
| **4a. ERP read/write** | Firestore read + write | Emulator live; `products` (35) + `customers` (10) + `meta/master_data` seeded ✓. **Read side** in validator package (`MasterDataRepo` over typed records) ✓. **Write side** in `backend/persistence/` — `FirestoreOrderStore` + `FirestoreExceptionStore` with `source_message_id` idempotency; `IntakeCoordinator` routes `RoutingDecision` → store with full customer/product snapshots; composite index for `find_pending_clarify(thread_id)` shipped ✓. | Nothing. Done. |
| **4b. Clarify email** | Gemini-generated email asking for missing fields | — | Part of Track A (router stage). |
| **4c. Human dashboard** | Firestore real-time + approve/reject/edit | — | **Track D** — read-only list + exception view. Approve/reject deferred. |
| **Orchestration** | ADK SequentialAgent wiring stages | Stub `backend/my_agent/agent.py` ⚠ | **Track A**: replace stub with real SequentialAgent. |
| **5. Learning loop** | Corrections update SOP rules in Firestore | — | Deferred entirely per cut-list. |
| **Eval / quality gate** | (implicit in spec) | — | **Track E**: `adk eval` + 3 golden-file evalsets. |
| **Deploy** | Cloud Run + Firebase Hosting | — | `adk deploy cloud_run` for agent (inside Track A). Dashboard deploy TBD. |
| **Demo** | 2-min video, 3+ scenarios | Fixtures exist ✓ | **Track Demo**: `scripts/run_demo.py` runs 3 fixtures. |

## One-line summary

**Read + judgment + persist are complete** — classify + extract + typed output + master data + realistic fixtures + envelope contract + inject CLI + Firestore emulator with seeded master data + typed async read repo + `OrderValidator` (3-tier SKU matcher, customer resolver, price tolerance, qty sanity, scorer, threshold-based router) + `FirestoreOrderStore` / `FirestoreExceptionStore` + `IntakeCoordinator` end-to-end against the emulator.

**The "surface" half is still open** — orchestrate the agent, generate clarify emails, surface to a dashboard, eval, demo.

That's **~3.5 worktrees** left (read side of `feat/persistence` landed 2026-04-21; Track V validator landed 2026-04-21; **Track P write side + coordinator landed 2026-04-22**; agent orchestration, clarify generation, dashboard, eval, demo remain). Roughly **~80% of total code lines are done**. The next critical-path branch is `feat/agent-orchestration` — wires `EmailEnvelope → ParsedDocument → IntakeCoordinator.process()` and replaces the `backend/my_agent/agent.py` stub with a real `SequentialAgent`. `feat/eval` can land in parallel.

## Built-vs-missing inventory

### Built (do not rebuild)

```
data/masters/{products,customers}.json                                  ✓
data/{csv,edi,email,excel,pdf}/                                         ✓ fixtures
data/pdf/redline_urgent_2026-04-19.{body.txt,wrapper.eml}               ✓ PDF wrapper (2026-04-20)
data/pdf/patterson_po-28491.{body.txt,wrapper.eml}                      ✓ PDF wrapper (2026-04-21)
data/pdf/sterling_po-SMS-114832.{body.txt,wrapper.eml}                  ✓ PDF wrapper (2026-04-21)
data/csv/ohio_valley_reorder_2026-04-08.{body.txt,wrapper.eml}          ✓ CSV wrapper (2026-04-20)
data/csv/patterson_adhoc_reorder.{body.txt,wrapper.eml}                 ✓ CSV wrapper (2026-04-21, octet-stream for BOM)
data/excel/hagan_reorder_2026-04-09.{body.txt,wrapper.eml}              ✓ XLSX wrapper (2026-04-20)
data/excel/glfp_weekly_reorder_2026-04-14.{body.txt,wrapper.eml}        ✓ XLSX wrapper (2026-04-21)
data/excel/ohio_valley_reorder_march_wk3.{body.txt,wrapper.eml}         ✓ XLSX wrapper (2026-04-21)
data/edi/glfp_850_GRR_202604211002.{body.txt,wrapper.eml}               ✓ EDI wrapper (2026-04-20)
data/edi/patterson_850_CLE_202604191435.{body.txt,wrapper.eml}          ✓ EDI wrapper (2026-04-21)
data/email/birch_valley_clarify_reply.{body.txt,eml}                    ✓ clarify reply-pair (2026-04-20)
backend/models/classified_document.py                                   ✓
backend/models/parsed_document.py                                       ✓
backend/models/ground_truth.py                                          ✓
backend/ingestion/email_envelope.py                                     ✓ EmailEnvelope + EmailAttachment (2026-04-20)
backend/ingestion/eml_parser.py                                         ✓ parse_eml() + EmlParseError (2026-04-20)
backend/prompts/{document_classifier,document_parser}.py                ✓
backend/tools/document_classifier/                                      ✓
backend/tools/document_parser/ (legacy/)                                ✓
backend/exceptions.py                                                   ✓
backend/utils/ (logging)                                                ✓
scripts/classify_file.py, classify_folder.py                            ✓
scripts/load_master_data.py                                             ✓ idempotent Firestore seeder
scripts/inject_email.py                                                 ✓ envelope CLI (2026-04-20)
scripts/scaffold_wrapper_eml.py                                         ✓ fixture-authoring helper (2026-04-20)
firebase.json, .firebaserc, firebase/*.{rules,indexes.json}             ✓ emulator config
google-cloud-firestore 2.27.0 (pyproject.toml)                          ✓
research/Firebase-Init-Decisions.md                                     ✓ decision record
backend/tools/order_validator/__init__.py                               ✓ public surface — re-exports OrderValidator, contracts, repo, records
backend/tools/order_validator/validator.py                              ✓ OrderValidator orchestrator (2026-04-21)
backend/tools/order_validator/scorer.py                                 ✓ aggregate() — mean confidence + check-failure penalty
backend/tools/order_validator/router.py                                 ✓ decide() — threshold-based RoutingDecision
backend/tools/order_validator/tools/__init__.py                         ✓ tool collection re-exports
backend/tools/order_validator/tools/master_data_repo.py                 ✓ MasterDataRepo — async read-only master-data repo (moved from backend/data)
backend/tools/order_validator/tools/firestore_client.py                 ✓ async Firestore client factory (moved from backend/data)
backend/tools/order_validator/tools/sku_matcher.py                      ✓ 3-tier ladder: exact (incl. alias) → fuzzy → embedding stub
backend/tools/order_validator/tools/customer_resolver.py                ✓ wraps repo.find_customer_by_name
backend/tools/order_validator/tools/price_check.py                      ✓ pure function: ±10% tolerance band, permissive on missing quote
backend/tools/order_validator/tools/qty_check.py                        ✓ pure function: presence/sign + UoM + min_order (base UoM only)
backend/models/validation_result.py                                     ✓ ValidationResult, LineItemValidation, RoutingDecision, AUTO/CLARIFY thresholds
backend/models/master_records.py                                        ✓ ProductRecord, CustomerRecord, AddressRecord, ShipToLocation,
                                                                            ContactRecord, MetaRecord, EmbeddingMatch (5286322)
tests/unit/conftest.py                                                  ✓ FakeAsyncClient + seeded_repo / empty_repo fixtures (shared)
tests/unit/test_document_classifier.py                                  ✓
tests/unit/test_eml_parser.py                                           ✓ 26 tests over all .eml fixtures (2026-04-20)
tests/unit/test_master_data_repo.py                                     ✓ in-memory fake — behavioural contract tests (renamed)
tests/unit/test_sku_matcher.py                                          ✓ 9 tests over the 3-tier ladder
tests/unit/test_customer_resolver.py                                    ✓ 7 tests incl. dba match + alias preservation
tests/unit/test_price_check.py                                          ✓ 8 tests covering tolerance edges
tests/unit/test_qty_check.py                                            ✓ 10 tests incl. alt-UoM min_order skip
tests/unit/test_scorer.py                                               ✓ 9 tests covering mean + penalty math
tests/unit/test_router.py                                               ✓ 8 tests on threshold edges (0.7999/0.8000/0.9499/0.9500)
tests/unit/test_validator.py                                            ✓ 8 end-to-end scenarios against seeded fake
tests/integration/test_master_data_repo_emulator.py                     ✓ emulator-backed parity tests (renamed)
rapidfuzz>=3.9, pytest-asyncio>=0.23, asyncio_mode="auto",              ✓ pytest config incl. firestore_emulator marker (06916a6)
backend/models/order_record.py                                          ✓ contracts commit (3202120) — OrderRecord + CustomerSnapshot + ProductSnapshot + OrderLine + OrderStatus
backend/models/exception_record.py                                      ✓ contracts commit — ExceptionRecord + ExceptionStatus (single-doc lifecycle: PENDING_CLARIFY → AWAITING_REVIEW → RESOLVED)
backend/persistence/__init__.py                                         ✓ public surface — re-exports stores, coordinator, ProcessResult
backend/persistence/base.py                                             ✓ OrderStore + ExceptionStore Protocols (find_pending_clarify, update_with_reply on exception side)
backend/persistence/orders_store.py                                     ✓ FirestoreOrderStore — optimistic create + AlreadyExists swallow for source_message_id idempotency, SERVER_TIMESTAMP on created_at
backend/persistence/exceptions_store.py                                 ✓ FirestoreExceptionStore — same idempotency; find_pending_clarify via composite index; update_with_reply enforces PENDING_CLARIFY status guard
backend/persistence/coordinator.py                                      ✓ IntakeCoordinator — routes RoutingDecision → store; OrderRecord built from validation.customer (already-resolved) + repo.get_product per line; ExceptionRecord auto-builds reason from concatenated LineItemValidation.notes
firebase/firestore.indexes.json                                         ✓ composite index on exceptions: (thread_id ASC, status ASC, created_at DESC)
tests/unit/conftest.py                                                  ✓ FakeAsyncClient extended with create/set/update + SERVER_TIMESTAMP resolution + where/order_by/limit query support (additive — Track V tests unaffected)
tests/unit/test_order_store.py                                          ✓ 10 tests: write, server-timestamp, idempotency, get None/hit, schema_version, snapshot round-trip, status enum, validation guards, multi-doc independence
tests/unit/test_exception_store.py                                      ✓ 14 tests: save/get, idempotency, find_pending_clarify (4 query-shape tests), update_with_reply lifecycle (4 tests), full ParsedDocument + ValidationResult round-trips
tests/unit/test_coordinator.py                                          ✓ 11 tests: AUTO_APPROVE / CLARIFY / ESCALATE routing, dedupe, snapshots, agent_version, reason concatenation, thread_id fallback
tests/integration/test_order_store_emulator.py                          ✓ 4 emulator tests: round-trip, idempotency under collision, None on miss, distinct-doc independence
tests/integration/test_exception_store_emulator.py                      ✓ 4 emulator tests: round-trip, find_pending_clarify (composite index), update_with_reply, full lifecycle
tests/integration/test_coordinator_emulator.py                          ✓ 2 emulator tests: end-to-end AUTO_APPROVE + CLARIFY against real validator + seeded master data
```

### Missing (this sprint's work, mapped to branches)

```
backend/persistence/embeddings_index.py    → feat/embeddings (unblocks SKU tier-3; find_product_by_embedding is stubbed today)
backend/my_agent/agent.py (rewrite)        → feat/agent-orchestration
backend/my_agent/stages/                   → feat/agent-orchestration
scripts/run_demo.py                        → feat/demo-script
scripts/run_eval.py                        → feat/eval
tests/eval/*.evalset.json                  → feat/eval
frontend/                                  → feat/dashboard
```

**Fixture wrappers complete (2026-04-21):** all 10 non-`.eml` fixtures now have `{body.txt,wrapper.eml}` pairs. `tests/unit/test_eml_parser.py` parametrizes over every `.eml` under `data/` and runs 44 checks (envelope parse + attachment byte round-trip) — all green. Patterson adhoc CSV uses `--attachment-mime application/octet-stream` because its UTF-8 BOM (a known_ambiguity) would be mangled by the default `text/csv` re-encoding path; scaffold script grew an `--attachment-mime` flag to support this.

## What to build first

With Track V and Track P (write side + coordinator) landed, the critical path moves to:

1. **`feat/agent-orchestration`** — replace the `backend/my_agent/agent.py` stub with a real ADK `SequentialAgent` that wires `EmailEnvelope → ParsedDocument → IntakeCoordinator.process()`. Coordinator already exposes the right surface (`process(parsed, envelope) → ProcessResult`); orchestration just owns the iteration over `parsed_doc.sub_documents`, the per-stage callbacks, and the clarify-email send-side (which writes `clarify_message_id` onto the existing exception via a follow-up update path on `ExceptionStore`).
2. **`feat/eval`** — `tests/eval/*.evalset.json` golden files authored alongside the 3 demo scenarios. The coordinator now returns deterministic `ProcessResult` objects against the seeded emulator — easy to snapshot.

**Parallel but deferrable:**
- `feat/embeddings` — seed Gemini `text-embedding-004` vectors for the 35 products into Firestore + replace the stub at `backend/tools/order_validator/tools/master_data_repo.py:find_product_by_embedding`. Not on the critical path; tier-1/2 matching demos fine without it.
- **Memory-as-a-Service track (post-sprint)** — proper long-term agent memory via `VertexAiMemoryBankService` behind a thin service interface, fed by completed orders + human corrections (the Glacis "learning loop"). Currently using `InMemorySessionService` as a placeholder; nothing in `backend/persistence/` changes when MaaS lands — they solve different problems (ledger vs. RAG retrieval).

~~`research/adk-session-memory`~~ — **collapsed 2026-04-20**: decided to use Firestore directly as the ERP substitute (spec-accurate) rather than ADK Sessions/Memory. See `Firebase-Init-Decisions.md` for the full rationale.

Once those two merge to master, everything else cascades per the dependency graph in [Order-Intake-Sprint-Worktrees](Order-Intake-Sprint-Worktrees.md).

## Before forking

All cross-worktree coordination types are now on master. Future branches import without further coordination:

- `EmailEnvelope`, `EmailAttachment` → `backend/ingestion/email_envelope.py` **✓ on master (2026-04-20)**
- `ValidationResult`, `RoutingDecision`, `LineItemValidation` → `backend/models/validation_result.py` **✓ on master (2026-04-21)**
- `OrderRecord`, `CustomerSnapshot`, `ProductSnapshot`, `OrderLine`, `OrderStatus` → `backend/models/order_record.py` **✓ on master (3202120, 2026-04-22)**
- `ExceptionRecord`, `ExceptionStatus` → `backend/models/exception_record.py` **✓ on master (3202120, 2026-04-22)**
- `OrderStore`, `ExceptionStore` Protocols → `backend/persistence/base.py` **✓ on master (3202120, 2026-04-22)**
- `IntakeCoordinator`, `ProcessResult` → `backend/persistence/coordinator.py` **✓ on feat/persistence-writes (b63b94d) — lands on master via merge**

No more coordination commits needed. `feat/agent-orchestration` and `feat/eval` can fork freely.

## Connections

- [Order-Intake-Sprint-Overview](Order-Intake-Sprint-Overview.md)
- [Order-Intake-Sprint-Worktrees](Order-Intake-Sprint-Worktrees.md) — full dependency graph and per-track contracts
- [Order-Intake-Sprint-Theory-vs-Practice](Order-Intake-Sprint-Theory-vs-Practice.md) — why "what we have" differs from "what spec says" in rows 2a, 2b
- [Order-Intake-Sprint-Verification](Order-Intake-Sprint-Verification.md) — what "done" looks like per track
- [Firebase-Init-Decisions](Firebase-Init-Decisions.md) — Firestore-only stack, emulator-first, `demo-order-intake-local` project ID
- `research/Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Overview.md` — the spec this status measures against
