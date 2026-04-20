---
type: sprint-decisions
topic: "Order Intake Agent — 20 Architectural Decisions"
sprint: 1
parent: "Order-Intake-Sprint-Overview.md"
date: 2026-04-20
tags:
  - sprint
  - architecture
  - decisions
---

# Order Intake Agent — 20 Architectural Decisions

Each decision was taken via a planning Q&A. Format: question → options considered → **chosen** → why → consequences.

## Ingestion and input

### 1. Email ingestion mode
**Chosen: Fixture injection only.** `scripts/inject_email.py` reads a file from `data/{pdf,email,excel,csv}/`, feeds bytes directly into the pipeline. No Gmail OAuth, no `watch()`, no Pub/Sub push.
Why: cuts ~1 day of Google-auth yak-shaving. Demo is reproducible offline. Gmail integration is a trivial replacement of the CLI's entry point — we can add it in a later sprint with no refactor to the agent pipeline.

### 2. Input formats
**Chosen: Plain text + PDF + XLS + CSV.** Drop XML / EDI for this sprint.
Why: LlamaParse/LlamaExtract already supports all four. XML/EDI add prompt-tuning work with no demo payoff (most real orders are the first four formats).

### 3. Multi-attachment policy
**Chosen: One attachment per email** — pick the first/best attachment, ignore the rest.
Why: covers 90% of realistic cases. The Heineken "one PDF → multiple orders" story is out of scope; we do "one email = one attachment = one or more line items" only.

### 4. Language scope
**Chosen: English only.**
Why: per memory rule "no LLM-slop data" — every non-English fixture needs hand-curation to hit realism. One language is enough for a Solution Challenge demo.

## Parsing and classification

### 5. Document parser engine
**Chosen: Keep LlamaParse + LlamaExtract.** Already built in `backend/tools/document_parser/legacy/parser.py`.
Why: working code. Swap to Gemini multimodal would be a regression in sprint velocity. The Glacis spec describes Gemini multimodal as theory; our practice is LlamaExtract. See [Order-Intake-Sprint-Theory-vs-Practice](Order-Intake-Sprint-Theory-vs-Practice.md).

### 6. Classifier strategy
**Already built.** The parser emits `ParsedDocument.classification` as part of its LlamaExtract job; the standalone `backend/tools/document_classifier/` adds a LlamaClassify-based classifier for the 8-label intent + deterministic format.
Why not re-decide: the existing design is sound — intent is LLM-inferred (handles ambiguous cases), format is rule-based (zero token cost).

### 7. Classification taxonomy
**Chosen: keep the existing 8-label `DocumentClassification` Literal; route only `purchase_order` downstream.** Everything else → drop or escalate.
Why: changing the Literal would break the parser schema. Binary routing at the orchestrator layer gives us "Order vs Not-order" behavior without touching typed models.

### 8. Extract schema
**Already built.** `ParsedDocument` / `ExtractedOrder` / `OrderLineItem` in `backend/models/parsed_document.py`.
Contains: header fields (customer_name, po_number, ship_to_address, requested_delivery_date, special_instructions) + line items (sku, description, quantity, unit_of_measure, unit_price, requested_date).
Why not re-decide: the fields align with Glacis `Firestore-Schema.md`. No redesign needed.

## Validation and matching

### 9. SKU matching strategy
**Chosen: 3-tier ladder** — exact code match → fuzzy string (rapidfuzz) → embedding fallback (Gemini `text-embedding-004`).
Why: demo variance. Exact alone is boring (only works on clean fixtures). Full ladder shows "agent handles `Dark Roast 5lb bag` mapping to `SKU-COF-DR-05LB`" which is exactly the Glacis wow moment.
Consequences: need to pre-compute embeddings for all master products once and cache (not re-embed per request).

### 10. Validation checks
**Chosen: three checks — SKU existence, price tolerance (±X% of master), quantity sanity (positive, below customer ceiling).**
Dropped for sprint: credit check, inventory check, delivery-date feasibility, duplicate detection.
Why: these three give us the three demo outcomes (auto / clarify / escalate). More checks → more prompt work, same demo.

### 11. Confidence thresholds
**Chosen: spec defaults** — auto ≥0.95, clarify 0.80–0.95, escalate <0.80.
Why: matches Glacis whitepaper numbers. No reason to invent new ones.
Note: thresholds are constants in code for this sprint, not Firestore-driven SOP rules (cut per `Build-Plan.md` cut-list).

### 12. Master data corpus
**Already built.** `data/masters/products.json` (~730 lines) + `data/masters/customers.json` (~550 lines) — hand-curated, realistic, matches the "no LLM-slop data" memory rule.
Why not re-decide: curation is the expensive part; it's done.

## State and orchestration

### 13. Persistence
**Chosen: deferred to an ADK research phase (Track R).** Abstract `OrderStore` / `ExceptionStore` interfaces now; concrete backend (ADK Sessions/Memory vs Firestore) chosen after reading `adk-dev-guide`, `adk-cheatsheet`, `adk-observability-guide`.
Why: ADK has first-party session + memory primitives that may replace Firestore for transactional state. Deciding before research would lock in the wrong backend. Interface-first lets every other worktree build against a mock.

### 14. Event flow between stages
**Chosen for MVP: direct function calls inside a SequentialAgent.** Revisit at build time via ADK docs.
Why: no queue, no broker, no emulator. SequentialAgent hands state through stages. Gmail→agent boundary is the only place Pub/Sub would matter and we're not doing Gmail this sprint.

### 15. ADK orchestration pattern
**Chosen for MVP: `SequentialAgent`** — Classifier → Parser → Validator → Router. Revisit at build via ADK docs.
Why: matches `Glacis-Agent-Reverse-Engineering-ADK-Order-Intake.md` exactly. Each stage is a sub-agent or FunctionTool. Gives us ADK traces for free (observability story for judges).

### 16. Routing outcomes
**Chosen: all three — auto-execute / clarify / escalate.**
- Auto-execute → persist order + generate confirmation email draft
- Clarify → generate email asking customer for missing fields
- Escalate → write exception record, route to dashboard queue

Why: three routing paths = three demo scenarios. Dropping the clarify path would gut the most interesting "agent composes a polite email" story.

## Surface and demo

### 17. Dashboard scope
**Chosen: read-only** — order list + exception detail view. No approve / reject / edit UI this sprint.
Why: proves the pipeline worked; adds UX score; avoids React form-state complexity. Approve/reject is a next-sprint enhancement.

### 18. Deploy target
**Chosen: `adk deploy` for the agent** (cloud_run target). Rest of stack (dashboard, Firestore if we use it) separate. Revisit specifics at build.
Why: ADK's one-command deploy is the path of least resistance for the agent. Dashboard deploys independently to Firebase Hosting if we go React, or is localhost-only if we skip hosting for sprint.

### 19. Demo scenario breadth
**Chosen: three fixtures** — one clean auto-execute path, one clarify-missing-field path, one exception path.
Why: covers all three routing outcomes in ~90 seconds of video. Matches `Glacis-Agent-Reverse-Engineering-Demo-Scenario.md` structure without stretching fixture curation.

### 20. Evaluation strategy
**Chosen: golden-file tests via `adk eval`** (per `adk-eval-guide` skill).
Each of the three demo fixtures has a ground-truth evalset with: input message, expected final session state, expected routing decision.
Why: `adk eval` is first-party. Evalsets serve double duty as the demo script AND the regression gate. Every worktree merge runs them; a failing eval blocks merge.

## Summary table

| # | Decision | Status |
|---|---|---|
| 1 | Fixture injection only | **chosen** |
| 2 | PT + PDF + XLS + CSV | **chosen** |
| 3 | One attachment per email | **chosen** |
| 4 | English only | **chosen** |
| 5 | Keep LlamaParse + LlamaExtract | **already built** |
| 6 | Classifier design | **already built** |
| 7 | 8-label Literal, route on `purchase_order` | **chosen** |
| 8 | ParsedDocument schema | **already built** |
| 9 | SKU 3-tier matching ladder | **chosen** |
| 10 | SKU + price + qty validation | **chosen** |
| 11 | Thresholds 0.95 / 0.80 | **chosen** |
| 12 | Master data corpus | **already built** |
| 13 | Persistence backend | **deferred to Track R** |
| 14 | In-SequentialAgent event flow | **chosen (MVP)** |
| 15 | SequentialAgent orchestration | **chosen (MVP)** |
| 16 | All three routing outcomes | **chosen** |
| 17 | Read-only dashboard | **chosen** |
| 18 | `adk deploy` for agent | **chosen** |
| 19 | Three demo fixtures | **chosen** |
| 20 | `adk eval` golden-file tests | **chosen** |

## Connections

- [Order-Intake-Sprint-Overview](Order-Intake-Sprint-Overview.md)
- [Order-Intake-Sprint-Worktrees](Order-Intake-Sprint-Worktrees.md) — maps these decisions onto worktrees
- [Order-Intake-Sprint-Theory-vs-Practice](Order-Intake-Sprint-Theory-vs-Practice.md) — details for decisions #5, #11, #13, #18
