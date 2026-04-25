"""Async polling loop orchestrating GmailClient + adapter + pipeline.

Sequential per tick: list unprocessed -> for each message, get_raw ->
adapt -> Runner.run_async -> apply_label. Errors per message are
logged and swallowed; the loop continues. SIGINT / SIGTERM exits
cleanly via asyncio.CancelledError.

Spec: docs/superpowers/specs/2026-04-24-track-a1-gmail-ingress-design.md
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Optional

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
            # for RFC 822 - headers are 7-bit ASCII, body content is
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
