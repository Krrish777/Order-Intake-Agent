---
type: sprint-verification
topic: "Order Intake Agent — Verification & Demo Plan"
sprint: 1
parent: "Order-Intake-Sprint-Overview.md"
date: 2026-04-20
tags:
  - sprint
  - verification
  - evaluation
  - demo
  - testing
---

# Order Intake Agent — Verification & Demo Plan

Two layers of verification:

1. **Per-worktree acceptance** — a worktree merges back to `master` only when its local tests pass with no shared-state dependency.
2. **End-to-end acceptance** — after the final worktree merges, the full pipeline passes the evalset regression gate and the demo runs cleanly on three fixtures.

## Per-worktree acceptance

Each worktree has its own "green" criteria. Do not merge a worktree that does not pass.

### Track R — ADK research note
- **Green when:** `research/ADK-Order-Intake-Decisions.md` exists on branch and clearly answers:
  - Which ADK primitive holds order/exception state (Sessions, Memory, or external Firestore)?
  - What is the exact session-state shape the SequentialAgent passes between stages?
  - What is the evalset schema (input format, expected-output format)?
  - What does `adk deploy cloud_run` need from our repo (entry point, requirements, secrets)?
- **Not green if:** any of the above is left as "TBD".

### Track V — Validation pipeline
- **Green when:** `pytest tests/unit/test_validator.py` passes with at minimum these cases:
  - Clean input (all SKUs match exactly, prices within tolerance, quantities sane) → `routing_decision == "auto_execute"`, `confidence_score >= 0.95`
  - Fuzzy-match case (SKU description like `Dark Roast 5lb bag` → matches `SKU-COF-DR-05LB` via fuzzy tier) → `routing_decision == "auto_execute"`
  - Embedding-match case (semantic alias that fuzzy misses) → `routing_decision == "auto_execute"`
  - Price-outlier case (unit_price 50% off master) → `routing_decision == "escalate"`, flagged in `ValidationResult.flags`
  - Quantity-outlier case → `routing_decision == "escalate"`
  - Unknown-SKU case (no match in any tier) → `routing_decision == "escalate"`
  - Missing required field (no ship_to_address) → `routing_decision == "clarify"`, `missing_fields` populated

### Track E — Eval harness
- **Green when:** the three evalsets load, and `scripts/run_eval.py` can execute them against a dummy agent that returns hand-crafted ValidationResults (proving the harness plumbing works before Track A exists).

### Track P — Persistence adapter
- **Green when:** round-trip test passes:
  1. `save_order(order_record)` returns an ID
  2. `get_order(id)` returns the same record
  3. `list_orders()` includes it
  4. Same for `ExceptionStore`

### Track A — ADK orchestration
- **Green when:**
  - `adk eval tests/eval/*.evalset.json` passes all three evalsets
  - A manual run via Track I's CLI on each of the three fixtures produces the expected routing decision
  - ADK traces show all four stages firing in order

### Track I — Fixture injection CLI
- **Green when:** `python scripts/inject_email.py data/pdf/<any fixture>.pdf` prints a final session state JSON that includes `validation_result` and `routing_decision` fields.

### Track D — Dashboard
- **Green when:**
  - Seed the persistence layer with 3 orders + 1 exception
  - Open the dashboard, see all 3 orders in the list
  - Click the exception, see its detail view with validation flags

### Track Demo — Demo scenario runner
- **Green when:** `python scripts/run_demo.py` runs start-to-finish without human intervention, leaving 2 auto-executed orders, 1 clarify draft, and 1 exception in persistence, and the dashboard shows them.

## End-to-end acceptance (sprint done criteria)

All four must be true:

1. **Golden-file regression gate**
   - Command: `adk eval tests/eval/*.evalset.json`
   - Must pass all three evalsets without flakes (run twice, same result).

2. **Demo scenarios run clean**
   - Command: `python scripts/run_demo.py`
   - Expected output: three fixtures injected, three distinct routing outcomes (auto, clarify, escalate), persistence populated, dashboard updated.

3. **Dashboard renders correctly**
   - Open the dashboard after demo run.
   - Order list shows 2 auto-executed orders.
   - Exception detail view shows the escalated case with validation flags visible.

4. **Stretch — `adk deploy` works**
   - Command: `adk deploy cloud_run`
   - Agent reachable at the deploy URL; one injection via the deployed agent returns the expected final state.
   - Stretch because deploy issues can eat days; don't block sprint completion on this.

## Evalset structure (golden files)

Each `tests/eval/*.evalset.json` follows the ADK eval format (see `adk-eval-guide` skill for exact schema). Minimum fields per case:

```json
{
  "eval_id": "clean_auto_pdf_001",
  "input": {
    "filename": "order_001.pdf",
    "content_ref": "data/pdf/order_001.pdf"
  },
  "expected": {
    "classification": "purchase_order",
    "line_item_count": 3,
    "all_skus_matched": true,
    "routing_decision": "auto_execute",
    "confidence_score_min": 0.95
  },
  "tool_trajectory": [
    "classify_document",
    "parse_document",
    "validate_order",
    "persist_order"
  ]
}
```

The three evalsets map 1:1 to the three demo scenarios:

| Evalset | Fixture | Expected routing | What it proves |
|---|---|---|---|
| `clean_auto.evalset.json` | PDF or PT, all-match, in tolerance | `auto_execute` | Happy path works end-to-end |
| `clarify_missing_po.evalset.json` | Email missing PO number or ship-to | `clarify` | Agent can ask for missing info |
| `exception_unknown_sku.evalset.json` | Order with SKU not in master, all matching tiers fail | `escalate` | Agent knows its own limits |

## Demo video outline (2 minutes)

The evalsets and the demo share fixtures — the video is a visual rendering of what `run_demo.py` does.

| Time | Shot | What shows |
|---|---|---|
| 0:00–0:20 | Problem hook | Pain of manual order intake (stat from Glacis: "8-15 minutes per order") |
| 0:20–0:40 | Architecture diagram | The pipeline: fixture → classify → parse → validate → route → persist → dashboard |
| 0:40–1:00 | Scenario 1 — clean auto-execute | Inject clean PDF, see dashboard populate within 10s |
| 1:00–1:20 | Scenario 2 — clarify | Inject PDF missing PO#, see clarify email draft appear |
| 1:20–1:40 | Scenario 3 — exception | Inject XLS with unknown SKU, see exception in dashboard with flags |
| 1:40–2:00 | Impact + tech stack | "60s per order. 3 Google technologies + ADK + Gemini. Ship-ready in [N] days." |

## What "done" looks like

When the sprint is done:

- `master` contains all worktrees merged.
- `adk eval tests/eval/` is green.
- `scripts/run_demo.py` runs in under 90 seconds and leaves the demo state in place.
- Either (a) the agent is deployed via `adk deploy` and reachable, or (b) the localhost demo is recorded cleanly.
- Next-sprint spec for PO Confirmation is a copy of this folder with scope changed — most of the infrastructure is reused.

## Connections

- [Order-Intake-Sprint-Overview](Order-Intake-Sprint-Overview.md)
- [Order-Intake-Sprint-Worktrees](Order-Intake-Sprint-Worktrees.md) — green criteria map to each worktree
- [Order-Intake-Sprint-Decisions](Order-Intake-Sprint-Decisions.md) — decision #19 (three demo fixtures), #20 (adk eval)
- `research/Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Demo-Scenario.md` — original demo script this narrows from
