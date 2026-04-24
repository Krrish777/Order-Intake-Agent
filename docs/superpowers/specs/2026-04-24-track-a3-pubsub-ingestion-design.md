---
type: design-spec
topic: "Track A3 — Pub/Sub Ingestion (PULL subscription)"
track: A3
date: 2026-04-24
parent: "research/Order-Intake-Sprint-Status.md"
source_spec: "Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Email-Ingestion.md"
status: approved-for-implementation
depends_on:
  - "Track A1 (Gmail ingress) — reuses GmailClient + adapter + scopes"
  - "Track A2 (Gmail egress) — A2_SCOPES cover A3's watch() usage"
  - "Track D (audit log) — optional; Gmail-triggered runs get audit trace for free"
blocks:
  - "Post-MVP Cloud Run + webhook deployment (deliberately deferred)"
tags:
  - design-spec
  - track-a3
  - gmail
  - pubsub
  - push-ingestion
  - watch
---

# Track A3 — Pub/Sub Ingestion — Design

## Summary

Replace Gmail-inbox polling (A1) with push-based delivery via Gmail `users.watch()` → Cloud Pub/Sub → Pub/Sub PULL subscription drained by a long-lived async worker. A1's `scripts/gmail_poll.py` stays in place as the no-GCP-infra dev loop; A3's new `scripts/gmail_pubsub_worker.py` is the production-shape loop. Pub/Sub message payloads (Gmail publishes `{emailAddress, historyId}`) drive a History API sync that produces the actual message ids. Each message runs through the pipeline exactly as A1 already does (same `get_raw` → adapter → `Runner.run_async` → `apply_label` flow). `historyId` cursor stored in Firestore `gmail_sync_state/{user_email}`. A background asyncio task inside the worker renews `users.watch()` every 24h. Stale-cursor recovery falls back to A1-style `messages.list` full-scan once, then resumes push. Credentials stay in `.env`; no Cloud Run / Secret Manager / Cloud Scheduler — push PULL instead of PUSH to keep infra lean for the hackathon.

This closes the Gmail push-path slice of Glacis `Email-Ingestion.md`. Full PUSH subscription + Cloud Run webhook + Cloud Scheduler + Secret Manager remain explicit post-MVP.

## Context

- Track A1 (`docs/superpowers/specs/2026-04-24-track-a1-gmail-ingress-design.md`, 9ddbf27) already built `backend/gmail/client.py` (`list_unprocessed`, `get_raw`, `label_id_for`, `apply_label`) + `adapter.py` (`gmail_message_to_envelope`) + `scripts/gmail_poll.py`. A3 reuses all of it.
- Track A2 (`docs/superpowers/specs/2026-04-24-track-a2-gmail-egress-design.md`, 0780025) bumped scopes to `A2_SCOPES = gmail.modify + gmail.send`. `users.watch()` falls under `gmail.modify` — no new scope needed for A3.
- Gmail's `users.watch(topicName, labelIds=None, userId='me')` tells Gmail to publish a JSON notification (`{emailAddress, historyId}`) to the given Pub/Sub topic whenever the inbox changes. The watch expires 7 days after call time. Re-calling `watch()` resets the expiration.
- The published notification does NOT contain the new messages themselves — only a `historyId` pointer. The worker must call `users.history.list(startHistoryId=X)` to get the list of new message ids since `X`, then fetch each via `messages.get(format='raw')` — which A1's `get_raw` already does.
- Cloud Pub/Sub PULL subscription semantics: at-least-once delivery, manual `.ack()` per message, unacked messages redeliver after `ackDeadlineSeconds` (max 600s, default 10s). For our single-ingestion-worker setup, pulling N messages per batch + processing sequentially + acking after the pipeline run completes is the simplest shape.
- Gmail label `orderintake-processed` (A1) is the idempotency guard against duplicates. At-least-once Pub/Sub delivery + possibly-duplicate History API entries (when historyId ranges overlap) both collapse naturally — messages carrying the label are skipped by A1's `list_unprocessed` query AND we'll apply the same skip logic in A3.
- `google-cloud-pubsub>=2.23` provides `pubsub_v1.SubscriberAsyncClient` with native async `pull(subscription=..., max_messages=N)` + `acknowledge(...)` methods. No threaded streaming-pull bridge required.
- Firestore emulator + Pub/Sub emulator both exist. Firestore emulator is already wired into the repo. Pub/Sub emulator is a new addition — gated behind a pytest marker in the integration suite.

## Architectural decisions

### Decision 1 — Scope: PULL subscription + in-worker watch renewal, no Cloud Run

A3 targets the "minimal production-shape push": watch + Pub/Sub topic + PULL subscription drained by a long-running local/VM process. Explicitly omits webhook + Cloud Run + Secret Manager + Cloud Scheduler. Credentials stay in `.env` same as A1/A2.

**Rejected:**
- **PUSH subscription + Cloud Run webhook** — 2-3x infra ceremony, live Cloud Run URL needed for hackathon demo, Secret Manager adds another setup step. Deferred to Phase 3.
- **Decompose into A3a/A3b/A3c** — 3x brainstorm overhead. Single scoped spec better aligned with hackathon pacing.
- **Skip push entirely** — A1 polling alone misses the Glacis-spec push-path story that judges may ask about.

### Decision 2 — A3 coexists with A1 as separate entrypoints

`scripts/gmail_poll.py` (A1) stays as the no-GCP-infra dev loop. `scripts/gmail_pubsub_worker.py` (A3) is the production-shape entrypoint. Both reuse `backend/gmail/client.py` + `adapter.py` + the pipeline unchanged. Operator picks per environment.

**Rejected:**
- **A3 replaces A1** — forces every contributor + hackathon demo onto GCP Pub/Sub setup. No fallback when Pub/Sub is misconfigured.
- **Single script with `--mode=poll|pubsub` flag** — marginal glue for no real benefit; more indirection at the runtime surface.

### Decision 3 — Pub/Sub mode: `SubscriberAsyncClient.pull()` in an asyncio loop

Use the async-native `pubsub_v1.SubscriberAsyncClient.pull(subscription=..., max_messages=10)` in a `while True:` loop. Each pulled batch is processed sequentially, acked after pipeline completes. Simple bridge to our async pipeline.

**Rejected:**
- **`SubscriberClient.subscribe(...)` streaming pull with callback** — threaded, requires asyncio.run_coroutine_threadsafe bridging, more moving parts. Lower latency technically but not meaningful at single-inbox demo cadence.
- **Manual HTTP pull** — requires re-implementing the gRPC/REST client; google-cloud-pubsub exists.

### Decision 4 — `historyId` cursor stored in Firestore

A new `gmail_sync_state/{user_email}` collection stores `{history_id: str, updated_at: datetime, user_email: str}`. Atomic upsert after each successful Pub/Sub message batch completes the pipeline.

**Rejected:**
- **`.env`** — not mutable at runtime.
- **Local file** — fragile across container restarts / worktrees.
- **Memory only** — re-scan on every restart.

### Decision 5 — Watch renewal: background asyncio task inside the worker, daily

A concurrent `_renew_loop` task runs alongside the drain loop. Calls `watch.start(topic_name, label_ids)` every 24h (configurable via `GMAIL_WATCH_RENEW_INTERVAL_SECONDS`, default 86400). First renewal also runs at startup. Exits cleanly on `CancelledError`.

**Rejected:**
- **Separate `scripts/gmail_watch_renew.py` + host cron** — two things to configure instead of one. More fragile operator experience.
- **Call watch() on worker startup only** — fragile: if the worker runs 7+ days without restart (entirely plausible in a soak test), the watch expires silently and ingestion stops.

### Decision 6 — Stale-cursor recovery: one-time full-scan fallback

When `users.history.list(startHistoryId=X)` returns a 404 (historyId older than Gmail's retention window, usually ~1 week), the worker:

1. Logs `gmail_history_id_stale`
2. Falls back to `GmailClient.list_unprocessed` (A1's full-scan) for this single Pub/Sub cycle
3. Advances the stored cursor to the `historyId` the current Pub/Sub message carries
4. Acks the Pub/Sub message and resumes normal push semantics next cycle

**Rejected:**
- **Crash + require operator intervention** — Phase-3-acceptable, but fragile for a demo-first MVP.
- **Advance cursor to "now" without full-scan** — silent data loss of messages that arrived during the worker outage.

## Components

### New file — `backend/gmail/watch.py`

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

### New file — `backend/gmail/history.py`

```python
"""Gmail History API sync for Track A3.

Given a starting historyId, walks users.history.list pages and
returns (new message ids, latest historyId observed). Raises
HistoryIdTooOldError when Gmail returns 404 (cursor older than
the service's retention window); caller falls back to full-scan.
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

    Only collects messagesAdded[*].message.id — we don't care about
    label-changes or deletes for ingestion. latest_history_id is the
    maximum historyId seen across all pages; caller persists it as
    the next cursor.

    Caps at max_pages to bound work when a long outage has accumulated
    history. 20 pages * ~100 history entries default = up to ~2000
    entries per sync cycle.
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

    # Dedup while preserving order (same id may appear across overlapping pages)
    seen: set[str] = set()
    deduped: list[str] = []
    for mid in new_ids:
        if mid not in seen:
            seen.add(mid)
            deduped.append(mid)
    return deduped, latest_id


__all__ = ["HistoryIdTooOldError", "fetch_new_message_ids"]
```

### New file — `backend/persistence/sync_state_store.py`

```python
"""Firestore-backed cursor store for Gmail History API sync.

One doc per authed inbox at gmail_sync_state/{user_email}.
Schema: {history_id: str, updated_at: SERVER_TIMESTAMP, user_email: str}.

Minimal surface: get_cursor + set_cursor. No schema versioning —
this is internal state, not a business record.
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

### New file — `backend/gmail/pubsub_worker.py`

```python
"""Long-lived Pub/Sub PULL worker for Gmail push ingestion (Track A3).

Concurrent drain loop + watch-renewal loop. Drain pulls up to N
messages per cycle, processes sequentially, acks after pipeline
completes. Renew calls users.watch() every 24h to keep the
subscription alive past its 7-day expiry.

Spec: docs/superpowers/specs/2026-04-24-track-a3-pubsub-ingestion-design.md
"""
from __future__ import annotations

import asyncio
import base64
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
                        # Do NOT ack — let Pub/Sub redeliver
                if ack_ids:
                    await self._subscriber.acknowledge(
                        request={
                            "subscription": self._subscription_path,
                            "ack_ids": ack_ids,
                        }
                    )
            except Exception as exc:
                _log.error("pubsub_pull_failed", error=str(exc))
                await asyncio.sleep(5.0)  # backoff

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
            except Exception as exc:
                _log.error("gmail_watch_renew_failed", error=str(exc))

    async def _process_pubsub_message(self, pubsub_message) -> None:
        """Parse payload + fetch new message ids + process each."""
        payload_bytes = pubsub_message.data
        payload = json.loads(payload_bytes.decode("utf-8"))
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
            # Full-scan fallback — like A1
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
            # Adapter validates parse_eml can handle the bytes
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

### New file — `scripts/gmail_pubsub_worker.py`

Long-running entrypoint. Reads env, constructs deps, invokes `worker.run_forever()`.

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
    subscription_path = f"projects/{project_id}/subscriptions/{os.environ['GMAIL_PUBSUB_SUBSCRIPTION']}"

    root_agent = _build_default_root_agent()
    session_service = InMemorySessionService()
    runner = Runner(app_name="order_intake", agent=root_agent, session_service=session_service)

    # Resolve label id up front so watch() can filter pushes to INBOX+our-label-absent only
    # (operator choice: pass label_ids=None to watch everything, or [INBOX] to restrict).
    # Default here: None (watch all inbox events; dedup via our processed label on read side).
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

### New file — `scripts/gmail_watch_setup.py`

One-time operator bootstrap for Pub/Sub infrastructure:

```python
#!/usr/bin/env python3
"""Create Pub/Sub topic + PULL subscription + grant Gmail publisher role.

Usage:
    uv run python scripts/gmail_watch_setup.py \\
        --project demo-order-intake-local \\
        --topic gmail-inbox-events \\
        --subscription order-intake-ingestion

Idempotent — safe to re-run. Each operation skips if the resource already
exists. Grants gmail-api-push@system.gserviceaccount.com the
pubsub.publisher role on the topic (Gmail requires this to deliver
notifications).

Spec: docs/superpowers/specs/2026-04-24-track-a3-pubsub-ingestion-design.md
"""
from __future__ import annotations

import argparse
import sys

from google.api_core.exceptions import AlreadyExists
from google.cloud import pubsub_v1
from google.iam.v1 import policy_pb2


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

### Modified — `pyproject.toml`

Add:
- `google-cloud-pubsub>=2.23`

### Modified — `.env.example`

```
# Track A3: Pub/Sub ingestion
GMAIL_PUBSUB_PROJECT_ID=demo-order-intake-local
GMAIL_PUBSUB_TOPIC=gmail-inbox-events
GMAIL_PUBSUB_SUBSCRIPTION=order-intake-ingestion
GMAIL_WATCH_RENEW_INTERVAL_SECONDS=86400
PUBSUB_EMULATOR_HOST=localhost:8085   # optional; leave unset in prod
```

### Modified — `backend/my_agent/README.md`

Add "Push-based ingestion (Track A3)" subsection with: `gmail_watch_setup.py` bootstrap + env vars + run command + troubleshooting + note about A1 coexistence.

## Data flow

### Startup

```
scripts/gmail_pubsub_worker.py
  load_dotenv()
  GmailClient (A2_SCOPES) + Firestore AsyncClient + SubscriberAsyncClient
  GmailWatch + GmailSyncStateStore + Runner + root_agent
  GmailPubSubWorker.run_forever()
    _init():
      user_email = await watch.get_profile_email()
      label_id = gmail_client.label_id_for("orderintake-processed")
      result = await watch.start(topic_name, label_ids=None)
      log gmail_watch_started history_id=... expiration=...
    asyncio.gather(_drain_loop(), _renew_loop())
```

### Normal push cycle (happy path)

```
Customer sends email to the authed inbox
  Gmail publishes: {"emailAddress": "me@gmail.com", "historyId": "523456"}
  Pub/Sub delivers to the subscription
  worker._drain_loop pulls it:
    payload = json.loads(base64-decoded message.data)
    stored = sync_state_store.get_cursor(user_email)          # e.g. "523400"
    new_ids, latest = await fetch_new_message_ids(
        gmail_client, start_history_id=stored or "523456"
    )                                                          # [msg-id-1], latest="523456"
    for msg_id in new_ids:
        await _process_gmail_message(msg_id)
            get_raw → adapter → Runner.run_async → apply_label
    sync_state_store.set_cursor(user_email, latest)            # "523456"
    subscriber.acknowledge(ack_ids=[received.ack_id])
  loop
```

### Background renewal

```
_renew_loop:
  loop:
    await asyncio.sleep(86400)   # 24h
    result = await watch.start(topic_name, label_ids)
    log gmail_watch_renewed history_id=... expiration=...
```

### Stale-cursor recovery

```
worker comes back after 10-day outage
  Pub/Sub delivers an accumulated message: {historyId: "999999"}
  stored_cursor = "523456" (from pre-outage)
  fetch_new_message_ids(start_history_id="523456") → raises HistoryIdTooOldError
  worker catches:
    log gmail_history_id_stale
    new_ids = gmail_client.list_unprocessed(label_name="orderintake-processed")
    latest = "999999" (from push payload)
  processes all unlabeled inbox messages
  set_cursor("999999")
  ack Pub/Sub message
  next push uses cursor="999999" — normal fetch_new_message_ids resumes
```

### Worker crash mid-batch

```
worker pulling 3 msgs, processed msg-1 + msg-2 (acked label applied, Firestore wrote),
crashed on msg-3 (pipeline exception)
  _drain_loop catches the pipeline exception → msg-3 ack_id NOT appended
  acknowledge(ack_ids=[msg-1.ack_id, msg-2.ack_id])
  msg-3 redelivered by Pub/Sub after ackDeadlineSeconds (default 10s)
  next pull picks up msg-3 → retry
```

### Concurrent A1 + A3 (operator misconfiguration)

```
Both scripts/gmail_poll.py and scripts/gmail_pubsub_worker.py running:
  Both try to pull + process the same inbox messages
  Gmail label dedup collapses the overlap: whichever process applies the
    label first wins; the other's list_unprocessed / history.list excludes
    labeled messages
  IntakeCoordinator source_message_id dedup provides a second safety net
  Outcome: no duplicates, just wasted cycles
```

## Error handling

| Scenario | Behavior |
|---|---|
| `subscriber.pull` raises (transient) | Caught in `_drain_loop`, logged `pubsub_pull_failed`, 5s sleep, retry. |
| `_process_pubsub_message` raises | Caught in `_drain_loop` per-message try; ack_id NOT added → Pub/Sub redelivers. |
| `fetch_new_message_ids` raises `HistoryIdTooOldError` | Fall back to `list_unprocessed` full-scan for this single cycle; advance cursor to push-payload's historyId; ack. |
| `messages.get` 404 (deleted message) | Caught in `_process_gmail_message`, logged, skip (don't label, message is gone). Ack the pubsub msg (we did our best). |
| Per-message pipeline error | Caught in `_process_gmail_message`, logged, no `apply_label`. Ack the pubsub msg (pipeline-side error is not Pub/Sub's fault). Next push of that message won't happen unless it re-enters inbox unlabeled. Operator intervention via dashboard / audit log. |
| `watch.start` fails | `_init` failure → propagates to `run_forever` → process exits. Operator re-runs `scripts/gmail_watch_setup.py` or investigates. |
| `_renew_loop` watch renewal fails | Caught, logged `gmail_watch_renew_failed`, next interval retries. 6 retries before 7-day expiry silent-failure risk. |
| `sync_state_store.set_cursor` fails after successful pipeline | Cursor not advanced → next push re-uses old cursor → History API returns overlapping ids → labeled-dedup skips them. Wasted cycles, no data loss. |
| Subscription doesn't exist (NotFound on pull) | `subscriber.pull` raises `NotFound` permanently → exit the process with non-zero. Operator re-runs `scripts/gmail_watch_setup.py`. |
| Pub/Sub emulator host unreachable | Client construction fails or pull hangs. Worker exits on timeout. Operator checks `PUBSUB_EMULATOR_HOST` + emulator status. |
| SIGINT / SIGTERM | Propagates `CancelledError` into `_drain_loop` + `_renew_loop` via `asyncio.gather` cancellation; best-effort mid-message completion; clean exit. |
| Multiple workers on the same subscription | Pub/Sub load-balances; Gmail label + source_message_id dedup ensure each message processes once. Anti-pattern but not catastrophic. |

### Logging

All structured via `backend.utils.logging`:
- `gmail_pubsub_worker_stopping` — clean-shutdown marker
- `gmail_watch_started` — first watch() call
- `gmail_watch_renewed` — per daily renewal
- `gmail_watch_renew_failed` — renewal error
- `gmail_history_id_stale` — fall-back triggered
- `pubsub_pull_failed` — per failed tick
- `pubsub_message_failed` — per failed message
- `gmail_message_processed` / `gmail_message_failed` — per message (reused from A1)

## Testing

### Unit — new `tests/unit/test_gmail_history.py` (4 tests)

Patch `GmailClient._service` via `MagicMock`:

1. `fetch_new_message_ids` returns ids from `messagesAdded` entries across pages
2. Handles pagination via `nextPageToken`
3. Returns `(ids, latest_history_id)` where latest = max across pages
4. Raises `HistoryIdTooOldError` when `history.list` returns 404 (mock `resp.status=404` on the raised exception)

### Unit — new `tests/unit/test_gmail_watch.py` (3 tests)

1. `watch.start(topic_name, label_ids=[X])` calls `users.watch` with correct body
2. `watch.stop()` calls `users.stop`
3. `watch.get_profile_email()` returns `emailAddress`

### Unit — new `tests/unit/test_sync_state_store.py` (3 tests)

Use the existing `FakeAsyncClient` from `conftest.py`:

1. `get_cursor` returns `None` for missing doc
2. `set_cursor` upserts via `merge=False`
3. Round-trip: set → get returns stored historyId

### Unit — new `tests/unit/test_pubsub_worker.py` (6 tests)

`AsyncMock(spec=SubscriberAsyncClient)` + `AsyncMock(spec=Runner)` + `MagicMock(spec=GmailClient)` + `AsyncMock(spec=GmailSyncStateStore)` + `AsyncMock(spec=GmailWatch)` + `AsyncMock(spec=InMemorySessionService)`:

1. `_init` calls `watch.get_profile_email` + `gmail_client.label_id_for` + `watch.start` in order
2. `_process_pubsub_message` with a fresh payload: calls `fetch_new_message_ids`, processes each id, updates cursor
3. `_process_pubsub_message` with `HistoryIdTooOldError`: falls back to `list_unprocessed`, advances cursor to push payload's historyId
4. `_process_pubsub_message` with 0 new ids still advances cursor + doesn't call `_process_gmail_message`
5. `_renew_loop` with short interval (0.01s) calls `watch.start` at least twice (use `asyncio.wait_for` + task cancellation)
6. `run_forever` exits cleanly on `asyncio.CancelledError`

### Integration — new `tests/integration/test_pubsub_worker_emulator.py` (1 gated test)

Gated behind `@pytest.mark.pubsub_emulator` + env check for `PUBSUB_EMULATOR_HOST` + `FIRESTORE_EMULATOR_HOST`. Auto-skip in default CI runs.

- Start both emulators in the test setup (or expect them running)
- Create topic + PULL subscription via `pubsub_v1.PublisherClient` + `SubscriberClient`
- Publish a Gmail-notification-shaped JSON payload
- Instantiate the worker with a mock `GmailClient.get_raw` returning a fixture `.eml` file's bytes
- Run ONE cycle of `_drain_loop` (via direct call to `_tick()` or short-timed `run_forever` task)
- Assert Firestore shows the `OrderRecord` + cursor updated + mock recorded the `apply_label` call

### Total test delta

- New unit: 4 + 3 + 3 + 6 = **16**
- New integration: **1** (gated)
- Baseline after C + D + A1 + A2 ≈ 390 → ~406 unit.

## Out of scope

- **Webhook PUSH subscription + Cloud Run** — deliberate Decision 1.
- **Secret Manager for credentials** — `.env` fine for MVP.
- **Cloud Scheduler** — in-worker renewal replaces it.
- **Domain-wide delegation** — single-inbox only.
- **Pub/Sub ordering keys** — History API is order-independent.
- **Exactly-once Pub/Sub delivery** — at-least-once + label dedup.
- **Dead-letter topic** — unacked messages redeliver; manual ops intervention for stuck ones.
- **Multi-worker horizontal scaling** — works by design (Pub/Sub load-balances), not tested.
- **VPC Service Controls / private Pub/Sub** — public endpoints only.
- **`users.stop()` on clean shutdown** — optional nice-to-have; watch expires naturally in 7 days.
- **Alerting when watch approaches expiry** — log-line only. No metrics / dashboards.

## Success criteria

1. Running `scripts/gmail_watch_setup.py --project X --topic Y --subscription Z` creates topic + subscription + IAM grant. Re-running is idempotent.
2. Operator adds `GMAIL_PUBSUB_*` vars to `.env` and runs `scripts/gmail_pubsub_worker.py` → worker calls `watch()` at startup → Gmail confirms `historyId` + `expiration` 7 days out.
3. Sending a fresh email to the inbox → Pub/Sub push arrives at the PULL subscription within ~1s → worker drains → pipeline processes the message → `orderintake-processed` label lands on the Gmail message → `OrderRecord` appears in Firestore.
4. Re-sending the same email (A2/A3 scope): dedup + label skip → no double-send.
5. Worker ran continuously for ≥25 hours → `_renew_loop` fires at hour 24 → watch re-asserted → new Gmail pushes succeed past original 7-day mark.
6. Operator stops the worker for 10 days → on restart + first push, stale-cursor recovery kicks in → all unlabeled inbox messages process exactly once.
7. `scripts/gmail_poll.py` (A1) continues to work unchanged — same tests green, same fixture integration passes.
8. Integration test against Pub/Sub emulator + Firestore emulator green when both emulators configured.
9. No regression in the unit + integration test suite.

## Files touched (summary)

| Type | Path | Change |
|---|---|---|
| New | `backend/gmail/watch.py` | `GmailWatch` wrapper |
| New | `backend/gmail/history.py` | `fetch_new_message_ids` + `HistoryIdTooOldError` |
| New | `backend/gmail/pubsub_worker.py` | `GmailPubSubWorker` |
| New | `backend/persistence/sync_state_store.py` | `GmailSyncStateStore` |
| New | `scripts/gmail_pubsub_worker.py` | Long-running entrypoint |
| New | `scripts/gmail_watch_setup.py` | One-time Pub/Sub topic + subscription + IAM bootstrap |
| Modified | `pyproject.toml` | +`google-cloud-pubsub>=2.23` |
| Modified | `.env.example` | +`GMAIL_PUBSUB_*`, +`PUBSUB_EMULATOR_HOST`, +`GMAIL_WATCH_RENEW_INTERVAL_SECONDS` |
| New | `tests/unit/test_gmail_history.py` | 4 tests |
| New | `tests/unit/test_gmail_watch.py` | 3 tests |
| New | `tests/unit/test_sync_state_store.py` | 3 tests |
| New | `tests/unit/test_pubsub_worker.py` | 6 tests |
| New | `tests/integration/test_pubsub_worker_emulator.py` | 1 gated test |
| Modified | `backend/my_agent/README.md` | +"Push-based ingestion (A3)" operator section |
| Modified | `research/Order-Intake-Sprint-Status.md` | §1 row: polling + push both done; Built inventory |
| Modified | `Glacis-Order-Intake.md` | §1 `users.watch()` + `Pub/Sub push subscription` + `History API sync` + `Thread tracking` bullets `[Post-MVP]` → `[MVP ✓]` (with note that Cloud Run webhook variant deferred) |

## Connections

- Track A1 (`9ddbf27`): reuses `GmailClient` + `gmail_message_to_envelope` + `A2_SCOPES` (no scope bump). A1's poller stays as the no-GCP dev loop.
- Track A2 (`0780025`): A2_SCOPES are sufficient for A3 — `users.watch()` falls under `gmail.modify`.
- Track C (duplicate detection): duplicate emails pushed via Pub/Sub ESCALATE normally through the pipeline.
- Track D (audit log): each push-triggered pipeline run produces a full audit trace with its own `correlation_id`; push vs poll is invisible at the stage layer.
- `backend/my_agent/agent.py:_build_default_root_agent()` — called unchanged.
- `Glacis-Order-Intake.md` §1: `users.watch()` / Pub/Sub push subscription / History API sync all flip `[MVP ✓]`; `Cloud Run webhook` stays `[Post-MVP]` intentionally. §14 `Cloud Run config` + `Cloud Scheduler cron` + `Secret Manager` stay `[Post-MVP]`.
