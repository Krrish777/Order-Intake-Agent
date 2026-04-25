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
  PUBSUB_EMULATOR_HOST (optional - auto-used when set)
  FIRESTORE_EMULATOR_HOST, GOOGLE_API_KEY, LLAMA_CLOUD_API_KEY (pipeline)

SubscriberAsyncClient lives at google.pubsub_v1 (not google.cloud.pubsub_v1
which only exposes the sync SubscriberClient). Same OAuth refresh token
as A1+A2; A3 adds no new scopes.

Spec: docs/superpowers/specs/2026-04-24-track-a3-pubsub-ingestion-design.md
"""
from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.pubsub_v1 import SubscriberAsyncClient

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
