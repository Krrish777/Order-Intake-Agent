# ConfirmStage — Order Intake AUTO_APPROVE Confirmation Email

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Before writing any ADK code, load `/adk-cheatsheet` and `/adk-dev-guide`. **Do not change the Gemini model string**: `gemini-3-flash-preview` is load-bearing (see `clarify_email_agent.py:34`).

**Goal:** Add a 9th pipeline stage (`ConfirmStage`) that, for every AUTO_APPROVE order persisted by `PersistStage`, drafts a customer-facing confirmation email body via a Gemini LlmAgent, persists it onto the existing `OrderRecord`, and surfaces it in the `adk web` event stream.

**Architecture:** Mirror the existing `ClarifyStage` + `build_clarify_email_agent` + `ClarifyEmail` + `clarify_email` prompt triad. Insert the new stage between `PersistStage` (slot 7) and `FinalizeStage` (slot 8 → 9). ConfirmStage reads `state["process_results"]` to find `kind == "order"` entries, seeds placeholder state keys by direct `ctx.session.state` mutation, drives an injected child `LlmAgent` via `run_async`, and for each emitted `ConfirmationEmail` calls `order_store.update_with_confirmation(source_message_id, body)` to mutate the persisted order doc in Firestore. `OrderRecord` gains a `confirmation_body: Optional[str] = None` field (schema_version bumped 1 → 2, mirroring the Track A Step 1 precedent on `ExceptionRecord.clarify_body`).

**Tech Stack:** Python 3.13, ADK (`google.adk.agents.BaseAgent` + `LlmAgent`), Pydantic v2, `google-cloud-firestore` (async client), pytest / pytest-asyncio, `rapidfuzz` (unchanged). Gemini model: `gemini-3-flash-preview`. Run all commands with `uv run`.

---

## Context

**Why this change.** Today's AUTO_APPROVE path is silent to the customer: the pipeline resolves the customer, matches SKUs, builds an `OrderRecord`, writes it to Firestore, and emits a run summary — but nothing ever goes back to the buyer. The user confirmed this gap after successfully landing the `mm_machine_reorder_2026-04-24.eml` test fixture through `adk web`: "What is good is that we have processed the order but we didn't reply to it with an order confirmation email."

The Glacis spec calls for a dashboard + outbound email path (`CLAUDE.md`, project doc). Track A built the **CLARIFY** leg of that (`ClarifyStage` → `ClarifyEmail` body lands on `ExceptionRecord.clarify_body`), but not the **AUTO_APPROVE** leg. This plan closes that second leg with the minimum machinery for a demo: body-only generation (no Gmail send), visible in the `adk web` trace, persisted on the order doc.

**Scope (per user decisions on 2026-04-24):**
- Body-only — same output shape as `ClarifyEmail`: `{subject, body}` dict in state, body-only string on the persisted record
- AUTO_APPROVE coverage only. CLARIFY keeps using existing `ClarifyStage`; ESCALATE remains silent to the customer
- Surface: `adk web` event-stream text is the only consumer this sprint. Dashboard + `outbound/` folder + Gmail send are deferred to future work (Track D and Post-MVP per `research/Order-Intake-Sprint-Status.md:31`)
- Architecture: new dedicated `ConfirmStage` inserted between `PersistStage` and `FinalizeStage`. No changes to `RunSummary` counters — confirmations are not surfaced in the `run_summary` schema this sprint

**Out of scope (defer):**
- No `updated_at` field on `OrderRecord` yet — we're mutating the doc but for demo simplicity we trust a single ConfirmStage write per order. Revisit in Track D if the dashboard needs it
- No evalset updates in the smoke set. Loose thresholds already tolerate the added event stream; rigorous confirmation-email eval is a follow-up
- No Gmail send. The `confirmation_body` on `OrderRecord` is purely the rendered text; `confirmation_message_id` equivalent is deliberately NOT added (no send path to produce one)

---

## File Structure

**New files (5):**
- `backend/models/confirmation_email.py` — `ConfirmationEmail(BaseModel)` with `subject`, `body` fields. Mirrors `backend/models/clarify_email.py:14-42`. No `extra="forbid"` — Gemini schema gotcha (see comment).
- `backend/prompts/confirmation_email.py` — `SYSTEM_PROMPT` + `INSTRUCTION_TEMPLATE` constants with literal `{state_key}` braces. Mirrors `backend/prompts/clarify_email.py`.
- `backend/my_agent/agents/confirmation_email_agent.py` — `build_confirmation_email_agent() -> LlmAgent` factory returning a fresh instance per call. Mirrors `backend/my_agent/agents/clarify_email_agent.py:25-42`.
- `backend/my_agent/stages/confirm.py` — `ConfirmStage(BaseAgent)` + `CONFIRM_STAGE_NAME`. Mirrors `backend/my_agent/stages/clarify.py` structure (PrivateAttr injection, reply_handled short-circuit, direct state-mutation template seeding, `async for event in child.run_async(ctx): yield event`, RuntimeError on missing emission).
- `tests/unit/test_stage_confirm.py` — unit tests for `ConfirmStage`. Mirrors `tests/unit/test_stage_clarify.py`.

**Modified files (6):**
- `backend/models/order_record.py` — add `confirmation_body: Optional[str] = None` field; bump `schema_version: int = 2` (mirror of the Track A Step 1 bump on `backend/models/exception_record.py:78`).
- `backend/persistence/base.py` — add `update_with_confirmation(...)` to the `OrderStore` Protocol at `backend/persistence/base.py:23-34`.
- `backend/persistence/orders_store.py` — implement `update_with_confirmation` on `FirestoreOrderStore`. Mirrors the shape of `FirestoreExceptionStore.update_with_reply` (check file for exact patterns) but simpler — no status guard needed because the single `OrderStatus.PERSISTED` is the only state today.
- `backend/my_agent/agent.py` — import + thread `ConfirmStage` and `build_confirmation_email_agent`; add `confirm_agent` and `order_store` kwargs to `build_root_agent`; insert `ConfirmStage` between `PersistStage` and `FinalizeStage` at the `sub_agents=[...]` list (`backend/my_agent/agent.py:143-152`).
- `tests/unit/test_order_store.py` — add tests for `update_with_confirmation` (happy path + missing doc raises + re-call overwrites).
- `research/Order-Intake-Sprint-Status.md` — bump the stage count (8 → 9) and add one row for the new confirmation leg.

**Also modified (required):**
- `tests/integration/test_order_store_emulator.py` — add one emulator round-trip test for `update_with_confirmation` (see Task 5.5 below). Per Track A convention every store method has both a fake-client unit test and a live emulator test.

---

## Reused Components

| Existing thing | File | Why we reuse |
|---|---|---|
| `FakeChildLlmAgent` | `tests/unit/_stage_testing.py:134-219` | Exactly the duck-typed fake ConfirmStage tests need. Parameterize with `output_key="confirmation_email"` and `capture_keys=["customer_name", "original_subject", "order_details"]`. |
| `make_stage_ctx`, `collect_events`, `final_state_delta` | `tests/unit/_stage_testing.py:55-131` | Shared ADK `InvocationContext` scaffolding. No duplication. |
| PrivateAttr injection pattern | `backend/my_agent/stages/clarify.py:72-76` | `_agent: Any = PrivateAttr()`, kwarg-only `__init__`, `super().__init__(**kwargs)` first. Same rationale (Protocol-typed deps + LlmAgent Pydantic type avoidance). |
| Direct `ctx.session.state` mutation for child-template seeding | `backend/my_agent/stages/clarify.py:146-154` (and same pattern in `finalize.py:108-118`) | State placeholders inside LlmAgent instruction templates get resolved at model-call time; single-invocation `state_delta` doesn't reach the child. Use direct mutation. |
| ADK cheatsheet §5 (`ConditionalRouter` pattern) | `/adk-cheatsheet` references/python.md | Authoritative reference the existing stages cite. Re-check before writing. |
| Gemini-schema-safe regression walker | `tests/unit/test_llm_agent_factories.py` | This test already asserts no LlmAgent's `output_schema` emits `additionalProperties: false`. Adding `ConfirmationEmail` to the set of scanned factories will cause this test to pick up the new one automatically — no new test needed IF the factory is discoverable. Verify after Task 3. |
| Idempotent `create(exists=False)` pattern | `backend/persistence/orders_store.py:25-34` | Existing `save()`. `update_with_confirmation` is a different operation (update, not create) — use `doc_ref.update({"confirmation_body": body})` which is field-mask-by-default in the async SDK. |

---

## Tasks

### Task 1: `ConfirmationEmail` Pydantic schema

**Files:**
- Create: `backend/models/confirmation_email.py`
- Test: `tests/unit/test_confirmation_email_schema.py` (new — small)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_confirmation_email_schema.py`:

```python
"""Smoke tests for the ConfirmationEmail output_schema.

Verifies the two-field contract and — critically — that the schema does
NOT emit ``additionalProperties: false`` in its JSON schema. Gemini's
``generation_config.response_schema`` rejects that field with a 400.
The Track A live-run audit (research/Order-Intake-Sprint-Status.md
line 7, F3) caught this on ``ClarifyEmail`` and the regression walker
in test_llm_agent_factories.py now guards all factories — this test
is a faster unit-level check for the same property on this schema
alone.
"""

from __future__ import annotations

from backend.models.confirmation_email import ConfirmationEmail


def test_fields_and_types() -> None:
    inst = ConfirmationEmail(subject="Re: order confirmed", body="Hi there, got it.")
    assert inst.subject == "Re: order confirmed"
    assert inst.body == "Hi there, got it."


def test_schema_has_no_additional_properties_false() -> None:
    """Gemini 400 regression: extra='forbid' would emit this and break."""
    schema = ConfirmationEmail.model_json_schema()
    # Pydantic default (no extra='forbid') should NOT emit this key.
    assert schema.get("additionalProperties") is not False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_confirmation_email_schema.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.models.confirmation_email'`

- [ ] **Step 3: Write the schema**

Create `backend/models/confirmation_email.py`:

```python
"""Pydantic schema for the ConfirmationEmailAgent output.

Used as ``output_schema`` for the Gemini-backed LlmAgent that drafts
customer-facing order confirmations on AUTO_APPROVE decisions. Field
descriptions double as guidance to Gemini via the generated JSON schema.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ConfirmationEmail(BaseModel):
    """One drafted order-confirmation email to send back to the customer.

    Used as ``output_schema`` on the Gemini ConfirmationEmailAgent.
    Intentionally does NOT set ``model_config = ConfigDict(extra="forbid")``
    — Pydantic emits ``additionalProperties: false`` for that, which
    Gemini's ``generation_config.response_schema`` rejects with a 400.
    Pydantic's default silently-ignore-extra is the right behavior here:
    the LLM's output is what we validate, not untrusted user input.
    """

    subject: str = Field(
        ...,
        description=(
            "Email subject line. Reuse the original order subject where "
            "possible, prefixed with 'Re: ' so it threads, and append a "
            "brief confirmation marker (e.g., 'confirmed') with the order "
            "total. Keep under ~80 characters."
        ),
    )
    body: str = Field(
        ...,
        description=(
            "Plain-text email body, 5 to 8 sentences. Warm but concise. "
            "Echo the line items, quantities, and pricing verbatim from "
            "the order details provided. Mention the ship-to address and "
            "payment terms. Do NOT invent ship dates, promotions, or "
            "anything not present in the provided order details. Sign "
            "off professionally."
        ),
    )


__all__ = ["ConfirmationEmail"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_confirmation_email_schema.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/models/confirmation_email.py tests/unit/test_confirmation_email_schema.py
git commit -m "feat: add ConfirmationEmail output_schema"
```

---

### Task 2: Confirmation email prompt module

**Files:**
- Create: `backend/prompts/confirmation_email.py`

- [ ] **Step 1: Write the prompt module**

Create `backend/prompts/confirmation_email.py`:

```python
"""Prompts for the ConfirmationEmailAgent (Gemini-backed LlmAgent).

The ``INSTRUCTION_TEMPLATE`` keeps ``{state_key}`` braces literal —
ADK's LlmAgent performs state injection at run time, so do not
f-string-interpolate these at module load.
"""

from __future__ import annotations

from typing import Final

SYSTEM_PROMPT: Final[str] = (
    "You draft a brief, professional order-confirmation email to a "
    "customer whose purchase order was auto-approved. Echo the order "
    "details back faithfully — do not invent SKUs, quantities, prices, "
    "ship dates, or promotions. Keep it warm but concise — 5 to 8 "
    "sentences."
)

INSTRUCTION_TEMPLATE: Final[str] = """\
Draft an order-confirmation email to the customer whose PO we just accepted.

Customer name: {customer_name}
Original email subject: {original_subject}

Order details (use these verbatim — do not restate units or re-calculate):
{order_details}

Reference id: {order_ref}

Requirements:
- Subject should be "Re: " plus the original subject, with " — confirmed, $TOTAL" appended
  where $TOTAL is the order total from order_details. Keep under ~80 characters.
- Body is 5 to 8 sentences of plain text. No HTML, no markdown, no bullet points.
  Conversational paragraph form is fine; a short indented item list in plain text is ok.
- Echo every line item with its quantity, SKU, description, unit price, and line total
  exactly as given in order_details.
- Mention the ship-to address and payment terms as given.
- Do not promise specific ship dates (lead times are not in order_details).
- Tone: warm but professional. Sign off with "Thanks," followed by
  "Grafton-Reese MRO" and the orders@ address.
- Include the reference id on a trailing "Ref: " line.
- Do not mention that you are an AI or reference internal systems.

Return a JSON object matching the ConfirmationEmail schema with exactly two keys:
`subject` and `body`.
"""

__all__ = ["SYSTEM_PROMPT", "INSTRUCTION_TEMPLATE"]
```

- [ ] **Step 2: Commit**

```bash
git add backend/prompts/confirmation_email.py
git commit -m "feat: add confirmation email prompt"
```

---

### Task 3: `build_confirmation_email_agent` factory

**Files:**
- Create: `backend/my_agent/agents/confirmation_email_agent.py`
- Test: `tests/unit/test_llm_agent_factories.py` (modify — add smoke test)

- [ ] **Step 1: Write the failing test**

Open `tests/unit/test_llm_agent_factories.py` and add a per-factory smoke test for the new factory. Look at the existing tests for `build_clarify_email_agent` and `build_summary_agent` and **copy the structure verbatim** — the file has a regression walker that iterates known factories. Add `build_confirmation_email_agent` to whatever data structure that test uses so it gets picked up.

Minimum per-factory assertions (mirror the clarify test):

```python
from backend.my_agent.agents.confirmation_email_agent import (
    CONFIRMATION_EMAIL_AGENT_NAME,
    build_confirmation_email_agent,
)
from backend.models.confirmation_email import ConfirmationEmail


def test_confirmation_email_agent_shape() -> None:
    agent = build_confirmation_email_agent()
    assert agent.name == CONFIRMATION_EMAIL_AGENT_NAME
    assert agent.model == "gemini-3-flash-preview"
    assert agent.output_schema is ConfirmationEmail
    assert agent.output_key == "confirmation_email"
    # Placeholders must appear in the instruction for ADK state injection.
    assert "{customer_name}" in agent.instruction
    assert "{original_subject}" in agent.instruction
    assert "{order_details}" in agent.instruction
    assert "{order_ref}" in agent.instruction


def test_confirmation_email_agent_is_fresh_per_call() -> None:
    a = build_confirmation_email_agent()
    b = build_confirmation_email_agent()
    assert id(a) != id(b)
```

- [ ] **Step 2: Run to verify fails**

Run: `uv run pytest tests/unit/test_llm_agent_factories.py -v`
Expected: FAIL with ModuleNotFoundError for `backend.my_agent.agents.confirmation_email_agent`

- [ ] **Step 3: Write the factory**

Create `backend/my_agent/agents/confirmation_email_agent.py`:

```python
"""Factory for the ConfirmationEmailAgent LlmAgent.

Produces a fresh ``LlmAgent`` instance per call. The ConfirmStage
holds the returned agent as an attribute and invokes it via
``child.run_async(ctx)``; the validated ``ConfirmationEmail`` output
lands on ``ctx.session.state['confirmation_email']`` for the stage
to copy out.

A fresh instance per call avoids ADK's "agent already has a parent"
validation error when the same instance would otherwise be reused
across stages or test setups.
"""

from __future__ import annotations

from typing import Final

from google.adk.agents import LlmAgent

from backend.models.confirmation_email import ConfirmationEmail
from backend.prompts.confirmation_email import (
    INSTRUCTION_TEMPLATE,
    SYSTEM_PROMPT,
)

CONFIRMATION_EMAIL_AGENT_NAME: Final[str] = "confirmation_email_agent"


def build_confirmation_email_agent() -> LlmAgent:
    """Return a freshly constructed ConfirmationEmailAgent LlmAgent."""
    combined_instruction = f"{SYSTEM_PROMPT}\n\n{INSTRUCTION_TEMPLATE}"
    return LlmAgent(
        name=CONFIRMATION_EMAIL_AGENT_NAME,
        model="gemini-3-flash-preview",
        description=(
            "Drafts a short order-confirmation email to the customer "
            "when a PO is auto-approved."
        ),
        instruction=combined_instruction,
        output_schema=ConfirmationEmail,
        output_key="confirmation_email",
    )


__all__ = ["CONFIRMATION_EMAIL_AGENT_NAME", "build_confirmation_email_agent"]
```

- [ ] **Step 4: Run to verify passes**

Run: `uv run pytest tests/unit/test_llm_agent_factories.py -v`
Expected: PASS (new tests + existing regression walker catches the new factory)

- [ ] **Step 5: Commit**

```bash
git add backend/my_agent/agents/confirmation_email_agent.py tests/unit/test_llm_agent_factories.py
git commit -m "feat: add build_confirmation_email_agent factory"
```

---

### Task 4: Extend `OrderRecord` with `confirmation_body`

**Files:**
- Modify: `backend/models/order_record.py`

- [ ] **Step 1: Update the model**

Open `backend/models/order_record.py`. At `OrderRecord` (starting line 76):

1. Add `confirmation_body: Optional[str] = None` after `schema_version` (around line 95).
2. Change `schema_version: int = 1` → `schema_version: int = 2`.
3. Update the module docstring to note the mutation: this field is written post-save by `ConfirmStage`.

Exact diff region to write (replace the `OrderRecord` class body ending with schema_version + created_at):

```python
    source_message_id: str
    thread_id: str
    customer: CustomerSnapshot
    lines: list[OrderLine]
    order_total: float
    confidence: float = Field(..., ge=0.0, le=1.0)
    status: OrderStatus = OrderStatus.PERSISTED
    processed_by_agent_version: str
    confirmation_body: Optional[str] = None
    schema_version: int = 2
    created_at: datetime
```

And at the top of the class docstring, add one line:

```
    ``confirmation_body`` is populated post-save by
    :class:`~backend.my_agent.stages.confirm.ConfirmStage` when it
    renders a customer confirmation email. ``None`` until that stage
    runs; stays ``None`` forever for non-AUTO_APPROVE paths.
```

- [ ] **Step 2: Run the existing order_store tests to confirm nothing breaks**

Run: `uv run pytest tests/unit/test_order_store.py tests/unit/test_coordinator.py -v`
Expected: PASS — all existing tests should still pass because `confirmation_body` defaults to `None`.

- [ ] **Step 3: Commit**

```bash
git add backend/models/order_record.py
git commit -m "feat: extend OrderRecord with confirmation_body field (schema_version 1→2)"
```

---

### Task 5: Add `update_with_confirmation` to `OrderStore` + Firestore impl

**Files:**
- Modify: `backend/persistence/base.py`
- Modify: `backend/persistence/orders_store.py`
- Modify: `tests/unit/test_order_store.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_order_store.py` (adapt existing `FakeAsyncClient` fixtures from `tests/unit/conftest.py`):

```python
import pytest
from backend.persistence.orders_store import FirestoreOrderStore


async def test_update_with_confirmation_sets_body_on_existing_doc(
    fake_async_client, persisted_order_record
):
    """Happy path: doc exists → field is written → subsequent get()
    returns the body."""
    store = FirestoreOrderStore(fake_async_client)
    updated = await store.update_with_confirmation(
        persisted_order_record.source_message_id,
        confirmation_body="Thanks Tony — order confirmed, $127.40.",
    )
    assert updated.confirmation_body == "Thanks Tony — order confirmed, $127.40."

    reread = await store.get(persisted_order_record.source_message_id)
    assert reread.confirmation_body == "Thanks Tony — order confirmed, $127.40."


async def test_update_with_confirmation_raises_when_doc_missing(
    fake_async_client,
):
    """Update on a non-existent doc is a caller bug — fail fast, do not
    silently create. ConfirmStage should only call this for orders that
    PersistStage just persisted this invocation."""
    store = FirestoreOrderStore(fake_async_client)
    with pytest.raises(Exception):  # Firestore NotFound, or local equivalent
        await store.update_with_confirmation(
            "<never-existed@example.com>",
            confirmation_body="should not land",
        )


async def test_update_with_confirmation_overwrites(
    fake_async_client, persisted_order_record
):
    """Re-calling overwrites the previous body. Every pipeline run
    regenerates a fresh confirmation; no idempotency skip."""
    store = FirestoreOrderStore(fake_async_client)
    await store.update_with_confirmation(
        persisted_order_record.source_message_id,
        confirmation_body="first draft",
    )
    await store.update_with_confirmation(
        persisted_order_record.source_message_id,
        confirmation_body="second draft",
    )
    reread = await store.get(persisted_order_record.source_message_id)
    assert reread.confirmation_body == "second draft"
```

**Note:** If `persisted_order_record` or `fake_async_client` fixtures do not exist with those exact names, look at the existing `test_order_store.py` file structure and the shared `conftest.py` (`tests/unit/conftest.py`) — reuse whatever `save()` round-trip fixture is already in use. Do not introduce new fixture names if existing ones cover it. The fake client's `update` method may need to be added — check `tests/unit/conftest.py` first.

- [ ] **Step 2: Run to verify fails**

Run: `uv run pytest tests/unit/test_order_store.py -v -k update_with_confirmation`
Expected: FAIL — method does not exist.

- [ ] **Step 3: Add the Protocol**

In `backend/persistence/base.py`, extend `OrderStore`:

```python
class OrderStore(Protocol):
    """Write + read surface for the ``orders`` collection."""

    async def save(self, record: OrderRecord) -> OrderRecord:
        """Persist ``record``. Idempotent on ``source_message_id``:
        a duplicate write returns the previously persisted record
        unchanged, rather than overwriting or raising."""
        ...

    async def get(self, source_message_id: str) -> Optional[OrderRecord]:
        """Load by Firestore doc id. ``None`` if absent."""
        ...

    async def update_with_confirmation(
        self, source_message_id: str, confirmation_body: str
    ) -> OrderRecord:
        """Write ``confirmation_body`` onto an already-persisted order.

        Raises when the doc does not exist — callers should only invoke
        this for orders that were just persisted by ``save()`` in the
        same pipeline invocation. Overwrites any prior confirmation_body
        on re-call (no idempotency skip — a re-run regenerates)."""
        ...
```

- [ ] **Step 4: Implement on FirestoreOrderStore**

Append to `backend/persistence/orders_store.py`:

```python
    async def update_with_confirmation(
        self, source_message_id: str, confirmation_body: str
    ) -> OrderRecord:
        doc_ref = (
            self._client.collection(ORDERS_COLLECTION).document(source_message_id)
        )
        # Field-mask update: only the confirmation_body changes. The
        # Firestore async SDK's `.update()` raises NotFound if the
        # document is absent — the caller contract is that this is only
        # invoked after `save()` in the same invocation, so that's
        # exactly the failure mode we want to surface.
        await doc_ref.update({"confirmation_body": confirmation_body})
        snap = await doc_ref.get()
        return OrderRecord(**snap.to_dict())
```

- [ ] **Step 5: Run tests — iterate on `FakeAsyncClient` if needed**

Run: `uv run pytest tests/unit/test_order_store.py -v -k update_with_confirmation`
Expected: PASS.

If `FakeAsyncClient` in `tests/unit/conftest.py` lacks `update` support, add it — the existing additive-extension comment in the conftest (referenced at `research/Order-Intake-Sprint-Status.md:115`) says tests were extended for create/set/update + SERVER_TIMESTAMP. An `update` method that writes into the in-memory dict and raises `google.api_core.exceptions.NotFound` when the doc is missing is the minimum. Commit the fake extension as a separate commit if needed.

- [ ] **Step 6: Commit**

```bash
git add backend/persistence/base.py backend/persistence/orders_store.py tests/unit/test_order_store.py
# + conftest.py if you extended the fake
git commit -m "feat: add update_with_confirmation to OrderStore"
```

---

### Task 5.5: Emulator integration test for `update_with_confirmation`

**Files:**
- Modify: `tests/integration/test_order_store_emulator.py`

- [ ] **Step 1: Write one round-trip test against the live emulator**

Append a test that follows the existing file's pattern — it will already be decorated with the `firestore_emulator` pytest marker and use a real `AsyncFirestoreClient` (see `tests/integration/test_exception_store_emulator.py` for the mirror on the exception side). Minimum assertion shape:

```python
@pytest.mark.firestore_emulator
async def test_update_with_confirmation_persists_to_emulator(
    emulator_client, sample_order_record
):
    """Happy path on a real emulator: save → update_with_confirmation
    → fresh get() reflects the new body."""
    store = FirestoreOrderStore(emulator_client)
    await store.save(sample_order_record)

    body = "Tony — confirmed, $127.40. Shipping ground."
    updated = await store.update_with_confirmation(
        sample_order_record.source_message_id,
        confirmation_body=body,
    )
    assert updated.confirmation_body == body

    reread = await store.get(sample_order_record.source_message_id)
    assert reread is not None
    assert reread.confirmation_body == body
```

Reuse whatever `emulator_client` / `sample_order_record` fixtures already exist in the file's `conftest.py`. If the sample order fixture doesn't exist yet, build one inline from a minimal `OrderRecord` constructor — one customer snapshot, one line, round numbers are fine because it's an integration test, not a content test.

- [ ] **Step 2: Run (requires emulator running)**

```bash
firebase emulators:start --only firestore &
FIRESTORE_EMULATOR_HOST=localhost:8080 uv run pytest tests/integration/test_order_store_emulator.py -v -k update_with_confirmation
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_order_store_emulator.py
git commit -m "test: emulator integration test for update_with_confirmation"
```

---

### Task 6: `ConfirmStage` — the BaseAgent

**Files:**
- Create: `backend/my_agent/stages/confirm.py`
- Create: `tests/unit/test_stage_confirm.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_stage_confirm.py`. Mirror `tests/unit/test_stage_clarify.py` case-for-case, substituting `confirm` vocabulary. Tests to include (each is 10–40 lines):

1. `test_reply_handled_no_ops` — `reply_handled=True` → child never invoked; `confirmation_bodies={}`; `skipped_docs` preserved; no store update call.
2. `test_missing_process_results_raises` — state without `process_results` → `ValueError` matching "requires PersistStage".
3. `test_missing_envelope_raises` — state without `envelope` → `ValueError` matching "requires IngestStage".
4. `test_no_auto_approve_entries_yields_empty_bodies` — `process_results` only has `kind="exception"` + `kind="duplicate"` → child never invoked; `confirmation_bodies={}`; `store.update_with_confirmation` not called.
5. `test_single_auto_approve_entry_produces_one_body` — one AUTO_APPROVE entry → child invoked once; body lands at `confirmation_bodies["{filename}#{sub_doc_index}"]`; `store.update_with_confirmation` called once with that `source_message_id`.
6. `test_multiple_mixed_entries_filters_to_auto_only` — `process_results` = [order, exception, order] → child invoked twice, keyed correctly; assert ordering via `capture_state` snapshots.
7. `test_child_never_emits_confirmation_email_raises` — fake with `responses=None` → `RuntimeError` matching "did not produce confirmation_email".
8. `test_prompt_state_keys_seeded_from_order_and_envelope` — verify `customer_name`, `original_subject`, `order_details`, `order_ref` all set on `ctx.session.state` before child invocation.
9. `test_store_update_call_count_matches_bodies` — order_store fake (AsyncMock) is called exactly N times where N == number of AUTO_APPROVE entries. On `kind="duplicate"` the stage DOES NOT call update (a duplicate was already persisted on a prior run; its confirmation is from that prior run).

Use `FakeChildLlmAgent` from `tests/unit/_stage_testing.py:134` with:

```python
FakeChildLlmAgent(
    output_key="confirmation_email",
    capture_keys=["customer_name", "original_subject", "order_details", "order_ref"],
    name="fake_confirmation_agent",
    responses=[{"subject": "Re: PO — confirmed, $N", "body": "Thanks — got it."}],
)
```

Use `AsyncMock(spec=OrderStore)` for the store dep, following `tests/unit/test_stage_reply_shortcircuit.py` precedent.

- [ ] **Step 2: Run tests to verify all fail**

Run: `uv run pytest tests/unit/test_stage_confirm.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `ConfirmStage`**

Create `backend/my_agent/stages/confirm.py`. The shape mirrors `clarify.py` with these substitutions:

- Module docstring: describe the AUTO_APPROVE leg instead of CLARIFY leg. Keep the two architectural notes from `clarify.py:11-29` verbatim — they explain the direct state-mutation pattern, which applies here for the same reason.
- Class: `ConfirmStage(BaseAgent)` with `CONFIRM_STAGE_NAME: Final[str] = "confirm_stage"`.
- Two PrivateAttr injected deps:
  - `_confirm_agent: Any = PrivateAttr()`
  - `_order_store: OrderStore = PrivateAttr()` (concrete Protocol type is fine here; the AsyncMock in tests satisfies it)
- `__init__(self, *, confirm_agent: Any, order_store: OrderStore, **kwargs: Any) -> None:` kwarg-only.
- `_run_async_impl` logic:
  1. Short-circuit on `reply_handled=True` → yield Event with `state_delta={"confirmation_bodies": {}, "skipped_docs": [...preserved...]}`; return.
  2. Read `process_results` from state → `ValueError` if missing.
  3. Read `envelope` from state → `ValueError` if missing. Hydrate via `EmailEnvelope.model_validate`.
  4. Preserve `skipped_docs` verbatim (copy list).
  5. Filter: `auto_entries = [r for r in process_results if r.get("result", {}).get("kind") == "order"]`.
  6. Empty → emit empty `confirmation_bodies`, preserve skipped, return.
  7. For each entry:
     - Extract `order_dict = entry["result"]["order"]` (note: this is `OrderRecord.model_dump(mode="json")`).
     - Compose `order_details` string — see _compose_order_details helper below.
     - Seed state keys by **direct ctx.session.state mutation**: `customer_name`, `original_subject`, `order_details`, `order_ref`. (Same pattern as `clarify.py:146-154`.)
     - `last_confirmation_email: Any = None`
     - `async for event in self._confirm_agent.run_async(ctx):` capture `state_delta["confirmation_email"]` if present; `yield event` to forward upward.
     - RuntimeError if `last_confirmation_email is None`.
     - `body_value = last_confirmation_email.model_dump(mode="json") if hasattr(...) else last_confirmation_email`.
     - Extract body string: `body_str = body_value["body"]` (assert "body" in body_value, fail-fast on schema drift — same pattern as `persist.py:169-176`).
     - `await self._order_store.update_with_confirmation(order_dict["source_message_id"], body_str)`.
     - `key = f"{entry['filename']}#{entry['sub_doc_index']}"`; `confirmation_bodies[key] = body_value`.
  8. Final emit: `Event(author=CONFIRM_STAGE_NAME, actions=EventActions(state_delta={"confirmation_bodies": confirmation_bodies, "skipped_docs": skipped_docs}), content=types.Content(role="model", parts=[types.Part(text=f"Drafted {N} confirmation email(s); {M} skipped upstream")]))`.

Helper `_compose_order_details(order_dict: dict[str, Any]) -> str` — module-private. Format the order into a prose-ish block the LLM can echo back:

```python
def _compose_order_details(order_dict: dict[str, Any]) -> str:
    """Render OrderRecord (dict form from process_results) into a block
    for the confirmation email prompt. One line per item + total +
    ship-to + payment terms. The LLM quotes this verbatim."""
    customer = order_dict["customer"]
    bill_to = customer["bill_to"]
    addr = f"{bill_to['street1']}, {bill_to['city']}, {bill_to['state']} {bill_to['zip']}"
    lines = "\n".join(
        f"  {line['quantity']} {line['product']['uom']} "
        f"{line['product']['sku']}  "
        f"{line['product']['short_description']}  "
        f"@ ${line['product']['price_at_time']:.2f}  =  "
        f"${line['line_total']:.2f}"
        for line in order_dict["lines"]
    )
    return (
        f"Line items:\n{lines}\n\n"
        f"Order total: ${order_dict['order_total']:.2f}\n"
        f"Ship-to: {addr}\n"
        f"Payment terms: {customer['payment_terms']}"
    )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_stage_confirm.py -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/my_agent/stages/confirm.py tests/unit/test_stage_confirm.py
git commit -m "feat: add ConfirmStage — drafts customer confirmation email on AUTO_APPROVE"
```

---

### Task 7: Wire `ConfirmStage` into the root `SequentialAgent`

**Files:**
- Modify: `backend/my_agent/agent.py`

- [ ] **Step 1: Extend `build_root_agent` signature**

In `backend/my_agent/agent.py:91-153`, add two new kwargs to `build_root_agent`: `confirm_agent: Any` and `order_store: OrderStore`. Update the docstring's Args section with matching entries.

Insert `ConfirmStage` into the `sub_agents` list at position 7 (0-indexed), between `PersistStage` and `FinalizeStage`:

```python
    sub_agents = [
        IngestStage(),
        ReplyShortCircuitStage(exception_store=exception_store),
        ClassifyStage(classify_fn=classify_fn),
        ParseStage(parse_fn=parse_fn),
        ValidateStage(validator=validator),
        ClarifyStage(clarify_agent=clarify_agent),
        PersistStage(coordinator=coordinator),
        ConfirmStage(confirm_agent=confirm_agent, order_store=order_store),
        FinalizeStage(summary_agent=summary_agent),
    ]
```

- [ ] **Step 2: Extend `_build_default_root_agent`**

Same file, `_build_default_root_agent` (line 156). After `summary_agent = build_summary_agent()` (line 196), add:

```python
    confirm_agent: LlmAgent = build_confirmation_email_agent()
```

Then pass both new kwargs to `build_root_agent`:

```python
    return build_root_agent(
        classify_fn=classify_document,
        parse_fn=parse_document,
        validator=order_validator,
        coordinator=intake_coordinator,
        clarify_agent=clarify_agent,
        summary_agent=summary_agent,
        confirm_agent=confirm_agent,
        exception_store=exception_store,
        order_store=order_store,
    )
```

- [ ] **Step 3: Update the imports and module docstring**

Add to imports (top of file, alphabetized within the `.stages` / `.agents` blocks):

```python
from .agents.confirmation_email_agent import build_confirmation_email_agent
from .stages.confirm import ConfirmStage
```

In the module docstring's canonical-order list (lines 27-54), insert stage #7.5 / renumber — decide: keep `ConfirmStage` as #8 and bump `FinalizeStage` to #9, updating the list accordingly. Mirror the wording of the existing entries.

- [ ] **Step 4: Bump `AGENT_VERSION`**

Change `AGENT_VERSION: Final[str] = "track-a-v0.1"` to `AGENT_VERSION: Final[str] = "track-a-v0.2"` — every new order from now on gets tagged with v0.2 so we can tell in Firestore which docs have a confirmation_body.

- [ ] **Step 5: Run the existing root-agent tests**

Run: `uv run pytest tests/ -v -x`
Expected: PASS — all existing unit + integration tests still pass. If the end-to-end `tests/integration/test_orchestrator_emulator.py` fails because the stubbed-fake-LlmAgent test context is missing a `confirm_agent`, extend that test's harness to inject a `FakeChildLlmAgent(output_key="confirmation_email", responses=[{"subject":"...","body":"..."}])`. Follow the pattern that's already there for `clarify_agent` and `summary_agent`.

- [ ] **Step 6: Commit**

```bash
git add backend/my_agent/agent.py tests/integration/test_orchestrator_emulator.py
git commit -m "feat: wire ConfirmStage into root SequentialAgent (9 stages; AGENT_VERSION → track-a-v0.2)"
```

---

### Task 8: End-to-end smoke against `adk web`

**Files:** none modified — just a manual verification before we ship.

- [ ] **Step 1: Relaunch emulator + seed**

In one terminal:
```bash
firebase emulators:start --only firestore
```

In another terminal (same env):
```bash
export FIRESTORE_EMULATOR_HOST=localhost:8080
uv run python scripts/load_master_data.py
```

- [ ] **Step 2: Clear the prior M&M order so we're not deduping**

Open `http://localhost:4000/firestore` → `orders` collection → delete any doc with id starting with `<20260424114200.9ab4.tony@mm-machineworks.com>`. Alternatively just use a fresh fixture with a new Message-ID.

- [ ] **Step 3: Run adk web**

```bash
export FIRESTORE_EMULATOR_HOST=localhost:8080
export GOOGLE_API_KEY=...  # or Vertex creds
uv run adk web adk_apps
```

Paste into the textbox:
```
data/email/mm_machine_reorder_2026-04-24.eml
```

- [ ] **Step 4: Verify the new stage fires**

Expected event stream (9 events, not 8):
```
ingest_stage              → envelope ingested
reply_shortcircuit_stage  → reply_handled=false
classify_stage            → 1 PO, 0 skipped
parse_stage               → 1 sub-doc
validate_stage            → decision=auto_approve, confidence=1.0
clarify_stage             → (skipped — no CLARIFY entries)
persist_stage             → kind=order
confirm_stage             → "Drafted 1 confirmation email(s); 0 skipped upstream"
run_summary_agent + finalize_stage → run summary
```

- [ ] **Step 5: Verify state and Firestore**

- The `state` JSON should carry a new top-level key `confirmation_bodies` with one entry keyed `"body.txt#0"`, value `{subject, body}` dict.
- In emulator UI: the order doc at `orders/<20260424114200.9ab4.tony@mm-machineworks.com>` now has a `confirmation_body` field containing the email body string.
- In the adk web chat, the child LlmAgent's final event should render the JSON confirmation email (subject + body) visibly.

- [ ] **Step 6: Spot-check the email content**

Open the `confirmation_body` value in the Firestore UI. It should:
- Mention "M&M Machine & Fabrication" or "Tony"
- Quote both SKUs (`FST-HXN-050-13-G5Z`, `HYD-HSE-R2-06`) with quantities
- State the $127.40 total
- Mention Net 30
- Sign off as Grafton-Reese MRO
- Contain the reference id `<20260424114200.9ab4.tony@mm-machineworks.com>`

---

### Task 9: Update sprint status doc

**Files:**
- Modify: `research/Order-Intake-Sprint-Status.md`

- [ ] **Step 1: Document the new stage**

Add one row to the status table for the new AUTO_APPROVE confirmation leg. Bump stage count from 8 → 9. Add an entry to the "Built (do not rebuild)" section listing the 5 new files + 2 modified. Note `AGENT_VERSION` is now `track-a-v0.2`.

- [ ] **Step 2: Commit**

```bash
git add research/Order-Intake-Sprint-Status.md
git commit -m "docs: record ConfirmStage addition in sprint status"
```

---

## Verification

**Unit tests:** `uv run pytest tests/unit/ -v`. Expect 310+ tests passing (previous baseline 304 + ~6 new in Tasks 1/3/5/6).

**Integration tests:** `uv run pytest tests/integration/ -v` (requires Firestore emulator running). Expect all existing tests pass plus the new `update_with_confirmation` emulator round-trip from Task 5.5.

**Regression walker:** `uv run pytest tests/unit/test_llm_agent_factories.py -v` — confirms `ConfirmationEmail` doesn't emit `additionalProperties: false`.

**End-to-end via `adk web`:** Task 8 above.

**Smoke evalset:** `adk eval adk_apps/order_intake tests/eval/smoke.evalset.json --config_file_path tests/eval/eval_config.json` — loose thresholds should still pass. The AUTO_APPROVE case (patterson fixture) will exercise the new stage but its expected text is loose enough not to need updating. If it fails, loosen the response_match_score in `eval_config.json` slightly — do NOT tighten it to force-match the new event text.

**What "done" looks like:**
- All 310+ unit tests green
- All integration tests green against a fresh emulator with master data seeded
- `adk web` shows the 9th event (`confirm_stage`) firing on AUTO_APPROVE, followed by the child LlmAgent's confirmation_email JSON
- The persisted order doc in Firestore carries a non-null `confirmation_body` field quoting the correct customer, SKUs, and total
- Sprint status doc reflects the new stage
