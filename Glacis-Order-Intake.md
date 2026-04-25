---
type: roadmap
topic: "Order Intake Agent — MVP vs Full Glacis Spec"
date: 2026-04-21
last_updated: 2026-04-24 (ConfirmStage landed — Track A AUTO-leg customer confirmation email now complete on master. Pipeline is 9 BaseAgent stages: ingest → reply_shortcircuit → classify → parse → validate → clarify → persist → **confirm** → finalize. §9 "Auto-confirmation email on AUTO_APPROVE" flipped `[Nice-to-have]` → `[MVP ✓]`. `ConfirmationEmail(subject, body)` output schema + `build_confirmation_email_agent()` (gemini-3-flash-preview) + `confirmation_email.py` prompt template + `OrderRecord.confirmation_body` (schema v2) + `OrderStore.update_with_confirmation` + `ConfirmStage(BaseAgent #8)` all on master (6c9d429 / 76eb96a / f301475 / 13f05a5 / e5efc96 / 7d9c9d8 / 6344a83 / f5db946). `AGENT_VERSION` bumped `track-a-v0.1` → `track-a-v0.2`. 323 unit tests + 10+ integration + 3-case smoke evalset. Live-smoke verified 2026-04-24 on MM Machine fixture: all 9 stages fired, spec-compliant 7-sentence confirmation draft echoed both SKUs + $127.40 total + ship-to + Net 30 + `Ref:` line; session dump at `data_email_mm_machine_reorder_2026-04-24.eml.json`. No Gmail send — body lands on the persisted `OrderRecord` + `adk web` trace; outbound Gmail still Post-MVP. Prior 2026-04-22 baseline: 8 stages + Track A complete + 297 tests + first live-run audit (23e5812) fixed F1/F3/F4/F5/F14/F16. Sprint scope closes out with Tracks D / E / Demo + Post-MVP phases still open.)
tags:
  - roadmap
  - gap-analysis
  - post-hackathon
  - glacis-parity
---

# Order Intake Agent — MVP vs Full Glacis Spec

## Purpose

This is the **post-hackathon roadmap**: every capability Glacis's Order Intake Agent specifies across `research/Glacis-Deep-Dive/`, tagged by MVP status, with a specific action to reach (or exceed) Glacis parity after the Google Solution Challenge 2026 submission.

For the *in-sprint* view — what's shipping this week and what's still on the board for MVP — see `research/Order-Intake-Sprint-Status.md`. This doc is the *longer horizon*: the features we consciously cut, the features Glacis specifies that we never planned, and the order they should return in after the demo ships.

## Legend

- `[MVP ✓]` — built and landed on master
- `[MVP ⚠]` — in-sprint work, still planned for MVP submission
- `[Post-MVP]` — explicitly cut from MVP (per `Order-Intake-Sprint-Decisions.md` cut-list); must be built post-hackathon to reach Glacis parity
- `[Nice-to-have]` — Glacis spec mentions it; not critical for parity; polish/enterprise-tier

Source references point to files in `research/Glacis-Deep-Dive/` (filename prefix `Glacis-Agent-Reverse-Engineering-` omitted for brevity).

---

## 1. Signal Ingestion

> Glacis spec: Gmail push notifications → Pub/Sub → Cloud Function → History API → attachment download → classification routing. Sub-minute latency from email arrival to agent invocation.

- `[MVP ✓]` **`.eml` envelope contract** — `EmailEnvelope` + `EmailAttachment` dataclasses + `parse_eml` parser. MVP: `backend/ingestion/email_envelope.py`, `backend/ingestion/eml_parser.py`. Post-hackathon: no change — this stays the internal contract regardless of Gmail source. Source: `Email-Ingestion.md`.
- `[MVP ✓]` **Injection CLI for local/demo replay** — feeds `.eml` fixtures through the pipeline without Gmail. MVP: `scripts/inject_email.py`. Post-hackathon: keep as the eval/replay harness even after real Gmail lands. Source: `Email-Ingestion.md`.
- `[MVP ✓]` **Multi-format fixture coverage** — PDF / CSV / XLSX / EDI wrapper `.eml`s for every attachment fixture (10/10 as of 2026-04-21). MVP: `data/{pdf,csv,excel,edi}/*.wrapper.eml`. Post-hackathon: extend with real customer samples once collected. Source: `Email-Ingestion.md`, `Document-Processing.md`.
- `[MVP ✓]` **Gmail OAuth via installed-app flow + refresh-token in `.env`** — `scripts/gmail_auth_init.py` runs the one-time `InstalledAppFlow.run_local_server()`; refresh token lives in `.env`. Covers personal `gmail.com` accounts without requiring Workspace + domain-wide delegation. MVP: landed 2026-04-25 via Track A1 (572782c). Post-hackathon: Secret Manager swap is one-line. Source: `Email-Ingestion.md`.
- `[MVP ✓]` **Gmail polling ingress loop** — `scripts/gmail_poll.py` runs every 30s, pulls `in:inbox -label:orderintake-processed`, drives each through the 9-stage pipeline in-process via `Runner.run_async`, applies the dedup label after success. MVP: landed 2026-04-25 via Track A1 (`backend/gmail/{client,adapter,poller,scopes}.py` + entry script da43b81). Post-hackathon: swapped for push (watch + Pub/Sub + webhook) in Track A3. Source: `Email-Ingestion.md`.
- `[MVP ✓]` **Gmail `users.watch()` registration** — `backend/gmail/watch.py` (start/stop/getProfile) + in-worker daily renewal via `GmailPubSubWorker._renew_loop` (`GMAIL_WATCH_RENEW_INTERVAL_SECONDS`, default 86400). 7-day Gmail auto-expiration handled by re-asserting daily; expiration timestamp logged via `gmail_watch_started`/`gmail_watch_renewed`. MVP: landed 2026-04-25 via Track A3 (68e0535 + 6787e10). Post-hackathon: move renewal to Cloud Scheduler so the worker doesn't own renewal lifecycle. Source: `Email-Ingestion.md`.
- `[MVP ✓]` **Pub/Sub PULL subscription** — `GmailPubSubWorker._drain_loop` uses `SubscriberAsyncClient.pull()` in a long-lived asyncio loop. Topic + subscription bootstrapped by `scripts/gmail_watch_setup.py`. Runs as `scripts/gmail_pubsub_worker.py`. MVP: landed 2026-04-25 via Track A3 (6787e10 + 66d8a57). Post-hackathon: PUSH subscription + Cloud Run webhook for sub-second latency without the long-lived worker. Source: `Email-Ingestion.md`, `Event-Architecture.md`.
- `[Post-MVP]` **Pub/Sub PUSH subscription + Cloud Run webhook** — deliberately deferred after Track A3 landed the PULL variant. A3's worker covers the functional gap; Cloud Run adds deployment posture + lower latency (live webhook is sub-second vs. PULL worker's ~1-5s drain cadence). Post-hackathon migration: swap `GmailPubSubWorker` with a FastAPI route; keep adapter, pipeline, cursor store unchanged. Source: `Email-Ingestion.md`, `Event-Architecture.md`.
- `[MVP ✓]` **History API sync + dedup** — `backend/gmail/history.py` walks `users.history.list()` from a stored cursor; `GmailSyncStateStore` (Firestore at `gmail_sync_state/{user_email}`) persists the cursor; stale-cursor `HistoryIdTooOldError` triggers an A1-style full-scan one-cycle fallback then resumes push. `orderintake-processed` Gmail label + `source_message_id` doc-id idempotency collapse all duplicates. MVP: landed 2026-04-25 via Track A3 (118a7a3 + be56942). Source: `Email-Ingestion.md`.
- `[Post-MVP]` **Thread tracking for clarify-reply loop** — pull full `thread` via Gmail API so clarify-reply correlates via `threadId`, not just subject. MVP: fixture exists (`birch_valley_clarify_reply`), handler does not. Post-hackathon: clarify reply-pair fixture already proves the contract; wire `thread_id` from `EmailEnvelope` into `ExceptionStore.find_pending_clarify(thread_id)`. Source: `Email-Ingestion.md`, `Exception-Handling.md`.
- `[Post-MVP]` **Gmail OAuth + domain-wide delegation** — service-account auth for Workspace accounts; OAuth consent screen for the agent's dedicated inbox. MVP: —. Post-hackathon: Secret Manager for refresh token; rotate quarterly. Source: `Email-Ingestion.md`, `Deployment.md`.
- `[Nice-to-have]` **CC-forwarding integration model** — customer leaves existing inbox untouched, auto-forwards to agent's dedicated Workspace inbox (Glacis's "2-week deployment" trick). Post-hackathon: document in deployment playbook; add fixture demonstrating forwarded headers. Source: `Email-Ingestion.md`.
- `[Nice-to-have]` **Multi-inbox routing** — one Cloud Function, multiple inboxes distinguished by `emailAddress` in notification payload. Post-hackathon: single-inbox first; multi-inbox only when first enterprise pilot lands. Source: `Email-Ingestion.md`.
- `[Nice-to-have]` **Pull Pub/Sub subscription w/ batch processing** — for scale beyond ~100 emails/hour; push is fine for MVP and early pilots. Source: `Email-Ingestion.md`.
- `[Nice-to-have]` **Disaster-recovery historyId catch-up** — fallback to `messages.list` with date filter when stored `historyId` is older than Gmail's retention window. Post-hackathon: only needed for prod ops; document the runbook. Source: `Email-Ingestion.md`.

---

## 2. Classification

> Glacis spec: lightweight LLM classifier (Gemini Flash) triages email intent — `order` / `inquiry` / `follow-up` / `not-relevant` — before running expensive extraction. Deterministic format detection via MIME type.

- `[MVP ✓]` **Intent classifier** — LlamaClassify over email body + attachment hints. MVP: `backend/tools/document_classifier/`. Post-hackathon: migrate to Gemini Flash call for Google-native parity once free-tier budget proves out, or keep LlamaClassify if quality is better. Source: `Order-Intake-Agent.md`, `Document-Processing.md`.
- `[MVP ✓]` **Deterministic format detection** — MIME-type based (PDF / CSV / XLSX / EDI / plain text). MVP: `backend/tools/document_classifier/`. Source: `Document-Processing.md`.
- `[Post-MVP]` **Digital-native vs scanned PDF discrimination** — PyMuPDF `page.get_text()` threshold check to decide Tier 1 (text extraction) vs Tier 2 (multimodal). MVP: —. Post-hackathon: saves ~90% of extraction cost on digital-native PDFs that dominate in practice. Source: `Document-Processing.md`.
- `[Post-MVP]` **Sender-domain → customer_id resolution at classification** — look up `customers.email_domains` before extraction so customer context primes the prompt. MVP: customer resolution happens post-extraction. Post-hackathon: cheaper and more accurate prompts if classifier hands downstream stages a likely `customer_id`. Source: `Firestore-Schema.md` (customers.email_domains).
- `[Nice-to-have]` **Confidence-scored routing for ambiguous emails** — flag "is this even an order?" below threshold; route to human inbox review. Post-hackathon: reduces noise in dashboard; low priority until Gmail push lands. Source: `Order-Intake-Agent.md`.

---

## 3. Extraction

> Glacis spec: tiered extraction — free deterministic parsers for structured formats, Gemini multimodal for the rest. Per-field confidence scoring. Anti-hallucination guarantees via structured output schemas.

- `[MVP ✓]` **LLM multimodal extraction → structured JSON** — LlamaExtract → `ParsedDocument` Pydantic model. MVP: `backend/tools/document_parser/`. Post-hackathon: consider dual-path (Gemini + LlamaExtract) for generator-judge cross-check on high-value orders. Source: `Document-Processing.md`.
- `[MVP ✓]` **Typed `ParsedDocument` contract** — line items + header fields with explicit types. MVP: `backend/models/parsed_document.py`. Source: `Prompt-Templates.md`, `Document-Processing.md`.
- `[Post-MVP]` **Tier 0/1 deterministic extractors** — `csv.DictReader`, `openpyxl`, PyMuPDF text layer + `pdfplumber` tables. Saves 80%+ of token cost on the ~40-50% of orders that are structured. MVP: all formats currently go through LlamaExtract. Post-hackathon: lowest-hanging cost optimization. Source: `Document-Processing.md`, `Token-Optimization.md`.
- `[Post-MVP]` **Customer-specific column-mapper registry** — first Excel order from a new customer → human maps columns → stored in Firestore → all subsequent orders extract deterministically at zero LLM cost. MVP: —. Post-hackathon: single feature that drives per-customer cost curve toward zero. Source: `Document-Processing.md`.
- `[Post-MVP]` **Per-field confidence scoring from LLM** — high/medium/low per extracted field, not a single document-level score. Drives field-level human review instead of whole-order escalation. MVP: only aggregate confidence today. Post-hackathon: required for the "only flag the bad field" dashboard UX. Source: `Document-Processing.md`, `Validation-Pipeline.md`.
- `[Post-MVP]` **Structured output mode with `response_schema`** — Gemini's Pydantic-schema-constrained generation; eliminates malformed-JSON failure class. MVP: LlamaExtract handles this internally; migrate when moving to Gemini. Source: `Document-Processing.md`.
- `[Post-MVP]` **17+ field-label variation prompt** — "Qty / QTY / Qty Ordered / Order Qty / Units / Pcs / Pieces…" etc, embedded in system prompt so LLM doesn't fall down on synonym drift. MVP: current prompt covers common variants; no systematic coverage audit. Source: `Document-Processing.md`, `Prompt-Templates.md`.
- `[Post-MVP]` **Retry-with-error-feedback loop** — when Pydantic validation fails, re-prompt with the specific error appended. MVP: —. Post-hackathon: pairs with the Tier 0/1 deterministic layer. Source: `Document-Processing.md`.
- `[Post-MVP]` **Few-shot contamination guardrail** — never let enriched values into extraction few-shots (Alan Engineering finding). MVP: not yet prompting with few-shots. Post-hackathon: architectural note, not code — "extraction produces only what document says; enrichment adds only what DB says". Source: `Document-Processing.md`.
- `[Nice-to-have]` **OCR + multimodal hybrid for scanned PDFs** — OCR transcription *plus* document image fed to Gemini together (Alan Engineering finding). Post-hackathon: only needed when scanned/faxed fixtures enter the corpus. Source: `Document-Processing.md`.
- `[Nice-to-have]` **Handwriting recognition for dock-worker forms** — contextual disambiguation (weight fields = numbers, city fields = locations). Source: `Document-Processing.md`.
- `[Nice-to-have]` **Multi-document order assembly** — merging email body + PDF attachment + Excel schedule into one order. Source: `Document-Processing.md`.
- `[Nice-to-have]` **Agentic multi-pass self-correction** — LlamaIndex pattern: second pass reviews and corrects first. Source: `Document-Processing.md`.

---

## 4. Validation Pipeline

> Glacis spec: seven sequential validation checks — Duplicate → SKU → Price → Address → Credit → Inventory → Delivery Feasibility. Confidence-accumulation model (each check adds/subtracts delta). Deterministic layer first, semantic layer fallback.

- `[MVP ✓]` **`OrderValidator` orchestrator** — wires SKU + customer + price + qty checks + scorer + router into one `.validate(order) → ValidationResult`. MVP: `backend/tools/order_validator/validator.py`. Source: `Validation-Pipeline.md`.
- `[MVP ✓]` **SKU validation check** — 3-tier ladder (see §5). MVP: `backend/tools/order_validator/tools/sku_matcher.py`. Source: `Validation-Pipeline.md`.
- `[MVP ✓]` **Price validation (±10% tolerance)** — pure function, permissive on missing quote. MVP: `backend/tools/order_validator/tools/price_check.py`. Post-hackathon: per-customer / per-category tolerance bands (Glacis uses tight bands for pharma, loose for CPG). Source: `Validation-Pipeline.md`.
- `[MVP ✓]` **Quantity validation** — presence / sign / UoM / min_order. MVP: `backend/tools/order_validator/tools/qty_check.py`. Post-hackathon: add case-pack-multiple check and historical-range anomaly detection (50,000 units when monthly avg is 500). Source: `Validation-Pipeline.md`.
- `[MVP ✓]` **Customer resolver** — exact name / dba / alias match. MVP: `backend/tools/order_validator/tools/customer_resolver.py`. Post-hackathon: fuzzy + embedding fallback, same pattern as SKU. Source: `Validation-Pipeline.md`.
- `[MVP ✓]` **Scorer** — mean confidence + check-failure penalty aggregation. MVP: `backend/tools/order_validator/scorer.py`. Source: `Validation-Pipeline.md`.
- `[MVP ✓]` **Duplicate detection (check #1)** — preflight short-circuit in `OrderValidator.validate`. PO# OR content-hash signal, customer + 90-day-window scoped, `source_message_id` self-match filter; routes to `RoutingDecision.ESCALATE` with `rationale="duplicate of <existing_order_id>"` on hit. `OrderRecord` bumped to `schema_version=3` adding `customer_id` + `po_number` + `content_hash` denormalized fields so the two new composite Firestore indexes on `orders` hit flat paths. 25 new tests (unit + emulator integration + e2e full-pipeline). MVP: `backend/tools/order_validator/tools/duplicate_check.py` + validator preflight + `firebase/firestore.indexes.json`. Track C landed 2026-04-24 across commits e416d6f → ed982bd. Source: `Validation-Pipeline.md`.
- `[Post-MVP]` **Credit check (check #5)** — `customer.credit_used + order_total ≤ customer.credit_limit`. MVP: cut. Post-hackathon: adds the "can this customer pay?" question; needs credit fields seeded in customers master. Source: `Validation-Pipeline.md`, `Firestore-Schema.md`.
- `[Post-MVP]` **Inventory / ATP check (check #6)** — `available_qty + incoming_qty - reserved_qty ≥ requested_qty`. MVP: cut. Post-hackathon: requires inventory collection + multi-warehouse split-shipment logic. Source: `Validation-Pipeline.md`, `Firestore-Schema.md`.
- `[Post-MVP]` **Delivery feasibility check (check #7)** — `requested_date ≥ today + lead_time_days + transfer_buffer`; business-day aware; carrier-capacity aware. MVP: cut. Post-hackathon: ties into fulfillment promise, not just order acceptance. Source: `Validation-Pipeline.md`.
- `[Post-MVP]` **Address validation + enrichment (check #4)** — normalize free-text ship-to; fuzzy match against `customer.shipping[]`; auto-fill postal code + state from master. MVP: cut. Post-hackathon: pair with a simple geocoder (Google Maps Places API) for addresses not in master. Source: `Validation-Pipeline.md`.
- `[Post-MVP]` **Confidence-accumulation model (not pass/fail)** — each check contributes a signed delta to a starting baseline; thresholds on the accumulated score drive routing. MVP: current scorer is simpler (mean + penalty). Post-hackathon: migrate when 3+ checks are in play — penalty math stops scaling. Source: `Validation-Pipeline.md`.
- `[Post-MVP]` **Fail-fast sequencing** — expensive checks (inventory, credit) run only after cheap checks (dup, SKU) pass. MVP: all four checks fire in every validation. Post-hackathon: implements when adding inventory/credit. Source: `Validation-Pipeline.md`.
- `[Post-MVP]` **Inline enrichment per check** — address check also fills postal code; SKU check also attaches UoM and hazmat class. Validators are gatekeepers *and* data providers. MVP: partial (SKU check attaches UoM). Source: `Validation-Pipeline.md`.
- `[Nice-to-have]` **Parallel execution for independent checks** — credit and address are independent once SKU is resolved; can run concurrently. Post-hackathon: only matters if single-order latency becomes a bottleneck. Source: `Validation-Pipeline.md`.
- `[Nice-to-have]` **Generator-Judge quality gate before auto-execute** — secondary Gemini Flash validates the proposed action against SOP playbook before any irreversible write. Source: `Generator-Judge.md`, `Exception-Handling.md`.

---

## 5. Item Matching / Enrichment

> Glacis spec: three-tier cascade — exact/alias match → embedding similarity → human escalation. Vectors live in Firestore native vector field. `text-embedding-004` with `RETRIEVAL_DOCUMENT` / `RETRIEVAL_QUERY` task-type hints.

- `[MVP ✓]` **Tier 1 — exact + alias match** — dict lookup over SKU and customer aliases. MVP: `sku_matcher.py`. Source: `Item-Matching.md`.
- `[MVP ✓]` **Tier 2 — fuzzy match via rapidfuzz `token_set_ratio`** — against `short_description`. MVP: `sku_matcher.py`. Post-hackathon: the fuzzy layer will likely stay even after embeddings land (it handles typos better than semantics). Source: `Item-Matching.md`.
- `[MVP ✓]` **Tier 3 stub — falls through cleanly** — `find_product_by_embedding` returns empty today; caller correctly routes to clarify/escalate. MVP: `master_data_repo.py:find_product_by_embedding`. Source: `Item-Matching.md`.
- `[Post-MVP]` **Tier 3 — embedding similarity search (the real one)** — seed `text-embedding-004` vectors for all 35 products into Firestore vector index; `find_nearest()` with `COSINE` distance; threshold 0.90 for auto-match. Was tagged `feat/embeddings` in sprint plan. MVP: stub. Post-hackathon: the highest-impact Post-MVP feature for matching accuracy on paraphrased queries. Source: `Item-Matching.md`, `Firestore-Schema.md`.
- `[Post-MVP]` **Asymmetric embedding task types** — `RETRIEVAL_DOCUMENT` for catalog ingest, `RETRIEVAL_QUERY` for customer description. Free accuracy bump. MVP: N/A until Tier 3 lands. Source: `Item-Matching.md`.
- `[Post-MVP]` **Hybrid search — category-filtered vector search** — `where("category", "==", "coffee").find_nearest(...)` when classifier can infer category. Dramatically improves precision. MVP: N/A until Tier 3 lands. Source: `Item-Matching.md`.
- `[Post-MVP]` **Alias learning from human corrections** — every exception resolution that corrects a SKU match appends to `products.aliases` and triggers re-embed. Over time: more items hit Tier 1, cost drops, latency drops. MVP: alias field exists but no learning loop. Post-hackathon: couples tightly with §11 Learning Loop. Source: `Item-Matching.md`, `Learning-Loop.md`.
- `[Post-MVP]` **Per-customer fuzzy threshold calibration** — Glacis pattern is a single threshold; post-hackathon we can tune per customer based on their historical correction rate. Source: `Item-Matching.md`, `SOP-Playbook.md`.
- `[Nice-to-have]` **Unit conversion module** — "5lb = 2.27kg"; confirms dimensional consistency of fuzzy matches. Source: `Item-Matching.md`.
- `[Nice-to-have]` **Fine-tuned embedding model on catalog corpus** — Vertex AI embedding fine-tuning; only worth it once alias learning has produced a few hundred labeled pairs. Source: `Item-Matching.md`.
- `[Nice-to-have]` **Vertex AI Vector Search sidecar** — only at >100K SKU scale; Firestore flat-KNN is fine up to ~50K. Source: `Item-Matching.md`.

---

## 6. Decision Layer

> Glacis spec: three discrete autonomy levels — Auto-Execute ≥0.95 / Clarify 0.80–0.95 / Escalate <0.80. Per-type thresholds live in the SOP playbook. Per-customer overrides. Confidence-ramp deployment pattern.

- `[MVP ✓]` **Three-level `RoutingDecision` enum** — `AUTO_APPROVE` / `CLARIFY` / `ESCALATE`. MVP: `backend/models/validation_result.py`. Source: `Exception-Handling.md`.
- `[MVP ✓]` **Threshold-based router** — hardcoded 0.95 / 0.80 thresholds (Glacis spec exactly). MVP: `backend/tools/order_validator/router.py`. Source: `Exception-Handling.md`, `Order-Intake-Agent.md`.
- `[Post-MVP]` **Per-exception-type thresholds** — price 0.90, SKU 0.98, quantity 0.92. Different error costs get different tolerances. MVP: single global threshold pair. Post-hackathon: lives in SOP playbook once that exists. Source: `Exception-Handling.md`.
- `[Post-MVP]` **Per-customer threshold overrides** — pharma customer gets tight thresholds, CPG customer gets loose. MVP: —. Post-hackathon: `business_rules` collection with `scope.customer_id` wins over global. Source: `SOP-Playbook.md`.
- `[Post-MVP]` **Confidence-ramp deployment mode** — new customer starts at `human_required` for everything; graduate per-rule as trust builds. MVP: —. Post-hackathon: operational feature for first enterprise pilot. Source: `Exception-Handling.md`, `SOP-Playbook.md`.
- `[Post-MVP]` **Clarification loop cap** — hard-stop escalate after 2 clarify rounds; prevents infinite email ping-pong. MVP: —. Post-hackathon: pairs with thread-tracking and `ExceptionStore.find_pending_clarify`. Source: `Exception-Handling.md`.
- `[Post-MVP]` **Extraction-confidence vs resolution-confidence distinction** — 99% sure the document said "$15.00" ≠ 99% sure what to do about $15.00. Two separate scores drive two separate decisions. MVP: single score. Source: `Exception-Handling.md`.
- `[Nice-to-have]` **Synchronous vs asynchronous escalation by severity** — high severity blocks; low severity proceeds provisionally with flag. Source: `Exception-Handling.md`.
- `[Nice-to-have]` **Auto-calibrating thresholds** — when a given threshold's downstream exception rate exceeds target, tighten automatically. Source: `Exception-Handling.md`.

---

## 7. ERP Read (Master Data)

> Glacis spec: non-invasive wrapper — agent reads from Firestore which caches/mirrors the real ERP. Master data sync is periodic (5 min for pricing, 1 min for inventory, 1 hr for customer master).

- `[MVP ✓]` **Typed `MasterDataRepo` (async)** — returns `ProductRecord` / `CustomerRecord` / `MetaRecord` / `EmbeddingMatch`. MVP: `backend/tools/order_validator/tools/master_data_repo.py`. Source: `ERP-Integration.md`, `Firestore-Schema.md`.
- `[MVP ✓]` **Async Firestore client factory** — reusable across all read paths. MVP: `backend/tools/order_validator/tools/firestore_client.py`. Source: `ERP-Integration.md`.
- `[MVP ✓]` **Seeded masters** — 35 `products` + 10 `customers` + `meta/master_data` fixtures via idempotent loader. MVP: `scripts/load_master_data.py`. Source: `Firestore-Schema.md`.
- `[MVP ✓]` **Emulator-backed integration tests** — `firestore_emulator` pytest marker; parity with in-memory fake. MVP: `tests/integration/test_master_data_repo_emulator.py`. Source: `ERP-Integration.md`.
- `[MVP ✓]` **In-memory `FakeAsyncClient` for unit tests** — shared fixture in `tests/unit/conftest.py`. Source: Internal pattern, aligned with `Firestore-Schema.md`.
- `[Post-MVP]` **Inventory collection seeded + read path** — `available_qty` / `reserved_qty` / `incoming_qty` / `last_updated` per SKU per warehouse. MVP: no inventory collection. Post-hackathon: gates the inventory/ATP check (§4). Source: `ERP-Integration.md`, `Firestore-Schema.md`.
- `[Post-MVP]` **Customer credit fields seeded + read path** — `credit_limit` / `credit_used` on customer record. MVP: schema supports it; not seeded. Post-hackathon: gates the credit check (§4). Source: `Firestore-Schema.md`.
- `[Post-MVP]` **`customers.ordering_patterns` anomaly baseline** — monthly-refreshed `avg_monthly_qty` / `typical_skus` / `usual_quantities`. Feeds the "50,000 units when avg is 500" flag. MVP: schema supports it; not populated. Post-hackathon: Cloud Function monthly rollup over historical orders. Source: `Firestore-Schema.md`.
- `[Post-MVP]` **Product `price_tiers` + contract-price override logic** — tiered pricing + `customers.contract_prices[sku]` override. Today price check is a flat `±10%` on list price. MVP: partial schema. Post-hackathon: up to ⅓ of orders have price exceptions; this is where they live. Source: `Firestore-Schema.md`, `Validation-Pipeline.md`.
- `[Nice-to-have]` **Real ERP cache-sync adapter (SAP / Oracle / Dynamics 365)** — periodic pull into Firestore via IDoc / BAPI / REST. Production feature only. Source: `ERP-Integration.md`.
- `[Nice-to-have]` **Cache staleness guard** — `last_synced` timestamp on every cached doc; fall back to direct ERP read when older than sync interval. Source: `ERP-Integration.md`.
- `[Nice-to-have]` **Customer-Managed Encryption Keys (CMEK)** — enterprise tier; Google can't read without the customer's Cloud KMS key. Source: `Security-Audit.md`.

---

## 8. ERP Write (Transactional State)

> Glacis spec: agent writes `orders` for AUTO_APPROVE / `exceptions` for CLARIFY/ESCALATE. Batch-write the audit log entry + the transactional doc atomically. Idempotency via `source_message_id`.

- `[MVP ✓]` **`OrderRecord` + `ExceptionRecord` contracts** — typed records carrying `source_message_id` + `thread_id` for idempotency and clarify correlation. MVP: `backend/models/order_record.py` + `backend/models/exception_record.py` (commit 3202120). Includes `CustomerSnapshot` / `ProductSnapshot` / `OrderLine` / `OrderStatus` and `ExceptionStatus` (single-doc lifecycle: PENDING_CLARIFY → AWAITING_REVIEW → RESOLVED). `OrderRecord` at schema_version=2 — adds `confirmation_body: Optional[str]` populated post-save by ConfirmStage on AUTO_APPROVE (13f05a5, 2026-04-24); `ExceptionRecord` at schema_version=2 — adds `clarify_body: Optional[str]` populated inline by the coordinator on PENDING_CLARIFY (4bf008e). Source: `Firestore-Schema.md`.
- `[MVP ✓]` **`OrderStore` + `ExceptionStore` (Track P write side)** — `backend/persistence/` package; `IntakeCoordinator` consumes `ValidationResult.decision` to pick collection. MVP: `FirestoreOrderStore` + `FirestoreExceptionStore` + `IntakeCoordinator` + `ProcessResult` (commits b63b94d / a1d9921 / f81f870). 35 unit + 10 integration tests against the emulator; full suite at 235 passed. Source: `ERP-Integration.md`, `Firestore-Schema.md`.
- `[MVP ✓]` **`source_message_id` idempotency key** — on both `orders` and `exceptions`; optimistic `create(exists=False)` + `AlreadyExists` swallow returns the existing record on duplicate. Coordinator additionally preflight-checks both stores so duplicate envelopes return `ProcessResult(kind="duplicate")` without re-running validation. Source: `Email-Ingestion.md`, `Firestore-Schema.md`.
- `[MVP ✓]` **`IntakeCoordinator` orchestration surface** — the single entry point Track A invokes per `ParsedDocument`. Routes `RoutingDecision` → store; builds `OrderRecord` with full snapshots (customer from `validation.customer` already-resolved, products via `MasterDataRepo.get_product` per line); builds `ExceptionRecord` with auto-concatenated line-level reason. MVP: `backend/persistence/coordinator.py` (b63b94d). Source: derived from `ERP-Integration.md` + `Validation-Pipeline.md` synthesis.
- `[MVP ✓]` **Firestore composite index for `find_pending_clarify`** — `(thread_id ASC, status ASC, created_at DESC)` on `exceptions`; verified end-to-end against the emulator. MVP: `firebase/firestore.indexes.json` (a1d9921). Source: `Firestore-Schema.md`.
- `[MVP ✓]` **`OrderStore.update_with_confirmation` — post-save confirmation-body write** — field-mask update (`doc_ref.update({"confirmation_body": ...})`) invoked by ConfirmStage after the AUTO_APPROVE leg drafts a customer confirmation email. Protocol extension on `OrderStore` + impl on `FirestoreOrderStore`; raises `NotFound` when doc is absent (callers only invoke post-save); no idempotency skip — re-runs overwrite. MVP: `backend/persistence/base.py` + `backend/persistence/orders_store.py` (e5efc96) + emulator round-trip test (7d9c9d8). Source: derived from `ERP-Integration.md` + ConfirmStage plan.
- `[Post-MVP]` **Firestore batch write: audit + transactional doc atomically** — if audit write fails, the order write must fail too. MVP: audit log isn't written yet (see §13). Post-hackathon: mandatory before claiming SOC-2 readiness. Source: `ERP-Integration.md`, `Security-Audit.md`.
- `[Post-MVP]` **Order-state lifecycle** — `draft → validated → confirmed → shipped → completed` with transition events. MVP: writes land at `validated` and stop. Post-hackathon: requires downstream fulfillment integration (out of scope for single-agent MVP). Source: `Firestore-Schema.md`.
- `[Post-MVP]` **Cloud Function trigger: Firestore `orders` `onCreate` → Pub/Sub `order-created`** — fan-out to BigQuery sink / notifications / downstream agents. MVP: —. Post-hackathon: couples with §13 observability. Source: `ERP-Integration.md`, `Event-Architecture.md`.
- `[Post-MVP]` **`erp_sync` field + status tracking** — `pending / synced / failed` — signal for the production sync adapter. MVP: schema-ready. Post-hackathon: required when a real ERP enters the picture. Source: `ERP-Integration.md`.
- `[Post-MVP]` **Cross-collection transaction: inventory reservation + order create** — Firestore transactions; atomic "read inventory → check → reserve → create order". MVP: —. Post-hackathon: gates inventory check. Source: `ERP-Integration.md`.
- `[Nice-to-have]` **Real ERP write-back adapter** — BAPI / IDoc / REST; moves `orders` from source-of-truth to staging area. Source: `ERP-Integration.md`.

---

## 9. Clarify Email Generation

> Glacis spec: Gemini drafts a targeted clarification email asking *only* for the missing data, in the *same thread* as the original. Quality gate reviews before send. Reply lands on same `threadId`, re-runs full validation.

- `[MVP ✓]` **Clarify email generator** — ClarifyStage holds a structured-output Gemini `LlmAgent` (`build_clarify_email_agent()` returning `gemini-3-flash-preview` with `output_schema=ClarifyEmail(subject, body)`); per CLARIFY-tier validation result, seeds `{customer_name, original_subject, reason}` on `ctx.session.state` for the prompt template to interpolate, invokes child exactly once, captures the structured body from the final event's `state_delta`. `clarify_body` is persisted inline on `ExceptionRecord` (schema v2) by the coordinator. MVP: `backend/my_agent/stages/clarify.py` (b33a030) + `backend/my_agent/agents/clarify_email_agent.py` (eebbd35) + `backend/prompts/clarify_email.py` (bc0bab5) + `ExceptionRecord.clarify_body` (4bf008e). Post-hackathon: quality-gate second-Flash review (see below) + actual Gmail send. Source: `Exception-Handling.md`, `Prompt-Templates.md`.
- `[MVP ✓]` **Clarify-reply correlation via `thread_id`** — `ReplyShortCircuitStage` detects `envelope.in_reply_to`, looks up the parent exception via `ExceptionStore.find_pending_clarify(thread_id)`, calls `update_with_reply()` which advances `PENDING_CLARIFY → AWAITING_REVIEW` with the status guard. Sets `state["reply_handled"]=True` so every downstream stage no-ops cleanly for reply invocations. MVP: `backend/my_agent/stages/reply_shortcircuit.py` (2566376) + `ExceptionStore.find_pending_clarify` / `update_with_reply` + composite index (Track P: b63b94d / a1d9921). End-to-end fixture covered by `data/email/birch_valley_clarify_reply.eml`. Post-hackathon: full auto-merge of the reply body into the `ExtractedOrder` + re-validation (currently lightweight — a human finishes the resolution from the dashboard). Source: `Exception-Handling.md`.
- `[MVP ✓]` **Gmail API send integration** — `SendStage` (10th `BaseAgent`, inserted after `FinalizeStage`) calls `GmailClient.send_message` for every AUTO_APPROVE `confirmation_body` and CLARIFY `clarify_body`. RFC 5322 reply threading via `In-Reply-To` + `References` headers (Gmail auto-threads in the customer's inbox). Idempotency + observability via `sent_at` + `send_error` fields on both `OrderRecord` (schema v4) and `ExceptionRecord` (schema v3); `update_with_send_receipt` on both Firestore stores. Fail-open per record: send errors are persisted to `send_error` and the next pipeline run retries. `GMAIL_SEND_DRY_RUN=1` env toggle for dev. `AGENT_VERSION` bumped `track-a-v0.2` → `v0.3`. MVP: landed 2026-04-25 via Track A2 (commit chain `eabfa5b` → `2606677` → `9b912f4` → `284f69b` → `f97e52d` → `3508734` → `bae1100` → `22089c1` → `184a429`). Post-hackathon: Generator-Judge quality gate (Track B) reviews bodies before send. Source: `Email-Ingestion.md`.
- `[Post-MVP]` **Gemini quality-gate check on outbound email** — secondary Flash call: "no hallucinated URLs, no hallucinated data, no unauthorized commitments, professional tone". MVP: —. Post-hackathon: mandatory before sending *anything* to real customers. Source: `Generator-Judge.md`, `Exception-Handling.md`.
- `[Post-MVP]` **Clarify loop round-cap (hard 2)** — after 2 rounds with no resolution → escalate unconditionally. Prevents infinite loops. MVP: —. Source: `Exception-Handling.md`.
- `[Post-MVP]` **Clarify email templates per exception type** — "missing FedEx account" vs "SKU ambiguity" have different question shapes. MVP: —. Post-hackathon: templates live in SOP playbook. Source: `SOP-Playbook.md`, `Prompt-Templates.md`.
- `[MVP ✓]` **Auto-confirmation email on AUTO_APPROVE** — `ConfirmStage` (BaseAgent #8, inserted between PersistStage and FinalizeStage) holds a structured-output Gemini `LlmAgent` (`build_confirmation_email_agent()` returning `gemini-3-flash-preview` with `output_schema=ConfirmationEmail(subject, body)`); per `kind=="order"` process_result, seeds `{customer_name, original_subject, order_details, order_ref}` on `ctx.session.state`, invokes child, captures body, then calls `OrderStore.update_with_confirmation(source_message_id, body)` to write onto the persisted `OrderRecord.confirmation_body` (schema v2). Duplicates skipped (confirmation came from prior run); exceptions go through the CLARIFY leg. `AGENT_VERSION` bumped `track-a-v0.1` → `track-a-v0.2` so Firestore analytics can distinguish pre/post-confirmation rows. MVP: `backend/my_agent/stages/confirm.py` (6344a83) + `backend/my_agent/agents/confirmation_email_agent.py` (f301475) + `backend/prompts/confirmation_email.py` (76eb96a) + `backend/models/confirmation_email.py` (6c9d429) + `OrderRecord.confirmation_body` (13f05a5) + `OrderStore.update_with_confirmation` (e5efc96) + 9-stage wiring (f5db946). Live-smoke verified 2026-04-24 on MM Machine fixture. No Gmail send — body lands on `OrderRecord` + `adk web` trace only. Post-hackathon: Gmail API send + quality-gate second-Flash review. Source: `Order-Intake-Agent.md`.
- `[Nice-to-have]` **Multi-language support** — clarify emails in the customer's language (Carlsberg case: 150 markets, multi-language). Source: `Order-Intake-Agent.md`.

---

## 10. Human Dashboard

> Glacis spec: six-panel real-time cockpit — KPI summary / order queue / exception panel with one-click approve/reject/edit / PO tracker / audit trail viewer / before-after metrics. Firestore `onSnapshot` listeners, no polling. Firebase Hosting static SPA.

- `[MVP ⚠]` **Read-only order list view (Track D)** — display of recent orders from `orders` collection with status badges. MVP: planned. Source: `Dashboard-UI.md`.
- `[MVP ⚠]` **Read-only exception view (Track D)** — display of pending exceptions with extracted data + validation flags. MVP: planned; approve/reject deferred. Source: `Dashboard-UI.md`.
- `[Post-MVP]` **One-click exception resolution (approve / reject / edit)** — Cloud Function write, not direct client write; security rules enforce. MVP: cut (read-only only). Post-hackathon: *the* dashboard feature per Glacis; without it the dashboard is a log viewer. Source: `Dashboard-UI.md`, `Exception-Handling.md`.
- `[Post-MVP]` **KPI summary panel** — Orders Today / Touchless Rate / Avg Processing Time / Exceptions Pending. Pre-aggregated via Cloud Function on `orders onCreate`. MVP: —. Source: `Dashboard-UI.md`, `Metrics-Dashboard.md`.
- `[Post-MVP]` **`daily_metrics/{date}` pre-aggregation** — Cloud Function on `orders`/`exceptions` writes increments a counter doc. Dashboard reads *one* document for the KPI panel. MVP: —. Post-hackathon: the "build it right once" call. Source: `Dashboard-UI.md`.
- `[Post-MVP]` **Firestore real-time `onSnapshot` listeners per panel** — each panel scoped to its own query; `docChanges()` for surgical re-renders. MVP: dashboard TBD. Source: `Dashboard-UI.md`.
- `[Post-MVP]` **Exception-age SLA coloring** — green/yellow/red based on age; oldest first. Source: `Dashboard-UI.md`.
- `[Post-MVP]` **Audit trail viewer with `correlation_id` search** — full chain reconstruction for any order. MVP: —. Post-hackathon: couples with §13. Source: `Dashboard-UI.md`, `Security-Audit.md`.
- `[Post-MVP]` **Before/after metrics comparison panel** — hardcoded Glacis baselines vs live measured metrics. The demo-closer visual. Source: `Dashboard-UI.md`.
- `[Post-MVP]` **Firebase Auth + custom-claim RBAC** — `operator` / `admin` / `auditor` roles via Firebase Auth custom claims; security rules read `request.auth.token.role`. MVP: —. Source: `Security-Audit.md`.
- `[Post-MVP]` **Cursor-based pagination on audit viewer** — `startAfter` not offset; O(1) regardless of collection size. Source: `Dashboard-UI.md`.
- `[Nice-to-have]` **Inline-edit grid for exception detail modal** — fix the extracted field, re-run validation. Source: `Dashboard-UI.md`.
- `[Nice-to-have]` **Supplier-portal tempation — DON'T** — dashboard is *internal only*. External parties interact via email. Explicit Anti-Portal. Source: `Anti-Portal-Design.md`, `Dashboard-UI.md`.

---

## 11. Learning Loop

> Glacis spec: every human correction becomes a candidate memory → backtest against 50-200 historical cases → promote to active if zero regressions → track application stats → graduate to structured SOP rule after 100+ applications at >95% accuracy.

- `[Post-MVP]` **Correction capture — `decision_log` writes on every human override** — structured event with `original_action` + `corrected_action` + `reason_text` + `context` + `entities`. MVP: —. Post-hackathon: day-one foundation; cheap to implement, valuable data from day one. Source: `Learning-Loop.md`.
- `[Post-MVP]` **Candidate memory generation from corrections** — LLM drafts the plain-English rule from the delta between original and corrected action. MVP: —. Source: `Learning-Loop.md`, `SOP-Playbook.md`.
- `[Post-MVP]` **Memory retrieval into agent context** — at validation time, pull memories scoped to `customer_id + stage` and inject into the LLM prompt. MVP: —. Post-hackathon: Hackathon Week-3 version of this was "scope-scoped retrieval"; the cheap version ships in a day. Source: `Learning-Loop.md`.
- `[Post-MVP]` **Backtest engine — replay candidate memory against last N historical cases** — Cloud Run job triggered by `memories onCreate`. Score improved/neutral/degraded. Gate: zero degraded, ≥1 improved. MVP: —. Post-hackathon: the non-negotiable feature per Pallet — "prevent unintended accuracy regression". Source: `Learning-Loop.md`.
- `[Post-MVP]` **Memory confidence ladder** — `candidate → backtested → active (medium) → active (high, 30+ apps @ >90%) → rule (100+ apps @ >95%, multi-entity)`. MVP: —. Source: `Learning-Loop.md`.
- `[Post-MVP]` **Deprecation — auto-disable memories with <70% accuracy over rolling 30 days** — prevents stale tribal knowledge from persisting. MVP: —. Source: `Learning-Loop.md`.
- `[Post-MVP]` **Graduation — memory → structured SOP rule after sustained high-confidence use** — baked into base prompt rather than dynamically retrieved. MVP: —. Source: `Learning-Loop.md`, `SOP-Playbook.md`.
- `[Nice-to-have]` **Multi-model redundancy for high-value decisions** — run memory-influenced decision through Gemini Pro + Flash; flag disagreement. Only for orders above value threshold. Source: `Learning-Loop.md`.
- `[Nice-to-have]` **Automated SOP-doc ingestion** — LLM extracts discrete rules from existing 20-page SOP PDFs; human-reviewed before activation. Bootstraps memory layer for new enterprise. Source: `SOP-Playbook.md`.

---

## 12. Orchestration (ADK)

> Glacis spec: ADK SequentialAgent wiring Classification → Extraction → Validation → Routing → Act. Tool-per-stage. Sessions in Firestore. `adk deploy cloud_run` for one-command deploy.

- `[MVP ✓]` **ADK `SequentialAgent` replacing the stub** — `backend/my_agent/agent.py` now exports `build_root_agent(*, kwarg-only deps)` (pure factory) + `_build_default_root_agent()` (constructs real deps — shared async Firestore client, MasterDataRepo, OrderValidator, Firestore stores, IntakeCoordinator with `agent_version="track-a-v0.2"`, all three LlmAgent factories: clarify/confirmation/summary) + module-level `root_agent: SequentialAgent` discovered at import time by `adk web`. `ROOT_AGENT_NAME = "order_intake_pipeline"`. Topology test at `tests/unit/test_orchestrator_build.py` pins name + canonical 9-stage order + subclass types + kwarg-only discipline + distinct-instance-per-call. MVP: 847d6eb (initial root_agent) + f5db946 (ConfirmStage + AGENT_VERSION bump). Source: `ADK-Order-Intake.md`.
- `[MVP ✓]` **Stage-per-subagent wiring** — all 9 BaseAgent stages on master with per-stage unit tests (7-9 each) + end-to-end integration test via `Runner.run_async` against the Firestore emulator. Canonical order (load-bearing for adk web traces + evalsets): IngestStage (63780e9) → ReplyShortCircuitStage (2566376) → ClassifyStage (18ce553) → ParseStage (6ba10e8) → ValidateStage (1ad3bd2) → ClarifyStage (b33a030, first child-LlmAgent) → PersistStage (6f75572) → **ConfirmStage (6344a83, second child-LlmAgent, AUTO-leg customer email)** → FinalizeStage (6eed197, third child-LlmAgent). Shared test helper at `tests/unit/_stage_testing.py` exposes `make_stage_ctx`, `collect_events`, `final_state_delta`, `FakeChildLlmAgent` (parameterized duck-typed fake that survives `SequentialAgent.model_copy` through the real Runner — proven in Step 6). Dep-injection pattern: `PrivateAttr` for all deps (Protocol / concrete / Callable / LlmAgent-as-Any — uniform template). MVP: Track A Steps 4a-4h across 8 commits + ConfirmStage plan Tasks 1-9 across 2026-04-23/24. Source: `ADK-Order-Intake.md`.
- `[Post-MVP]` **Firestore-backed ADK sessions** — `session_service_uri="firestore://"` in `get_fast_api_app`. MVP: deferred to post-sprint Memory-as-a-Service track per user direction (2026-04-22). The persistent ledger (`OrderStore` / `ExceptionStore`) handles business-record durability; ADK sessions are reasoning-trace state, which `InMemorySessionService` covers for the demo. Post-hackathon: likely `VertexAiMemoryBankService` behind a thin service interface, fed by completed orders + human corrections. Source: `ADK-Order-Intake.md`, `Deployment.md`, `Firebase-Init-Decisions.md`.
- `[Post-MVP]` **ADK dev UI (`--with_ui`) for debugging** — served at root during dev; replaced by custom dashboard in prod. MVP: —. Post-hackathon: minor quality-of-life. Source: `Deployment.md`.
- `[Post-MVP]` **Tool definitions per validator** — each of the 6 validator tools exposed as ADK tools so the agent can call them by name with typed args. MVP: tools exist as pure Python; not yet wrapped. Post-hackathon: enables the LLM to dynamically choose which validation to run based on context. Source: `ADK-Order-Intake.md`.
- `[Post-MVP]` **Parallel subagent execution for independent validation checks** — `ParallelAgent` wrapping the independent checks from §4. Source: `ADK-Order-Intake.md`.
- `[Post-MVP]` **Callback hooks for audit logging** — ADK `before_tool_call` / `after_tool_call` callbacks write to `audit_log` automatically. MVP: —. Post-hackathon: this is how the audit trail becomes automatic rather than discipline-dependent. Source: `ADK-Order-Intake.md`, `Security-Audit.md`.
- `[Post-MVP]` **Graceful handling of LLM-returns-malformed-JSON** — retry with error feedback, then fall through to escalate. MVP: —. Source: `ADK-Order-Intake.md`.
- `[Nice-to-have]` **Coordinator agent dispatching Order Intake vs PO Confirmation** — classifier-first architecture. Only meaningful once PO Confirmation exists. Source: `ADK-Order-Intake.md`.

---

## 13. Eval & Observability

> Glacis spec: `adk eval` with golden-file evalsets per scenario; metrics dashboard (touchless rate, processing time, exception breakdown, per-type override rate); Cloud Trace + prompt-response logging.

- `[MVP ⚠]` **`adk eval` + 3 golden evalsets (Track E)** — Track A Step 7 shipped a 3-case smoke evalset (`tests/eval/smoke.evalset.json` — patterson AUTO / redline AUTO-or-CLARIFY / birch_valley reply) + `eval_config.json` with loose thresholds (0.3) + `tests/eval/fixtures/seed_birch_valley_exception.py` idempotent seed helper + `tests/eval/README.md` operator runbook. CLARIFY-band and ESCALATE cases deferred pending a live validator-discovery run. Track E's goal is to expand to the full 3-scenario golden set, tighten thresholds, and pick the CLARIFY/ESCALATE fixtures. MVP smoke ✓ landed (cdfa7f7 + 59d4f84); golden-set expansion is the residual Track E work. Source: `Overview.md` (eval), `ADK-Order-Intake.md`.
- `[MVP ✓]` **`audit_log` collection — append-only Firestore collection** — every agent action across the 9-stage pipeline: `stage_entered` / `stage_exited` per stage (9 × 2 = 18/run) plus `envelope_received` / `routing_decided` / `order_persisted` | `exception_opened` | `duplicate_seen` / `email_drafted` / `run_finalized` lifecycle events. Written via `AuditLogger` fail-open emitter from `backend/audit/logger.py`. Immutable via security rules (`allow update, delete: if false` on `/audit_log/{doc}`). 3 composite indexes on (correlation_id, ts), (source_message_id, ts), (stage, action, ts). Fail-open on write errors (MVP call; Phase 2 hardens to fail-closed). MVP: Track D landed 2026-04-24 across commits `5428bb3` → `bbc1201`. Source: `Security-Audit.md`, `ERP-Integration.md`.
- `[MVP ✓]` **`session_id` + `correlation_id` on every audit event** — `correlation_id` is fresh UUID4 per pipeline invocation, minted by `IngestStage` as its first business-logic act, threaded through `ctx.session.state["correlation_id"]` for all downstream stages. `session_id` from `ctx.session.id`. Query `audit_log.where("correlation_id", "==", X).order_by("ts")` reconstructs the full decision chain for one run; `audit_log.where("source_message_id", "==", X).order_by("ts")` reconstructs all retries of one envelope. Source: `Security-Audit.md`.
- `[Post-MVP]` **Daily metrics Cloud Function** — touchless rate, avg processing time, exception breakdown by type, override rate by type. Writes to `daily_metrics/{date}`. MVP: —. Source: `Metrics-Dashboard.md`, `Dashboard-UI.md`.
- `[Post-MVP]` **Cloud Trace integration for LLM spans** — trace every Gemini call with token count, latency, cost; enables per-request cost attribution. MVP: —. Source: `Token-Optimization.md`.
- `[Post-MVP]` **Prompt-response logging for eval** — store raw Gemini inputs/outputs (by reference to GCS for large payloads) so you can replay evals after prompt changes. MVP: —. Source: `Token-Optimization.md`.
- `[Post-MVP]` **Token-cost tracking per order** — running total of `$/order` by stage; the Glacis target is $1.77-5/order. MVP: —. Source: `Token-Optimization.md`.
- `[Post-MVP]` **Override-rate alerting** — when a memory or rule's override rate exceeds 20% over rolling 30 days, alert. Indicator that the rule is broken. MVP: —. Source: `Exception-Handling.md`, `Learning-Loop.md`.
- `[Nice-to-have]` **BigQuery export of audit log + metrics** — for longitudinal analytics beyond Firestore's query limits. Source: `Security-Audit.md`.
- `[Nice-to-have]` **Expected Calibration Error (ECE) tracking** — does the confidence score match observed accuracy? Drives threshold recalibration. Source: `Exception-Handling.md`.

---

## 14. Deployment & Security

> Glacis spec: Cloud Run (scale-to-zero, single-worker) + Firebase Hosting + Pub/Sub + Cloud Scheduler + Secret Manager. SOC-2-style audit trail. CMEK for sensitive data. Kill switch at `config/agent_status.paused`.

- `[MVP ⚠]` **`adk deploy cloud_run` for agent (Track A)** — one-command deploy with `--with_ui`; falls back to manual Dockerfile if it breaks. MVP: planned. Source: `Deployment.md`.
- `[MVP ✓]` **Firestore emulator setup** — local-first development; `firebase.json` / `.firebaserc` + emulator-seeded master data + `firestore_emulator` pytest marker + composite index for `find_pending_clarify` all landed on master. Source: `Deployment.md`.
- `[Post-MVP]` **Firebase Hosting deployment for dashboard** — static SPA; `firebase.json` rewrites `/api/**` → Cloud Run service. MVP: dashboard deploy TBD. Source: `Deployment.md`.
- `[Post-MVP]` **Secret Manager for Gmail OAuth + Gemini API key** — `--set-secrets=GMAIL_OAUTH_TOKEN=...:latest`. Never in env vars. MVP: —. Source: `Deployment.md`, `Security-Audit.md`.
- `[Post-MVP]` **Cloud Scheduler cron for clarify-reply timeout sweep** — every N minutes, find unresolved clarify-awaiting exceptions past SLA, escalate. MVP: —. Source: `Event-Architecture.md`.
- `[Post-MVP]` **Cloud Run config: `--min-instances=0 --max-instances=3 --timeout=300`** — scale-to-zero for cost; cap on runaway; 5min timeout for large docs. MVP: —. Source: `Deployment.md`.
- `[Post-MVP]` **Vertex AI for Gemini (not AI Studio) in Cloud Run** — service-account auth, no API key to rotate. MVP: —. Source: `Deployment.md`.
- `[MVP ✓]` **Firestore security rules — append-only audit log** — `allow update, delete: if false` on `audit_log/{id}` landed in `firebase/firestore.rules` (Track D Task 10, commit `5bdf353`). Authed read + authed create; deny update/delete. Emulator default admin mode bypasses rules in dev; Phase 2 flips test harness to use authed client so immutability is exercised end-to-end in CI. Source: `Security-Audit.md`, `Firestore-Schema.md`.
- `[Post-MVP]` **Firebase Auth custom-claim RBAC (operator / admin / auditor)** — enforce role in security rules, not client code. MVP: —. Source: `Security-Audit.md`.
- `[Post-MVP]` **Kill switch — `config/agent_status.paused` check at pipeline entry** — single-click pause, sub-second, audit-logged. MVP: —. Post-hackathon: the enterprise-buyer-confidence feature. Source: `Security-Audit.md`.
- `[Post-MVP]` **Per-stage service accounts (least privilege)** — extraction SA reads email + products only; writer SA writes orders + exceptions only; no SA can modify SOPs. MVP: —. Source: `Security-Audit.md`.
- `[Post-MVP]` **GitHub Actions CI/CD with Workload Identity Federation** — no service-account key files in secrets. MVP: —. Source: `Deployment.md`.
- `[Nice-to-have]` **CMEK (Customer-Managed Encryption Keys)** — customer holds the key in Cloud KMS. Enterprise tier only. Source: `Security-Audit.md`.
- `[Nice-to-have]` **VPC Service Controls + ingress restrictions on Cloud Run** — internal-only endpoints. Source: `Security-Audit.md`.
- `[Nice-to-have]` **SOC 2 Type II attestation** — 5.5-17.5 months of preparation. Not a feature; a process. Source: `Security-Audit.md`.
- `[Nice-to-have]` **Prompt-injection mitigation** — input sanitization + output validation as defense-in-depth around the extraction LLM. Source: `Security-Audit.md`.
- `[Nice-to-have]` **GDPR erasure compatibility with append-only audit** — audit entries reference order IDs, not raw personal data; order docs can be anonymized without breaking audit trail. Source: `Security-Audit.md`.

---

## Post-hackathon phase roadmap

The `[Post-MVP]` items above group into three coherent milestones. Order is dependency-driven: Phase 2 unlocks the Glacis demo metrics (touchless rate, processing cost, audit), Phase 3 makes the system self-improving, Phase 4 makes it sellable.

### Phase 2 — "Close the Glacis validation + persistence gaps" (~4-6 weeks post-demo)

**Goal:** match Glacis's 7-check validation pipeline and audit-trail claims.

- Duplicate / credit / inventory / delivery / address checks (§4)
- Inventory collection + seeded credit fields + `ordering_patterns` baselines (§7)
- Batch atomic write: `audit_log` + `orders` / `exceptions` (§8, §13)
- Append-only `audit_log` with security rules enforcing immutability (§13, §14)
- `correlation_id` + `session_id` on every event (§13)
- Kill switch (§14)
- Secret Manager for Gmail + Gemini creds (§14)

### Phase 3 — "Make it self-improving" (~8-12 weeks post-demo)

**Goal:** match Glacis's "manage by exception" + learning-loop promise.

- Gmail push ingestion: `watch()`, Pub/Sub webhook, History API sync, thread tracking (§1)
- Tier 3 embedding search with `text-embedding-004` + alias learning from corrections (§5, §11)
- Gmail send + Gemini quality-gate review for outbound clarify + confirmation bodies (§9 — generation + clarify-reply correlation already landed MVP; only the Gmail-send side + judge remain)
- Decision-cockpit dashboard with one-click resolution + pre-aggregated KPIs + audit viewer (§10)
- Correction capture + backtest engine + memory retrieval + confidence ladder (§11)
- ADK `SequentialAgent` with tool-per-stage + callback-driven audit logging (§12)
- Per-type / per-customer thresholds via `sop_rules` collection (§6)
- Daily metrics Cloud Function + Cloud Trace + token-cost tracking (§13)

### Phase 4 — "Enterprise-ready polish" (scope-driven; pilot-customer dependent)

**Goal:** pass enterprise procurement / security review.

- Per-stage service accounts + Firebase Auth RBAC (§14)
- CMEK + VPC-SC + ingress restrictions (§14)
- Real ERP cache-sync + write-back adapter (SAP / Oracle / Dynamics 365) (§7, §8)
- Memory graduation → SOP rule promotion pipeline (§11)
- Automated SOP-doc ingestion for enterprise onboarding (§11)
- Multi-inbox + CC-forwarding deployment model (§1)
- Multi-language clarify emails (§9)
- SOC 2 Type II attestation process (§14)

Generator-Judge quality gate (§4, §9) is a cross-cutting add — most naturally lands in Phase 3 alongside clarify email generation.

---

## Connections

- `research/Order-Intake-Sprint-Status.md` — authoritative "what's built now" view; cross-reference for every `[MVP ✓]` / `[MVP ⚠]` tag above.
- `research/Order-Intake-Sprint-Decisions.md` — authoritative "what got cut" cut-list; cross-reference for every `[Post-MVP]` tag.
- `research/Order-Intake-Sprint-Worktrees.md` — dependency graph for in-sprint tracks referenced as `[MVP ⚠]` above.
- `research/Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Overview.md` — the spec entry point; 27-note map.
- `research/Firebase-Init-Decisions.md` — why Firestore + emulator-first, not ADK Sessions/Memory.
- `CLAUDE.md` — project-level guidance including the demo-driven-scope constraint that shaped the MVP cut-list.
