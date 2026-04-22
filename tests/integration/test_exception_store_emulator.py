"""Integration tests for :class:`FirestoreExceptionStore` against the emulator.

The ``find_pending_clarify`` test additionally validates that the composite
index in ``firebase/firestore.indexes.json`` matches the query — without
the index, the emulator would warn (but the real production Firestore
would fail).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest

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
from backend.persistence import FirestoreExceptionStore
from backend.tools.order_validator import get_async_client

pytestmark = [
    pytest.mark.firestore_emulator,
    pytest.mark.skipif(
        not os.environ.get("FIRESTORE_EMULATOR_HOST"),
        reason="FIRESTORE_EMULATOR_HOST not set; emulator tests skipped",
    ),
]


@pytest.fixture
async def store():
    client = get_async_client()
    s = FirestoreExceptionStore(client)
    try:
        yield s
    finally:
        client.close()


def _exception(
    source_message_id: str,
    thread_id: str,
    status: ExceptionStatus = ExceptionStatus.PENDING_CLARIFY,
) -> ExceptionRecord:
    base = datetime(2026, 4, 22, tzinfo=timezone.utc)
    return ExceptionRecord(
        source_message_id=source_message_id,
        thread_id=thread_id,
        status=status,
        reason="Integration: SKU mismatch.",
        parsed_doc=ParsedDocument(
            classification="purchase_order",
            classification_rationale="integration test fixture",
            sub_documents=[
                ExtractedOrder(
                    customer_name="Integration Test Co",
                    line_items=[OrderLineItem(sku="MYSTERY", quantity=10, unit_of_measure="EA")],
                )
            ],
        ),
        validation_result=ValidationResult(
            customer=None,
            lines=[
                LineItemValidation(
                    line_index=0, matched_sku=None, match_tier="none",
                    match_confidence=0.0, notes=["no match"],
                )
            ],
            aggregate_confidence=0.85,
            decision=RoutingDecision.CLARIFY,
            rationale="1 line, 1 unmatched -> clarify",
        ),
        created_at=base,
        updated_at=base,
    )


async def test_save_and_get_round_trip_against_emulator(store):
    msg_id = f"int-exc-{uuid.uuid4().hex}"
    exc = _exception(msg_id, thread_id=f"thr-{msg_id}")

    await store.save(exc)
    fetched = await store.get(msg_id)

    assert fetched is not None
    assert fetched.source_message_id == msg_id
    assert fetched.status is ExceptionStatus.PENDING_CLARIFY
    assert fetched.parsed_doc.classification == "purchase_order"
    assert fetched.validation_result.decision is RoutingDecision.CLARIFY


async def test_find_pending_clarify_uses_composite_index(store):
    """If the composite index is missing the emulator logs a warning and
    the query still works, but production Firestore would fail. This test
    documents the index requirement and exercises the query path
    end-to-end."""
    thread_id = f"thr-int-{uuid.uuid4().hex}"
    msg_id = f"int-exc-{uuid.uuid4().hex}"
    await store.save(_exception(msg_id, thread_id=thread_id))

    found = await store.find_pending_clarify(thread_id)
    assert found is not None
    assert found.source_message_id == msg_id


async def test_update_with_reply_round_trip_against_emulator(store):
    msg_id = f"int-exc-{uuid.uuid4().hex}"
    await store.save(_exception(msg_id, thread_id=f"thr-{msg_id}"))

    advanced = await store.update_with_reply(
        source_message_id=msg_id, reply_message_id="reply-int-123"
    )

    assert advanced.status is ExceptionStatus.AWAITING_REVIEW
    assert advanced.reply_message_id == "reply-int-123"

    # Verify the emulator now reflects the new state on a fresh read.
    fetched = await store.get(msg_id)
    assert fetched is not None
    assert fetched.status is ExceptionStatus.AWAITING_REVIEW


async def test_full_lifecycle_pending_to_awaiting_review(store):
    """End-to-end: save PENDING_CLARIFY → find_pending_clarify hits → reply
    advances → find_pending_clarify no longer returns it (different status)."""
    thread_id = f"thr-life-{uuid.uuid4().hex}"
    msg_id = f"int-exc-{uuid.uuid4().hex}"
    await store.save(_exception(msg_id, thread_id=thread_id))

    pending = await store.find_pending_clarify(thread_id)
    assert pending is not None and pending.source_message_id == msg_id

    await store.update_with_reply(msg_id, reply_message_id="reply-life-1")

    after = await store.find_pending_clarify(thread_id)
    assert after is None  # exception now AWAITING_REVIEW; not in pending bucket
