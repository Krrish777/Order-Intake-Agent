---
type: design-spec
topic: "Track B — Generator-Judge Outbound-Email Quality Gate"
track: B
date: 2026-04-24
parent: "research/Order-Intake-Sprint-Status.md"
source_spec: "research/Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Generator-Judge.md + Glacis-Order-Intake.md §9 \"Gemini quality-gate check on outbound email\""
status: approved-for-implementation
depends_on:
  - "Track A2 (Gmail egress) — SendStage exists; _maybe_send_confirmation / _maybe_send_clarify get a 5-line judge-gate check between the sent_at-guard and the send_message call"
  - "Track D (audit log) — optional: JudgeStage inherits AuditedStage mixin if D has landed, else uses inline _audit no-op"
blocks: []
tags:
  - design-spec
  - track-b
  - generator-judge
  - quality-gate
  - outbound-email
  - llm-as-judge
  - gemini-flash
---

# Track B — Generator-Judge Outbound-Email Quality Gate — Design

## Summary

A new `BaseAgent` stage — `JudgeStage` — inserted at pipeline position **#10**, between `FinalizeStage` (#9) and `SendStage` (which moves from #10 to #11). Total stage count becomes 11 after Track B lands. The stage runs a Gemini Flash `LlmAgent` against every drafted outbound email body (confirmation + clarify) before `SendStage` fires. Hard-blocks the Gmail send on hallucinated facts, unauthorized commitments, tone drift, or disallowed URLs. The structured verdict + findings persist on the underlying `OrderRecord` / `ExceptionRecord` (new `judge_verdict: Optional[JudgeVerdict]` field) so operators, Track D audit log, and any later dashboard see *why* a send was blocked.

This closes Glacis `Email-Ingestion.md` §9's *"Gemini quality-gate check on outbound email"* bullet, flipping it from `[Post-MVP]` to `[MVP ✓]`. It closes the safety-critical gap Track A2 leaves open: A2 *can* send email; Track B makes it *safe* to send without human-in-the-loop review. `AGENT_VERSION` bumps `track-a-v0.3` → `track-a-v0.4`.

**Explicitly out of scope:** full three-stage Generator-Judge validation loop from the Glacis note (LoopAgent-wrapped extraction ↔ judge iteration). That stays Post-MVP — see §11.

## Context

- `ConfirmStage` (track A close-out, 6344a83 / f5db946) already drafts a confirmation body for every AUTO_APPROVE order and writes it onto `OrderRecord.confirmation_body` via `OrderStore.update_with_confirmation`. Nothing gates what it drafts.
- `ClarifyStage` (Track A Step 4f, b33a030) already drafts a clarify body for every CLARIFY exception and the coordinator persists it as `ExceptionRecord.clarify_body`. Nothing gates what it drafts.
- Track A2 (`docs/superpowers/specs/2026-04-24-track-a2-gmail-egress-design.md`, 0780025 / d0c65e7) introduces `SendStage` at pipeline position #10, which reads the persisted bodies and calls `gmail_client.send_message`. A2's §Connections explicitly carves out Track B: *"wraps SendStage's `gmail_client.send_message` call with a pre-flight judge pass. Expected shape: a `JudgeService.evaluate(body)` call inside `_maybe_send_confirmation` / `_maybe_send_clarify` between the `sent_at`-guard and the `send_message` call. Fail-closed on judge failure → treated as `send_error="judge_rejected: <reason>"`."*
- The `research/Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Generator-Judge.md` note describes a much larger three-stage architecture (Generator LlmAgent + deterministic Code Judge + Model Judge wrapped in a `LoopAgent` with `max_iterations=3`) that is *tangential* to Track B. Track B implements only the smaller "outbound-email quality gate" bullet from `Glacis-Order-Intake.md` §9.
- Existing precedent for LlmAgent-hosting stages: `ClarifyStage` + `ConfirmStage` each hold a child `LlmAgent` via `PrivateAttr` typed `Any`, invoke it via `child.run_async(ctx)`, and forward events upward. Both prompts use literal `{state_key}` placeholders resolved against `ctx.session.state` at model-call time. See `backend/my_agent/stages/confirm.py:24-31` for the state-mutation-vs-state_delta gotcha.
- Track A2's `OrderRecord` already reaches v4 (A2 adds `sent_at`, `send_error`) and `ExceptionRecord` reaches v3. Track C (`docs/superpowers/specs/2026-04-24-track-c-duplicate-detection-design.md`) bumps `OrderRecord` v2 → v3 independently. Under the handoff's recommended execution order (C → D → A1 → A2 → A3 → B → E), Track B starts from `OrderRecord` v4 + `ExceptionRecord` v3.

## Decisions

### Decision 1 — Scope: outbound-email gate only

Track B is the **outbound-email quality gate** from `Glacis-Order-Intake.md` §9, not the full three-stage Generator-Judge validation loop from the Glacis deep-dive note. The judge evaluates drafted email bodies between ConfirmStage/ClarifyStage drafting and SendStage transmission. It does **not** iterate validation, does **not** rewrite extractions, does **not** wrap validation in a `LoopAgent`.

**Rejected:**
- Full three-stage Generator-Judge loop (Glacis note §"Three-Stage Architecture"). 3-10x scope; not demo-critical; the Glacis note itself tags the validation-loop variant `[Nice-to-have]`; rightful Phase 3 work.
- Validation-loop-only (skip the outbound-email gate). Defeats A2's explicit carve-out; leaves unsafe send as the headline MVP capability.

### Decision 2 — Hookpoint: new `JudgeStage` at pipeline position #10

Insert `JudgeStage` between `FinalizeStage` and `SendStage`. Pipeline order becomes:

```
1. ingest → 2. reply_shortcircuit → 3. classify → 4. parse → 5. validate →
6. clarify → 7. persist → 8. confirm → 9. finalize → 10. JudgeStage → 11. SendStage
```

`SendStage` moves from position #10 (A2's baseline) to #11 on Track B landing.

**Why after finalize, not before.** `FinalizeStage` produces the `RunSummary` that populates audit/trace output. Putting `JudgeStage` after it keeps `RunSummary` a pure statement of what the agent *decided*, independent of whether a later judge rejection blocks the actual send. It also keeps `FinalizeStage` free of any dependency on verdict state.

**Why before send, not inline in SendStage.**
- *Single responsibility:* each stage in this repo does one thing — `SendStage` transports, `JudgeStage` evaluates. Inlining would give `SendStage` two `PrivateAttr` children (GmailClient + judge agent) and double the test surface.
- *Observability:* a peer stage renders in `adk web` traces as its own node with its own events; the verdict is inspectable without replaying a send attempt.
- *Persistence:* a separate stage naturally writes the verdict onto the record (for dashboard + audit consumption) independent of whether a send happens.

**Rejected:**
- Inline judge inside `SendStage._maybe_send_*` (A2's spec carve-out shape). Breaks the one-responsibility-per-stage pattern; hides judge from `adk web` traces; verdict invisible without a send replay; couples audit logic with send logic.
- Wrap `GmailClient.send_message` with a judge decorator at the client layer. Hides judge from pipeline view entirely; couples transport + evaluation; hardest to dry-run judge alone.

### Decision 3 — Judge inputs: body + subject + record_facts

The judge `LlmAgent` receives four state keys: `{judge_subject}`, `{judge_body}`, `{judge_record_kind}` (`"order"` or `"exception"`), and `{judge_record_facts}` (a JSON-stringified flat dict containing the ground-truth values the body may legitimately reference — customer name/id, order total, line items, status for `"order"`; exception type, reason, missing fields, customer name for `"exception"`).

The judge cross-checks every claim in the body against `record_facts`. Numbers, SKUs, customer names, and addresses that appear in the body but do not trace to `record_facts` are flagged as `hallucinated_fact` findings.

**Rejected:**
- Blind (body + subject only). Cannot catch hallucinated totals/SKUs — the most common and highest-impact failure mode. Blind evaluation only catches tone + URL issues, which are a strict subset.
- Full context (add the customer's original inbound email). Over-scoped for MVP; 2-3x prompt size; marginal incremental coverage when record_facts already captures what the pipeline *decided*; adds a dependency on threading the original envelope through to `state['judge_record_facts']`.

### Decision 4 — Verdict schema: binary + structured findings

```python
class JudgeFindingKind(str, Enum):
    HALLUCINATED_FACT       = "hallucinated_fact"
    UNAUTHORIZED_COMMITMENT = "unauthorized_commitment"
    TONE                    = "tone"
    DISALLOWED_URL          = "disallowed_url"
    OTHER                   = "other"

class JudgeFinding(BaseModel):
    kind:        JudgeFindingKind
    quote:       str  # verbatim snippet from the body
    explanation: str  # why it's a problem

class JudgeVerdict(BaseModel):
    status:   Literal["pass", "rejected"]
    reason:   str                 # "" on pass; one-liner on rejected
    findings: list[JudgeFinding]  # [] on pass
```

Binary pass/rejected — no tri-status warn. Structured `findings` with enum `kind` enables future grouped counts (*"38% of rejections this week were unauthorized_commitment"*) without re-parsing free text. `list[JudgeFinding]` (not dict) preserves order — the judge quotes findings in the order they appear in the body, so operators scan top-to-bottom.

**No `model_config = ConfigDict(extra="forbid")`** — that would emit `additionalProperties: false`, which Gemini's `response_schema` rejects (see Track A Audit finding F3 regression walker in `tests/unit/test_llm_agent_factories.py`).

**Rejected:**
- Free-text `reason` only (no structured findings). Loses groupable `kind` enum for dashboards; harder to audit; wastes the structured-output primitive this repo already uses for ConfirmationEmail / ClarifyEmail.
- Tri `pass | warn | rejected`. Warn is operationally ambiguous in a full-auto pipeline — if nobody checks warn, it's just pass with extra logging.

### Decision 5 — Single judge agent, `record_kind` discriminator

One `build_judge_agent()` factory in `backend/my_agent/agents/judge_agent.py` returns one `LlmAgent` (`gemini-3-flash-preview`, `output_schema=JudgeVerdict`, `output_key="judge_verdict"`). One `SYSTEM_PROMPT` + `INSTRUCTION_TEMPLATE` in `backend/prompts/judge.py`. The prompt branches on `{judge_record_kind}` inline — order-kind bodies state facts (every number must match `record_facts`); exception-kind bodies ask questions (must not commit to anything).

**Rejected:**
- Two judges (`build_confirmation_judge_agent()` + `build_clarify_judge_agent()`). Would match the `confirmation_email_agent` / `clarify_email_agent` split precedent, but duplicates prompt + factory + test surface; the failure taxonomy is identical for both body kinds.

### Decision 6 — Fail-closed on LLM failure

Any exception during `self._judge_agent.run_async(ctx)` or during `JudgeVerdict.model_validate(...)` becomes a synthesized `JudgeVerdict(status="rejected", reason=f"judge_unavailable:{type(exc).__name__}", findings=[])`. The synthesized verdict is persisted to the record like any real rejection. `SendStage` sees `status != "pass"` and blocks the send.

The fail-closed synth-verdict is **load-bearing**: every error path produces a real, typed `JudgeVerdict(status="rejected", ...)` object, not `None` or a bubbling exception. This keeps `SendStage`'s decision logic a single branch (`verdict.status == "pass"`) and guarantees the Firestore `judge_verdict` field always reflects the pipeline's decision.

**Rejected:**
- Fail-open (send anyway, log error). Defeats the entire point of the gate — a customer-facing email leaves the system unverified.
- Fail-closed with retry. Absorbs transient Gemini outages (<1s), but adds a code path that matters <1% of the time. Pipeline-level retries (re-run the envelope) absorb the same outages without intra-stage complexity.

### Decision 7 — Reject behavior: record + block, full stop

On `status == "rejected"` (real or synthesized):
1. The verdict + findings persist on the record via `update_with_judge_verdict`.
2. `SendStage` reads the verdict from `state["judge_verdicts"]`, records `send_error=f"judge_rejected:{verdict.reason}"` via `update_with_send_error`, emits an `email_send_blocked` audit event, returns without calling `gmail_client.send_message`.

No auto-escalation (order → exception), no re-draft loop. Operator inspects the record + audit event and manually overrides.

**Rejected:**
- Record + block + auto-escalate order → exception. Introduces cross-record state flip + `OrderStatus` enum extension + `IntakeCoordinator` change. Much bigger scope; judge MVP only needs to *prevent bad emails*, not *orchestrate recovery*.
- Record + block + re-draft loop (feed findings back into ConfirmStage/ClarifyStage). Needs a `LoopAgent` wrapping the draft stages + judge feedback injection into the prompt + `max_iterations` guard — closer to the full Generator-Judge validation loop. Post-MVP.

### Decision 8 — Dry-run: judge always runs; `GMAIL_SEND_DRY_RUN` only gates send

The existing `GMAIL_SEND_DRY_RUN=1` env var (introduced by A2) only skips the Gmail network call. `JudgeStage` runs unconditionally regardless of the flag. The verdict still persists to the record, and `adk web` traces still show the judge executing. This means dry-run sessions *fully exercise the judge* — operators can observe verdict shape and findings without risking a real send.

No separate `JUDGE_DRY_RUN` env var.

**Rejected:**
- Separate `JUDGE_DRY_RUN=1`. Redundant with the existing `FakeChildLlmAgent` test-fixture pattern for offline tests.
- Tie judge execution to `GMAIL_SEND_DRY_RUN`. Loses dry-run observability of judge behavior — the one setting where operators most want to see the judge run.

## Data model

### New — `backend/models/judge_verdict.py`

```python
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class JudgeFindingKind(str, Enum):
    HALLUCINATED_FACT       = "hallucinated_fact"
    UNAUTHORIZED_COMMITMENT = "unauthorized_commitment"
    TONE                    = "tone"
    DISALLOWED_URL          = "disallowed_url"
    OTHER                   = "other"


class JudgeFinding(BaseModel):
    kind:        JudgeFindingKind
    quote:       str = Field(description="Verbatim snippet from the body.")
    explanation: str = Field(description="Why this is a problem.")


class JudgeVerdict(BaseModel):
    status:   Literal["pass", "rejected"]
    reason:   str                = Field(default="",
                   description="Empty on pass; one-liner on rejected.")
    findings: list[JudgeFinding] = Field(default_factory=list,
                   description="Empty on pass; structured issue list on rejected.")
```

No `model_config = ConfigDict(extra="forbid")` — Gemini's `response_schema` rejects the `additionalProperties: false` that Pydantic emits for `forbid` (regression walker in `tests/unit/test_llm_agent_factories.py` would catch a re-introduction).

### Modified — `backend/models/order_record.py`

Add field + bump:
```python
judge_verdict:  Optional[JudgeVerdict] = None
schema_version: int = 5     # was 4 after A2
```

### Modified — `backend/models/exception_record.py`

Add field + bump:
```python
judge_verdict:  Optional[JudgeVerdict] = None
schema_version: int = 4     # was 3 after A2
```

### Modified — `AGENT_VERSION`

`track-a-v0.3` (A2 baseline) → `track-a-v0.4`. So Firestore analytics can distinguish pre/post-judge rows. Bumped in `_build_default_root_agent` in `backend/my_agent/agent.py`.

**Execution-order robustness.** Under the handoff's recommended order (C → D → A1 → A2 → A3 → B → E), Track B inherits `OrderRecord` v4 + `ExceptionRecord` v3 from A2. If tracks execute out of order, the implementation plan's Task 1 will read the current `schema_version` and bump by +1, so the spec survives reordering. The version numbers above assume the recommended order.

### Store methods

Both via field-mask update, same pattern as `update_with_confirmation` / `update_with_send`:

```python
# backend/persistence/base.py  — protocols
class OrderStore(Protocol):
    ...
    async def update_with_judge_verdict(
        self, source_message_id: str, verdict: JudgeVerdict,
    ) -> None: ...

class ExceptionStore(Protocol):
    ...
    async def update_with_judge_verdict(
        self, source_message_id: str, verdict: JudgeVerdict,
    ) -> None: ...

# backend/persistence/orders_store.py  — FirestoreOrderStore impl
async def update_with_judge_verdict(self, source_message_id, verdict):
    doc_ref = self._client.collection("orders").document(source_message_id)
    await doc_ref.update({"judge_verdict": verdict.model_dump(mode="json")})

# backend/persistence/exception_store.py  — FirestoreExceptionStore impl
# (identical shape, collection "exceptions")
```

Raises `NotFound` if the doc is absent — callers only invoke post-persist. No idempotency skip; re-runs overwrite (matching `update_with_confirmation`).

## Architecture

### JudgeStage — `backend/my_agent/stages/judge.py`

`BaseAgent` subclass (or `AuditedStage` subclass if Track D has landed). PrivateAttrs: `_judge_agent: Any` (duck-typed for `FakeChildLlmAgent` compatibility per the Track A pattern), `_order_store: OrderStore`, `_exception_store: ExceptionStore`.

Pseudocode of `_run_async_impl` (or `_audited_run` under Track D):

```python
async def _run_async_impl(
    self, ctx: InvocationContext,
) -> AsyncGenerator[Event, None]:
    # Short-circuit: clarify reply was handled upstream (ConfirmStage pattern).
    if ctx.session.state.get("reply_handled") is True:
        yield Event(
            author=JUDGE_STAGE_NAME,
            actions=EventActions(state_delta={"judge_verdicts": {}}),
        )
        return

    process_results = ctx.session.state.get("process_results")
    if process_results is None:
        raise ValueError(
            "JudgeStage requires PersistStage to have populated "
            "state['process_results']"
        )

    envelope = EmailEnvelope.model_validate(ctx.session.state["envelope"])

    judge_verdicts: dict[str, dict] = {}   # keyed by source_message_id; dicts for JSON-serializable state

    for entry in process_results:
        kind      = entry["result"]["kind"]
        if kind == "duplicate":
            continue  # dup was judged on prior run

        subject, body, source_id = _extract_draft(entry, envelope)
        if body is None:
            continue  # nothing to judge (ESCALATE exceptions, already-sent)

        record_facts = _flatten_facts(entry)

        # Seed {state_key} placeholders by direct mutation (ADK state_delta
        # is not committed between parent + child in the same run_async;
        # see backend/my_agent/stages/confirm.py:24-31).
        ctx.session.state["judge_subject"]      = subject
        ctx.session.state["judge_body"]         = body
        ctx.session.state["judge_record_kind"]  = kind          # "order" | "exception"
        ctx.session.state["judge_record_facts"] = json.dumps(record_facts, default=str)

        try:
            last_verdict: Any = None
            async for event in self._judge_agent.run_async(ctx):
                if (event.actions
                    and event.actions.state_delta
                    and "judge_verdict" in event.actions.state_delta):
                    last_verdict = event.actions.state_delta["judge_verdict"]
                yield event
            if last_verdict is None:
                raise RuntimeError("judge agent produced no output")
            verdict = JudgeVerdict.model_validate(last_verdict)
        except Exception as exc:
            verdict = JudgeVerdict(
                status="rejected",
                reason=f"judge_unavailable:{type(exc).__name__}",
                findings=[],
            )
            await self._audit("judge_unavailable", {
                "source_message_id": source_id,
                "record_kind":       kind,
                "exception":         type(exc).__name__,
            })

        # Persist verdict onto the record.
        if kind == "order":
            await self._order_store.update_with_judge_verdict(source_id, verdict)
        else:  # exception
            await self._exception_store.update_with_judge_verdict(source_id, verdict)

        event_kind = ("judge_verdict_passed" if verdict.status == "pass"
                      else "judge_verdict_rejected")
        await self._audit(event_kind, {
            "source_message_id": source_id,
            "record_kind":       kind,
            "reason":             verdict.reason,
            "findings_count":    len(verdict.findings),
            "findings":          ([f.model_dump() for f in verdict.findings]
                                  if verdict.status == "rejected" else []),
        })

        judge_verdicts[source_id] = verdict.model_dump(mode="json")

    rejected = sum(1 for v in judge_verdicts.values() if v["status"] == "rejected")
    yield Event(
        author=JUDGE_STAGE_NAME,
        actions=EventActions(state_delta={"judge_verdicts": judge_verdicts}),
        content=types.Content(role="model", parts=[types.Part(text=(
            f"Judged {len(judge_verdicts)} outbound body(ies); "
            f"{rejected} rejected."
        ))]),
    )
```

`_extract_draft(entry, envelope)` returns `(subject, body, source_message_id)`:
- `kind == "order"` → body = `entry["result"]["order"]["confirmation_body"]`; subject = `"Re: " + envelope.subject`.
- `kind == "exception"` → body = `entry["result"]["exception"]["clarify_body"]`; subject = `"Re: " + envelope.subject`.

Returns `body=None` if the record has no drafted body (ESCALATE exceptions without clarify_body, or edge cases where a prior run's verdict blocked + no re-draft happened).

`_flatten_facts(entry)` returns a small flat dict:
- `kind == "order"`: `{customer_name, customer_id, order_total, line_items: [{sku, qty, unit_price, line_total}, ...], status}`
- `kind == "exception"`: `{customer_name, exception_type, reason, missing_fields, status}`

### SendStage contract change (A2's file)

Inside both `_maybe_send_confirmation` and `_maybe_send_clarify`, after the existing `sent_at`-guard:

```python
verdict_dict = ctx.session.state.get("judge_verdicts", {}).get(source_message_id)
if verdict_dict is None or verdict_dict.get("status") != "pass":
    reason = (verdict_dict.get("reason") if verdict_dict else "judge_missing")
    await self._order_store.update_with_send_error(    # or exception_store.*
        source_message_id, f"judge_rejected:{reason}",
    )
    await self._audit("email_send_blocked", {
        "source_message_id": source_message_id,
        "send_error":        f"judge_rejected:{reason}",
    })
    return  # do not call gmail_client.send_message
```

Five lines. The `verdict_dict is None` branch is defensive — JudgeStage always writes one for every body it encountered — but fails closed if JudgeStage was somehow skipped or state was serialized without it.

### Prompt sketch — `backend/prompts/judge.py`

```python
SYSTEM_PROMPT: Final[str] = """\
You are a strict outbound-email quality gate for a B2B supply-chain
ordering system. You receive one email this system is about to send to a
business customer. Block the send if the body contains ANY of:

  • hallucinated_fact        — any SKU, quantity, price, total, customer
                               name, or address not present in record_facts
  • unauthorized_commitment  — any promise beyond what record_facts
                               explicitly authorizes (e.g. free shipping,
                               discounts, specific ship dates, guarantees)
                               — even if the customer requested it
  • disallowed_url           — any URL outside the company's own domain
  • tone                     — insults, legal advice, speculation, or
                               apologies beyond brief acknowledgment

If any issue is found, return status='rejected' with a `findings` list
citing each issue. Otherwise return status='pass' with empty findings
and an empty reason.
"""

INSTRUCTION_TEMPLATE: Final[str] = """\
Subject:
{judge_subject}

Body:
{judge_body}

Record kind: {judge_record_kind}
  - 'order'     → confirmation email; body states facts; every number and
                  SKU must trace to record_facts.
  - 'exception' → clarify email; body asks questions; must not commit to
                  anything; questions must be answerable by the customer.

Ground truth (record_facts JSON):
{judge_record_facts}

Return a JSON object matching the JudgeVerdict schema with keys
`status`, `reason`, `findings`.
"""
```

Literal `{state_key}` placeholders — ADK resolves them against `ctx.session.state` at model-call time; do not f-string-interpolate at module load (same rule as `backend/prompts/confirmation_email.py`).

### Agent factory — `backend/my_agent/agents/judge_agent.py`

Mirror of `build_confirmation_email_agent`:

```python
def build_judge_agent() -> LlmAgent:
    combined_instruction = f"{SYSTEM_PROMPT}\n\n{INSTRUCTION_TEMPLATE}"
    return LlmAgent(
        name="judge_agent",
        model="gemini-3-flash-preview",
        description=(
            "Evaluates drafted outbound emails (confirmation + clarify) "
            "against the underlying order/exception record before Gmail send."
        ),
        instruction=combined_instruction,
        output_schema=JudgeVerdict,
        output_key="judge_verdict",
    )
```

Fresh instance per call (prevents ADK's "agent already has a parent" validation error).

## Failure matrix

| Scenario | Judge behavior | Record state | Send outcome | Audit event |
|---|---|---|---|---|
| Happy path | `status="pass"`, findings=[] | `judge_verdict` set, `reason=""` | `gmail_client.send_message` called normally | `judge_verdict_passed` + `email_sent` |
| Real reject | `status="rejected"`, findings=[…] | `judge_verdict` set, `send_error="judge_rejected:<reason>"` | Skipped | `judge_verdict_rejected` + `email_send_blocked` |
| Gemini timeout / API error | `status="rejected"`, `reason="judge_unavailable:TimeoutError"`, findings=[] | Same as real reject | Skipped | `judge_unavailable` + `email_send_blocked` |
| Malformed output (`ValidationError`) | Same as Gemini error path (`reason="judge_unavailable:ValidationError"`) | Same as real reject | Skipped | `judge_unavailable` |
| No output events emitted | Caught via `RuntimeError("judge agent produced no output")` → `reason="judge_unavailable:RuntimeError"` | Same as real reject | Skipped | `judge_unavailable` |
| `GMAIL_SEND_DRY_RUN=1` | Runs, produces real verdict | `judge_verdict` set | Logs `"dry_run: judge=<status>, would send to <recipient>"`; no network call; `sent_at` unchanged | `judge_verdict_*` + `email_send_dry_run` |
| `kind == "duplicate"` | Skipped; loop `continue` | Previous-run verdict preserved | Skipped upstream (A2 already skips dups) | None from judge |
| `kind == "exception"`, no `clarify_body` (ESCALATE) | Skipped; `body is None` branch | No `judge_verdict` write | Skipped upstream (A2 only sends if body present) | None from judge |
| `reply_handled is True` | No-op; empty `state_delta` | Untouched | SendStage also no-ops on reply_handled | None from judge |
| Empty `process_results` | No-op; emits empty `judge_verdicts={}` | Untouched | SendStage also emits empty | None from judge |

## New / modified files

### New

| Path | Purpose |
|---|---|
| `backend/models/judge_verdict.py` | `JudgeFindingKind`, `JudgeFinding`, `JudgeVerdict` Pydantic models. |
| `backend/prompts/judge.py` | `SYSTEM_PROMPT` + `INSTRUCTION_TEMPLATE` with the four `{judge_*}` placeholders; record_kind branches inline. |
| `backend/my_agent/agents/judge_agent.py` | `build_judge_agent()` factory — `LlmAgent(model="gemini-3-flash-preview", output_schema=JudgeVerdict, output_key="judge_verdict")`. |
| `backend/my_agent/stages/judge.py` | `JudgeStage(BaseAgent or AuditedStage)` with PrivateAttrs for `_judge_agent`, `_order_store`, `_exception_store`; inline `_audit` no-op if Track D absent. |
| `tests/unit/my_agent/stages/test_judge_stage.py` | Stage-level unit tests (~8). |
| `tests/unit/my_agent/agents/test_judge_agent.py` | Factory test (~2). |
| `tests/unit/models/test_judge_verdict.py` | Schema round-trip tests (~3). |
| `tests/unit/persistence/test_store_judge_verdict.py` | `update_with_judge_verdict` emulator tests for both stores (~3). |
| `tests/integration/test_pipeline_with_judge.py` | Full 11-stage Runner run with stub judge; asserts verdict persists + exit clean. |

### Modified

| Path | Change |
|---|---|
| `backend/models/order_record.py` | `judge_verdict: Optional[JudgeVerdict] = None`; `schema_version = 5` (was 4 post-A2). |
| `backend/models/exception_record.py` | `judge_verdict: Optional[JudgeVerdict] = None`; `schema_version = 4` (was 3 post-A2). |
| `backend/persistence/base.py` | `update_with_judge_verdict` added to `OrderStore` + `ExceptionStore` protocols. |
| `backend/persistence/orders_store.py` | `FirestoreOrderStore.update_with_judge_verdict` impl. |
| `backend/persistence/exception_store.py` | `FirestoreExceptionStore.update_with_judge_verdict` impl. |
| `backend/my_agent/agent.py` | Wire `JudgeStage` at position #10; `_build_default_root_agent` constructs `build_judge_agent()` + injects stores; `AGENT_VERSION = "track-a-v0.4"`. |
| `backend/my_agent/stages/send.py` (A2 file) | 5-line judge-gate check at top of `_maybe_send_confirmation` + `_maybe_send_clarify`. |
| `tests/unit/test_orchestrator_build.py` | Extend canonical stage order to 11 stages; add `JudgeStage` subclass assertion. |
| `tests/unit/my_agent/stages/test_send_stage.py` (A2 file) | +2 tests — `_maybe_send_*` blocks on rejected verdict; `_maybe_send_*` passes through on pass verdict. |
| `tests/integration/test_pipeline_e2e.py` | Inject stub judge; assert 11-stage flow. |
| `research/Order-Intake-Sprint-Status.md` | Flip/add "Outbound-email quality gate" row + one-line summary + Built inventory; bump pipeline stage count 10→11. |
| `Glacis-Order-Intake.md` | §9 "Gemini quality-gate check on outbound email" `[Post-MVP]` → `[MVP ✓]`; Phase 3 roadmap removes judge bullet; last_updated frontmatter bumped. |

## Test plan

**Unit (~16 new):**

| File | Count | What |
|---|---|---|
| `test_judge_stage.py` | 8 | happy-path pass for `kind="order"`; happy-path pass for `kind="exception"`; rejected verdict with findings persists on record; LLM exception → fail-closed synthesized verdict; malformed output → fail-closed; duplicate skipped; `reply_handled` short-circuit; stage name + canonical position. Uses `FakeChildLlmAgent` from `tests/unit/_stage_testing.py`. |
| `test_judge_agent.py` | 2 | factory returns correct name + model + output_schema + output_key; fresh instance per call. |
| `test_judge_verdict.py` | 3 | pass with empty findings JSON round-trip; rejected with multi-finding JSON round-trip; `JudgeFindingKind` enum covers all 5 values and round-trips cleanly. |
| `test_store_judge_verdict.py` | 3 | `FirestoreOrderStore.update_with_judge_verdict` emulator round-trip; `FirestoreExceptionStore.update_with_judge_verdict` emulator round-trip; `NotFound` raised for missing doc. |

**Unit (A2 file, ~2 new):** `test_send_stage_judge_gate` — `_maybe_send_*` blocks + records `send_error` on rejected verdict; passes through on pass verdict.

**Integration (1 new):** `test_pipeline_with_judge.py` — full 11-stage `Runner.run_async` against Firestore emulator with injected stub judge; asserts `judge_verdict` landed on persisted `OrderRecord`, `SendStage` fired dry-run (since `GMAIL_SEND_DRY_RUN=1`), pipeline exits cleanly.

**Evalset (0 new):** existing `tests/eval/smoke.evalset.json` cases continue to pass — stub judge passes by default in the test harness.

**Live-smoke path:** `scripts/smoke_run.py` gets an optional `--verbose-judge` flag that prints verdict summary (status + first finding) after each pipeline run. Reproducible via the MM Machine fixture from Track A's close-out.

## Success criteria

1. 11-stage pipeline boots cleanly; `adk web adk_apps` discovers it; `adk eval adk_apps/order_intake tests/eval/smoke.evalset.json ...` continues to pass.
2. For a demo AUTO_APPROVE email whose `ConfirmStage` body matches `record_facts`: verdict `status="pass"`, `findings=[]`, SendStage proceeds (dry-run or live per env).
3. For a synthetic body containing a hallucinated total ("your total is $999.99" against `order.total == 127.40`): verdict `status="rejected"`, at least one `hallucinated_fact` finding quoting `"$999.99"`, SendStage skips with `send_error=f"judge_rejected:..."`.
4. On simulated Gemini outage (exception injected into `FakeChildLlmAgent`): verdict `status="rejected"`, `reason="judge_unavailable:..."`, SendStage skips.
5. Duplicate path: no new judge verdict written; prior-run verdict untouched; SendStage already no-ops upstream.
6. Both persisted records (`orders`, `exceptions`) carry `judge_verdict` queryable by `status` — supports future dashboard grouping.
7. All existing tests green. Pipeline-wide count moves 323 → ~341 unit tests; integration +1.
8. `AGENT_VERSION` visible as `track-a-v0.4` on new Firestore docs after Track B lands.

## Out of scope (explicitly)

- Full three-stage Generator-Judge validation loop with `LoopAgent(max_iterations=3)` — Glacis note's "Three-Stage Architecture"; Phase 3.
- Re-draft on reject (feedback loop into `ConfirmStage` / `ClarifyStage`). Post-MVP.
- Auto-escalation `OrderStatus: AUTO_APPROVE → ESCALATED` on reject. Post-MVP.
- Per-kind judge prompts — single judge with `record_kind` discriminator chosen (Decision 5).
- Sampling (run judge on a random 5–10% of auto-pass paths as QA) — Glacis note §"short-circuit tradeoff"; Phase 3.
- Multi-language judging — Glacis `[Nice-to-have]`.
- Judge training / fine-tuning / rubric calibration harness.
- Judge-cost dashboard or token accounting — folds into Track D or a later observability track.
- Deterministic Code Judge pre-stage (Glacis note §"Stage 2: The Code Judge"). The existing `OrderValidator` + `sku_matcher` + `price_check` already cover these checks inside the Validate stage; they are *not* re-run at egress.

## Connections

**Depends on.** Track A2 hard — `SendStage` must exist with `_maybe_send_confirmation` + `_maybe_send_clarify` + `sent_at`-guard. Track A2 blocks Track B.

**Soft dep.** Track D (`AuditedStage` mixin) — JudgeStage uses the same `audit_emit(event_kind, payload)` pattern the other stages adopt. Fallback without D: inline `async def _audit(self, event, payload): pass` on `JudgeStage` — trivially refactored to mixin inheritance when D lands.

**Parallel-compatible.**
- Track C (`docs/superpowers/specs/2026-04-24-track-c-duplicate-detection-design.md`): complementary — dup path has no body, judge naturally `continue`s.
- Track A1 (`docs/superpowers/specs/2026-04-24-track-a1-gmail-ingress-design.md`): judge is egress-only, untouched by ingress.
- Track A3 (`docs/superpowers/specs/2026-04-24-track-a3-pubsub-ingestion-design.md`): same — egress-only, independent.
- Track E (embedding Tier 3): orthogonal; different subsystem.

**Blocks.** Nothing. Track B is a leaf node; no other planned track depends on it landing.

**Doc-flip targets.**
- `Glacis-Order-Intake.md` §9 "Gemini quality-gate check on outbound email" — `[Post-MVP]` → `[MVP ✓]` with full citation chain (commits, files, stage position).
- `Glacis-Order-Intake.md` Phase 3 roadmap — remove the judge bullet from remaining work.
- `research/Order-Intake-Sprint-Status.md` — flip/add row "Outbound-email quality gate" + update pipeline stage count 10 → 11 + bump test counts.

End of design.
