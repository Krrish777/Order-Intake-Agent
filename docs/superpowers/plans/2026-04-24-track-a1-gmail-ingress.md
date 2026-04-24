# Track A1 — Gmail Ingress (Polling) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a polling-loop Gmail ingress that pulls new messages from a single inbox every 30s, converts each to `EmailEnvelope` via the existing `parse_eml`, drives it through the 9-stage pipeline in-process via `Runner.run_async`, and applies a `orderintake-processed` Gmail label for dedup. OAuth via installed-app flow; refresh token in `.env`.

**Architecture:** New `backend/gmail/` package (sync `GmailClient` wrapper + async `GmailPoller` loop + tiny `parse_eml` adapter) + two runnable scripts (`gmail_auth_init.py` one-time OAuth, `gmail_poll.py` long-lived poller). Zero pipeline changes — the poller calls `Runner.run_async` with raw RFC 822 bytes using the exact same shape as `scripts/inject_email.py`.

**Tech Stack:** Python 3.13, `google-api-python-client>=2.140`, `google-auth>=2.35`, `google-auth-oauthlib>=1.2`, ADK `Runner` + `InMemorySessionService`, Pydantic models (existing `EmailEnvelope`), pytest + pytest-asyncio, MagicMock for Gmail Resource, python-dotenv.

**Source spec:** `docs/superpowers/specs/2026-04-24-track-a1-gmail-ingress-design.md` (rev `9ddbf27`).

---

## File structure

| Path | Responsibility |
|---|---|
| **New** `backend/gmail/__init__.py` | Package marker + re-exports |
| **New** `backend/gmail/scopes.py` | `GMAIL_MODIFY_SCOPE` + `A1_SCOPES` constants |
| **New** `backend/gmail/client.py` | `GmailClient` sync wrapper — list / get / label surface |
| **New** `backend/gmail/adapter.py` | `gmail_message_to_envelope(bytes) → EmailEnvelope` via tempfile |
| **New** `backend/gmail/poller.py` | `GmailPoller` async loop — in-process `Runner.run_async` invocation |
| **New** `scripts/gmail_auth_init.py` | One-time OAuth bootstrap |
| **New** `scripts/gmail_poll.py` | Long-running polling loop entrypoint |
| **Modified** `pyproject.toml` | +4 deps (google auth/API stack) + python-dotenv |
| **New** `.env.example` | Template for all GMAIL_* env vars |
| **New** `tests/unit/test_gmail_client.py` | 8 tests — list / get / label round-trips via MagicMock |
| **New** `tests/unit/test_gmail_adapter.py` | 3 tests — fixture bytes → EmailEnvelope |
| **New** `tests/unit/test_gmail_poller.py` | 6 tests — poll tick, process_one sequence, error handling |
| **New** `tests/unit/test_gmail_auth.py` | 2 tests — scope + Credentials shape |
| **New** `tests/integration/test_gmail_poller_fixture.py` | 1 gated live test (`@pytest.mark.gmail_live`) |
| **Modified** `research/Order-Intake-Sprint-Status.md` | §1 row flip + Built inventory |
| **Modified** `Glacis-Order-Intake.md` | §1 installed-app-flow OAuth `[Post-MVP]` → `[MVP ✓]` |
| **Modified** `backend/my_agent/README.md` | +"Gmail ingress (A1)" operator section |

---

## Task 1: Scopes module

**Files:**
- Create: `backend/gmail/__init__.py`
- Create: `backend/gmail/scopes.py`
- Create: `tests/unit/test_gmail_auth.py`

- [ ] **Step 1.1: Write the failing test**

Create `tests/unit/test_gmail_auth.py`:

```python
"""Unit tests for Gmail OAuth scopes.

Spec: docs/superpowers/specs/2026-04-24-track-a1-gmail-ingress-design.md
"""
from __future__ import annotations


def test_gmail_modify_scope_is_the_official_uri():
    from backend.gmail.scopes import GMAIL_MODIFY_SCOPE

    assert GMAIL_MODIFY_SCOPE == "https://www.googleapis.com/auth/gmail.modify"


def test_a1_scopes_is_exactly_gmail_modify():
    from backend.gmail.scopes import A1_SCOPES, GMAIL_MODIFY_SCOPE

    assert A1_SCOPES == [GMAIL_MODIFY_SCOPE]
```

- [ ] **Step 1.2: Run — expect failure**

Run: `uv run pytest tests/unit/test_gmail_auth.py -v`

Expected: `ModuleNotFoundError: No module named 'backend.gmail'`.

- [ ] **Step 1.3: Create `backend/gmail/scopes.py`**

```python
"""OAuth scopes for the Gmail-ingestion tracks.

A1 (ingress):  gmail.modify — read inbox + apply labels
A2 (egress):   + gmail.send — send messages
A3 (deploy):   no additional scope — watch uses the same subset
"""

GMAIL_MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"

A1_SCOPES = [GMAIL_MODIFY_SCOPE]

__all__ = ["GMAIL_MODIFY_SCOPE", "A1_SCOPES"]
```

- [ ] **Step 1.4: Create `backend/gmail/__init__.py`**

```python
"""Gmail integration package (Track A1 ingress side).

Public surface:
- GmailClient (client.py)
- gmail_message_to_envelope (adapter.py)
- GmailPoller (poller.py)
- GMAIL_MODIFY_SCOPE, A1_SCOPES (scopes.py)
"""
from backend.gmail.scopes import A1_SCOPES, GMAIL_MODIFY_SCOPE

# Other exports added as modules land in subsequent tasks.

__all__ = ["A1_SCOPES", "GMAIL_MODIFY_SCOPE"]
```

- [ ] **Step 1.5: Run tests — expect 2 passes**

Run: `uv run pytest tests/unit/test_gmail_auth.py -v`

Expected: `2 passed`.

- [ ] **Step 1.6: Commit**

```bash
git add backend/gmail/__init__.py backend/gmail/scopes.py tests/unit/test_gmail_auth.py
git commit -m "feat(track-a1): add Gmail OAuth scopes module"
```

---

## Task 2: Add Gmail API dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 2.1: Read current dependencies**

Run: `grep -A 40 '\[project\]' pyproject.toml | grep -E 'dependencies|google-|python-dotenv'`

Note which Google SDKs are already present. `google-cloud-firestore` is in the tree; the new additions are the `google-api-python-client` + auth libs for Gmail specifically.

- [ ] **Step 2.2: Add the 4 new deps**

In `pyproject.toml`, find the `dependencies = [` array and append (alphabetical in the existing style):

```toml
"google-api-python-client>=2.140",
"google-auth>=2.35",
"google-auth-httplib2>=0.2",
"google-auth-oauthlib>=1.2",
```

**Also check `python-dotenv`:** if not already in `dependencies`, add:
```toml
"python-dotenv>=1.0",
```

- [ ] **Step 2.3: Sync the lock**

Run: `uv sync`

Expected: lock file updates. No errors — these are widely-compatible libraries with no known conflicts.

- [ ] **Step 2.4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "feat(track-a1): add google-api-python-client + auth stack for Gmail"
```

---

## Task 3: `GmailClient` sync wrapper

**Files:**
- Create: `backend/gmail/client.py`
- Create: `tests/unit/test_gmail_client.py`
- Modify: `backend/gmail/__init__.py`

- [ ] **Step 3.1: Write the failing tests**

Create `tests/unit/test_gmail_client.py`:

```python
"""Unit tests for GmailClient sync wrapper.

All tests patch googleapiclient.discovery.build to return a
MagicMock Resource — no network calls.

Spec: docs/superpowers/specs/2026-04-24-track-a1-gmail-ingress-design.md
"""
from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

import pytest


def _make_client():
    """Build a GmailClient with a patched Resource."""
    from backend.gmail.client import GmailClient

    patcher = patch("backend.gmail.client.build")
    mock_build = patcher.start()
    mock_service = MagicMock()
    mock_build.return_value = mock_service

    client = GmailClient(
        refresh_token="rt-abc",
        client_id="cid-123",
        client_secret="sec-456",
        scopes=["https://www.googleapis.com/auth/gmail.modify"],
    )
    return client, mock_service, patcher


def _teardown(patcher):
    patcher.stop()


class TestGmailClientListUnprocessed:
    def test_list_unprocessed_issues_expected_query(self):
        client, svc, patcher = _make_client()
        try:
            svc.users().messages().list().execute.return_value = {
                "messages": [{"id": "a"}, {"id": "b"}]
            }

            result = client.list_unprocessed(label_name="foo")
            assert result == ["a", "b"]
            # Verify the query shape — last .list(...) call kwargs
            last_list_call = svc.users().messages().list.call_args
            assert last_list_call.kwargs["userId"] == "me"
            assert last_list_call.kwargs["q"] == "in:inbox -label:foo"
            assert last_list_call.kwargs["maxResults"] == 50
        finally:
            _teardown(patcher)

    def test_list_unprocessed_returns_empty_on_no_messages(self):
        client, svc, patcher = _make_client()
        try:
            svc.users().messages().list().execute.return_value = {}
            assert client.list_unprocessed(label_name="foo") == []
        finally:
            _teardown(patcher)

    def test_list_unprocessed_preserves_order(self):
        client, svc, patcher = _make_client()
        try:
            svc.users().messages().list().execute.return_value = {
                "messages": [{"id": "z"}, {"id": "a"}, {"id": "m"}]
            }
            assert client.list_unprocessed(label_name="foo") == ["z", "a", "m"]
        finally:
            _teardown(patcher)


class TestGmailClientGetRaw:
    def test_get_raw_decodes_base64url_to_bytes(self):
        client, svc, patcher = _make_client()
        try:
            raw_bytes = b"From: test@test\r\n\r\nhello"
            encoded = base64.urlsafe_b64encode(raw_bytes).decode("ascii")
            svc.users().messages().get().execute.return_value = {"raw": encoded}

            result = client.get_raw("msg-1")
            assert result == raw_bytes
        finally:
            _teardown(patcher)

    def test_get_raw_raises_on_missing_raw_field(self):
        client, svc, patcher = _make_client()
        try:
            svc.users().messages().get().execute.return_value = {}
            with pytest.raises(ValueError, match="msg-1"):
                client.get_raw("msg-1")
        finally:
            _teardown(patcher)


class TestGmailClientLabels:
    def test_label_id_for_returns_existing_label_and_caches(self):
        client, svc, patcher = _make_client()
        try:
            svc.users().labels().list().execute.return_value = {
                "labels": [{"id": "Label_1", "name": "foo"}]
            }
            # First call
            assert client.label_id_for("foo") == "Label_1"
            # Second call should be cache-hit — verify labels.list not called again
            labels_list_calls_before = svc.users().labels().list.call_count
            assert client.label_id_for("foo") == "Label_1"
            labels_list_calls_after = svc.users().labels().list.call_count
            assert labels_list_calls_after == labels_list_calls_before
        finally:
            _teardown(patcher)

    def test_label_id_for_creates_when_missing(self):
        client, svc, patcher = _make_client()
        try:
            svc.users().labels().list().execute.return_value = {"labels": []}
            svc.users().labels().create().execute.return_value = {
                "id": "Label_new",
                "name": "bar",
            }
            assert client.label_id_for("bar") == "Label_new"
            # Verify create was called
            create_body = svc.users().labels().create.call_args.kwargs["body"]
            assert create_body["name"] == "bar"
        finally:
            _teardown(patcher)

    def test_apply_label_calls_modify_with_add_label_ids(self):
        client, svc, patcher = _make_client()
        try:
            client.apply_label("msg-1", "Label_X")
            modify_call = svc.users().messages().modify.call_args
            assert modify_call.kwargs["userId"] == "me"
            assert modify_call.kwargs["id"] == "msg-1"
            assert modify_call.kwargs["body"] == {"addLabelIds": ["Label_X"]}
        finally:
            _teardown(patcher)
```

- [ ] **Step 3.2: Run — expect failure**

Run: `uv run pytest tests/unit/test_gmail_client.py -v`

Expected: import failure on `backend.gmail.client`.

- [ ] **Step 3.3: Create `backend/gmail/client.py`**

```python
"""Authed Gmail API Resource wrapper for Track A1 ingress.

Sync-only (googleapiclient is sync). Async boundary lives at
poller.py via asyncio.to_thread. Methods map 1:1 onto the small
surface the poller needs — list_unprocessed, get_raw, label_id_for,
apply_label.

Spec: docs/superpowers/specs/2026-04-24-track-a1-gmail-ingress-design.md
"""
from __future__ import annotations

import base64
from typing import Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource, build

_GMAIL_TOKEN_URL = "https://oauth2.googleapis.com/token"


class GmailClient:
    """Sync wrapper around a Gmail API Resource.

    Construct once per process; the underlying HTTP transport +
    Credentials object handle access-token refresh automatically.
    """

    def __init__(
        self,
        *,
        refresh_token: str,
        client_id: str,
        client_secret: str,
        scopes: list[str],
    ) -> None:
        credentials = Credentials(
            token=None,
            refresh_token=refresh_token,
            client_id=client_id,
            client_secret=client_secret,
            token_uri=_GMAIL_TOKEN_URL,
            scopes=scopes,
        )
        self._service: Resource = build(
            "gmail", "v1", credentials=credentials, cache_discovery=False
        )
        self._label_id_cache: dict[str, str] = {}

    # ---- read surface ----

    def list_unprocessed(
        self,
        *,
        label_name: str,
        max_results: int = 50,
    ) -> list[str]:
        query = f"in:inbox -label:{label_name}"
        resp = (
            self._service.users()
            .messages()
            .list(userId="me", q=query, maxResults=max_results)
            .execute()
        )
        return [m["id"] for m in resp.get("messages", [])]

    def get_raw(self, message_id: str) -> bytes:
        resp = (
            self._service.users()
            .messages()
            .get(userId="me", id=message_id, format="raw")
            .execute()
        )
        raw_b64url: Optional[str] = resp.get("raw")
        if raw_b64url is None:
            raise ValueError(f"Gmail message {message_id} has no raw payload")
        return base64.urlsafe_b64decode(raw_b64url.encode("ascii"))

    # ---- label surface ----

    def label_id_for(self, label_name: str) -> str:
        if label_name in self._label_id_cache:
            return self._label_id_cache[label_name]

        resp = self._service.users().labels().list(userId="me").execute()
        for label in resp.get("labels", []):
            if label["name"] == label_name:
                self._label_id_cache[label_name] = label["id"]
                return label["id"]

        created = (
            self._service.users()
            .labels()
            .create(
                userId="me",
                body={
                    "name": label_name,
                    "labelListVisibility": "labelShow",
                    "messageListVisibility": "show",
                },
            )
            .execute()
        )
        self._label_id_cache[label_name] = created["id"]
        return created["id"]

    def apply_label(self, message_id: str, label_id: str) -> None:
        self._service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"addLabelIds": [label_id]},
        ).execute()


__all__ = ["GmailClient"]
```

- [ ] **Step 3.4: Update `backend/gmail/__init__.py` to re-export**

```python
"""Gmail integration package (Track A1 ingress side)."""
from backend.gmail.client import GmailClient
from backend.gmail.scopes import A1_SCOPES, GMAIL_MODIFY_SCOPE

__all__ = ["A1_SCOPES", "GMAIL_MODIFY_SCOPE", "GmailClient"]
```

- [ ] **Step 3.5: Run tests — expect 8 passes**

Run: `uv run pytest tests/unit/test_gmail_client.py -v`

Expected: `8 passed`.

- [ ] **Step 3.6: Commit**

```bash
git add backend/gmail/client.py backend/gmail/__init__.py tests/unit/test_gmail_client.py
git commit -m "feat(track-a1): add GmailClient sync wrapper"
```

---

## Task 4: `gmail_message_to_envelope` adapter

**Files:**
- Create: `backend/gmail/adapter.py`
- Create: `tests/unit/test_gmail_adapter.py`
- Modify: `backend/gmail/__init__.py`

- [ ] **Step 4.1: Identify a fixture `.eml` we can read raw**

Run: `ls data/email/*.eml | head -3`

Pick any fixture with attachments — e.g., `data/email/birch_valley_clarify_reply.eml`. Tests will read it as bytes and feed to the adapter.

- [ ] **Step 4.2: Write the failing tests**

Create `tests/unit/test_gmail_adapter.py`:

```python
"""Unit tests for gmail_message_to_envelope adapter.

The adapter writes raw RFC 822 bytes to a NamedTemporaryFile then
calls the existing parse_eml — so these tests simultaneously verify
(a) the adapter doesn't lose bytes in the round-trip and (b) parse_eml
still handles whatever's in the fixture. The parse_eml suite itself
(tests/unit/test_eml_parser.py) is the deep coverage.
"""
from __future__ import annotations

from pathlib import Path

import pytest


FIXTURE_WITH_THREAD = Path("data/email/birch_valley_clarify_reply.eml")
# Any fixture with at least one attachment is fine for test #3
FIXTURE_WITH_ATTACHMENT = Path("data/pdf/patterson_po-28491.wrapper.eml")


@pytest.mark.asyncio
async def test_adapter_returns_email_envelope():
    from backend.gmail.adapter import gmail_message_to_envelope
    from backend.ingestion.email_envelope import EmailEnvelope

    raw = FIXTURE_WITH_THREAD.read_bytes()
    envelope = await gmail_message_to_envelope(raw)

    assert isinstance(envelope, EmailEnvelope)
    assert envelope.message_id  # non-empty string
    assert envelope.sender
    assert envelope.subject


@pytest.mark.asyncio
async def test_adapter_preserves_thread_headers():
    from backend.gmail.adapter import gmail_message_to_envelope

    raw = FIXTURE_WITH_THREAD.read_bytes()
    envelope = await gmail_message_to_envelope(raw)

    # birch_valley_clarify_reply is a reply fixture — must carry in_reply_to
    assert envelope.in_reply_to is not None
    assert envelope.in_reply_to != ""


@pytest.mark.asyncio
async def test_adapter_preserves_attachment_bytes():
    from backend.gmail.adapter import gmail_message_to_envelope
    from backend.ingestion.eml_parser import parse_eml

    raw = FIXTURE_WITH_ATTACHMENT.read_bytes()
    via_adapter = await gmail_message_to_envelope(raw)
    via_parse_eml_direct = parse_eml(FIXTURE_WITH_ATTACHMENT)

    assert len(via_adapter.attachments) == len(via_parse_eml_direct.attachments)
    for a, b in zip(via_adapter.attachments, via_parse_eml_direct.attachments):
        assert a.filename == b.filename
        assert a.content == b.content
```

- [ ] **Step 4.3: Run — expect failure**

Run: `uv run pytest tests/unit/test_gmail_adapter.py -v`

Expected: import failure on `backend.gmail.adapter`.

- [ ] **Step 4.4: Create `backend/gmail/adapter.py`**

```python
"""Raw Gmail bytes → EmailEnvelope via parse_eml.

parse_eml takes a filesystem Path today. This helper writes raw RFC
822 bytes to a NamedTemporaryFile so we can reuse every multipart /
attachment / encoding edge case already handled by the existing
parser. Zero new parsing code.

Spec: docs/superpowers/specs/2026-04-24-track-a1-gmail-ingress-design.md
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from backend.ingestion.email_envelope import EmailEnvelope
from backend.ingestion.eml_parser import parse_eml


async def gmail_message_to_envelope(raw_rfc822: bytes) -> EmailEnvelope:
    """Parse raw RFC 822 bytes via the existing .eml parser.

    Writes bytes to a NamedTemporaryFile because parse_eml is
    Path-only by design. Cleans up the tempfile in a finally.
    """
    fd, tmp_name = tempfile.mkstemp(suffix=".eml")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw_rfc822)
        return parse_eml(Path(tmp_name))
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass


__all__ = ["gmail_message_to_envelope"]
```

- [ ] **Step 4.5: Update `backend/gmail/__init__.py`**

```python
"""Gmail integration package (Track A1 ingress side)."""
from backend.gmail.adapter import gmail_message_to_envelope
from backend.gmail.client import GmailClient
from backend.gmail.scopes import A1_SCOPES, GMAIL_MODIFY_SCOPE

__all__ = [
    "A1_SCOPES",
    "GMAIL_MODIFY_SCOPE",
    "GmailClient",
    "gmail_message_to_envelope",
]
```

- [ ] **Step 4.6: Run tests — expect 3 passes**

Run: `uv run pytest tests/unit/test_gmail_adapter.py -v`

Expected: `3 passed`. If `FIXTURE_WITH_THREAD` or `FIXTURE_WITH_ATTACHMENT` paths are wrong for this codebase, grep `data/` for `.eml` with `In-Reply-To` and with binary attachments and update the constants.

- [ ] **Step 4.7: Commit**

```bash
git add backend/gmail/adapter.py backend/gmail/__init__.py tests/unit/test_gmail_adapter.py
git commit -m "feat(track-a1): add gmail_message_to_envelope adapter"
```

---

## Task 5: `GmailPoller` async loop

**Files:**
- Create: `backend/gmail/poller.py`
- Create: `tests/unit/test_gmail_poller.py`
- Modify: `backend/gmail/__init__.py`

- [ ] **Step 5.1: Write the failing tests**

Create `tests/unit/test_gmail_poller.py`:

```python
"""Unit tests for GmailPoller async loop.

Uses AsyncMock for the Runner + SessionService, MagicMock for the
GmailClient. No network, no pipeline invocation — only the
orchestration logic is under test.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.asyncio


async def _make_poller(list_result=None, raw_result=None):
    from backend.gmail.client import GmailClient
    from backend.gmail.poller import GmailPoller

    gmail_client = MagicMock(spec=GmailClient)
    gmail_client.list_unprocessed = MagicMock(return_value=list_result or [])
    gmail_client.get_raw = MagicMock(return_value=raw_result or b"")
    gmail_client.label_id_for = MagicMock(return_value="Label_X")
    gmail_client.apply_label = MagicMock()

    runner = AsyncMock()

    async def _empty_stream(*a, **kw):
        if False:
            yield None  # pragma: no cover

    runner.run_async = MagicMock(side_effect=lambda **kw: _empty_stream())

    session_service = AsyncMock()
    session_service.create_session = AsyncMock()

    poller = GmailPoller(
        gmail_client=gmail_client,
        runner=runner,
        session_service=session_service,
        root_agent=MagicMock(),
        label_name="orderintake-processed",
        poll_interval_seconds=0,  # zero for fast tests
    )
    return poller, gmail_client, runner, session_service


class TestGmailPollerTick:
    async def test_tick_with_no_messages_does_nothing(self):
        poller, gmail_client, runner, session_service = await _make_poller(list_result=[])
        await poller._tick()
        gmail_client.list_unprocessed.assert_called_once()
        gmail_client.get_raw.assert_not_called()
        runner.run_async.assert_not_called()
        gmail_client.apply_label.assert_not_called()

    async def test_tick_with_one_message_processes_it(self, monkeypatch):
        poller, gmail_client, runner, session_service = await _make_poller(
            list_result=["m1"],
            raw_result=b"From: x\r\n\r\nhi",
        )

        # Stub the adapter so we don't actually parse
        from backend.gmail import poller as poller_module
        adapter_mock = AsyncMock(return_value=MagicMock(message_id="<msg-id>"))
        monkeypatch.setattr(
            poller_module, "gmail_message_to_envelope", adapter_mock
        )

        await poller._tick()

        gmail_client.get_raw.assert_called_once_with("m1")
        adapter_mock.assert_awaited_once()
        session_service.create_session.assert_awaited_once()
        runner.run_async.assert_called_once()
        gmail_client.apply_label.assert_called_once_with("m1", "Label_X")

    async def test_tick_with_three_messages_processes_in_order(self, monkeypatch):
        poller, gmail_client, runner, session_service = await _make_poller(
            list_result=["a", "b", "c"],
            raw_result=b"From: x\r\n\r\nhi",
        )

        from backend.gmail import poller as poller_module
        monkeypatch.setattr(
            poller_module,
            "gmail_message_to_envelope",
            AsyncMock(return_value=MagicMock(message_id="x")),
        )

        await poller._tick()

        applied_ids = [c.args[0] for c in gmail_client.apply_label.call_args_list]
        assert applied_ids == ["a", "b", "c"]


class TestGmailPollerProcessOne:
    async def test_pipeline_error_skips_apply_label(self, monkeypatch):
        poller, gmail_client, runner, session_service = await _make_poller(
            list_result=["bad-msg"],
            raw_result=b"From: x\r\n\r\nhi",
        )

        from backend.gmail import poller as poller_module
        monkeypatch.setattr(
            poller_module,
            "gmail_message_to_envelope",
            AsyncMock(return_value=MagicMock(message_id="x")),
        )

        async def _raising_stream(**kwargs):
            raise RuntimeError("pipeline boom")
            if False:
                yield None  # pragma: no cover

        runner.run_async = MagicMock(side_effect=_raising_stream)

        # _process_one must NOT re-raise
        await poller._process_one("bad-msg")

        gmail_client.apply_label.assert_not_called()

    async def test_adapter_error_skips_apply_label(self, monkeypatch):
        poller, gmail_client, runner, session_service = await _make_poller(
            list_result=["bad-msg"],
            raw_result=b"invalid bytes",
        )

        from backend.gmail import poller as poller_module
        monkeypatch.setattr(
            poller_module,
            "gmail_message_to_envelope",
            AsyncMock(side_effect=ValueError("parse error")),
        )

        await poller._process_one("bad-msg")
        gmail_client.apply_label.assert_not_called()


class TestGmailPollerRunForever:
    async def test_run_forever_exits_cleanly_on_cancellation(self):
        poller, *_ = await _make_poller(list_result=[])

        async def _run_with_cancel():
            task = asyncio.create_task(poller.run_forever())
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        await _run_with_cancel()
```

- [ ] **Step 5.2: Run — expect failure**

Run: `uv run pytest tests/unit/test_gmail_poller.py -v`

Expected: import failure on `backend.gmail.poller`.

- [ ] **Step 5.3: Create `backend/gmail/poller.py`**

```python
"""Async polling loop orchestrating GmailClient + adapter + pipeline.

Sequential per tick: list unprocessed → for each message, get_raw →
adapt → Runner.run_async → apply_label. Errors per message are
logged and swallowed; the loop continues. SIGINT / SIGTERM exits
cleanly via asyncio.CancelledError.

Spec: docs/superpowers/specs/2026-04-24-track-a1-gmail-ingress-design.md
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any, Optional

from google.adk.agents import BaseAgent
from google.adk.runners import Runner
from google.adk.sessions import BaseSessionService
from google.genai import types

from backend.gmail.adapter import gmail_message_to_envelope
from backend.gmail.client import GmailClient
from backend.utils.logging import get_logger

_log = get_logger(__name__)


class GmailPoller:
    def __init__(
        self,
        *,
        gmail_client: GmailClient,
        runner: Runner,
        session_service: BaseSessionService,
        root_agent: BaseAgent,
        app_name: str = "order_intake",
        user_id: str = "gmail_poller",
        label_name: str = "orderintake-processed",
        poll_interval_seconds: int = 30,
    ) -> None:
        self._gmail = gmail_client
        self._runner = runner
        self._sessions = session_service
        self._root_agent = root_agent
        self._app_name = app_name
        self._user_id = user_id
        self._label_name = label_name
        self._poll_interval = poll_interval_seconds
        self._label_id_cached: Optional[str] = None

    async def run_forever(self) -> None:
        _log.info("gmail_poller_start", interval=self._poll_interval)
        try:
            while True:
                try:
                    await self._tick()
                except Exception as exc:
                    _log.error("gmail_poller_tick_failed", error=str(exc))
                await asyncio.sleep(self._poll_interval)
        except asyncio.CancelledError:
            _log.info("gmail_poller_stopping")
            raise

    async def _tick(self) -> None:
        if self._label_id_cached is None:
            self._label_id_cached = await asyncio.to_thread(
                self._gmail.label_id_for, self._label_name
            )

        message_ids = await asyncio.to_thread(
            self._gmail.list_unprocessed, label_name=self._label_name
        )
        for message_id in message_ids:
            await self._process_one(message_id)

    async def _process_one(self, message_id: str) -> None:
        try:
            raw_bytes = await asyncio.to_thread(self._gmail.get_raw, message_id)
            # Adapter call validates parse_eml can handle the bytes before we
            # invoke the pipeline; malformed incoming mail fails fast here.
            envelope = await gmail_message_to_envelope(raw_bytes)
            session_id = uuid.uuid4().hex

            await self._sessions.create_session(
                app_name=self._app_name,
                user_id=self._user_id,
                session_id=session_id,
            )

            # IngestStage accepts raw EML bytes via user_content.text (same
            # shape scripts/inject_email.py uses). utf-8 with replace is safe
            # for RFC 822 — headers are 7-bit ASCII, body content is
            # base64/quoted-printable encoded.
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
                self._gmail.apply_label, message_id, self._label_id_cached
            )
            _log.info(
                "gmail_message_processed",
                gmail_id=message_id,
                source_message_id=envelope.message_id,
            )
        except Exception as exc:
            _log.error(
                "gmail_message_failed",
                gmail_id=message_id,
                error=str(exc),
            )


__all__ = ["GmailPoller"]
```

- [ ] **Step 5.4: Update `backend/gmail/__init__.py`**

```python
"""Gmail integration package (Track A1 ingress side)."""
from backend.gmail.adapter import gmail_message_to_envelope
from backend.gmail.client import GmailClient
from backend.gmail.poller import GmailPoller
from backend.gmail.scopes import A1_SCOPES, GMAIL_MODIFY_SCOPE

__all__ = [
    "A1_SCOPES",
    "GMAIL_MODIFY_SCOPE",
    "GmailClient",
    "GmailPoller",
    "gmail_message_to_envelope",
]
```

- [ ] **Step 5.5: Run tests — expect 6 passes**

Run: `uv run pytest tests/unit/test_gmail_poller.py -v`

Expected: `6 passed`.

- [ ] **Step 5.6: Commit**

```bash
git add backend/gmail/poller.py backend/gmail/__init__.py tests/unit/test_gmail_poller.py
git commit -m "feat(track-a1): add GmailPoller async loop"
```

---

## Task 6: `scripts/gmail_auth_init.py` one-time OAuth bootstrap

**Files:**
- Create: `scripts/gmail_auth_init.py`

**No tests:** this is an interactive bootstrap script. The behavior (open browser → print token) is tested manually during demo/hackathon setup. The individual libraries (`google-auth-oauthlib`) have their own coverage upstream.

- [ ] **Step 6.1: Create the script**

```python
#!/usr/bin/env python3
"""One-time OAuth bootstrap for the Gmail poller.

Usage:
    uv run python scripts/gmail_auth_init.py path/to/credentials.json

credentials.json is downloaded from Google Cloud Console → APIs &
Services → Credentials → OAuth 2.0 Client IDs → "Desktop application"
type. The file contains `installed.client_id` + `installed.client_secret`.

This script runs InstalledAppFlow.run_local_server(), which pops a
browser to Google's consent screen, captures the authorization code,
exchanges it for access + refresh tokens, and prints all three values
you need for .env.

Do NOT commit credentials.json (already excluded via .gitignore if
the codebase has one; add it to .gitignore if not).

Spec: docs/superpowers/specs/2026-04-24-track-a1-gmail-ingress-design.md
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

from backend.gmail.scopes import A1_SCOPES


def main(credentials_path: Path) -> int:
    if not credentials_path.is_file():
        print(f"error: credentials file not found: {credentials_path}", file=sys.stderr)
        return 2

    flow = InstalledAppFlow.from_client_secrets_file(
        str(credentials_path), scopes=A1_SCOPES
    )
    creds = flow.run_local_server(port=0)

    data = json.loads(credentials_path.read_text())
    installed = data.get("installed", data.get("web", {}))

    print()
    print("=" * 72)
    print("OAuth setup complete. Paste these three lines into .env:")
    print("=" * 72)
    print(f"GMAIL_CLIENT_ID={installed.get('client_id', '<see credentials.json>')}")
    print(f"GMAIL_CLIENT_SECRET={installed.get('client_secret', '<see credentials.json>')}")
    print(f"GMAIL_REFRESH_TOKEN={creds.refresh_token}")
    print("=" * 72)
    print()
    print("Then run: uv run python scripts/gmail_poll.py")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: gmail_auth_init.py path/to/credentials.json", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(Path(sys.argv[1])))
```

- [ ] **Step 6.2: Verify import-time syntax**

Run: `uv run python -c "import ast; ast.parse(open('scripts/gmail_auth_init.py').read())"`

Expected: no output.

- [ ] **Step 6.3: Commit**

```bash
git add scripts/gmail_auth_init.py
git commit -m "feat(track-a1): add one-time OAuth bootstrap script"
```

---

## Task 7: `scripts/gmail_poll.py` long-running entrypoint

**Files:**
- Create: `scripts/gmail_poll.py`
- Create: `.env.example`

- [ ] **Step 7.1: Create the polling script**

Create `scripts/gmail_poll.py`:

```python
#!/usr/bin/env python3
"""Runnable Gmail polling loop.

Usage:
    uv run python scripts/gmail_poll.py

Reads env (via .env or process env):
  GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN (required)
  GMAIL_POLL_INTERVAL_SECONDS (optional, default 30)
  GMAIL_PROCESSED_LABEL (optional, default 'orderintake-processed')
  FIRESTORE_EMULATOR_HOST (if using emulator; else prod Firestore)
  GOOGLE_API_KEY, LLAMA_CLOUD_API_KEY (required by the pipeline)

Ctrl-C exits cleanly. Fatal errors during pipeline construction
propagate. No auto-restart: use a process supervisor (systemd, pm2,
tmux) if you want it to stay up across crashes.

Spec: docs/superpowers/specs/2026-04-24-track-a1-gmail-ingress-design.md
"""
from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService

from backend.gmail.client import GmailClient
from backend.gmail.poller import GmailPoller
from backend.gmail.scopes import A1_SCOPES
from backend.my_agent.agent import _build_default_root_agent


async def _main() -> int:
    load_dotenv()

    required = ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN")
    for var in required:
        if not os.environ.get(var):
            print(f"error: {var} missing from env/.env", file=sys.stderr)
            print(
                "hint: run 'uv run python scripts/gmail_auth_init.py path/to/credentials.json' first",
                file=sys.stderr,
            )
            return 2

    poll_interval = int(os.environ.get("GMAIL_POLL_INTERVAL_SECONDS", "30"))
    label_name = os.environ.get("GMAIL_PROCESSED_LABEL", "orderintake-processed")

    gmail_client = GmailClient(
        refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
        client_id=os.environ["GMAIL_CLIENT_ID"],
        client_secret=os.environ["GMAIL_CLIENT_SECRET"],
        scopes=A1_SCOPES,
    )

    root_agent = _build_default_root_agent()
    session_service = InMemorySessionService()
    runner = Runner(
        app_name="order_intake",
        agent=root_agent,
        session_service=session_service,
    )

    poller = GmailPoller(
        gmail_client=gmail_client,
        runner=runner,
        session_service=session_service,
        root_agent=root_agent,
        label_name=label_name,
        poll_interval_seconds=poll_interval,
    )

    try:
        await poller.run_forever()
    except asyncio.CancelledError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
```

- [ ] **Step 7.2: Create `.env.example`**

Create `.env.example`:

```
# Firestore
FIRESTORE_EMULATOR_HOST=localhost:8080

# LLM + extraction (existing)
GOOGLE_API_KEY=<paste your Vertex/AI Studio API key>
LLAMA_CLOUD_API_KEY=<paste your LlamaCloud API key>

# Gmail ingress — Track A1 (paste from scripts/gmail_auth_init.py output)
GMAIL_CLIENT_ID=
GMAIL_CLIENT_SECRET=
GMAIL_REFRESH_TOKEN=
GMAIL_POLL_INTERVAL_SECONDS=30
GMAIL_PROCESSED_LABEL=orderintake-processed
```

(Adjust existing-var values above by checking the current `.env` / looking at a working setup — the Gmail block is the new addition.)

- [ ] **Step 7.3: Syntax-check the polling script**

Run: `uv run python -c "import ast; ast.parse(open('scripts/gmail_poll.py').read())"`

Expected: no output.

- [ ] **Step 7.4: Commit**

```bash
git add scripts/gmail_poll.py .env.example
git commit -m "feat(track-a1): add long-running Gmail polling entrypoint"
```

---

## Task 8: Gated live integration test

**Files:**
- Create: `tests/integration/test_gmail_poller_fixture.py`

- [ ] **Step 8.1: Create the gated integration test**

```python
"""Live-integration smoke test for Gmail polling.

Gated behind @pytest.mark.gmail_live + env gate — CI + normal dev
runs skip. Intended to run manually once, immediately after
scripts/gmail_auth_init.py has produced a refresh token:

    GMAIL_LIVE_TEST=1 uv run pytest -m gmail_live

Requires GMAIL_CLIENT_ID + GMAIL_CLIENT_SECRET + GMAIL_REFRESH_TOKEN
in env (or .env loaded by the test itself). Also requires at least
one email sitting in the target inbox not carrying the label yet,
otherwise the test is trivially green.

Spec: docs/superpowers/specs/2026-04-24-track-a1-gmail-ingress-design.md
"""
from __future__ import annotations

import os

import pytest

pytestmark = [pytest.mark.gmail_live, pytest.mark.asyncio]


def _live_setup_available() -> bool:
    return (
        os.environ.get("GMAIL_LIVE_TEST") == "1"
        and os.environ.get("GMAIL_CLIENT_ID")
        and os.environ.get("GMAIL_CLIENT_SECRET")
        and os.environ.get("GMAIL_REFRESH_TOKEN")
    )


@pytest.mark.skipif(not _live_setup_available(), reason="GMAIL_LIVE_TEST + credentials not set")
async def test_one_tick_against_real_inbox_does_not_crash():
    from backend.gmail.client import GmailClient
    from backend.gmail.poller import GmailPoller
    from backend.gmail.scopes import A1_SCOPES

    gmail_client = GmailClient(
        refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
        client_id=os.environ["GMAIL_CLIENT_ID"],
        client_secret=os.environ["GMAIL_CLIENT_SECRET"],
        scopes=A1_SCOPES,
    )

    # Smoke: just list messages — don't construct the full pipeline
    # (that'd require seeded master data + emulator / real Firestore).
    # The list_unprocessed call exercises the entire auth + API plumbing.
    ids = gmail_client.list_unprocessed(label_name="orderintake-processed")
    assert isinstance(ids, list)
    # And verify label_id_for creates the label if missing (idempotent)
    label_id = gmail_client.label_id_for("orderintake-processed")
    assert isinstance(label_id, str)
    assert label_id.startswith(("Label_", "CATEGORY_", "INBOX", "IMPORTANT"))
```

- [ ] **Step 8.2: Register the marker in pytest config**

Check `pyproject.toml` for an existing `[tool.pytest.ini_options]` section with `markers = [...]`. If present, append `"gmail_live: gated live Gmail integration (set GMAIL_LIVE_TEST=1)"`. If absent, the test still runs but pytest warns about unknown marker — minor, not a blocker.

- [ ] **Step 8.3: Verify the test auto-skips in the default run**

Run: `uv run pytest tests/integration/test_gmail_poller_fixture.py -v`

Expected: `1 skipped` (because `GMAIL_LIVE_TEST` is not set).

- [ ] **Step 8.4: Commit**

```bash
git add tests/integration/test_gmail_poller_fixture.py pyproject.toml
git commit -m "test(track-a1): gated live integration smoke test for Gmail poller"
```

---

## Task 9: Operator documentation

**Files:**
- Modify: `backend/my_agent/README.md`
- Modify: `research/Order-Intake-Sprint-Status.md`
- Modify: `Glacis-Order-Intake.md`

- [ ] **Step 9.1: Add a "Gmail ingress (A1)" section to `backend/my_agent/README.md`**

Find the main README.md for the agent module. Insert a new section after the existing "Launch" section:

```markdown
## Gmail ingress (Track A1, polling)

One-time setup:

1. Create an OAuth 2.0 Desktop-application client in
   [Google Cloud Console → APIs & Services → Credentials](https://console.cloud.google.com/apis/credentials).
   Download the JSON as `credentials.json`.

2. Enable the Gmail API on the same project:
   [APIs & Services → Library → Gmail API → Enable](https://console.cloud.google.com/apis/library/gmail.googleapis.com).

3. Run the OAuth bootstrap:

   ```bash
   uv run python scripts/gmail_auth_init.py path/to/credentials.json
   ```

   A browser pops up. Sign in with the Gmail account the agent will read.
   Grant the `gmail.modify` permission.

4. Copy the three printed lines into `.env`:

   ```
   GMAIL_CLIENT_ID=...
   GMAIL_CLIENT_SECRET=...
   GMAIL_REFRESH_TOKEN=...
   ```

Run the poller:

```bash
uv run python scripts/gmail_poll.py
```

Every 30 seconds the poller pulls messages from the inbox that do NOT carry
the `orderintake-processed` Gmail label, drives each through the 9-stage
pipeline, then applies the label. Ctrl-C exits cleanly.

**What to watch for:**

- Structured log line `gmail_message_processed` per successful run — carries `gmail_id` + `source_message_id`.
- Structured log line `gmail_message_failed` on any per-message error — the message stays unlabeled and will be retried next poll.
- Audit log entries (if Track D has landed) under one `correlation_id` per Gmail message.
- The `orderintake-processed` label appearing on messages in Gmail's UI.

**Limitations (in scope only for Track A1):**

- Polling only. Push-based ingestion via `users.watch()` + Pub/Sub + Cloud Run is Track A3.
- Read-side only. Outbound `messages.send` for clarify / confirmation bodies is Track A2.
- Single-inbox only. Multi-inbox deployment lives in Track A3.
- No Secret Manager. Credentials live in `.env`.
```

- [ ] **Step 9.2: Flip §1 Signal Ingestion row in `research/Order-Intake-Sprint-Status.md`**

Find the row beginning `| **1. Signal ingestion** |` in the Status table. Update the "What we have" cell to add A1, and the "What's left" cell to point at A3 for push:

```
| **1. Signal ingestion** | Gmail watch → Pub/Sub → attachment download | Fixtures ✓ + 4/4 format wrappers (PDF/CSV/XLSX/EDI) ✓ + clarify-reply fixture ✓ + `backend/ingestion/` (`EmailEnvelope` + `parse_eml`) ✓ + `scripts/inject_email.py` CLI ✓ + **Gmail polling ingress ✓** (Track A1: `backend/gmail/` + `scripts/gmail_auth_init.py` + `scripts/gmail_poll.py`; installed-app OAuth + Gmail-label dedup + raw-message → parse_eml reuse) | Wrap remaining 6 non-`.eml` fixtures (iterative, non-blocking). Push-based ingress (watch + Pub/Sub + webhook) deferred to Track A3. |
```

Also add to the Built inventory block (alphabetical with Track C/D entries):

```
backend/gmail/__init__.py, scopes.py                                    ✓ Track A1 (<sha-task-1>) — A1_SCOPES constant (gmail.modify)
backend/gmail/client.py                                                 ✓ Track A1 (<sha-task-3>) — GmailClient sync wrapper
backend/gmail/adapter.py                                                ✓ Track A1 (<sha-task-4>) — gmail_message_to_envelope via parse_eml reuse
backend/gmail/poller.py                                                 ✓ Track A1 (<sha-task-5>) — GmailPoller async loop
scripts/gmail_auth_init.py                                              ✓ Track A1 (<sha-task-6>) — one-time OAuth bootstrap
scripts/gmail_poll.py                                                   ✓ Track A1 (<sha-task-7>) — long-running polling entrypoint
tests/integration/test_gmail_poller_fixture.py                          ✓ Track A1 (<sha-task-8>) — 1 gated live integration test
```

Update the "What to build first" section to note A1 complete:

```
- **Track A1 — Gmail polling ingress** ✓ landed 2026-04-24; sprint status Built inventory above.
- **Track A2 — Gmail egress** (messages.send for clarify + confirmation bodies). Extends GmailClient with gmail.send scope. Blocked only on A1 landing.
- **Track A3 — Push-based ingestion** (watch + Pub/Sub + webhook + History API + Cloud Run). Replaces polling loop; same adapter + pipeline reuse.
```

- [ ] **Step 9.3: Flip §1 Email-Ingestion bullets in `Glacis-Order-Intake.md`**

Find the `§1. Signal Ingestion` section. Flip the installed-app-flow part:

```markdown
- `[MVP ✓]` **Gmail OAuth via installed-app flow + refresh-token in `.env`** — `scripts/gmail_auth_init.py` runs the one-time InstalledAppFlow; refresh token lives in `.env`. Covers personal `gmail.com` accounts without requiring Workspace + domain-wide delegation. MVP: landed 2026-04-24 via Track A1 (<sha-task-6>). Post-hackathon: Secret Manager swap is one-line. Source: `Email-Ingestion.md`.

- `[MVP ✓]` **Gmail polling ingress loop** — `scripts/gmail_poll.py` runs every 30s, pulls `in:inbox -label:orderintake-processed`, drives each through the 9-stage pipeline in-process, applies label after. MVP: landed 2026-04-24 via Track A1. Post-hackathon: swapped for push (watch + Pub/Sub + webhook) in Track A3. Source: `Email-Ingestion.md`.
```

Keep the following bullets at `[Post-MVP]` tagged for A3:
- `Gmail users.watch() registration` (A3)
- `Pub/Sub push subscription + Cloud Run webhook` (A3)
- `History API sync + dedup` (A3)
- `Gmail OAuth + domain-wide delegation` (A3 / Phase 4)

- [ ] **Step 9.4: Commit**

```bash
git add backend/my_agent/README.md research/Order-Intake-Sprint-Status.md Glacis-Order-Intake.md
git commit -m "docs(track-a1): flip Gmail polling ingress to [MVP ✓] across status + roadmap"
```

---

## Task 10: Final verification

- [ ] **Step 10.1: Full unit suite — no regressions**

Run: `uv run pytest tests/unit -v 2>&1 | tail -20`

Expected: all green. Test count: baseline (post-Track-C + Track-D would land at ~349) + 19 new A1 = ~368 unit.

- [ ] **Step 10.2: Full integration suite — no regressions**

Run: `uv run pytest tests/integration -v 2>&1 | tail -20`

Expected: all green + 1 skip (`test_gmail_poller_fixture.py` auto-skips when `GMAIL_LIVE_TEST` is unset).

- [ ] **Step 10.3: Dry-import the scripts**

Run: `uv run python -c "import scripts.gmail_poll; import scripts.gmail_auth_init" 2>&1 || echo 'NOTE: scripts are not importable as modules; syntax-check via ast instead'`

Expected: either silent success or the NOTE (if `scripts/` isn't set up as a package). The `ast.parse` steps already cover syntax — this is extra belt-and-suspenders.

- [ ] **Step 10.4: Manual live smoke (optional, high-confidence gate)**

Prerequisites:
- Credentials in `.env` per Task 9.1's runbook
- Firestore emulator running + master data seeded
- `GOOGLE_API_KEY` + `LLAMA_CLOUD_API_KEY` in `.env`

Send a fixture-style email to the Gmail account (e.g., forward `data/pdf/patterson_po-28491.wrapper.eml` to the agent's inbox, or compose a fresh "I want 50 units of SKU X" email).

Run:
```bash
uv run python scripts/gmail_poll.py
```

Within ~30s:
- Expect a structured log line `gmail_message_processed`
- Expect the `orderintake-processed` label to appear on the message in Gmail
- Expect an `OrderRecord` or `ExceptionRecord` to appear in Firestore

Ctrl-C. Restart. Message should NOT be reprocessed (label is present).

- [ ] **Step 10.5: Done**

Track A1 closed. Next session picks up Track A2 (Gmail egress) via brainstorm → spec → plan → execute.

---

## Self-review

**Spec coverage:**
- ✅ Decision 1 (in-process Runner.run_async) → Task 5 (poller.py + tests)
- ✅ Decision 2 (installed-app OAuth, refresh token in .env) → Task 1 (scopes) + Task 3 (client) + Task 6 (auth_init script) + Task 7 (poll script)
- ✅ Decision 3 (Gmail label dedup) → Task 3 (label_id_for + apply_label) + Task 5 (poller invocation ordering)
- ✅ Decision 4 (format='RAW' + parse_eml reuse) → Task 4 (adapter)
- ✅ Scopes constants → Task 1
- ✅ Fail-open per-message error handling → Task 5 (_process_one catches + logs)
- ✅ Sequential processing per tick → Task 5 (for-loop, no gather)
- ✅ Label caching + auto-create → Task 3 tests 6-7 + Task 5 tick first-call caching
- ✅ OAuth bootstrap script → Task 6
- ✅ Runnable polling script → Task 7
- ✅ .env.example → Task 7
- ✅ Dependency additions → Task 2
- ✅ 8 + 3 + 6 + 2 = 19 unit tests → Tasks 3, 4, 5, 1
- ✅ Gated live integration → Task 8
- ✅ Operator README → Task 9
- ✅ Status + Glacis doc flips → Task 9

**Placeholder scan:**
- Task 4.1 references `FIXTURE_WITH_THREAD = Path("data/email/birch_valley_clarify_reply.eml")` and `FIXTURE_WITH_ATTACHMENT = Path("data/pdf/patterson_po-28491.wrapper.eml")` — both verified to exist in the current tree via the sprint-status Built inventory. Task 4.6 includes a grep-for-alternative fallback if paths are wrong. Not a placeholder.
- Task 7.2 `.env.example` has empty values for `GMAIL_CLIENT_ID=` etc. — those ARE intentional empty placeholders in the template file (they're filled at setup time); that's the correct shape for an `.env.example`.
- Task 9.2 line for Built inventory uses `<sha-task-N>` placeholders that the executor fills in with actual commit SHAs. This is the same pattern Track C's plan used; acceptable.
- No `TBD` / `TODO` / `fill in` / `similar to` patterns anywhere else.

**Type consistency:**
- `GmailClient.list_unprocessed(*, label_name, max_results=50) → list[str]` — consistent Task 3 impl + tests + Task 5 poller usage.
- `GmailClient.get_raw(message_id) → bytes` — consistent Task 3 + Task 5.
- `GmailClient.label_id_for(label_name) → str` — consistent Task 3 + Task 5.
- `GmailClient.apply_label(message_id, label_id) → None` — consistent Task 3 + Task 5.
- `gmail_message_to_envelope(raw_rfc822: bytes) → EmailEnvelope` — consistent Task 4 + Task 5.
- `GmailPoller` constructor kwargs (gmail_client / runner / session_service / root_agent / label_name / poll_interval_seconds) — consistent Task 5 + Task 7.
- `A1_SCOPES = [GMAIL_MODIFY_SCOPE]` — consistent Task 1 + Task 3 + Task 6 + Task 7.

No inconsistencies.

**Scope check:** 10 tasks, each 3-8 steps, TDD-cycled. Estimated execution: 4-5 hours for a focused implementer. Single-plan-sized.

All good — no fixes needed inline.
