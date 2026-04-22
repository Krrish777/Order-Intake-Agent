# Order Intake — Smoke Evalset

This directory holds the smoke-tier `adk eval` harness for the 8-stage Order
Intake pipeline. It is the **first gate that exercises the full pipeline
against live services**: real Gemini for the clarify + summary LlmAgents,
real LlamaClassify + LlamaExtract for classification + parsing, and a local
Firestore emulator for persistence + master-data reads.

Because the harness hits live APIs with non-deterministic outputs, the
thresholds in `eval_config.json` are deliberately loose (0.3 for both
`tool_trajectory_avg_score` and `response_match_score`). We grade
**completion**, not output fidelity — a run that reaches
`FinalizeStage` and publishes a `run_summary` without crashing is a pass.

## Files

| File | Purpose |
|------|---------|
| `smoke.evalset.json` | Evalset definition — 3 cases covering AUTO_APPROVE, a second AUTO_APPROVE-or-CLARIFY fixture, and the reply-handled short-circuit. |
| `eval_config.json` | Loose thresholds + `ANY_ORDER` trajectory match. |
| `fixtures/seed_birch_valley_exception.py` | Idempotent helper that pre-seeds the `PENDING_CLARIFY` exception Case 3 depends on. |

## Prerequisites

### 1. Environment variables

```bash
export GOOGLE_API_KEY=...             # Gemini via google-genai (clarify + summary LlmAgents)
export LLAMA_CLOUD_API_KEY=...        # LlamaClassify + LlamaExtract
export FIRESTORE_EMULATOR_HOST=localhost:8080
```

If you deploy on Vertex AI instead of the public Gemini API, substitute
`GOOGLE_CLOUD_PROJECT` + `GOOGLE_CLOUD_LOCATION` + Application Default
Credentials for `GOOGLE_API_KEY` — see the ADK model docs.

### 2. Firestore emulator + master data

```bash
# Terminal 1: emulator (keep running)
firebase emulators:start --only firestore

# Terminal 2: seed master data (10 customers + 35 products + meta)
uv run python scripts/load_master_data.py
```

The patterson fixture maps to `CUST-00042` and pulls SKUs from the seeded
catalog — without master data the validator tier drops below AUTO_APPROVE
and Case 1 starts failing in counter-intuitive ways.

### 3. Case 3 (birch_valley) only: seed the parent exception

Case 3 exercises the reply short-circuit, which requires a matching
`PENDING_CLARIFY` exception in the same thread as the reply's
`In-Reply-To` / `References` headers. Seed it before running `adk eval`:

```bash
FIRESTORE_EMULATOR_HOST=localhost:8080 \
    uv run python tests/eval/fixtures/seed_birch_valley_exception.py
```

The script is idempotent — re-running is safe; it no-ops when the doc
already exists with `PENDING_CLARIFY` status.

**Note**: if a previous eval run already advanced the exception to
`AWAITING_REVIEW`, the seeder will refuse to overwrite. Either delete the
doc from the emulator UI (`http://localhost:4000/firestore`) or rotate
`ORIGINAL_CLARIFY_MESSAGE_ID` in the seed script.

## Running the evalset

```bash
uv run adk eval backend/my_agent \
    tests/eval/smoke.evalset.json \
    --config_file_path tests/eval/eval_config.json \
    --print_detailed_results
```

Run a single case:

```bash
uv run adk eval backend/my_agent \
    tests/eval/smoke.evalset.json:patterson_auto_approve \
    --config_file_path tests/eval/eval_config.json
```

## Cases shipped

| eval_id | Fixture | Expected routing | Asserting |
|---------|---------|------------------|-----------|
| `patterson_auto_approve` | `data/pdf/patterson_po-28491.wrapper.eml` | AUTO_APPROVE | Pipeline completes; `FinalizeStage` emits a summary mentioning orders created. |
| `redline_urgent_auto_approve` | `data/pdf/redline_urgent_2026-04-19.wrapper.eml` | AUTO_APPROVE (likely) | Same. If the validator routes to CLARIFY, re-pin the expected `final_response` when promoting this case from smoke to a stricter tier. |
| `birch_valley_reply_short_circuit` | `data/email/birch_valley_clarify_reply.eml` | Reply-handled short-circuit — pipeline advances the seeded exception to `AWAITING_REVIEW` and does not create an order. | Pipeline completes; summary mentions 0 orders created. |

## Cases deferred

- **CLARIFY-scoring fixture** (aggregate_confidence in `[0.80, 0.95)`): no
  existing fixture is known to land cleanly in the CLARIFY band at
  authoring time. Picking one requires running `OrderValidator` against
  each fixture's `ExtractedOrder` with the emulator + master data up. Add
  this case once the discovery run is complete.
- **ESCALATE fixture** (aggregate_confidence `< 0.80`): same as above —
  needs a validator run to pick the right fixture.

## Known flakiness

- **LlamaCloud rate limits** — classify + parse hit the live API. If the
  smoke run 429s, back off or re-run; the parser's `external_file_id`
  suffix (SHA-256 of payload) means re-uploads are idempotent per the
  LlamaCloud `(project_id, external_file_id)` unique constraint.
- **Gemini latency** — the summary LlmAgent can take >10s on cold cache.
  `adk eval` has no per-case timeout today; expect full-set runs to take
  30-60s per case.
- **Non-determinism in generated summary text** — both the clarify email
  and the run summary are Gemini-generated, so literal
  `response_match_score` comparison against the expected text will
  always be low. Threshold is 0.3 on purpose; tighten only after pinning a
  judge-based metric (`final_response_match_v2` with a loose rubric).

## Why loose thresholds

Per `/adk-eval-guide` (see "Common Gotchas"):

- The pipeline has **no tool calls** at the `Runner` level — stages pass
  state through `InvocationContext` rather than model-visible tool calls,
  so `tool_trajectory_avg_score` against an empty expected-trajectory
  tolerates anything the child LlmAgents do internally.
- `response_match_score` at 0.3 catches complete mismatches (agent
  crashed, empty response) without penalising Gemini's phrasing drift.

When we graduate this harness from smoke to regression, swap
`response_match_score` for `final_response_match_v2` with a rubric like
"the response reports a non-zero count of orders created" and tighten to
0.7+.

## CI wiring (future)

This evalset is **not wired into CI** today — it depends on live API
credentials and a running emulator, neither of which the unit-test CI
workflow provides. See `research/Order-Intake-Sprint-Status.md` for the
planned promotion path (manual → nightly eval job → PR gate).
