# Track B — Generator-Judge Quality Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Insert a new `JudgeStage` at pipeline position #10 (`SendStage` moves to #11) that runs a Gemini Flash `LlmAgent` against every drafted outbound email body (confirmation + clarify) before Gmail send. Hard-blocks on hallucinated facts / unauthorized commitments / disallowed URLs / tone drift. Verdicts persist on `OrderRecord.judge_verdict` (schema v4 → v5) + `ExceptionRecord.judge_verdict` (schema v3 → v4). `SendStage` gets a 5-line judge-gate check after the existing `sent_at` guard; fail-closed on LLM errors; reject records `send_error="judge_rejected:<reason>"` and skips the Gmail send. `AGENT_VERSION` bumps `track-a-v0.3` → `track-a-v0.4`.

**Architecture:** New `backend/models/judge_verdict.py` (Pydantic `JudgeVerdict` + `JudgeFinding` + `JudgeFindingKind` enum). New `backend/prompts/judge.py` (SYSTEM_PROMPT + INSTRUCTION_TEMPLATE with four `{judge_*}` placeholders; record_kind branches inline). New `backend/my_agent/agents/judge_agent.py` (`build_judge_agent()` factory returning a fresh `LlmAgent` per call). New `backend/my_agent/stages/judge.py` (`JudgeStage(AuditedStage)` with PrivateAttrs for judge agent + both stores; per-ProcessResult loop; fail-closed synth verdict on any exception). Modified `backend/my_agent/stages/send.py` (A2's file) adds a 5-line guard at the top of `_maybe_send_confirmation` + `_maybe_send_clarify`. Modified `backend/my_agent/agent.py` wires `JudgeStage` at index 9 (0-indexed position #10 in the 11-stage list) and bumps `AGENT_VERSION`.

**Tech Stack:** Python 3.13, Pydantic 2.x (schema + structured output), Google ADK 1.x (`LlmAgent`, `BaseAgent`, `AuditedStage` mixin from Track D), `google-cloud-firestore` 2.27.0 (async), pytest + pytest-asyncio, `AsyncMock`/`MagicMock`/`FakeChildLlmAgent` from `tests/unit/_stage_testing.py`.

**Source spec:** `docs/superpowers/specs/2026-04-24-track-b-generator-judge-design.md` (rev `20157a2`).

**Prerequisites:** Track A2 (`SendStage`) has landed. Track D (`AuditedStage` mixin + `AuditLogger`) has landed. Schema assumes: `OrderRecord` at v4, `ExceptionRecord` at v3, `AGENT_VERSION="track-a-v0.3"`. Plan fails fast in Task 4 / Task 5 / Task 9 with a clear error if prerequisites are violated.

---

## File structure

| Path | Responsibility |
|---|---|
| **New** `backend/models/judge_verdict.py` | `JudgeFindingKind` (str Enum, 5 values), `JudgeFinding`, `JudgeVerdict` Pydantic models. No `extra="forbid"`. |
| **New** `backend/prompts/judge.py` | `SYSTEM_PROMPT` + `INSTRUCTION_TEMPLATE` with `{judge_subject}`, `{judge_body}`, `{judge_record_kind}`, `{judge_record_facts}` placeholders. |
| **New** `backend/my_agent/agents/judge_agent.py` | `build_judge_agent()` factory: fresh `LlmAgent(model="gemini-3-flash-preview", output_schema=JudgeVerdict, output_key="judge_verdict")` per call. |
| **New** `backend/my_agent/stages/judge.py` | `JudgeStage(AuditedStage)` with `_judge_agent`, `_order_store`, `_exception_store` PrivateAttrs; `_audited_run` implementation; `_flatten_facts` + `_extract_draft` helpers. |
| **New** `tests/unit/test_judge_verdict_schema.py` | 3 tests: pass round-trip, rejected w/ findings round-trip, JudgeFindingKind enum coverage. |
| **New** `tests/unit/test_judge_prompt.py` | 1 test: placeholders are literal `{state_key}` strings, all four present. |
| **New** `tests/unit/test_stage_judge.py` | 8 tests (see Task 8). |
| **Modified** `backend/models/order_record.py` | `judge_verdict: Optional[JudgeVerdict] = None`; `schema_version = 5` (was 4). |
| **Modified** `backend/models/exception_record.py` | `judge_verdict: Optional[JudgeVerdict] = None`; `schema_version = 4` (was 3). |
| **Modified** `backend/persistence/base.py` | `update_with_judge_verdict` added to `OrderStore` + `ExceptionStore` Protocols. |
| **Modified** `backend/persistence/orders_store.py` | `FirestoreOrderStore.update_with_judge_verdict` impl (field-mask update). |
| **Modified** `backend/persistence/exceptions_store.py` | `FirestoreExceptionStore.update_with_judge_verdict` impl. |
| **Modified** `backend/my_agent/agent.py` | Wire `JudgeStage` at index 9; build `judge_agent` in `_build_default_root_agent`; `AGENT_VERSION = "track-a-v0.4"`. |
| **Modified** `backend/my_agent/stages/send.py` | 5-line judge-gate check at top of `_maybe_send_confirmation` + `_maybe_send_clarify`. |
| **Modified** `tests/unit/test_llm_agent_factories.py` | +1 factory smoke test for `build_judge_agent`; +`JudgeVerdict` to the additionalProperties-false regression walker loop. |
| **Modified** `tests/unit/test_order_store.py` | +3 tests for `update_with_judge_verdict` (happy path, missing-doc, re-call overwrites). |
| **Modified** `tests/unit/test_exception_store.py` | +3 tests for `update_with_judge_verdict`. |
| **Modified** `tests/integration/test_order_store_emulator.py` | +1 emulator round-trip test. |
| **Modified** `tests/integration/test_exception_store_emulator.py` | +1 emulator round-trip test. |
| **Modified** `tests/unit/test_stage_send.py` | +2 tests: judge-gate blocks on rejected; passes through on pass. |
| **Modified** `tests/unit/test_orchestrator_build.py` | Canonical stage order updated to 11; `JudgeStage` subclass assertion at index 9. |
| **Modified** `tests/integration/test_orchestrator_emulator.py` | Inject stub `judge_agent` into full-pipeline run; assert `judge_verdict` landed on persisted record. |
| **Modified** `research/Order-Intake-Sprint-Status.md` | Flip §"Outbound-email quality gate" row; bump pipeline count 10 → 11; append to Built inventory. |
| **Modified** `Glacis-Order-Intake.md` | §9 "Gemini quality-gate check on outbound email" `[Post-MVP]` → `[MVP ✓]`; update Phase 3 roadmap; bump `last_updated`. |

---

## Task 1: `JudgeVerdict` / `JudgeFinding` / `JudgeFindingKind` Pydantic models

**Files:**
- Create: `backend/models/judge_verdict.py`
- Create: `tests/unit/test_judge_verdict_schema.py`

- [ ] **Step 1.1: Write the failing tests**

Create `tests/unit/test_judge_verdict_schema.py`:

```python
"""Schema round-trip tests for the JudgeVerdict Pydantic models.

Guards the same Gemini-response_schema gotcha as ConfirmationEmail /
ClarifyEmail: no ``extra="forbid"``, because Pydantic emits
``additionalProperties: false`` which Gemini 400s on. The regression
walker in ``test_llm_agent_factories.py`` catches drift for LlmAgent
factories; this test-file covers the models themselves.
"""

from __future__ import annotations

import json

import pytest

from backend.models.judge_verdict import (
    JudgeFinding,
    JudgeFindingKind,
    JudgeVerdict,
)


def test_judge_verdict_pass_round_trips_cleanly():
    verdict = JudgeVerdict(status="pass", reason="", findings=[])

    dumped = verdict.model_dump(mode="json")
    assert dumped == {"status": "pass", "reason": "", "findings": []}

    restored = JudgeVerdict.model_validate(dumped)
    assert restored == verdict


def test_judge_verdict_rejected_with_multiple_findings_round_trips():
    verdict = JudgeVerdict(
        status="rejected",
        reason="two findings detected",
        findings=[
            JudgeFinding(
                kind=JudgeFindingKind.HALLUCINATED_FACT,
                quote="your total is $999.99",
                explanation="Body states $999.99 but order.total is 127.40.",
            ),
            JudgeFinding(
                kind=JudgeFindingKind.UNAUTHORIZED_COMMITMENT,
                quote="free shipping on your next order",
                explanation="record_facts has no shipping-terms field authorizing a discount.",
            ),
        ],
    )

    dumped = verdict.model_dump(mode="json")
    assert dumped["status"] == "rejected"
    assert len(dumped["findings"]) == 2
    assert dumped["findings"][0]["kind"] == "hallucinated_fact"
    assert dumped["findings"][1]["kind"] == "unauthorized_commitment"

    restored = JudgeVerdict.model_validate(dumped)
    assert restored == verdict


@pytest.mark.parametrize(
    "value,kind",
    [
        ("hallucinated_fact",       JudgeFindingKind.HALLUCINATED_FACT),
        ("unauthorized_commitment", JudgeFindingKind.UNAUTHORIZED_COMMITMENT),
        ("tone",                    JudgeFindingKind.TONE),
        ("disallowed_url",          JudgeFindingKind.DISALLOWED_URL),
        ("other",                   JudgeFindingKind.OTHER),
    ],
)
def test_judge_finding_kind_enum_covers_all_five_values(value: str, kind: JudgeFindingKind):
    assert kind.value == value
    # Round-trip through JSON to make sure str-enum serialization is stable.
    finding = JudgeFinding(kind=kind, quote="q", explanation="e")
    assert json.loads(finding.model_dump_json())["kind"] == value


def test_judge_verdict_does_not_emit_additional_properties_false():
    # Regression guard: the ConfirmationEmail/ClarifyEmail models intentionally
    # do NOT set model_config = ConfigDict(extra="forbid") because that
    # emits additionalProperties:false which Gemini's response_schema rejects.
    # This test pins the same discipline on JudgeVerdict.
    schema = JudgeVerdict.model_json_schema()

    def scan(node):
        if isinstance(node, dict):
            if node.get("additionalProperties") is False:
                pytest.fail(
                    f"additionalProperties:false found in JudgeVerdict schema at {node!r}"
                )
            for v in node.values():
                scan(v)
        elif isinstance(node, list):
            for v in node:
                scan(v)

    scan(schema)
```

- [ ] **Step 1.2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_judge_verdict_schema.py -v`

Expected: all tests fail with `ModuleNotFoundError: No module named 'backend.models.judge_verdict'`.

- [ ] **Step 1.3: Write the module**

Create `backend/models/judge_verdict.py`:

```python
"""Pydantic models for the Generator-Judge outbound-email quality gate.

The Judge ``LlmAgent`` returns a :class:`JudgeVerdict`; ``JudgeStage``
persists it onto the underlying :class:`~backend.models.order_record.OrderRecord`
or :class:`~backend.models.exception_record.ExceptionRecord` and stashes
it in ``ctx.session.state['judge_verdicts']`` for ``SendStage`` to read.

Intentionally **no** ``model_config = ConfigDict(extra="forbid")`` — that
emits ``additionalProperties: false`` which Gemini's ``response_schema``
rejects (see Track A live-run audit finding F3; regression walker at
``tests/unit/test_llm_agent_factories.py``).
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class JudgeFindingKind(str, Enum):
    """Categorical tag for a judge finding — the reason the body fails."""

    HALLUCINATED_FACT = "hallucinated_fact"
    UNAUTHORIZED_COMMITMENT = "unauthorized_commitment"
    TONE = "tone"
    DISALLOWED_URL = "disallowed_url"
    OTHER = "other"


class JudgeFinding(BaseModel):
    """One concrete issue the judge flagged in the outbound body."""

    kind: JudgeFindingKind
    quote: str = Field(
        description="Verbatim snippet from the body that triggered the finding."
    )
    explanation: str = Field(
        description="Why this snippet is a problem — one sentence."
    )


class JudgeVerdict(BaseModel):
    """The judge's verdict on one drafted outbound email.

    ``status='pass'`` means the body is safe to send; ``reason`` is empty
    and ``findings`` is an empty list.

    ``status='rejected'`` means the send must be blocked; ``reason`` is
    a one-liner used directly in ``send_error='judge_rejected:<reason>'``
    and ``findings`` lists the specific issues in body-appearance order.
    """

    status: Literal["pass", "rejected"]
    reason: str = Field(
        default="",
        description="Empty on pass; one-liner on rejected.",
    )
    findings: list[JudgeFinding] = Field(
        default_factory=list,
        description="Empty on pass; structured issue list on rejected.",
    )


__all__ = ["JudgeFindingKind", "JudgeFinding", "JudgeVerdict"]
```

- [ ] **Step 1.4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_judge_verdict_schema.py -v`

Expected: 7 passed (3 parametrized + 4 standalone).

- [ ] **Step 1.5: Commit**

```bash
git add backend/models/judge_verdict.py tests/unit/test_judge_verdict_schema.py
git commit -m "feat(models): add JudgeVerdict / JudgeFinding Pydantic models

Lands the output schema for the Track B outbound-email quality gate.
Three nested models:
  - JudgeFindingKind (str Enum) — 5 values: hallucinated_fact,
    unauthorized_commitment, tone, disallowed_url, other.
  - JudgeFinding — {kind, quote, explanation} — one concrete issue.
  - JudgeVerdict — {status: pass|rejected, reason, findings[]}.

No model_config extra='forbid' on any of them — that emits
additionalProperties:false which Gemini's response_schema rejects
(F3 regression walker in test_llm_agent_factories). Schema round-trip
+ enum coverage + regression guard tests land alongside.

Track B plan Task 1."
```

---

## Task 2: Judge prompt module

**Files:**
- Create: `backend/prompts/judge.py`
- Create: `tests/unit/test_judge_prompt.py`

- [ ] **Step 2.1: Write the failing test**

Create `tests/unit/test_judge_prompt.py`:

```python
"""Smoke test on the judge prompt module.

Guards:
- SYSTEM_PROMPT + INSTRUCTION_TEMPLATE exist as non-empty strings.
- All four required {state_key} placeholders are present LITERALLY —
  the template must not f-string-resolve at module load (ADK does it
  at model-call time).
- record_kind branching is documented inside the instruction.
"""

from __future__ import annotations

from backend.prompts import judge as judge_prompt


def test_judge_prompt_has_system_prompt_and_instruction_template():
    assert isinstance(judge_prompt.SYSTEM_PROMPT, str)
    assert isinstance(judge_prompt.INSTRUCTION_TEMPLATE, str)
    assert len(judge_prompt.SYSTEM_PROMPT) > 0
    assert len(judge_prompt.INSTRUCTION_TEMPLATE) > 0


def test_judge_prompt_contains_all_four_state_key_placeholders():
    template = judge_prompt.INSTRUCTION_TEMPLATE
    assert "{judge_subject}" in template
    assert "{judge_body}" in template
    assert "{judge_record_kind}" in template
    assert "{judge_record_facts}" in template


def test_judge_prompt_instructs_record_kind_branching():
    template = judge_prompt.INSTRUCTION_TEMPLATE
    # Both branches should be called out explicitly so the model knows
    # that 'order' bodies state facts while 'exception' bodies ask
    # questions.
    assert "order" in template
    assert "exception" in template


def test_judge_system_prompt_enumerates_all_five_finding_kinds():
    s = judge_prompt.SYSTEM_PROMPT
    for k in ("hallucinated_fact", "unauthorized_commitment", "tone", "disallowed_url"):
        assert k in s, f"system prompt missing finding kind: {k}"
    # 'other' is a catch-all and may or may not be named explicitly.
```

- [ ] **Step 2.2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_judge_prompt.py -v`

Expected: all tests fail with `ModuleNotFoundError: No module named 'backend.prompts.judge'`.

- [ ] **Step 2.3: Write the prompt module**

Create `backend/prompts/judge.py`:

```python
"""Prompts for the JudgeAgent (Gemini-backed LlmAgent) — Track B gate.

The ``INSTRUCTION_TEMPLATE`` keeps ``{state_key}`` braces literal —
ADK's LlmAgent performs state injection at run time, so do not
f-string-interpolate these at module load.
"""

from __future__ import annotations

from typing import Final

SYSTEM_PROMPT: Final[str] = """\
You are a strict outbound-email quality gate for a B2B supply-chain
ordering system. You receive one email this system is about to send to
a business customer. Block the send if the body contains ANY of:

  - hallucinated_fact        any SKU, quantity, price, total, customer
                             name, or address NOT present in record_facts
  - unauthorized_commitment  any promise beyond what record_facts
                             explicitly authorizes (e.g. free shipping,
                             discounts, specific ship dates, guarantees)
                             even if the customer requested it
  - disallowed_url           any URL outside the company's own domain
  - tone                     insults, legal advice, speculation, or
                             apologies beyond brief acknowledgment

If any issue is found, return status='rejected' with findings quoting
the exact offending snippet from the body, in body-appearance order.
Otherwise return status='pass' with empty findings and empty reason.
Never rewrite or correct the body — only evaluate.
"""

INSTRUCTION_TEMPLATE: Final[str] = """\
Subject:
{judge_subject}

Body:
{judge_body}

Record kind: {judge_record_kind}
  - 'order'     confirmation email; body states facts; every number and
                SKU must trace to record_facts.
  - 'exception' clarify email; body asks questions; must not commit to
                anything; questions must be answerable by the customer.

Ground truth (record_facts JSON):
{judge_record_facts}

Return a JSON object matching the JudgeVerdict schema with exactly
three keys: `status`, `reason`, `findings`.
- status:   either "pass" or "rejected"
- reason:   "" on pass, a one-line human-readable summary on rejected
- findings: [] on pass, a list of {kind, quote, explanation} on rejected
"""

__all__ = ["SYSTEM_PROMPT", "INSTRUCTION_TEMPLATE"]
```

- [ ] **Step 2.4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_judge_prompt.py -v`

Expected: 4 passed.

- [ ] **Step 2.5: Commit**

```bash
git add backend/prompts/judge.py tests/unit/test_judge_prompt.py
git commit -m "feat(prompts): add JudgeAgent prompt template

SYSTEM_PROMPT + INSTRUCTION_TEMPLATE with four literal state-key
placeholders ({judge_subject}, {judge_body}, {judge_record_kind},
{judge_record_facts}) that ADK resolves at model-call time. Prompt
branches on record_kind inline — 'order' bodies state facts, 'exception'
bodies ask questions — so a single JudgeAgent handles both.

All four rejection kinds (hallucinated_fact, unauthorized_commitment,
tone, disallowed_url) named in the system prompt so the model has a
crisp taxonomy to map its findings to.

Track B plan Task 2."
```

---

## Task 3: `build_judge_agent()` factory + factory-suite extension

**Files:**
- Create: `backend/my_agent/agents/judge_agent.py`
- Modify: `tests/unit/test_llm_agent_factories.py`

- [ ] **Step 3.1: Write the failing tests**

Append to `tests/unit/test_llm_agent_factories.py` (do not overwrite the existing walker — just add smoke test + extend the walker's factory list):

```python
# --- Track B additions: build_judge_agent smoke + regression walker extension ---

def test_build_judge_agent_returns_correctly_configured_llmagent():
    from backend.models.judge_verdict import JudgeVerdict
    from backend.my_agent.agents.judge_agent import (
        JUDGE_AGENT_NAME,
        build_judge_agent,
    )

    agent = build_judge_agent()

    assert agent.name == JUDGE_AGENT_NAME == "judge_agent"
    assert agent.model == "gemini-3-flash-preview"
    assert agent.output_schema is JudgeVerdict
    assert agent.output_key == "judge_verdict"


def test_build_judge_agent_returns_a_fresh_instance_per_call():
    from backend.my_agent.agents.judge_agent import build_judge_agent

    a = build_judge_agent()
    b = build_judge_agent()
    assert a is not b  # parent-conflict guard
```

Also extend the existing regression walker. Locate the walker list of factories (grep for `build_confirmation_email_agent` — the walker iterates `[build_clarify_email_agent, build_confirmation_email_agent, build_summary_agent]` or similar) and **add `build_judge_agent` to it**. The walker asserts that none of the factories emit a schema with `additionalProperties: false`. Example (adapt to the exact current shape):

```python
# In the existing regression walker test:
FACTORIES_UNDER_TEST = [
    build_clarify_email_agent,
    build_confirmation_email_agent,
    build_summary_agent,
    build_judge_agent,        # ← added by Track B
]
```

- [ ] **Step 3.2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_llm_agent_factories.py -v`

Expected: the two new smoke tests fail with `ModuleNotFoundError: No module named 'backend.my_agent.agents.judge_agent'`. The walker test also fails (can't import `build_judge_agent`).

- [ ] **Step 3.3: Write the factory**

Create `backend/my_agent/agents/judge_agent.py`:

```python
"""Factory for the JudgeAgent LlmAgent — Track B outbound-email gate.

Produces a fresh ``LlmAgent`` instance per call. The JudgeStage holds
the returned agent as an attribute and invokes it via
``child.run_async(ctx)``; the validated ``JudgeVerdict`` output lands
on ``ctx.session.state['judge_verdict']`` for the stage to copy out
per iteration.

A fresh instance per call avoids ADK's "agent already has a parent"
validation error when the same instance would otherwise be reused
across stages or test setups (same pattern as ClarifyEmailAgent +
ConfirmationEmailAgent).
"""

from __future__ import annotations

from typing import Final

from google.adk.agents import LlmAgent

from backend.models.judge_verdict import JudgeVerdict
from backend.prompts.judge import INSTRUCTION_TEMPLATE, SYSTEM_PROMPT

JUDGE_AGENT_NAME: Final[str] = "judge_agent"


def build_judge_agent() -> LlmAgent:
    """Return a freshly constructed JudgeAgent LlmAgent.

    Each call yields a new instance to avoid parent-conflict errors
    when the agent is held as an attribute on a BaseAgent stage.
    """
    combined_instruction = f"{SYSTEM_PROMPT}\n\n{INSTRUCTION_TEMPLATE}"
    return LlmAgent(
        name=JUDGE_AGENT_NAME,
        model="gemini-3-flash-preview",
        description=(
            "Evaluates drafted outbound emails (confirmation + clarify) "
            "against the underlying order/exception record before Gmail send."
        ),
        instruction=combined_instruction,
        output_schema=JudgeVerdict,
        output_key="judge_verdict",
    )


__all__ = ["JUDGE_AGENT_NAME", "build_judge_agent"]
```

- [ ] **Step 3.4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_llm_agent_factories.py -v`

Expected: all existing factory tests green + both new smoke tests pass + regression walker passes for 4 factories (clarify, confirmation, summary, judge).

- [ ] **Step 3.5: Commit**

```bash
git add backend/my_agent/agents/judge_agent.py tests/unit/test_llm_agent_factories.py
git commit -m "feat(agents): add build_judge_agent LlmAgent factory

Track B outbound-email quality gate. Mirror of build_confirmation_email_agent:
- name='judge_agent', model='gemini-3-flash-preview'
- output_schema=JudgeVerdict (output_key='judge_verdict')
- instruction = SYSTEM_PROMPT + INSTRUCTION_TEMPLATE concatenated
- fresh LlmAgent per call (parent-conflict guard)

Adds per-factory smoke test + extends the regression walker to
cover JudgeVerdict for the additionalProperties:false guard.

Track B plan Task 3."
```

---

## Task 4: `OrderRecord` schema v4 → v5 (add `judge_verdict`)

**Files:**
- Modify: `backend/models/order_record.py`
- Modify: `tests/unit/test_order_record_schema.py` (or the schema block in `test_order_store.py` — follow existing precedent; if neither exists, add a schema test to `tests/unit/test_order_record_schema.py`)

- [ ] **Step 4.1: Preflight — verify prerequisite schema version**

Run:

```bash
uv run python -c "from backend.models.order_record import OrderRecord; print(OrderRecord.model_fields['schema_version'].default)"
```

Expected output: `4`

If it prints `2` or `3`, Track A2 (and Track C, if applicable) haven't landed yet — **stop** and complete the prerequisite tracks before proceeding. The spec's `depends_on` list requires A2 landed first.

- [ ] **Step 4.2: Write the failing test**

Append to `tests/unit/test_order_record_schema.py` (create if missing):

```python
from backend.models.order_record import OrderRecord
from backend.models.judge_verdict import (
    JudgeFinding,
    JudgeFindingKind,
    JudgeVerdict,
)


def test_order_record_schema_version_is_5_after_track_b():
    # Track B bumps v4 -> v5 by adding judge_verdict.
    assert OrderRecord.model_fields["schema_version"].default == 5


def test_order_record_judge_verdict_defaults_to_none():
    # Make a minimal record via the same shape the coordinator uses;
    # if the existing file has a _sample_order() helper, import and use it.
    # Otherwise reuse test_order_store's _sample_order via a direct import.
    from tests.unit.test_order_store import _sample_order   # existing helper
    record = _sample_order()
    assert record.judge_verdict is None


def test_order_record_accepts_populated_judge_verdict():
    from tests.unit.test_order_store import _sample_order
    verdict = JudgeVerdict(
        status="rejected",
        reason="hallucinated total",
        findings=[
            JudgeFinding(
                kind=JudgeFindingKind.HALLUCINATED_FACT,
                quote="$999.99",
                explanation="order.total is 127.40",
            )
        ],
    )
    record = _sample_order().model_copy(update={"judge_verdict": verdict})
    dumped = record.model_dump(mode="json")
    assert dumped["judge_verdict"]["status"] == "rejected"
    assert dumped["judge_verdict"]["findings"][0]["kind"] == "hallucinated_fact"
```

*(If `_sample_order` isn't in `test_order_store.py` — grep for it; the test already lives somewhere in `tests/unit/` from Track A.)*

- [ ] **Step 4.3: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_order_record_schema.py -v`

Expected: first test fails (`schema_version == 4, not 5`); second test fails with `AttributeError: 'OrderRecord' object has no attribute 'judge_verdict'`; third test fails similarly.

- [ ] **Step 4.4: Add the field + bump schema_version**

In `backend/models/order_record.py`, edit the `OrderRecord` class body:

```python
# (Existing imports + class body. Add near confirmation_body + send fields.)
from backend.models.judge_verdict import JudgeVerdict     # NEW import


class OrderRecord(BaseModel):
    # ... existing fields ...

    confirmation_body: Optional[str] = None       # (existing — from ConfirmStage)

    # A2 fields (assumed present pre-Track-B):
    sent_at:    Optional[datetime] = None
    send_error: Optional[str]      = None

    # Track B addition:
    judge_verdict: Optional[JudgeVerdict] = None
    """Populated by JudgeStage before SendStage fires. None until the
    stage has evaluated this record's drafted body. ``status='rejected'``
    records a send_error on the record and skips the Gmail send."""

    schema_version: int = 5    # was 4; Track B bumps for judge_verdict
```

**Do not** reorder existing fields — append `judge_verdict` after the A2 fields and before `schema_version`. The migration note comment on `schema_version` should be updated to mention v4 → v5 and the judge_verdict addition.

Also update any in-class docstring that says "schema v4" to "schema v5" and adds one line explaining `judge_verdict`.

- [ ] **Step 4.5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_order_record_schema.py tests/unit/test_order_store.py -v`

Expected: all existing OrderRecord tests green (schema_version now 5 everywhere) + the three new tests pass.

If any existing test hard-codes `schema_version == 4`, update it to `5` in the same commit.

- [ ] **Step 4.6: Commit**

```bash
git add backend/models/order_record.py tests/unit/test_order_record_schema.py tests/unit/test_order_store.py
git commit -m "feat(models): OrderRecord v4 -> v5 — add judge_verdict field

Optional[JudgeVerdict] field populated by the new JudgeStage (Track B)
before SendStage fires. Stays None on duplicate path and on entries
where no body was drafted (ESCALATE exceptions). schema_version bump
from 4 (A2 baseline) to 5.

Any existing test hard-coding schema_version==4 is updated to 5.

Track B plan Task 4."
```

---

## Task 5: `ExceptionRecord` schema v3 → v4 (add `judge_verdict`)

**Files:**
- Modify: `backend/models/exception_record.py`
- Modify: `tests/unit/test_exception_record_schema.py` (or the schema block in `test_exception_store.py`)

- [ ] **Step 5.1: Preflight — verify prerequisite schema version**

Run:

```bash
uv run python -c "from backend.models.exception_record import ExceptionRecord; print(ExceptionRecord.model_fields['schema_version'].default)"
```

Expected output: `3`

If not 3, Track A2 hasn't landed (or schema drift exists). Stop and fix before proceeding.

- [ ] **Step 5.2: Write the failing test**

Append to `tests/unit/test_exception_record_schema.py` (create if missing):

```python
from backend.models.exception_record import ExceptionRecord
from backend.models.judge_verdict import JudgeFinding, JudgeFindingKind, JudgeVerdict


def test_exception_record_schema_version_is_4_after_track_b():
    assert ExceptionRecord.model_fields["schema_version"].default == 4


def test_exception_record_judge_verdict_defaults_to_none():
    from tests.unit.test_exception_store import _sample_exception
    record = _sample_exception()
    assert record.judge_verdict is None


def test_exception_record_accepts_populated_judge_verdict():
    from tests.unit.test_exception_store import _sample_exception
    verdict = JudgeVerdict(
        status="rejected",
        reason="clarify body makes an unauthorized commitment",
        findings=[
            JudgeFinding(
                kind=JudgeFindingKind.UNAUTHORIZED_COMMITMENT,
                quote="we will ship immediately",
                explanation="clarify emails ask questions; no ship commitment authorized.",
            )
        ],
    )
    record = _sample_exception().model_copy(update={"judge_verdict": verdict})
    dumped = record.model_dump(mode="json")
    assert dumped["judge_verdict"]["status"] == "rejected"
    assert dumped["judge_verdict"]["findings"][0]["kind"] == "unauthorized_commitment"
```

- [ ] **Step 5.3: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_exception_record_schema.py -v`

Expected: `schema_version == 3, not 4`; `AttributeError` on `judge_verdict`.

- [ ] **Step 5.4: Add the field + bump schema_version**

In `backend/models/exception_record.py`:

```python
from backend.models.judge_verdict import JudgeVerdict     # NEW import


class ExceptionRecord(BaseModel):
    # ... existing fields ...

    clarify_body: Optional[str] = None     # (existing)

    # A2 fields (assumed present pre-Track-B):
    sent_at:    Optional[datetime] = None
    send_error: Optional[str]      = None

    # Track B addition:
    judge_verdict: Optional[JudgeVerdict] = None
    """Populated by JudgeStage before SendStage fires. None until the
    stage has evaluated this record's drafted clarify body. ESCALATE
    exceptions without clarify_body skip the judge and stay None."""

    schema_version: int = 4    # was 3; Track B bumps for judge_verdict
```

Update any docstring referencing "schema v3".

- [ ] **Step 5.5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_exception_record_schema.py tests/unit/test_exception_store.py -v`

Expected: all tests green; any `schema_version == 3` hard-codes updated to `4`.

- [ ] **Step 5.6: Commit**

```bash
git add backend/models/exception_record.py tests/unit/test_exception_record_schema.py tests/unit/test_exception_store.py
git commit -m "feat(models): ExceptionRecord v3 -> v4 — add judge_verdict field

Optional[JudgeVerdict] populated by JudgeStage. Stays None for
ESCALATE exceptions that have no clarify_body (judge has nothing to
evaluate). schema_version bump 3 (A2 baseline) -> 4.

Track B plan Task 5."
```

---

## Task 6: `OrderStore.update_with_judge_verdict` — protocol + impl + unit + emulator

**Files:**
- Modify: `backend/persistence/base.py`
- Modify: `backend/persistence/orders_store.py`
- Modify: `tests/unit/test_order_store.py`
- Modify: `tests/integration/test_order_store_emulator.py`

- [ ] **Step 6.1: Write the failing unit tests**

Append to `tests/unit/test_order_store.py`:

```python
# --- Track B: update_with_judge_verdict ---

@pytest.mark.asyncio
async def test_update_with_judge_verdict_happy_path(fake_client):
    """Saved order gains judge_verdict via field-mask update; get() reflects it."""
    from backend.models.judge_verdict import (
        JudgeFinding,
        JudgeFindingKind,
        JudgeVerdict,
    )
    from backend.persistence.orders_store import FirestoreOrderStore

    store  = FirestoreOrderStore(fake_client)
    order  = _sample_order()
    source = order.source_message_id

    await store.save(order)

    verdict = JudgeVerdict(
        status="rejected",
        reason="hallucinated total",
        findings=[
            JudgeFinding(
                kind=JudgeFindingKind.HALLUCINATED_FACT,
                quote="$999.99",
                explanation="order.total is 127.40",
            )
        ],
    )
    await store.update_with_judge_verdict(source, verdict)

    restored = await store.get(source)
    assert restored is not None
    assert restored.judge_verdict == verdict


@pytest.mark.asyncio
async def test_update_with_judge_verdict_raises_notfound_for_missing_doc(fake_client):
    from google.cloud.firestore_v1.base_client import NotFound  # or the library's NotFound

    from backend.models.judge_verdict import JudgeVerdict
    from backend.persistence.orders_store import FirestoreOrderStore

    store = FirestoreOrderStore(fake_client)
    with pytest.raises(NotFound):
        await store.update_with_judge_verdict(
            "nonexistent-source-id",
            JudgeVerdict(status="pass", reason="", findings=[]),
        )


@pytest.mark.asyncio
async def test_update_with_judge_verdict_overwrites_prior_verdict(fake_client):
    """No idempotency skip — re-call overwrites.
    Matches update_with_confirmation semantics."""
    from backend.models.judge_verdict import JudgeVerdict
    from backend.persistence.orders_store import FirestoreOrderStore

    store  = FirestoreOrderStore(fake_client)
    order  = _sample_order()
    source = order.source_message_id
    await store.save(order)

    v1 = JudgeVerdict(status="pass", reason="", findings=[])
    await store.update_with_judge_verdict(source, v1)
    assert (await store.get(source)).judge_verdict == v1

    v2 = JudgeVerdict(status="rejected", reason="tone issue", findings=[])
    await store.update_with_judge_verdict(source, v2)
    assert (await store.get(source)).judge_verdict == v2
```

*(The exact `NotFound` import path matches Track A's existing `update_with_confirmation` NotFound test — copy it verbatim from that file.)*

- [ ] **Step 6.2: Run unit tests to verify they fail**

Run: `uv run pytest tests/unit/test_order_store.py -k judge_verdict -v`

Expected: all three fail with `AttributeError: 'FirestoreOrderStore' object has no attribute 'update_with_judge_verdict'`.

- [ ] **Step 6.3: Add to the Protocol + impl**

In `backend/persistence/base.py`, extend the `OrderStore` protocol:

```python
from backend.models.judge_verdict import JudgeVerdict     # NEW import


class OrderStore(Protocol):
    # ... existing methods ...

    async def update_with_judge_verdict(
        self,
        source_message_id: str,
        verdict: JudgeVerdict,
    ) -> None:
        """Write ``verdict`` onto the persisted order via field-mask
        update. Raises ``NotFound`` if the source doc does not exist —
        callers must call this only AFTER :meth:`save`. No idempotency
        skip; re-calls overwrite the prior value."""
        ...
```

In `backend/persistence/orders_store.py`, add the impl in `FirestoreOrderStore`:

```python
from backend.models.judge_verdict import JudgeVerdict     # NEW import


class FirestoreOrderStore:
    # ... existing methods ...

    async def update_with_judge_verdict(
        self,
        source_message_id: str,
        verdict: JudgeVerdict,
    ) -> None:
        doc_ref = self._client.collection("orders").document(source_message_id)
        await doc_ref.update(
            {"judge_verdict": verdict.model_dump(mode="json")}
        )
```

The `DocumentReference.update` method raises `NotFound` natively if the doc is absent; no extra guard needed. `mode="json"` is required for the enum to serialize to its `str` value.

- [ ] **Step 6.4: Run unit tests to verify they pass**

Run: `uv run pytest tests/unit/test_order_store.py -v`

Expected: all tests green (pre-existing + new three).

- [ ] **Step 6.5: Add the emulator round-trip test**

Append to `tests/integration/test_order_store_emulator.py`:

```python
@pytest.mark.asyncio
@pytest.mark.firestore_emulator
async def test_update_with_judge_verdict_round_trips_against_emulator(
    emulator_firestore_client,
):
    """SDK-parity guard: the field-mask update for judge_verdict
    round-trips cleanly through the real Firestore emulator."""
    from backend.models.judge_verdict import (
        JudgeFinding,
        JudgeFindingKind,
        JudgeVerdict,
    )
    from backend.persistence.orders_store import FirestoreOrderStore

    store = FirestoreOrderStore(emulator_firestore_client)
    order = _sample_order()
    await store.save(order)

    verdict = JudgeVerdict(
        status="rejected",
        reason="hallucinated total",
        findings=[
            JudgeFinding(
                kind=JudgeFindingKind.HALLUCINATED_FACT,
                quote="$999.99",
                explanation="order.total is 127.40",
            )
        ],
    )
    await store.update_with_judge_verdict(order.source_message_id, verdict)

    restored = await store.get(order.source_message_id)
    assert restored.judge_verdict == verdict
```

- [ ] **Step 6.6: Run emulator test**

Run: `uv run pytest tests/integration/test_order_store_emulator.py -v`

Expected: the new test passes (Firestore emulator fixture assumed up per conftest).

- [ ] **Step 6.7: Commit**

```bash
git add backend/persistence/base.py backend/persistence/orders_store.py tests/unit/test_order_store.py tests/integration/test_order_store_emulator.py
git commit -m "feat(persistence): OrderStore.update_with_judge_verdict

Protocol extension on backend/persistence/base.py; impl on
FirestoreOrderStore via doc_ref.update({'judge_verdict': ...}) —
field-mask write. Raises NotFound when doc is absent (callers only
invoke post-save); no idempotency skip (re-calls overwrite —
matching update_with_confirmation).

Three unit tests (happy path, missing doc, re-call overwrite) +
one emulator round-trip for SDK parity.

Track B plan Task 6."
```

---

## Task 7: `ExceptionStore.update_with_judge_verdict` — protocol + impl + unit + emulator

**Files:**
- Modify: `backend/persistence/base.py`
- Modify: `backend/persistence/exceptions_store.py`
- Modify: `tests/unit/test_exception_store.py`
- Modify: `tests/integration/test_exception_store_emulator.py`

Mechanically identical to Task 6 but for `ExceptionStore` / `FirestoreExceptionStore` / `exceptions` collection.

- [ ] **Step 7.1: Write the failing unit tests**

Append to `tests/unit/test_exception_store.py`:

```python
# --- Track B: update_with_judge_verdict ---

@pytest.mark.asyncio
async def test_update_with_judge_verdict_happy_path(fake_client):
    from backend.models.judge_verdict import (
        JudgeFinding,
        JudgeFindingKind,
        JudgeVerdict,
    )
    from backend.persistence.exceptions_store import FirestoreExceptionStore

    store  = FirestoreExceptionStore(fake_client)
    record = _sample_exception()
    source = record.source_message_id
    await store.save(record)

    verdict = JudgeVerdict(
        status="rejected",
        reason="unauthorized commitment in clarify body",
        findings=[
            JudgeFinding(
                kind=JudgeFindingKind.UNAUTHORIZED_COMMITMENT,
                quote="we will ship immediately",
                explanation="clarify emails ask; no commitment authorized.",
            )
        ],
    )
    await store.update_with_judge_verdict(source, verdict)

    restored = await store.get(source)
    assert restored is not None
    assert restored.judge_verdict == verdict


@pytest.mark.asyncio
async def test_update_with_judge_verdict_raises_notfound_for_missing_doc(fake_client):
    from google.cloud.firestore_v1.base_client import NotFound    # match the other test's path
    from backend.models.judge_verdict import JudgeVerdict
    from backend.persistence.exceptions_store import FirestoreExceptionStore

    store = FirestoreExceptionStore(fake_client)
    with pytest.raises(NotFound):
        await store.update_with_judge_verdict(
            "nonexistent-source-id",
            JudgeVerdict(status="pass", reason="", findings=[]),
        )


@pytest.mark.asyncio
async def test_update_with_judge_verdict_overwrites_prior_verdict(fake_client):
    from backend.models.judge_verdict import JudgeVerdict
    from backend.persistence.exceptions_store import FirestoreExceptionStore

    store  = FirestoreExceptionStore(fake_client)
    record = _sample_exception()
    source = record.source_message_id
    await store.save(record)

    v1 = JudgeVerdict(status="pass", reason="", findings=[])
    await store.update_with_judge_verdict(source, v1)
    assert (await store.get(source)).judge_verdict == v1

    v2 = JudgeVerdict(status="rejected", reason="tone", findings=[])
    await store.update_with_judge_verdict(source, v2)
    assert (await store.get(source)).judge_verdict == v2
```

- [ ] **Step 7.2: Run unit tests to verify they fail**

Run: `uv run pytest tests/unit/test_exception_store.py -k judge_verdict -v`

Expected: three failures with `AttributeError: 'FirestoreExceptionStore' object has no attribute 'update_with_judge_verdict'`.

- [ ] **Step 7.3: Add to the Protocol + impl**

In `backend/persistence/base.py`, extend `ExceptionStore`:

```python
class ExceptionStore(Protocol):
    # ... existing methods ...

    async def update_with_judge_verdict(
        self,
        source_message_id: str,
        verdict: JudgeVerdict,
    ) -> None:
        """Same contract as OrderStore.update_with_judge_verdict —
        field-mask update; ``NotFound`` on missing doc; no idempotency skip."""
        ...
```

In `backend/persistence/exceptions_store.py`:

```python
from backend.models.judge_verdict import JudgeVerdict


class FirestoreExceptionStore:
    # ... existing methods ...

    async def update_with_judge_verdict(
        self,
        source_message_id: str,
        verdict: JudgeVerdict,
    ) -> None:
        doc_ref = self._client.collection("exceptions").document(source_message_id)
        await doc_ref.update(
            {"judge_verdict": verdict.model_dump(mode="json")}
        )
```

- [ ] **Step 7.4: Run unit tests to verify they pass**

Run: `uv run pytest tests/unit/test_exception_store.py -v`

Expected: green.

- [ ] **Step 7.5: Add the emulator round-trip test**

Append to `tests/integration/test_exception_store_emulator.py`:

```python
@pytest.mark.asyncio
@pytest.mark.firestore_emulator
async def test_update_with_judge_verdict_round_trips_against_emulator(
    emulator_firestore_client,
):
    from backend.models.judge_verdict import (
        JudgeFinding,
        JudgeFindingKind,
        JudgeVerdict,
    )
    from backend.persistence.exceptions_store import FirestoreExceptionStore

    store  = FirestoreExceptionStore(emulator_firestore_client)
    record = _sample_exception()
    await store.save(record)

    verdict = JudgeVerdict(
        status="rejected",
        reason="tone drift",
        findings=[
            JudgeFinding(
                kind=JudgeFindingKind.TONE,
                quote="sorry for the trouble, we'll figure it out",
                explanation="clarify emails should ask precise questions, not apologize.",
            )
        ],
    )
    await store.update_with_judge_verdict(record.source_message_id, verdict)

    restored = await store.get(record.source_message_id)
    assert restored.judge_verdict == verdict
```

- [ ] **Step 7.6: Run emulator test**

Run: `uv run pytest tests/integration/test_exception_store_emulator.py -v`

Expected: green.

- [ ] **Step 7.7: Commit**

```bash
git add backend/persistence/base.py backend/persistence/exceptions_store.py tests/unit/test_exception_store.py tests/integration/test_exception_store_emulator.py
git commit -m "feat(persistence): ExceptionStore.update_with_judge_verdict

Mirror of Task 6 for the exceptions collection. Three unit tests +
one emulator round-trip. Same NotFound + no-idempotency semantics.

Track B plan Task 7."
```

---

## Task 8: `JudgeStage` — the stage class + 8 unit tests

**Files:**
- Create: `backend/my_agent/stages/judge.py`
- Create: `tests/unit/test_stage_judge.py`

- [ ] **Step 8.1: Write the failing tests**

Create `tests/unit/test_stage_judge.py`:

```python
"""Unit tests for JudgeStage — the Track B outbound-email quality gate.

Harness uses the shared `FakeChildLlmAgent` + `make_stage_ctx` helpers
from `tests/unit/_stage_testing.py`. Deps are Protocol-typed stores
satisfied by `AsyncMock(spec=...)`.

Covered scenarios:
  1. happy-path pass for kind='order'
  2. happy-path pass for kind='exception'
  3. rejected verdict persists findings onto record
  4. LLM exception -> fail-closed synth verdict
  5. malformed output (ValidationError) -> fail-closed
  6. duplicate entry skipped (no verdict write)
  7. reply_handled short-circuit (no child invocation)
  8. stage name + canonical position contract
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions

from backend.models.judge_verdict import (
    JudgeFinding,
    JudgeFindingKind,
    JudgeVerdict,
)
from backend.my_agent.stages.judge import JUDGE_STAGE_NAME, JudgeStage
from backend.persistence.base import ExceptionStore, OrderStore
from tests.unit._stage_testing import (
    FakeChildLlmAgent,
    collect_events,
    final_state_delta,
    make_stage_ctx,
)


def _order_process_result(*, source_id: str = "src-order-1") -> dict:
    """Minimal ProcessResult-dict for an AUTO_APPROVE order."""
    return {
        "filename":       "body.txt",
        "sub_doc_index":  0,
        "result": {
            "kind":               "order",
            "source_message_id":  source_id,
            "order": {
                "source_message_id": source_id,
                "customer": {"name": "MM Machine", "customer_id": "CUST-00042"},
                "lines": [
                    {"quantity": 20, "product": {
                        "sku": "WID-RED-100", "short_description": "widget red",
                        "uom": "EA", "price_at_time": 4.20,
                    }, "line_total": 84.00},
                ],
                "order_total":       84.00,
                "confirmation_body": "Hi MM Machine, thank you for your order of 20 EA WID-RED-100.",
                "status":            "AUTO_APPROVE",
            },
        },
    }


def _exception_process_result(*, source_id: str = "src-exc-1") -> dict:
    return {
        "filename":       "body.txt",
        "sub_doc_index":  0,
        "result": {
            "kind":               "exception",
            "source_message_id":  source_id,
            "exception": {
                "source_message_id": source_id,
                "customer":          {"name": "MM Machine"},
                "exception_type":    "MISSING_SHIP_TO",
                "reason":            "ship-to address was not provided",
                "missing_fields":    ["ship_to_address"],
                "clarify_body":      "Hi MM Machine, could you share the ship-to address for this order?",
                "status":            "PENDING_CLARIFY",
            },
        },
    }


def _duplicate_process_result(*, source_id: str = "src-dup-1") -> dict:
    return {
        "filename":       "body.txt",
        "sub_doc_index":  0,
        "result": {
            "kind":              "duplicate",
            "source_message_id": source_id,
            "order":             None,
            "exception":         None,
        },
    }


def _minimal_envelope_dict() -> dict:
    return {
        "source_message_id": "src-order-1",
        "subject":           "PO #2026-04-24",
        "from_address":      "ops@mm-machine.example",
        "to_address":        "orders@gr-mro.example",
        "received_at":       "2026-04-24T15:00:00+00:00",
        "in_reply_to":       None,
        "attachments":       [],
    }


@pytest.mark.asyncio
async def test_judge_stage_pass_on_order_persists_and_stashes_verdict():
    audit = AsyncMock()
    order_store = AsyncMock(spec=OrderStore)
    exc_store   = AsyncMock(spec=ExceptionStore)
    pass_verdict = JudgeVerdict(status="pass", reason="", findings=[])
    child = FakeChildLlmAgent(
        name="judge_agent",
        output_key="judge_verdict",
        payload=pass_verdict.model_dump(mode="json"),
    )

    stage = JudgeStage(
        judge_agent=child, order_store=order_store,
        exception_store=exc_store, audit_logger=audit,
    )

    ctx = make_stage_ctx(state={
        "envelope":        _minimal_envelope_dict(),
        "process_results": [_order_process_result()],
    })

    events = await collect_events(stage.run_async(ctx))
    delta  = final_state_delta(events)

    assert "judge_verdicts" in delta
    assert delta["judge_verdicts"]["src-order-1"]["status"] == "pass"
    order_store.update_with_judge_verdict.assert_awaited_once()
    exc_store.update_with_judge_verdict.assert_not_awaited()


@pytest.mark.asyncio
async def test_judge_stage_pass_on_exception_writes_to_exception_store():
    audit = AsyncMock()
    order_store = AsyncMock(spec=OrderStore)
    exc_store   = AsyncMock(spec=ExceptionStore)
    pass_verdict = JudgeVerdict(status="pass", reason="", findings=[])
    child = FakeChildLlmAgent(
        name="judge_agent",
        output_key="judge_verdict",
        payload=pass_verdict.model_dump(mode="json"),
    )

    stage = JudgeStage(
        judge_agent=child, order_store=order_store,
        exception_store=exc_store, audit_logger=audit,
    )

    ctx = make_stage_ctx(state={
        "envelope":        _minimal_envelope_dict(),
        "process_results": [_exception_process_result()],
    })

    await collect_events(stage.run_async(ctx))

    exc_store.update_with_judge_verdict.assert_awaited_once()
    order_store.update_with_judge_verdict.assert_not_awaited()


@pytest.mark.asyncio
async def test_judge_stage_rejected_persists_findings_and_stashes_verdict():
    audit = AsyncMock()
    order_store = AsyncMock(spec=OrderStore)
    exc_store   = AsyncMock(spec=ExceptionStore)
    rejected = JudgeVerdict(
        status="rejected",
        reason="hallucinated total",
        findings=[
            JudgeFinding(
                kind=JudgeFindingKind.HALLUCINATED_FACT,
                quote="$999.99",
                explanation="order.order_total is 84.00 but body claims $999.99",
            )
        ],
    )
    child = FakeChildLlmAgent(
        name="judge_agent",
        output_key="judge_verdict",
        payload=rejected.model_dump(mode="json"),
    )

    stage = JudgeStage(
        judge_agent=child, order_store=order_store,
        exception_store=exc_store, audit_logger=audit,
    )

    ctx = make_stage_ctx(state={
        "envelope":        _minimal_envelope_dict(),
        "process_results": [_order_process_result()],
    })

    events = await collect_events(stage.run_async(ctx))
    delta  = final_state_delta(events)

    assert delta["judge_verdicts"]["src-order-1"]["status"] == "rejected"
    assert (delta["judge_verdicts"]["src-order-1"]["findings"][0]["kind"]
            == "hallucinated_fact")

    # Order store got the full verdict (field-mask update).
    call = order_store.update_with_judge_verdict.await_args
    assert call.args[0] == "src-order-1"
    persisted_verdict = call.args[1]
    assert persisted_verdict.status == "rejected"
    assert len(persisted_verdict.findings) == 1


@pytest.mark.asyncio
async def test_judge_stage_fails_closed_on_child_exception():
    class RaisingChild:
        """Duck-typed child that raises on run_async."""
        name = "judge_agent"
        async def run_async(self, ctx):    # noqa: D401 — protocol stub
            raise RuntimeError("simulated Gemini outage")
            yield  # pragma: no cover

    audit = AsyncMock()
    order_store = AsyncMock(spec=OrderStore)
    exc_store   = AsyncMock(spec=ExceptionStore)

    stage = JudgeStage(
        judge_agent=RaisingChild(), order_store=order_store,
        exception_store=exc_store, audit_logger=audit,
    )

    ctx = make_stage_ctx(state={
        "envelope":        _minimal_envelope_dict(),
        "process_results": [_order_process_result()],
    })

    events = await collect_events(stage.run_async(ctx))
    delta  = final_state_delta(events)

    v = delta["judge_verdicts"]["src-order-1"]
    assert v["status"] == "rejected"
    assert v["reason"].startswith("judge_unavailable:")
    assert v["findings"] == []

    # Synth verdict still persisted so SendStage can read it.
    order_store.update_with_judge_verdict.assert_awaited_once()
    # Audit emitted judge_unavailable.
    kinds = [c.args[0] if c.args else c.kwargs.get("event_kind")
             for c in audit.emit.await_args_list]
    assert "judge_unavailable" in kinds


@pytest.mark.asyncio
async def test_judge_stage_fails_closed_on_validation_error():
    """Child produces a malformed payload (missing required field)
    that Pydantic cannot validate."""
    audit = AsyncMock()
    order_store = AsyncMock(spec=OrderStore)
    exc_store   = AsyncMock(spec=ExceptionStore)
    # Intentionally missing 'status' — model_validate will raise.
    bad_payload = {"reason": "malformed", "findings": []}
    child = FakeChildLlmAgent(
        name="judge_agent",
        output_key="judge_verdict",
        payload=bad_payload,
    )

    stage = JudgeStage(
        judge_agent=child, order_store=order_store,
        exception_store=exc_store, audit_logger=audit,
    )

    ctx = make_stage_ctx(state={
        "envelope":        _minimal_envelope_dict(),
        "process_results": [_order_process_result()],
    })

    events = await collect_events(stage.run_async(ctx))
    delta  = final_state_delta(events)

    v = delta["judge_verdicts"]["src-order-1"]
    assert v["status"] == "rejected"
    assert "judge_unavailable:" in v["reason"]


@pytest.mark.asyncio
async def test_judge_stage_skips_duplicate_entries():
    """Duplicates were judged on a prior run; no new verdict written,
    nothing persisted, nothing audited for judge_verdict_*."""
    audit = AsyncMock()
    order_store = AsyncMock(spec=OrderStore)
    exc_store   = AsyncMock(spec=ExceptionStore)
    child = FakeChildLlmAgent(
        name="judge_agent", output_key="judge_verdict",
        payload={"status": "pass", "reason": "", "findings": []},
    )

    stage = JudgeStage(
        judge_agent=child, order_store=order_store,
        exception_store=exc_store, audit_logger=audit,
    )

    ctx = make_stage_ctx(state={
        "envelope":        _minimal_envelope_dict(),
        "process_results": [_duplicate_process_result()],
    })

    events = await collect_events(stage.run_async(ctx))
    delta  = final_state_delta(events)

    assert delta["judge_verdicts"] == {}
    order_store.update_with_judge_verdict.assert_not_awaited()
    exc_store.update_with_judge_verdict.assert_not_awaited()


@pytest.mark.asyncio
async def test_judge_stage_short_circuits_on_reply_handled():
    """If reply_handled=True (ReplyShortCircuitStage fired), JudgeStage
    emits an empty delta and does not invoke the child or touch stores."""
    audit = AsyncMock()
    order_store = AsyncMock(spec=OrderStore)
    exc_store   = AsyncMock(spec=ExceptionStore)
    child = AsyncMock()
    child.name = "judge_agent"

    stage = JudgeStage(
        judge_agent=child, order_store=order_store,
        exception_store=exc_store, audit_logger=audit,
    )

    ctx = make_stage_ctx(state={
        "envelope":        _minimal_envelope_dict(),
        "process_results": [_order_process_result()],
        "reply_handled":   True,
    })

    events = await collect_events(stage.run_async(ctx))
    delta  = final_state_delta(events)

    assert delta == {"judge_verdicts": {}}
    child.run_async.assert_not_called()
    order_store.update_with_judge_verdict.assert_not_awaited()


def test_judge_stage_name_constant_is_exported():
    assert JUDGE_STAGE_NAME == "judge_stage"
    # Construction contract: kwargs-only, no positional deps.
    with pytest.raises(TypeError):
        JudgeStage(
            AsyncMock(), AsyncMock(spec=OrderStore),
            AsyncMock(spec=ExceptionStore), AsyncMock(),
        )
```

- [ ] **Step 8.2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_stage_judge.py -v`

Expected: all fail with `ModuleNotFoundError: No module named 'backend.my_agent.stages.judge'`.

- [ ] **Step 8.3: Write the JudgeStage**

Create `backend/my_agent/stages/judge.py`:

```python
"""The :class:`JudgeStage` — stage #10 of the Order Intake pipeline.

Runs after :class:`~backend.my_agent.stages.finalize.FinalizeStage`.
For every entry in ``state['process_results']`` whose
``result.kind`` is ``"order"`` or ``"exception"`` and which carries a
drafted body (``confirmation_body`` or ``clarify_body``), this stage
invokes the injected judge :class:`~google.adk.agents.LlmAgent` to
evaluate the body against the underlying record's ground-truth facts,
writes a :class:`~backend.models.judge_verdict.JudgeVerdict` onto the
persisted record via ``OrderStore.update_with_judge_verdict`` /
``ExceptionStore.update_with_judge_verdict``, and stashes all verdicts
on ``state['judge_verdicts']`` (keyed by ``source_message_id``) for
:class:`~backend.my_agent.stages.send.SendStage` to read.

Fail-closed posture: any exception during ``run_async`` or during
``JudgeVerdict.model_validate`` synthesizes a
``JudgeVerdict(status="rejected", reason="judge_unavailable:<exc>",
findings=[])``. SendStage reads ``status != "pass"`` and blocks the
send. No email leaves the system unverified.

``kind == "duplicate"`` entries are skipped — a duplicate was judged
on the prior run; re-judging would overwrite the stored verdict. Same
short-circuit as ConfirmStage's ``kind=="duplicate"`` skip.

This stage follows the AuditedStage mixin pattern from Track D:
  1. Override ``_audited_run`` instead of ``_run_async_impl``.
  2. Emit custom lifecycle events (``judge_verdict_passed`` /
     ``judge_verdict_rejected`` / ``judge_unavailable``) via
     ``self._audit_logger.emit(...)``.

Short-circuit: if ``state['reply_handled']`` is ``True``, this stage
no-ops — emits an empty ``judge_verdicts={}`` delta. The child
LlmAgent is not invoked; neither store is touched.
"""

from __future__ import annotations

import json
from typing import Any, AsyncGenerator, Final

from google.adk.agents.invocation_context import InvocationContext
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions
from google.genai import types
from pydantic import PrivateAttr, ValidationError

from backend.ingestion.email_envelope import EmailEnvelope
from backend.models.judge_verdict import JudgeVerdict
from backend.my_agent.stages._audited import AuditedStage
from backend.persistence.base import ExceptionStore, OrderStore

JUDGE_STAGE_NAME: Final[str] = "judge_stage"


class JudgeStage(AuditedStage):
    """AuditedStage that evaluates drafted outbound emails.

    Dep-injection: PrivateAttr-as-Any for the child agent (Pydantic
    isinstance checks would reject FakeChildLlmAgent in tests; same
    rationale as ClarifyStage + ConfirmStage). Stores are Protocol-typed
    so AsyncMock(spec=OrderStore) satisfies them.
    """

    name: str = JUDGE_STAGE_NAME
    _judge_agent:     Any             = PrivateAttr()
    _order_store:     OrderStore      = PrivateAttr()
    _exception_store: ExceptionStore  = PrivateAttr()

    def __init__(
        self,
        *,
        judge_agent:     Any,
        order_store:     OrderStore,
        exception_store: ExceptionStore,
        audit_logger:    Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(audit_logger=audit_logger, **kwargs)
        self._judge_agent     = judge_agent
        self._order_store     = order_store
        self._exception_store = exception_store

    async def _audited_run(    # type: ignore[override]
        self, ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        # Short-circuit: reply was handled upstream.
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

        envelope_dict = ctx.session.state.get("envelope")
        if envelope_dict is None:
            raise ValueError(
                "JudgeStage requires IngestStage to have populated "
                "state['envelope']"
            )
        envelope = EmailEnvelope.model_validate(envelope_dict)

        judge_verdicts: dict[str, dict] = {}

        for entry in process_results:
            result = entry.get("result", {})
            kind   = result.get("kind")

            if kind == "duplicate":
                continue

            subject, body, source_id = _extract_draft(entry, envelope)
            if body is None:
                continue   # no draft to judge (ESCALATE exceptions, etc.)

            record_facts = _flatten_facts(entry)

            # Seed {state_key} placeholders by direct mutation of state
            # (ADK state_delta is NOT committed between parent + child in
            # the same run_async; see backend/my_agent/stages/confirm.py
            # docstring §1 for the ConditionalRouter gotcha).
            ctx.session.state["judge_subject"]      = subject
            ctx.session.state["judge_body"]         = body
            ctx.session.state["judge_record_kind"]  = kind
            ctx.session.state["judge_record_facts"] = json.dumps(
                record_facts, default=str
            )

            try:
                last_payload: Any = None
                async for event in self._judge_agent.run_async(ctx):
                    if (
                        event.actions
                        and event.actions.state_delta
                        and "judge_verdict" in event.actions.state_delta
                    ):
                        last_payload = event.actions.state_delta["judge_verdict"]
                    yield event
                if last_payload is None:
                    raise RuntimeError("judge agent produced no output")
                verdict = JudgeVerdict.model_validate(last_payload)
            except (Exception,) as exc:    # noqa: BLE001 — fail-closed by design
                verdict = JudgeVerdict(
                    status="rejected",
                    reason=f"judge_unavailable:{type(exc).__name__}",
                    findings=[],
                )
                await self._audit_logger.emit(
                    event_kind="judge_unavailable",
                    stage=JUDGE_STAGE_NAME,
                    payload={
                        "source_message_id": source_id,
                        "record_kind":       kind,
                        "exception":         type(exc).__name__,
                    },
                    correlation_id=ctx.session.state.get("correlation_id"),
                )

            # Persist onto the record (field-mask update).
            if kind == "order":
                await self._order_store.update_with_judge_verdict(source_id, verdict)
            else:  # exception
                await self._exception_store.update_with_judge_verdict(source_id, verdict)

            event_kind = (
                "judge_verdict_passed" if verdict.status == "pass"
                else "judge_verdict_rejected"
            )
            await self._audit_logger.emit(
                event_kind=event_kind,
                stage=JUDGE_STAGE_NAME,
                payload={
                    "source_message_id": source_id,
                    "record_kind":       kind,
                    "reason":             verdict.reason,
                    "findings_count":    len(verdict.findings),
                    "findings":          (
                        [f.model_dump() for f in verdict.findings]
                        if verdict.status == "rejected" else []
                    ),
                },
                correlation_id=ctx.session.state.get("correlation_id"),
            )

            judge_verdicts[source_id] = verdict.model_dump(mode="json")

        rejected_count = sum(
            1 for v in judge_verdicts.values() if v["status"] == "rejected"
        )
        yield Event(
            author=JUDGE_STAGE_NAME,
            actions=EventActions(state_delta={"judge_verdicts": judge_verdicts}),
            content=types.Content(
                role="model",
                parts=[
                    types.Part(
                        text=(
                            f"Judged {len(judge_verdicts)} outbound body(ies); "
                            f"{rejected_count} rejected."
                        )
                    )
                ],
            ),
        )


def _extract_draft(entry: dict, envelope: EmailEnvelope) -> tuple[str, Any, str]:
    """Return ``(subject, body, source_message_id)``.

    Body is ``None`` if the record has no drafted body (e.g. ESCALATE
    exceptions without clarify_body, or confirmations where
    confirmation_body didn't land).
    """
    result    = entry["result"]
    kind      = result["kind"]
    source_id = result["source_message_id"]
    subject   = f"Re: {envelope.subject}" if envelope.subject else "(no subject)"

    if kind == "order":
        order = result.get("order") or {}
        body  = order.get("confirmation_body")
    elif kind == "exception":
        exc  = result.get("exception") or {}
        body = exc.get("clarify_body")
    else:
        body = None

    return subject, body, source_id


def _flatten_facts(entry: dict) -> dict[str, Any]:
    """Return the flat ground-truth dict the judge cross-checks against.

    For ``kind='order'``: customer_name/id, order_total, line_items
    (sku/qty/unit_price/line_total), status.
    For ``kind='exception'``: customer_name, exception_type, reason,
    missing_fields, status.
    """
    result = entry["result"]
    kind   = result["kind"]

    if kind == "order":
        order    = result["order"]
        customer = order.get("customer", {})
        lines    = order.get("lines", [])
        return {
            "customer_name":  customer.get("name"),
            "customer_id":    customer.get("customer_id"),
            "order_total":    order.get("order_total"),
            "line_items":     [
                {
                    "sku":        ln.get("product", {}).get("sku"),
                    "qty":        ln.get("quantity"),
                    "unit_price": ln.get("product", {}).get("price_at_time"),
                    "line_total": ln.get("line_total"),
                }
                for ln in lines
            ],
            "status":         order.get("status"),
        }

    # kind == 'exception'
    exc      = result["exception"]
    customer = exc.get("customer", {})
    return {
        "customer_name":   customer.get("name"),
        "exception_type":  exc.get("exception_type"),
        "reason":          exc.get("reason"),
        "missing_fields":  exc.get("missing_fields", []),
        "status":          exc.get("status"),
    }


__all__ = ["JUDGE_STAGE_NAME", "JudgeStage"]
```

- [ ] **Step 8.4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_stage_judge.py -v`

Expected: all 8 tests pass.

If the `audit.emit` kwarg signature mismatch surfaces (Track D's exact kwarg names), adjust the call sites in `judge.py` to match D's actual `AuditLogger.emit(...)` signature — the `await self._audit_logger.emit(...)` lines are the only points that depend on D's contract. Tests 4 and 5 (fail-closed scenarios) assert on `audit.emit.await_args_list`; the kwargs check is the source of truth for signature drift.

- [ ] **Step 8.5: Commit**

```bash
git add backend/my_agent/stages/judge.py tests/unit/test_stage_judge.py
git commit -m "feat(stages): add JudgeStage at pipeline position #10

Track B outbound-email quality gate. AuditedStage subclass that loops
state['process_results'], pulls each drafted body + flat record_facts,
invokes the injected judge LlmAgent, and persists a JudgeVerdict onto
the matching OrderRecord / ExceptionRecord via update_with_judge_verdict.
Stashes all verdicts on state['judge_verdicts'] keyed by source_message_id
for SendStage to read.

Fail-closed: any exception during child run or Pydantic validation
becomes a synthesized JudgeVerdict(status='rejected',
reason='judge_unavailable:<exc_type>', findings=[]). Audit emits
judge_verdict_passed / judge_verdict_rejected / judge_unavailable.

Short-circuits on reply_handled (no child invocation, empty delta).
Skips kind='duplicate' entries (verdict from prior run preserved).
Skips entries with no drafted body (ESCALATE exceptions).

8 unit tests: pass-order, pass-exception, rejected-with-findings,
LLM-exception -> fail-closed, ValidationError -> fail-closed,
duplicate skip, reply_handled short-circuit, name + kwargs-only
construction contract.

Track B plan Task 8."
```

---

## Task 9: Wire `JudgeStage` into the pipeline + orchestrator topology test

**Files:**
- Modify: `backend/my_agent/agent.py`
- Modify: `tests/unit/test_orchestrator_build.py`

- [ ] **Step 9.1: Preflight — verify prerequisite AGENT_VERSION**

Run:

```bash
uv run python -c "from backend.my_agent.agent import AGENT_VERSION; print(AGENT_VERSION)"
```

Expected: `track-a-v0.3`. If it prints `track-a-v0.2`, Track A2 hasn't landed (SendStage missing too). Stop.

- [ ] **Step 9.2: Write the failing topology test updates**

In `tests/unit/test_orchestrator_build.py`, update the canonical stage order and add a JudgeStage type-assertion:

```python
# --- update existing CANONICAL_STAGE_ORDER constant ---
CANONICAL_STAGE_ORDER = [
    "ingest_stage",
    "reply_shortcircuit_stage",
    "classify_stage",
    "parse_stage",
    "validate_stage",
    "clarify_stage",
    "persist_stage",
    "confirm_stage",
    "finalize_stage",
    "judge_stage",      # NEW — position #10 (0-indexed 9)
    "send_stage",       # A2 — now position #11 (0-indexed 10)
]

# --- update/extend the build_root_agent topology test ---
def test_root_agent_has_11_stages_in_canonical_order():
    deps = _make_deps()
    root = build_root_agent(**deps)

    assert root.name == ROOT_AGENT_NAME == "order_intake_pipeline"
    assert len(root.sub_agents) == 11
    assert [s.name for s in root.sub_agents] == CANONICAL_STAGE_ORDER


def test_judge_stage_is_subclass_at_index_9():
    from backend.my_agent.stages.judge import JudgeStage
    deps = _make_deps()
    root = build_root_agent(**deps)
    assert isinstance(root.sub_agents[9], JudgeStage)


def test_agent_version_bumped_to_v04_after_track_b():
    from backend.my_agent.agent import AGENT_VERSION
    assert AGENT_VERSION == "track-a-v0.4"


def test_build_root_agent_requires_judge_agent_kwarg():
    deps = _make_deps()
    del deps["judge_agent"]
    with pytest.raises(TypeError):
        build_root_agent(**deps)


def test_build_root_agent_requires_exception_store_judge_dep():
    # JudgeStage needs both stores even though it reads ProcessResult.kind
    # to decide which one to call — construction-time DI pins this.
    deps = _make_deps()
    del deps["exception_store"]
    with pytest.raises(TypeError):
        build_root_agent(**deps)
```

Also extend the `_make_deps()` helper in the same file to include a `judge_agent` fake (FakeChildLlmAgent with pass payload) so all existing tests still construct the root agent cleanly:

```python
def _make_deps() -> dict:
    # ... existing keys: ingestion helpers, classifier, parser,
    # validator, clarify_agent, confirm_agent, summary_agent,
    # gmail_client (A2), order_store, exception_store, audit_logger,
    # send_dry_run, ...

    return {
        # ... existing entries ...
        "judge_agent": FakeChildLlmAgent(
            name="judge_agent",
            output_key="judge_verdict",
            payload={"status": "pass", "reason": "", "findings": []},
        ),
    }
```

*(The exact `_make_deps()` shape depends on the latest state from A2; the addition is purely additive.)*

- [ ] **Step 9.3: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_orchestrator_build.py -v`

Expected: topology tests fail (10 stages found, expected 11; `judge_agent` kwarg missing from `build_root_agent`).

- [ ] **Step 9.4: Wire `JudgeStage` into the pipeline**

In `backend/my_agent/agent.py`:

1. Add imports:

```python
from backend.my_agent.agents.judge_agent import build_judge_agent
from backend.my_agent.stages.judge import JudgeStage, JUDGE_STAGE_NAME
```

2. Bump `AGENT_VERSION`:

```python
AGENT_VERSION: Final[str] = "track-a-v0.4"    # was "track-a-v0.3" after A2
```

3. Extend `build_root_agent` signature with a `judge_agent` kwarg. Required, kwarg-only (matches the existing discipline):

```python
def build_root_agent(
    *,
    # ... existing kwargs ...
    judge_agent:     Any,              # NEW — LlmAgent for the Track B gate
    # ... existing post-judge kwargs (gmail_client, send_dry_run, audit_logger, etc.) ...
) -> SequentialAgent:
    # ... stage construction ...

    judge_stage = JudgeStage(
        judge_agent     = judge_agent,
        order_store     = order_store,
        exception_store = exception_store,
        audit_logger    = audit_logger,
    )

    send_stage = SendStage(
        # ... existing SendStage kwargs from A2 ...
    )

    return SequentialAgent(
        name=ROOT_AGENT_NAME,
        sub_agents=[
            ingest_stage,
            reply_shortcircuit_stage,
            classify_stage,
            parse_stage,
            validate_stage,
            clarify_stage,
            persist_stage,
            confirm_stage,
            finalize_stage,
            judge_stage,       # NEW — index 9 (0-indexed), position #10
            send_stage,        # was at index 9 post-A2; now at index 10
        ],
    )
```

4. Extend `_build_default_root_agent` to construct the judge:

```python
def _build_default_root_agent() -> SequentialAgent:
    # ... existing shared deps: client, master_data_repo, validator,
    # order_store, exception_store, audit_logger, gmail_client, etc. ...

    return build_root_agent(
        # ... existing kwargs ...
        judge_agent = build_judge_agent(),
        # ... existing post-judge kwargs ...
    )
```

- [ ] **Step 9.5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_orchestrator_build.py -v`

Expected: all tests green — 11 stages; JudgeStage at index 9; `AGENT_VERSION == "track-a-v0.4"`; missing-kwarg tests raise `TypeError`.

- [ ] **Step 9.6: Smoke-run adk-discovery to catch agent.py import regressions**

Run:

```bash
uv run python -c "from backend.my_agent.agent import root_agent; print(root_agent.name, len(root_agent.sub_agents))"
```

Expected: `order_intake_pipeline 11`.

- [ ] **Step 9.7: Commit**

```bash
git add backend/my_agent/agent.py tests/unit/test_orchestrator_build.py
git commit -m "feat(orchestrator): wire JudgeStage at #10; AGENT_VERSION v0.3 -> v0.4

Extends build_root_agent with a required judge_agent kwarg. Pipeline
grows 10 -> 11 stages (JudgeStage at 0-indexed 9, between FinalizeStage
and SendStage). _build_default_root_agent constructs a build_judge_agent()
instance and threads it + shared stores + shared AuditLogger through.

Topology test bumped to 11-stage canonical order with a JudgeStage
subclass assertion at index 9. Missing-kwarg + AGENT_VERSION tests
round out the contract.

Track B plan Task 9."
```

---

## Task 10: `SendStage` judge-gate check + 2 tests

**Files:**
- Modify: `backend/my_agent/stages/send.py`
- Modify: `tests/unit/test_stage_send.py`

- [ ] **Step 10.1: Preflight — verify send.py exists**

Run:

```bash
ls backend/my_agent/stages/send.py
```

Expected: file exists. If not, Track A2 hasn't landed — stop.

- [ ] **Step 10.2: Write the failing tests**

Append to `tests/unit/test_stage_send.py`:

```python
# --- Track B: judge-gate integration ---

@pytest.mark.asyncio
async def test_send_stage_blocks_when_judge_verdict_is_rejected():
    """Given a ProcessResult with a drafted confirmation body AND a
    rejected judge verdict in state['judge_verdicts'], SendStage must:
      - NOT call gmail_client.send_message
      - CALL update_with_send_receipt with send_error='judge_rejected:<reason>'
        and sent_at=None (matches A2's shape: one method, two kwargs).
    """
    audit        = AsyncMock()
    gmail_client = AsyncMock()
    order_store  = AsyncMock(spec=OrderStore)
    exc_store    = AsyncMock(spec=ExceptionStore)

    stage = SendStage(
        gmail_client    = gmail_client,
        order_store     = order_store,
        exception_store = exc_store,
        audit_logger    = audit,
        send_dry_run    = False,
    )

    order_pr = _order_process_result()    # from Task 8's helper, or inline
    envelope = _minimal_envelope_dict()

    ctx = make_stage_ctx(state={
        "envelope":        envelope,
        "process_results": [order_pr],
        "judge_verdicts": {
            "src-order-1": {
                "status":   "rejected",
                "reason":   "hallucinated total",
                "findings": [{
                    "kind":         "hallucinated_fact",
                    "quote":        "$999.99",
                    "explanation":  "order.total is 84.00",
                }],
            },
        },
    })

    await collect_events(stage.run_async(ctx))

    gmail_client.send_message.assert_not_called()
    order_store.update_with_send_receipt.assert_awaited_once()
    call = order_store.update_with_send_receipt.await_args
    # Receipt carries sent_at=None + send_error='judge_rejected:<reason>'.
    assert call.kwargs.get("sent_at") is None
    assert "judge_rejected:" in call.kwargs.get("send_error", "")
    assert "hallucinated total" in call.kwargs.get("send_error", "")


@pytest.mark.asyncio
async def test_send_stage_passes_through_when_judge_verdict_is_pass():
    """Pass verdict: the existing A2 send flow fires normally."""
    audit        = AsyncMock()
    gmail_client = AsyncMock()
    gmail_client.send_message = AsyncMock(return_value="gmail-msg-id-123")
    order_store  = AsyncMock(spec=OrderStore)
    exc_store    = AsyncMock(spec=ExceptionStore)

    stage = SendStage(
        gmail_client    = gmail_client,
        order_store     = order_store,
        exception_store = exc_store,
        audit_logger    = audit,
        send_dry_run    = False,
    )

    order_pr = _order_process_result()
    envelope = _minimal_envelope_dict()

    ctx = make_stage_ctx(state={
        "envelope":        envelope,
        "process_results": [order_pr],
        "judge_verdicts": {
            "src-order-1": {"status": "pass", "reason": "", "findings": []},
        },
    })

    await collect_events(stage.run_async(ctx))

    gmail_client.send_message.assert_awaited_once()
    # Success path: update_with_send_receipt called with sent_at set + send_error=None.
    order_store.update_with_send_receipt.assert_awaited_once()
    success_call = order_store.update_with_send_receipt.await_args
    assert success_call.kwargs.get("send_error") is None
    assert success_call.kwargs.get("sent_at") is not None
```

- [ ] **Step 10.3: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_stage_send.py -k judge -v`

Expected: test 1 fails (SendStage currently sends regardless of judge_verdicts); test 2 passes trivially (pass verdict doesn't change behavior) OR fails if the lookup logic isn't present yet.

- [ ] **Step 10.4: Add the judge-gate guard**

In `backend/my_agent/stages/send.py`, add the 5-line check at the **top** of `_maybe_send_confirmation` (right after the existing `sent_at`-guard from A2):

```python
async def _maybe_send_confirmation(
    self,
    ctx: InvocationContext,
    order: OrderRecord,
    envelope: EmailEnvelope,
) -> None:
    # Existing A2 guard — skip if already sent this invocation (or prior run).
    if order.sent_at is not None:
        return

    # --- Track B judge-gate (NEW) -----------------------------------
    source_id = order.source_message_id
    verdict   = ctx.session.state.get("judge_verdicts", {}).get(source_id)
    if verdict is None or verdict.get("status") != "pass":
        reason = (verdict.get("reason") if verdict else "judge_missing")
        # Match A2's single-method contract: sent_at stays None,
        # send_error records the judge rejection for operator triage.
        await self._order_store.update_with_send_receipt(
            source_id,
            sent_at=None,
            send_error=f"judge_rejected:{reason}",
        )
        await self._audit_logger.emit(
            event_kind="email_send_blocked",
            stage=SEND_STAGE_NAME,
            payload={
                "source_message_id": source_id,
                "send_error":        f"judge_rejected:{reason}",
            },
            correlation_id=ctx.session.state.get("correlation_id"),
        )
        return
    # --- end Track B judge-gate -------------------------------------

    # Existing A2 send flow below (build MIME, call gmail_client.send_message,
    # update_with_send_receipt(sent_at=now, send_error=None), audit 'email_sent', ...)
```

Apply the **same** block at the top of `_maybe_send_clarify`, reading the verdict for `exception.source_message_id` and calling `self._exception_store.update_with_send_receipt(source_id, sent_at=None, send_error=f"judge_rejected:{reason}")`.

- [ ] **Step 10.5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_stage_send.py -v`

Expected: all tests green — existing A2 tests + both new judge-gate tests.

If any pre-existing A2 test fails because its ctx didn't include `judge_verdicts` in state, update those tests to seed `{"src-whatever": {"status": "pass", "reason": "", "findings": []}}` matching the record they use — this is honest data-through-the-pipeline, not a workaround.

- [ ] **Step 10.6: Commit**

```bash
git add backend/my_agent/stages/send.py tests/unit/test_stage_send.py
git commit -m "feat(stages): SendStage reads judge_verdicts, blocks non-pass

Adds a short guard at the top of _maybe_send_confirmation and
_maybe_send_clarify (right after the existing sent_at check).
Reads ctx.session.state['judge_verdicts'][source_message_id]; if
absent or status != 'pass', records
update_with_send_receipt(source_id, sent_at=None,
send_error='judge_rejected:<reason>') matching A2's single-method
contract, emits audit event 'email_send_blocked', and returns
without calling gmail_client.send_message.

Defensive 'judge_missing' reason covers the (never expected)
case where JudgeStage was skipped — fails closed by design.

Two new tests: judge-rejected blocks + doesn't send; judge-pass
flows through to the existing A2 send path.

Track B plan Task 10."
```

---

## Task 11: Full 11-stage integration test

**Files:**
- Modify: `tests/integration/test_orchestrator_emulator.py`

- [ ] **Step 11.1: Write the failing test**

Append to `tests/integration/test_orchestrator_emulator.py` (or the equivalent Runner-based integration file — verify the path first):

```python
@pytest.mark.asyncio
@pytest.mark.firestore_emulator
async def test_full_pipeline_writes_judge_verdict_on_auto_approve(
    emulator_firestore_client,
    mm_machine_envelope_fixture,     # existing helper from Track A
):
    """End-to-end: ingest -> extract -> validate AUTO_APPROVE -> persist ->
    confirm (draft) -> finalize -> judge (PASS) -> send (dry-run) lands,
    and OrderRecord.judge_verdict reflects status='pass' on Firestore."""
    from backend.models.judge_verdict import JudgeVerdict
    from backend.my_agent.agent import build_root_agent
    from google.adk.runners import Runner
    from tests.unit._stage_testing import FakeChildLlmAgent

    # Stub LlmAgents — fake child agents for the two draft stages + judge.
    # Confirmation body is built from the fixture so the judge's
    # record_facts-vs-body comparison is realistic (no hallucinated values).
    confirm_child = FakeChildLlmAgent(
        name="confirmation_email_agent",
        output_key="confirmation_email",
        payload={
            "subject": "Re: PO #2026-04-24 — confirmed, $127.40",
            "body":    "Hi MM Machine, thank you for your order of 20 EA WID-RED-100 ...",
        },
    )
    judge_child = FakeChildLlmAgent(
        name="judge_agent",
        output_key="judge_verdict",
        payload={"status": "pass", "reason": "", "findings": []},
    )

    root = build_root_agent(
        # ... fixture-built deps: ingest/classify/parse/validate stubs,
        # clarify_agent stub, confirm_agent=confirm_child,
        # summary_agent stub, judge_agent=judge_child,
        # gmail_client=MockGmailClient() that records calls,
        # order_store/exception_store over the emulator client,
        # audit_logger=NoOpAuditLogger() or shared AuditLogger,
        # send_dry_run=True,
    )

    runner = Runner(
        app_name="order_intake_test",
        agent=root,
        session_service=InMemorySessionService(),   # ADK sessions
    )

    session = await runner.session_service.create_session(
        app_name="order_intake_test",
        user_id="test-user",
        state={"envelope": mm_machine_envelope_fixture.model_dump(mode="json")},
    )

    async for _ in runner.run_async(
        user_id="test-user",
        session_id=session.id,
        new_message=None,
    ):
        pass

    # Pull back the persisted OrderRecord and assert judge_verdict landed.
    from backend.persistence.orders_store import FirestoreOrderStore
    store    = FirestoreOrderStore(emulator_firestore_client)
    restored = await store.get(mm_machine_envelope_fixture.source_message_id)
    assert restored is not None
    assert restored.judge_verdict is not None
    assert restored.judge_verdict.status == "pass"
    assert restored.judge_verdict.findings == []

    # And the dry-run SendStage recorded no send_error (judge passed).
    assert restored.send_error is None
```

*(The exact stub-construction shape depends on `_make_deps()`/`build_root_agent`'s current kwargs — mirror the existing integration test's setup; the additions are `judge_agent=judge_child` and a `judge_verdicts` assertion on the returned record.)*

- [ ] **Step 11.2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_orchestrator_emulator.py -v -k judge_verdict`

Expected: may or may not run to completion depending on harness. If it runs and fails on `restored.judge_verdict is None`, the stub judge isn't being invoked — likely the 11-stage wiring from Task 9 is missing. If it passes, skip to commit.

- [ ] **Step 11.3: Resolve any harness gaps**

Common issues to chase:
- `build_root_agent` call missing `judge_agent` kwarg → seed `FakeChildLlmAgent` as in the test.
- `state["judge_verdicts"]` unavailable in SendStage because the Runner didn't commit the state_delta — integration tests go through Runner which does commit deltas between stages; so this should work naturally, unlike the in-stage test harness.

- [ ] **Step 11.4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_orchestrator_emulator.py -v -k judge_verdict`

Expected: green.

- [ ] **Step 11.5: Run the full test suite to catch unrelated breakage**

Run: `uv run pytest -x`

Expected: all previously green tests remain green. Total count approximately: 323 (pre-B) + ~20 new (schema + prompt + factory + stores + stage + send-gate + integration) = ~343.

- [ ] **Step 11.6: Commit**

```bash
git add tests/integration/test_orchestrator_emulator.py
git commit -m "test(integration): full 11-stage pipeline writes judge_verdict

End-to-end emulator test: ingest -> extract -> AUTO_APPROVE ->
persist -> confirm draft -> finalize -> judge (PASS) -> send
(dry-run). Asserts OrderRecord.judge_verdict landed on the
emulator-persisted document with status='pass'.

Stubs: confirm_agent drafts a realistic body; judge_agent returns
a pass verdict; gmail_client is a recording mock; send_dry_run=True.

Guards that JudgeStage is correctly wired into the Runner and that
state['judge_verdicts'] propagates to SendStage via ADK state_delta
commits.

Track B plan Task 11."
```

---

## Task 12: Doc flips — Sprint Status + Glacis roadmap

**Files:**
- Modify: `research/Order-Intake-Sprint-Status.md`
- Modify: `Glacis-Order-Intake.md`

- [ ] **Step 12.1: Flip the sprint status row + Built inventory**

In `research/Order-Intake-Sprint-Status.md`:

1. **Update the `last_updated` frontmatter** (line 7) — append a 2026-04-24 track-B-complete note:

```
... previous sessionb note ... **Track B complete 2026-04-24:** outbound-email quality gate lands — new JudgeStage at pipeline position #10 (SendStage moves to #11; pipeline now 11 BaseAgent stages). Single judge LlmAgent (gemini-3-flash-preview) with record_kind discriminator evaluates confirmation + clarify bodies against flat record_facts; binary pass/rejected verdict + structured findings (5-value JudgeFindingKind enum) persist on OrderRecord.judge_verdict (schema v4→v5) + ExceptionRecord.judge_verdict (schema v3→v4). Fail-closed on LLM errors (synth JudgeVerdict with reason='judge_unavailable:<exc>'). Reject blocks Gmail send via send_error='judge_rejected:<reason>'; no auto-escalate or re-draft loop. Judge runs regardless of GMAIL_SEND_DRY_RUN; only Gmail network call is gated. AGENT_VERSION track-a-v0.3 → track-a-v0.4. +~20 unit tests + 2 integration.
```

2. **Flip the status table row** (in the markdown table around line 30):

Add a new row (or modify the existing *"quality-gate judge"* bullet in §4b′):

```markdown
| **Outbound-email quality gate** | Second Gemini Flash call reviews outbound bodies before send; hard-blocks hallucinated facts / unauthorized commitments / tone / disallowed URLs | `JudgeStage` at #10, single judge LlmAgent with record_kind discriminator; `JudgeVerdict` binary + findings; fail-closed on LLM errors; reject records `send_error='judge_rejected:...'`; judge always runs regardless of GMAIL_SEND_DRY_RUN ✓ | Nothing on MVP. |
```

3. **Bump the one-line summary** (around line 42) — change "9-stage" / "10-stage" references to "11-stage" where appropriate; add *"Quality gate enforced on every outbound email (auto-confirmation + clarify)"* to the "read + judgment + persist + confirm + orchestrate" sentence.

4. **Completion metrics** (around line 44) — bump test counts (~343 unit + 12 integration).

5. **Append to the Built-vs-missing inventory** (`### Built (do not rebuild)` block near line 50) — one line per new file, citing the commit SHA from Task N where it landed. Template — fill in the actual SHAs as you land each task:

```
backend/models/judge_verdict.py                                         ✓ JudgeFindingKind / JudgeFinding / JudgeVerdict Pydantic models — 5-value enum + structured findings; no extra='forbid' (Gemini response_schema gotcha guarded by regression walker). Feeds build_judge_agent() (<SHA Task 1>, 2026-04-24, Track B plan Task 1)
backend/prompts/judge.py                                                ✓ SYSTEM_PROMPT + INSTRUCTION_TEMPLATE with {judge_subject}/{judge_body}/{judge_record_kind}/{judge_record_facts} state-key placeholders; record_kind branches inline (<SHA Task 2>, 2026-04-24, Track B plan Task 2)
backend/my_agent/agents/judge_agent.py                                  ✓ build_judge_agent() → fresh LlmAgent per call, gemini-3-flash-preview, output_schema=JudgeVerdict, output_key='judge_verdict' (<SHA Task 3>, 2026-04-24, Track B plan Task 3)
backend/models/order_record.py (schema v5)                              ✓ +judge_verdict: Optional[JudgeVerdict]; schema_version 4→5 (<SHA Task 4>, 2026-04-24, Track B plan Task 4)
backend/models/exception_record.py (schema v4)                          ✓ +judge_verdict: Optional[JudgeVerdict]; schema_version 3→4 (<SHA Task 5>, 2026-04-24, Track B plan Task 5)
backend/persistence/orders_store.py (update_with_judge_verdict)         ✓ field-mask update via doc_ref.update({'judge_verdict': ...}); NotFound on missing doc; overwrites on re-call (<SHA Task 6>, 2026-04-24, Track B plan Task 6)
backend/persistence/exceptions_store.py (update_with_judge_verdict)     ✓ mirror on exceptions collection (<SHA Task 7>, 2026-04-24, Track B plan Task 7)
backend/my_agent/stages/judge.py                                        ✓ JudgeStage(AuditedStage) at pipeline #10; _audited_run loops process_results; fail-closed synth on exceptions; kind==duplicate + reply_handled short-circuits; _extract_draft + _flatten_facts helpers (<SHA Task 8>, 2026-04-24, Track B plan Task 8)
backend/my_agent/agent.py (11-stage wiring + AGENT_VERSION v0.4)        ✓ build_root_agent +judge_agent kwarg; _build_default_root_agent wires build_judge_agent(); AGENT_VERSION 'track-a-v0.3' → 'track-a-v0.4' (<SHA Task 9>, 2026-04-24, Track B plan Task 9)
backend/my_agent/stages/send.py (judge-gate)                            ✓ 5-line block at top of _maybe_send_confirmation + _maybe_send_clarify; reads judge_verdicts; blocks non-pass with send_error='judge_rejected:<reason>'; emits 'email_send_blocked' audit (<SHA Task 10>, 2026-04-24, Track B plan Task 10)
tests/unit/test_judge_verdict_schema.py                                 ✓ 7 schema round-trip + enum coverage + additionalProperties-false regression tests (<SHA Task 1>)
tests/unit/test_judge_prompt.py                                         ✓ 4 placeholder + content-presence tests (<SHA Task 2>)
tests/unit/test_stage_judge.py                                          ✓ 8 tests: pass-order, pass-exception, rejected-with-findings, LLM-exception→fail-closed, ValidationError→fail-closed, duplicate skip, reply_handled short-circuit, name + kwargs-only (<SHA Task 8>)
tests/unit/test_orchestrator_build.py (11-stage topology)               ✓ CANONICAL_STAGE_ORDER updated; JudgeStage at index 9; AGENT_VERSION assertion; missing-kwarg guards (<SHA Task 9>)
tests/integration/test_orchestrator_emulator.py (judge verdict)         ✓ full 11-stage emulator run asserts OrderRecord.judge_verdict lands post-Runner (<SHA Task 11>)
```

6. **Update the remaining-tracks bullet list** around line 199 — flip Track B from "in progress" / "next" to "✓ landed":

Change:

```markdown
- **Track B — Generator-Judge quality gate** → ... Implementation plan cycle next.
```

to:

```markdown
- **Track B — Generator-Judge quality gate** ✓ landed 2026-04-24 — JudgeStage at pipeline #10; single judge LlmAgent with record_kind discriminator; binary verdict + structured findings; fail-closed on LLM errors; record-and-block on reject; judge runs regardless of GMAIL_SEND_DRY_RUN. OrderRecord v4→v5 + ExceptionRecord v3→v4; AGENT_VERSION track-a-v0.3→v0.4. Design spec `docs/superpowers/specs/2026-04-24-track-b-generator-judge-design.md` (20157a2); plan `docs/superpowers/plans/2026-04-24-track-b-generator-judge.md` (<plan SHA>); implementation landed across ~12 commits.
```

- [ ] **Step 12.2: Flip the Glacis roadmap**

In `Glacis-Order-Intake.md`:

1. **Frontmatter `last_updated`** (line 5) — append a Track B note matching the existing prose style.

2. **§9 "Gemini quality-gate check on outbound email"** — change `[Post-MVP]` → `[MVP ✓]` with full citation chain:

Find (around line 185):

```markdown
- `[Post-MVP]` **Gemini quality-gate check on outbound email** — secondary Flash call: "no hallucinated URLs, no hallucinated data, no unauthorized commitments, professional tone". MVP: —. Post-hackathon: mandatory before sending *anything* to real customers. Source: `Generator-Judge.md`, `Exception-Handling.md`.
```

Replace with:

```markdown
- `[MVP ✓]` **Gemini quality-gate check on outbound email** — `JudgeStage` (BaseAgent #10, inserted between `FinalizeStage` and `SendStage`) holds a structured-output Gemini `LlmAgent` (`build_judge_agent()` returning `gemini-3-flash-preview` with `output_schema=JudgeVerdict(status: 'pass'|'rejected', reason, findings: list[JudgeFinding])` where `JudgeFindingKind` enum covers hallucinated_fact / unauthorized_commitment / tone / disallowed_url / other). Per drafted body, seeds `{judge_subject, judge_body, judge_record_kind, judge_record_facts}` on `ctx.session.state`, invokes child, captures verdict, writes onto `OrderRecord.judge_verdict` (schema v5) / `ExceptionRecord.judge_verdict` (schema v4) via new `update_with_judge_verdict` field-mask updates, stashes on `state['judge_verdicts']` for SendStage to read. Fail-closed on LLM errors (synth reason `judge_unavailable:<exc>`). `SendStage` reads verdict after `sent_at`-guard; non-pass → `send_error='judge_rejected:<reason>'` + `email_send_blocked` audit event + no Gmail call. `GMAIL_SEND_DRY_RUN` only gates the network call — judge always runs. `AGENT_VERSION` track-a-v0.3 → track-a-v0.4. MVP: `backend/my_agent/stages/judge.py` + `backend/models/judge_verdict.py` + `backend/prompts/judge.py` + `backend/my_agent/agents/judge_agent.py` + store protocol/impl additions + SendStage gate + 11-stage topology update (SHAs <fill in at land-time>). Source: `Generator-Judge.md`, `Exception-Handling.md`.
```

3. **Phase 3 roadmap** (around line 308) — remove the judge bullet:

Find:

```markdown
- Gmail send + Gemini quality-gate review for outbound clarify + confirmation bodies (§9 — generation + clarify-reply correlation already landed MVP; only the Gmail-send side + judge remain)
```

Replace with (Gmail send is Track A2's concern; the judge is now MVP):

```markdown
- Gmail send for outbound clarify + confirmation bodies (§9 — generation + clarify-reply correlation + quality-gate judge already landed MVP; only the Gmail-send side remains, tracked by Track A2)
```

*(Or remove entirely if A2 has also landed by the time Track B lands — in which case §9 is fully green.)*

4. **Full validation-loop Generator-Judge remains `[Nice-to-have]`** — do NOT flip §4b.c `[Nice-to-have]` *"Generator-Judge quality gate before auto-execute"* in §4 Validation. That's the bigger three-stage loop, still out of scope.

- [ ] **Step 12.3: Run existing tests to confirm docs changes don't cause churn**

Run: `uv run pytest -x`

Expected: all green. (Sanity check — doc-only commits shouldn't affect tests.)

- [ ] **Step 12.4: Commit**

```bash
git add research/Order-Intake-Sprint-Status.md Glacis-Order-Intake.md
git commit -m "docs: flip Track B + §9 quality-gate to MVP complete

Sprint status:
- last_updated frontmatter appends Track B completion note.
- Adds/flips 'Outbound-email quality gate' row in status table.
- One-line summary + completion metrics bumped to 11 stages / ~343
  tests.
- Built inventory appends 10 new/modified files per plan task with
  landing-commit SHAs.
- Remaining-tracks bullet for Track B flipped 'next' -> '✓ landed'.

Glacis-Order-Intake.md:
- §9 'Gemini quality-gate check on outbound email' [Post-MVP] ->
  [MVP checkmark] with full citation chain (JudgeStage, JudgeVerdict,
  Judge prompt, judge_agent factory, store extensions, SendStage
  gate, 11-stage topology, AGENT_VERSION v0.4).
- Phase 3 roadmap updated: Gmail send (A2) is the only remaining §9
  item.
- last_updated bumped to 2026-04-24 with Track B narrative.

The bigger three-stage Generator-Judge VALIDATION loop from the
Glacis deep-dive note (wrapping extraction + validation with a
LoopAgent) stays [Nice-to-have] — out of Track B's scope by design.

Track B plan Task 12."
```

---

## Post-implementation verification

After all 12 tasks land:

- [ ] **Run full unit suite:** `uv run pytest tests/unit -v`

Expected: ~343 tests green (323 baseline + ~20 new from Track B).

- [ ] **Run integration suite:** `uv run pytest tests/integration -v`

Expected: ~12 integration tests green.

- [ ] **Run smoke evalset:** `uv run adk eval adk_apps/order_intake tests/eval/smoke.evalset.json --config_file_path tests/eval/eval_config.json`

Expected: 3-case smoke set passes (stub judge passes each body; SendStage dry-run).

- [ ] **Live-smoke sanity (optional):**

```bash
GMAIL_SEND_DRY_RUN=1 uv run python scripts/smoke_run.py data/email/mm_machine_reorder_2026-04-24.eml
```

Expected: pipeline runs 11 stages; real Gemini Flash judge evaluates the ConfirmStage body and returns `status='pass'`; `send_dry_run` logs `dry_run: judge=pass, would send to ops@mm-machine.example`; Firestore emulator has `OrderRecord.judge_verdict` populated.

- [ ] **Adk-web discovery check:**

```bash
uv run adk web adk_apps
```

Expected: `order_intake_pipeline` listed as one agent; opening it in the UI shows 11 stages including `judge_stage` at position #10; running a fixture shows judge_verdict_* events in the trace.

---

## Execution notes

- **Total tasks:** 12.
- **Estimated execution time:** ~5–7h (comparable to A2: 10 tasks / 5–7h).
- **Per-task commit SHAs:** fill in the Built-inventory entries in Task 12 by running `git log --oneline` at land-time.
- **Test count delta:** +~20 unit + 2 integration. Pipeline total ends around **~343 unit + ~12 integration**.
- **Schema chain assumption:** C → D → A1 → A2 → A3 → B → E per the session handoff. If executed out of order, Task 4 / Task 5 / Task 9 preflight checks fail fast with clear guidance.
- **Track D dependency:** `AuditedStage` subclass + `_audit_logger.emit(...)` contract assumed. If Track D has NOT landed by the time this plan runs, you have two options:
  1. Execute Track D first (recommended — matches the handoff order).
  2. Swap `class JudgeStage(AuditedStage)` → `class JudgeStage(BaseAgent)`, rename `_audited_run` → `_run_async_impl`, drop the `_audit_logger` PrivateAttr + kwarg, and stub `await self._audit_logger.emit(...)` as inline no-ops. Subsequent refactor to mixin-inheritance is a one-commit diff.

**Last step:** `research/Order-Intake-Sprint-Status.md` auto-updates with Track B completion via Task 12; the stop hook's staleness guard will pass cleanly post-commit.

End of plan.
