"""Unit tests for :class:`backend.my_agent.stages.reply_shortcircuit.ReplyShortCircuitStage`.

The stage correlates clarify replies to pending exceptions. It reads the
:class:`EmailEnvelope` that :class:`IngestStage` previously wrote to
``session.state['envelope']``. Depending on the envelope's threading
headers and the state of the ``exceptions`` collection, it either:

* passes through (``reply_handled=False``), or
* advances a pending exception PENDING_CLARIFY → AWAITING_REVIEW and
  stashes the reply body on state (``reply_handled=True``).

The :class:`ExceptionStore` dep is a Protocol, so every test uses
``unittest.mock.AsyncMock`` to control its behaviour. Returned records are
real :class:`ExceptionRecord` instances so :meth:`~ExceptionRecord.model_dump`
produces the real JSON shape downstream will receive.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from backend.ingestion.email_envelope import EmailEnvelope
from backend.models.exception_record import ExceptionRecord, ExceptionStatus
from backend.models.parsed_document import (
    ExtractedOrder,
    OrderLineItem,
    ParsedDocument,
)
from backend.models.validation_result import (
    LineItemValidation,
    RoutingDecision,
    ValidationResult,
)
from backend.my_agent.stages.reply_shortcircuit import ReplyShortCircuitStage
from backend.persistence.base import ExceptionStore
from tests.unit._stage_testing import collect_events, final_state_delta, make_stage_ctx


# --------------------------------------------------------------------- helpers


def _make_envelope(
    message_id: str = "<reply-001@customer.com>",
    in_reply_to: str | None = "<clarify-001@us.com>",
    thread_id: str | None = "thread-xyz",
    body_text: str = "Thanks — the missing SKU is WIDGET-42.",
) -> EmailEnvelope:
    return EmailEnvelope(
        message_id=message_id,
        in_reply_to=in_reply_to,
        thread_id=thread_id,
        from_addr="buyer@birchvalley.com",
        to_addr="orders@us.com",
        subject="Re: clarification needed on PO-12345",
        received_at=datetime(2026, 4, 22, 9, 30, tzinfo=timezone.utc),
        body_text=body_text,
    )


def _make_ctx(stage: ReplyShortCircuitStage, envelope: EmailEnvelope | None):
    """Build a real :class:`InvocationContext` with envelope pre-seeded on state."""
    state: dict[str, object] = {}
    if envelope is not None:
        state["envelope"] = envelope.model_dump(mode="json")
    return make_stage_ctx(stage=stage, state=state)


def _sample_parsed_doc() -> ParsedDocument:
    return ParsedDocument(
        classification="purchase_order",
        classification_rationale="Subject reads 'PO 12345' and body lists qty/sku rows.",
        sub_documents=[
            ExtractedOrder(
                customer_name="Birch Valley Manufacturing",
                po_number="PO-12345",
                line_items=[
                    OrderLineItem(
                        sku="MYSTERY-SKU",
                        description="3/8 inch widget",
                        quantity=50,
                        unit_of_measure="EA",
                        unit_price=0.42,
                    )
                ],
            )
        ],
        page_count=1,
        detected_language="en",
    )


def _sample_validation_result() -> ValidationResult:
    return ValidationResult(
        customer=None,
        lines=[
            LineItemValidation(
                line_index=0,
                matched_sku=None,
                match_tier="none",
                match_confidence=0.0,
                price_ok=True,
                qty_ok=True,
                notes=["no match for line input: 'MYSTERY-SKU'"],
            )
        ],
        aggregate_confidence=0.85,
        decision=RoutingDecision.CLARIFY,
        rationale="1 line, 1 unmatched -> clarify",
    )


def _make_exception(
    source_message_id: str = "<orig-msg-001@customer.com>",
    thread_id: str = "thread-xyz",
    status: ExceptionStatus = ExceptionStatus.PENDING_CLARIFY,
    reply_message_id: str | None = None,
) -> ExceptionRecord:
    base = datetime(2026, 4, 22, 0, 0, 0, tzinfo=timezone.utc)
    return ExceptionRecord(
        source_message_id=source_message_id,
        thread_id=thread_id,
        clarify_message_id="<clarify-001@us.com>",
        reply_message_id=reply_message_id,
        status=status,
        reason="Line 0: no match for 'MYSTERY-SKU'.",
        clarify_body="Could you confirm the SKU?",
        parsed_doc=_sample_parsed_doc(),
        validation_result=_sample_validation_result(),
        created_at=base,
        updated_at=base,
    )


# ---------------------------------------------------------------------- tests


def test_no_in_reply_to_sets_reply_handled_false() -> None:
    store = AsyncMock(spec=ExceptionStore)
    stage = ReplyShortCircuitStage(exception_store=store)
    env = _make_envelope(in_reply_to=None)
    ctx = _make_ctx(stage, env)

    events = collect_events(stage.run_async(ctx))

    delta = final_state_delta(events)
    assert delta["reply_handled"] is False
    store.find_pending_clarify.assert_not_awaited()
    store.update_with_reply.assert_not_awaited()


def test_empty_in_reply_to_sets_reply_handled_false() -> None:
    """Empty string is semantically the same as missing — not a reply."""
    store = AsyncMock(spec=ExceptionStore)
    stage = ReplyShortCircuitStage(exception_store=store)
    env = _make_envelope(in_reply_to="")
    ctx = _make_ctx(stage, env)

    events = collect_events(stage.run_async(ctx))

    delta = final_state_delta(events)
    assert delta["reply_handled"] is False
    store.find_pending_clarify.assert_not_awaited()
    store.update_with_reply.assert_not_awaited()


def test_in_reply_to_with_no_pending_match_sets_reply_handled_false() -> None:
    store = AsyncMock(spec=ExceptionStore)
    store.find_pending_clarify.return_value = None
    stage = ReplyShortCircuitStage(exception_store=store)
    env = _make_envelope(in_reply_to="<msg-abc@x.com>", thread_id="thread-xyz")
    ctx = _make_ctx(stage, env)

    events = collect_events(stage.run_async(ctx))

    delta = final_state_delta(events)
    assert delta["reply_handled"] is False
    store.find_pending_clarify.assert_awaited_once_with("thread-xyz")
    store.update_with_reply.assert_not_awaited()

    # Event content should mention the no-pending condition for traceability.
    texts = [
        part.text
        for event in events
        if event.content and event.content.parts
        for part in event.content.parts
        if part.text
    ]
    assert any("no pending clarify" in t for t in texts)


def test_in_reply_to_with_pending_match_advances_exception() -> None:
    parent = _make_exception(
        source_message_id="<orig-msg-001@customer.com>",
        thread_id="thread-xyz",
        status=ExceptionStatus.PENDING_CLARIFY,
    )
    advanced = _make_exception(
        source_message_id="<orig-msg-001@customer.com>",
        thread_id="thread-xyz",
        status=ExceptionStatus.AWAITING_REVIEW,
        reply_message_id="<reply-001@customer.com>",
    )

    store = AsyncMock(spec=ExceptionStore)
    store.find_pending_clarify.return_value = parent
    store.update_with_reply.return_value = advanced

    stage = ReplyShortCircuitStage(exception_store=store)
    env = _make_envelope(
        message_id="<reply-001@customer.com>",
        in_reply_to="<clarify-001@us.com>",
        thread_id="thread-xyz",
        body_text="Thanks — WIDGET-42 is the right SKU.",
    )
    ctx = _make_ctx(stage, env)

    events = collect_events(stage.run_async(ctx))

    delta = final_state_delta(events)
    assert delta["reply_handled"] is True
    assert delta["reply_parent_source_message_id"] == "<orig-msg-001@customer.com>"
    assert delta["reply_updated_exception"]["status"] == "awaiting_review"
    assert delta["reply_body_text"] == "Thanks — WIDGET-42 is the right SKU."

    store.update_with_reply.assert_awaited_once_with(
        source_message_id="<orig-msg-001@customer.com>",
        reply_message_id="<reply-001@customer.com>",
    )


def test_missing_envelope_state_raises() -> None:
    store = AsyncMock(spec=ExceptionStore)
    stage = ReplyShortCircuitStage(exception_store=store)
    ctx = _make_ctx(stage, envelope=None)

    with pytest.raises(ValueError, match="requires IngestStage"):
        collect_events(stage.run_async(ctx))


def test_update_with_reply_raises_propagates() -> None:
    """Status-guard violation from the store must not be swallowed."""
    parent = _make_exception(status=ExceptionStatus.PENDING_CLARIFY)
    store = AsyncMock(spec=ExceptionStore)
    store.find_pending_clarify.return_value = parent
    store.update_with_reply.side_effect = ValueError("status guard")

    stage = ReplyShortCircuitStage(exception_store=store)
    env = _make_envelope()
    ctx = _make_ctx(stage, env)

    with pytest.raises(ValueError, match="status guard"):
        collect_events(stage.run_async(ctx))
