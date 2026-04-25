"""Unit tests for SendStage.

Uses AsyncMock(spec=OrderStore) + AsyncMock(spec=ExceptionStore) +
MagicMock(spec=GmailClient) + AsyncMock for AuditLogger.

Spec: docs/superpowers/specs/2026-04-24-track-a2-gmail-egress-design.md
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.gmail.client import GmailClient
from backend.persistence.base import ExceptionStore, OrderStore

pytestmark = pytest.mark.asyncio


def _order_result_entry(
    *,
    sub_doc_index: int = 0,
    source_message_id: str = "msg-1",
    confirmation_body: str | None = "Thanks for your order.",
    sent_at=None,
    contact_email: str | None = "customer@example.com",
):
    return {
        "filename": "body.txt",
        "sub_doc_index": sub_doc_index,
        "result": {
            "kind": "order",
            "order": {
                "source_message_id": source_message_id,
                "confirmation_body": confirmation_body,
                "sent_at": sent_at,
                "customer": {"contact_email": contact_email},
            },
            "exception": None,
        },
    }


def _exception_result_entry(
    *,
    sub_doc_index: int = 0,
    source_message_id: str = "msg-2",
    clarify_body: str | None = "Please clarify the missing qty.",
    sent_at=None,
):
    return {
        "filename": "body.txt",
        "sub_doc_index": sub_doc_index,
        "result": {
            "kind": "exception",
            "order": None,
            "exception": {
                "source_message_id": source_message_id,
                "clarify_body": clarify_body,
                "sent_at": sent_at,
            },
        },
    }


def _make_state(
    process_results,
    envelope=None,
    reply_handled=False,
    correlation_id="c1",
    fallback_sender="customer@example.com",
):
    return {
        "correlation_id": correlation_id,
        "reply_handled": reply_handled,
        "envelope": envelope or {
            "message_id": "<orig-msg@mailer>",
            "subject": "Order request",
            "from_addr": fallback_sender,
            "references": [],
        },
        "process_results": process_results,
    }


def _make_ctx(stage, state):
    from tests.unit._stage_testing import make_stage_ctx
    return make_stage_ctx(stage=stage, state=state)


def _drain(gen):
    """Drain async generator without waiting for events."""
    async def _go():
        async for _ in gen:
            pass
    return _go()


async def _make_stage(*, gmail_client="default", dry_run=False):
    from backend.my_agent.stages.send import SendStage

    order_store = AsyncMock(spec=OrderStore)
    exception_store = AsyncMock(spec=ExceptionStore)
    audit_logger = AsyncMock()
    if gmail_client == "default":
        gc = MagicMock(spec=GmailClient)
    else:
        gc = gmail_client

    stage = SendStage(
        gmail_client=gc,
        order_store=order_store,
        exception_store=exception_store,
        dry_run=dry_run,
        audit_logger=audit_logger,
    )
    return stage, order_store, exception_store, audit_logger, gc


class TestSendStageSkipPaths:
    async def test_noop_when_gmail_client_is_none(self):
        stage, order_store, exception_store, audit_logger, _ = await _make_stage(
            gmail_client=None
        )
        ctx = _make_ctx(stage, _make_state([_order_result_entry()]))
        async for _ in stage.run_async(ctx):
            pass
        order_store.update_with_send_receipt.assert_not_awaited()
        exception_store.update_with_send_receipt.assert_not_awaited()

    async def test_noop_when_reply_handled(self):
        stage, order_store, exception_store, audit_logger, gc = await _make_stage()
        ctx = _make_ctx(stage, _make_state([_order_result_entry()], reply_handled=True))
        async for _ in stage.run_async(ctx):
            pass
        order_store.update_with_send_receipt.assert_not_awaited()
        gc.send_message.assert_not_called()


class TestSendStageAutoApprove:
    async def test_sends_confirmation_when_body_present_and_not_sent(self):
        stage, order_store, exception_store, audit_logger, gc = await _make_stage()
        gc.send_message = MagicMock(return_value="gmail-id-1")
        ctx = _make_ctx(stage, _make_state([_order_result_entry()]))

        async for _ in stage.run_async(ctx):
            pass

        gc.send_message.assert_called_once()
        order_store.update_with_send_receipt.assert_awaited_once()
        update_kwargs = order_store.update_with_send_receipt.await_args.kwargs
        assert update_kwargs["source_message_id"] == "msg-1"
        assert update_kwargs["sent_at"] is not None
        assert update_kwargs["send_error"] is None

    async def test_skips_send_when_sent_at_already_set(self):
        stage, order_store, exception_store, audit_logger, gc = await _make_stage()
        gc.send_message = MagicMock(return_value="gmail-id-skip")
        ctx = _make_ctx(stage, _make_state([
            _order_result_entry(sent_at=datetime.now(timezone.utc).isoformat())
        ]))

        async for _ in stage.run_async(ctx):
            pass

        gc.send_message.assert_not_called()
        order_store.update_with_send_receipt.assert_not_awaited()


class TestSendStageClarify:
    async def test_sends_clarify_when_exception_has_body(self):
        stage, order_store, exception_store, audit_logger, gc = await _make_stage()
        gc.send_message = MagicMock(return_value="gmail-id-2")
        ctx = _make_ctx(stage, _make_state([_exception_result_entry()]))

        async for _ in stage.run_async(ctx):
            pass

        gc.send_message.assert_called_once()
        exception_store.update_with_send_receipt.assert_awaited_once()


class TestSendStageEscalateAndFailure:
    async def test_skips_send_when_exception_has_no_body(self):
        stage, order_store, exception_store, audit_logger, gc = await _make_stage()
        ctx = _make_ctx(stage, _make_state([
            _exception_result_entry(clarify_body=None)
        ]))

        async for _ in stage.run_async(ctx):
            pass

        gc.send_message.assert_not_called()
        exception_store.update_with_send_receipt.assert_not_awaited()

    async def test_dry_run_logs_but_does_not_send_or_update(self):
        stage, order_store, exception_store, audit_logger, gc = await _make_stage(dry_run=True)
        gc.send_message = MagicMock(return_value="should-not-be-called")
        ctx = _make_ctx(stage, _make_state([_order_result_entry()]))

        async for _ in stage.run_async(ctx):
            pass

        gc.send_message.assert_not_called()
        order_store.update_with_send_receipt.assert_not_awaited()
        # Audit event emitted
        dry_run_emits = [
            c for c in audit_logger.emit.await_args_list
            if c.kwargs.get("action") == "email_send_dry_run"
        ]
        assert len(dry_run_emits) == 1

    async def test_send_failure_records_error_and_continues(self):
        stage, order_store, exception_store, audit_logger, gc = await _make_stage()
        gc.send_message = MagicMock(side_effect=[
            RuntimeError("quota exceeded"),
            "gmail-id-ok",
        ])
        ctx = _make_ctx(stage, _make_state([
            _order_result_entry(source_message_id="msg-fail"),
            _order_result_entry(source_message_id="msg-ok"),
        ]))

        async for _ in stage.run_async(ctx):
            pass

        # First update records error
        calls = order_store.update_with_send_receipt.await_args_list
        fail_call = calls[0].kwargs
        ok_call = calls[1].kwargs
        assert fail_call["sent_at"] is None
        assert "quota exceeded" in fail_call["send_error"]
        assert ok_call["sent_at"] is not None
        assert ok_call["send_error"] is None

    async def test_missing_recipient_records_no_recipient_error(self):
        stage, order_store, exception_store, audit_logger, gc = await _make_stage()
        gc.send_message = MagicMock(return_value="should-not-fire")
        ctx = _make_ctx(stage, _make_state([
            _order_result_entry(contact_email=None)
        ]))

        async for _ in stage.run_async(ctx):
            pass

        gc.send_message.assert_not_called()
        order_store.update_with_send_receipt.assert_awaited_once()
        update_kwargs = order_store.update_with_send_receipt.await_args.kwargs
        assert update_kwargs["send_error"] == "no_recipient"
