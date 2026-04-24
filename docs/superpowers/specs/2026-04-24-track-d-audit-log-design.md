---
type: design-spec
topic: "Track D — Audit Log + correlation_id"
track: D
date: 2026-04-24
parent: "research/Order-Intake-Sprint-Status.md"
source_spec: "Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Security-Audit.md §audit_log"
status: approved-for-implementation
tags:
  - design-spec
  - track-d
  - audit-log
  - correlation-id
  - observability
---

# Track D — Audit Log + correlation_id — Design

## Summary

Append-only `audit_log` Firestore collection populated automatically by every `BaseAgent` stage in the 9-stage pipeline plus a handful of lifecycle emits (`envelope_received`, `routing_decided`, `order_persisted` | `exception_opened`, `email_drafted`, `run_finalized`). Each event carries a strict header — `correlation_id` (fresh UUID4 per pipeline invocation), `source_message_id`, `session_id`, `stage`, `phase`, `action`, `outcome`, `ts`, `agent_version` — plus a free-form `payload: dict[str, Any]`. Firestore security rules enforce `allow update, delete: if false`. Audit-write failures are fail-open (logged, pipeline continues) for MVP.

This closes Glacis `Security-Audit.md` §audit_log — the Phase-2 enterprise-readiness foundation (immutable action trail, reconstructable decision chains, SOC-2 precursor).

## Context

- `InMemorySessionService` provides `session_id` per `Runner.run_async` invocation; today's codebase has no cross-invocation audit trail other than the business records (`orders` / `exceptions`) themselves.
- 9 `BaseAgent` stages + 3 child `LlmAgent`s + 1 root `SequentialAgent` compose the pipeline (`build_root_agent` in `backend/my_agent/agent.py`). Every stage currently defines `async def _run_async_impl(self, ctx)` and uses `BaseAgent` directly as the base class. Source-of-truth list as of head `1df0ae1`: ingest, reply_shortcircuit, classify, parse, validate, clarify, persist, confirm, finalize.
- `AGENT_VERSION = "track-a-v0.2"` (set at `backend/my_agent/agent.py`, bumped 2026-04-24 with ConfirmStage).
- Existing `firebase/firestore.rules` + composite-index file already exist — Track D extends both.
- Dependency-injection pattern established in Track A: `PrivateAttr` typed `Any` (Protocol / concrete / `LlmAgent`) passed as kwargs through stage constructors. Track D adopts this verbatim.

## Architectural decisions

The six foundational calls, each with trade-offs explicitly considered and rejected alternatives documented.

### Decision 1 — Granularity: stage-level only

Emit one event on stage entry + one on stage exit per `BaseAgent` stage, plus ~5 lifecycle events per run (`envelope_received`, `routing_decided`, `order_persisted` | `exception_opened`, `email_drafted` if fired, `run_finalized`). Total ~20-25 `audit_log` docs per order.

**Rejected:**
- **Stage + LLM-call level** (every Gemini call via `before_model_callback` / `after_model_callback`) — doubles Firestore writes; forensic detail not yet needed at MVP.
- **Everything** (stage + LLM + tool + Firestore write) — 60-100 docs/order, dashboard noise, higher cost.
- **Decision-only (coarse)** — ~5-8 docs/order but loses per-stage timing; silent stage failures wouldn't surface.

### Decision 2 — Emit style: `AuditedStage` mixin wrapping `_run_async_impl`

New `AuditedStage` class inherits from `BaseAgent` and provides a concrete `_run_async_impl` that:
1. Emits `stage_entered` before yielding to the stage body,
2. Delegates to a new abstract `_audited_run(ctx)` async generator method (subclasses implement this instead of `_run_async_impl`),
3. Emits `stage_exited` with outcome (`"ok"` or `"error:<ExceptionClass>"`) in a `finally` block.

Each of the 9 stages changes `class X(BaseAgent)` → `class X(AuditedStage)` and renames `_run_async_impl` → `_audited_run`. Zero boilerplate in stage bodies. Single place to evolve the audit contract later.

**Rejected:**
- **Explicit `self._audit.emit()` at stage boundaries** — 18 manual touch-points, uniformity drift.
- **Decorator `@audited`** — can't access `self` cleanly for payload enrichment.
- **ADK `before_*` / `after_*` callbacks** — scoped to `LlmAgent` only; misses 6 of 9 stages.

### Decision 3 — Event schema: strict header + free-form payload dict

`AuditEvent(BaseModel)` with `ConfigDict(extra="forbid")`. Required header: `correlation_id`, `source_message_id` (Optional), `session_id`, `stage`, `phase`, `action`, `outcome` (Optional), `ts`, `agent_version`. Plus `payload: dict[str, Any]` for stage-specific detail. One Pydantic class. One collection.

**Rejected:**
- **Per-event-type subclasses with discriminated union** — schema bump per new event type; verbose.
- **Free-form dict, 3 required fields** — loses type safety; schema rot.

### Decision 4 — `correlation_id`: fresh UUID4 per invocation, minted by `IngestStage`

`IngestStage._audited_run` mints `uuid4().hex` as its first business-logic act (after the mixin's `stage_entered` emit), writes it via `EventActions.state_delta({"correlation_id": ...})`. All subsequent stages read `ctx.session.state["correlation_id"]`. Retries of the same envelope produce different correlation_ids.

**Rejected:**
- **Reuse ADK `session_id` as `correlation_id`** — Future `VertexAiMemoryBankService` may persist sessions across invocations for memory retrieval; silent semantic change.
- **Derive from `source_message_id`** — retries collapse to same id; conflates envelope vs invocation identity.

### Decision 5 — Audit-write failure handling: fail-open

`AuditLogger.emit` catches exceptions, logs an ERROR line with structured context (correlation_id, stage, action, error), and returns normally. Pipeline business logic continues.

**Rejected:**
- **Fail-closed** (raise on audit write error) — enterprise-ready but cost: one Firestore blip during audit write kills an otherwise-successful run. Phase 2 hardening can flip.
- **Hybrid** (fail-closed on lifecycle events, fail-open on stage entry/exit) — too clever for MVP; policy-drift risk.

### Decision 6 — Dependency injection: `PrivateAttr` constructor kwarg on `AuditedStage`

`AuditedStage` declares `_audit_logger: Any = PrivateAttr()`. Each stage constructor takes `audit_logger` as a kwarg. `build_root_agent` threads the shared `AuditLogger` instance through to all 9 stages. Consistent with Track A's `OrderStore` / `ExceptionStore` / `LlmAgent`-as-Any injection pattern.

**Rejected:**
- **Pulled from `ctx.session.state`** — state is for serializable data; service object in state hurts type hints + test clarity.
- **Module-level singleton** — breaks testability; inconsistent with codebase.

## Components

### New file — `backend/audit/__init__.py`

Package marker. Re-exports `AuditEvent`, `AuditLogger`.

### New file — `backend/audit/models.py`

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

    correlation_id: str                                  # UUID4 hex per invocation
    source_message_id: Optional[str] = None              # None until IngestStage seeds envelope
    session_id: str                                      # ADK session id
    stage: str                                           # canonical stage name or "lifecycle"
    phase: Literal["entered", "exited", "lifecycle"]
    action: str                                          # "stage_entered" | "routing_decided" | ...
    outcome: Optional[str] = None                        # "ok" | "error:<ClassName>" | "auto_approve" | ...
    ts: datetime                                         # SERVER_TIMESTAMP at write time
    agent_version: str                                   # "track-a-v0.2"
    payload: dict[str, Any] = Field(default_factory=dict)
    schema_version: int = 1


__all__ = ["AuditEvent"]
```

### New file — `backend/audit/logger.py`

```python
"""Fail-open audit-log emitter. Construct once per process, inject
into stages via PrivateAttr kwarg."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from google.cloud.firestore_v1 import SERVER_TIMESTAMP
from google.cloud.firestore_v1.async_client import AsyncClient

from backend.audit.models import AuditEvent
from backend.utils.logging import get_logger

_log = get_logger(__name__)


class AuditLogger:
    """Writes one doc per audit event to the ``audit_log`` collection.

    Fail-open: Firestore exceptions are logged at ERROR and swallowed —
    the pipeline keeps running. Phase-2 compliance hardening flips to
    fail-closed by replacing this class.
    """

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
                ts=datetime.now(timezone.utc),  # pre-serialisation placeholder
                agent_version=self._agent_version,
                payload=payload or {},
            )
            data = event.model_dump(mode="json")
            data["ts"] = SERVER_TIMESTAMP  # swap after-dump so Firestore owns the timestamp
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

### New file — `backend/my_agent/stages/_audited.py`

```python
"""AuditedStage mixin — wraps _run_async_impl with entry/exit emits.

Subclass contract:
- Class attribute ``name: str`` must be set (stage's canonical name).
- Subclasses implement ``_audited_run(ctx)`` as the real stage body.
- ``correlation_id`` must be present in ``ctx.session.state`` by the
  time a non-Ingest stage runs — IngestStage seeds it as its first act.

The mixin emits ``stage_entered`` BEFORE yielding to ``_audited_run``,
then ``stage_exited`` in a ``finally`` block. If the body raises, the
exit event carries ``outcome=f"error:{exception_class_name}"`` and the
exception re-raises.
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
            # Re-read state in case _audited_run seeded source_message_id
            source_message_id = self._extract_source_message_id(ctx.session.state)
            correlation_id = ctx.session.state.get("correlation_id", "")
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
        raise NotImplementedError("AuditedStage subclasses must implement _audited_run")
        yield  # keep this an async-generator for typing

    @staticmethod
    def _extract_source_message_id(state) -> Optional[str]:
        envelope = state.get("envelope")
        if isinstance(envelope, dict):
            return envelope.get("message_id")
        return None


__all__ = ["AuditedStage"]
```

### Modified — each of the 9 stage files under `backend/my_agent/stages/`

For every stage (`ingest.py`, `reply_shortcircuit.py`, `classify.py`, `parse.py`, `validate.py`, `clarify.py`, `persist.py`, `confirm.py`, `finalize.py`):

1. Change base class: `class X(BaseAgent)` → `class X(AuditedStage)`.
2. Rename method: `_run_async_impl` → `_audited_run`.
3. Remove the `# type: ignore[override]` comment (no longer needed — `_audited_run` is the documented extension point).
4. Constructor: add `audit_logger: Any` to whatever init kwargs exist. Call `super().__init__(audit_logger=audit_logger, **other_kwargs_forwarded_to_BaseAgent)`.

**Lifecycle emits inside specific stages** (besides the mixin's entry/exit):

- **`ingest.py` `_audited_run`** — after `parse_eml` completes and envelope has been seeded to state:
  ```python
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

- **`validate.py` `_audited_run`** — per sub-doc, after `validator.validate` returns:
  ```python
  await self._audit_logger.emit(
      ...,
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

- **`persist.py` `_audited_run`** — per sub-doc, after `coordinator.process` returns:
  ```python
  action = {"order": "order_persisted", "exception": "exception_opened", "duplicate": "duplicate_seen"}[result.kind]
  payload = {"sub_doc_index": entry["sub_doc_index"], "filename": entry["filename"]}
  if result.order:
      payload["order_id"] = result.order.source_message_id
  if result.exception:
      payload["exception_id"] = result.exception.source_message_id
  await self._audit_logger.emit(..., stage="lifecycle", phase="lifecycle",
                                action=action, outcome=result.kind, payload=payload)
  ```

- **`confirm.py` `_audited_run`** — per `kind=="order"` result after `update_with_confirmation`:
  ```python
  await self._audit_logger.emit(..., stage="lifecycle", phase="lifecycle",
                                action="email_drafted", outcome="ok",
                                payload={"order_id": result.order.source_message_id, "body_key": body_key})
  ```

- **`finalize.py` `_audited_run`** — after summary agent returns:
  ```python
  await self._audit_logger.emit(..., stage="lifecycle", phase="lifecycle",
                                action="run_finalized", outcome="ok", payload={
      "orders_created": orders_created,
      "exceptions_opened": exceptions_opened,
      "docs_skipped": docs_skipped,
      "reply_handled": reply_handled,
  })
  ```

### Modified — `backend/my_agent/agent.py`

`build_root_agent` accepts `audit_logger: AuditLogger` as a new required kwarg. Threads it into every stage constructor. `_build_default_root_agent` constructs a single `AuditLogger(client=<shared_async_client>, agent_version=AGENT_VERSION)` and reuses it.

### Modified — `firebase/firestore.rules`

Add a new `/audit_log/{doc}` match block:

```
match /audit_log/{doc} {
  allow read:   if request.auth != null;
  allow create: if request.auth != null;
  allow update, delete: if false;
}
```

### Modified — `firebase/firestore.indexes.json`

Three new composite indexes on `audit_log`:

```json
{"collectionGroup": "audit_log", "queryScope": "COLLECTION", "fields": [
  {"fieldPath": "correlation_id", "order": "ASCENDING"},
  {"fieldPath": "ts", "order": "ASCENDING"}
]},
{"collectionGroup": "audit_log", "queryScope": "COLLECTION", "fields": [
  {"fieldPath": "source_message_id", "order": "ASCENDING"},
  {"fieldPath": "ts", "order": "ASCENDING"}
]},
{"collectionGroup": "audit_log", "queryScope": "COLLECTION", "fields": [
  {"fieldPath": "stage", "order": "ASCENDING"},
  {"fieldPath": "action", "order": "ASCENDING"},
  {"fieldPath": "ts", "order": "DESCENDING"}
]}
```

## Data flow

### AUTO_APPROVE happy path (1 sub-doc)

```
Runner.run_async(envelope)
  IngestStage                  →  stage_entered, envelope_received, stage_exited      (3 docs)
  ReplyShortCircuitStage       →  stage_entered, stage_exited                          (2)
  ClassifyStage                →  stage_entered, stage_exited                          (2)
  ParseStage                   →  stage_entered, stage_exited                          (2)
  ValidateStage                →  stage_entered, routing_decided, stage_exited         (3)
  ClarifyStage                 →  stage_entered, stage_exited                          (2)
  PersistStage                 →  stage_entered, order_persisted, stage_exited         (3)
  ConfirmStage                 →  stage_entered, email_drafted, stage_exited           (3)
  FinalizeStage                →  stage_entered, run_finalized, stage_exited           (3)
                                                                      = 23 audit docs
```

### ESCALATE (duplicate) path (1 sub-doc)

Same as above but `routing_decided.outcome="escalate"`, `PersistStage` emits `exception_opened` instead of `order_persisted`, `ConfirmStage` emits no `email_drafted` → **22 docs**.

### Reply short-circuit path

`ReplyShortCircuitStage` matches — every downstream stage's `_audited_run` no-ops but the mixin still emits entered/exited for each → **18 stage-phase docs + envelope_received + run_finalized = 20 docs**.

### Correlation propagation

- `IngestStage` mints `uuid4().hex` and writes it to `ctx.session.state["correlation_id"]` via `EventActions.state_delta`.
- `AuditedStage._run_async_impl` reads `correlation_id` from `ctx.session.state` on every entry/exit emit.
- For `IngestStage` specifically, the **first** `stage_entered` emit reads `correlation_id=""` (state doesn't have it yet) — the mixin accepts empty string. The envelope_received and stage_exited emits inside IngestStage use the minted id. **Documented limitation:** the single pre-mint `stage_entered` audit row is the one doc in the run without a correlation_id. Consumers filter by `source_message_id` or `session_id` to reconstruct the full trace including that edge row.

## Error handling

| Scenario | Behavior |
|---|---|
| `audit_log` Firestore write fails | `AuditLogger.emit` catches, logs ERROR, returns. Pipeline continues. |
| Stage body raises | Mixin `finally` emits `stage_exited` with `outcome=f"error:{ClassName}"`, then re-raises so ADK's retry / error handling still fires. |
| `correlation_id` missing from state | Emit uses empty string. Only happens for `IngestStage`'s first entry emit. Not a crash. |
| `source_message_id` unavailable pre-parse | Field is `Optional[str]`, emits `None`. Not a crash. |
| `AuditEvent` Pydantic validation fails | Caught by the same fail-open `try/except` in `AuditLogger.emit`. Indicates a code bug — surfaces in ERROR logs immediately during dev. |
| Fan-out: `ParsedDocument` with N sub-docs | ValidateStage / PersistStage / ConfirmStage each emit N lifecycle events. Stage entry/exit still fires once per stage. Audit doc count scales with sub-docs for those 3 stages. |
| Retry of same envelope | Distinct `correlation_id` per `Runner.run_async` call. Query `where("source_message_id", "==", X).order_by("ts")` returns all retries interleaved. |

### Logging

All emits are structured-log-friendly via `backend.utils.logging`. The fail-open failure path emits `audit_emit_failed` with `correlation_id`, `stage`, `action`, `error` as structured fields so operators grep the pipeline log for audit losses.

## Testing

### Unit — new `tests/unit/test_audit_event.py` (~4 tests)

1. All required fields enforced (missing any of `correlation_id`, `session_id`, `stage`, `phase`, `action`, `ts`, `agent_version` raises `ValidationError`)
2. `payload` accepts free-form JSON-serializable dict
3. `schema_version` defaults to `1`
4. `extra="forbid"` rejects unknown top-level fields

### Unit — new `tests/unit/test_audit_logger.py` (~5 tests)

1. `emit` writes one doc to `audit_log` via `FakeAsyncClient`
2. Written doc has all header fields + `schema_version=1`
3. Exception from `client.collection(...).add(...)` is swallowed (fake raises → no propagation)
4. Exception path emits `audit_emit_failed` log line (assert via caplog or structlog capture)
5. `agent_version` propagates from constructor into written doc

### Unit — new `tests/unit/test_stage_audited.py` (~5 tests)

1. Mixin subclass emits `stage_entered` then `stage_exited` wrapping the body
2. When body raises `RuntimeError`, mixin emits `stage_exited` with `outcome="error:RuntimeError"` and re-raises
3. `correlation_id` missing from state → emit uses `""`; no crash
4. `source_message_id` extracted from `state["envelope"]["message_id"]` when present
5. Mixin preserves yield ordering — body events reach the caller before `stage_exited` emit completes

### Unit — modify existing 9 `tests/unit/test_stage_*.py` files

Each existing stage test fixture adds `audit_logger=AsyncMock(spec=AuditLogger)` to the stage constructor call. Add one new test per file:

```python
async def test_<stage>_emits_entered_and_exited(audit_logger_mock, ...):
    stage = <StageClass>(audit_logger=audit_logger_mock, ...)
    async for _ in stage.run_async(ctx):
        pass
    calls = audit_logger_mock.emit.call_args_list
    phases = [c.kwargs["phase"] for c in calls if c.kwargs.get("stage") == "<StageName>"]
    assert phases[0] == "entered"
    assert phases[-1] == "exited"
```

**Total:** +9 new stage-audit tests (one per file).

### Unit — extend `tests/unit/test_orchestrator_build.py` (+2 tests)

1. `build_root_agent` requires `audit_logger` kwarg — missing → `TypeError`
2. Every stage in the constructed `SequentialAgent.sub_agents` has `._audit_logger` bound to the same instance

### Integration — new `tests/integration/test_audit_log_emulator.py` (~3 tests)

1. **Happy-path** — drive a single AUTO_APPROVE fixture through `Runner.run_async` against the emulator + `FakeChildLlmAgent` stubs; query `audit_log.where("correlation_id", "==", X).order_by("ts")`. Assert:
   - >= 20 docs
   - All docs share one `correlation_id`
   - All required header fields populated
   - First doc is `stage="IngestStage"`, `phase="entered"`
   - Last doc is `stage="FinalizeStage"`, `phase="exited"`, `outcome="ok"`

2. **Immutability** — write one audit doc via `Runner.run_async`; attempt `doc.reference.update({...})` against the emulator; assert `PermissionDenied` or equivalent.

3. **Retry distinct correlation_ids** — invoke same envelope twice (two separate `Runner.run_async` calls); query by `source_message_id`; assert two distinct `correlation_id` values present in the audit docs.

### Total test delta

- New unit: 4 (event) + 5 (logger) + 5 (mixin) + 9 (stage smoke) + 2 (orchestrator) = **25**
- New integration: **3**
- Baseline 323 → ~348 unit; 14+ integration (post-Track-C) → 17+ integration.

## Out of scope

- **LLM-call + tool-call audit events** — Decision 1 locked to stage-level only.
- **External payload storage (GCS / Cloud Storage)** — payload stays inline; size is bounded by stage-level granularity.
- **Read-side dashboard surfacing audit events** — dashboard is the later deferred `feat/dashboard`; Track D is write side only.
- **Fail-closed compliance mode** — MVP is fail-open; Phase 2 hardens.
- **BigQuery export of audit log** — Phase 3 enterprise.
- **RBAC via `request.auth.token.role`** — Phase 3; current rule is `read: if request.auth != null`.
- **Retention / TTL rules on `audit_log`** — out of scope; handled via Firestore TTL policies at ops time, not schema time.

## Success criteria

1. Every AUTO_APPROVE pipeline run produces ≥20 `audit_log` docs under one `correlation_id`.
2. Every doc carries all required header fields per `AuditEvent` schema and is non-mutable (attempted update via emulator returns `PermissionDenied`).
3. Full chain for a given envelope reconstructable via `where("source_message_id", "==", X).order_by("ts")`.
4. Audit-write failure during any stage does NOT fail the pipeline — pipeline run still completes, failure visible only in stdout ERROR log.
5. No regression in the existing 323-test unit baseline or integration baseline.
6. Live smoke on MM Machine fixture: single invocation produces ≥20 audit docs, all sharing one `correlation_id`.

## Files touched (summary)

| Type | Path | Change |
|---|---|---|
| New | `backend/audit/__init__.py` | Package marker + re-exports |
| New | `backend/audit/models.py` | `AuditEvent` Pydantic model |
| New | `backend/audit/logger.py` | `AuditLogger` fail-open emitter |
| New | `backend/my_agent/stages/_audited.py` | `AuditedStage` mixin |
| Modified | `backend/my_agent/stages/ingest.py` | base class → `AuditedStage`; rename method; mint correlation_id + `envelope_received` lifecycle emit |
| Modified | `backend/my_agent/stages/reply_shortcircuit.py` | base class + rename |
| Modified | `backend/my_agent/stages/classify.py` | base class + rename |
| Modified | `backend/my_agent/stages/parse.py` | base class + rename |
| Modified | `backend/my_agent/stages/validate.py` | base class + rename + `routing_decided` lifecycle emit per sub-doc |
| Modified | `backend/my_agent/stages/clarify.py` | base class + rename |
| Modified | `backend/my_agent/stages/persist.py` | base class + rename + `order_persisted` / `exception_opened` / `duplicate_seen` lifecycle emit per sub-doc |
| Modified | `backend/my_agent/stages/confirm.py` | base class + rename + `email_drafted` lifecycle emit per order |
| Modified | `backend/my_agent/stages/finalize.py` | base class + rename + `run_finalized` lifecycle emit |
| Modified | `backend/my_agent/agent.py` | `build_root_agent` threads `audit_logger` into all 9 stages |
| Modified | `firebase/firestore.rules` | Append `/audit_log/{doc}` block (immutable) |
| Modified | `firebase/firestore.indexes.json` | 3 new composite indexes on `audit_log` |
| New | `tests/unit/test_audit_event.py` | 4 tests |
| New | `tests/unit/test_audit_logger.py` | 5 tests |
| New | `tests/unit/test_stage_audited.py` | 5 tests |
| Modified | `tests/unit/test_stage_*.py` (9 files) | Add `audit_logger=AsyncMock(...)` fixture kwarg + 1 new emit-smoke test per file |
| Modified | `tests/unit/test_orchestrator_build.py` | +2 tests: required kwarg, shared instance across stages |
| New | `tests/integration/test_audit_log_emulator.py` | 3 tests |
| Modified | `research/Order-Intake-Sprint-Status.md` | Add Track D row + Built inventory entries |
| Modified | `Glacis-Order-Intake.md` | Flip §13 `audit_log` bullets `[Post-MVP]` → `[MVP ✓]` |

## Connections

- `research/Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Security-Audit.md` — §audit_log spec being implemented
- `research/Order-Intake-Sprint-Status.md` — extend Orchestration row + Built inventory
- `Glacis-Order-Intake.md` — §13 Eval & Observability: `audit_log` + `correlation_id + session_id` bullets flip `[Post-MVP]` → `[MVP ✓]`; Phase 2 roadmap bullets for audit log + correlation_id can be struck
- `docs/superpowers/specs/2026-04-24-track-c-duplicate-detection-design.md` — Track C's `duplicate_detected` log line has a natural home as a Track D lifecycle emit post-Track-D; optional bridging note in the code
- Tracks A (Gmail), B (Judge), E (Embeddings) all inherit the audit surface for free once Track D lands — no per-track audit plumbing needed
