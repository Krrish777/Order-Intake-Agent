---
type: design-spec
topic: "Track A2 — Gmail Egress (messages.send for clarify + confirmation)"
track: A2
date: 2026-04-24
parent: "research/Order-Intake-Sprint-Status.md"
source_spec: "Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Email-Ingestion.md §9 Clarify Email Generation"
status: approved-for-implementation
depends_on:
  - "Track A1 (Gmail ingress) — extends GmailClient with send_message + scopes constant"
  - "Track D (audit log) — SendStage inherits AuditedStage mixin (optional: A2 works without D but benefits if D has landed)"
  - "Track C (duplicate detection) — optional: ESCALATE-on-dup naturally has no body to send"
blocks:
  - "Track B (generator-judge) — wraps SendStage's actual send call with a judge-pass gate"
tags:
  - design-spec
  - track-a2
  - gmail
  - egress
  - send
  - messages.send
---

# Track A2 — Gmail Egress — Design

## Summary

A 10th `BaseAgent` stage — `SendStage` — at the tail of the pipeline (immediately after `FinalizeStage`) that sends Gmail replies for two kinds of output: customer confirmation emails on AUTO_APPROVE orders (drafted by `ConfirmStage`) and clarification emails on CLARIFY exceptions (drafted by `ClarifyStage`). Replies are threaded via RFC 5322 `In-Reply-To` + `References` headers — Gmail auto-threads when the authed inbox is the recipient of the original. Idempotency + observability via `sent_at` + `send_error` fields on `OrderRecord` + `ExceptionRecord`. Fail-open on send errors. Dry-run via `GMAIL_SEND_DRY_RUN=1` env var. `AGENT_VERSION` bumps `track-a-v0.2` → `track-a-v0.3`.

This closes the Gmail-send leg of Glacis `Email-Ingestion.md` §9. Generator-Judge quality gate (Track B) wraps the actual send call as a post-MVP layer.

## Context

- `ConfirmStage` (Track A close-out, 6344a83 / f5db946) already drafts a confirmation body for every AUTO_APPROVE order and writes it via `OrderStore.update_with_confirmation`. Nothing sends it.
- `ClarifyStage` (Track A Step 4f, b33a030) already drafts a clarify body for every CLARIFY exception and the coordinator persists it as `ExceptionRecord.clarify_body`. Nothing sends it.
- Track A1 gave us `GmailClient` (list_unprocessed / get_raw / label_id_for / apply_label) with `gmail.modify` scope only. A2 extends the client with `send_message(...)` + adds `gmail.send` to a new `A2_SCOPES` list.
- Gmail API's `users.messages.send` accepts a `raw` base64url-encoded RFC 822 message. Server-side auto-threading works when `In-Reply-To` + `References` headers point at a message already in the authed inbox's view. No explicit `threadId` parameter required.
- Pipeline state after PersistStage carries `state["process_results"]` — a flat list of `{filename, sub_doc_index, result: ProcessResult}`. `ProcessResult.kind ∈ {"order", "exception", "duplicate"}` + `.order: Optional[OrderRecord]` + `.exception: Optional[ExceptionRecord]`.
- `IntakeCoordinator` has idempotency on `source_message_id` at both `orders` + `exceptions` levels — re-processing the same envelope returns the existing record, so `sent_at` landed on the first run is visible on the retry.
- `AGENT_VERSION` currently `"track-a-v0.2"` (set after Track A's ConfirmStage). A2 bumps to `"track-a-v0.3"` so Firestore analytics can distinguish pre/post-egress rows.

## Architectural decisions

The five foundational calls, each with trade-offs explicitly considered and rejected alternatives documented.

### Decision 1 — Trigger point: new `SendStage` at position #10

A new `BaseAgent` stage runs after `FinalizeStage`. It walks `state["process_results"]` and per entry decides whether to send. Subclass of `AuditedStage` (Track D) so mixin entry/exit + per-send lifecycle emits happen uniformly with every other stage.

**Rejected:**
- **Post-pipeline hook inside `GmailPoller._process_one`** — decouples pipeline from send but the poller then has to understand the state schema + emit audit events manually + pipeline tests don't cover the send path.
- **Inline in ConfirmStage + ClarifyStage** — couples drafting (LLM generation) with external I/O (send); existing stages become harder to unit-test; "drafted but didn't send" case becomes ambiguous.
- **Decoupled Firestore-triggered consumer** — architectural overkill for single-process polling MVP.

### Decision 2 — Threading: RFC 5322 `In-Reply-To` + `References` headers; no explicit Gmail `threadId`

Gmail server-side threading groups replies when the outgoing message's `In-Reply-To` header matches a Message-ID already present in the authed inbox. We set:

- `In-Reply-To: <original-envelope.message_id>`
- `References: <original envelope.references joined> <original-envelope.message_id>` (append the original's Message-ID to the prior References chain)

No Gmail-internal `threadId` is needed. We never persist Gmail's internal `threadId` anywhere — RFC 5322 Message-IDs are the portable thread identifier the codebase already uses everywhere (`EmailEnvelope.message_id`, `EmailEnvelope.in_reply_to`, `ExceptionStore.find_pending_clarify(thread_id)`).

**Rejected:**
- **Explicit `threadId` in `messages.send` payload** — requires persisting Gmail's internal id during A1 ingestion; A1 doesn't, and A3 (push-based ingestion) also doesn't; we'd have to round-trip query Gmail for the threadId per send.
- **No threading** (fresh message per reply) — looks broken in Gmail UI; defeats the demo narrative.

### Decision 3 — Idempotency: `sent_at` + `send_error` on each record

`OrderRecord` schema v3 → v4, `ExceptionRecord` schema v2 → v3. Both gain:

- `sent_at: Optional[datetime] = None` — `None` when not sent (or send failed); `datetime` when successful
- `send_error: Optional[str] = None` — captures the error class + message when `sent_at` is `None` after a failed attempt

SendStage's per-entry guard: `if record.sent_at is not None: skip`. Retries see the already-landed `sent_at` and short-circuit. Failures leave `sent_at=None` + populate `send_error` for next-run retry visibility.

**Rejected:**
- **Separate `sent_messages` collection** — disconnects send state from the record it describes; forces join for dashboard.
- **Audit-log-only observability** — muddles "what is the current state?" with "what happened historically?"; adds an audit query per candidate send.

### Decision 4 — Failure handling: fail-open + record error

SendStage catches `Exception` in a per-entry try/except. Successful send: update `sent_at=now` + `send_error=None`. Failed send: log ERROR, emit audit `email_send_failed`, call `update_with_send_receipt(sent_at=None, send_error=<ClassName: message>)`. Stage continues to the next entry. Pipeline completes normally.

At-least-once guarantee. If Gmail accepted the send but our acknowledgment path failed → `sent_at` stays None → next retry re-sends → customer gets two emails. MVP-acceptable; at-most-once requires provided-Message-ID + Gmail de-dup (Phase 3).

**Rejected:**
- **Fail-closed** (raise → ADK retries entire run) — one Gmail blip crashes the whole pipeline.
- **Retry with exponential backoff inside SendStage** — implicit retry complicates the audit trace; manual retry via operator is sufficient for MVP.
- **Hybrid (fail-closed on critical paths, fail-open on advisory sends)** — all our sends are equally important; no principled way to split.

### Decision 5 — Dry-run via `GMAIL_SEND_DRY_RUN` env var

`send_dry_run: bool` kwarg on `build_root_agent`, wired from `os.environ.get("GMAIL_SEND_DRY_RUN") == "1"` in `_build_default_root_agent`. When true, SendStage logs `"dry_run: would send to <recipient> subject <subj>"` + emits audit `email_send_dry_run` + does NOT call `gmail_client.send_message` and does NOT update `sent_at`. Dry-run is a pure observation mode that leaves persisted state unchanged — the next real (non-dry-run) run will send normally.

**Rejected:**
- **No dry-run / "just don't run the poller in dev"** — precludes local end-to-end testing through SendStage.
- **Dry-run via gmail_client=None** — conflates "no Gmail configured" (offline fixture run) with "configured but don't send" (dev mode); the latter wants full MIME construction + logging for audit purposes, which gmail_client=None disables entirely.

## Components

### Modified — `backend/gmail/scopes.py`

```python
GMAIL_MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"

A1_SCOPES = [GMAIL_MODIFY_SCOPE]
A2_SCOPES = A1_SCOPES + [GMAIL_SEND_SCOPE]

__all__ = [
    "GMAIL_MODIFY_SCOPE",
    "GMAIL_SEND_SCOPE",
    "A1_SCOPES",
    "A2_SCOPES",
]
```

### Modified — `backend/gmail/client.py`

Add `send_message` method:

```python
import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import make_msgid, formatdate
from typing import Optional

# ... inside GmailClient class ...

def send_message(
    self,
    *,
    to: str,
    subject: str,
    body_text: str,
    in_reply_to: Optional[str] = None,
    references: Optional[list[str]] = None,
) -> str:
    """Send a plain-text email via users.messages.send.

    Constructs RFC 5322 MIME with thread-reply headers. Auto-prepends
    "Re: " to subject when not already present. Returns the sent
    Gmail message id.

    in_reply_to: RFC 5322 Message-ID of the original message, sans
                 angle brackets OR with; will be wrapped consistently.
    references: ordered list of Message-IDs forming the thread chain.
                Typically = <original.references> + [original.message_id].
    """
    msg = MIMEMultipart()
    msg["To"] = to
    msg["From"] = "me"  # Gmail resolves to authed user server-side
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()

    def _bracket(m: str) -> str:
        m = m.strip()
        if m.startswith("<") and m.endswith(">"):
            return m
        return f"<{m}>"

    if in_reply_to:
        msg["In-Reply-To"] = _bracket(in_reply_to)
    if references:
        msg["References"] = " ".join(_bracket(r) for r in references)

    msg.attach(MIMEText(body_text, "plain"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    resp = (
        self._service.users()
        .messages()
        .send(userId="me", body={"raw": raw})
        .execute()
    )
    return resp["id"]
```

### New file — `backend/my_agent/stages/send.py`

```python
"""SendStage — 10th BaseAgent, sends Gmail replies for confirm + clarify bodies.

Position: immediately after FinalizeStage. Walks state["process_results"]
produced by PersistStage, filters entries that have a draft body + haven't
been sent (sent_at is None), calls GmailClient.send_message, records
sent_at via the appropriate store. Fail-open per entry.

Spec: docs/superpowers/specs/2026-04-24-track-a2-gmail-egress-design.md
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Final, Optional

from pydantic import PrivateAttr

from backend.gmail.client import GmailClient
from backend.my_agent.stages._audited import AuditedStage  # Track D mixin
from backend.persistence.base import ExceptionStore, OrderStore
from backend.utils.logging import get_logger

_log = get_logger(__name__)

SEND_STAGE_NAME: Final[str] = "SendStage"


class SendStage(AuditedStage):
    name: str = SEND_STAGE_NAME

    _gmail_client: Optional[Any] = PrivateAttr()
    _order_store: Any = PrivateAttr()
    _exception_store: Any = PrivateAttr()
    _dry_run: bool = PrivateAttr()

    def __init__(
        self,
        *,
        gmail_client: Optional[GmailClient],
        order_store: OrderStore,
        exception_store: ExceptionStore,
        dry_run: bool,
        audit_logger: Any,
    ) -> None:
        super().__init__(audit_logger=audit_logger)
        self._gmail_client = gmail_client
        self._order_store = order_store
        self._exception_store = exception_store
        self._dry_run = dry_run

    async def _audited_run(self, ctx):
        state = ctx.session.state

        if state.get("reply_handled"):
            return

        if self._gmail_client is None:
            _log.info("send_stage_disabled", reason="no_gmail_client")
            return

        process_results = state.get("process_results", [])
        envelope = state.get("envelope") or {}
        original_message_id = envelope.get("message_id")
        original_references = envelope.get("references") or []
        original_subject = envelope.get("subject") or ""

        references_chain = list(original_references)
        if original_message_id:
            references_chain.append(original_message_id)

        for entry in process_results:
            result = entry.get("result") or {}
            kind = result.get("kind")

            if kind == "order":
                await self._maybe_send_confirmation(
                    ctx=ctx,
                    order=result.get("order"),
                    original_message_id=original_message_id,
                    references=references_chain,
                    original_subject=original_subject,
                )
            elif kind == "exception":
                await self._maybe_send_clarify(
                    ctx=ctx,
                    exception=result.get("exception"),
                    original_message_id=original_message_id,
                    references=references_chain,
                    original_subject=original_subject,
                )
            # "duplicate" kind: nothing to send (body was drafted on prior run + already sent then)
```

(Method bodies `_maybe_send_confirmation` and `_maybe_send_clarify` are symmetric; one implementation shown below, the other identical with `clarify_body` + `exception_store`.)

```python
async def _maybe_send_confirmation(
    self,
    *,
    ctx,
    order: Optional[dict[str, Any]],
    original_message_id: Optional[str],
    references: list[str],
    original_subject: str,
) -> None:
    if order is None:
        return
    body = order.get("confirmation_body")
    if not body:
        await self._emit_skipped(ctx, "no_body", order)
        return
    if order.get("sent_at") is not None:
        await self._emit_skipped(ctx, "already_sent", order)
        return
    recipient = ((order.get("customer") or {}).get("contact_email")) or ""
    if not recipient:
        await self._emit_failure(ctx, order, error="no_recipient")
        await self._record_failure(
            order["source_message_id"],
            self._order_store,
            "no_recipient",
        )
        return

    if self._dry_run:
        _log.info(
            "send_dry_run",
            order_id=order["source_message_id"],
            to=recipient,
            subject=original_subject,
        )
        await self._emit_dry_run(ctx, order, recipient)
        return

    try:
        gmail_id = await asyncio.to_thread(
            self._gmail_client.send_message,
            to=recipient,
            subject=original_subject or "Your order confirmation",
            body_text=body,
            in_reply_to=original_message_id,
            references=references,
        )
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        _log.error(
            "send_failed",
            order_id=order["source_message_id"],
            error=reason,
        )
        await self._record_failure(
            order["source_message_id"],
            self._order_store,
            reason,
        )
        await self._emit_failure(ctx, order, error=reason)
        return

    await self._order_store.update_with_send_receipt(
        source_message_id=order["source_message_id"],
        sent_at=datetime.now(timezone.utc),
        send_error=None,
    )
    await self._emit_success(ctx, order, gmail_id=gmail_id)


async def _maybe_send_clarify(self, *, ctx, exception, original_message_id, references, original_subject):
    # Symmetric: uses exception.get("clarify_body") and self._exception_store. Body repeats for clarity
    # rather than refactoring into a shared helper — keeps the send semantics per-record-kind readable.
    ...  # (implementation mirrors _maybe_send_confirmation)


async def _record_failure(self, source_message_id, store, reason):
    try:
        await store.update_with_send_receipt(
            source_message_id=source_message_id,
            sent_at=None,
            send_error=reason,
        )
    except Exception as exc:
        _log.error("send_receipt_write_failed", error=str(exc))


async def _emit_success(self, ctx, record, *, gmail_id):
    await self._audit_logger.emit(
        correlation_id=ctx.session.state.get("correlation_id", ""),
        session_id=ctx.session.id,
        source_message_id=record["source_message_id"],
        stage="lifecycle",
        phase="lifecycle",
        action="email_sent",
        outcome="ok",
        payload={"gmail_message_id": gmail_id, "record_id": record["source_message_id"]},
    )


async def _emit_failure(self, ctx, record, *, error):
    await self._audit_logger.emit(
        correlation_id=ctx.session.state.get("correlation_id", ""),
        session_id=ctx.session.id,
        source_message_id=record["source_message_id"],
        stage="lifecycle",
        phase="lifecycle",
        action="email_send_failed",
        outcome="error",
        payload={"record_id": record["source_message_id"], "error": error},
    )


async def _emit_dry_run(self, ctx, record, recipient):
    await self._audit_logger.emit(
        correlation_id=ctx.session.state.get("correlation_id", ""),
        session_id=ctx.session.id,
        source_message_id=record["source_message_id"],
        stage="lifecycle",
        phase="lifecycle",
        action="email_send_dry_run",
        outcome="ok",
        payload={"record_id": record["source_message_id"], "would_send_to": recipient},
    )


async def _emit_skipped(self, ctx, reason, record):
    await self._audit_logger.emit(
        correlation_id=ctx.session.state.get("correlation_id", ""),
        session_id=ctx.session.id,
        source_message_id=record["source_message_id"],
        stage="lifecycle",
        phase="lifecycle",
        action="email_send_skipped",
        outcome="skip",
        payload={"record_id": record["source_message_id"], "reason": reason},
    )


__all__ = ["SendStage", "SEND_STAGE_NAME"]
```

### Modified — `backend/models/order_record.py`

Schema v3 → v4 (assumes Track C has landed; otherwise v2 → v3). Add:

```python
sent_at: Optional[datetime] = None
send_error: Optional[str] = None
schema_version: int = 4  # was 3
```

### Modified — `backend/models/exception_record.py`

Schema v2 → v3. Add:

```python
sent_at: Optional[datetime] = None
send_error: Optional[str] = None
schema_version: int = 3  # was 2
```

### Modified — `backend/persistence/base.py`

Extend both Protocols:

```python
class OrderStore(Protocol):
    # ... existing methods ...
    async def update_with_send_receipt(
        self,
        *,
        source_message_id: str,
        sent_at: Optional[datetime],
        send_error: Optional[str],
    ) -> None: ...


class ExceptionStore(Protocol):
    # ... existing methods ...
    async def update_with_send_receipt(
        self,
        *,
        source_message_id: str,
        sent_at: Optional[datetime],
        send_error: Optional[str],
    ) -> None: ...
```

### Modified — `backend/persistence/orders_store.py` + `backend/persistence/exceptions_store.py`

```python
async def update_with_send_receipt(
    self,
    *,
    source_message_id: str,
    sent_at: Optional[datetime],
    send_error: Optional[str],
) -> None:
    doc_ref = self._client.collection(self._collection).document(source_message_id)
    await doc_ref.update({"sent_at": sent_at, "send_error": send_error})
```

Raises `google.api_core.exceptions.NotFound` when the doc is missing. Callers always invoke post-save; NotFound indicates a pipeline invariant violation.

### Modified — `backend/my_agent/agent.py`

- Add `gmail_client: Optional[GmailClient] = None` + `send_dry_run: bool = False` to `build_root_agent`.
- Append `SendStage(...)` as the 10th sub-agent.
- `AGENT_VERSION` → `"track-a-v0.3"`.
- `_build_default_root_agent` reads `GMAIL_SEND_DRY_RUN` env var; constructs `gmail_client` only when all three `GMAIL_*` env vars are set, else passes `None`.

### Modified — `scripts/gmail_poll.py`

- `scopes=A2_SCOPES` (not `A1_SCOPES`) when constructing `GmailClient`.
- Pass `gmail_client` + `send_dry_run` into `build_root_agent` via the new kwargs.

### Modified — `scripts/gmail_auth_init.py`

Default scopes argument switches from `A1_SCOPES` → `A2_SCOPES`. Print a note: "Scopes requested: gmail.modify + gmail.send (Track A1 + A2)".

### Modified — `.env.example`

```
# Track A2: send mode
GMAIL_SEND_DRY_RUN=1  # set to 0 or unset to actually send
```

### Modified — `backend/my_agent/README.md`

Add "Sending (Track A2)" subsection under "Gmail ingress":

- Re-run `gmail_auth_init.py` to regenerate refresh token with `gmail.send` scope
- Paste the new `GMAIL_REFRESH_TOKEN` into `.env`
- Default `GMAIL_SEND_DRY_RUN=1` — poller runs pipeline end-to-end but logs "would send" instead of sending
- Set `GMAIL_SEND_DRY_RUN=0` to flip into real-send mode
- Verify sends land in Gmail's "Sent" folder of the authed account, threaded under the original message

## Data flow

### AUTO_APPROVE happy path (not dry-run)

```
IngestStage … PersistStage (coordinator writes OrderRecord with
                            confirmation_body + sent_at=None + schema_version=4)
  → FinalizeStage (run_summary emitted)
  → SendStage
      state["process_results"] = [{"kind":"order", "order":{..., confirmation_body:<text>, sent_at:null}}]
      for entry:
        kind == "order" → _maybe_send_confirmation
          body present ✓, sent_at None ✓, contact_email resolvable ✓
          dry_run False
          gmail_client.send_message(
              to=order.customer.contact_email,
              subject="Re: <original subject>",
              body_text=order.confirmation_body,
              in_reply_to=envelope.message_id,
              references=[...envelope.references, envelope.message_id],
          ) → gmail_id="17abc..."
          order_store.update_with_send_receipt(source_message_id, sent_at=now, send_error=None)
          audit email_sent {gmail_message_id: "17abc...", record_id: ...}
```

### CLARIFY path

Identical shape using `exception.clarify_body` + `exception_store`. Subject prepends `"Re: "` to `envelope.subject`.

### Retry after successful send

```
Runner.run_async same envelope
  → IntakeCoordinator.process sees source_message_id collision → returns
    ProcessResult with the EXISTING OrderRecord (sent_at != None from first run)
  → SendStage iterates process_results
      sent_at != None → skip; emit email_send_skipped outcome=skip reason=already_sent
```

No second send. Audit log shows the attempted run.

### Dry-run

```
SendStage _audited_run
  self._dry_run=True
  for each entry with body:
    log "send_dry_run" with recipient + subject
    emit email_send_dry_run (audit)
    do NOT call gmail_client.send_message
    do NOT update sent_at
```

Next non-dry-run run picks up where dry-run left off (sent_at is still None).

### Failure path

```
gmail_client.send_message raises RuntimeError("quota exceeded")
  except catches → reason = "RuntimeError: quota exceeded"
  order_store.update_with_send_receipt(sent_at=None, send_error=reason)
  emit email_send_failed payload={error: reason}
  continue to next entry
Pipeline completes normally.
```

Operator sees `send_error` field + audit event. Next pipeline invocation of the same envelope re-attempts (sent_at still None; guard not triggered).

### `gmail_client is None` (offline / fixture / integration test without Gmail)

```
SendStage _audited_run:
  self._gmail_client is None → log once at INFO + return
  no state mutation, no audit events beyond the mixin's stage_entered / stage_exited
```

All existing fixture + integration tests continue to work by passing `gmail_client=None` into `build_root_agent`.

## Error handling

| Scenario | Behavior |
|---|---|
| Gmail `messages.send` 5xx / network | Caught per-entry; `send_error` populated; audit `email_send_failed`; stage continues. |
| OAuth 401 / `RefreshError` at send time | Same catch path; operator re-runs `gmail_auth_init.py` with A2 scopes. |
| Missing `contact_email` on customer snapshot | `send_error="no_recipient"`; no Gmail call; audit `email_send_failed outcome=error`. |
| Missing `clarify_body` or `confirmation_body` | Treated as "no body" → skip + audit `email_send_skipped reason=no_body`. Not an error — some paths legitimately don't draft bodies (e.g., ESCALATE exceptions). |
| `sent_at != None` (retry) | Skip + audit `email_send_skipped reason=already_sent`. |
| `dry_run=True` | Log + audit only; no network call; `sent_at` untouched. |
| `gmail_client is None` | Whole stage no-ops (single INFO log). All fixture / integration / offline runs. |
| `update_with_send_receipt` raises after successful Gmail send | Customer got the email; our record says `sent_at=None`; next retry re-sends → customer gets it twice. **Accepted MVP tradeoff** — at-least-once guarantee. Log ERROR prominently + audit `send_receipt_write_failed`. Phase 3 uses provided Message-ID + Gmail server-side dedup. |
| Send succeeds, store write NotFound | Same as above — log, stage continues. The NotFound indicates the source_message_id doesn't match a known doc; this is an invariant violation (shouldn't happen post-PersistStage). Log loudly. |
| `reply_handled=True` in state | Stage early-exits (reply path does no outbound sends). |

### Logging

All structured via `backend.utils.logging`:
- `send_stage_disabled` — `gmail_client is None`
- `send_dry_run` — per candidate entry
- `send_succeeded` — per successful send (alongside audit)
- `send_failed` — per failed send (alongside audit)
- `send_receipt_write_failed` — the rare post-send store-update failure

## Testing

### Unit — new `tests/unit/test_gmail_send.py` (5 tests)

All patch `googleapiclient.discovery.build` to return a `MagicMock`.

1. `send_message` passes to/subject/body/references as MIME headers — inspect the MIME bytes decoded from `raw`
2. `send_message` auto-prepends `"Re: "` when subject doesn't start with `Re:`
3. `send_message` keeps subject as-is when already `"Re: ..."`
4. `send_message` returns the `id` from the API response
5. `send_message` without `in_reply_to` / `references` still constructs valid MIME

### Unit — new `tests/unit/test_stage_send.py` (9 tests)

`AsyncMock(spec=OrderStore)` + `AsyncMock(spec=ExceptionStore)` + `MagicMock(spec=GmailClient)` + `AsyncMock(spec=AuditLogger)` (Track D).

1. Stage no-ops when `gmail_client=None` — no store calls, no audit emits beyond mixin
2. Stage no-ops when `state["reply_handled"]=True`
3. AUTO order, body present, `sent_at=None` → `send_message` + `update_with_send_receipt(sent_at != None)`
4. AUTO order, `sent_at != None` (retry) → no `send_message`, audit `email_send_skipped reason=already_sent`
5. CLARIFY exception, `clarify_body` present → `send_message` + `exception_store.update_with_send_receipt`
6. ESCALATE / no-body exception → no `send_message`, audit `email_send_skipped reason=no_body`
7. Dry-run mode → no `send_message`, no store update, audit `email_send_dry_run`
8. Gmail error → catches, `update_with_send_receipt(sent_at=None, send_error=<str>)`, audit `email_send_failed`, next entry still processes
9. Customer lacks `contact_email` → `update_with_send_receipt(sent_at=None, send_error="no_recipient")`, no `send_message` call

### Unit — extend `tests/unit/test_order_store.py` (+2 tests)

1. `update_with_send_receipt` writes `sent_at` + `send_error` via field-mask update
2. Round-trip: save → update_with_send_receipt → get → fields match

### Unit — extend `tests/unit/test_exception_store.py` (+2 tests)

Symmetric to `test_order_store.py`.

### Unit — extend schema tests (+2 total, one per record)

- `OrderRecord.schema_version == 4`, `sent_at` optional datetime, `send_error` optional str
- `ExceptionRecord.schema_version == 3`, same fields

### Unit — extend `tests/unit/test_orchestrator_build.py` (+3 tests)

1. `build_root_agent` accepts `gmail_client` + `send_dry_run` kwargs
2. Assembled root agent has 10 sub-agents; index 9 is `SendStage`
3. Canonical-order test updated to expect the 10-stage order (ingest → reply → classify → parse → validate → clarify → persist → confirm → finalize → **send**)

### Integration — extend `tests/integration/test_orchestrator_emulator.py` (+1 test)

- End-to-end AUTO_APPROVE via `Runner.run_async` against emulator with `gmail_client=MagicMock(spec=GmailClient)` + `send_dry_run=False`. Verify:
  - `gmail_client.send_message` called exactly once
  - `order_store.update_with_send_receipt` called with matching `sent_at != None`
  - Persisted `OrderRecord.sent_at` populated in Firestore
  - `AGENT_VERSION` on the record is `"track-a-v0.3"`

### Total test delta

- New unit: 5 + 9 + 2 + 2 + 2 + 3 = **23**
- New integration: **1**
- Baseline after C + D + A1 ≈ 367 → ~390 unit + ~15 integration

## Out of scope

- **Retry with exponential backoff** — fail-open with manual retry on next pipeline run is MVP-adequate.
- **HTML-formatted body** — plain text only. Prompt templates produce text.
- **Attachments on outbound mail** — no PDF receipts.
- **Delivery-status webhook / bounce handling** — Phase 3.
- **Per-customer email templates** — LLM already interpolates per-customer context via the prompt. Phase 3 adds per-customer SOP overrides.
- **At-most-once guarantee** — at-least-once is MVP. Provided-Message-ID + Gmail server dedup is Phase 3.
- **Rate limiting** — Gmail's 250 quota-unit/sec limit is orders of magnitude above single-inbox demo cadence.
- **Unsubscribe headers** — transactional email, not marketing.
- **Localization / multi-language** — English only.
- **Generator-Judge quality gate** — Track B wraps SendStage's send call.

## Success criteria

1. Operator re-runs `gmail_auth_init.py` once → new refresh token with `gmail.modify` + `gmail.send` scopes → pastes into `.env`.
2. Poller running with `GMAIL_SEND_DRY_RUN=0` + a fixture-equivalent email to the agent's inbox → AUTO_APPROVE pipeline completes → customer confirmation email arrives in operator's Gmail "Sent" folder, threaded under the original.
3. Resending the same original (pipeline retry) → dedup or `sent_at` guard → no second confirmation.
4. `GMAIL_SEND_DRY_RUN=1` → pipeline completes → logs show "would send to X" → no real email leaves → `sent_at` stays None.
5. Simulated Gmail failure (e.g., invalid recipient) → pipeline completes → `OrderRecord.send_error` populated + audit `email_send_failed` → next pipeline run of the same envelope re-attempts.
6. `AGENT_VERSION == "track-a-v0.3"` on all new orders written post-deployment.
7. No regression in the existing unit / integration test suite.

## Files touched (summary)

| Type | Path | Change |
|---|---|---|
| Modified | `backend/gmail/scopes.py` | +`GMAIL_SEND_SCOPE`, +`A2_SCOPES` |
| Modified | `backend/gmail/client.py` | +`send_message(to, subject, body_text, in_reply_to, references) → gmail_id` |
| New | `backend/my_agent/stages/send.py` | `SendStage(AuditedStage)` |
| Modified | `backend/models/order_record.py` | schema v3→v4 + `sent_at` + `send_error` |
| Modified | `backend/models/exception_record.py` | schema v2→v3 + `sent_at` + `send_error` |
| Modified | `backend/persistence/base.py` | +`update_with_send_receipt` on both Protocols |
| Modified | `backend/persistence/orders_store.py` | impl |
| Modified | `backend/persistence/exceptions_store.py` | impl |
| Modified | `backend/my_agent/agent.py` | +`gmail_client`, +`send_dry_run` kwargs; 9→10 stages; `AGENT_VERSION v0.2→v0.3` |
| Modified | `scripts/gmail_poll.py` | `scopes=A2_SCOPES` + thread new kwargs through |
| Modified | `scripts/gmail_auth_init.py` | default scopes `A1_SCOPES → A2_SCOPES` |
| Modified | `.env.example` | +`GMAIL_SEND_DRY_RUN=1` |
| New | `tests/unit/test_gmail_send.py` | 5 tests |
| New | `tests/unit/test_stage_send.py` | 9 tests |
| Modified | `tests/unit/test_order_store.py` | +2 tests |
| Modified | `tests/unit/test_exception_store.py` | +2 tests |
| Modified | `tests/unit/test_order_record_schema.py` (or add) | +1 test |
| Modified | `tests/unit/test_exception_record_schema.py` (or add) | +1 test |
| Modified | `tests/unit/test_orchestrator_build.py` | +3 tests |
| Modified | `tests/integration/test_orchestrator_emulator.py` | +1 test |
| Modified | `research/Order-Intake-Sprint-Status.md` | §9 row flip + Built inventory |
| Modified | `Glacis-Order-Intake.md` | §9 `Gmail API send integration` [Post-MVP] → [MVP ✓] |
| Modified | `backend/my_agent/README.md` | +"Sending (Track A2)" section |

## Connections

- Track A1 (`docs/superpowers/specs/2026-04-24-track-a1-gmail-ingress-design.md`, 9ddbf27): extends `GmailClient` + `scopes.py`; A1 continues to work unchanged.
- Track C (`docs/superpowers/specs/2026-04-24-track-c-duplicate-detection-design.md`, c978942/cbcf7ce): complementary — duplicates route to ESCALATE which has no body, so SendStage naturally skips them.
- Track D (`docs/superpowers/specs/2026-04-24-track-d-audit-log-design.md`, 510559d): `SendStage` subclasses `AuditedStage`. Audit events `email_sent`, `email_send_failed`, `email_send_dry_run`, `email_send_skipped` emitted uniformly.
- Track A3 (future): push-based ingestion replaces the polling loop; SendStage itself is unaffected — same 10-stage pipeline, same tail.
- Track B (Generator-Judge): wraps SendStage's `gmail_client.send_message` call with a pre-flight judge pass. Expected shape: a `JudgeService.evaluate(body)` call inside `_maybe_send_confirmation` / `_maybe_send_clarify` between the `sent_at`-guard and the `send_message` call. Fail-closed on judge failure → treated as `send_error="judge_rejected: <reason>"`.
- `Glacis-Order-Intake.md` §9 "Gmail API send integration" bullet flips `[Post-MVP]` → `[MVP ✓]`. §9 "Gemini quality-gate check on outbound email" stays `[Post-MVP]` tagged for Track B.
