"""Long-lived Pub/Sub PULL worker for Gmail push ingestion (Track A3).

Concurrent drain loop + watch-renewal loop. Drain pulls up to N
messages per cycle, processes sequentially, acks after pipeline
completes. Renew calls users.watch() every 24h.

Note: SubscriberAsyncClient lives at google.pubsub_v1 (not
google.cloud.pubsub_v1, which only exposes the sync SubscriberClient).
This is the modern split introduced when google-cloud-pubsub moved
async support into google.api_core.

Spec: docs/superpowers/specs/2026-04-24-track-a3-pubsub-ingestion-design.md
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Optional

from google.adk.runners import Runner
from google.adk.sessions import BaseSessionService
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
        subscriber: Any,
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
                # Yield to the event loop so cancellation can propagate
                # even when the subscription is idle.
                await asyncio.sleep(0)
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
