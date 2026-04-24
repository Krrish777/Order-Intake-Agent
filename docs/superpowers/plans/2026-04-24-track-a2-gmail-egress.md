# Track A2 — Gmail Egress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `SendStage` (10th BaseAgent) that sends Gmail replies for AUTO_APPROVE confirmation bodies and CLARIFY clarification bodies. Thread via RFC 5322 `In-Reply-To` + `References` headers. Idempotency + observability via `sent_at` + `send_error` fields on `OrderRecord` (schema v3→v4) + `ExceptionRecord` (schema v2→v3). Fail-open on send errors. Dry-run via `GMAIL_SEND_DRY_RUN=1`. `AGENT_VERSION` bumps `track-a-v0.2` → `track-a-v0.3`.

**Architecture:** Extends `GmailClient` with `send_message(...)` + the scopes module with `A2_SCOPES = A1_SCOPES + [gmail.send]`. New `SendStage(AuditedStage)` walks `state["process_results"]` and per record calls the Gmail API + updates `sent_at` via new `update_with_send_receipt` store methods. All pipeline-level plumbing lives in `build_root_agent`.

**Tech Stack:** Python 3.13, `google-api-python-client`, Python stdlib `email.mime.*` + `email.utils` for MIME construction, Pydantic 2.x for schema bumps, `google-cloud-firestore` 2.27.0 (async) for store updates, pytest + pytest-asyncio, `AsyncMock` / `MagicMock` for `GmailClient` / `Runner` / stores.

**Source spec:** `docs/superpowers/specs/2026-04-24-track-a2-gmail-egress-design.md` (rev `0780025`).

---

## File structure

| Path | Responsibility |
|---|---|
| **Modified** `backend/gmail/scopes.py` | +`GMAIL_SEND_SCOPE`, +`A2_SCOPES` |
| **Modified** `backend/gmail/client.py` | +`send_message(to, subject, body_text, in_reply_to, references) → gmail_id` |
| **Modified** `backend/models/order_record.py` | schema v3→v4, +`sent_at`, +`send_error` |
| **Modified** `backend/models/exception_record.py` | schema v2→v3, +`sent_at`, +`send_error` |
| **Modified** `backend/persistence/base.py` | +`update_with_send_receipt` on both Protocols |
| **Modified** `backend/persistence/orders_store.py` | impl |
| **Modified** `backend/persistence/exceptions_store.py` | impl |
| **New** `backend/my_agent/stages/send.py` | `SendStage(AuditedStage)` |
| **Modified** `backend/my_agent/agent.py` | +`gmail_client`, +`send_dry_run` kwargs; 9→10 stages; `AGENT_VERSION v0.2→v0.3` |
| **Modified** `scripts/gmail_poll.py` | `scopes=A2_SCOPES` + thread new kwargs |
| **Modified** `scripts/gmail_auth_init.py` | default `A1_SCOPES → A2_SCOPES` |
| **Modified** `.env.example` | +`GMAIL_SEND_DRY_RUN=1` |
| **New** `tests/unit/test_gmail_send.py` | 5 tests |
| **New** `tests/unit/test_stage_send.py` | 9 tests |
| **Modified** `tests/unit/test_order_store.py` | +2 tests |
| **Modified** `tests/unit/test_exception_store.py` | +2 tests |
| **Modified** `tests/unit/test_order_record_schema.py` (or schema block in test_order_store.py) | +1 test (v4 + new fields) |
| **Modified** `tests/unit/test_exception_record_schema.py` (or block in test_exception_store.py) | +1 test (v3 + new fields) |
| **Modified** `tests/unit/test_orchestrator_build.py` | +3 tests |
| **Modified** `tests/integration/test_orchestrator_emulator.py` | +1 test |
| **Modified** `research/Order-Intake-Sprint-Status.md` | §9 row flip + Built inventory |
| **Modified** `Glacis-Order-Intake.md` | §9 Gmail send flip |
| **Modified** `backend/my_agent/README.md` | +"Sending (A2)" section |

---

## Task 1: Add `GMAIL_SEND_SCOPE` + `A2_SCOPES` constants

**Files:**
- Modify: `backend/gmail/scopes.py`
- Modify: `tests/unit/test_gmail_auth.py`

- [ ] **Step 1.1: Write the failing tests**

Append to `tests/unit/test_gmail_auth.py`:

```python
def test_gmail_send_scope_is_the_official_uri():
    from backend.gmail.scopes import GMAIL_SEND_SCOPE

    assert GMAIL_SEND_SCOPE == "https://www.googleapis.com/auth/gmail.send"


def test_a2_scopes_extends_a1_with_send():
    from backend.gmail.scopes import A1_SCOPES, A2_SCOPES, GMAIL_SEND_SCOPE

    assert A2_SCOPES == A1_SCOPES + [GMAIL_SEND_SCOPE]
    assert len(A2_SCOPES) == len(A1_SCOPES) + 1
```

- [ ] **Step 1.2: Run — expect failure**

Run: `uv run pytest tests/unit/test_gmail_auth.py -v`

Expected: `ImportError` on `GMAIL_SEND_SCOPE` / `A2_SCOPES`.

- [ ] **Step 1.3: Update `backend/gmail/scopes.py`**

Replace file contents:

```python
"""OAuth scopes for the Gmail-ingestion tracks.

A1 (ingress):  gmail.modify — read inbox + apply labels
A2 (egress):   + gmail.send — send messages
A3 (deploy):   no additional scope — watch uses the same subset
"""

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

- [ ] **Step 1.4: Run tests — expect 4 passes** (2 existing + 2 new)

Run: `uv run pytest tests/unit/test_gmail_auth.py -v`

Expected: `4 passed`.

- [ ] **Step 1.5: Commit**

```bash
git add backend/gmail/scopes.py tests/unit/test_gmail_auth.py
git commit -m "feat(track-a2): add GMAIL_SEND_SCOPE + A2_SCOPES"
```

---

## Task 2: `GmailClient.send_message` method

**Files:**
- Modify: `backend/gmail/client.py`
- Create: `tests/unit/test_gmail_send.py`

- [ ] **Step 2.1: Write the failing tests**

Create `tests/unit/test_gmail_send.py`:

```python
"""Unit tests for GmailClient.send_message.

All tests patch googleapiclient.discovery.build to return a
MagicMock Resource — no network.

Spec: docs/superpowers/specs/2026-04-24-track-a2-gmail-egress-design.md
"""
from __future__ import annotations

import base64
from email import message_from_bytes
from unittest.mock import MagicMock, patch


def _make_client():
    from backend.gmail.client import GmailClient

    patcher = patch("backend.gmail.client.build")
    mock_build = patcher.start()
    mock_service = MagicMock()
    mock_build.return_value = mock_service

    client = GmailClient(
        refresh_token="rt-abc",
        client_id="cid-123",
        client_secret="sec-456",
        scopes=[
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/gmail.send",
        ],
    )
    return client, mock_service, patcher


def _teardown(patcher):
    patcher.stop()


def _decode_sent_mime(svc):
    """Pull the `raw` field out of the last send(...) call and parse MIME."""
    send_call = svc.users().messages().send.call_args
    raw_b64 = send_call.kwargs["body"]["raw"]
    raw_bytes = base64.urlsafe_b64decode(raw_b64.encode("ascii"))
    return message_from_bytes(raw_bytes)


class TestGmailClientSendMessage:
    def test_send_message_sets_headers_correctly(self):
        client, svc, patcher = _make_client()
        try:
            svc.users().messages().send().execute.return_value = {"id": "gmail-1"}

            gmail_id = client.send_message(
                to="customer@example.com",
                subject="Re: Order confirmation",
                body_text="Thank you for your order.",
                in_reply_to="<orig-msg@mailer>",
                references=["<root-msg@mailer>", "<orig-msg@mailer>"],
            )

            assert gmail_id == "gmail-1"
            mime = _decode_sent_mime(svc)
            assert mime["To"] == "customer@example.com"
            assert mime["Subject"] == "Re: Order confirmation"
            assert mime["In-Reply-To"] == "<orig-msg@mailer>"
            assert "<orig-msg@mailer>" in mime["References"]
            assert "<root-msg@mailer>" in mime["References"]
        finally:
            _teardown(patcher)

    def test_send_message_auto_prepends_re_when_missing(self):
        client, svc, patcher = _make_client()
        try:
            svc.users().messages().send().execute.return_value = {"id": "gmail-2"}

            client.send_message(
                to="c@e.com",
                subject="New order",
                body_text="body",
                in_reply_to=None,
                references=None,
            )

            mime = _decode_sent_mime(svc)
            assert mime["Subject"] == "Re: New order"
        finally:
            _teardown(patcher)

    def test_send_message_preserves_subject_when_re_already_present(self):
        client, svc, patcher = _make_client()
        try:
            svc.users().messages().send().execute.return_value = {"id": "gmail-3"}

            client.send_message(
                to="c@e.com",
                subject="Re: Existing reply",
                body_text="body",
                in_reply_to=None,
                references=None,
            )

            mime = _decode_sent_mime(svc)
            assert mime["Subject"] == "Re: Existing reply"
        finally:
            _teardown(patcher)

    def test_send_message_returns_gmail_id_from_api_response(self):
        client, svc, patcher = _make_client()
        try:
            svc.users().messages().send().execute.return_value = {"id": "expected-id-xyz"}
            result = client.send_message(
                to="c@e.com",
                subject="s",
                body_text="b",
                in_reply_to=None,
                references=None,
            )
            assert result == "expected-id-xyz"
        finally:
            _teardown(patcher)

    def test_send_message_without_reply_headers_still_valid_mime(self):
        client, svc, patcher = _make_client()
        try:
            svc.users().messages().send().execute.return_value = {"id": "gmail-5"}

            client.send_message(
                to="c@e.com",
                subject="New conversation",
                body_text="Hello.",
                in_reply_to=None,
                references=None,
            )

            mime = _decode_sent_mime(svc)
            assert mime["To"] == "c@e.com"
            assert mime["In-Reply-To"] is None
            assert mime["References"] is None
            # body present
            payload = mime.get_payload()
            assert payload
        finally:
            _teardown(patcher)
```

- [ ] **Step 2.2: Run — expect failure**

Run: `uv run pytest tests/unit/test_gmail_send.py -v`

Expected: `AttributeError: 'GmailClient' object has no attribute 'send_message'`.

- [ ] **Step 2.3: Add `send_message` to `backend/gmail/client.py`**

Add imports at the top:

```python
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
```

Append method to the `GmailClient` class (after `apply_label`):

```python
    # ---- send surface ----

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
        'Re: ' to subject when not already present. Returns the sent
        Gmail message id.
        """
        msg = MIMEMultipart()
        msg["To"] = to
        msg["From"] = "me"
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid()

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

Add module-level helper `_bracket`:

```python
def _bracket(m: str) -> str:
    """Ensure RFC 5322 Message-ID refs are angle-bracketed."""
    m = m.strip()
    if m.startswith("<") and m.endswith(">"):
        return m
    return f"<{m}>"
```

- [ ] **Step 2.4: Run — expect 5 passes**

Run: `uv run pytest tests/unit/test_gmail_send.py -v`

Expected: `5 passed`.

- [ ] **Step 2.5: Verify A1 tests still pass**

Run: `uv run pytest tests/unit/test_gmail_client.py -v`

Expected: all green.

- [ ] **Step 2.6: Commit**

```bash
git add backend/gmail/client.py tests/unit/test_gmail_send.py
git commit -m "feat(track-a2): GmailClient.send_message with RFC 5322 reply threading"
```

---

## Task 3: Bump `OrderRecord` schema v3→v4 with `sent_at` + `send_error`

**Files:**
- Modify: `backend/models/order_record.py`
- Modify: `tests/unit/test_order_store.py` (schema test + fixture updates)
- Modify: `tests/unit/test_stage_persist.py` (fixture updates)
- Modify: `tests/unit/test_stage_confirm.py` (fixture updates if constructs OrderRecord directly)
- Modify: `tests/integration/test_order_store_emulator.py` (fixture updates)

**Note:** This task assumes Track C has bumped schema to v3. If Track C has NOT landed yet (schema at v2), bump v2→v3 here instead. The field additions are identical either way.

- [ ] **Step 3.1: Write the failing schema test**

Append to `tests/unit/test_order_store.py`:

```python
# Track A2 — schema v4 with send receipt fields

from datetime import datetime, timezone


class TestOrderRecordSchemaV4:
    def test_schema_version_default_is_4(self):
        from backend.models.order_record import OrderRecord
        # Use the canonical test-fixture construction helper. If none exists
        # yet, use whatever sites in this file build a minimum-valid OrderRecord.
        record = _build_valid_order_record()
        assert record.schema_version == 4

    def test_sent_at_defaults_to_none(self):
        record = _build_valid_order_record()
        assert record.sent_at is None

    def test_send_error_defaults_to_none(self):
        record = _build_valid_order_record()
        assert record.send_error is None

    def test_sent_at_accepts_utc_datetime(self):
        from backend.models.order_record import OrderRecord
        now = datetime.now(timezone.utc)
        record = _build_valid_order_record(sent_at=now)
        assert record.sent_at == now


def _build_valid_order_record(**overrides):
    """Helper mirroring the existing minimal-record construction pattern
    in this file. Find the nearest existing OrderRecord(...) call in the
    test file and replicate its kwargs here, then merge overrides."""
    from backend.models.order_record import CustomerSnapshot, OrderRecord
    from backend.models.master_records import AddressRecord
    from datetime import datetime, timezone

    base = dict(
        source_message_id="msg-1",
        thread_id="thr-1",
        customer=CustomerSnapshot(
            customer_id="CUST-00042",
            name="Acme",
            bill_to=AddressRecord(
                street1="100 Ind Way", city="Dayton", state="OH",
                zip="45402", country="USA",
            ),
            payment_terms="Net 30",
        ),
        customer_id="CUST-00042",     # assumes Track C landed; drop if it didn't
        po_number="PO-123",            # assumes Track C landed
        content_hash="a" * 64,         # assumes Track C landed
        lines=[],
        order_total=0.0,
        confidence=1.0,
        processed_by_agent_version="track-a-v0.3",
        created_at=datetime.now(timezone.utc),
    )
    base.update(overrides)
    return OrderRecord(**base)
```

If Track C's schema v3 has NOT landed, omit `customer_id` / `po_number` / `content_hash` from `_build_valid_order_record`.

- [ ] **Step 3.2: Run — expect failure on schema_version default**

Run: `uv run pytest tests/unit/test_order_store.py::TestOrderRecordSchemaV4 -v`

Expected: `assert 3 == 4` (or `2 == 4` if Track C didn't land).

Plus failures on `sent_at` / `send_error` attribute-unknown errors (ConfigDict `extra="forbid"` rejects).

- [ ] **Step 3.3: Update `backend/models/order_record.py`**

Find the `OrderRecord` class. Add two fields + bump `schema_version`:

```python
class OrderRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # ... existing fields ...

    sent_at: Optional[datetime] = None
    send_error: Optional[str] = None
    schema_version: int = 4
```

(If Track C didn't land, use `schema_version: int = 3`.)

- [ ] **Step 3.4: Run — expect 4 new tests pass; other tests may fail due to extra="forbid" rejecting `sent_at`/`send_error` if they build OrderRecord directly**

Run: `uv run pytest tests/unit/test_order_store.py -v`

If existing tests fail because they construct OrderRecord literally without the new fields (unlikely — fields have defaults), update them. The new Optional default values SHOULD make existing fixtures continue to work without touching them.

- [ ] **Step 3.5: Grep for other construction sites that might hardcode schema_version**

Run: `grep -rn "schema_version" backend/ tests/ | grep -i "order"`

If any test hardcodes `schema_version=3` or `schema_version=2` in an `OrderRecord` construction, update to `4`. If any test asserts `schema_version == 3`, update to `4`. Adjust per Track C state.

- [ ] **Step 3.6: Run full unit suite — expect green**

Run: `uv run pytest tests/unit -x --tb=short 2>&1 | tail -10`

Expected: all green.

- [ ] **Step 3.7: Commit**

```bash
git add backend/models/order_record.py tests/unit/test_order_store.py tests/unit/test_stage_persist.py tests/unit/test_stage_confirm.py tests/integration/test_order_store_emulator.py
git commit -m "feat(track-a2): OrderRecord schema v4 with sent_at + send_error"
```

---

## Task 4: Bump `ExceptionRecord` schema v2→v3 with `sent_at` + `send_error`

**Files:**
- Modify: `backend/models/exception_record.py`
- Modify: `tests/unit/test_exception_store.py` (schema test + fixture updates)
- Modify: `tests/unit/test_coordinator.py` (fixture updates if needed)
- Modify: `tests/integration/test_exception_store_emulator.py` (fixture updates)

- [ ] **Step 4.1: Write the failing schema test**

Append to `tests/unit/test_exception_store.py`:

```python
# Track A2 — schema v3 with send receipt fields

from datetime import datetime, timezone


class TestExceptionRecordSchemaV3:
    def test_schema_version_default_is_3(self):
        record = _build_valid_exception_record()
        assert record.schema_version == 3

    def test_sent_at_and_send_error_default_to_none(self):
        record = _build_valid_exception_record()
        assert record.sent_at is None
        assert record.send_error is None

    def test_sent_at_accepts_utc_datetime(self):
        now = datetime.now(timezone.utc)
        record = _build_valid_exception_record(sent_at=now)
        assert record.sent_at == now


def _build_valid_exception_record(**overrides):
    """Mirror the existing minimal-record pattern in this file."""
    from backend.models.exception_record import ExceptionRecord, ExceptionStatus
    from datetime import datetime, timezone

    base = dict(
        source_message_id="msg-1",
        thread_id="thr-1",
        reason="validation failed",
        status=ExceptionStatus.PENDING_CLARIFY,
        processed_by_agent_version="track-a-v0.3",
        created_at=datetime.now(timezone.utc),
        clarify_body=None,
        # add any other currently-required fields here by inspecting
        # backend/models/exception_record.py
    )
    base.update(overrides)
    return ExceptionRecord(**base)
```

- [ ] **Step 4.2: Run — expect failure**

Run: `uv run pytest tests/unit/test_exception_store.py::TestExceptionRecordSchemaV3 -v`

Expected: `assert 2 == 3` + `extra="forbid"` rejection on `sent_at` / `send_error`.

- [ ] **Step 4.3: Update `backend/models/exception_record.py`**

```python
class ExceptionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # ... existing fields ...

    sent_at: Optional[datetime] = None
    send_error: Optional[str] = None
    schema_version: int = 3
```

- [ ] **Step 4.4: Run new tests + full exception-store suite**

Run: `uv run pytest tests/unit/test_exception_store.py -v`

Expected: all green.

- [ ] **Step 4.5: Grep for existing schema_version assertions**

Run: `grep -rn "schema_version" backend/ tests/ | grep -i "except"`

Update any `== 2` / `=2` to `== 3` / `=3`.

- [ ] **Step 4.6: Run full unit suite — expect green**

Run: `uv run pytest tests/unit -x --tb=short 2>&1 | tail -10`

Expected: all green.

- [ ] **Step 4.7: Commit**

```bash
git add backend/models/exception_record.py tests/unit/test_exception_store.py tests/unit/test_coordinator.py tests/integration/test_exception_store_emulator.py
git commit -m "feat(track-a2): ExceptionRecord schema v3 with sent_at + send_error"
```

---

## Task 5: `update_with_send_receipt` on both stores

**Files:**
- Modify: `backend/persistence/base.py`
- Modify: `backend/persistence/orders_store.py`
- Modify: `backend/persistence/exceptions_store.py`
- Modify: `tests/unit/test_order_store.py`
- Modify: `tests/unit/test_exception_store.py`

- [ ] **Step 5.1: Write the failing tests for OrderStore**

Append to `tests/unit/test_order_store.py`:

```python
from datetime import datetime, timezone


@pytest.mark.asyncio
class TestOrderStoreUpdateWithSendReceipt:
    async def test_update_sets_sent_at_and_clears_send_error(self, fake_client):
        from backend.persistence.orders_store import FirestoreOrderStore

        store = FirestoreOrderStore(fake_client)
        # Save a minimal order first via existing helpers
        record = _build_valid_order_record(source_message_id="msg-X")
        await store.save(record)

        sent_at = datetime.now(timezone.utc)
        await store.update_with_send_receipt(
            source_message_id="msg-X",
            sent_at=sent_at,
            send_error=None,
        )

        got = await store.get("msg-X")
        assert got.sent_at == sent_at
        assert got.send_error is None

    async def test_update_records_send_error_when_sent_at_none(self, fake_client):
        from backend.persistence.orders_store import FirestoreOrderStore

        store = FirestoreOrderStore(fake_client)
        record = _build_valid_order_record(source_message_id="msg-Y")
        await store.save(record)

        await store.update_with_send_receipt(
            source_message_id="msg-Y",
            sent_at=None,
            send_error="RuntimeError: quota exceeded",
        )

        got = await store.get("msg-Y")
        assert got.sent_at is None
        assert got.send_error == "RuntimeError: quota exceeded"
```

- [ ] **Step 5.2: Write the failing tests for ExceptionStore**

Append to `tests/unit/test_exception_store.py`:

```python
from datetime import datetime, timezone


@pytest.mark.asyncio
class TestExceptionStoreUpdateWithSendReceipt:
    async def test_update_sets_sent_at(self, fake_client):
        from backend.persistence.exceptions_store import FirestoreExceptionStore

        store = FirestoreExceptionStore(fake_client)
        record = _build_valid_exception_record(source_message_id="msg-A")
        await store.save(record)

        sent_at = datetime.now(timezone.utc)
        await store.update_with_send_receipt(
            source_message_id="msg-A",
            sent_at=sent_at,
            send_error=None,
        )

        got = await store.get("msg-A")
        assert got.sent_at == sent_at
        assert got.send_error is None

    async def test_update_records_send_error(self, fake_client):
        from backend.persistence.exceptions_store import FirestoreExceptionStore

        store = FirestoreExceptionStore(fake_client)
        record = _build_valid_exception_record(source_message_id="msg-B")
        await store.save(record)

        await store.update_with_send_receipt(
            source_message_id="msg-B",
            sent_at=None,
            send_error="no_recipient",
        )

        got = await store.get("msg-B")
        assert got.sent_at is None
        assert got.send_error == "no_recipient"
```

- [ ] **Step 5.3: Run — expect failure**

Run: `uv run pytest tests/unit/test_order_store.py::TestOrderStoreUpdateWithSendReceipt tests/unit/test_exception_store.py::TestExceptionStoreUpdateWithSendReceipt -v`

Expected: `AttributeError: 'FirestoreOrderStore' object has no attribute 'update_with_send_receipt'`.

- [ ] **Step 5.4: Extend Protocols in `backend/persistence/base.py`**

Add to both `OrderStore` and `ExceptionStore` Protocols:

```python
from datetime import datetime
from typing import Optional

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

- [ ] **Step 5.5: Add impl in `backend/persistence/orders_store.py`**

```python
from datetime import datetime
from typing import Optional

# Inside FirestoreOrderStore class:

async def update_with_send_receipt(
    self,
    *,
    source_message_id: str,
    sent_at: Optional[datetime],
    send_error: Optional[str],
) -> None:
    """Field-mask update of sent_at + send_error on a persisted order.

    Raises google.api_core.exceptions.NotFound if the doc is missing
    (invariant violation — callers invoke post-save).
    """
    doc_ref = self._client.collection(self._collection).document(source_message_id)
    await doc_ref.update({
        "sent_at": sent_at,
        "send_error": send_error,
    })
```

- [ ] **Step 5.6: Add the same impl to `backend/persistence/exceptions_store.py`**

Same method, same body, on `FirestoreExceptionStore`.

- [ ] **Step 5.7: Run tests — expect all 4 pass**

Run: `uv run pytest tests/unit/test_order_store.py::TestOrderStoreUpdateWithSendReceipt tests/unit/test_exception_store.py::TestExceptionStoreUpdateWithSendReceipt -v`

Expected: `4 passed`.

- [ ] **Step 5.8: Run full unit suite — expect green**

Run: `uv run pytest tests/unit -x --tb=short 2>&1 | tail -10`

Expected: all green.

- [ ] **Step 5.9: Commit**

```bash
git add backend/persistence/base.py backend/persistence/orders_store.py backend/persistence/exceptions_store.py tests/unit/test_order_store.py tests/unit/test_exception_store.py
git commit -m "feat(track-a2): update_with_send_receipt on OrderStore + ExceptionStore"
```

---

## Task 6: `SendStage` full implementation

**Files:**
- Create: `backend/my_agent/stages/send.py`
- Create: `tests/unit/test_stage_send.py`

- [ ] **Step 6.1: Write the 9 failing tests**

Create `tests/unit/test_stage_send.py`:

```python
"""Unit tests for SendStage.

Uses AsyncMock(spec=OrderStore) + AsyncMock(spec=ExceptionStore) +
MagicMock(spec=GmailClient) + AsyncMock(spec=AuditLogger from Track D).
If Track D has not landed, replace AuditLogger spec with a protocol
that has an awaitable .emit(...) method — the mixin's contract is stable.

Spec: docs/superpowers/specs/2026-04-24-track-a2-gmail-egress-design.md
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.gmail.client import GmailClient
from backend.persistence.base import ExceptionStore, OrderStore

pytestmark = pytest.mark.asyncio


def _order_result_entry(
    *,
    sub_doc_index: int = 0,
    source_message_id: str = "msg-1",
    confirmation_body: str | None = "Thanks for your order.",
    sent_at=None,
    contact_email: str | None = "customer@example.com",
):
    return {
        "filename": "body.txt",
        "sub_doc_index": sub_doc_index,
        "result": {
            "kind": "order",
            "order": {
                "source_message_id": source_message_id,
                "confirmation_body": confirmation_body,
                "sent_at": sent_at,
                "customer": {"contact_email": contact_email},
            },
            "exception": None,
        },
    }


def _exception_result_entry(
    *,
    sub_doc_index: int = 0,
    source_message_id: str = "msg-2",
    clarify_body: str | None = "Please clarify the missing qty.",
    sent_at=None,
    contact_email: str | None = "customer@example.com",
):
    return {
        "filename": "body.txt",
        "sub_doc_index": sub_doc_index,
        "result": {
            "kind": "exception",
            "order": None,
            "exception": {
                "source_message_id": source_message_id,
                "clarify_body": clarify_body,
                "sent_at": sent_at,
                "customer_contact_email": contact_email,
                # Note: ExceptionRecord may store contact differently;
                # adjust per the actual schema. The SendStage pulls
                # recipient from the envelope (sender) for clarify emails.
            },
        },
    }


def _make_state(process_results, envelope=None, reply_handled=False, correlation_id="c1"):
    return {
        "correlation_id": correlation_id,
        "reply_handled": reply_handled,
        "envelope": envelope or {
            "message_id": "<orig-msg@mailer>",
            "subject": "Order request",
            "sender": "customer@example.com",
            "references": [],
        },
        "process_results": process_results,
    }


def _make_ctx(state):
    """Build a minimal ctx that SendStage can read state from. Use the
    existing _stage_testing helper if present."""
    from tests.unit._stage_testing import make_stage_ctx
    return make_stage_ctx(stage=None, state=state)


async def _make_stage(*, gmail_client=None, dry_run=False):
    from backend.my_agent.stages.send import SendStage

    order_store = AsyncMock(spec=OrderStore)
    exception_store = AsyncMock(spec=ExceptionStore)
    audit_logger = AsyncMock()  # spec=AuditLogger if Track D landed
    gc = gmail_client if gmail_client is not None else MagicMock(spec=GmailClient)

    stage = SendStage(
        gmail_client=gc,
        order_store=order_store,
        exception_store=exception_store,
        dry_run=dry_run,
        audit_logger=audit_logger,
    )
    return stage, order_store, exception_store, audit_logger, gc


class TestSendStageSkipPaths:
    async def test_noop_when_gmail_client_is_none(self):
        stage, order_store, exception_store, audit_logger, _ = await _make_stage(gmail_client=None)
        ctx = _make_ctx(_make_state([_order_result_entry()]))
        async for _ in stage.run_async(ctx):
            pass
        order_store.update_with_send_receipt.assert_not_awaited()
        exception_store.update_with_send_receipt.assert_not_awaited()

    async def test_noop_when_reply_handled(self):
        stage, order_store, exception_store, audit_logger, gc = await _make_stage()
        ctx = _make_ctx(_make_state([_order_result_entry()], reply_handled=True))
        async for _ in stage.run_async(ctx):
            pass
        order_store.update_with_send_receipt.assert_not_awaited()
        gc.send_message.assert_not_called()


class TestSendStageAutoApprove:
    async def test_sends_confirmation_when_body_present_and_not_sent(self):
        stage, order_store, exception_store, audit_logger, gc = await _make_stage()
        gc.send_message = MagicMock(return_value="gmail-id-1")
        ctx = _make_ctx(_make_state([_order_result_entry()]))

        async for _ in stage.run_async(ctx):
            pass

        gc.send_message.assert_called_once()
        order_store.update_with_send_receipt.assert_awaited_once()
        update_kwargs = order_store.update_with_send_receipt.await_args.kwargs
        assert update_kwargs["source_message_id"] == "msg-1"
        assert update_kwargs["sent_at"] is not None
        assert update_kwargs["send_error"] is None

    async def test_skips_send_when_sent_at_already_set(self):
        stage, order_store, exception_store, audit_logger, gc = await _make_stage()
        ctx = _make_ctx(_make_state([
            _order_result_entry(sent_at=datetime.now(timezone.utc))
        ]))

        async for _ in stage.run_async(ctx):
            pass

        gc.send_message.assert_not_called()
        order_store.update_with_send_receipt.assert_not_awaited()


class TestSendStageClarify:
    async def test_sends_clarify_when_exception_has_body(self):
        stage, order_store, exception_store, audit_logger, gc = await _make_stage()
        gc.send_message = MagicMock(return_value="gmail-id-2")
        ctx = _make_ctx(_make_state([_exception_result_entry()]))

        async for _ in stage.run_async(ctx):
            pass

        gc.send_message.assert_called_once()
        exception_store.update_with_send_receipt.assert_awaited_once()


class TestSendStageEscalateAndFailure:
    async def test_skips_send_when_exception_has_no_body(self):
        stage, order_store, exception_store, audit_logger, gc = await _make_stage()
        ctx = _make_ctx(_make_state([
            _exception_result_entry(clarify_body=None)
        ]))

        async for _ in stage.run_async(ctx):
            pass

        gc.send_message.assert_not_called()
        exception_store.update_with_send_receipt.assert_not_awaited()

    async def test_dry_run_logs_but_does_not_send_or_update(self):
        stage, order_store, exception_store, audit_logger, gc = await _make_stage(dry_run=True)
        ctx = _make_ctx(_make_state([_order_result_entry()]))

        async for _ in stage.run_async(ctx):
            pass

        gc.send_message.assert_not_called()
        order_store.update_with_send_receipt.assert_not_awaited()
        # Audit event emitted
        dry_run_emits = [
            c for c in audit_logger.emit.await_args_list
            if c.kwargs.get("action") == "email_send_dry_run"
        ]
        assert len(dry_run_emits) == 1

    async def test_send_failure_records_error_and_continues(self):
        stage, order_store, exception_store, audit_logger, gc = await _make_stage()
        gc.send_message = MagicMock(side_effect=RuntimeError("quota exceeded"))
        ctx = _make_ctx(_make_state([
            _order_result_entry(source_message_id="msg-fail"),
            _order_result_entry(source_message_id="msg-ok"),
        ]))

        # First call fails; second call succeeds — need side_effect list
        gc.send_message = MagicMock(side_effect=[
            RuntimeError("quota exceeded"),
            "gmail-id-ok",
        ])

        async for _ in stage.run_async(ctx):
            pass

        # First update records error
        calls = order_store.update_with_send_receipt.await_args_list
        fail_call = calls[0].kwargs
        ok_call = calls[1].kwargs
        assert fail_call["sent_at"] is None
        assert "quota exceeded" in fail_call["send_error"]
        assert ok_call["sent_at"] is not None
        assert ok_call["send_error"] is None

    async def test_missing_recipient_records_no_recipient_error(self):
        stage, order_store, exception_store, audit_logger, gc = await _make_stage()
        ctx = _make_ctx(_make_state([
            _order_result_entry(contact_email=None)
        ]))

        async for _ in stage.run_async(ctx):
            pass

        gc.send_message.assert_not_called()
        order_store.update_with_send_receipt.assert_awaited_once()
        update_kwargs = order_store.update_with_send_receipt.await_args.kwargs
        assert update_kwargs["send_error"] == "no_recipient"
```

- [ ] **Step 6.2: Run — expect failure**

Run: `uv run pytest tests/unit/test_stage_send.py -v`

Expected: import failure on `backend.my_agent.stages.send`.

- [ ] **Step 6.3: Create `backend/my_agent/stages/send.py`**

```python
"""SendStage — 10th BaseAgent. Sends Gmail replies for AUTO_APPROVE
confirmation bodies and CLARIFY clarification bodies.

Walks state["process_results"] after FinalizeStage, fail-open per entry.
Subclasses AuditedStage (Track D) so stage entry/exit + lifecycle emits
happen uniformly.

Spec: docs/superpowers/specs/2026-04-24-track-a2-gmail-egress-design.md
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Final, Optional

from pydantic import PrivateAttr

from backend.gmail.client import GmailClient
from backend.my_agent.stages._audited import AuditedStage
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

        envelope = state.get("envelope") or {}
        original_message_id = envelope.get("message_id")
        original_references = envelope.get("references") or []
        original_subject = envelope.get("subject") or ""
        original_sender = envelope.get("sender") or ""

        references_chain: list[str] = list(original_references)
        if original_message_id:
            references_chain.append(original_message_id)

        for entry in state.get("process_results", []):
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
                    fallback_recipient=original_sender,
                )
            # kind == "duplicate": no new body, nothing to send

        # Keep generator async-iterable
        if False:
            yield None  # pragma: no cover

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
        source_message_id = order.get("source_message_id") or ""
        body = order.get("confirmation_body")
        if not body:
            await self._emit_skipped(ctx, source_message_id, "no_body")
            return
        if order.get("sent_at") is not None:
            await self._emit_skipped(ctx, source_message_id, "already_sent")
            return
        recipient = ((order.get("customer") or {}).get("contact_email")) or ""
        if not recipient:
            await self._record_failure(source_message_id, self._order_store, "no_recipient")
            await self._emit_failure(ctx, source_message_id, "no_recipient")
            return

        if self._dry_run:
            _log.info(
                "send_dry_run",
                order_id=source_message_id,
                to=recipient,
                subject=original_subject,
            )
            await self._emit_dry_run(ctx, source_message_id, recipient)
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
            _log.error("send_failed", order_id=source_message_id, error=reason)
            await self._record_failure(source_message_id, self._order_store, reason)
            await self._emit_failure(ctx, source_message_id, reason)
            return

        try:
            await self._order_store.update_with_send_receipt(
                source_message_id=source_message_id,
                sent_at=datetime.now(timezone.utc),
                send_error=None,
            )
        except Exception as exc:
            _log.error(
                "send_receipt_write_failed",
                order_id=source_message_id,
                error=str(exc),
            )
        await self._emit_success(ctx, source_message_id, gmail_id)

    async def _maybe_send_clarify(
        self,
        *,
        ctx,
        exception: Optional[dict[str, Any]],
        original_message_id: Optional[str],
        references: list[str],
        original_subject: str,
        fallback_recipient: str,
    ) -> None:
        if exception is None:
            return
        source_message_id = exception.get("source_message_id") or ""
        body = exception.get("clarify_body")
        if not body:
            await self._emit_skipped(ctx, source_message_id, "no_body")
            return
        if exception.get("sent_at") is not None:
            await self._emit_skipped(ctx, source_message_id, "already_sent")
            return

        # Exception recipient strategy: ExceptionRecord may not carry a
        # contact_email; fall back to the envelope's original sender.
        # If ExceptionRecord exposes a recipient field, prefer that.
        recipient = (
            exception.get("customer_contact_email")
            or exception.get("contact_email")
            or fallback_recipient
            or ""
        )
        if not recipient:
            await self._record_failure(
                source_message_id, self._exception_store, "no_recipient"
            )
            await self._emit_failure(ctx, source_message_id, "no_recipient")
            return

        if self._dry_run:
            _log.info(
                "send_dry_run",
                exception_id=source_message_id,
                to=recipient,
                subject=original_subject,
            )
            await self._emit_dry_run(ctx, source_message_id, recipient)
            return

        try:
            gmail_id = await asyncio.to_thread(
                self._gmail_client.send_message,
                to=recipient,
                subject=original_subject or "We need a bit more detail",
                body_text=body,
                in_reply_to=original_message_id,
                references=references,
            )
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            _log.error("send_failed", exception_id=source_message_id, error=reason)
            await self._record_failure(source_message_id, self._exception_store, reason)
            await self._emit_failure(ctx, source_message_id, reason)
            return

        try:
            await self._exception_store.update_with_send_receipt(
                source_message_id=source_message_id,
                sent_at=datetime.now(timezone.utc),
                send_error=None,
            )
        except Exception as exc:
            _log.error(
                "send_receipt_write_failed",
                exception_id=source_message_id,
                error=str(exc),
            )
        await self._emit_success(ctx, source_message_id, gmail_id)

    async def _record_failure(self, source_message_id, store, reason):
        try:
            await store.update_with_send_receipt(
                source_message_id=source_message_id,
                sent_at=None,
                send_error=reason,
            )
        except Exception as exc:
            _log.error("send_receipt_write_failed", error=str(exc))

    async def _emit_success(self, ctx, source_message_id, gmail_id):
        await self._audit_logger.emit(
            correlation_id=ctx.session.state.get("correlation_id", ""),
            session_id=ctx.session.id,
            source_message_id=source_message_id,
            stage="lifecycle",
            phase="lifecycle",
            action="email_sent",
            outcome="ok",
            payload={"gmail_message_id": gmail_id, "record_id": source_message_id},
        )

    async def _emit_failure(self, ctx, source_message_id, error):
        await self._audit_logger.emit(
            correlation_id=ctx.session.state.get("correlation_id", ""),
            session_id=ctx.session.id,
            source_message_id=source_message_id,
            stage="lifecycle",
            phase="lifecycle",
            action="email_send_failed",
            outcome="error",
            payload={"record_id": source_message_id, "error": error},
        )

    async def _emit_dry_run(self, ctx, source_message_id, recipient):
        await self._audit_logger.emit(
            correlation_id=ctx.session.state.get("correlation_id", ""),
            session_id=ctx.session.id,
            source_message_id=source_message_id,
            stage="lifecycle",
            phase="lifecycle",
            action="email_send_dry_run",
            outcome="ok",
            payload={"record_id": source_message_id, "would_send_to": recipient},
        )

    async def _emit_skipped(self, ctx, source_message_id, reason):
        await self._audit_logger.emit(
            correlation_id=ctx.session.state.get("correlation_id", ""),
            session_id=ctx.session.id,
            source_message_id=source_message_id,
            stage="lifecycle",
            phase="lifecycle",
            action="email_send_skipped",
            outcome="skip",
            payload={"record_id": source_message_id, "reason": reason},
        )


__all__ = ["SEND_STAGE_NAME", "SendStage"]
```

- [ ] **Step 6.4: Run tests — expect 9 passes**

Run: `uv run pytest tests/unit/test_stage_send.py -v`

Expected: `9 passed`.

If AuditedStage is not yet available (Track D hasn't landed), temporarily inherit directly from `BaseAgent` and inline the audit-emit calls — or defer this task until after Track D. **Recommended:** land Track D first, then A2. If you must proceed without D, replace `from backend.my_agent.stages._audited import AuditedStage` with a local inlined version of the mixin.

- [ ] **Step 6.5: Commit**

```bash
git add backend/my_agent/stages/send.py tests/unit/test_stage_send.py
git commit -m "feat(track-a2): SendStage orchestrates Gmail replies per process_result"
```

---

## Task 7: Wire `SendStage` into `build_root_agent` + `AGENT_VERSION` bump

**Files:**
- Modify: `backend/my_agent/agent.py`
- Modify: `tests/unit/test_orchestrator_build.py`

- [ ] **Step 7.1: Write the 3 failing orchestrator tests**

Append to `tests/unit/test_orchestrator_build.py`:

```python
class TestTrackA2Orchestration:
    def test_build_root_agent_accepts_gmail_client_and_send_dry_run(self):
        from backend.my_agent.agent import build_root_agent
        from unittest.mock import MagicMock
        from backend.gmail.client import GmailClient

        deps = _make_deps()  # existing helper
        root = build_root_agent(
            **deps,
            gmail_client=MagicMock(spec=GmailClient),
            send_dry_run=True,
        )
        assert root is not None

    def test_assembled_root_agent_has_10_sub_agents_with_send_last(self):
        from backend.my_agent.agent import build_root_agent
        from backend.my_agent.stages.send import SendStage, SEND_STAGE_NAME

        deps = _make_deps()
        root = build_root_agent(
            **deps,
            gmail_client=None,
            send_dry_run=False,
        )
        assert len(root.sub_agents) == 10
        assert isinstance(root.sub_agents[9], SendStage)
        assert root.sub_agents[9].name == SEND_STAGE_NAME

    def test_agent_version_is_track_a_v0_3(self):
        from backend.my_agent.agent import AGENT_VERSION
        assert AGENT_VERSION == "track-a-v0.3"
```

Update `_make_deps()` (the existing test fixture) — add `audit_logger=AsyncMock(...)` if Track D requires it; leave other deps as-is. The new `gmail_client` + `send_dry_run` kwargs do NOT go into `_make_deps` (they're passed separately in each test above).

- [ ] **Step 7.2: Run — expect failure**

Run: `uv run pytest tests/unit/test_orchestrator_build.py::TestTrackA2Orchestration -v`

Expected: all 3 fail — `TypeError: build_root_agent() got an unexpected keyword argument 'gmail_client'`, + `AGENT_VERSION` still `"track-a-v0.2"`.

- [ ] **Step 7.3: Update `backend/my_agent/agent.py`**

Two edits:

1. Bump `AGENT_VERSION`:

```python
AGENT_VERSION: Final[str] = "track-a-v0.3"
```

2. Update `build_root_agent` signature + body:

```python
from typing import Any, Final, Optional

# New imports:
from backend.gmail.client import GmailClient
from .stages.send import SendStage


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
    gmail_client: Optional[GmailClient] = None,
    send_dry_run: bool = False,
    audit_logger: Any = None,  # if Track D present; else drop this kwarg
) -> SequentialAgent:
    sub_agents = [
        IngestStage(),  # + audit_logger=audit_logger if D present
        ReplyShortCircuitStage(exception_store=exception_store),
        ClassifyStage(classify_fn=classify_fn),
        ParseStage(parse_fn=parse_fn),
        ValidateStage(validator=validator),
        ClarifyStage(clarify_agent=clarify_agent),
        PersistStage(coordinator=coordinator),
        ConfirmStage(confirm_agent=confirm_agent, order_store=order_store),
        FinalizeStage(summary_agent=summary_agent),
        SendStage(
            gmail_client=gmail_client,
            order_store=order_store,
            exception_store=exception_store,
            dry_run=send_dry_run,
            audit_logger=audit_logger,
        ),
    ]
    return SequentialAgent(name=ROOT_AGENT_NAME, sub_agents=sub_agents)
```

3. Update `_build_default_root_agent`:

```python
import os

# Inside _build_default_root_agent, after existing variable construction:
send_dry_run = os.environ.get("GMAIL_SEND_DRY_RUN", "1") == "1"

gmail_client: Optional[GmailClient] = None
cid = os.environ.get("GMAIL_CLIENT_ID")
csec = os.environ.get("GMAIL_CLIENT_SECRET")
rt = os.environ.get("GMAIL_REFRESH_TOKEN")
if cid and csec and rt:
    from backend.gmail.scopes import A2_SCOPES
    gmail_client = GmailClient(
        refresh_token=rt,
        client_id=cid,
        client_secret=csec,
        scopes=A2_SCOPES,
    )
# else: gmail_client stays None — offline / fixture / non-Gmail-configured runs

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
    gmail_client=gmail_client,
    send_dry_run=send_dry_run,
    # audit_logger=audit_logger if Track D present
)
```

- [ ] **Step 7.4: Run new tests — expect 3 pass**

Run: `uv run pytest tests/unit/test_orchestrator_build.py::TestTrackA2Orchestration -v`

Expected: `3 passed`.

- [ ] **Step 7.5: Run full unit suite to catch any existing orchestrator tests broken by the 10-stage topology**

Run: `uv run pytest tests/unit -x --tb=short 2>&1 | tail -20`

Expected: all green. If existing canonical-order or stage-count assertions fail, update those tests to expect 10 stages / `SendStage` at index 9.

- [ ] **Step 7.6: Commit**

```bash
git add backend/my_agent/agent.py tests/unit/test_orchestrator_build.py
git commit -m "feat(track-a2): wire SendStage into build_root_agent; AGENT_VERSION v0.2→v0.3"
```

---

## Task 8: Update scripts + `.env.example`

**Files:**
- Modify: `scripts/gmail_auth_init.py`
- Modify: `scripts/gmail_poll.py`
- Modify: `.env.example`

- [ ] **Step 8.1: Update `scripts/gmail_auth_init.py` default scopes**

Change:
```python
from backend.gmail.scopes import A1_SCOPES

# ... in main body:
flow = InstalledAppFlow.from_client_secrets_file(
    str(credentials_path), scopes=A1_SCOPES
)
```

To:
```python
from backend.gmail.scopes import A2_SCOPES

# ... in main body:
flow = InstalledAppFlow.from_client_secrets_file(
    str(credentials_path), scopes=A2_SCOPES
)
```

Also update the print-header line near the top of `main` to mention the scope upgrade:

```python
print()
print("=" * 72)
print("OAuth setup complete — scopes: gmail.modify + gmail.send (A1 + A2)")
print("Paste these three lines into .env:")
print("=" * 72)
```

- [ ] **Step 8.2: Update `scripts/gmail_poll.py`**

Change the import:

```python
from backend.gmail.scopes import A2_SCOPES
```

Change the `GmailClient` construction to use `A2_SCOPES`:

```python
gmail_client = GmailClient(
    refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
    client_id=os.environ["GMAIL_CLIENT_ID"],
    client_secret=os.environ["GMAIL_CLIENT_SECRET"],
    scopes=A2_SCOPES,
)
```

The `_build_default_root_agent()` call already reads `GMAIL_SEND_DRY_RUN` internally (per Task 7.3) — nothing else to change in this script.

- [ ] **Step 8.3: Update `.env.example`**

Append:

```
# Track A2: send mode
# Set to 0 (or unset) to actually send email. Default 1 = dry-run for safety.
GMAIL_SEND_DRY_RUN=1
```

- [ ] **Step 8.4: Syntax-check the updated scripts**

Run:
```bash
uv run python -c "import ast; ast.parse(open('scripts/gmail_auth_init.py').read())"
uv run python -c "import ast; ast.parse(open('scripts/gmail_poll.py').read())"
```

Expected: no output.

- [ ] **Step 8.5: Commit**

```bash
git add scripts/gmail_auth_init.py scripts/gmail_poll.py .env.example
git commit -m "feat(track-a2): scripts use A2_SCOPES + GMAIL_SEND_DRY_RUN toggle"
```

---

## Task 9: Integration test + doc updates + final verification

**Files:**
- Modify: `tests/integration/test_orchestrator_emulator.py`
- Modify: `backend/my_agent/README.md`
- Modify: `research/Order-Intake-Sprint-Status.md`
- Modify: `Glacis-Order-Intake.md`

- [ ] **Step 9.1: Write the failing integration test**

Append to `tests/integration/test_orchestrator_emulator.py`:

```python
@pytest.mark.asyncio
@pytest.mark.firestore_emulator
async def test_auto_approve_with_send_stage_writes_sent_at_in_firestore(
    # existing fixtures for the orchestrator emulator harness
):
    """End-to-end: AUTO_APPROVE path with real SendStage + mock GmailClient.

    Verifies that sent_at lands on the persisted OrderRecord in Firestore
    and that gmail_client.send_message is called exactly once.
    """
    from unittest.mock import MagicMock
    from backend.gmail.client import GmailClient

    gmail_client = MagicMock(spec=GmailClient)
    gmail_client.send_message = MagicMock(return_value="integration-gmail-id")

    # Build root agent with the mock Gmail client + send_dry_run=False
    from backend.my_agent.agent import build_root_agent
    root = build_root_agent(
        # existing test deps from the orchestrator emulator fixture
        gmail_client=gmail_client,
        send_dry_run=False,
        # audit_logger=... if Track D present
    )

    # Drive the pipeline — reuse the existing AUTO_APPROVE fixture
    # from test_orchestrator_emulator.py (patterson or MM Machine).
    # Adapt to whatever harness shape exists.
    result = await _run_pipeline_for_patterson_fixture(root)
    assert result.run_summary.orders_created == 1

    # Verify Gmail send happened exactly once
    assert gmail_client.send_message.call_count == 1

    # Verify the Firestore doc has sent_at populated
    doc_id = result.orders[0].source_message_id
    from backend.persistence.orders_store import FirestoreOrderStore
    store = FirestoreOrderStore(emulator_client)  # existing fixture
    order = await store.get(doc_id)
    assert order.sent_at is not None
    assert order.send_error is None
    assert order.processed_by_agent_version == "track-a-v0.3"
```

Flesh out fixture names to match the existing orchestrator-emulator harness. The key assertions are: `send_message` called once, `sent_at != None` on the persisted record, `AGENT_VERSION == "track-a-v0.3"`.

- [ ] **Step 9.2: Start emulator + run the test**

Start emulator:
```bash
firebase emulators:start --only firestore
```

In another terminal:
```bash
export FIRESTORE_EMULATOR_HOST=localhost:8080
uv run pytest tests/integration/test_orchestrator_emulator.py::test_auto_approve_with_send_stage_writes_sent_at_in_firestore -v
```

Expected: green on the first run (after resolving any fixture-name shim).

- [ ] **Step 9.3: Add "Sending (Track A2)" section to `backend/my_agent/README.md`**

Insert as a subsection under the existing "Gmail ingress" section:

```markdown
### Sending (Track A2)

After Track A1 landed, the pipeline writes confirmation bodies
(`OrderRecord.confirmation_body`) and clarification bodies
(`ExceptionRecord.clarify_body`) but doesn't send them anywhere.
Track A2 adds `SendStage` as the 10th pipeline stage — it sends
Gmail replies for both kinds of bodies.

**One-time re-auth** (A2 extends scopes from `gmail.modify` to
`gmail.modify + gmail.send`):

```bash
uv run python scripts/gmail_auth_init.py path/to/credentials.json
```

The consent screen now asks for "Send email" permission. Paste the
new `GMAIL_REFRESH_TOKEN` into `.env` (overwrites the A1 token).

**Dry-run by default:** `.env` has `GMAIL_SEND_DRY_RUN=1`. The
pipeline logs `send_dry_run: would send to X subject Y` instead
of actually sending. Safe to run end-to-end while iterating.

**To actually send:** set `GMAIL_SEND_DRY_RUN=0` in `.env` and
restart `scripts/gmail_poll.py`.

**What to watch for:**

- Log line `send_succeeded` per actual send
- Audit event `email_sent` with Gmail's returned message id
- Gmail "Sent" folder shows the reply threaded under the original
- `OrderRecord.sent_at` or `ExceptionRecord.sent_at` populated in Firestore

**Failure behavior:** a send that fails (Gmail API error, invalid
recipient, etc.) leaves `sent_at=None` and records `send_error` on
the record. Audit event `email_send_failed` fires. Pipeline continues.
Next pipeline invocation of the same envelope re-attempts the send.
```

- [ ] **Step 9.4: Flip Sprint-Status §9 row + Built inventory**

In `research/Order-Intake-Sprint-Status.md`, update the §9 row for Gmail-send capabilities, noting A2 landed.

Add to Built inventory:

```
backend/gmail/client.py (send_message)                                  ✓ Track A2 (<sha-task-2>) — RFC 5322 MIME with reply threading
backend/models/order_record.py (schema v4)                              ✓ Track A2 (<sha-task-3>) — sent_at + send_error
backend/models/exception_record.py (schema v3)                          ✓ Track A2 (<sha-task-4>) — sent_at + send_error
backend/persistence/{base,orders_store,exceptions_store}.py             ✓ Track A2 (<sha-task-5>) — update_with_send_receipt
backend/my_agent/stages/send.py                                         ✓ Track A2 (<sha-task-6>) — 10th BaseAgent, fail-open per entry
backend/my_agent/agent.py (10-stage wiring, AGENT_VERSION v0.3)         ✓ Track A2 (<sha-task-7>)
```

- [ ] **Step 9.5: Flip §9 Gmail-send bullet in `Glacis-Order-Intake.md`**

Find `[Post-MVP] **Gmail API send integration** — actually puts the email on the wire.` and flip:

```markdown
- `[MVP ✓]` **Gmail API send integration** — `SendStage` (10th BaseAgent) calls `GmailClient.send_message` for every AUTO_APPROVE `confirmation_body` and CLARIFY `clarify_body`. Threading via RFC 5322 `In-Reply-To` + `References` headers (Gmail auto-threads). `sent_at` + `send_error` fields on both records for idempotency + observability. Fail-open on errors; `GMAIL_SEND_DRY_RUN=1` toggle for dev. `AGENT_VERSION` bumped `track-a-v0.2` → `v0.3`. MVP: Track A2 landed 2026-04-24 (<sha-task-7>). Source: `Email-Ingestion.md`.
```

The `[Post-MVP]` bullet for `Gemini quality-gate check on outbound email` stays — Track B.

- [ ] **Step 9.6: Commit docs**

```bash
git add backend/my_agent/README.md research/Order-Intake-Sprint-Status.md Glacis-Order-Intake.md tests/integration/test_orchestrator_emulator.py
git commit -m "docs(track-a2): flip Gmail-send to [MVP ✓] across status + Glacis + README + integration"
```

---

## Task 10: Final verification

- [ ] **Step 10.1: Full unit suite — no regressions**

Run: `uv run pytest tests/unit -v 2>&1 | tail -15`

Expected: all green. Test count ~390 (baseline after C + D + A1 ≈ 367 + 23 new A2).

- [ ] **Step 10.2: Full integration suite**

With emulator running + `FIRESTORE_EMULATOR_HOST=localhost:8080`:

Run: `uv run pytest tests/integration -v 2>&1 | tail -15`

Expected: all green. Previously-gated live tests remain auto-skipped.

- [ ] **Step 10.3: Live smoke (optional)**

Re-run `gmail_auth_init.py` to regenerate the refresh token with A2 scopes. Paste into `.env`. Set `GMAIL_SEND_DRY_RUN=0`.

Send a fixture-equivalent email to the agent's Gmail address. Run `scripts/gmail_poll.py`. Observe:

- `send_succeeded` log line
- A reply appears in the agent's Gmail "Sent" folder, threaded under the original
- Firestore shows the `OrderRecord.sent_at` populated
- Audit_log (Track D) shows `email_sent` event with Gmail message id

Immediately re-send the same original email:
- SendStage sees `sent_at != None` → skips → audit `email_send_skipped reason=already_sent`
- No second reply arrives

Flip `GMAIL_SEND_DRY_RUN=1`, send another fresh email:
- Pipeline completes
- Logs show `send_dry_run: would send to X`
- No real email sent
- `sent_at` stays None on the record
- Audit `email_send_dry_run` fires

- [ ] **Step 10.4: Done**

Track A2 closed. Next session picks up Track A3 (push-based ingestion) via brainstorm → spec → plan → execute.

---

## Self-review

**Spec coverage:**
- ✅ Decision 1 (SendStage at position #10) → Tasks 6, 7
- ✅ Decision 2 (RFC 5322 header threading) → Task 2 send_message + Task 6 references construction in `_audited_run`
- ✅ Decision 3 (sent_at + send_error idempotency) → Tasks 3, 4, 5, 6
- ✅ Decision 4 (fail-open on send errors) → Task 6 per-entry try/except + test #8 `test_send_failure_records_error_and_continues`
- ✅ Decision 5 (GMAIL_SEND_DRY_RUN dry-run) → Task 6 `dry_run` flag + Task 7 env read + Task 6 test #7 `test_dry_run_logs_but_does_not_send_or_update`
- ✅ A2_SCOPES constant → Task 1
- ✅ Scope re-auth via gmail_auth_init.py A1_SCOPES → A2_SCOPES → Task 8.1
- ✅ Poller uses A2_SCOPES → Task 8.2
- ✅ AGENT_VERSION bump → Task 7.3
- ✅ `gmail_client=None` no-op path → Task 6 test #1 `test_noop_when_gmail_client_is_none`
- ✅ Reply-handled short-circuit → Task 6 test #2
- ✅ Missing-recipient handling → Task 6 test #9
- ✅ Operator README → Task 9.3
- ✅ Status + Glacis flips → Tasks 9.4, 9.5
- ✅ Integration test → Task 9.1

**Placeholder scan:**
- Task 3.1 `_build_valid_order_record` helper references "find the nearest existing OrderRecord(...) call and replicate its kwargs" — concrete enough for a skilled developer, acceptable.
- Task 6.1 test fixture `_exception_result_entry` has a note "ExceptionRecord may store contact differently; adjust per the actual schema" — flagged inline to let the executor handle schema-specific wiring without fabricating field names that may not exist. Acceptable per "skilled developer" framing.
- Task 9.1 integration test references `_run_pipeline_for_patterson_fixture(root)` — this helper exists in the Track A/D end-to-end test shape per the orchestrator-emulator harness. The step prose explicitly says "adapt to whatever harness shape exists." Acceptable.
- Task 9.4 and 9.5 use `<sha-task-N>` SHA placeholders — standard pattern used in Track C / Track D plans, acceptable.
- No `TBD` / `TODO` / `fill in` anywhere.

**Type consistency:**
- `send_message(to, subject, body_text, in_reply_to, references) → str (gmail_id)` — consistent Task 2 impl + tests + Task 6 call sites.
- `update_with_send_receipt(source_message_id, sent_at, send_error) → None` — consistent Task 5 Protocol + impl + tests + Task 6 call sites.
- `SendStage(gmail_client, order_store, exception_store, dry_run, audit_logger)` — consistent Task 6 + Task 7.
- `sent_at: Optional[datetime]` + `send_error: Optional[str]` — consistent Tasks 3, 4, 5, 6.
- `A2_SCOPES = A1_SCOPES + [GMAIL_SEND_SCOPE]` — consistent Task 1 + Task 8.
- `AGENT_VERSION = "track-a-v0.3"` — consistent Task 7 + assertion in integration test Task 9.

No inconsistencies.

**Scope check:** 10 tasks, each 3-9 steps, TDD-cycled. Estimated execution: 5-7 hours. Single-plan-sized.

**Dependency check:** Task 6 uses `AuditedStage` from Track D. If Track D hasn't landed, replace with direct `BaseAgent` + inline audit-emit calls per the spec. This is noted in Task 6.4 as a fallback. The plan otherwise stands on its own against Track A1 + Track C (or neither).

No fixes needed inline.
