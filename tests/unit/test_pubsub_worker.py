"""Unit tests for GmailPubSubWorker.

Uses AsyncMock for async collaborators + MagicMock for sync ones.
No network, no emulator - just orchestration logic under test.

Spec: docs/superpowers/specs/2026-04-24-track-a3-pubsub-ingestion-design.md
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

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
        worker, subscriber, gmail_client, runner, cursor_store, watch = await _make_worker()
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
