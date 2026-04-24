---
type: design-spec
topic: "Track A1 — Gmail Ingress (Polling)"
track: A1
date: 2026-04-24
parent: "research/Order-Intake-Sprint-Status.md"
source_spec: "Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Email-Ingestion.md"
status: approved-for-implementation
depends_on:
  - "Track C (duplicate detection) — optional; ingress works without it but naturally benefits"
  - "Track D (audit log) — optional; correlation_id / audit events capture Gmail-driven runs for free when Track D is in"
blocks:
  - "Track A2 (Gmail egress) — extends GmailClient scope with gmail.send"
  - "Track A3 (push-based ingestion) — replaces polling loop with watch + Pub/Sub + webhook"
tags:
  - design-spec
  - track-a1
  - gmail
  - ingress
  - polling
  - oauth
---

# Track A1 — Gmail Ingress (Polling) — Design

## Summary

Polling-loop Gmail ingress that pulls new messages from a single Gmail inbox every 30 seconds and drives each through the existing 9-stage pipeline in-process. OAuth via the installed-app flow (one-time setup → refresh token in `.env`). Deduplication via a Gmail label `orderintake-processed` applied after each pipeline run. Gmail-message → `EmailEnvelope` conversion reuses `parse_eml` verbatim by downloading `format='RAW'` and routing bytes through a tempfile. Sequential processing per poll; no concurrency.

This closes the Track-A polling slice of Glacis `Email-Ingestion.md`. `users.watch()` / Pub/Sub / Cloud Run webhook / History API remain `[Post-MVP]`, implemented separately as Track A3 (push-based ingestion).

## Context

- Existing `backend/ingestion/email_envelope.py` defines the `EmailEnvelope` + `EmailAttachment` contract; `backend/ingestion/eml_parser.py` parses `.eml` bytes/paths into that contract. 26+ unit tests cover the parser across all 10 fixture `.eml` files.
- `scripts/inject_email.py` is the existing CLI that takes a `.eml` path and drives it through the pipeline via `Runner.run_async`. Track A1 emulates that invocation pattern but sources messages from Gmail instead of disk fixtures.
- `backend/my_agent/agent.py:_build_default_root_agent()` constructs the production root agent and returns it. Track A1 consumes this helper as-is.
- ADK's `Runner` + `InMemorySessionService` are the canonical in-process invocation surface (reference: Track A Step 6 integration test + `scripts/smoke_run.py`).
- No existing code touches the Gmail API or OAuth.
- `.env` is the existing credential-carrying file (referenced by `GOOGLE_API_KEY`, `LLAMA_CLOUD_API_KEY`, `FIRESTORE_EMULATOR_HOST`). Adding Gmail vars alongside.

## Architectural decisions

The four foundational calls, each with trade-offs explicitly considered and rejected alternatives documented.

### Decision 1 — Trigger model: in-process `Runner.run_async`

A new `GmailPoller.run_forever()` async loop runs in the same process as the pipeline. Per discovered message: fetch → adapter → `Runner.run_async(...)` → await completion → label. One process, shared state, no intermediate storage.

**Rejected:**
- **File-drop** (poller writes `.eml` to disk, separate process invokes `scripts/inject_email.py`) — two processes to coordinate; disk-as-queue dance.
- **Firestore queue** (poller writes to `gmail_queue` collection; consumer loop pulls) — extra collection + schema + consumer for in-memory work. Closer to the A3 push model but duplicates infra.

### Decision 2 — OAuth: installed-app flow, refresh token in `.env`

One-time `scripts/gmail_auth_init.py` run locally uses `google_auth_oauthlib.flow.InstalledAppFlow` to pop a browser + consent screen. The resulting refresh token is printed to stdout; operator pastes into `.env` as `GMAIL_REFRESH_TOKEN`. Poller at startup constructs `google.oauth2.credentials.Credentials` and auto-refreshes access tokens as needed.

**Rejected:**
- **Service account + domain-wide delegation** — requires Google Workspace + admin access to the domain. Not viable for personal `gmail.com` accounts used in hackathon setups.
- **Device flow** — same refresh-token outcome as installed-app flow with an extra indirection that adds no value on a machine with a browser available during setup.

### Decision 3 — Dedup: Gmail label `orderintake-processed`

Poll query: `q='in:inbox -label:orderintake-processed'`. After each successful pipeline run (regardless of AUTO / CLARIFY / ESCALATE outcome), `messages.modify` applies the label. Idempotent; visible in Gmail UI; restart-safe. No new Firestore collection.

**Rejected:**
- **Firestore `gmail_processed` collection keyed by Gmail internal message id** — functional but invisible in Gmail UI and requires a second read per candidate message.
- **Dedup via `orders.source_message_id` / `exceptions.source_message_id` match** — existing idempotency keys are based on the RFC 5322 Message-ID header, which is only available after downloading the full message contents. Can't dedup at the `list()` step → every poll re-downloads every message in the filter window. Also: messages that crashed mid-pipeline before any Firestore write are replayed every poll (deterministic-crash loop).

### Decision 4 — Adapter: `format='RAW'` → `parse_eml`

`messages.get(id=X, format='RAW')` returns the full RFC 822 message as base64url-encoded bytes. Decode → bytes → write to `NamedTemporaryFile` → feed to existing `backend.ingestion.eml_parser.parse_eml(Path)`. Zero new parsing code. Reuses every multipart / attachment / encoding edge case already battle-tested by the existing `test_eml_parser.py` suite + 10 fixture `.eml` files.

**Rejected:**
- **Walk `message.payload.parts` directly (`format='FULL'`)** — requires reimplementing multipart tree walking + Content-Type handling + Content-Disposition sniffing + `attachments.get` for large parts. 100-200 lines of new bug surface.
- **Hybrid (`FULL` for metadata, `RAW` for body)** — two API calls per message; `parse_eml` already parses all the headers we'd want from the `FULL` payload, so the extra call is wasted.

## Components

### New file — `backend/gmail/__init__.py`

Package marker. Re-exports `GmailClient`, `GmailPoller`, `gmail_message_to_envelope`, `GMAIL_MODIFY_SCOPE`.

### New file — `backend/gmail/scopes.py`

```python
"""OAuth scopes for the Gmail-ingestion track. Each track adds the
scopes it needs; A1 takes gmail.modify (read + labels); A2 will add
gmail.send; A3 adds gmail.readonly as a watch target (no extra scope)."""

GMAIL_MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"

A1_SCOPES = [GMAIL_MODIFY_SCOPE]

__all__ = ["GMAIL_MODIFY_SCOPE", "A1_SCOPES"]
```

### New file — `backend/gmail/client.py`

Thin sync wrapper around `googleapiclient.discovery.build(...)` + `google.oauth2.credentials.Credentials`. Sync because `googleapiclient` is sync-only; async boundary lives at `poller.py` via `asyncio.to_thread`.

```python
"""Authed Gmail Resource wrapper for A1 ingress.

Constructs a Credentials object from installed-app refresh token and
builds a sync googleapiclient Resource. Only the surface used by
the poller is exposed here — list_unprocessed, get_raw, label_id_for,
apply_label. Track A2 extends this class with send_message.
"""
from __future__ import annotations

import base64
from typing import Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource, build


_GMAIL_TOKEN_URL = "https://oauth2.googleapis.com/token"


class GmailClient:
    """Sync wrapper around a Gmail API Resource.

    Construct once per process with refresh_token + client_id +
    client_secret + scopes; call the small method surface to avoid
    scattering raw .users().messages().list() calls everywhere.
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

    # --- read surface ---

    def list_unprocessed(
        self,
        *,
        label_name: str,
        max_results: int = 50,
    ) -> list[str]:
        """Return Gmail message ids not yet carrying `label_name`."""
        query = f"in:inbox -label:{label_name}"
        resp = (
            self._service.users()
            .messages()
            .list(userId="me", q=query, maxResults=max_results)
            .execute()
        )
        return [m["id"] for m in resp.get("messages", [])]

    def get_raw(self, message_id: str) -> bytes:
        """Return the raw RFC 822 bytes of a single message."""
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

    # --- label surface ---

    def label_id_for(self, label_name: str) -> str:
        """Resolve label id, creating the label if missing. Cached."""
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

### New file — `backend/gmail/adapter.py`

```python
"""Raw Gmail bytes → EmailEnvelope via parse_eml.

parse_eml takes a filesystem Path today. This helper writes raw RFC
822 bytes to a NamedTemporaryFile so we can reuse every multipart /
attachment / encoding edge case already handled by the existing
parser. Eight extra lines of code instead of a 150-line re-implementation.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from backend.ingestion.email_envelope import EmailEnvelope
from backend.ingestion.eml_parser import parse_eml


async def gmail_message_to_envelope(raw_rfc822: bytes) -> EmailEnvelope:
    """Parse raw bytes via the existing .eml pipeline.

    Writes to a NamedTemporaryFile because parse_eml is Path-only
    (by design — its current callers are all file-based). Track A3
    will add a bytes overload if push-based ingestion volumes warrant.
    """
    with tempfile.NamedTemporaryFile(
        mode="wb", suffix=".eml", delete=False
    ) as tf:
        tf.write(raw_rfc822)
        tmp_path = Path(tf.name)
    try:
        return parse_eml(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)


__all__ = ["gmail_message_to_envelope"]
```

### New file — `backend/gmail/poller.py`

```python
"""Async polling loop orchestrating GmailClient + adapter + pipeline.

Sequential per tick: list unprocessed → for each message, get_raw →
adapt → Runner.run_async → apply_label. Errors per message are
logged and swallowed; the loop continues. SIGINT / SIGTERM exits
cleanly via asyncio.CancelledError.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

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
        self._label_id_cached: str | None = None

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
            envelope = await gmail_message_to_envelope(raw_bytes)
            session_id = uuid.uuid4().hex

            await self._sessions.create_session(
                app_name=self._app_name,
                user_id=self._user_id,
                session_id=session_id,
            )

            new_message = types.Content(
                role="user",
                parts=[types.Part.from_text(text=envelope.to_eml_bytes().decode("utf-8", errors="replace"))],
            )

            # Drain events (audit log captures them; we don't need them here)
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

**Note on `envelope.to_eml_bytes()`:** the adapter currently returns `EmailEnvelope`. To re-drive it through `IngestStage`, the poller needs to pass either the raw bytes or a path. The existing `scripts/inject_email.py` pattern is to pass the raw RFC 822 bytes through `new_message.parts[0].text` and have `IngestStage` sniff the MIME header. Path and raw-content are both supported by `IngestStage`. Check: does `EmailEnvelope` expose the raw bytes? If not, the poller retains the raw bytes from `get_raw` and passes those directly (never materializing an envelope) — in which case the adapter is a validation-only step or moves into `IngestStage`. **Implementation-time decision:** pass the raw bytes from `get_raw` directly as `new_message.parts[0].text` (utf-8 decoded with errors='replace'). The adapter's role becomes "validate that parse_eml doesn't raise on these bytes"; if it raises, we log + skip the message instead of feeding broken data into the pipeline.

### New file — `scripts/gmail_auth_init.py`

```python
"""One-time OAuth bootstrap for the Gmail poller.

Usage:
    uv run python scripts/gmail_auth_init.py path/to/credentials.json

Runs InstalledAppFlow, prints the refresh token. Paste into .env as
GMAIL_REFRESH_TOKEN. Also prints the client_id and client_secret
from the credentials.json for easy .env copy-paste.

credentials.json is downloaded from Google Cloud Console → APIs &
Services → Credentials → OAuth 2.0 Client IDs → Desktop application.
Do NOT commit credentials.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

from backend.gmail.scopes import A1_SCOPES


def main(credentials_path: Path) -> int:
    flow = InstalledAppFlow.from_client_secrets_file(
        str(credentials_path), scopes=A1_SCOPES
    )
    creds = flow.run_local_server(port=0)
    data = json.loads(credentials_path.read_text())
    installed = data.get("installed", data.get("web", {}))
    print()
    print("=" * 72)
    print("Copy these into .env:")
    print("=" * 72)
    print(f"GMAIL_CLIENT_ID={installed.get('client_id', '<see credentials.json>')}")
    print(f"GMAIL_CLIENT_SECRET={installed.get('client_secret', '<see credentials.json>')}")
    print(f"GMAIL_REFRESH_TOKEN={creds.refresh_token}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: gmail_auth_init.py path/to/credentials.json", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(Path(sys.argv[1])))
```

### New file — `scripts/gmail_poll.py`

```python
"""Runnable long-lived Gmail polling loop.

Usage:
    uv run python scripts/gmail_poll.py

Reads env (via .env or process env):
  GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN (required)
  GMAIL_POLL_INTERVAL_SECONDS (optional, default 30)
  GMAIL_PROCESSED_LABEL (optional, default 'orderintake-processed')
  FIRESTORE_EMULATOR_HOST (if using emulator; else use prod Firestore)
  GOOGLE_API_KEY, LLAMA_CLOUD_API_KEY (required by the pipeline itself)

Ctrl-C exits cleanly. Any fatal error in pipeline construction
propagates (missing env vars, missing Firestore, missing emulator,
etc.) — the process does NOT daemonize on its own; use a process
supervisor (systemd / pm2 / tmux) if you want auto-restart.
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

    for var in ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN"):
        if not os.environ.get(var):
            print(f"error: {var} missing from env/.env", file=sys.stderr)
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

### Modified — `pyproject.toml`

Add to `[project.dependencies]`:

- `google-api-python-client>=2.140`
- `google-auth>=2.35`
- `google-auth-oauthlib>=1.2`
- `google-auth-httplib2>=0.2`
- `python-dotenv>=1.0` (if not already present)

### Modified — `.env.example` (create if missing)

```
# Gmail ingress (Track A1)
GMAIL_CLIENT_ID=<paste from gmail_auth_init.py output>
GMAIL_CLIENT_SECRET=<paste from gmail_auth_init.py output>
GMAIL_REFRESH_TOKEN=<paste from gmail_auth_init.py output>
GMAIL_POLL_INTERVAL_SECONDS=30
GMAIL_PROCESSED_LABEL=orderintake-processed
```

## Data flow

### Startup

```
scripts/gmail_poll.py
  load_dotenv()
  GmailClient(refresh_token, client_id, client_secret, scopes=[gmail.modify])
  root_agent = _build_default_root_agent()          # existing Track A path
  session_service = InMemorySessionService()
  runner = Runner(app_name, root_agent, session_service)
  poller = GmailPoller(gmail_client, runner, session_service, root_agent, ...)
  await poller.run_forever()
```

### Steady-state poll cycle

```
GmailPoller.run_forever()
  loop:
    _tick()
      label_id = gmail.label_id_for("orderintake-processed")  # cached after first call
      ids = gmail.list_unprocessed(label_name=...)             # via asyncio.to_thread
      for id in ids (sequential):
        _process_one(id)
          raw = gmail.get_raw(id)                              # to_thread
          envelope = await gmail_message_to_envelope(raw)      # validation step
          session_id = uuid4().hex
          session_service.create_session(app_name, user_id, session_id)
          new_message = types.Content(text=raw.decode('utf-8', errors='replace'))
          async for _ in runner.run_async(user_id, session_id, new_message):
              pass
          gmail.apply_label(id, label_id)                       # to_thread
      (end for)
    sleep(poll_interval)
```

### How the envelope reaches `IngestStage`

`IngestStage._audited_run` (post-Track-D) accepts `ctx.user_content.parts[0].text` as either a path-to-eml or raw-eml-bytes-as-string (MIME-header sniff). The poller passes raw RFC 822 bytes decoded as utf-8 via `parts=[types.Part.from_text(...)]`. This matches the existing `scripts/inject_email.py` raw-content path exactly. Zero `IngestStage` changes.

### Track D (audit log) interaction

If Track D has landed, `IngestStage` mints `correlation_id` per invocation and every stage emits audit events automatically. Gmail-triggered runs get full audit coverage with zero extra wiring from A1. If Track D has not landed, A1 works without it — the audit integration is orthogonal.

### Track C (duplicate detection) interaction

If Track C has landed, re-sending the same email content (same basket) within 90 days produces ESCALATE with `reason="duplicate of <prior_order_id>"` — the ordinary pipeline behavior. Gmail-triggered runs get duplicate detection automatically.

## Error handling

| Scenario | Behavior |
|---|---|
| `messages.list` 5xx / network error | Caught in `_tick`'s try/except; logged, loop sleeps, next tick retries. No label changes. |
| `messages.get` 404 / deleted message | Caught in `_process_one`; logged with message_id; label NOT applied (message no longer exists anyway). |
| `parse_eml` raises `EmlParseError` inside `gmail_message_to_envelope` | Caught in `_process_one`; logged with message_id; label NOT applied. Next poll retries. Deterministic parse failure → infinite retry — mitigated by Track D audit log visibility. |
| Pipeline crash during `runner.run_async` | Caught in `_process_one`; logged; label NOT applied. Retry on next poll. Same deterministic-crash caveat. |
| OAuth 401 / `google.auth.exceptions.RefreshError` | `Credentials.refresh()` attempt is handled automatically by google-auth library on each API call. If refresh itself fails, exception propagates up through `asyncio.to_thread`, caught in `_tick`, logged as tick-failure, loop continues. Operator sees repeated error → regenerates refresh token via `gmail_auth_init.py` → restarts `gmail_poll.py`. |
| Label not found on `apply_label` (someone deleted it in Gmail UI) | Raises an API error; caught in `_process_one`; logged; label cache invalidated implicitly (next `_tick` re-fetches via `label_id_for`). |
| Two poller processes running against the same inbox | Not prevented. Gmail `modify` is idempotent per message, but double-processing would invoke the pipeline twice for each new message and write audit events twice (different correlation_ids). **Runbook:** run exactly one poller per inbox. Not enforced in code. |
| SIGINT / SIGTERM | `asyncio.run` propagates `CancelledError` into `run_forever`; the except block logs `gmail_poller_stopping` and re-raises. Mid-tick completion is best-effort (whatever's in `_process_one` finishes). |
| `envelope.message_id` is missing (Gmail message with no RFC 5322 Message-ID header — unusual but possible for malformed incoming mail) | `parse_eml` raises `EmlParseError`; handled by the `EmlParseError` row above. |

### Logging

All structured via `backend.utils.logging`:
- `gmail_poller_start` — one emit at process start
- `gmail_poller_stopping` — one emit on clean shutdown
- `gmail_poller_tick_failed` — per-tick failure (rare)
- `gmail_message_processed` — per successful message with `gmail_id` + `source_message_id`
- `gmail_message_failed` — per failed message with `gmail_id` + `error`

## Testing

### Unit — new `tests/unit/test_gmail_client.py` (8 tests)

All tests patch `googleapiclient.discovery.build` to return a `MagicMock` Resource — no network.

1. `list_unprocessed(label_name='X')` issues a `list` call with `q='in:inbox -label:X'` + `maxResults=50`
2. `list_unprocessed` returns `[]` when the API returns `{}`
3. `list_unprocessed` returns message ids in order when the API returns `{messages: [{id: 'a'}, {id: 'b'}]}`
4. `get_raw` base64url-decodes the API's `raw` field and returns bytes
5. `get_raw` raises `ValueError` when the API response is missing the `raw` field
6. `label_id_for` returns existing label id from `labels.list` when found, and caches — subsequent calls don't re-hit the API
7. `label_id_for` calls `labels.create` when the label is missing, and returns the new id
8. `apply_label` calls `messages.modify` with the expected body

### Unit — new `tests/unit/test_gmail_adapter.py` (3 tests)

1. `gmail_message_to_envelope(raw_bytes)` returns a valid `EmailEnvelope` — feed it bytes from an existing fixture (`.eml` file read as bytes) and assert envelope fields match
2. Thread headers (`In-Reply-To`, `References`) propagate into `envelope.in_reply_to` — use the birch_valley_clarify_reply fixture
3. Attachment bytes round-trip — envelope.attachments[0].content equals the fixture's original attachment bytes

### Unit — new `tests/unit/test_gmail_poller.py` (6 tests)

Use `AsyncMock(spec=GmailClient)` + `AsyncMock(spec=Runner)` + `AsyncMock(spec=InMemorySessionService)`.

1. `_tick` with no messages → `list_unprocessed` called, `get_raw` NOT called, sleep-loop continues
2. `_tick` with 1 message id → `_process_one` invoked with that id
3. `_process_one` calls sequence `get_raw → adapter → runner.run_async → apply_label` in order (assert via `mock.mock_calls`)
4. `_process_one` pipeline crash → skips `apply_label`, swallows exception, next message still processed
5. `_tick` with 3 messages processes all sequentially in order
6. `run_forever` exits on `asyncio.CancelledError` without re-raising past its except handler

### Unit — new `tests/unit/test_gmail_auth.py` (2 tests)

1. `GmailClient.__init__` constructs `Credentials` with all required fields (mock `build`; inspect `Credentials` kwargs)
2. `A1_SCOPES` is exactly `[GMAIL_MODIFY_SCOPE]` and `GMAIL_MODIFY_SCOPE` equals `"https://www.googleapis.com/auth/gmail.modify"`

### Integration — new `tests/integration/test_gmail_poller_fixture.py` (1 test, gated)

Skipped unless `GMAIL_REFRESH_TOKEN` + `GMAIL_CLIENT_ID` + `GMAIL_CLIENT_SECRET` are set AND `GMAIL_LIVE_TEST=1` is set. Live: invoke one `_tick()` against a real inbox, assert the poller completes without error. Gated behind `@pytest.mark.gmail_live`. CI skips by default; developer runs manually against a test account.

### Total test delta

- New unit: 8 + 3 + 6 + 2 = **19**
- New integration: **1** (gated, auto-skip)
- Baseline after Track C + D expected: ~349 unit → ~367 after A1.

## Out of scope (explicit non-goals)

- **Push-based ingestion** (`users.watch()` / Pub/Sub / Cloud Run webhook / History API) — Track A3.
- **Gmail send** (messages.send for clarify + confirmation) — Track A2.
- **Secret Manager for credentials** — `.env` is MVP.
- **Multi-inbox support** — single `user_id='me'` inbox only.
- **Historical backfill** — no pre-existing messages are processed unless un-labeled manually.
- **Rate-limit backoff beyond naive `sleep(poll_interval)`** — Phase 3 hardening.
- **Thread correlation via Gmail `threadId`** — `parse_eml` already extracts `In-Reply-To` / `References` from RFC 5322 headers, which feeds `ReplyShortCircuitStage` correctly.
- **Signed-webhook verification** — no webhook in A1.
- **Process-supervisor integration** (systemd unit, Docker entrypoint, pm2 config) — documented in A3 as part of deployment.
- **Dashboard surface for Gmail poller health** — none; stdout logs are the UI.

## Success criteria

1. Running `scripts/gmail_auth_init.py <credentials.json>` prints a usable refresh token + client_id + client_secret; pasting into `.env` is sufficient setup.
2. Running `scripts/gmail_poll.py` enters a 30s-cadence loop; soak for 10 minutes against an empty inbox without crashing.
3. Sending a fixture-equivalent email to the agent's Gmail address results in a pipeline run within one poll interval (~30s) AND the `orderintake-processed` label lands on the message within ~5s of pipeline completion.
4. The pipeline run lands an `OrderRecord` in `orders` (AUTO_APPROVE path) or an `ExceptionRecord` in `exceptions` (CLARIFY / ESCALATE path), indistinguishable from a fixture-driven invocation.
5. If Track D has landed: the audit_log captures the full ~23-event trace under one `correlation_id` per Gmail message, with `source_message_id` matching the RFC 5322 Message-ID header of the Gmail message.
6. Crashing the poller mid-run (Ctrl-C) and restarting does NOT reprocess already-labeled messages; any message not labeled before crash IS reprocessed.
7. No regression in the existing unit + integration test suite.

## Files touched (summary)

| Type | Path | Change |
|---|---|---|
| New | `backend/gmail/__init__.py` | Package marker + re-exports |
| New | `backend/gmail/scopes.py` | `GMAIL_MODIFY_SCOPE` + `A1_SCOPES` constants |
| New | `backend/gmail/client.py` | `GmailClient` sync wrapper (list / get / label) |
| New | `backend/gmail/adapter.py` | `gmail_message_to_envelope(bytes) → EmailEnvelope` |
| New | `backend/gmail/poller.py` | `GmailPoller` async loop |
| New | `scripts/gmail_auth_init.py` | One-time OAuth bootstrap |
| New | `scripts/gmail_poll.py` | Long-running polling loop entrypoint |
| Modified | `pyproject.toml` | +4 Google auth/API deps (+ `python-dotenv` if missing) |
| New | `.env.example` | Template for all Gmail env vars |
| New | `tests/unit/test_gmail_client.py` | 8 tests |
| New | `tests/unit/test_gmail_adapter.py` | 3 tests |
| New | `tests/unit/test_gmail_poller.py` | 6 tests |
| New | `tests/unit/test_gmail_auth.py` | 2 tests |
| New | `tests/integration/test_gmail_poller_fixture.py` | 1 live test, gated by `@pytest.mark.gmail_live` |
| Modified | `research/Order-Intake-Sprint-Status.md` | §1 row flip (polling done, push still `[Post-MVP]`); Built inventory additions |
| Modified | `Glacis-Order-Intake.md` | §1 Gmail-OAuth bullet: installed-app flow flips `[Post-MVP]` → `[MVP ✓]` for A1; `users.watch()` + Pub/Sub bullets stay `[Post-MVP]` tagged for A3 |
| Modified | `backend/my_agent/README.md` | New "Gmail ingress (A1)" section: one-time auth + runbook |

## Connections

- `research/Order-Intake-Sprint-Status.md` — §1 Signal Ingestion row partial flip: "polling ingress ✓ (A1); push-based Gmail watch / Pub/Sub deferred to A3"
- `Glacis-Order-Intake.md` §1 — installed-app-flow OAuth + polling flip to `[MVP ✓]`; `users.watch()` / Pub/Sub / Cloud Run webhook stay `[Post-MVP]` tagged for A3
- `research/Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Email-Ingestion.md` — the spec being partially implemented
- A2 (Gmail egress) extends `GmailClient` with `send_message(...)` + adds `gmail.send` scope to `A2_SCOPES = A1_SCOPES + [GMAIL_SEND_SCOPE]`
- A3 (push-based ingestion) replaces `GmailPoller` with a webhook-triggered `_process_one` invocation; the adapter + the credential layer are reusable verbatim
- Track C (duplicate detection) — any Gmail-driven run benefits from dup detection automatically; no A1 changes needed
- Track D (audit log) — Gmail-driven runs produce full audit traces automatically via the `AuditedStage` mixin; no A1 changes needed
- `backend/my_agent/agent.py:_build_default_root_agent()` — called by `scripts/gmail_poll.py` unchanged
