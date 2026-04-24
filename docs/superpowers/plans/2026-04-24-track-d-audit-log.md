# Track D — Audit Log + correlation_id Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an append-only `audit_log` Firestore collection populated automatically by every `BaseAgent` stage in the 9-stage pipeline + 5 lifecycle emits. Each event carries a `correlation_id` (UUID4 per invocation, minted by `IngestStage`), `source_message_id`, `session_id`, `stage`, `phase`, `action`, `outcome`, `ts`, `agent_version`, and a free-form `payload` dict. Fail-open on write errors.

**Architecture:** `AuditedStage` mixin wraps `_run_async_impl` with entry/exit emits; stages switch base class from `BaseAgent` to `AuditedStage` and rename their main method to `_audited_run`. `AuditLogger` (new `backend/audit/` package) is injected via `PrivateAttr` constructor kwarg through `build_root_agent`. Firestore security rules enforce immutability.

**Tech Stack:** Python 3.13, Pydantic 2.x, `google-cloud-firestore` 2.27.0 (async), ADK `BaseAgent` / `SequentialAgent` / `Runner`, pytest + pytest-asyncio, Firestore emulator + rules + indexes.

**Source spec:** `docs/superpowers/specs/2026-04-24-track-d-audit-log-design.md` (rev `510559d`).

---

## File structure

| Path | Responsibility |
|---|---|
| **New** `backend/audit/__init__.py` | Package marker + re-exports `AuditEvent`, `AuditLogger` |
| **New** `backend/audit/models.py` | `AuditEvent` Pydantic model (strict header + free-form `payload`) |
| **New** `backend/audit/logger.py` | `AuditLogger` fail-open emitter; calls `client.collection("audit_log").add(...)` |
| **New** `backend/my_agent/stages/_audited.py` | `AuditedStage` mixin — wraps `_run_async_impl` with entry/exit emits, delegates to `_audited_run` |
| **Modified** `backend/my_agent/stages/*.py` (9 files) | Base class `BaseAgent` → `AuditedStage`; rename method; accept `audit_logger` kwarg |
| **Modified** `backend/my_agent/stages/ingest.py` | +correlation_id mint + `envelope_received` lifecycle emit |
| **Modified** `backend/my_agent/stages/validate.py` | +`routing_decided` lifecycle emit per sub-doc |
| **Modified** `backend/my_agent/stages/persist.py` | +`order_persisted` / `exception_opened` / `duplicate_seen` lifecycle emit per sub-doc |
| **Modified** `backend/my_agent/stages/confirm.py` | +`email_drafted` lifecycle emit per order |
| **Modified** `backend/my_agent/stages/finalize.py` | +`run_finalized` lifecycle emit |
| **Modified** `backend/my_agent/agent.py` | `build_root_agent` accepts + threads `audit_logger: AuditLogger`; `_build_default_root_agent` constructs one shared instance |
| **Modified** `firebase/firestore.rules` | Append `/audit_log/{doc}` immutable block |
| **Modified** `firebase/firestore.indexes.json` | 3 new composite indexes |
| **New** `tests/unit/test_audit_event.py` | 4 tests |
| **New** `tests/unit/test_audit_logger.py` | 5 tests |
| **New** `tests/unit/test_stage_audited.py` | 5 tests |
| **Modified** `tests/unit/test_stage_*.py` (9 files) | +`audit_logger=AsyncMock(spec=AuditLogger)` fixture kwarg + 1 entry/exit smoke test per file |
| **Modified** `tests/unit/test_orchestrator_build.py` | +2 tests: required kwarg + shared instance across stages |
| **New** `tests/integration/test_audit_log_emulator.py` | 3 tests — happy path, immutability, retry distinct correlation_ids |
| **Modified** `research/Order-Intake-Sprint-Status.md` | Add Track D Built-inventory rows |
| **Modified** `Glacis-Order-Intake.md` | §13 audit_log + correlation_id flips `[Post-MVP]` → `[MVP ✓]` |

---

## Task 1: `AuditEvent` Pydantic model

**Files:**
- Create: `backend/audit/__init__.py`
- Create: `backend/audit/models.py`
- Create: `tests/unit/test_audit_event.py`

- [ ] **Step 1.1: Write the failing tests**

Create `tests/unit/test_audit_event.py`:

```python
"""Unit tests for the AuditEvent Pydantic model.

Spec: docs/superpowers/specs/2026-04-24-track-d-audit-log-design.md
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from backend.audit.models import AuditEvent


NOW = datetime(2026, 4, 24, 12, 0, 0, tzinfo=timezone.utc)


def _valid_kwargs(**overrides):
    base = dict(
        correlation_id="abc123",
        source_message_id="msg-1",
        session_id="sess-1",
        stage="ValidateStage",
        phase="entered",
        action="stage_entered",
        outcome=None,
        ts=NOW,
        agent_version="track-a-v0.2",
        payload={},
    )
    base.update(overrides)
    return base


class TestAuditEventSchema:
    def test_all_required_fields_populated_round_trips(self):
        event = AuditEvent(**_valid_kwargs())
        assert event.correlation_id == "abc123"
        assert event.schema_version == 1

    def test_payload_accepts_arbitrary_dict(self):
        event = AuditEvent(
            **_valid_kwargs(payload={"confidence": 0.87, "order_id": "ORD-xyz"})
        )
        assert event.payload["confidence"] == 0.87
        assert event.payload["order_id"] == "ORD-xyz"

    def test_extra_top_level_field_rejected(self):
        with pytest.raises(ValidationError) as exc:
            AuditEvent(**_valid_kwargs(), mystery_field="nope")
        assert "mystery_field" in str(exc.value)

    def test_missing_correlation_id_rejected(self):
        kwargs = _valid_kwargs()
        del kwargs["correlation_id"]
        with pytest.raises(ValidationError) as exc:
            AuditEvent(**kwargs)
        assert "correlation_id" in str(exc.value)
```

- [ ] **Step 1.2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_audit_event.py -v`

Expected: `ModuleNotFoundError: No module named 'backend.audit'` — all 4 tests error at import.

- [ ] **Step 1.3: Create `backend/audit/models.py`**

```python
"""Audit-log event contract.

Strict typed header (so consumers — dashboard, eval, forensic
tooling — can rely on required fields) plus a free-form payload
dict for per-stage detail that would otherwise force a schema
bump every time we add an event type.

Spec: docs/superpowers/specs/2026-04-24-track-d-audit-log-design.md
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class AuditEvent(BaseModel):
    """One immutable row in the ``audit_log`` Firestore collection."""

    model_config = ConfigDict(extra="forbid")

    correlation_id: str
    source_message_id: Optional[str] = None
    session_id: str
    stage: str
    phase: Literal["entered", "exited", "lifecycle"]
    action: str
    outcome: Optional[str] = None
    ts: datetime
    agent_version: str
    payload: dict[str, Any] = Field(default_factory=dict)
    schema_version: int = 1


__all__ = ["AuditEvent"]
```

- [ ] **Step 1.4: Create `backend/audit/__init__.py`**

```python
"""Audit-log package for Track D.

Public surface: AuditEvent (schema), AuditLogger (fail-open emitter).
"""
from backend.audit.models import AuditEvent

__all__ = ["AuditEvent"]  # AuditLogger re-exported in Task 2
```

- [ ] **Step 1.5: Run tests — expect 4 passes**

Run: `uv run pytest tests/unit/test_audit_event.py -v`

Expected: `4 passed`.

- [ ] **Step 1.6: Commit**

```bash
git add backend/audit/__init__.py backend/audit/models.py tests/unit/test_audit_event.py
git commit -m "feat(track-d): add AuditEvent pydantic model"
```

---

## Task 2: `AuditLogger` fail-open emitter

**Files:**
- Create: `backend/audit/logger.py`
- Modify: `backend/audit/__init__.py`
- Create: `tests/unit/test_audit_logger.py`

- [ ] **Step 2.1: Write the failing tests**

Create `tests/unit/test_audit_logger.py`:

```python
"""Unit tests for AuditLogger fail-open emitter."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.audit.logger import AuditLogger


@pytest.mark.asyncio
class TestAuditLogger:
    async def test_emit_writes_one_doc_to_audit_log(self):
        add_mock = AsyncMock()
        collection_mock = MagicMock()
        collection_mock.add = add_mock
        client = MagicMock()
        client.collection = MagicMock(return_value=collection_mock)

        logger = AuditLogger(client=client, agent_version="track-a-v0.2")
        await logger.emit(
            correlation_id="c1",
            session_id="s1",
            source_message_id="m1",
            stage="ValidateStage",
            phase="entered",
            action="stage_entered",
        )

        client.collection.assert_called_once_with("audit_log")
        add_mock.assert_awaited_once()
        written = add_mock.await_args.args[0]
        assert written["correlation_id"] == "c1"
        assert written["agent_version"] == "track-a-v0.2"
        assert written["schema_version"] == 1

    async def test_emit_swallows_firestore_exceptions(self):
        """Fail-open: pipeline must not crash on audit write failure."""
        add_mock = AsyncMock(side_effect=RuntimeError("firestore outage"))
        collection_mock = MagicMock()
        collection_mock.add = add_mock
        client = MagicMock()
        client.collection = MagicMock(return_value=collection_mock)

        logger = AuditLogger(client=client, agent_version="track-a-v0.2")

        # Must NOT raise
        await logger.emit(
            correlation_id="c1",
            session_id="s1",
            source_message_id="m1",
            stage="ValidateStage",
            phase="entered",
            action="stage_entered",
        )

    async def test_emit_accepts_payload_dict(self):
        add_mock = AsyncMock()
        collection_mock = MagicMock()
        collection_mock.add = add_mock
        client = MagicMock()
        client.collection = MagicMock(return_value=collection_mock)

        logger = AuditLogger(client=client, agent_version="track-a-v0.2")
        await logger.emit(
            correlation_id="c1",
            session_id="s1",
            source_message_id="m1",
            stage="lifecycle",
            phase="lifecycle",
            action="routing_decided",
            outcome="auto_approve",
            payload={"confidence": 0.97, "customer_id": "CUST-1"},
        )

        written = add_mock.await_args.args[0]
        assert written["payload"]["confidence"] == 0.97
        assert written["payload"]["customer_id"] == "CUST-1"
        assert written["outcome"] == "auto_approve"

    async def test_emit_uses_server_timestamp_sentinel(self):
        """ts field must be replaced with SERVER_TIMESTAMP before write."""
        from google.cloud.firestore_v1 import SERVER_TIMESTAMP

        add_mock = AsyncMock()
        collection_mock = MagicMock()
        collection_mock.add = add_mock
        client = MagicMock()
        client.collection = MagicMock(return_value=collection_mock)

        logger = AuditLogger(client=client, agent_version="track-a-v0.2")
        await logger.emit(
            correlation_id="c1",
            session_id="s1",
            source_message_id=None,
            stage="IngestStage",
            phase="entered",
            action="stage_entered",
        )

        written = add_mock.await_args.args[0]
        assert written["ts"] is SERVER_TIMESTAMP

    async def test_missing_payload_defaults_to_empty_dict(self):
        add_mock = AsyncMock()
        collection_mock = MagicMock()
        collection_mock.add = add_mock
        client = MagicMock()
        client.collection = MagicMock(return_value=collection_mock)

        logger = AuditLogger(client=client, agent_version="track-a-v0.2")
        await logger.emit(
            correlation_id="c1",
            session_id="s1",
            source_message_id="m1",
            stage="ValidateStage",
            phase="exited",
            action="stage_exited",
            outcome="ok",
        )

        written = add_mock.await_args.args[0]
        assert written["payload"] == {}
```

- [ ] **Step 2.2: Run tests — expect failure**

Run: `uv run pytest tests/unit/test_audit_logger.py -v`

Expected: `ModuleNotFoundError: No module named 'backend.audit.logger'`.

- [ ] **Step 2.3: Create `backend/audit/logger.py`**

```python
"""Fail-open audit-log emitter.

Spec: docs/superpowers/specs/2026-04-24-track-d-audit-log-design.md

Construct once per process (shared async Firestore client + the
pipeline's AGENT_VERSION constant) and inject into every stage via
PrivateAttr kwarg. Fail-open: Firestore exceptions are logged at
ERROR and swallowed — pipeline keeps running. Phase-2 compliance
hardening flips this to fail-closed by replacing the class.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from google.cloud.firestore_v1 import SERVER_TIMESTAMP
from google.cloud.firestore_v1.async_client import AsyncClient

from backend.audit.models import AuditEvent
from backend.utils.logging import get_logger

_log = get_logger(__name__)


class AuditLogger:
    def __init__(self, client: AsyncClient, agent_version: str) -> None:
        self._client = client
        self._agent_version = agent_version

    async def emit(
        self,
        *,
        correlation_id: str,
        session_id: str,
        source_message_id: Optional[str],
        stage: str,
        phase: Literal["entered", "exited", "lifecycle"],
        action: str,
        outcome: Optional[str] = None,
        payload: Optional[dict[str, Any]] = None,
    ) -> None:
        try:
            event = AuditEvent(
                correlation_id=correlation_id,
                source_message_id=source_message_id,
                session_id=session_id,
                stage=stage,
                phase=phase,
                action=action,
                outcome=outcome,
                ts=datetime.now(timezone.utc),  # placeholder — swapped below
                agent_version=self._agent_version,
                payload=payload or {},
            )
            data = event.model_dump(mode="json")
            data["ts"] = SERVER_TIMESTAMP  # Firestore server-side timestamp
            await self._client.collection("audit_log").add(data)
        except Exception as exc:
            _log.error(
                "audit_emit_failed",
                correlation_id=correlation_id,
                stage=stage,
                action=action,
                error=str(exc),
            )


__all__ = ["AuditLogger"]
```

- [ ] **Step 2.4: Update `backend/audit/__init__.py` to re-export**

```python
"""Audit-log package for Track D.

Public surface: AuditEvent (schema), AuditLogger (fail-open emitter).
"""
from backend.audit.logger import AuditLogger
from backend.audit.models import AuditEvent

__all__ = ["AuditEvent", "AuditLogger"]
```

- [ ] **Step 2.5: Run tests — expect 5 passes**

Run: `uv run pytest tests/unit/test_audit_logger.py -v`

Expected: `5 passed`.

- [ ] **Step 2.6: Commit**

```bash
git add backend/audit/logger.py backend/audit/__init__.py tests/unit/test_audit_logger.py
git commit -m "feat(track-d): add AuditLogger fail-open emitter"
```

---

## Task 3: `AuditedStage` mixin

**Files:**
- Create: `backend/my_agent/stages/_audited.py`
- Create: `tests/unit/test_stage_audited.py`

- [ ] **Step 3.1: Write the failing tests**

Create `tests/unit/test_stage_audited.py`:

```python
"""Unit tests for the AuditedStage mixin.

Uses a minimal subclass _TestStage that yields one event, plus a
failing variant that raises inside _audited_run. The real stage
tests in test_stage_*.py provide per-stage coverage.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from backend.audit.logger import AuditLogger
from backend.my_agent.stages._audited import AuditedStage
from tests.unit._stage_testing import (
    collect_events,
    make_stage_ctx,
)


class _OkStage(AuditedStage):
    name: str = "TestStage"

    async def _audited_run(self, ctx):
        from google.adk.events import Event, EventActions

        yield Event(
            author=self.name,
            actions=EventActions(state_delta={"_probe": "ran"}),
        )


class _RaisesStage(AuditedStage):
    name: str = "BadStage"

    async def _audited_run(self, ctx):
        raise RuntimeError("boom")
        yield  # unreachable; keep async-generator


@pytest.mark.asyncio
class TestAuditedStageMixin:
    async def test_emits_entered_then_exited_wrapping_body(self):
        logger = AsyncMock(spec=AuditLogger)
        stage = _OkStage(audit_logger=logger)
        ctx = make_stage_ctx(
            stage=stage, state={"correlation_id": "c1"}
        )

        events = await collect_events(stage.run_async(ctx))

        # Body event reached caller
        assert any(
            e.actions and e.actions.state_delta.get("_probe") == "ran"
            for e in events
        )
        # Exactly two emits — entered then exited
        assert logger.emit.await_count == 2
        first, second = logger.emit.await_args_list
        assert first.kwargs["phase"] == "entered"
        assert first.kwargs["action"] == "stage_entered"
        assert second.kwargs["phase"] == "exited"
        assert second.kwargs["action"] == "stage_exited"
        assert second.kwargs["outcome"] == "ok"

    async def test_body_exception_emits_error_outcome_and_reraises(self):
        logger = AsyncMock(spec=AuditLogger)
        stage = _RaisesStage(audit_logger=logger)
        ctx = make_stage_ctx(
            stage=stage, state={"correlation_id": "c1"}
        )

        with pytest.raises(RuntimeError, match="boom"):
            await collect_events(stage.run_async(ctx))

        assert logger.emit.await_count == 2
        exit_call = logger.emit.await_args_list[1]
        assert exit_call.kwargs["outcome"] == "error:RuntimeError"

    async def test_missing_correlation_id_emits_empty_string(self):
        logger = AsyncMock(spec=AuditLogger)
        stage = _OkStage(audit_logger=logger)
        ctx = make_stage_ctx(stage=stage, state={})  # no correlation_id

        await collect_events(stage.run_async(ctx))

        assert logger.emit.await_args_list[0].kwargs["correlation_id"] == ""

    async def test_source_message_id_extracted_from_envelope_state(self):
        logger = AsyncMock(spec=AuditLogger)
        stage = _OkStage(audit_logger=logger)
        ctx = make_stage_ctx(
            stage=stage,
            state={
                "correlation_id": "c1",
                "envelope": {"message_id": "<envelope-msg-42>"},
            },
        )

        await collect_events(stage.run_async(ctx))

        first = logger.emit.await_args_list[0]
        assert first.kwargs["source_message_id"] == "<envelope-msg-42>"

    async def test_session_id_propagates_from_ctx(self):
        logger = AsyncMock(spec=AuditLogger)
        stage = _OkStage(audit_logger=logger)
        ctx = make_stage_ctx(stage=stage, state={"correlation_id": "c1"})
        # ctx.session.id is set by make_stage_ctx helper

        await collect_events(stage.run_async(ctx))

        first = logger.emit.await_args_list[0]
        assert first.kwargs["session_id"] == ctx.session.id
```

- [ ] **Step 3.2: Run — expect failure**

Run: `uv run pytest tests/unit/test_stage_audited.py -v`

Expected: import failure on `backend.my_agent.stages._audited`.

- [ ] **Step 3.3: Create `backend/my_agent/stages/_audited.py`**

```python
"""AuditedStage mixin — wraps _run_async_impl with entry/exit emits.

Subclass contract:
- Class attribute ``name: str`` must be set (stage's canonical name).
- Subclasses implement ``_audited_run(ctx)`` as the real stage body;
  yield Events inside it exactly as you would in ``_run_async_impl``.
- ``correlation_id`` must be present in ``ctx.session.state`` by the
  time a non-Ingest stage runs — IngestStage seeds it as its first
  business-logic act (see Task 5).

The mixin emits ``stage_entered`` BEFORE yielding to ``_audited_run``,
then ``stage_exited`` in a ``finally`` block. If the body raises, the
exit event carries ``outcome=f"error:{ExceptionClass}"`` and the
exception re-raises.

Spec: docs/superpowers/specs/2026-04-24-track-d-audit-log-design.md
"""
from __future__ import annotations

from typing import Any, Optional

from google.adk.agents import BaseAgent
from pydantic import PrivateAttr


class AuditedStage(BaseAgent):
    _audit_logger: Any = PrivateAttr()

    def __init__(self, *, audit_logger: Any, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._audit_logger = audit_logger

    async def _run_async_impl(self, ctx):  # type: ignore[override]
        state = ctx.session.state
        correlation_id: str = state.get("correlation_id", "")
        session_id: str = ctx.session.id
        source_message_id = self._extract_source_message_id(state)

        await self._audit_logger.emit(
            correlation_id=correlation_id,
            session_id=session_id,
            source_message_id=source_message_id,
            stage=self.name,
            phase="entered",
            action="stage_entered",
        )

        outcome = "ok"
        try:
            async for event in self._audited_run(ctx):
                yield event
        except BaseException as exc:
            outcome = f"error:{type(exc).__name__}"
            raise
        finally:
            # Re-read state in case _audited_run seeded envelope /
            # correlation_id (IngestStage does both).
            state = ctx.session.state
            correlation_id = state.get("correlation_id", "")
            source_message_id = self._extract_source_message_id(state)
            await self._audit_logger.emit(
                correlation_id=correlation_id,
                session_id=session_id,
                source_message_id=source_message_id,
                stage=self.name,
                phase="exited",
                action="stage_exited",
                outcome=outcome,
            )

    async def _audited_run(self, ctx):  # pragma: no cover
        raise NotImplementedError(
            "AuditedStage subclasses must implement _audited_run"
        )
        yield  # keep async-generator for typing

    @staticmethod
    def _extract_source_message_id(state) -> Optional[str]:
        envelope = state.get("envelope")
        if isinstance(envelope, dict):
            return envelope.get("message_id")
        return None


__all__ = ["AuditedStage"]
```

- [ ] **Step 3.4: Run — expect 5 passes**

Run: `uv run pytest tests/unit/test_stage_audited.py -v`

Expected: `5 passed`.

- [ ] **Step 3.5: Commit**

```bash
git add backend/my_agent/stages/_audited.py tests/unit/test_stage_audited.py
git commit -m "feat(track-d): add AuditedStage mixin for stage entry/exit emits"
```

---

## Task 4: Migrate all 9 stages to `AuditedStage` + thread audit_logger through build_root_agent

**Files (all modified):**
- `backend/my_agent/stages/ingest.py`
- `backend/my_agent/stages/reply_shortcircuit.py`
- `backend/my_agent/stages/classify.py`
- `backend/my_agent/stages/parse.py`
- `backend/my_agent/stages/validate.py`
- `backend/my_agent/stages/clarify.py`
- `backend/my_agent/stages/persist.py`
- `backend/my_agent/stages/confirm.py`
- `backend/my_agent/stages/finalize.py`
- `backend/my_agent/agent.py`
- `tests/unit/test_stage_ingest.py`
- `tests/unit/test_stage_reply_shortcircuit.py`
- `tests/unit/test_stage_classify.py`
- `tests/unit/test_stage_parse.py`
- `tests/unit/test_stage_validate.py`
- `tests/unit/test_stage_clarify.py`
- `tests/unit/test_stage_persist.py`
- `tests/unit/test_stage_confirm.py`
- `tests/unit/test_stage_finalize.py`
- `tests/unit/test_orchestrator_build.py`

**Strategy:** This is the mechanical-migration task. All 9 stages get the SAME shape of change. The lifecycle emits inside specific stages (IngestStage correlation_id mint, ValidateStage routing_decided, etc.) happen in Tasks 5-9. This task only does base-class + method-rename + constructor-kwarg-thread-through.

- [ ] **Step 4.1: Update `build_root_agent` signature + call sites (agent.py)**

In `backend/my_agent/agent.py`:

(a) Add import:
```python
from backend.audit.logger import AuditLogger
```

(b) Add `audit_logger: AuditLogger` as a required kwarg to `build_root_agent`:
```python
def build_root_agent(
    *,
    classify_fn: ClassifyFn,
    parse_fn: ParseFn,
    validator: OrderValidator,
    coordinator: IntakeCoordinator,
    clarify_agent: Any,
    summary_agent: Any,
    confirm_agent: Any,
    exception_store: ExceptionStore,
    order_store: OrderStore,
    audit_logger: AuditLogger,   # NEW
) -> SequentialAgent:
```

(c) Thread `audit_logger=audit_logger` into every stage construction inside the `sub_agents` list:
```python
sub_agents = [
    IngestStage(audit_logger=audit_logger),
    ReplyShortCircuitStage(exception_store=exception_store, audit_logger=audit_logger),
    ClassifyStage(classify_fn=classify_fn, audit_logger=audit_logger),
    ParseStage(parse_fn=parse_fn, audit_logger=audit_logger),
    ValidateStage(validator=validator, audit_logger=audit_logger),
    ClarifyStage(clarify_agent=clarify_agent, audit_logger=audit_logger),
    PersistStage(coordinator=coordinator, audit_logger=audit_logger),
    ConfirmStage(confirm_agent=confirm_agent, order_store=order_store, audit_logger=audit_logger),
    FinalizeStage(summary_agent=summary_agent, audit_logger=audit_logger),
]
```

(d) In `_build_default_root_agent`, construct one shared `AuditLogger`:
```python
audit_logger = AuditLogger(client=client, agent_version=AGENT_VERSION)
```
and pass it into the `build_root_agent(...)` call at the bottom.

(e) Update docstring's `Args:` block to list `audit_logger` and add a module-docstring line noting Track D.

- [ ] **Step 4.2: Migrate each of the 9 stages — uniform shape**

For EACH stage file in `backend/my_agent/stages/`:

(a) Change `from google.adk.agents import BaseAgent` → `from backend.my_agent.stages._audited import AuditedStage` (keep other ADK imports).

(b) Change the class declaration:
- `class IngestStage(BaseAgent):` → `class IngestStage(AuditedStage):`
- (same pattern for all 9)

(c) Rename the method `_run_async_impl` → `_audited_run`, drop the `# type: ignore[override]` comment (no longer needed — `_audited_run` is the documented extension point).

(d) For stages that have `__init__` (most do), make sure it accepts `audit_logger: Any` and passes to `super().__init__(audit_logger=audit_logger)`. Pattern:

```python
# Before:
class ClarifyStage(BaseAgent):
    _clarify_agent: Any = PrivateAttr()

    def __init__(self, *, clarify_agent: Any) -> None:
        super().__init__()
        self._clarify_agent = clarify_agent

# After:
class ClarifyStage(AuditedStage):
    _clarify_agent: Any = PrivateAttr()

    def __init__(self, *, clarify_agent: Any, audit_logger: Any) -> None:
        super().__init__(audit_logger=audit_logger)
        self._clarify_agent = clarify_agent
```

**Stage-by-stage signature summary (new):**
| Stage | Constructor kwargs |
|---|---|
| `IngestStage` | `audit_logger` |
| `ReplyShortCircuitStage` | `exception_store, audit_logger` |
| `ClassifyStage` | `classify_fn, audit_logger` |
| `ParseStage` | `parse_fn, audit_logger` |
| `ValidateStage` | `validator, audit_logger` |
| `ClarifyStage` | `clarify_agent, audit_logger` |
| `PersistStage` | `coordinator, audit_logger` |
| `ConfirmStage` | `confirm_agent, order_store, audit_logger` |
| `FinalizeStage` | `summary_agent, audit_logger` |

`IngestStage` previously had no kwargs; now takes `audit_logger` only.

- [ ] **Step 4.3: Update each `tests/unit/test_stage_*.py` fixture**

For each of the 9 `test_stage_*.py` files:

(a) Add `from unittest.mock import AsyncMock` (if not already imported) and `from backend.audit.logger import AuditLogger`.

(b) Find the stage-construction site(s) in the test file (usually inside a fixture or `_make_stage` helper). Add `audit_logger=AsyncMock(spec=AuditLogger)` to every call.

(c) Add ONE new entry/exit smoke test per file, at the end:

```python
# Append to EACH test_stage_<stage>.py — adjust stage name + constructor kwargs

@pytest.mark.asyncio
async def test_stage_emits_entered_and_exited_audit_events():
    audit_logger = AsyncMock(spec=AuditLogger)
    stage = <StageClass>(
        # other required deps as mocks / fakes
        audit_logger=audit_logger,
    )
    ctx = make_stage_ctx(
        stage=stage,
        state={"correlation_id": "c1", <any other required state>},
    )

    # Drive the stage — body may raise or no-op depending on state
    try:
        async for _ in stage.run_async(ctx):
            pass
    except Exception:
        pass  # stage-level failure is fine; we're asserting audit emits still fire

    calls = audit_logger.emit.await_args_list
    phases = [c.kwargs["phase"] for c in calls]
    assert "entered" in phases
    assert "exited" in phases
```

Note: some stages (Validate, Persist, Confirm, Finalize) will emit MORE than 2 calls after Tasks 6-9 layer in lifecycle emits — that's fine because we only assert "entered" and "exited" are present, not count.

- [ ] **Step 4.4: Update `tests/unit/test_orchestrator_build.py`**

Add two new tests:

```python
@pytest.mark.asyncio
async def test_build_root_agent_requires_audit_logger_kwarg():
    """Missing audit_logger should raise TypeError."""
    from backend.my_agent.agent import build_root_agent

    with pytest.raises(TypeError, match="audit_logger"):
        build_root_agent(
            **_make_deps_without_audit_logger(),  # helper below
        )


@pytest.mark.asyncio
async def test_every_stage_gets_the_same_audit_logger_instance():
    """All 9 stages must share one AuditLogger instance — guards against
    someone accidentally passing different loggers per stage."""
    from unittest.mock import AsyncMock
    from backend.audit.logger import AuditLogger
    from backend.my_agent.agent import build_root_agent

    audit_logger = AsyncMock(spec=AuditLogger)
    root = build_root_agent(
        audit_logger=audit_logger,
        **_make_deps(),  # existing fixture
    )

    for sub_agent in root.sub_agents:
        assert sub_agent._audit_logger is audit_logger
```

Also update the existing `_make_deps` fixture to include `audit_logger=AsyncMock(spec=AuditLogger)` so the existing positional-arg + canonical-order tests still pass.

- [ ] **Step 4.5: Run the unit suite — expect all green**

Run: `uv run pytest tests/unit -x --tb=short 2>&1 | tail -25`

Expected: all tests pass. The 9 new per-stage smoke tests + 2 orchestrator tests + all prior tests (with updated fixtures) should total `baseline + 25 + 9 + 2 = baseline + 36`.

- [ ] **Step 4.6: Commit**

```bash
git add backend/my_agent/agent.py backend/my_agent/stages/ tests/unit/test_stage_*.py tests/unit/test_orchestrator_build.py
git commit -m "feat(track-d): migrate all 9 stages to AuditedStage + thread logger through build_root_agent"
```

---

## Task 5: `IngestStage` mints correlation_id + emits `envelope_received`

**Files:**
- Modify: `backend/my_agent/stages/ingest.py`
- Modify: `tests/unit/test_stage_ingest.py`

- [ ] **Step 5.1: Write failing test for correlation_id seeding + lifecycle emit**

Append to `tests/unit/test_stage_ingest.py`:

```python
@pytest.mark.asyncio
class TestIngestStageCorrelationIdAndLifecycle:
    async def test_seeds_correlation_id_in_state_delta(self):
        audit_logger = AsyncMock(spec=AuditLogger)
        stage = IngestStage(audit_logger=audit_logger)
        ctx = make_stage_ctx(
            stage=stage,
            user_text=str(fixture_path_to_patterson),  # existing helper
        )

        events = await collect_events(stage.run_async(ctx))
        deltas = [
            e.actions.state_delta
            for e in events
            if e.actions and e.actions.state_delta
        ]
        # correlation_id seeded in the SAME state_delta as envelope
        seeded_corr = [d.get("correlation_id") for d in deltas if "correlation_id" in d]
        assert len(seeded_corr) == 1
        assert isinstance(seeded_corr[0], str)
        assert len(seeded_corr[0]) == 32  # uuid4().hex length

    async def test_emits_envelope_received_lifecycle_with_attachment_count(self):
        audit_logger = AsyncMock(spec=AuditLogger)
        stage = IngestStage(audit_logger=audit_logger)
        ctx = make_stage_ctx(
            stage=stage,
            user_text=str(fixture_path_to_patterson),
        )

        await collect_events(stage.run_async(ctx))

        lifecycle_calls = [
            c for c in audit_logger.emit.await_args_list
            if c.kwargs.get("action") == "envelope_received"
        ]
        assert len(lifecycle_calls) == 1
        assert "attachment_count" in lifecycle_calls[0].kwargs["payload"]
```

- [ ] **Step 5.2: Run — expect failure**

Run: `uv run pytest tests/unit/test_stage_ingest.py::TestIngestStageCorrelationIdAndLifecycle -v`

Expected: both tests fail — no correlation_id in deltas; no `envelope_received` emit.

- [ ] **Step 5.3: Update `IngestStage._audited_run`**

In `backend/my_agent/stages/ingest.py`, modify `_audited_run` so that after `parse_eml` succeeds, the state_delta includes `correlation_id` AND a lifecycle emit fires:

```python
# Pseudo-diff — locate the existing place where envelope state_delta is yielded:

import uuid

async def _audited_run(self, ctx):
    # ... existing parse_eml logic producing `envelope` ...

    correlation_id = uuid.uuid4().hex
    yield Event(
        author=self.name,
        actions=EventActions(state_delta={
            "envelope": envelope.model_dump(mode="json"),
            "correlation_id": correlation_id,
        }),
    )
    await self._audit_logger.emit(
        correlation_id=correlation_id,
        session_id=ctx.session.id,
        source_message_id=envelope.message_id,
        stage="lifecycle",
        phase="lifecycle",
        action="envelope_received",
        payload={"attachment_count": len(envelope.attachments)},
    )
```

- [ ] **Step 5.4: Run the 2 new tests + existing ingest tests**

Run: `uv run pytest tests/unit/test_stage_ingest.py -v`

Expected: all green including 2 new.

- [ ] **Step 5.5: Commit**

```bash
git add backend/my_agent/stages/ingest.py tests/unit/test_stage_ingest.py
git commit -m "feat(track-d): IngestStage mints correlation_id + emits envelope_received"
```

---

## Task 6: `ValidateStage` emits `routing_decided` per sub-doc

**Files:**
- Modify: `backend/my_agent/stages/validate.py`
- Modify: `tests/unit/test_stage_validate.py`

- [ ] **Step 6.1: Write failing test**

Append to `tests/unit/test_stage_validate.py`:

```python
@pytest.mark.asyncio
async def test_validate_emits_routing_decided_per_sub_doc():
    """For each entry in validation_results, ValidateStage emits a
    lifecycle 'routing_decided' with outcome=<decision.value> + payload
    carrying confidence and customer_id."""
    validator = AsyncMock()
    # Have the validator return an AUTO_APPROVE ValidationResult
    validator.validate = AsyncMock(return_value=_auto_approve_result())  # existing helper
    audit_logger = AsyncMock(spec=AuditLogger)

    stage = ValidateStage(validator=validator, audit_logger=audit_logger)
    ctx = make_stage_ctx(
        stage=stage,
        state={
            "correlation_id": "c1",
            "envelope": {"message_id": "m1"},
            "parsed_docs": [
                {"filename": "body.txt", "sub_doc_index": 0, "parsed": {...}, "sub_doc": {...}},
            ],
        },
    )
    await collect_events(stage.run_async(ctx))

    routing_calls = [
        c for c in audit_logger.emit.await_args_list
        if c.kwargs.get("action") == "routing_decided"
    ]
    assert len(routing_calls) == 1
    assert routing_calls[0].kwargs["outcome"] == "auto_approve"
    assert "confidence" in routing_calls[0].kwargs["payload"]
    assert "customer_id" in routing_calls[0].kwargs["payload"]
```

(Flesh out `_auto_approve_result()` by mirroring the closest existing ValidateStage test that constructs a ValidationResult — the helper returns a ValidationResult with `decision=RoutingDecision.AUTO_APPROVE`, `aggregate_confidence=0.97`, `customer=CustomerRecord(customer_id="CUST-1", ...)`, `lines=[one LineItemValidation]`.)

- [ ] **Step 6.2: Run — expect failure**

Run: `uv run pytest tests/unit/test_stage_validate.py::test_validate_emits_routing_decided_per_sub_doc -v`

Expected: fails — no `routing_decided` emit.

- [ ] **Step 6.3: Patch `ValidateStage._audited_run`**

In `backend/my_agent/stages/validate.py`, after the `validator.validate(...)` call inside the per-entry loop, add:

```python
await self._audit_logger.emit(
    correlation_id=ctx.session.state.get("correlation_id", ""),
    session_id=ctx.session.id,
    source_message_id=self._source_message_id(ctx.session.state),
    stage="lifecycle",
    phase="lifecycle",
    action="routing_decided",
    outcome=validation.decision.value,
    payload={
        "filename": entry["filename"],
        "sub_doc_index": entry["sub_doc_index"],
        "confidence": validation.aggregate_confidence,
        "customer_id": validation.customer.customer_id if validation.customer else None,
    },
)
```

Add a `_source_message_id(state)` static helper on the stage (or reuse `AuditedStage._extract_source_message_id` via `self._extract_source_message_id`).

- [ ] **Step 6.4: Run the new test + existing validate tests**

Run: `uv run pytest tests/unit/test_stage_validate.py -v`

Expected: all green.

- [ ] **Step 6.5: Commit**

```bash
git add backend/my_agent/stages/validate.py tests/unit/test_stage_validate.py
git commit -m "feat(track-d): ValidateStage emits routing_decided lifecycle per sub-doc"
```

---

## Task 7: `PersistStage` emits `order_persisted` / `exception_opened` / `duplicate_seen`

**Files:**
- Modify: `backend/my_agent/stages/persist.py`
- Modify: `tests/unit/test_stage_persist.py`

- [ ] **Step 7.1: Write failing test**

Append to `tests/unit/test_stage_persist.py`:

```python
@pytest.mark.asyncio
class TestPersistStageLifecycleEmits:
    async def test_auto_approve_emits_order_persisted(self, ...):  # existing fixture pattern
        audit_logger = AsyncMock(spec=AuditLogger)
        coordinator = AsyncMock()
        coordinator.process = AsyncMock(return_value=ProcessResult(
            kind="order",
            order=_sample_order_record(),  # existing helper
        ))

        stage = PersistStage(coordinator=coordinator, audit_logger=audit_logger)
        # ... drive with a state that has 1 parsed_doc entry ...

        actions = [
            c.kwargs["action"] for c in audit_logger.emit.await_args_list
            if c.kwargs.get("phase") == "lifecycle"
        ]
        assert "order_persisted" in actions

    async def test_escalate_emits_exception_opened(self, ...):
        coordinator = AsyncMock()
        coordinator.process = AsyncMock(return_value=ProcessResult(
            kind="exception",
            exception=_sample_exception_record(),
        ))
        audit_logger = AsyncMock(spec=AuditLogger)
        stage = PersistStage(coordinator=coordinator, audit_logger=audit_logger)
        # drive ...

        actions = [
            c.kwargs["action"] for c in audit_logger.emit.await_args_list
            if c.kwargs.get("phase") == "lifecycle"
        ]
        assert "exception_opened" in actions

    async def test_duplicate_emits_duplicate_seen(self, ...):
        coordinator = AsyncMock()
        coordinator.process = AsyncMock(return_value=ProcessResult(
            kind="duplicate",
            order=_sample_order_record(),
        ))
        audit_logger = AsyncMock(spec=AuditLogger)
        stage = PersistStage(coordinator=coordinator, audit_logger=audit_logger)
        # drive ...

        actions = [
            c.kwargs["action"] for c in audit_logger.emit.await_args_list
            if c.kwargs.get("phase") == "lifecycle"
        ]
        assert "duplicate_seen" in actions
```

- [ ] **Step 7.2: Run — expect failure**

Run: `uv run pytest tests/unit/test_stage_persist.py::TestPersistStageLifecycleEmits -v`

Expected: all 3 fail.

- [ ] **Step 7.3: Patch `PersistStage._audited_run`**

After `await self._coordinator.process(...)` returns `result` inside the per-entry loop, add:

```python
_ACTION_FOR_KIND = {
    "order": "order_persisted",
    "exception": "exception_opened",
    "duplicate": "duplicate_seen",
}
action = _ACTION_FOR_KIND[result.kind]

payload = {
    "filename": entry["filename"],
    "sub_doc_index": entry["sub_doc_index"],
}
if result.order is not None:
    payload["order_id"] = result.order.source_message_id
if result.exception is not None:
    payload["exception_id"] = result.exception.source_message_id

await self._audit_logger.emit(
    correlation_id=ctx.session.state.get("correlation_id", ""),
    session_id=ctx.session.id,
    source_message_id=self._extract_source_message_id(ctx.session.state),
    stage="lifecycle",
    phase="lifecycle",
    action=action,
    outcome=result.kind,
    payload=payload,
)
```

- [ ] **Step 7.4: Run the 3 new tests + existing persist tests**

Run: `uv run pytest tests/unit/test_stage_persist.py -v`

Expected: all green.

- [ ] **Step 7.5: Commit**

```bash
git add backend/my_agent/stages/persist.py tests/unit/test_stage_persist.py
git commit -m "feat(track-d): PersistStage emits order_persisted/exception_opened/duplicate_seen lifecycle"
```

---

## Task 8: `ConfirmStage` emits `email_drafted`

**Files:**
- Modify: `backend/my_agent/stages/confirm.py`
- Modify: `tests/unit/test_stage_confirm.py`

- [ ] **Step 8.1: Write failing test**

Append to `tests/unit/test_stage_confirm.py`:

```python
@pytest.mark.asyncio
async def test_confirm_emits_email_drafted_per_order(...):
    audit_logger = AsyncMock(spec=AuditLogger)
    # existing fake child LLM + order_store + process_results with 1 AUTO kind=order

    stage = ConfirmStage(
        confirm_agent=fake_confirm_agent,
        order_store=fake_order_store,
        audit_logger=audit_logger,
    )
    # drive with state that has 1 process_results entry with kind=="order"

    drafted = [
        c for c in audit_logger.emit.await_args_list
        if c.kwargs.get("action") == "email_drafted"
    ]
    assert len(drafted) == 1
    assert "order_id" in drafted[0].kwargs["payload"]
    assert "body_key" in drafted[0].kwargs["payload"]
    assert drafted[0].kwargs["outcome"] == "ok"
```

- [ ] **Step 8.2: Run — expect failure**

Run: `uv run pytest tests/unit/test_stage_confirm.py::test_confirm_emits_email_drafted_per_order -v`

Expected: fail.

- [ ] **Step 8.3: Patch `ConfirmStage._audited_run`**

Inside the per-`kind=="order"` branch, AFTER the successful `update_with_confirmation` write, add:

```python
body_key = f"{entry['filename']}#{entry['sub_doc_index']}"
await self._audit_logger.emit(
    correlation_id=ctx.session.state.get("correlation_id", ""),
    session_id=ctx.session.id,
    source_message_id=self._extract_source_message_id(ctx.session.state),
    stage="lifecycle",
    phase="lifecycle",
    action="email_drafted",
    outcome="ok",
    payload={
        "order_id": result_dict["result"]["order"]["source_message_id"],
        "body_key": body_key,
    },
)
```

- [ ] **Step 8.4: Run tests**

Run: `uv run pytest tests/unit/test_stage_confirm.py -v`

Expected: all green.

- [ ] **Step 8.5: Commit**

```bash
git add backend/my_agent/stages/confirm.py tests/unit/test_stage_confirm.py
git commit -m "feat(track-d): ConfirmStage emits email_drafted lifecycle per order"
```

---

## Task 9: `FinalizeStage` emits `run_finalized`

**Files:**
- Modify: `backend/my_agent/stages/finalize.py`
- Modify: `tests/unit/test_stage_finalize.py`

- [ ] **Step 9.1: Write failing test**

Append to `tests/unit/test_stage_finalize.py`:

```python
@pytest.mark.asyncio
async def test_finalize_emits_run_finalized_with_counts():
    audit_logger = AsyncMock(spec=AuditLogger)
    # existing fake summary agent

    stage = FinalizeStage(summary_agent=fake_summary_agent, audit_logger=audit_logger)
    ctx = make_stage_ctx(
        stage=stage,
        state={
            "correlation_id": "c1",
            "envelope": {"message_id": "m1"},
            "process_results": [<1 AUTO, 1 exception>],
            "skipped_docs": [<1 entry>],
            "reply_handled": False,
        },
    )
    await collect_events(stage.run_async(ctx))

    finalized = [
        c for c in audit_logger.emit.await_args_list
        if c.kwargs.get("action") == "run_finalized"
    ]
    assert len(finalized) == 1
    payload = finalized[0].kwargs["payload"]
    assert payload["orders_created"] == 1
    assert payload["exceptions_opened"] == 1
    assert payload["docs_skipped"] == 1
    assert payload["reply_handled"] is False
```

- [ ] **Step 9.2: Run — expect failure**

Run: `uv run pytest tests/unit/test_stage_finalize.py::test_finalize_emits_run_finalized_with_counts -v`

Expected: fail.

- [ ] **Step 9.3: Patch `FinalizeStage._audited_run`**

AFTER summary agent runs and counts are computed, add:

```python
await self._audit_logger.emit(
    correlation_id=ctx.session.state.get("correlation_id", ""),
    session_id=ctx.session.id,
    source_message_id=self._extract_source_message_id(ctx.session.state),
    stage="lifecycle",
    phase="lifecycle",
    action="run_finalized",
    outcome="ok",
    payload={
        "orders_created": orders_created,
        "exceptions_opened": exceptions_opened,
        "docs_skipped": docs_skipped,
        "reply_handled": reply_handled,
    },
)
```

- [ ] **Step 9.4: Run tests**

Run: `uv run pytest tests/unit/test_stage_finalize.py -v`

Expected: all green.

- [ ] **Step 9.5: Commit**

```bash
git add backend/my_agent/stages/finalize.py tests/unit/test_stage_finalize.py
git commit -m "feat(track-d): FinalizeStage emits run_finalized lifecycle"
```

---

## Task 10: Firestore security rules + composite indexes

**Files:**
- Modify: `firebase/firestore.rules`
- Modify: `firebase/firestore.indexes.json`

- [ ] **Step 10.1: Append audit_log rule block**

Read the current `firebase/firestore.rules` file. Find the closing `}` of the existing `match /databases/{database}/documents { ... }` block. Add before that closing brace:

```
match /audit_log/{doc} {
  allow read:   if request.auth != null;
  allow create: if request.auth != null;
  allow update, delete: if false;
}
```

- [ ] **Step 10.2: Add 3 composite indexes**

In `firebase/firestore.indexes.json`, append to the `indexes` array:

```json
{
  "collectionGroup": "audit_log",
  "queryScope": "COLLECTION",
  "fields": [
    {"fieldPath": "correlation_id", "order": "ASCENDING"},
    {"fieldPath": "ts", "order": "ASCENDING"}
  ]
},
{
  "collectionGroup": "audit_log",
  "queryScope": "COLLECTION",
  "fields": [
    {"fieldPath": "source_message_id", "order": "ASCENDING"},
    {"fieldPath": "ts", "order": "ASCENDING"}
  ]
},
{
  "collectionGroup": "audit_log",
  "queryScope": "COLLECTION",
  "fields": [
    {"fieldPath": "stage", "order": "ASCENDING"},
    {"fieldPath": "action", "order": "ASCENDING"},
    {"fieldPath": "ts", "order": "DESCENDING"}
  ]
}
```

- [ ] **Step 10.3: Validate JSON**

Run: `uv run python -c "import json; json.load(open('firebase/firestore.indexes.json'))"`

Expected: no output (valid JSON).

- [ ] **Step 10.4: Commit**

```bash
git add firebase/firestore.rules firebase/firestore.indexes.json
git commit -m "feat(track-d): audit_log security rules + 3 composite indexes"
```

---

## Task 11: Integration tests against the emulator

**Files:**
- Create: `tests/integration/test_audit_log_emulator.py`

- [ ] **Step 11.1: Start emulator + set env**

In a separate terminal:
```bash
firebase emulators:start --only firestore
```
In the test terminal:
```bash
export FIRESTORE_EMULATOR_HOST=localhost:8080
```

- [ ] **Step 11.2: Write the 3 failing tests**

Create `tests/integration/test_audit_log_emulator.py`:

```python
"""Emulator-backed integration tests for Track D audit log.

Exercises the full 9-stage pipeline via Runner.run_async against the
real Firestore emulator, with stubbed LlmAgent children. Guards the
happy-path audit doc count, correlation_id sharing, immutability
(security rules enforcement), and retry behavior.

Requires FIRESTORE_EMULATOR_HOST + firestore emulator + seeded
master data. Tests marked @pytest.mark.firestore_emulator auto-skip
otherwise (existing pytest config).
"""
from __future__ import annotations

import pytest
from google.api_core.exceptions import PermissionDenied
from google.cloud.firestore_v1.async_client import AsyncClient

pytestmark = [pytest.mark.firestore_emulator, pytest.mark.asyncio]


class TestAuditLogEmulator:
    async def test_happy_path_produces_multi_event_audit_trail(
        self,
        emulator_client: AsyncClient,
        seeded_master_data,
        patterson_fixture_env,  # existing happy-path fixture from test_orchestrator_emulator.py
    ):
        # Drive one AUTO_APPROVE run end-to-end (reuse the harness from
        # tests/integration/test_orchestrator_emulator.py)
        result = await _run_pipeline(patterson_fixture_env)
        assert result.run_summary.orders_created == 1

        # Fetch all audit_log docs
        docs = [
            doc.to_dict()
            async for doc in emulator_client.collection("audit_log").stream()
        ]
        assert len(docs) >= 20, f"expected >=20 audit docs, got {len(docs)}"

        # All non-first-IngestStage-entry docs share one correlation_id
        correlation_ids = {d["correlation_id"] for d in docs if d["correlation_id"]}
        assert len(correlation_ids) == 1

        # Required header fields populated
        for d in docs:
            assert "session_id" in d and d["session_id"]
            assert "stage" in d and d["stage"]
            assert "phase" in d and d["phase"] in {"entered", "exited", "lifecycle"}
            assert "action" in d and d["action"]
            assert d["agent_version"] == "track-a-v0.2"

        # First audit doc should be IngestStage entered
        # (if FirstStore.stream returns in insertion order; otherwise sort by ts)
        docs_sorted = sorted(docs, key=lambda d: d["ts"])
        assert docs_sorted[0]["stage"] == "IngestStage"
        assert docs_sorted[0]["phase"] == "entered"

        # Last audit doc should be FinalizeStage exited, outcome=ok
        assert docs_sorted[-1]["stage"] == "FinalizeStage"
        assert docs_sorted[-1]["phase"] == "exited"
        assert docs_sorted[-1]["outcome"] == "ok"

    async def test_audit_log_is_immutable(
        self,
        emulator_client: AsyncClient,
        seeded_master_data,
        patterson_fixture_env,
    ):
        await _run_pipeline(patterson_fixture_env)

        # Pick one audit doc + try to mutate it
        async for doc in emulator_client.collection("audit_log").limit(1).stream():
            ref = doc.reference
            with pytest.raises((PermissionDenied, Exception)):
                # Emulator with rules enforcement should reject update
                await ref.update({"stage": "tamper"})
            break

    async def test_retries_produce_distinct_correlation_ids(
        self,
        emulator_client: AsyncClient,
        seeded_master_data,
        patterson_fixture_env,
    ):
        # Two separate Runner.run_async invocations with the same envelope
        await _run_pipeline(patterson_fixture_env)
        await _run_pipeline(patterson_fixture_env, message_id_override="msg-retry-2")

        docs = [
            doc.to_dict()
            async for doc in emulator_client.collection("audit_log").stream()
        ]
        correlation_ids = {d["correlation_id"] for d in docs if d["correlation_id"]}
        assert len(correlation_ids) >= 2
```

Flesh out `_run_pipeline` by mirroring the existing orchestrator-emulator test's pipeline-run helper. The key difference: the helper must now construct a real `AuditLogger(client=emulator_client, agent_version="track-a-v0.2")` and pass it to `build_root_agent`.

- [ ] **Step 11.3: Run — expect failure on fixture wiring**

Run: `uv run pytest tests/integration/test_audit_log_emulator.py -v`

Expected: likely fixture name mismatches (`emulator_client`, `patterson_fixture_env`). Use existing test file as reference for fixture names.

- [ ] **Step 11.4: Fix fixture wiring + re-run until green**

Adjust fixture imports / names to match what exists in `tests/integration/conftest.py` and `tests/integration/test_orchestrator_emulator.py`. Also ensure the emulator has the updated `firestore.rules` loaded — `firebase emulators:start` re-reads the file on every startup, so if rules changed mid-test, restart the emulator.

For the immutability test specifically: Firestore emulator honors security rules ONLY when the client is authenticated. If the client runs as "admin" (which is the default for emulator dev), rules are bypassed. Confirm auth mode. If the emulator is in admin mode by default, the immutability test needs the client to use `firestore.Client(credentials=AnonymousCredentials())` or equivalent. If this turns out to require significant setup, mark the test `@pytest.mark.skip(reason="emulator default admin mode bypasses rules; Track D Phase 2 hardens")` and add a TODO pointing back to this plan.

- [ ] **Step 11.5: Commit**

```bash
git add tests/integration/test_audit_log_emulator.py
git commit -m "test(track-d): integration tests for audit log against emulator"
```

---

## Task 12: Update status + roadmap docs

**Files:**
- Modify: `research/Order-Intake-Sprint-Status.md`
- Modify: `Glacis-Order-Intake.md`

- [ ] **Step 12.1: Flip §13 Eval & Observability bullets in `Glacis-Order-Intake.md`**

Find `- [Post-MVP] **audit_log collection — append-only Firestore collection**` — flip to `[MVP ✓]`:

```markdown
- `[MVP ✓]` **`audit_log` collection — append-only Firestore collection** — every agent action across the 9-stage pipeline: `stage_entered` / `stage_exited` per stage (9 × 2 = 18/run) plus `envelope_received` / `routing_decided` / `order_persisted` | `exception_opened` | `duplicate_seen` / `email_drafted` / `run_finalized` lifecycle events. Immutable via security rules (`allow update, delete: if false`). Fail-open on write errors (MVP call; Phase 2 hardens to fail-closed). MVP: Track D landed 2026-04-24 across commits `<sha-task-1>` through `<sha-task-11>`. Source: `Security-Audit.md`, `ERP-Integration.md`.

- `[MVP ✓]` **`session_id` + `correlation_id` on every audit event** — `correlation_id` is fresh UUID4 per pipeline invocation, minted by `IngestStage` as its first business-logic act, threaded through `ctx.session.state["correlation_id"]`. Query `audit_log.where("correlation_id", "==", X).order_by("ts")` reconstructs the full decision chain for one run. `audit_log.where("source_message_id", "==", X).order_by("ts")` reconstructs all retries of one envelope. Source: `Security-Audit.md`.
```

- [ ] **Step 12.2: Strike Phase 2 roadmap bullets for audit log + correlation_id**

In the Phase 2 section, remove the audit_log + correlation_id lines (they're now `[MVP ✓]`).

- [ ] **Step 12.3: Extend `research/Order-Intake-Sprint-Status.md` Built inventory**

Add a new block to the Built inventory — after the Track C entries:

```
backend/audit/models.py                                                 ✓ Track D (<sha-task-1>) — AuditEvent pydantic model (strict header + free-form payload)
backend/audit/logger.py                                                 ✓ Track D (<sha-task-2>) — AuditLogger fail-open emitter
backend/my_agent/stages/_audited.py                                     ✓ Track D (<sha-task-3>) — AuditedStage mixin wrapping _run_async_impl
backend/my_agent/stages/*.py (base class migration)                     ✓ Track D (<sha-task-4>) — all 9 stages switched from BaseAgent to AuditedStage
backend/my_agent/stages/ingest.py (correlation_id + envelope_received)  ✓ Track D (<sha-task-5>)
backend/my_agent/stages/validate.py (routing_decided)                   ✓ Track D (<sha-task-6>)
backend/my_agent/stages/persist.py (order_persisted/exception_opened/duplicate_seen)  ✓ Track D (<sha-task-7>)
backend/my_agent/stages/confirm.py (email_drafted)                      ✓ Track D (<sha-task-8>)
backend/my_agent/stages/finalize.py (run_finalized)                     ✓ Track D (<sha-task-9>)
backend/my_agent/agent.py (AuditLogger threading)                       ✓ Track D (<sha-task-4>) — build_root_agent requires audit_logger kwarg
firebase/firestore.rules (audit_log immutable block)                    ✓ Track D (<sha-task-10>)
firebase/firestore.indexes.json (3 audit_log composite indexes)         ✓ Track D (<sha-task-10>)
tests/integration/test_audit_log_emulator.py                            ✓ Track D (<sha-task-11>) — 3 emulator tests
```

- [ ] **Step 12.4: Update the §13 row in the Status table top-matter**

Find the Status table row `| **Eval / quality gate** | (implicit in spec) | — | **Track E**: ...|` — audit_log doesn't fit there. Add a new row:

```
| **13. Audit log + correlation_id** | Every action structured-logged to immutable Firestore collection; session_id + correlation_id on every event | `audit_log` collection live ✓ — AuditedStage mixin emits stage_entered/stage_exited per stage; 5 lifecycle emits per run (envelope_received/routing_decided/order_persisted\|exception_opened\|duplicate_seen/email_drafted/run_finalized); correlation_id UUID4 minted by IngestStage in state; immutable via rules; fail-open on write errors per Track D (2026-04-24 commits <sha-task-1> through <sha-task-11>). | Nothing on MVP. Phase 2 hardens fail-closed + RBAC on reads. |
```

- [ ] **Step 12.5: Commit**

```bash
git add research/Order-Intake-Sprint-Status.md Glacis-Order-Intake.md
git commit -m "docs(track-d): flip audit_log + correlation_id to [MVP ✓] across both docs"
```

---

## Task 13: Final verification

- [ ] **Step 13.1: Full unit suite**

Run: `uv run pytest tests/unit -v 2>&1 | tail -20`

Expected: all green. Test count: 323 baseline + 25 new = ~348 unit.

- [ ] **Step 13.2: Full integration suite (with emulator)**

Start emulator, `export FIRESTORE_EMULATOR_HOST=localhost:8080`, then:

Run: `uv run pytest tests/integration -v 2>&1 | tail -20`

Expected: all green. Test count: 14+ baseline + 3 audit_log_emulator = 17+.

- [ ] **Step 13.3: Live smoke on MM Machine fixture (optional, high-confidence)**

With emulator still running + real Gemini + LlamaCloud creds in env:

Run: `uv run python scripts/smoke_run.py data/email/mm_machine_reorder_2026-04-24.eml`

Expected: AUTO_APPROVE run completes as before. Afterwards, inspect `audit_log`:

```bash
uv run python -c "
import asyncio
from google.cloud.firestore_v1.async_client import AsyncClient

async def main():
    client = AsyncClient(project='demo-order-intake-local')
    docs = [d.to_dict() async for d in client.collection('audit_log').stream()]
    print(f'{len(docs)} audit docs')
    for d in sorted(docs, key=lambda x: x.get('ts', '')):
        print(f\"  {d['stage']:>22} {d['phase']:<10} {d['action']:<22} {d.get('outcome') or ''}\")

asyncio.run(main())
"
```

Expected: ≥20 audit docs, one correlation_id, clean start-to-finish trace.

- [ ] **Step 13.4: Done.**

Track D closed. Next session picks up Track A (Gmail ingress + egress) via brainstorm → spec → plan → execute.

---

## Self-review

**Spec coverage:**
- ✅ Decision 1 (stage-level granularity) → Tasks 3-9
- ✅ Decision 2 (AuditedStage mixin) → Task 3 + Task 4
- ✅ Decision 3 (strict header + free-form payload) → Task 1
- ✅ Decision 4 (fresh UUID4 per invocation, minted by IngestStage) → Task 5
- ✅ Decision 5 (fail-open audit write) → Task 2 test #2
- ✅ Decision 6 (PrivateAttr constructor kwarg) → Task 3 mixin + Task 4 all 9 stages
- ✅ 5 lifecycle events (envelope_received / routing_decided / order_persisted|exception_opened|duplicate_seen / email_drafted / run_finalized) → Tasks 5-9
- ✅ Firestore rules (append-only) → Task 10
- ✅ 3 composite indexes → Task 10
- ✅ Integration tests (happy path, immutability, retry) → Task 11
- ✅ Doc flips → Task 12

**Placeholder scan:**
- Task 6.1 test uses `parsed_docs: [{"filename": ..., "parsed": {...}, "sub_doc": {...}}]` with `{...}` for nested dicts — explicitly "see existing ValidateStage test for the concrete shape." Flagged but acceptable under "skilled developer" framing.
- Task 7.1, 8.1, 9.1 similarly reference `<existing fixture pattern>` without inlining the entire fixture body. Each task's step prose names the exact source test file to mirror.
- Task 11 integration test helper `_run_pipeline` shorthand — Task 11.4 explicitly says "mirror the existing orchestrator-emulator test's pipeline-run helper." Acceptable.
- Task 11.4 notes that the immutability test may need `@pytest.mark.skip` if emulator admin mode bypasses rules — this is a decision the executor makes with live context.

No fixes needed inline — all flagged items have concrete reference points in the existing codebase, consistent with "skilled developer" framing.

**Type consistency:**
- `AuditEvent` field set is consistent across Tasks 1 (definition), 2 (logger), 3 (mixin), 5-9 (lifecycle emits).
- `AuditLogger.emit` kwarg set (`correlation_id`, `session_id`, `source_message_id`, `stage`, `phase`, `action`, `outcome`, `payload`) consistent across all tasks.
- `AuditedStage.__init__` takes `audit_logger: Any` (PrivateAttr pattern) — consistent Task 3 + Task 4 + all stage migrations.
- Stage constructor kwargs table in Task 4 matches the existing code + adds `audit_logger` uniformly.
- `ctx.session.state["correlation_id"]` as the state key — consistent Task 3, 5, 6, 7, 8, 9.

No inconsistencies found.

**Scope check:** 13 tasks, TDD-cycled, each 4-7 steps. Estimated execution: 6-8 hours for a focused implementer. Fits in one session. Task 4 is the largest by line count (9 files × mechanical migration) but the shape is uniform and test-driven.
