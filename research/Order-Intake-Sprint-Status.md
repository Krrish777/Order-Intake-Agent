---
type: sprint-status
topic: "Order Intake Agent — Status vs Glacis Spec"
sprint: 1
parent: "Order-Intake-Sprint-Overview.md"
date: 2026-04-20
last_updated: 2026-04-20 (sessions: Firestore init + master-data load; ingestion CLI + envelope contract + 4 format wrappers + reply pair)
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
| **2c. Validation** | SKU + price + quantity + credit + inventory + delivery + duplicate | — | **Track V**: 3-tier SKU matcher + price tolerance + qty sanity. Drop credit/inventory/delivery/duplicate for sprint. |
| **2d. Enrichment (item matching)** | Exact → fuzzy → embedding | — | Part of Track V (inside SKU matcher). |
| **3. Decision layer** | Auto ≥0.95 / Clarify 0.80–0.95 / Escalate <0.80 | — | Part of Track V (`scorer.py`). |
| **4a. ERP write** | Firestore write | Emulator live; `products` (35) + `customers` (10) + `meta/master_data` seeded ✓ | **Track P** — write adapter for `orders` / `exceptions` collections. ADK Sessions/Memory research collapsed: decided on Firestore directly (see `Firebase-Init-Decisions.md`). |
| **4b. Clarify email** | Gemini-generated email asking for missing fields | — | Part of Track A (router stage). |
| **4c. Human dashboard** | Firestore real-time + approve/reject/edit | — | **Track D** — read-only list + exception view. Approve/reject deferred. |
| **Orchestration** | ADK SequentialAgent wiring stages | Stub `backend/my_agent/agent.py` ⚠ | **Track A**: replace stub with real SequentialAgent. |
| **5. Learning loop** | Corrections update SOP rules in Firestore | — | Deferred entirely per cut-list. |
| **Eval / quality gate** | (implicit in spec) | — | **Track E**: `adk eval` + 3 golden-file evalsets. |
| **Deploy** | Cloud Run + Firebase Hosting | — | `adk deploy cloud_run` for agent (inside Track A). Dashboard deploy TBD. |
| **Demo** | 2-min video, 3+ scenarios | Fixtures exist ✓ | **Track Demo**: `scripts/run_demo.py` runs 3 fixtures. |

## One-line summary

**We've built the "understanding" half of the pipeline plus the ingestion layer and persistence substrate** — classify + extract + typed output + master data + realistic fixtures + **envelope contract + inject CLI** + **Firestore emulator with seeded master data**.

**Most of the "judgment and action" half is still open** — validate, decide, route, persist writes, orchestrate, surface, eval, demo.

That's **6 worktrees** of work left (was 8 — `feat/inject-cli` shipped 2026-04-20; `research/adk-session-memory` collapsed into direct-Firestore same day). Roughly **~50% of total code lines are done**, but the 50% left is the part that turns a parser into an agent.

## Built-vs-missing inventory

### Built (do not rebuild)

```
data/masters/{products,customers}.json                                  ✓
data/{csv,edi,email,excel,pdf}/                                         ✓ fixtures
data/pdf/redline_urgent_2026-04-19.{body.txt,wrapper.eml}               ✓ PDF wrapper (2026-04-20)
data/csv/ohio_valley_reorder_2026-04-08.{body.txt,wrapper.eml}          ✓ CSV wrapper (2026-04-20)
data/excel/hagan_reorder_2026-04-09.{body.txt,wrapper.eml}              ✓ XLSX wrapper (2026-04-20)
data/edi/glfp_850_GRR_202604211002.{body.txt,wrapper.eml}               ✓ EDI wrapper (2026-04-20)
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
tests/unit/test_document_classifier.py                                  ✓
tests/unit/test_eml_parser.py                                           ✓ 26 tests over all .eml fixtures (2026-04-20)
```

### Missing (this sprint's work, mapped to branches)

```
backend/tools/order_validator/          → feat/validation
backend/models/validation_result.py     → feat/validation (integration contract)
backend/models/order_record.py          → contracts commit on master
backend/models/exception_record.py      → contracts commit on master
backend/persistence/                    → feat/persistence (Firestore direct; no ADK research gating)
backend/my_agent/agent.py (rewrite)     → feat/agent-orchestration
backend/my_agent/stages/                → feat/agent-orchestration
scripts/run_demo.py                     → feat/demo-script
scripts/run_eval.py                     → feat/eval
tests/eval/*.evalset.json               → feat/eval
frontend/                               → feat/dashboard
```

**Not blocking — iterative follow-up:** 6 remaining non-`.eml` fixtures still need wrappers (`data/{pdf,csv,excel,edi}/*.wrapper.eml` for: sterling pdf, patterson pdf; patterson_adhoc csv; glfp_weekly xlsx, ohio_valley_march xlsx; patterson edi). Pattern established via 2026-04-20 session — one per turn, `body.txt` + `scripts/scaffold_wrapper_eml.py` + review.

## What to build first

Two parallel branches, no deps between them — start both in separate worktrees:

1. **`feat/validation`** — pure function, fastest unit-test loop, highest signal for demo. **Now unblocked**: master data is queryable from Firestore emulator.
2. **`feat/eval`** — tests authored alongside the 3 demo scenarios.

~~`research/adk-session-memory`~~ — **collapsed 2026-04-20**: decided to use Firestore directly as the ERP substitute (spec-accurate) rather than ADK Sessions/Memory. See `Firebase-Init-Decisions.md` for the full rationale.

Once those two merge to master, everything else cascades per the dependency graph in [Order-Intake-Sprint-Worktrees](Order-Intake-Sprint-Worktrees.md).

## Before forking

Land a single contracts commit on `master` first — every branch imports these types:

- `EmailEnvelope`, `EmailAttachment` → `backend/ingestion/email_envelope.py` **✓ already on master (2026-04-20)**
- `ValidationResult`, `RoutingDecision` → `backend/models/validation_result.py`
- `OrderRecord` → `backend/models/order_record.py` (must carry `source_message_id` + `thread_id` for idempotency and clarify correlation)
- `ExceptionRecord` → `backend/models/exception_record.py` (three IDs across its lifecycle: `source_message_id`, `clarify_message_id`, `reply_message_id`)
- `OrderStore`, `ExceptionStore` protocols → `backend/persistence/base.py` (include `find_pending_clarify(thread_id)` on `ExceptionStore` — clarify-reply loop correlates via thread)
- `MasterDataIndex` loader → `backend/data/master_index.py`

The envelope pair is already shipped. The remaining five are the only real coordination point across worktrees.

## Connections

- [Order-Intake-Sprint-Overview](Order-Intake-Sprint-Overview.md)
- [Order-Intake-Sprint-Worktrees](Order-Intake-Sprint-Worktrees.md) — full dependency graph and per-track contracts
- [Order-Intake-Sprint-Theory-vs-Practice](Order-Intake-Sprint-Theory-vs-Practice.md) — why "what we have" differs from "what spec says" in rows 2a, 2b
- [Order-Intake-Sprint-Verification](Order-Intake-Sprint-Verification.md) — what "done" looks like per track
- [Firebase-Init-Decisions](Firebase-Init-Decisions.md) — Firestore-only stack, emulator-first, `demo-order-intake-local` project ID
- `research/Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Overview.md` — the spec this status measures against
