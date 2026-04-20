---
type: sprint-decomposition
topic: "Order Intake Agent — Worktree Decomposition"
sprint: 1
parent: "Order-Intake-Sprint-Overview.md"
date: 2026-04-20
tags:
  - sprint
  - worktree
  - architecture
  - parallel-development
---

# Order Intake Agent — Worktree Decomposition

Eight worktrees. Each is a separate `git worktree add` under its own branch so you can context-switch cleanly between concerns without stepping on in-flight work.

## Principle

For a solo builder, "parallel worktrees" is **parallel context**, not parallel people. Each worktree is a sandbox where one concern lives end-to-end with its own mocks. Switch worktrees like switching mental modes. Merge each worktree back to `master` only when it is green in isolation.

Two rules enforced by this structure:
1. **Contracts first.** Integration types (Pydantic models) land on `master` before any feature worktree is forked. That eliminates ~80% of merge-conflict risk.
2. **No cross-worktree testing** until the worktree merges. Each worktree must pass its own tests with mocks for its collaborators.

## The 8 worktrees

### Phase 1 — Parallel leaves (no cross-worktree deps; start all three concurrently)

#### Track R — ADK deep-research note
- **Branch:** `research/adk-session-memory`
- **Type:** documentation-only
- **Output:** a single `research/ADK-Order-Intake-Decisions.md`
- **Covers:**
  - ADK Sessions vs Memory — which for order state, exception state, metrics
  - SequentialAgent state-passing idioms (what goes in session state vs tool return values)
  - Golden-file evalset schema (inputs, tool trajectories, expected session state)
  - `adk deploy cloud_run` config for our agent
  - Observability: what to trace, prompt-response logging strategy
- **Uses skills:** `adk-dev-guide`, `adk-cheatsheet`, `adk-eval-guide`, `adk-observability-guide`, `adk-deploy-guide`
- **Blocks:** Track P (persistence), final details in Track A
- **Time:** ~0.5 day reading + ~0.5 day writeup
- **Merge risk:** LOW — doc only

#### Track V — Validation pipeline
- **Branch:** `feat/validation`
- **New files:**
  - `backend/tools/order_validator/__init__.py`
  - `backend/tools/order_validator/sku_matcher.py` (exact → fuzzy → embedding ladder)
  - `backend/tools/order_validator/checks.py` (price tolerance, quantity sanity)
  - `backend/tools/order_validator/scorer.py` (confidence + routing decision)
  - `backend/models/validation_result.py` (new Pydantic model — integration contract)
- **Public surface:** `validate(parsed: ParsedDocument, masters: MasterDataIndex) -> ValidationResult`
- **Inputs:** `ParsedDocument` (existing, from parser) + `MasterDataIndex` loaded from `data/masters/*.json`
- **Output:** `ValidationResult` with `confidence_score`, per-check flags, `routing_decision: "auto_execute" | "clarify" | "escalate"`, `missing_fields` (for clarify path), `unmatched_skus` (for escalate path)
- **Mock for parallel dev:** build `ParsedDocument` fixtures by hand from `data/masters/`; no agent runs needed
- **Dependencies:** `rapidfuzz` (fuzzy string), `google-genai` (already in project for embeddings)
- **Merge risk:** LOW — pure function, no ADK, no persistence

#### Track E — Eval harness + golden files
- **Branch:** `feat/eval`
- **New files:**
  - `tests/eval/clean_auto.evalset.json`
  - `tests/eval/clarify_missing_po.evalset.json`
  - `tests/eval/exception_unknown_sku.evalset.json`
  - `scripts/run_eval.py` (wrapper around `adk eval`)
- **Per evalset:** input fixture filename + expected final session state + expected `routing_decision`
- **Uses skill:** `adk-eval-guide`
- **Merge risk:** LOW — tests only, no prod-code change
- **Note:** fixtures must be authored hand-in-hand with Track Demo so the evalset matches what the demo actually runs

### Phase 2 — Middle tracks (after their leaves)

#### Track P — Persistence adapter *(after R merges)*
- **Branch:** `feat/persistence`
- **New files:**
  - `backend/persistence/__init__.py`
  - `backend/persistence/base.py` — abstract `OrderStore` + `ExceptionStore` protocols
  - `backend/persistence/<backend>.py` — concrete implementation chosen by Track R (either `sessions_memory.py` or `firestore.py`)
- **Methods (interface):** `save_order`, `save_exception`, `list_orders`, `list_exceptions`, `get_order(order_id)`
- **Why interface-first:** lets Tracks A and D mock the store; the concrete backend swap at the end is a one-line import change
- **Dependencies:** what Track R decides (google-cloud-firestore OR ADK session/memory SDK)
- **Merge risk:** LOW → MED — depends on how ADK session state composes with "list all orders" queries

#### Track A — ADK agent orchestration *(after V merges; reads R)*
- **Branch:** `feat/agent-orchestration`
- **Replaces:** stub `backend/my_agent/agent.py`
- **New files:**
  - `backend/my_agent/agent.py` (rewritten — SequentialAgent root)
  - `backend/my_agent/stages/classifier_stage.py`
  - `backend/my_agent/stages/parser_stage.py`
  - `backend/my_agent/stages/validator_stage.py`
  - `backend/my_agent/stages/router_stage.py`
- **Architecture:** `SequentialAgent(sub_agents=[classifier, parser, validator, router])`
  - **ClassifierTool** — wraps existing `backend/tools/document_classifier`
  - **ParserTool** — wraps existing `backend/tools/document_parser.parse_document`
  - **ValidatorTool** — wraps Track V's `validate`
  - **RouterAgent** — `LlmAgent` that reads `ValidationResult` and executes: persist order via Track P, OR generate clarify-email draft, OR write exception record
- **State shape** (per Track R decision): session state carries `input_bytes`, `filename`, `classified_document`, `parsed_document`, `validation_result`, `routing_decision`, `side_effects` (email drafts, persisted IDs)
- **Merge risk:** MED — prompts for classifier + router need tuning; expect iteration

### Phase 3 — Surface tracks

#### Track I — Fixture injection CLI *(after A merges)*
- **Branch:** `feat/inject-cli`
- **New file:** `scripts/inject_email.py`
- **Usage:** `python scripts/inject_email.py data/pdf/order_003.pdf`
- **Behavior:** reads file bytes → constructs initial ADK session state → runs the root agent → prints final session state JSON (with Rich formatting)
- **Why separate from A:** keeps the CLI out of the agent-tuning feedback loop; easier to iterate on agent without breaking the CLI and vice versa
- **Merge risk:** LOW

#### Track D — Read-only dashboard *(after P merges; can overlap A)*
- **Branch:** `feat/dashboard`
- **New tree:** `frontend/` (Vite + React)
- **Two views:**
  - Order list: reads `OrderStore.list_orders()`, shows customer, PO #, status badge, created_at
  - Exception detail: click exception → see original classified+parsed doc, validation flags, recommended action
- **Backend wiring:** depends on Track P's concrete backend
  - If Firestore: real-time `onSnapshot` listeners
  - If ADK Sessions/Memory: short-poll against a small FastAPI wrapper (extra ~50 LOC)
- **Merge risk:** LOW → MED — backend latency is the integration point

#### Track Demo — Demo scenario runner *(last; uses everything)*
- **Branch:** `feat/demo-script`
- **New file:** `scripts/run_demo.py`
- **Behavior:** resets persistence → injects the three fixtures in order with 3-second pauses → prints rich-terminal progress → leaves the dashboard populated for recording
- **Fixtures:** same three that Track E's evalsets reference
- **Merge risk:** LOW — pure orchestration script

## Dependency graph

```
Phase 1 (parallel):   [R]  [V]  [E]
                       |    |
Phase 2:              [P]  [A]    (A depends on V; reads R)
                       \   /
Phase 3:                [I]  [D]
                         \   /
                         [Demo]
```

Build order for a solo developer, picking one worktree at a time:

```
1. R (doc-only, unblocks P)
2. V (pure function, fastest unit-test loop)
3. E (tests — drive-by during V's dev)
4. P (waits on R; if R says Sessions/Memory, quick; if Firestore, add emulator setup)
5. A (replaces stub; most prompt iteration happens here)
6. I + D (can happen in either order, or interleaved)
7. Demo (final wiring)
```

## Integration contracts to freeze on master first

Before forking any feature worktree, land these in a single small commit on `master`:

| Contract | File | Producer | Consumers |
|---|---|---|---|
| `ValidationResult` | `backend/models/validation_result.py` (new) | Track V | A, E, D |
| `RoutingDecision` Literal `"auto_execute" \| "clarify" \| "escalate"` | same file | Track V | A, E, D |
| `OrderRecord` | `backend/models/order_record.py` (new) | Track A | P, D |
| `ExceptionRecord` | `backend/models/exception_record.py` (new) | Track A | P, D |
| `OrderStore`, `ExceptionStore` protocols | `backend/persistence/base.py` (new) | Track P | A, D |
| `MasterDataIndex` | `backend/data/master_index.py` (new) | — (loader over existing `data/masters/`) | V, A |

Existing contracts already on master (do not touch):
- `ParsedDocument`, `ExtractedOrder`, `OrderLineItem` — `backend/models/parsed_document.py`
- `ClassifiedDocument`, `DocumentFormat` — `backend/models/classified_document.py`
- `DocumentClassification` Literal — `backend/models/parsed_document.py`

## Worktree commands (reference)

```bash
# From master, after landing contracts commit:
git worktree add ../Order-Intake-Agent-validation  -b feat/validation
git worktree add ../Order-Intake-Agent-eval        -b feat/eval
git worktree add ../Order-Intake-Agent-research    -b research/adk-session-memory
# ... etc per track

# Each worktree has its own .venv and can be opened in its own editor window.
# When green, merge back via PR:
cd ../Order-Intake-Agent
git checkout master && git merge feat/validation
```

## Connections

- [Order-Intake-Sprint-Overview](Order-Intake-Sprint-Overview.md)
- [Order-Intake-Sprint-Decisions](Order-Intake-Sprint-Decisions.md) — decision numbers map to worktree choices
- [Order-Intake-Sprint-Verification](Order-Intake-Sprint-Verification.md) — per-worktree green criteria
- `research/Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-ADK-Order-Intake.md` — blueprint for Track A
