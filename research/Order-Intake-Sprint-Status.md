---
type: sprint-status
topic: "Order Intake Agent — Status vs Glacis Spec"
sprint: 1
parent: "Order-Intake-Sprint-Overview.md"
date: 2026-04-20
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
| **1. Signal ingestion** | Gmail watch → Pub/Sub → attachment download | Fixture corpus in `data/{csv,edi,email,excel,pdf}/` ✓ | Inject-CLI (`scripts/inject_email.py`). Gmail API deferred to later sprint. |
| **2a. Classification** | LLM classifier (intent) + rules (format) | `backend/tools/document_classifier/` — LlamaClassify intent + deterministic format ✓ | Nothing. Done. |
| **2b. Extraction** | Gemini multimodal → structured JSON | `backend/tools/document_parser/` — LlamaExtract → `ParsedDocument` ✓ | Nothing. Done. |
| **2c. Validation** | SKU + price + quantity + credit + inventory + delivery + duplicate | — | **Track V**: 3-tier SKU matcher + price tolerance + qty sanity. Drop credit/inventory/delivery/duplicate for sprint. |
| **2d. Enrichment (item matching)** | Exact → fuzzy → embedding | — | Part of Track V (inside SKU matcher). |
| **3. Decision layer** | Auto ≥0.95 / Clarify 0.80–0.95 / Escalate <0.80 | — | Part of Track V (`scorer.py`). |
| **4a. ERP write** | Firestore write | — (no persistence adapter yet) | **Track P** after ADK research (Track R) decides Sessions/Memory vs Firestore. |
| **4b. Clarify email** | Gemini-generated email asking for missing fields | — | Part of Track A (router stage). |
| **4c. Human dashboard** | Firestore real-time + approve/reject/edit | — | **Track D** — read-only list + exception view. Approve/reject deferred. |
| **Orchestration** | ADK SequentialAgent wiring stages | Stub `backend/my_agent/agent.py` ⚠ | **Track A**: replace stub with real SequentialAgent. |
| **5. Learning loop** | Corrections update SOP rules in Firestore | — | Deferred entirely per cut-list. |
| **Eval / quality gate** | (implicit in spec) | — | **Track E**: `adk eval` + 3 golden-file evalsets. |
| **Deploy** | Cloud Run + Firebase Hosting | — | `adk deploy cloud_run` for agent (inside Track A). Dashboard deploy TBD. |
| **Demo** | 2-min video, 3+ scenarios | Fixtures exist ✓ | **Track Demo**: `scripts/run_demo.py` runs 3 fixtures. |

## One-line summary

**We've built the "understanding" half of the pipeline** — classify + extract + typed output + master data + realistic fixtures.

**Nothing of the "judgment and action" half exists yet** — validate, route, persist, orchestrate, surface, eval, demo.

That's 8 worktrees of work left. Roughly **~40% of total code lines are done**, but the 60% left is the part that turns a parser into an agent.

## Built-vs-missing inventory

### Built (do not rebuild)

```
data/masters/{products,customers}.json           ✓
data/{csv,edi,email,excel,pdf}/                  ✓ fixtures
backend/models/classified_document.py            ✓
backend/models/parsed_document.py                ✓
backend/models/ground_truth.py                   ✓
backend/prompts/{document_classifier,document_parser}.py  ✓
backend/tools/document_classifier/               ✓
backend/tools/document_parser/ (legacy/)         ✓
backend/exceptions.py                            ✓
backend/utils/ (logging)                         ✓
scripts/classify_file.py, classify_folder.py     ✓
tests/unit/test_document_classifier.py           ✓
```

### Missing (this sprint's work, mapped to branches)

```
backend/tools/order_validator/          → feat/validation
backend/models/validation_result.py     → feat/validation (integration contract)
backend/models/order_record.py          → contracts commit on master
backend/models/exception_record.py      → contracts commit on master
backend/persistence/                    → feat/persistence (after research/adk-session-memory)
backend/my_agent/agent.py (rewrite)     → feat/agent-orchestration
backend/my_agent/stages/                → feat/agent-orchestration
scripts/inject_email.py                 → feat/inject-cli
scripts/run_demo.py                     → feat/demo-script
scripts/run_eval.py                     → feat/eval
tests/eval/*.evalset.json               → feat/eval
frontend/                               → feat/dashboard
research/ADK-Order-Intake-Decisions.md  → research/adk-session-memory
```

## What to build first

Three parallel branches, no deps between them — start all three in separate worktrees:

1. **`feat/validation`** — pure function, fastest unit-test loop, highest signal for demo
2. **`research/adk-session-memory`** — doc-only, unblocks persistence choice
3. **`feat/eval`** — tests authored alongside the 3 demo scenarios

Once those three merge to master, everything else cascades per the dependency graph in [Order-Intake-Sprint-Worktrees](Order-Intake-Sprint-Worktrees.md).

## Before forking

Land a single contracts commit on `master` first — every branch imports these types:

- `ValidationResult`, `RoutingDecision` → `backend/models/validation_result.py`
- `OrderRecord` → `backend/models/order_record.py`
- `ExceptionRecord` → `backend/models/exception_record.py`
- `OrderStore`, `ExceptionStore` protocols → `backend/persistence/base.py`
- `MasterDataIndex` loader → `backend/data/master_index.py`

That commit is the only real coordination point across worktrees.

## Connections

- [Order-Intake-Sprint-Overview](Order-Intake-Sprint-Overview.md)
- [Order-Intake-Sprint-Worktrees](Order-Intake-Sprint-Worktrees.md) — full dependency graph and per-track contracts
- [Order-Intake-Sprint-Theory-vs-Practice](Order-Intake-Sprint-Theory-vs-Practice.md) — why "what we have" differs from "what spec says" in rows 2a, 2b
- [Order-Intake-Sprint-Verification](Order-Intake-Sprint-Verification.md) — what "done" looks like per track
- `research/Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Overview.md` — the spec this status measures against
