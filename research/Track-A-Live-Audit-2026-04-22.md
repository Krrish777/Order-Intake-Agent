---
type: audit
topic: "Track A live-run audit vs ADK documentation"
date: 2026-04-22
status: "findings + action items — fixes land one-by-one on master"
tags:
  - track-a
  - adk
  - audit
  - live-run
---

# Track A Live-Run Audit (2026-04-22)

Comprehensive audit of the assembled Order Intake Agent pipeline against the official ADK documentation set (`/adk-dev-guide`, `/adk-cheatsheet`, `/adk-eval-guide`, `/adk-scaffold`, `/adk-deploy-guide`, `/adk-observability-guide`) cross-referenced against the installed `google.adk` source at `.venv/Lib/site-packages/google/adk/`.

Triggered by the first live `adk run` smoke against real Gemini + real LlamaCloud + the local Firestore emulator. The live smoke surfaced three blocking failures, and the audit found six more issues ranging from minor polish to real bugs.

Already fixed in-session before this audit landed:
- Import-path mismatch between ADK's `sys.path.insert(0, parent_dir); import my_agent` convention and our `from backend.my_agent.*` absolute imports → `sys.path` bootstrap added to `backend/my_agent/__init__.py`, plus `agent.py`'s intra-package imports converted to relative (`.stages.ingest` etc.).
- `ConfigDict(extra="forbid")` on `ClarifyEmail` / `RunSummary` → Pydantic emits `additionalProperties: false` → Gemini rejects `response_schema` with a 400 → removed `extra="forbid"` from both; kept Pydantic's default silently-ignore-extra (safe for LLM output validation).
- `backend/my_agent/.env` populated with all required keys (`GOOGLE_API_KEY`, `GOOGLE_GENAI_USE_VERTEXAI=0`, `LLAMA_CLOUD_API_KEY`, `FIRESTORE_EMULATOR_HOST=localhost:8080`, `GOOGLE_CLOUD_PROJECT=demo-order-intake-local`) so `adk run` / `adk web` / `adk eval` all find what they need via `envs.load_dotenv_for_agent`.

Findings below are open items.

## Findings

### F1 — `adk web` shows sibling packages as separate agent entries
- **Severity:** CRITICAL (UX blocker; the user's original complaint).
- **What we do:** Pipeline lives at `backend/my_agent/agent.py`; `backend/` also contains `ingestion/`, `models/`, `persistence/`, `prompts/`, `tools/`, `utils/`. `backend/my_agent/` itself contains `agents/` and `stages/`. `adk web backend/my_agent` lists `agents/` and `stages/` as entries; `adk web backend` would list every sibling package under `backend/`.
- **What ADK docs say:** `adk-cheatsheet/references/python.md:14-30` canonical layout is `<project_root>/<agent_name>/agent.py` — the agent directory's *siblings* are `tests/`, `pyproject.toml`. Not other Python packages the agent depends on. `adk web <dir>` enumerates every sub-directory under `<dir>` as an agent candidate (`agent_loader.py:364-375`).
- **Fix:** Create a single-entry scan directory at the repo root that contains only a thin wrapper pointing at `backend.my_agent`:
  ```
  agents/
  └── order_intake/
      ├── __init__.py      # from backend.my_agent.agent import root_agent
      └── agent.py         # re-export root_agent
  ```
  Then run `adk web agents` — shows exactly one entry. Keeps `backend/my_agent/` untouched so pytest imports + the existing intra-package graph stay intact.
- **Confidence:** HIGH.

### F3 — Gemini `additionalProperties: false` regression risk
- **Severity:** IMPORTANT (already fixed for current schemas; need a guard against reintroduction).
- **What we do:** Current `ClarifyEmail` + `RunSummary` do NOT set `extra="forbid"` (fixed live). But six other Pydantic models (`LineItemValidation`, `ValidationResult`, `ExceptionRecord`, four `OrderRecord*` snapshots) still have it — safe because they reach Firestore, not Gemini. The risk is a future contributor promoting one of them to an `output_schema` on an LlmAgent without realizing.
- **What ADK docs say:** `adk-cheatsheet/references/python.md:98-117` shows `output_schema=Evaluation` without `extra="forbid"`.
- **Fix:** Add a unit test that iterates every LlmAgent factory's produced agent and asserts `agent.output_schema.model_json_schema()` does NOT contain `"additionalProperties": false`.
- **Confidence:** HIGH.

### F4 — Parser `external_file_id` collides on re-runs
- **Severity:** CRITICAL (blocks any re-run of the same fixture against LlamaCloud).
- **What we do:** `backend/tools/document_parser/legacy/parser.py:49-67` uses `f"{filename}::{sha256(content)[:12]}"` — deterministic across runs. LlamaCloud rejects duplicate `external_file_id`s with `UniqueViolationError`. Classifier at `classifier.py:202` uses `uuid.uuid4().hex[:12]` and succeeds on every run. Step 6.5's rationale ("content-hash enables cache-hit") was a misread — LlamaCloud doesn't cache-hit on duplicate external_file_id; it rejects.
- **Fix:** Swap parser to the classifier's UUID pattern. Update the two unit tests in `tests/unit/test_document_parser.py` that assert deterministic-vs-unique semantics — those asserted the WRONG contract. Flip them to assert per-call uniqueness like the classifier's tests.
- **Confidence:** HIGH (live failure reproduced).

### F5 — Validator runs twice per sub-document (ValidateStage + coordinator)
- **Severity:** CRITICAL (~15s wasted LLM+I/O per sub-doc; correctness-safe but doubles cost).
- **What we do:** `ValidateStage._run_async_impl` awaits `validator.validate(order)` for every parsed sub-doc and writes to `state["validation_results"]`. PersistStage calls `coordinator.process(...)` which re-awaits `validator.validate` internally (`coordinator.py:94`). The ValidateStage output is never consumed by the coordinator.
- **What ADK docs say:** N/A (coordinator is custom), but `adk-cheatsheet:137-163` SequentialAgent pattern has each stage *read* upstream state, not recompute.
- **Fix:** Add `precomputed_validation: Optional[ValidationResult] = None` kwarg to `IntakeCoordinator.process(...)`; PersistStage re-hydrates `ValidationResult` from state and passes it in. Existing unit tests that pass no `precomputed_validation` stay green.
- **Confidence:** HIGH.

### F6 — Patterson fixture routes ESCALATE because master data isn't seeded in the emulator
- **Severity:** IMPORTANT (smoke-eval failure, NOT a code bug).
- **What we do:** Live run showed `line_count=22, aggregate=0.0, decision=escalate`. 22 Patterson line-items failed to match any product. But `data/masters/products.json` has 35 products covering all 22 SKUs. Root cause: `MasterDataRepo` reads from Firestore, not the JSON files. Live smoke didn't run `scripts/load_master_data.py` first → empty collections → all matches miss.
- **Fix:** No code change. Run `uv run python scripts/load_master_data.py` before the smoke. README already documents this at `tests/eval/README.md:36-45`. Optional dev-ergonomics improvement: add an idempotent pre-flight check in the smoke script (`scripts/smoke_run.py`) that queries products and warns / auto-seeds if empty.
- **Confidence:** HIGH.

### F8 — Parent→child state seeding uses direct `ctx.session.state` mutation
- **Severity:** IMPORTANT (works; deviates from "state_delta everywhere" discipline but is forced by the topology).
- **What we do:** `ClarifyStage` and `FinalizeStage` directly mutate `ctx.session.state["customer_name" | "reason" | ...]` before invoking a child LlmAgent, because `state_delta` events don't propagate to the child when the child is driven synchronously via `async for event in child.run_async(ctx)` without a Runner loop between.
- **What ADK docs say:** `python.md:242-268` ConditionalRouter example reads `ctx.session.state` but doesn't write. `python.md:619` shows `tool_context.state["key"]="value"` but that's a different context. No explicit blessing of the pattern we use.
- **Fix:** Keep as-is for now (documented inline, works). Followup: migrate to a `before_model_callback` on each child LlmAgent that sets the placeholders from a dict stored in state — the canonical ADK extension point. Not urgent.
- **Confidence:** MEDIUM.

### F14 — Evalset `user_content.role` + `app_name` verification
- **Severity:** IMPORTANT (small fix + runtime verification).
- **What we do:** `tests/eval/smoke.evalset.json` has `user_content: { role: "user", parts: [...] }`. ADK's schema in the eval guide shows `user_content: { parts: [...] }` without `role`. `session_input.app_name="my_agent"` is a guess.
- **Fix:** Drop the `"role": "user"` field from `user_content` in all three cases. Leave `app_name="my_agent"` — it matches the ADK directory convention when invoking `adk eval backend/my_agent ...`. Add the app_name fallback note (already in README Known Flakiness) as explicit runbook.
- **Confidence:** MEDIUM (needs a live `adk eval` run to fully verify).

### F16 — Two `.env` files (repo-root + backend/my_agent)
- **Severity:** MINOR (footgun, not broken).
- **What we do:** Root `.env` (gitignored) holds `LLAMA_CLOUD_API_KEY`. `backend/my_agent/.env` now holds everything. ADK's `envs.load_dotenv_for_agent` walks up from the agent dir and finds `backend/my_agent/.env` first — root `.env` is ignored by ADK. Still, having two files is confusing.
- **Fix:** Keep `backend/my_agent/.env` as the one-true-source for ADK. Shrink the root `.env` to a docstring pointing at the canonical file, or delete it entirely (pytest/scripts that need values can call `load_dotenv()` on the agent's .env explicitly).
- **Confidence:** HIGH.

### F19 / F8 — Consider `before_agent_callback` / `before_model_callback` for state init
- **Severity:** MINOR (polish).
- **What we do:** `ClarifyStage` / `FinalizeStage` direct-mutate state for placeholders (see F8). `adk-eval-guide/SKILL.md:243-258` flags that unresolved `{state_key}` placeholders cause eval failures, and recommends `before_agent_callback=initialize_state` as the best-practice pattern.
- **Fix:** Migrate the direct-mutation seeding to `before_model_callback` on each child LlmAgent. Keeps state_delta discipline clean and makes the placeholder keys explicit at agent-construction time.
- **Confidence:** LOW-MEDIUM (current pattern works; migration is gold-plating).

## Patterns ADK canon confirms we got right

- **Fresh LlmAgent per factory call** (`build_clarify_email_agent()` / `build_summary_agent()` return new instances) — matches `python.md:733-744`.
- **`EventActions(state_delta=...)` for stage state writes** — matches `python.md:260-268`.
- **`asyncio.to_thread` for blocking LlamaCloud calls + native `await` for async validator** — correct Python async pattern; rationale documented in-file.
- **`output_schema` + `output_key` on LlmAgents** — matches `python.md:98-117`.
- **`skipped_docs` cumulative audit trail (append-not-overwrite)** — every stage preserves upstream skips.
- **`PrivateAttr` for injected deps** — blessed fallback for Protocol deps; template uniformity justifies keeping it across concrete-class deps too.
- **Smoke tier eval config (tool_trajectory + response_match at 0.3)** — matches `adk-eval-guide/SKILL.md:63-76`.
- **`SequentialAgent` of `BaseAgent` stages** — canonical shape for deterministic-with-LLM-touchpoints pipelines per `python.md:137-163`.
- **Model: `gemini-3-flash-preview` on AI Studio** — accessible; no 404 during live run (the 400 was schema, not model availability).
- **`.env` at `backend/my_agent/.env`** — ADK convention location.

## Recommended fix order

1. **F4** — parser UUID (5-minute change; unblocks re-runs).
2. **F6** — seed master data (no code; just run the script before each smoke).
3. **F5** — `precomputed_validation` kwarg (halves per-doc latency; mechanical change).
4. **F1** — single-entry scan dir for `adk web` (user's original UX complaint).
5. **F3** — schema regression test (prevents future re-introduction).
6. **F14** — drop `user_content.role` in evalset.
7. **F16** — consolidate `.env`.
8. **F8 / F19** — migrate to `before_model_callback` (optional polish).
