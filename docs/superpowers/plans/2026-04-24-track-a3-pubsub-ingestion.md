# Track A3 — Pub/Sub Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Gmail-inbox polling (A1) with push-based delivery via `users.watch()` → Cloud Pub/Sub → Pub/Sub PULL subscription drained by a long-lived async worker. A1's `scripts/gmail_poll.py` stays as the no-GCP dev loop; A3's new `scripts/gmail_pubsub_worker.py` is the production-shape entrypoint. Background watch renewal + stale-cursor recovery + in-Firestore historyId cursor.

**Architecture:** New `backend/gmail/{watch,history,pubsub_worker}.py` modules + new `backend/persistence/sync_state_store.py`. Reuses A1's `GmailClient` + `gmail_message_to_envelope` + `apply_label` flow for per-message processing. `SubscriberAsyncClient.pull()` in an asyncio loop; concurrent `_drain_loop` + `_renew_loop` via `asyncio.gather`. Stale-cursor recovery falls back to A1-style full-scan once, then resumes push.

**Tech Stack:** Python 3.13, `google-cloud-pubsub>=2.23` (`SubscriberAsyncClient` for native async pull), reuses A1's `google-api-python-client` + `google-auth` stack, `google-cloud-firestore` 2.27.0 (async) for cursor store, pytest + pytest-asyncio, `AsyncMock` / `MagicMock` for GCP client doubles, Pub/Sub emulator for gated integration.

**Source spec:** `docs/superpowers/specs/2026-04-24-track-a3-pubsub-ingestion-design.md` (rev `5b604c4`).

---

## File structure

| Path | Responsibility |
|---|---|
| **New** `backend/gmail/watch.py` | `GmailWatch` — thin wrapper: `start`, `stop`, `get_profile_email` |
| **New** `backend/gmail/history.py` | `fetch_new_message_ids(client, start_history_id) → (ids, latest)` + `HistoryIdTooOldError` |
| **New** `backend/gmail/pubsub_worker.py` | `GmailPubSubWorker` — drain + renew loops + per-message processing |
| **New** `backend/persistence/sync_state_store.py` | `GmailSyncStateStore` — Firestore cursor at `gmail_sync_state/{user_email}` |
| **New** `scripts/gmail_pubsub_worker.py` | Long-running entrypoint |
| **New** `scripts/gmail_watch_setup.py` | One-time Pub/Sub topic + subscription + IAM bootstrap |
| **Modified** `pyproject.toml` | +`google-cloud-pubsub>=2.23` |
| **Modified** `.env.example` | +`GMAIL_PUBSUB_*` + `PUBSUB_EMULATOR_HOST` + `GMAIL_WATCH_RENEW_INTERVAL_SECONDS` |
| **New** `tests/unit/test_gmail_watch.py` | 3 tests |
| **New** `tests/unit/test_gmail_history.py` | 4 tests |
| **New** `tests/unit/test_sync_state_store.py` | 3 tests |
| **New** `tests/unit/test_pubsub_worker.py` | 6 tests |
| **New** `tests/integration/test_pubsub_worker_emulator.py` | 1 gated test |
| **Modified** `backend/my_agent/README.md` | +"Push-based ingestion (A3)" section |
| **Modified** `research/Order-Intake-Sprint-Status.md` | §1 row flip + Built inventory |
| **Modified** `Glacis-Order-Intake.md` | §1 watch + Pub/Sub + History API flip |

---

## Task 1: Add `google-cloud-pubsub` dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1.1: Read current deps**

Run: `grep -E 'google-cloud|google-api-python|google-auth' pyproject.toml`

Verify A1 + A2 Gmail deps already present. A3 adds one line.

- [ ] **Step 1.2: Add `google-cloud-pubsub>=2.23`**

In `pyproject.toml`, find `dependencies = [` and insert alphabetically between `google-cloud-firestore` and any later `google-` entries:

```toml
"google-cloud-pubsub>=2.23",
```

- [ ] **Step 1.3: Sync**

Run: `uv sync`

Expected: lock file updates, no conflicts.

- [ ] **Step 1.4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "feat(track-a3): add google-cloud-pubsub dependency"
```

---

## Task 2: `GmailWatch` wrapper

**Files:**
- Create: `backend/gmail/watch.py`
- Create: `tests/unit/test_gmail_watch.py`

- [ ] **Step 2.1: Write the failing tests**

Create `tests/unit/test_gmail_watch.py`:

```python
"""Unit tests for GmailWatch wrapper.

Patches GmailClient._service with a MagicMock so no network.

Spec: docs/superpowers/specs/2026-04-24-track-a3-pubsub-ingestion-design.md
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.asyncio


def _make_watch():
    from backend.gmail.client import GmailClient
    from backend.gmail.watch import GmailWatch

    gmail_client = MagicMock(spec=GmailClient)
    # GmailClient exposes _service as an internal attr; we patch it directly
    gmail_client._service = MagicMock()
    watch = GmailWatch(gmail_client)
    return watch, gmail_client._service


class TestGmailWatch:
    async def test_start_calls_users_watch_with_topic_and_labels(self):
        watch, svc = _make_watch()
        svc.users().watch().execute.return_value = {
            "historyId": "12345",
            "expiration": "99999999",
        }

        result = await watch.start(
            topic_name="projects/p/topics/t",
            label_ids=["Label_X"],
        )

        assert result["historyId"] == "12345"
        call_kwargs = svc.users().watch.call_args.kwargs
        assert call_kwargs["userId"] == "me"
        assert call_kwargs["body"]["topicName"] == "projects/p/topics/t"
        assert call_kwargs["body"]["labelIds"] == ["Label_X"]

    async def test_start_omits_label_ids_when_none(self):
        watch, svc = _make_watch()
        svc.users().watch().execute.return_value = {"historyId": "1", "expiration": "2"}

        await watch.start(topic_name="projects/p/topics/t", label_ids=None)

        call_kwargs = svc.users().watch.call_args.kwargs
        assert "labelIds" not in call_kwargs["body"]

    async def test_get_profile_email_returns_email_address(self):
        watch, svc = _make_watch()
        svc.users().getProfile().execute.return_value = {
            "emailAddress": "agent@example.com",
            "historyId": "xyz",
        }

        result = await watch.get_profile_email()

        assert result == "agent@example.com"
```

- [ ] **Step 2.2: Run — expect failure**

Run: `uv run pytest tests/unit/test_gmail_watch.py -v`

Expected: `ModuleNotFoundError: backend.gmail.watch`.

- [ ] **Step 2.3: Create `backend/gmail/watch.py`**

```python
"""Gmail users.watch() wrapper for Track A3.

Thin sync wrapper around the Gmail Resource — watch() starts a push
subscription; stop() ends it; get_profile_email() returns the authed
user's email address for cursor keying.

Async methods wrap sync calls via asyncio.to_thread because
googleapiclient is sync-only.

Spec: docs/superpowers/specs/2026-04-24-track-a3-pubsub-ingestion-design.md
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from backend.gmail.client import GmailClient


class GmailWatch:
    def __init__(self, gmail_client: GmailClient) -> None:
        self._gmail = gmail_client

    async def start(
        self,
        *,
        topic_name: str,
        label_ids: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Start or re-assert a watch(). Returns {historyId, expiration}."""
        body: dict[str, Any] = {"topicName": topic_name}
        if label_ids:
            body["labelIds"] = label_ids
        return await asyncio.to_thread(
            lambda: self._gmail._service.users().watch(userId="me", body=body).execute()
        )

    async def stop(self) -> None:
        """End the current watch(). Optional — watch expires in 7 days anyway."""
        await asyncio.to_thread(
            lambda: self._gmail._service.users().stop(userId="me").execute()
        )

    async def get_profile_email(self) -> str:
        """users.getProfile(userId='me') → emailAddress."""
        resp = await asyncio.to_thread(
            lambda: self._gmail._service.users().getProfile(userId="me").execute()
        )
        return resp["emailAddress"]


__all__ = ["GmailWatch"]
```

- [ ] **Step 2.4: Run — expect 3 passes**

Run: `uv run pytest tests/unit/test_gmail_watch.py -v`

Expected: `3 passed`.

- [ ] **Step 2.5: Commit**

```bash
git add backend/gmail/watch.py tests/unit/test_gmail_watch.py
git commit -m "feat(track-a3): GmailWatch wrapper for users.watch / stop / getProfile"
```

---

## Task 3: `fetch_new_message_ids` + `HistoryIdTooOldError`

**Files:**
- Create: `backend/gmail/history.py`
- Create: `tests/unit/test_gmail_history.py`

- [ ] **Step 3.1: Write the failing tests**

Create `tests/unit/test_gmail_history.py`:

```python
"""Unit tests for Gmail History API sync.

Spec: docs/superpowers/specs/2026-04-24-track-a3-pubsub-ingestion-design.md
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.asyncio


def _make_client_with_history_responses(pages):
    """Build a MagicMock GmailClient whose history().list returns the
    given sequence of page dicts."""
    from backend.gmail.client import GmailClient

    gmail_client = MagicMock(spec=GmailClient)
    gmail_client._service = MagicMock()

    execute_returns = iter(pages)

    def _history_list(**kwargs):
        m = MagicMock()
        m.execute.return_value = next(execute_returns)
        return m

    gmail_client._service.users().history().list = _history_list
    return gmail_client


class _FakeHttpError(Exception):
    """Mimics googleapiclient.errors.HttpError enough for status detection."""

    def __init__(self, status: int) -> None:
        super().__init__(f"http {status}")
        self.resp = MagicMock()
        self.resp.status = status


class TestFetchNewMessageIds:
    async def test_collects_message_ids_across_pages(self):
        from backend.gmail.history import fetch_new_message_ids

        gmail_client = _make_client_with_history_responses([
            {
                "history": [
                    {"id": "101", "messagesAdded": [{"message": {"id": "m1"}}]},
                    {"id": "102", "messagesAdded": [{"message": {"id": "m2"}}]},
                ],
                "nextPageToken": "tok1",
            },
            {
                "history": [
                    {"id": "103", "messagesAdded": [{"message": {"id": "m3"}}]},
                ],
            },
        ])

        ids, latest = await fetch_new_message_ids(
            gmail_client, start_history_id="100"
        )

        assert ids == ["m1", "m2", "m3"]
        assert latest == "103"

    async def test_returns_latest_history_id_across_pages(self):
        from backend.gmail.history import fetch_new_message_ids

        gmail_client = _make_client_with_history_responses([
            {"history": [{"id": "500"}]},
        ])

        ids, latest = await fetch_new_message_ids(
            gmail_client, start_history_id="100"
        )

        assert ids == []
        assert latest == "500"

    async def test_dedupes_message_ids_across_overlapping_pages(self):
        from backend.gmail.history import fetch_new_message_ids

        gmail_client = _make_client_with_history_responses([
            {
                "history": [
                    {"id": "101", "messagesAdded": [{"message": {"id": "m1"}}]},
                    {"id": "102", "messagesAdded": [{"message": {"id": "m1"}}]},
                ],
            },
        ])

        ids, _ = await fetch_new_message_ids(
            gmail_client, start_history_id="100"
        )

        assert ids == ["m1"]

    async def test_raises_history_id_too_old_on_404(self):
        from backend.gmail.history import fetch_new_message_ids, HistoryIdTooOldError

        gmail_client = MagicMock()
        gmail_client._service = MagicMock()

        def _raising_list(**kwargs):
            m = MagicMock()
            m.execute.side_effect = _FakeHttpError(404)
            return m

        gmail_client._service.users().history().list = _raising_list

        with pytest.raises(HistoryIdTooOldError):
            await fetch_new_message_ids(gmail_client, start_history_id="too-old")
```

- [ ] **Step 3.2: Run — expect failure**

Run: `uv run pytest tests/unit/test_gmail_history.py -v`

Expected: `ModuleNotFoundError: backend.gmail.history`.

- [ ] **Step 3.3: Create `backend/gmail/history.py`**

```python
"""Gmail History API sync for Track A3.

Given a starting historyId, walks users.history.list pages and
returns (new message ids, latest historyId observed). Raises
HistoryIdTooOldError when Gmail returns 404 (cursor older than
the service's retention window); caller falls back to full-scan.

Spec: docs/superpowers/specs/2026-04-24-track-a3-pubsub-ingestion-design.md
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from backend.gmail.client import GmailClient


class HistoryIdTooOldError(Exception):
    """Raised when users.history.list returns 404 — historyId stale."""


async def fetch_new_message_ids(
    gmail_client: GmailClient,
    *,
    start_history_id: str,
    max_pages: int = 20,
) -> tuple[list[str], str]:
    """Walk history pages; return (new_message_ids, latest_history_id).

    Only collects messagesAdded[*].message.id. latest_history_id is
    the maximum historyId seen across all pages; caller persists it
    as the next cursor. Bounded by max_pages (default 20) to cap work
    when a long outage accumulates history.
    """
    new_ids: list[str] = []
    latest_id = start_history_id
    page_token: Optional[str] = None
    pages = 0

    svc = gmail_client._service

    def _page(token: Optional[str]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "userId": "me",
            "startHistoryId": start_history_id,
        }
        if token:
            kwargs["pageToken"] = token
        return svc.users().history().list(**kwargs).execute()

    try:
        while pages < max_pages:
            resp = await asyncio.to_thread(_page, page_token)

            for entry in resp.get("history", []):
                entry_id = entry.get("id")
                if entry_id and entry_id > latest_id:
                    latest_id = entry_id
                for added in entry.get("messagesAdded", []):
                    msg = added.get("message", {})
                    mid = msg.get("id")
                    if mid:
                        new_ids.append(mid)

            page_token = resp.get("nextPageToken")
            pages += 1
            if not page_token:
                break
    except Exception as exc:
        status = getattr(getattr(exc, "resp", None), "status", None)
        if status == 404:
            raise HistoryIdTooOldError(
                f"startHistoryId={start_history_id} is no longer available"
            ) from exc
        raise

    # Dedup while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for mid in new_ids:
        if mid not in seen:
            seen.add(mid)
            deduped.append(mid)
    return deduped, latest_id


__all__ = ["HistoryIdTooOldError", "fetch_new_message_ids"]
```

- [ ] **Step 3.4: Run — expect 4 passes**

Run: `uv run pytest tests/unit/test_gmail_history.py -v`

Expected: `4 passed`.

- [ ] **Step 3.5: Commit**

```bash
git add backend/gmail/history.py tests/unit/test_gmail_history.py
git commit -m "feat(track-a3): fetch_new_message_ids + HistoryIdTooOldError"
```

---

## Task 4: `GmailSyncStateStore`

**Files:**
- Create: `backend/persistence/sync_state_store.py`
- Create: `tests/unit/test_sync_state_store.py`

- [ ] **Step 4.1: Write the failing tests**

Create `tests/unit/test_sync_state_store.py`:

```python
"""Unit tests for GmailSyncStateStore.

Reuses the FakeAsyncClient fixture from conftest.py (same shape Track C
extended). Round-trips through the `gmail_sync_state` collection.

Spec: docs/superpowers/specs/2026-04-24-track-a3-pubsub-ingestion-design.md
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


class TestGmailSyncStateStore:
    async def test_get_cursor_returns_none_for_missing_doc(self, fake_client):
        from backend.persistence.sync_state_store import GmailSyncStateStore

        store = GmailSyncStateStore(fake_client)
        result = await store.get_cursor("new-user@example.com")

        assert result is None

    async def test_set_cursor_then_get_returns_history_id(self, fake_client):
        from backend.persistence.sync_state_store import GmailSyncStateStore

        store = GmailSyncStateStore(fake_client)
        await store.set_cursor("user@example.com", "history-12345")
        result = await store.get_cursor("user@example.com")

        assert result == "history-12345"

    async def test_set_cursor_upserts_existing_doc(self, fake_client):
        from backend.persistence.sync_state_store import GmailSyncStateStore

        store = GmailSyncStateStore(fake_client)
        await store.set_cursor("user@example.com", "history-1")
        await store.set_cursor("user@example.com", "history-2")
        result = await store.get_cursor("user@example.com")

        assert result == "history-2"
```

- [ ] **Step 4.2: Run — expect failure**

Run: `uv run pytest tests/unit/test_sync_state_store.py -v`

Expected: `ModuleNotFoundError: backend.persistence.sync_state_store`.

- [ ] **Step 4.3: Create `backend/persistence/sync_state_store.py`**

```python
"""Firestore-backed cursor store for Gmail History API sync (Track A3).

One doc per authed inbox at gmail_sync_state/{user_email}.
Schema: {history_id: str, updated_at: SERVER_TIMESTAMP, user_email: str}.

Spec: docs/superpowers/specs/2026-04-24-track-a3-pubsub-ingestion-design.md
"""
from __future__ import annotations

from typing import Optional

from google.cloud.firestore_v1 import SERVER_TIMESTAMP
from google.cloud.firestore_v1.async_client import AsyncClient


class GmailSyncStateStore:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client
        self._collection = "gmail_sync_state"

    async def get_cursor(self, user_email: str) -> Optional[str]:
        doc_ref = self._client.collection(self._collection).document(user_email)
        snap = await doc_ref.get()
        if not snap.exists:
            return None
        data = snap.to_dict() or {}
        hid = data.get("history_id")
        return hid if isinstance(hid, str) else None

    async def set_cursor(self, user_email: str, history_id: str) -> None:
        doc_ref = self._client.collection(self._collection).document(user_email)
        await doc_ref.set(
            {
                "history_id": history_id,
                "updated_at": SERVER_TIMESTAMP,
                "user_email": user_email,
            },
            merge=False,
        )


__all__ = ["GmailSyncStateStore"]
```

- [ ] **Step 4.4: Run — expect 3 passes**

Run: `uv run pytest tests/unit/test_sync_state_store.py -v`

Expected: `3 passed`. If `fake_client` fixture isn't available, check Track A1 conftest and confirm the fixture exists (Track A1 Task 4 added it).

- [ ] **Step 4.5: Commit**

```bash
git add backend/persistence/sync_state_store.py tests/unit/test_sync_state_store.py
git commit -m "feat(track-a3): GmailSyncStateStore for historyId cursor persistence"
```

---

## Task 5: `GmailPubSubWorker`

**Files:**
- Create: `backend/gmail/pubsub_worker.py`
- Create: `tests/unit/test_pubsub_worker.py`

- [ ] **Step 5.1: Write the failing tests**

Create `tests/unit/test_pubsub_worker.py`:

```python
"""Unit tests for GmailPubSubWorker.

Uses AsyncMock for async collaborators + MagicMock for sync ones.
No network, no emulator — just orchestration logic under test.

Spec: docs/superpowers/specs/2026-04-24-track-a3-pubsub-ingestion-design.md
"""
from __future__ import annotations

import asyncio
import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio


async def _make_worker(*, cursor="hist-100", label_id="Label_X"):
    from backend.gmail.client import GmailClient
    from backend.gmail.pubsub_worker import GmailPubSubWorker
    from backend.gmail.watch import GmailWatch
    from backend.persistence.sync_state_store import GmailSyncStateStore

    subscriber = AsyncMock()
    gmail_client = MagicMock(spec=GmailClient)
    gmail_client.label_id_for = MagicMock(return_value=label_id)
    gmail_client.get_raw = MagicMock(return_value=b"From: a\r\n\r\nhi")
    gmail_client.apply_label = MagicMock()
    gmail_client.list_unprocessed = MagicMock(return_value=["fallback-m1"])

    runner = AsyncMock()

    async def _empty_stream(**kw):
        if False:
            yield None  # pragma: no cover

    runner.run_async = MagicMock(side_effect=lambda **kw: _empty_stream())

    session_service = AsyncMock()
    session_service.create_session = AsyncMock()

    sync_state_store = AsyncMock(spec=GmailSyncStateStore)
    sync_state_store.get_cursor = AsyncMock(return_value=cursor)
    sync_state_store.set_cursor = AsyncMock()

    watch = AsyncMock(spec=GmailWatch)
    watch.get_profile_email = AsyncMock(return_value="me@example.com")
    watch.start = AsyncMock(return_value={"historyId": "500", "expiration": "9999999"})

    worker = GmailPubSubWorker(
        subscriber=subscriber,
        subscription_path="projects/p/subscriptions/s",
        gmail_client=gmail_client,
        runner=runner,
        session_service=session_service,
        sync_state_store=sync_state_store,
        watch=watch,
        topic_name="projects/p/topics/t",
        watch_label_ids=None,
        watch_renew_interval_seconds=3600,
        max_messages_per_pull=10,
        label_name="orderintake-processed",
    )
    return worker, subscriber, gmail_client, runner, sync_state_store, watch


def _push_payload(history_id: str) -> bytes:
    return json.dumps({"emailAddress": "me@example.com", "historyId": history_id}).encode()


class _FakePubsubMessage:
    def __init__(self, data: bytes, message_id: str = "pm-1"):
        self.data = data
        self.message_id = message_id


class TestInit:
    async def test_init_resolves_email_label_and_starts_watch(self):
        worker, subscriber, gmail_client, runner, cursor, watch = await _make_worker()
        await worker._init()

        watch.get_profile_email.assert_awaited_once()
        gmail_client.label_id_for.assert_called_once_with("orderintake-processed")
        watch.start.assert_awaited_once()


class TestProcessPubsubMessage:
    async def test_fresh_payload_fetches_history_processes_and_advances_cursor(
        self, monkeypatch
    ):
        worker, subscriber, gmail_client, runner, cursor_store, watch = await _make_worker()
        await worker._init()

        from backend.gmail import pubsub_worker as worker_module

        monkeypatch.setattr(
            worker_module,
            "fetch_new_message_ids",
            AsyncMock(return_value=(["m1", "m2"], "hist-500")),
        )
        # Stub the adapter to avoid real parse_eml
        monkeypatch.setattr(
            worker_module,
            "gmail_message_to_envelope",
            AsyncMock(return_value=MagicMock(message_id="<msg@x>")),
        )

        await worker._process_pubsub_message(_FakePubsubMessage(_push_payload("hist-500")))

        # Both messages processed
        assert gmail_client.get_raw.call_count == 2
        assert gmail_client.apply_label.call_count == 2
        # Cursor advanced to latest historyId
        cursor_store.set_cursor.assert_awaited_once_with("me@example.com", "hist-500")

    async def test_stale_cursor_triggers_full_scan_fallback(self, monkeypatch):
        from backend.gmail.history import HistoryIdTooOldError
        worker, subscriber, gmail_client, runner, cursor_store, watch = await _make_worker()
        await worker._init()

        from backend.gmail import pubsub_worker as worker_module

        monkeypatch.setattr(
            worker_module,
            "fetch_new_message_ids",
            AsyncMock(side_effect=HistoryIdTooOldError("too old")),
        )
        monkeypatch.setattr(
            worker_module,
            "gmail_message_to_envelope",
            AsyncMock(return_value=MagicMock(message_id="<msg@x>")),
        )

        await worker._process_pubsub_message(_FakePubsubMessage(_push_payload("hist-999")))

        # Full-scan fallback was used (list_unprocessed returns ["fallback-m1"])
        gmail_client.list_unprocessed.assert_called_once_with(label_name="orderintake-processed")
        gmail_client.get_raw.assert_called_with("fallback-m1")
        # Cursor advanced to push payload's historyId
        cursor_store.set_cursor.assert_awaited_once_with("me@example.com", "hist-999")

    async def test_empty_history_still_advances_cursor(self, monkeypatch):
        worker, subscriber, gmail_client, runner, cursor_store, watch = await _make_worker()
        await worker._init()

        from backend.gmail import pubsub_worker as worker_module

        monkeypatch.setattr(
            worker_module,
            "fetch_new_message_ids",
            AsyncMock(return_value=([], "hist-200")),
        )

        await worker._process_pubsub_message(_FakePubsubMessage(_push_payload("hist-200")))

        gmail_client.get_raw.assert_not_called()
        cursor_store.set_cursor.assert_awaited_once_with("me@example.com", "hist-200")


class TestRenewLoop:
    async def test_renew_loop_calls_watch_start_periodically(self):
        worker, *_rest, watch = await _make_worker()
        # Use a 0.01s interval so the test runs fast
        worker._renew_interval = 0.01

        task = asyncio.create_task(worker._renew_loop())
        await asyncio.sleep(0.05)  # let it fire ~4 times
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert watch.start.await_count >= 2


class TestRunForever:
    async def test_exits_on_cancellation(self, monkeypatch):
        worker, subscriber, *_rest = await _make_worker()

        # Make pull return empty response list each call
        empty_resp = MagicMock()
        empty_resp.received_messages = []
        subscriber.pull = AsyncMock(return_value=empty_resp)

        task = asyncio.create_task(worker.run_forever())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
```

- [ ] **Step 5.2: Run — expect failure**

Run: `uv run pytest tests/unit/test_pubsub_worker.py -v`

Expected: `ModuleNotFoundError: backend.gmail.pubsub_worker`.

- [ ] **Step 5.3: Create `backend/gmail/pubsub_worker.py`**

```python
"""Long-lived Pub/Sub PULL worker for Gmail push ingestion (Track A3).

Concurrent drain loop + watch-renewal loop. Drain pulls up to N
messages per cycle, processes sequentially, acks after pipeline
completes. Renew calls users.watch() every 24h.

Spec: docs/superpowers/specs/2026-04-24-track-a3-pubsub-ingestion-design.md
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Optional

from google.adk.runners import Runner
from google.adk.sessions import BaseSessionService
from google.cloud.pubsub_v1 import SubscriberAsyncClient
from google.genai import types

from backend.gmail.adapter import gmail_message_to_envelope
from backend.gmail.client import GmailClient
from backend.gmail.history import HistoryIdTooOldError, fetch_new_message_ids
from backend.gmail.watch import GmailWatch
from backend.persistence.sync_state_store import GmailSyncStateStore
from backend.utils.logging import get_logger

_log = get_logger(__name__)


class GmailPubSubWorker:
    def __init__(
        self,
        *,
        subscriber: SubscriberAsyncClient,
        subscription_path: str,
        gmail_client: GmailClient,
        runner: Runner,
        session_service: BaseSessionService,
        sync_state_store: GmailSyncStateStore,
        watch: GmailWatch,
        topic_name: str,
        watch_label_ids: Optional[list[str]] = None,
        watch_renew_interval_seconds: int = 86400,
        max_messages_per_pull: int = 10,
        label_name: str = "orderintake-processed",
        app_name: str = "order_intake",
        user_id: str = "gmail_pubsub_worker",
    ) -> None:
        self._subscriber = subscriber
        self._subscription_path = subscription_path
        self._gmail = gmail_client
        self._runner = runner
        self._sessions = session_service
        self._cursor_store = sync_state_store
        self._watch = watch
        self._topic_name = topic_name
        self._watch_label_ids = watch_label_ids
        self._renew_interval = watch_renew_interval_seconds
        self._max_messages = max_messages_per_pull
        self._label_name = label_name
        self._app_name = app_name
        self._user_id = user_id

        self._user_email: Optional[str] = None
        self._label_id: Optional[str] = None

    async def run_forever(self) -> None:
        await self._init()
        try:
            await asyncio.gather(self._drain_loop(), self._renew_loop())
        except asyncio.CancelledError:
            _log.info("gmail_pubsub_worker_stopping")
            raise

    async def _init(self) -> None:
        self._user_email = await self._watch.get_profile_email()
        self._label_id = await asyncio.to_thread(
            self._gmail.label_id_for, self._label_name
        )
        result = await self._watch.start(
            topic_name=self._topic_name,
            label_ids=self._watch_label_ids,
        )
        _log.info(
            "gmail_watch_started",
            history_id=result.get("historyId"),
            expiration=result.get("expiration"),
            user=self._user_email,
        )

    async def _drain_loop(self) -> None:
        while True:
            try:
                resp = await self._subscriber.pull(
                    request={
                        "subscription": self._subscription_path,
                        "max_messages": self._max_messages,
                    }
                )
                ack_ids: list[str] = []
                for received in resp.received_messages:
                    try:
                        await self._process_pubsub_message(received.message)
                        ack_ids.append(received.ack_id)
                    except Exception as exc:
                        _log.error(
                            "pubsub_message_failed",
                            error=str(exc),
                            message_id=received.message.message_id,
                        )
                if ack_ids:
                    await self._subscriber.acknowledge(
                        request={
                            "subscription": self._subscription_path,
                            "ack_ids": ack_ids,
                        }
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _log.error("pubsub_pull_failed", error=str(exc))
                await asyncio.sleep(5.0)

    async def _renew_loop(self) -> None:
        while True:
            await asyncio.sleep(self._renew_interval)
            try:
                result = await self._watch.start(
                    topic_name=self._topic_name,
                    label_ids=self._watch_label_ids,
                )
                _log.info(
                    "gmail_watch_renewed",
                    history_id=result.get("historyId"),
                    expiration=result.get("expiration"),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _log.error("gmail_watch_renew_failed", error=str(exc))

    async def _process_pubsub_message(self, pubsub_message) -> None:
        payload = json.loads(pubsub_message.data.decode("utf-8"))
        history_id_from_push = str(payload.get("historyId", ""))

        stored = await self._cursor_store.get_cursor(self._user_email or "")
        start_id = stored or history_id_from_push

        try:
            new_ids, latest_id = await fetch_new_message_ids(
                self._gmail, start_history_id=start_id
            )
        except HistoryIdTooOldError:
            _log.info(
                "gmail_history_id_stale",
                start_id=start_id,
                fallback="full_scan",
            )
            new_ids = await asyncio.to_thread(
                self._gmail.list_unprocessed, label_name=self._label_name
            )
            latest_id = history_id_from_push

        for message_id in new_ids:
            await self._process_gmail_message(message_id)

        await self._cursor_store.set_cursor(self._user_email or "", latest_id)

    async def _process_gmail_message(self, message_id: str) -> None:
        try:
            raw_bytes = await asyncio.to_thread(self._gmail.get_raw, message_id)
            envelope = await gmail_message_to_envelope(raw_bytes)
            session_id = uuid.uuid4().hex
            await self._sessions.create_session(
                app_name=self._app_name,
                user_id=self._user_id,
                session_id=session_id,
            )
            new_message = types.Content(
                role="user",
                parts=[
                    types.Part.from_text(
                        text=raw_bytes.decode("utf-8", errors="replace")
                    )
                ],
            )
            async for _ in self._runner.run_async(
                user_id=self._user_id,
                session_id=session_id,
                new_message=new_message,
            ):
                pass
            await asyncio.to_thread(
                self._gmail.apply_label, message_id, self._label_id
            )
            _log.info(
                "gmail_message_processed",
                gmail_id=message_id,
                source_message_id=envelope.message_id,
            )
        except Exception as exc:
            _log.error("gmail_message_failed", gmail_id=message_id, error=str(exc))


__all__ = ["GmailPubSubWorker"]
```

- [ ] **Step 5.4: Run — expect 6 passes**

Run: `uv run pytest tests/unit/test_pubsub_worker.py -v`

Expected: `6 passed`. If a test fails on `_FakePubsubMessage` not matching the real Pub/Sub message API closely enough, inspect the error and adjust the fake to match what `pubsub_message.data` / `pubsub_message.message_id` look like in the real `SubscriberAsyncClient.pull` response (both are simple string/bytes attributes).

- [ ] **Step 5.5: Commit**

```bash
git add backend/gmail/pubsub_worker.py tests/unit/test_pubsub_worker.py
git commit -m "feat(track-a3): GmailPubSubWorker with drain + renew loops"
```

---

## Task 6: `scripts/gmail_watch_setup.py` one-time bootstrap

**Files:**
- Create: `scripts/gmail_watch_setup.py`

No automated tests — this is an interactive ops script. Library calls (`create_topic`, `create_subscription`, `set_iam_policy`) are covered by `google-cloud-pubsub`'s own test suite.

- [ ] **Step 6.1: Create the script**

```python
#!/usr/bin/env python3
"""Create Pub/Sub topic + PULL subscription + grant Gmail publisher role.

Usage:
    uv run python scripts/gmail_watch_setup.py \\
        --project demo-order-intake-local \\
        --topic gmail-inbox-events \\
        --subscription order-intake-ingestion

Idempotent — safe to re-run. Grants gmail-api-push@system.gserviceaccount
.com the pubsub.publisher role on the topic (Gmail requires this to
deliver notifications).

Spec: docs/superpowers/specs/2026-04-24-track-a3-pubsub-ingestion-design.md
"""
from __future__ import annotations

import argparse
import sys

from google.api_core.exceptions import AlreadyExists
from google.cloud import pubsub_v1


GMAIL_SERVICE_ACCOUNT = "serviceAccount:gmail-api-push@system.gserviceaccount.com"
PUBLISHER_ROLE = "roles/pubsub.publisher"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--subscription", required=True)
    args = parser.parse_args()

    publisher = pubsub_v1.PublisherClient()
    subscriber = pubsub_v1.SubscriberClient()

    topic_path = publisher.topic_path(args.project, args.topic)
    subscription_path = subscriber.subscription_path(args.project, args.subscription)

    # 1. Create topic
    try:
        publisher.create_topic(request={"name": topic_path})
        print(f"created topic: {topic_path}")
    except AlreadyExists:
        print(f"topic already exists: {topic_path}")

    # 2. Grant Gmail service account publisher role
    policy = publisher.get_iam_policy(request={"resource": topic_path})
    has_role = any(
        b.role == PUBLISHER_ROLE and GMAIL_SERVICE_ACCOUNT in b.members
        for b in policy.bindings
    )
    if not has_role:
        from google.iam.v1 import policy_pb2
        binding = policy_pb2.Binding(
            role=PUBLISHER_ROLE, members=[GMAIL_SERVICE_ACCOUNT]
        )
        policy.bindings.append(binding)
        publisher.set_iam_policy(
            request={"resource": topic_path, "policy": policy}
        )
        print(f"granted {PUBLISHER_ROLE} to {GMAIL_SERVICE_ACCOUNT}")
    else:
        print(f"{GMAIL_SERVICE_ACCOUNT} already has {PUBLISHER_ROLE}")

    # 3. Create PULL subscription
    try:
        subscriber.create_subscription(
            request={"name": subscription_path, "topic": topic_path}
        )
        print(f"created subscription: {subscription_path}")
    except AlreadyExists:
        print(f"subscription already exists: {subscription_path}")

    print()
    print("Next: set these in .env and run scripts/gmail_pubsub_worker.py")
    print(f"  GMAIL_PUBSUB_PROJECT_ID={args.project}")
    print(f"  GMAIL_PUBSUB_TOPIC={args.topic}")
    print(f"  GMAIL_PUBSUB_SUBSCRIPTION={args.subscription}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6.2: Syntax-check**

Run: `uv run python -c "import ast; ast.parse(open('scripts/gmail_watch_setup.py').read())"`

Expected: no output.

- [ ] **Step 6.3: Commit**

```bash
git add scripts/gmail_watch_setup.py
git commit -m "feat(track-a3): one-time Pub/Sub topic + subscription + IAM bootstrap"
```

---

## Task 7: `scripts/gmail_pubsub_worker.py` long-running entrypoint

**Files:**
- Create: `scripts/gmail_pubsub_worker.py`
- Modify: `.env.example`

- [ ] **Step 7.1: Create the polling script**

Create `scripts/gmail_pubsub_worker.py`:

```python
#!/usr/bin/env python3
"""Gmail Pub/Sub PULL worker (Track A3).

Usage:
    uv run python scripts/gmail_pubsub_worker.py

Reads env:
  GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN (required)
  GMAIL_PROCESSED_LABEL (optional, default 'orderintake-processed')
  GMAIL_PUBSUB_PROJECT_ID (required)
  GMAIL_PUBSUB_TOPIC (required)
  GMAIL_PUBSUB_SUBSCRIPTION (required)
  GMAIL_WATCH_RENEW_INTERVAL_SECONDS (optional, default 86400)
  PUBSUB_EMULATOR_HOST (optional — auto-used when set)
  FIRESTORE_EMULATOR_HOST, GOOGLE_API_KEY, LLAMA_CLOUD_API_KEY (pipeline)

Spec: docs/superpowers/specs/2026-04-24-track-a3-pubsub-ingestion-design.md
"""
from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.cloud.pubsub_v1 import SubscriberAsyncClient

from backend.gmail.client import GmailClient
from backend.gmail.pubsub_worker import GmailPubSubWorker
from backend.gmail.scopes import A2_SCOPES
from backend.gmail.watch import GmailWatch
from backend.my_agent.agent import _build_default_root_agent
from backend.persistence.sync_state_store import GmailSyncStateStore
from backend.tools.order_validator.tools.firestore_client import get_async_client


async def _main() -> int:
    load_dotenv()

    required = (
        "GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN",
        "GMAIL_PUBSUB_PROJECT_ID", "GMAIL_PUBSUB_TOPIC", "GMAIL_PUBSUB_SUBSCRIPTION",
    )
    for var in required:
        if not os.environ.get(var):
            print(f"error: {var} missing from env/.env", file=sys.stderr)
            print(
                "hint: run 'uv run python scripts/gmail_watch_setup.py' first "
                "if topic/subscription don't exist; "
                "then 'uv run python scripts/gmail_auth_init.py path/to/credentials.json' "
                "for OAuth.",
                file=sys.stderr,
            )
            return 2

    label_name = os.environ.get("GMAIL_PROCESSED_LABEL", "orderintake-processed")
    renew_interval = int(os.environ.get("GMAIL_WATCH_RENEW_INTERVAL_SECONDS", "86400"))

    gmail_client = GmailClient(
        refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
        client_id=os.environ["GMAIL_CLIENT_ID"],
        client_secret=os.environ["GMAIL_CLIENT_SECRET"],
        scopes=A2_SCOPES,
    )
    watch = GmailWatch(gmail_client)

    firestore_client = get_async_client()
    sync_state_store = GmailSyncStateStore(firestore_client)

    subscriber = SubscriberAsyncClient()
    project_id = os.environ["GMAIL_PUBSUB_PROJECT_ID"]
    topic_name = f"projects/{project_id}/topics/{os.environ['GMAIL_PUBSUB_TOPIC']}"
    subscription_path = (
        f"projects/{project_id}/subscriptions/{os.environ['GMAIL_PUBSUB_SUBSCRIPTION']}"
    )

    root_agent = _build_default_root_agent()
    session_service = InMemorySessionService()
    runner = Runner(
        app_name="order_intake", agent=root_agent, session_service=session_service,
    )

    worker = GmailPubSubWorker(
        subscriber=subscriber,
        subscription_path=subscription_path,
        gmail_client=gmail_client,
        runner=runner,
        session_service=session_service,
        sync_state_store=sync_state_store,
        watch=watch,
        topic_name=topic_name,
        watch_label_ids=None,
        watch_renew_interval_seconds=renew_interval,
        label_name=label_name,
    )

    try:
        await worker.run_forever()
    except asyncio.CancelledError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
```

- [ ] **Step 7.2: Update `.env.example`**

Append:

```
# Track A3: Pub/Sub ingestion
GMAIL_PUBSUB_PROJECT_ID=demo-order-intake-local
GMAIL_PUBSUB_TOPIC=gmail-inbox-events
GMAIL_PUBSUB_SUBSCRIPTION=order-intake-ingestion
GMAIL_WATCH_RENEW_INTERVAL_SECONDS=86400
# PUBSUB_EMULATOR_HOST=localhost:8085   # uncomment for local emulator
```

- [ ] **Step 7.3: Syntax-check**

Run: `uv run python -c "import ast; ast.parse(open('scripts/gmail_pubsub_worker.py').read())"`

Expected: no output.

- [ ] **Step 7.4: Commit**

```bash
git add scripts/gmail_pubsub_worker.py .env.example
git commit -m "feat(track-a3): long-running Pub/Sub worker entrypoint"
```

---

## Task 8: Integration test against emulators

**Files:**
- Create: `tests/integration/test_pubsub_worker_emulator.py`

- [ ] **Step 8.1: Create the gated integration test**

```python
"""Emulator integration smoke test for GmailPubSubWorker (Track A3).

Gated behind @pytest.mark.pubsub_emulator + env checks. Default
CI runs skip. Manual invocation:

    # In separate terminals:
    gcloud beta emulators pubsub start --host-port=localhost:8085
    firebase emulators:start --only firestore

    # Then:
    export PUBSUB_EMULATOR_HOST=localhost:8085
    export FIRESTORE_EMULATOR_HOST=localhost:8080
    uv run pytest -m pubsub_emulator tests/integration/test_pubsub_worker_emulator.py -v

Spec: docs/superpowers/specs/2026-04-24-track-a3-pubsub-ingestion-design.md
"""
from __future__ import annotations

import json
import os
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = [pytest.mark.pubsub_emulator, pytest.mark.asyncio]


def _live_emulators_available() -> bool:
    return bool(
        os.environ.get("PUBSUB_EMULATOR_HOST")
        and os.environ.get("FIRESTORE_EMULATOR_HOST")
    )


@pytest.mark.skipif(
    not _live_emulators_available(),
    reason="PUBSUB_EMULATOR_HOST + FIRESTORE_EMULATOR_HOST not set",
)
async def test_worker_processes_one_pubsub_message_against_emulators():
    from pathlib import Path
    from google.adk.sessions import InMemorySessionService
    from google.cloud import pubsub_v1
    from google.cloud.pubsub_v1 import SubscriberAsyncClient

    from backend.gmail.client import GmailClient
    from backend.gmail.pubsub_worker import GmailPubSubWorker
    from backend.gmail.watch import GmailWatch
    from backend.persistence.sync_state_store import GmailSyncStateStore
    from backend.tools.order_validator.tools.firestore_client import get_async_client

    # Unique project name per test run to avoid cross-test pollution
    project_id = f"a3-test-{uuid.uuid4().hex[:8]}"
    topic_name = "gmail-inbox-events"
    subscription_name = "order-intake-ingestion"

    publisher = pubsub_v1.PublisherClient()
    subscriber_sync = pubsub_v1.SubscriberClient()

    topic_path = publisher.topic_path(project_id, topic_name)
    subscription_path = subscriber_sync.subscription_path(project_id, subscription_name)

    publisher.create_topic(request={"name": topic_path})
    subscriber_sync.create_subscription(
        request={"name": subscription_path, "topic": topic_path}
    )

    # Publish one fake Gmail-notification-shaped message
    payload = json.dumps(
        {"emailAddress": "me@example.com", "historyId": "12345"}
    ).encode()
    publisher.publish(topic_path, payload).result(timeout=10)

    # Mock GmailClient.get_raw to return a fixture .eml as bytes
    fixture_bytes = Path(
        "data/pdf/patterson_po-28491.wrapper.eml"
    ).read_bytes()

    gmail_client = MagicMock(spec=GmailClient)
    gmail_client.label_id_for = MagicMock(return_value="Label_TEST")
    gmail_client.get_raw = MagicMock(return_value=fixture_bytes)
    gmail_client.apply_label = MagicMock()
    gmail_client.list_unprocessed = MagicMock(return_value=[])

    # Mock watch — don't hit real Gmail API
    watch = AsyncMock(spec=GmailWatch)
    watch.get_profile_email = AsyncMock(return_value="me@example.com")
    watch.start = AsyncMock(return_value={"historyId": "500", "expiration": "9"})

    # Mock fetch_new_message_ids to return the fixture message id
    import backend.gmail.pubsub_worker as worker_module
    from backend.gmail.adapter import gmail_message_to_envelope as real_adapter

    orig_fetch = worker_module.fetch_new_message_ids
    async def _fake_fetch(gc, *, start_history_id, max_pages=20):
        return ["fixture-msg-id"], "12346"

    worker_module.fetch_new_message_ids = _fake_fetch

    try:
        firestore_client = get_async_client()
        sync_state_store = GmailSyncStateStore(firestore_client)

        # Build a minimal Runner+root_agent that we can exercise without
        # the full pipeline (which needs seeded master data). For this
        # smoke test, use a stub root_agent that completes immediately.
        root_agent = MagicMock()
        runner = MagicMock()
        async def _empty_stream(**kw):
            if False:
                yield None
        runner.run_async = MagicMock(side_effect=lambda **kw: _empty_stream())

        session_service = InMemorySessionService()

        subscriber = SubscriberAsyncClient()

        worker = GmailPubSubWorker(
            subscriber=subscriber,
            subscription_path=subscription_path,
            gmail_client=gmail_client,
            runner=runner,
            session_service=session_service,
            sync_state_store=sync_state_store,
            watch=watch,
            topic_name=topic_path,
            watch_label_ids=None,
            watch_renew_interval_seconds=999999,  # effectively disabled
            label_name="orderintake-processed",
        )

        await worker._init()

        # One pull cycle
        resp = await subscriber.pull(
            request={"subscription": subscription_path, "max_messages": 10}
        )
        assert len(resp.received_messages) >= 1
        for received in resp.received_messages:
            await worker._process_pubsub_message(received.message)
            await subscriber.acknowledge(
                request={
                    "subscription": subscription_path,
                    "ack_ids": [received.ack_id],
                }
            )

        # Assertions
        gmail_client.get_raw.assert_called_with("fixture-msg-id")
        gmail_client.apply_label.assert_called_with("fixture-msg-id", "Label_TEST")

        cursor = await sync_state_store.get_cursor("me@example.com")
        assert cursor == "12346"
    finally:
        worker_module.fetch_new_message_ids = orig_fetch
```

- [ ] **Step 8.2: Register the `pubsub_emulator` marker**

In `pyproject.toml` under `[tool.pytest.ini_options]` → `markers`, add:

```toml
"pubsub_emulator: requires the Cloud Pub/Sub emulator running locally (set PUBSUB_EMULATOR_HOST=localhost:8085)",
```

- [ ] **Step 8.3: Verify auto-skip in default run**

Run: `uv run pytest tests/integration/test_pubsub_worker_emulator.py -v`

Expected: `1 skipped` (because env vars not set).

- [ ] **Step 8.4: Commit**

```bash
git add tests/integration/test_pubsub_worker_emulator.py pyproject.toml
git commit -m "test(track-a3): gated emulator integration test for PubSub worker"
```

---

## Task 9: Operator documentation + doc flips

**Files:**
- Modify: `backend/my_agent/README.md`
- Modify: `research/Order-Intake-Sprint-Status.md`
- Modify: `Glacis-Order-Intake.md`

- [ ] **Step 9.1: Add "Push-based ingestion (Track A3)" section to `backend/my_agent/README.md`**

Insert after the existing "Sending (Track A2)" subsection:

```markdown
### Push-based ingestion (Track A3)

A3 replaces A1's inbox polling with a Gmail `users.watch()` → Cloud Pub/Sub →
PULL subscription flow. A1's `scripts/gmail_poll.py` remains available as the
no-GCP-infra dev loop.

**One-time infrastructure setup:**

```bash
uv run python scripts/gmail_watch_setup.py \
  --project demo-order-intake-local \
  --topic gmail-inbox-events \
  --subscription order-intake-ingestion
```

Idempotent. Creates the Pub/Sub topic + PULL subscription + grants
Gmail's service account publisher role on the topic.

**Add to `.env`:**

```
GMAIL_PUBSUB_PROJECT_ID=demo-order-intake-local
GMAIL_PUBSUB_TOPIC=gmail-inbox-events
GMAIL_PUBSUB_SUBSCRIPTION=order-intake-ingestion
GMAIL_WATCH_RENEW_INTERVAL_SECONDS=86400
```

OAuth credentials are shared with A1 + A2 (same `GMAIL_*` vars); no re-auth
needed if A2's refresh token is present.

**Run the worker:**

```bash
uv run python scripts/gmail_pubsub_worker.py
```

Output on startup:
- `gmail_watch_started` log with the Gmail-provided historyId + expiration
- Two concurrent async loops: drain (pulls Pub/Sub) + renew (daily watch()
  re-assertion)

**What to watch for:**

- `gmail_message_processed` per message (same as A1)
- `gmail_watch_renewed` every 24 hours
- `gmail_history_id_stale` if the worker was down >1 week — triggers
  full-scan fallback for one cycle, then resumes push
- `pubsub_pull_failed` on transient Pub/Sub errors — worker backs off 5s
  and retries

**Using the Pub/Sub emulator locally:**

```bash
gcloud beta emulators pubsub start --host-port=localhost:8085
export PUBSUB_EMULATOR_HOST=localhost:8085
```

The `SubscriberAsyncClient` auto-uses the emulator when `PUBSUB_EMULATOR_HOST`
is set.

**A1 vs A3:**

- **A1 (`scripts/gmail_poll.py`)** — no GCP infra required. Dev-friendly.
  30-second polling cadence.
- **A3 (`scripts/gmail_pubsub_worker.py`)** — requires Pub/Sub topic +
  subscription. Near-real-time. Production-shape.

Both reuse the same pipeline + credentials. Operator picks one per
environment. Running both concurrently against the same inbox is safe
(Gmail label dedup + source_message_id idempotency) but wastes cycles.

**Limitations (deferred to Phase 3):**

- No Cloud Run / webhook PUSH subscription. The PULL approach still
  requires a long-lived process to drain the subscription.
- No Secret Manager. Credentials remain in `.env`.
- No Cloud Scheduler renewal. Renewal runs inside the worker; a
  different process is responsible for keeping the worker alive.
```

- [ ] **Step 9.2: Flip §1 Signal Ingestion row + Built inventory in `research/Order-Intake-Sprint-Status.md`**

Update the §1 row "What we have" cell to note both A1 and A3 completion:

```
| **1. Signal ingestion** | Gmail watch → Pub/Sub → attachment download | Fixtures ✓ + 4/4 format wrappers (PDF/CSV/XLSX/EDI) ✓ + clarify-reply fixture ✓ + `backend/ingestion/` ✓ + `scripts/inject_email.py` CLI ✓ + **Gmail polling ingress ✓** (Track A1) + **Gmail push ingestion ✓** (Track A3: users.watch → Pub/Sub PULL subscription + in-worker watch renewal + Firestore historyId cursor + stale-cursor full-scan fallback) | Wrap remaining 6 non-`.eml` fixtures (non-blocking). Webhook PUSH + Cloud Run + Secret Manager + Cloud Scheduler deferred to Phase 3. |
```

Append to the Built inventory block (alphabetical with C / D / A1 / A2 entries):

```
backend/gmail/watch.py                                                  ✓ Track A3 (<sha-task-2>) — GmailWatch wrapper (start/stop/getProfile)
backend/gmail/history.py                                                ✓ Track A3 (<sha-task-3>) — fetch_new_message_ids + HistoryIdTooOldError
backend/persistence/sync_state_store.py                                 ✓ Track A3 (<sha-task-4>) — GmailSyncStateStore for historyId cursor
backend/gmail/pubsub_worker.py                                          ✓ Track A3 (<sha-task-5>) — drain + renew loops
scripts/gmail_watch_setup.py                                            ✓ Track A3 (<sha-task-6>) — one-time topic/subscription/IAM bootstrap
scripts/gmail_pubsub_worker.py                                          ✓ Track A3 (<sha-task-7>) — long-running entrypoint
tests/integration/test_pubsub_worker_emulator.py                        ✓ Track A3 (<sha-task-8>) — 1 gated emulator integration test
```

- [ ] **Step 9.3: Flip §1 Email-Ingestion bullets in `Glacis-Order-Intake.md`**

Find `[Post-MVP]` entries and flip to `[MVP ✓]`:

```markdown
- `[MVP ✓]` **Gmail `users.watch()` registration** — MVP: `backend/gmail/watch.py` +
  scripts/gmail_watch_setup.py bootstrap + in-worker daily renewal via
  `GmailPubSubWorker._renew_loop` (default 24h, configurable).
  Landed 2026-04-24 via Track A3 (<sha-task-2> + <sha-task-5>). Source: `Email-Ingestion.md`.

- `[MVP ✓]` **Pub/Sub PULL subscription** — MVP: `GmailPubSubWorker._drain_loop` uses
  `SubscriberAsyncClient.pull()` in a long-lived asyncio loop. Credentials from
  `.env`. Runs as `scripts/gmail_pubsub_worker.py`. Landed 2026-04-24 via Track A3
  (<sha-task-5> + <sha-task-7>). Source: `Email-Ingestion.md`, `Event-Architecture.md`.

- `[MVP ✓]` **History API sync + dedup** — MVP: `backend/gmail/history.py` +
  `GmailSyncStateStore` (Firestore cursor at `gmail_sync_state/{user_email}`).
  Stale-cursor recovery falls back to A1-style full-scan one time, then resumes push.
  `orderintake-processed` Gmail label + `source_message_id` idempotency collapse
  all duplicates. Landed 2026-04-24 via Track A3 (<sha-task-3> + <sha-task-4>).
  Source: `Email-Ingestion.md`.

- `[MVP ✓]` **Thread tracking for clarify-reply loop** — parse_eml already extracts
  `In-Reply-To` + `References`; reuses A1 adapter; no A3-specific changes.
  Fully wired since Track A / A1. Source: `Email-Ingestion.md`.
```

Keep `[Post-MVP]` for the webhook PUSH + Cloud Run variant — it's deliberately deferred. Update its row:

```markdown
- `[Post-MVP]` **Pub/Sub PUSH subscription + Cloud Run webhook** — deliberately
  deferred after Track A3 landed the PULL variant. A3's worker covers the
  functional gap; Cloud Run adds deployment posture + latency (a live webhook
  is sub-second vs. a PULL worker's ~1-5s poll + drain cadence). Post-hackathon
  migration: swap `GmailPubSubWorker` with a FastAPI route; keep everything
  else (adapter, pipeline, cursor store). Source: `Email-Ingestion.md`, `Event-Architecture.md`.
```

- [ ] **Step 9.4: Commit**

```bash
git add backend/my_agent/README.md research/Order-Intake-Sprint-Status.md Glacis-Order-Intake.md
git commit -m "docs(track-a3): flip watch + Pub/Sub + History API to [MVP ✓]"
```

---

## Task 10: Final verification

- [ ] **Step 10.1: Full unit suite — no regressions**

Run: `uv run pytest tests/unit -v 2>&1 | tail -15`

Expected: all green. Test count: baseline (after C + D + A1 + A2 ≈ 390) + 16 new A3 = ~406.

- [ ] **Step 10.2: Full integration suite**

With Firestore emulator running + `FIRESTORE_EMULATOR_HOST=localhost:8080` set:

Run: `uv run pytest tests/integration -v 2>&1 | tail -15`

Expected: all green. A3's gated test auto-skips unless `PUBSUB_EMULATOR_HOST` is also set.

- [ ] **Step 10.3: Pub/Sub emulator integration (optional)**

Start the Pub/Sub emulator:

```bash
gcloud beta emulators pubsub start --host-port=localhost:8085
```

In another terminal:
```bash
export PUBSUB_EMULATOR_HOST=localhost:8085
export FIRESTORE_EMULATOR_HOST=localhost:8080
uv run pytest -m pubsub_emulator -v
```

Expected: 1 test passes (the gated integration test).

- [ ] **Step 10.4: Manual live smoke (optional, high-confidence gate)**

Real GCP project with:
- Pub/Sub API enabled
- Topic + subscription created via `scripts/gmail_watch_setup.py`
- `gcloud auth application-default login` run
- `.env` carrying all GMAIL + GMAIL_PUBSUB vars

Start:
```bash
uv run python scripts/gmail_pubsub_worker.py
```

Within ~5s of startup:
- Log line `gmail_watch_started` with a 7-day-out expiration
- Drain loop idle (empty inbox)

Send a test email to the agent's Gmail address from another account. Within ~5s:
- Pub/Sub delivers the notification
- Log line `gmail_message_processed` with `source_message_id`
- Gmail shows `orderintake-processed` label on the message
- Firestore shows the `OrderRecord` or `ExceptionRecord`
- `gmail_sync_state/{user_email}` cursor advances

Ctrl-C → `gmail_pubsub_worker_stopping` log line.

Restart. Previously-processed messages are NOT reprocessed (label dedup).

- [ ] **Step 10.5: Done**

Track A3 closed. Next session picks up Track B (Generator-Judge quality gate) via brainstorm → spec → plan → execute.

---

## Self-review

**Spec coverage:**
- ✅ Decision 1 (PULL scope, no Cloud Run) → entire plan (no webhook in any task)
- ✅ Decision 2 (coexist with A1) → A1 scripts not modified; A3 adds new scripts
- ✅ Decision 3 (SubscriberAsyncClient.pull in asyncio loop) → Task 5 `_drain_loop`
- ✅ Decision 4 (Firestore historyId cursor) → Task 4 `GmailSyncStateStore`
- ✅ Decision 5 (in-worker daily watch renewal) → Task 5 `_renew_loop`
- ✅ Decision 6 (stale-cursor full-scan fallback) → Task 5 `_process_pubsub_message` + test #2 in that file
- ✅ `GmailWatch.start / stop / get_profile_email` → Task 2
- ✅ `fetch_new_message_ids` + `HistoryIdTooOldError` → Task 3
- ✅ `scripts/gmail_watch_setup.py` IAM bootstrap → Task 6
- ✅ `scripts/gmail_pubsub_worker.py` entrypoint → Task 7
- ✅ `.env.example` Pub/Sub vars → Task 7
- ✅ `google-cloud-pubsub` dep → Task 1
- ✅ Integration test against emulator → Task 8
- ✅ README + status + Glacis doc flips → Task 9

**Placeholder scan:**
- Task 8.1 integration test uses `project_id = f"a3-test-{uuid.uuid4().hex[:8]}"` — runtime value, not a placeholder.
- Task 8.1 imports `pathlib.Path("data/pdf/patterson_po-28491.wrapper.eml")` — fixture verified to exist in the tree per the status-inventory entry from A1's Task 4.
- Task 9.2 and 9.3 use `<sha-task-N>` placeholders — standard pattern the executor fills after each task commits.
- No `TBD` / `fill in` / `similar to` anywhere.

**Type consistency:**
- `GmailWatch.start(topic_name, label_ids) → dict` — consistent Task 2 impl + test + Task 5 worker usage.
- `GmailWatch.get_profile_email() → str` — consistent Task 2 + Task 5.
- `fetch_new_message_ids(client, start_history_id, max_pages) → (list[str], str)` — consistent Task 3 + Task 5 + Task 8 fake.
- `HistoryIdTooOldError` — consistent Task 3 raise + Task 5 catch + Task 5 test #2.
- `GmailSyncStateStore.get_cursor / set_cursor` — consistent Task 4 + Task 5.
- `GmailPubSubWorker(*, subscriber, subscription_path, gmail_client, runner, session_service, sync_state_store, watch, topic_name, watch_label_ids, watch_renew_interval_seconds, max_messages_per_pull, label_name, app_name, user_id)` — consistent Task 5 + Task 7 script.
- `A2_SCOPES` reused (no new scope) — consistent Task 7 + spec.

No inconsistencies.

**Scope check:** 10 tasks, each 3-8 steps, TDD-cycled. Estimated execution: 4-5 hours. Single-plan-sized.

**Dependency check:** Depends on A1 (`GmailClient`, `gmail_message_to_envelope`, `fake_client` fixture, `test_gmail_auth.py` structure). Optionally benefits from A2 (A2_SCOPES). Does NOT depend on C or D. Can be executed standalone against a post-A1 baseline.

No fixes needed inline.
