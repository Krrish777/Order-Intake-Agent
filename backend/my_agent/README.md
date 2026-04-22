# Order Intake Agent — `adk web` Operator Guide

The assembled Order Intake Agent is a `SequentialAgent` named
`order_intake_pipeline` wiring eight `BaseAgent` stages: ingest → reply
short-circuit → classify → parse → validate → clarify → persist →
finalize. This page is the single-page runbook for dogfooding it through
`adk web` against live Gemini + LlamaCloud + a local Firestore emulator.

If you've never run this project before, read `CLAUDE.md` first — it
establishes the scope (Sprint 1 = Order Intake only; Firestore is the
ERP) and the toolchain (Python 3.13 + `uv`). This README assumes you
have.

## Prerequisites

### 1. Dependencies

```bash
uv sync
```

### 2. Firestore emulator

Keep this running in a dedicated terminal. `firebase.json` + `.firebaserc`
pin the emulator to `localhost:8080` with UI on `localhost:4000`.

```bash
firebase emulators:start --only firestore
```

### 3. Master data

The validator reads customers + products from the emulator. Seed them
once per emulator session:

```bash
FIRESTORE_EMULATOR_HOST=localhost:8080 \
    uv run python scripts/load_master_data.py
```

Without master data every validation drops to ESCALATE and the trace
stops being useful.

### 4. Environment variables

```bash
export GOOGLE_API_KEY=...          # Gemini via google-genai (clarify + summary LlmAgents)
export LLAMA_CLOUD_API_KEY=...     # LlamaClassify + LlamaExtract
export FIRESTORE_EMULATOR_HOST=localhost:8080
```

Substitute `GOOGLE_CLOUD_PROJECT` + `GOOGLE_CLOUD_LOCATION` + ADC for
`GOOGLE_API_KEY` if you're on Vertex AI.

## Launch

From the **repo root** (not `backend/`):

```bash
uv run adk web .
```

CWD is load-bearing: `IngestStage` resolves fixture paths via
`Path(text.strip())`, which is relative to `os.getcwd()`. Launching from
anywhere other than the repo root will break the fixture-path UX
described below.

`adk web` discovers `root_agent` by attribute lookup in
`backend/my_agent/agent.py`. Module import constructs the production
dependency graph (async Firestore client, `MasterDataRepo`,
`OrderValidator`, both stores, `IntakeCoordinator`, the two LlmAgents).
If your emulator isn't up or credentials are missing, the import will
fail loudly at launch time — that's by design.

## What to type in the UI

`IngestStage` accepts either of:

- **A fixture path, relative to the repo root** — e.g.
  `data/pdf/patterson_po-28491.wrapper.eml`. The parser does a
  `Path.exists()` check and reads the file.
- **A raw EML blob**, pasted directly. Any input that doesn't resolve to
  an existing file is treated as raw content.

No JSON wrapping, no preamble — just the path or the blob.

## Expected event stream

Each stage emits at least one event with a state delta. Watch the adk
web trace column and match to this sequence:

| # | Stage | What fires | State keys written |
|---|-------|-----------|--------------------|
| 1 | `IngestStage` | 1 event after parsing the EML | `envelope` |
| 2 | `ReplyShortCircuitStage` | 1 event (sets flag either way) | `reply_handled` |
| 3 | `ClassifyStage` | 1 event per attachment, then a summary | `classified_docs` |
| 4 | `ParseStage` | 1 event per PO attachment; sub-documents flattened | `parsed_orders` |
| 5 | `ValidateStage` | 1 event per sub-doc with routing decision | `validation_results` |
| 6 | `ClarifyStage` | 1 Gemini call per CLARIFY-tier sub-doc (none for pure AUTO) | `clarify_bodies` |
| 7 | `PersistStage` | 1 `IntakeCoordinator.process` per sub-doc; order or exception written | `process_results` |
| 8 | `FinalizeStage` | 1 Gemini call; always runs, even on short-circuit | `run_summary` |

If stage 2 sets `reply_handled=True`, stages 3–7 still execute but
become no-ops (no attachments to classify, no orders to persist). Stage
8 always fires.

## Expected final state

After a successful AUTO invocation:

- `state["run_summary"]` — one-to-two-sentence recap from Gemini.
- `state["process_results"]` — list of dicts, one per sub-doc, each with
  `kind` (`"order"` or `"exception"`) and the corresponding record ID.
- Firestore side effects:
  - AUTO path: one `OrderRecord` in the `orders` collection.
  - CLARIFY / ESCALATE path: one `ExceptionRecord` in the `exceptions`
    collection (status `PENDING_CLARIFY` or `ESCALATED`).
  - Reply short-circuit: the matched parent exception moves to
    `AWAITING_REVIEW`; no new order or exception.

The Firestore emulator UI at `http://localhost:4000/firestore` is the
fastest way to verify.

## Three suggested fixtures

### 1. AUTO path — `data/pdf/patterson_po-28491.wrapper.eml`

The reference happy path proven in the Step 6 emulator integration test.
Customer `CUST-00042`, SKUs resolve cleanly against seeded master data,
routes AUTO_APPROVE, writes one `OrderRecord`. Use this first to
confirm your setup works.

### 2. Reply short-circuit — `data/email/birch_valley_clarify_reply.eml`

Exercises `ReplyShortCircuitStage`. **Requires a pre-seeded
`PENDING_CLARIFY` exception** — run the idempotent seeder before typing
the path:

```bash
FIRESTORE_EMULATOR_HOST=localhost:8080 \
    uv run python tests/eval/fixtures/seed_birch_valley_exception.py
```

Expect the trace to show stage 2 flipping `reply_handled=True`, stages
3–7 as no-ops, and stage 8 summarising "0 orders created". The seeded
exception should advance to `AWAITING_REVIEW` in the emulator UI.

### 3. Trace-interesting — `data/pdf/redline_urgent_2026-04-19.wrapper.eml`

Routing depends on how the extracted order scores against master data
at run time. Use it to observe the classify → parse → validate → clarify
trace end-to-end; if it lands CLARIFY, you get a free demo of stage 6
calling Gemini to draft a clarification email. If it lands AUTO, you
still see the full happy path on a different customer/SKU mix than
patterson.

For additional options see `data/pdf/*.wrapper.eml`, `data/csv/*.wrapper.eml`,
and `data/email/*.eml`.

## Known quirks

- **Gemini non-determinism.** The stage 6 clarify email and the stage 8
  `run_summary` are LLM output — wording varies run-to-run. The eval
  thresholds are loose on purpose (see `tests/eval/README.md`).
- **LlamaCloud rate limits.** Classify + parse hit the live API. If you
  get 429s, pause and rerun. Payloads are SHA-256-suffixed so re-uploads
  are idempotent against LlamaCloud's `(project_id, external_file_id)`
  uniqueness constraint (fixed in Step 6.5).
- **Emulator state leaks between sessions.** The emulator keeps data in
  memory until restarted. A previous run's `PENDING_CLARIFY` will still
  be there; an advanced `AWAITING_REVIEW` will block the Case 2 seeder.
  Restart the emulator (Ctrl-C, relaunch) or delete offending docs in
  the UI to reset.

## Troubleshooting

**Import fails with a Firestore auth / connection error at launch.**
The emulator isn't running, or `FIRESTORE_EMULATOR_HOST` isn't set in
the shell that launched `adk web`. Start the emulator and re-export the
env var in the same shell before retrying.

**LlamaCloud raises `external_file_id` collision.**
Step 6.5 fixed the parser to SHA-256-suffix its `external_file_id`, so
the common re-upload case is idempotent. If you still see this, you're
uploading two semantically-different payloads under the same logical
name within one LlamaCloud project — restart the emulator (fresh intake
history) or rotate the fixture.

**`ValidateStage` routes everything to ESCALATE.**
Master data isn't seeded, or the emulator was restarted without a
re-seed. Run `scripts/load_master_data.py` again.

**`GOOGLE_API_KEY` missing / `google.auth` errors in stage 6 or 8.**
Export it (or configure Vertex ADC) in the launching shell; the
LlmAgents pick it up at call time.

## Cross-references

- `tests/eval/README.md` — the `adk eval` harness for the same pipeline.
  Same prerequisites, formalised as 3 evalset cases.
- `research/Order-Intake-Sprint-Status.md` — sprint-level context: what's
  landed, what's next, how this agent slots into the demo.
- `CLAUDE.md` — project-wide guidance.
- `tests/integration/test_orchestrator_emulator.py` — the Step 6
  integration test that proves the same pipeline against the emulator
  with stubbed LlmAgents. A good read when `adk web` behaves unexpectedly
  and you want a headless reference run to compare against.
