---
type: sprint-status
topic: "Order Intake Agent — Status vs Glacis Spec"
sprint: 1
parent: "Order-Intake-Sprint-Overview.md"
date: 2026-04-20
last_updated: 2026-04-22 (Track A in flight — 8 commits on master: contracts schema bump / output-schema models / LlmAgent factories / IngestStage / ReplyShortCircuitStage / ClassifyStage / EmailAttachment bytes round-trip fix / ParseStage (BaseAgent #4/8, flattens ParsedDocument.sub_documents into per-sub-doc entries, wraps LlamaExtract in asyncio.to_thread, establishes the APPEND-not-overwrite contract for `skipped_docs` that Stages 4e-4h inherit). +37 new unit tests; suite at 256 passed. ADK/test findings: (a) PrivateAttr is the dep-injection pattern for Protocol-typed services; (b) `asyncio.to_thread` cleanly wraps sync poll-based tools; (c) Pydantic `Base64Bytes` rejects raw binary — symmetric round-trip needs a custom mode="before" validator; (d) `skipped_docs` lives across stages as a cumulative audit trail, so each stage must `list(state.get("skipped_docs", []))` defensive-copy + extend, never assign a fresh list.)
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
| **4b. Clarify email** | Gemini-generated email asking for missing fields | `ClarifyEmail(subject, body)` output schema + `build_clarify_email_agent()` factory (gemini-3-flash-preview) + state-injected `clarify_email.py` prompt template ✓ (Track A Steps 2+3). `clarify_body` field on `ExceptionRecord`; `IntakeCoordinator.process(..., clarify_body=...)` persists inline on PENDING_CLARIFY, drops on ESCALATE ✓ (Track A Step 1). | ClarifyStage (Track A Step 4f) wires the child LlmAgent into the SequentialAgent. No Gmail send — body-generation only per plan. |
| **4c. Human dashboard** | Firestore real-time + approve/reject/edit | — | **Track D** — read-only list + exception view. Approve/reject deferred. |
| **Orchestration** | ADK SequentialAgent wiring stages | **Track A in flight on master** (no worktree — per-plan decision): Steps 1-3 ✓ (contracts, models, prompts, LlmAgent factories) · Step 4a `IngestStage` ✓ · Step 4b `ReplyShortCircuitStage` ✓ · Step 4c `ClassifyStage` ✓ · Step 4d `ParseStage` ✓ (BaseAgent #4: flattens `ParsedDocument.sub_documents` into per-sub-doc `{filename, sub_doc_index, parsed, sub_doc}` entries; `asyncio.to_thread(parse_fn, ...)` around sync LlamaExtract; zero-subdocs→`skipped_docs` entry; **establishes the `skipped_docs` APPEND-not-overwrite contract** via `list(state.get("skipped_docs", []))` defensive copy) · Bytes round-trip fix ✓. Stub `backend/my_agent/agent.py` still present ⚠ (Step 5 replaces). | Steps 4e-4h: ValidateStage, ClarifyStage, PersistStage, FinalizeStage (4 remaining BaseAgent stages — all reuse PrivateAttr + sequential-by-design + append-skipped patterns). Step 4e is the planned pull-up trigger for the duplicated `_make_ctx` test helper (copy #5). Step 5 assemble `SequentialAgent`. Step 6 emulator integration test. Step 7 smoke evalset (4 cases). Step 8 `adk web` manual smoke. Step 9 close-out status update. |
| **5. Learning loop** | Corrections update SOP rules in Firestore | — | Deferred entirely per cut-list. |
| **Eval / quality gate** | (implicit in spec) | — | **Track E**: `adk eval` + 3 golden-file evalsets. |
| **Deploy** | Cloud Run + Firebase Hosting | — | `adk deploy cloud_run` for agent (inside Track A). Dashboard deploy TBD. |
| **Demo** | 2-min video, 3+ scenarios | Fixtures exist ✓ | **Track Demo**: `scripts/run_demo.py` runs 3 fixtures. |

## One-line summary

**Read + judgment + persist are complete** — classify + extract + typed output + master data + realistic fixtures + envelope contract + inject CLI + Firestore emulator with seeded master data + typed async read repo + `OrderValidator` (3-tier SKU matcher, customer resolver, price tolerance, qty sanity, scorer, threshold-based router) + `FirestoreOrderStore` / `FirestoreExceptionStore` + `IntakeCoordinator` end-to-end against the emulator.

**Track A (agent orchestration) is ~40% in** — contracts bumped, models + prompts + LlmAgent factories in, first BaseAgent stage (`IngestStage`) landed. Seven stages + root assembly + integration tests + smoke evalset + status close-out remain.

That's **Track A (partial) + Track D (dashboard) + Track E (eval) + Track Demo** left. Track V, Track P (read+write), and the first wave of Track A all landed on master. Roughly **~85% of total code lines are done**. Track A completes the full `EmailEnvelope → ClassifiedDocument → ParsedDocument → ValidationResult → (ClarifyEmail) → ProcessResult → RunSummary` pipeline; `feat/eval` can proceed in parallel once Step 5 assembles the root agent.

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
tests/unit/test_coordinator.py                                          ✓ 14 tests: AUTO_APPROVE / CLARIFY / ESCALATE routing, dedupe, snapshots, agent_version, reason concatenation, thread_id fallback + clarify_body written on CLARIFY / dropped on ESCALATE / defaults None (Track A Step 1)
tests/unit/test_exception_store.py                                      ✓ 15 tests (was 14): adds clarify_body round-trip + schema_version==2 assertion (Track A Step 1)
tests/integration/test_order_store_emulator.py                          ✓ 4 emulator tests: round-trip, idempotency under collision, None on miss, distinct-doc independence
tests/integration/test_exception_store_emulator.py                      ✓ 4 emulator tests: round-trip, find_pending_clarify (composite index), update_with_reply, full lifecycle
tests/integration/test_coordinator_emulator.py                          ✓ 2 emulator tests: end-to-end AUTO_APPROVE + CLARIFY against real validator + seeded master data
backend/models/exception_record.py (schema v2)                          ✓ adds clarify_body: Optional[str], schema_version default 1→2 (4bf008e, Track A Step 1)
backend/persistence/coordinator.py (signature)                          ✓ IntakeCoordinator.process(..., clarify_body: Optional[str] = None) — PENDING_CLARIFY persists the body inline; ESCALATE drops it defensively (4bf008e, Track A Step 1)
backend/models/clarify_email.py                                         ✓ ClarifyEmail(subject, body) output schema with ConfigDict(extra="forbid") — feeds build_clarify_email_agent() (bc0bab5, Track A Step 2)
backend/models/run_summary.py                                           ✓ RunSummary(orders_created, exceptions_opened, docs_skipped, summary) — feeds build_summary_agent() (bc0bab5, Track A Step 2)
backend/prompts/clarify_email.py                                        ✓ SYSTEM_PROMPT + INSTRUCTION_TEMPLATE with {customer_name}/{original_subject}/{reason} state-key placeholders for ADK runtime injection (bc0bab5, Track A Step 2)
backend/prompts/summary.py                                              ✓ SYSTEM_PROMPT + INSTRUCTION_TEMPLATE with {orders_created}/{exceptions_opened}/{docs_skipped}/{reply_handled} placeholders (bc0bab5, Track A Step 2)
backend/my_agent/agents/__init__.py                                     ✓ factory-package marker (eebbd35, Track A Step 3)
backend/my_agent/agents/clarify_email_agent.py                          ✓ build_clarify_email_agent() → fresh LlmAgent per call, gemini-3-flash-preview, output_schema=ClarifyEmail, output_key="clarify_email"; persona + instruction template concatenated into LlmAgent.instruction (the supported path; global_instruction is deprecated, system_instruction is not a constructor field per llm_agent.py:874-876) (eebbd35, Track A Step 3)
backend/my_agent/agents/summary_agent.py                                ✓ build_summary_agent() → fresh LlmAgent, same model/pattern, output_schema=RunSummary, output_key="run_summary" (eebbd35, Track A Step 3)
tests/unit/test_llm_agent_factories.py                                  ✓ 3 smoke tests: per-factory config (name/model/output_schema identity/output_key/placeholder presence) + distinct-instance guard via id() (eebbd35, Track A Step 3)
backend/my_agent/stages/__init__.py                                     ✓ stages-package marker (63780e9, Track A Step 4a)
backend/my_agent/stages/ingest.py                                       ✓ IngestStage(BaseAgent) — reads ctx.user_content.parts[0].text, heuristic path-vs-raw-EML (MIME-header + blank-line sniff), parses via parse_eml (raw content routed through NamedTemporaryFile since parse_eml is Path-only), synthesizes body.txt EmailAttachment when envelope.attachments is empty, writes envelope.model_dump(mode="json") via EventActions.state_delta; name: str = INGEST_STAGE_NAME class default (63780e9, Track A Step 4a)
tests/unit/test_stage_ingest.py                                         ✓ 9 tests: path input / raw EML input via tempfile / empty user_content → ValueError / nonexistent path → EmlParseError / body-only synthesis / MIME-header heuristic / whitespace-only user_content / author-and-name / path-starting-with-From_Suppliers/-without-blank-line (code-review follow-up landed with 2566376)
backend/my_agent/stages/reply_shortcircuit.py                           ✓ ReplyShortCircuitStage(BaseAgent #2) — reads state["envelope"] (model_validate round-trips the model_dump(mode="json") dict cleanly), branches on envelope.in_reply_to falsiness (treats None and "" identically), looks up pending exception via find_pending_clarify, advances PENDING_CLARIFY → AWAITING_REVIEW via update_with_reply(source_message_id=<parent>, reply_message_id=envelope.message_id) on match; state_delta on match carries reply_handled/reply_parent_source_message_id/reply_updated_exception/reply_body_text; ExceptionStore injected via PrivateAttr (Pattern B — Pattern A with arbitrary_types_allowed fails at schema-build time because Python Protocol classes aren't isinstance-valid without @runtime_checkable) (2566376, Track A Step 4b)
tests/unit/test_stage_reply_shortcircuit.py                             ✓ 6 tests: no in_reply_to → reply_handled=False / empty in_reply_to → reply_handled=False / in_reply_to but no pending match → reply_handled=False + update_with_reply NOT called / in_reply_to with pending match → reply_handled=True + full state delta / missing envelope state → ValueError / update_with_reply raising propagates (fail-fast). AsyncMock(spec=ExceptionStore) deps (2566376, Track A Step 4b)
backend/my_agent/stages/classify.py                                     ✓ ClassifyStage(BaseAgent #3) — reads state["envelope"], skips cleanly when state["reply_handled"] is True (preserves prior skipped_docs), otherwise iterates attachments sequentially and awaits `asyncio.to_thread(classify_fn, att.content, att.filename)` per attachment (sequential for trace legibility; concurrency-cap reasoning in module docstring); splits by `document_intent == "purchase_order"`; emits state_delta with `classified_docs` (ClassifiedDocument dicts via model_dump(mode="json")) and `skipped_docs` ({filename, stage, reason} entries); classify_fn raising propagates. Injected ClassifyFn = Callable[[bytes, str], ClassifiedDocument] via PrivateAttr (18ce553, Track A Step 4c)
tests/unit/test_stage_classify.py                                       ✓ 7 tests: reply_handled no-op / single PO / mixed PO+non-PO split / all non-PO / missing envelope → ValueError / empty attachments list / classify_fn raising propagates. Plain-def fake classify_fn with side_effect keyed on filename (18ce553, Track A Step 4c)
backend/ingestion/email_envelope.py (bytes roundtrip fix)               ✓ @field_validator("content", mode="before") — base64-decodes when input is a valid-base64 string; falls back to default bytes coercion otherwise (keeps parse_eml's str-content path working for text attachments); unblocks Step 4d ParseStage which passes `attachment.content` straight to LlamaExtract (fda48a0)
tests/unit/test_email_envelope_roundtrip.py                             ✓ 3 tests: ASCII / non-ASCII binary (0x89 PNG header + high bytes) / empty bytes — all assert `EmailEnvelope.model_validate(env.model_dump(mode="json")).attachments[0].content == original` (fda48a0)
backend/my_agent/stages/parse.py                                        ✓ ParseStage(BaseAgent #4) — reads state["envelope"] + state["classified_docs"], builds filename→bytes lookup over envelope.attachments, iterates classified docs sequentially with `asyncio.to_thread(parse_fn, content, filename)`, flattens `parsed.sub_documents` per-sub-doc into `{filename, sub_doc_index, parsed, sub_doc}` entries (1→N fan-out), and on zero sub_documents APPENDS `{filename, stage: "parse_stage", reason: "parser returned zero sub_documents"}` to `skipped_docs` (preserves ClassifyStage entries via `list(state.get("skipped_docs", []))` defensive copy). Injected `ParseFn = Callable[[bytes, str], ParsedDocument]` via PrivateAttr (6ba10e8, Track A Step 4d)
tests/unit/test_stage_parse.py                                          ✓ 9 tests: reply_handled no-op / missing envelope → ValueError / missing classified_docs → ValueError / empty classified_docs / single doc 1 subdoc / single doc 3 subdocs (index reset 0-1-2) / multi-doc flatten (index reset per source) / zero subdocs adds skipped entry AND preserves pre-seeded ClassifyStage entry at index 0 / parse_fn raising propagates (6ba10e8, Track A Step 4d)
```

### Missing (this sprint's work, mapped to branches / Track A steps)

```
backend/persistence/embeddings_index.py           → feat/embeddings (unblocks SKU tier-3; find_product_by_embedding is stubbed today)
backend/my_agent/agent.py (rewrite of stub)       → Track A Step 5 (assemble root SequentialAgent)
backend/my_agent/stages/validate.py               → Track A Step 4e (per-sub-doc validator.validate; also planned pull-up of `_make_ctx` test helper to `tests/unit/_stage_testing.py` — copy #5 is the trigger)
backend/my_agent/stages/clarify.py                → Track A Step 4f (per-CLARIFY-tier validation result, invokes build_clarify_email_agent() child via child.run_async(ctx), copies state["clarify_email"] → state["clarify_bodies"][flat_index])
backend/my_agent/stages/persist.py                → Track A Step 4g (per-sub-doc coordinator.process(..., clarify_body=...))
backend/my_agent/stages/finalize.py               → Track A Step 4h (invokes build_summary_agent() child; RunSummary dict lands on state["run_summary"])
tests/unit/test_stage_{validate,clarify,persist,finalize}.py  → Track A Steps 4e-4h
tests/unit/_stage_testing.py (pulled-up _make_ctx helper)         → Track A Step 4e
tests/unit/test_orchestrator_build.py             → Track A Step 5 (asserts root SequentialAgent topology + stage names)
tests/integration/test_orchestrator_emulator.py   → Track A Step 6 (end-to-end Runner + real validator + real coordinator + real emulator + stubbed clarify/summary; drives patterson_po-28491.wrapper.eml)
tests/eval/smoke.evalset.json                     → Track A Step 7 (4 cases: patterson AUTO, redline, CLARIFY-forcing, birch_valley reply)
tests/eval/eval_config.json                       → Track A Step 7
tests/eval/fixtures/seed_birch_valley_exception.py → Track A Step 7 (pre-seeds PENDING_CLARIFY before the reply case)
scripts/run_demo.py                               → feat/demo-script
tests/eval/*.evalset.json (full 3-scenario)       → feat/eval (Track E — tightens Track A Step 7 thresholds)
scripts/run_eval.py                               → feat/eval
frontend/                                         → feat/dashboard
```

**Fixture wrappers complete (2026-04-21):** all 10 non-`.eml` fixtures now have `{body.txt,wrapper.eml}` pairs. `tests/unit/test_eml_parser.py` parametrizes over every `.eml` under `data/` and runs 44 checks (envelope parse + attachment byte round-trip) — all green. Patterson adhoc CSV uses `--attachment-mime application/octet-stream` because its UTF-8 BOM (a known_ambiguity) would be mangled by the default `text/csv` re-encoding path; scaffold script grew an `--attachment-mime` flag to support this.

## What to build first

Track A is in flight on master (no worktree) under subagent-driven development. Steps 1 through 4a landed; remaining work in priority order:

1. **Track A Steps 4b-4h** (seven more `BaseAgent` stages): ReplyShortCircuitStage, ClassifyStage, ParseStage, ValidateStage, ClarifyStage, PersistStage, FinalizeStage. Each is constructor-injected with its dependency (exception_store / classify_fn / parse_fn / validator / clarify_agent / coordinator / summary_agent), reads its inputs from `session.state`, yields Events with `EventActions.state_delta`. Per-stage unit test against real `InvocationContext` + `InMemorySessionService` (the pattern `IngestStage` established — `SimpleNamespace` ducking doesn't work because `BaseAgent.run_async` calls `parent_context.model_copy`).
2. **Track A Step 5** — `build_root_agent()` assembles the eight stages into a `SequentialAgent`; topology test asserts stage order + names.
3. **Track A Step 6** — end-to-end integration test against the Firestore emulator with stubbed clarify/summary LlmAgents (deterministic output).
4. **Track A Step 7** — `tests/eval/smoke.evalset.json` with 4 cases; `adk eval` gated by `GOOGLE_API_KEY`.
5. **Track A Step 8** — manual `adk web .` smoke (operator types a fixture path, watches events stream).
6. **Track A Step 9** — close out this status doc.
7. **`feat/eval`** — full 3-scenario golden set (parallel to Track A after Step 5 lands; Step 7's smoke set is the first draft that Track E tightens).

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
- `ExceptionRecord` (schema v2, now with `clarify_body`), `ExceptionStatus` → `backend/models/exception_record.py` **✓ on master (4bf008e, 2026-04-22)**
- `OrderStore`, `ExceptionStore` Protocols → `backend/persistence/base.py` **✓ on master (3202120, 2026-04-22)**
- `IntakeCoordinator.process(..., clarify_body=None)`, `ProcessResult` → `backend/persistence/coordinator.py` **✓ on master (4bf008e, 2026-04-22)**
- `ClarifyEmail`, `RunSummary` (LlmAgent `output_schema` contracts) → `backend/models/clarify_email.py`, `backend/models/run_summary.py` **✓ on master (bc0bab5, 2026-04-22)**
- `build_clarify_email_agent()`, `build_summary_agent()` (LlmAgent factories, fresh-per-call) → `backend/my_agent/agents/` **✓ on master (eebbd35, 2026-04-22)**
- `IngestStage`, `INGEST_STAGE_NAME` → `backend/my_agent/stages/ingest.py` **✓ on master (63780e9, 2026-04-22)**
- `ReplyShortCircuitStage`, `REPLY_SHORTCIRCUIT_STAGE_NAME` → `backend/my_agent/stages/reply_shortcircuit.py` **✓ on master (2566376, 2026-04-22)** — establishes the `PrivateAttr` dep-injection pattern for Protocol-typed deps.
- `ClassifyStage`, `CLASSIFY_STAGE_NAME`, `ClassifyFn` type alias → `backend/my_agent/stages/classify.py` **✓ on master (18ce553, 2026-04-22)** — establishes the `asyncio.to_thread` pattern for sync blocking tools.
- `EmailAttachment.content` bytes round-trip via `@field_validator("content", mode="before")` → `backend/ingestion/email_envelope.py` **✓ on master (fda48a0, 2026-04-22)** — required for ParseStage to receive real binary.
- `ParseStage`, `PARSE_STAGE_NAME`, `ParseFn` type alias → `backend/my_agent/stages/parse.py` **✓ on master (6ba10e8, 2026-04-22)** — establishes 1→N flatten shape on `state["parsed_docs"]` + APPEND-not-overwrite contract on `state["skipped_docs"]` for the remaining stages.

`feat/eval` can fork freely as soon as Track A Step 5 (root_agent) lands — that's the surface `adk eval` binds to.

## Connections

- [Order-Intake-Sprint-Overview](Order-Intake-Sprint-Overview.md)
- [Order-Intake-Sprint-Worktrees](Order-Intake-Sprint-Worktrees.md) — full dependency graph and per-track contracts
- [Order-Intake-Sprint-Theory-vs-Practice](Order-Intake-Sprint-Theory-vs-Practice.md) — why "what we have" differs from "what spec says" in rows 2a, 2b
- [Order-Intake-Sprint-Verification](Order-Intake-Sprint-Verification.md) — what "done" looks like per track
- [Firebase-Init-Decisions](Firebase-Init-Decisions.md) — Firestore-only stack, emulator-first, `demo-order-intake-local` project ID
- `research/Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Overview.md` — the spec this status measures against
