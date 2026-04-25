"""Unit tests for GmailPoller async loop.

Uses AsyncMock for the Runner + SessionService, MagicMock for the
GmailClient. No network, no pipeline invocation - only the
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
