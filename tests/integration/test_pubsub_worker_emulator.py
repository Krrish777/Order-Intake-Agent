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

import os
import uuid

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
    from unittest.mock import AsyncMock, MagicMock

    from google.adk.sessions import InMemorySessionService
    from google.cloud import pubsub_v1
    from google.pubsub_v1 import SubscriberAsyncClient

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
    import json
    payload = json.dumps(
        {"emailAddress": "me@example.com", "historyId": "12345"}
    ).encode()
    publisher.publish(topic_path, payload).result(timeout=10)

    # Mock GmailClient.get_raw to return a minimal valid EML as bytes
    fixture_bytes = (
        b"From: customer@example.com\r\n"
        b"To: orders@example.com\r\n"
        b"Subject: Test order\r\n"
        b"Message-ID: <pubsub-test@example.com>\r\n"
        b"\r\n"
        b"please order 5 widgets\r\n"
    )

    gmail_client = MagicMock(spec=GmailClient)
    gmail_client.label_id_for = MagicMock(return_value="Label_TEST")
    gmail_client.get_raw = MagicMock(return_value=fixture_bytes)
    gmail_client.apply_label = MagicMock()
    gmail_client.list_unprocessed = MagicMock(return_value=[])

    # Mock watch - don't hit real Gmail API
    watch = AsyncMock(spec=GmailWatch)
    watch.get_profile_email = AsyncMock(return_value="me@example.com")
    watch.start = AsyncMock(return_value={"historyId": "500", "expiration": "9"})

    # Mock fetch_new_message_ids to return the fixture message id
    import backend.gmail.pubsub_worker as worker_module

    orig_fetch = worker_module.fetch_new_message_ids

    async def _fake_fetch(gc, *, start_history_id, max_pages=20):
        return ["fixture-msg-id"], "12346"

    worker_module.fetch_new_message_ids = _fake_fetch

    try:
        firestore_client = get_async_client()
        sync_state_store = GmailSyncStateStore(firestore_client)

        # Stub Runner to skip the full pipeline (which needs seeded master data).
        runner = MagicMock()

        async def _empty_stream(**kw):
            if False:
                yield None  # pragma: no cover

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
