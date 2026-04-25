"""Unit tests for :class:`backend.persistence.exceptions_store.FirestoreExceptionStore`.

Uses the extended :class:`FakeAsyncClient` from ``conftest.py``. The fake's
query, transaction, and SERVER_TIMESTAMP support stand in for the real
Firestore async surface; integration parity is asserted in
``tests/integration/test_exception_store_emulator.py``.
"""

from __future__ import annotations

from datetime import datetime, timezone

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


# ----------------------------------------------------- helpers


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


def _sample_validation_result(
    decision: RoutingDecision = RoutingDecision.CLARIFY,
    confidence: float = 0.85,
) -> ValidationResult:
    return ValidationResult(
        customer=None,  # exception case: customer not always resolved
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
        aggregate_confidence=confidence,
        decision=decision,
        rationale="1 line, confidence 0.85, 1 unmatched -> clarify",
    )


def _sample_exception(
    source_message_id: str = "msg-001",
    thread_id: str = "thread-001",
    status: ExceptionStatus = ExceptionStatus.PENDING_CLARIFY,
    reason: str = "Line 0: no match for 'MYSTERY-SKU'.",
    clarify_message_id: str | None = None,
    reply_message_id: str | None = None,
    clarify_body: str | None = None,
    sent_at: datetime | None = None,
    send_error: str | None = None,
) -> ExceptionRecord:
    base = datetime(2026, 4, 22, 0, 0, 0, tzinfo=timezone.utc)
    return ExceptionRecord(
        source_message_id=source_message_id,
        thread_id=thread_id,
        clarify_message_id=clarify_message_id,
        reply_message_id=reply_message_id,
        status=status,
        reason=reason,
        clarify_body=clarify_body,
        parsed_doc=_sample_parsed_doc(),
        validation_result=_sample_validation_result(),
        created_at=base,
        updated_at=base,
        sent_at=sent_at,
        send_error=send_error,
    )


# ----------------------------------------------------- save / get


async def test_save_writes_exception_to_exceptions_collection(fake_client):
    from backend.persistence.exceptions_store import (
        EXCEPTIONS_COLLECTION,
        FirestoreExceptionStore,
    )

    store = FirestoreExceptionStore(fake_client)
    exc = _sample_exception()

    await store.save(exc)

    snap = (
        await fake_client.collection(EXCEPTIONS_COLLECTION)
        .document(exc.source_message_id)
        .get()
    )
    assert snap.exists
    assert snap.to_dict()["source_message_id"] == "msg-001"


async def test_save_is_idempotent_on_source_message_id():
    from tests.unit.conftest import FakeAsyncClient
    from backend.persistence.exceptions_store import (
        EXCEPTIONS_COLLECTION,
        FirestoreExceptionStore,
    )

    t1 = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 4, 22, 11, 0, 0, tzinfo=timezone.utc)
    clock = iter([t1, t2])
    client = FakeAsyncClient({}, clock=lambda: next(clock))
    store = FirestoreExceptionStore(client)

    first = _sample_exception(reason="first reason")
    second = _sample_exception(reason="overwriting reason")

    first_persisted = await store.save(first)
    second_persisted = await store.save(second)

    assert first_persisted.created_at == t1
    assert second_persisted.created_at == t1
    assert second_persisted.reason == "first reason"
    bucket = client._store.get(EXCEPTIONS_COLLECTION, {})
    assert len(bucket) == 1


async def test_get_returns_none_for_unknown_id(fake_client):
    from backend.persistence.exceptions_store import FirestoreExceptionStore

    store = FirestoreExceptionStore(fake_client)
    assert await store.get("missing") is None


async def test_get_returns_full_exception_after_save(fake_client):
    from backend.persistence.exceptions_store import FirestoreExceptionStore

    store = FirestoreExceptionStore(fake_client)
    saved = await store.save(_sample_exception())

    fetched = await store.get(saved.source_message_id)

    assert fetched is not None
    assert fetched.source_message_id == "msg-001"
    assert fetched.status is ExceptionStatus.PENDING_CLARIFY
    assert fetched.reason == "Line 0: no match for 'MYSTERY-SKU'."


# ----------------------------------------------------- find_pending_clarify


async def test_find_pending_clarify_returns_matching_pending_in_thread(fake_client):
    from backend.persistence.exceptions_store import FirestoreExceptionStore

    store = FirestoreExceptionStore(fake_client)
    await store.save(
        _sample_exception(source_message_id="msg-A", thread_id="thread-X")
    )

    found = await store.find_pending_clarify("thread-X")

    assert found is not None
    assert found.source_message_id == "msg-A"
    assert found.status is ExceptionStatus.PENDING_CLARIFY


async def test_find_pending_clarify_returns_none_when_no_match(fake_client):
    from backend.persistence.exceptions_store import FirestoreExceptionStore

    store = FirestoreExceptionStore(fake_client)
    await store.save(
        _sample_exception(source_message_id="msg-A", thread_id="thread-X")
    )

    assert await store.find_pending_clarify("thread-NOPE") is None


async def test_find_pending_clarify_ignores_non_pending_status(fake_client):
    """A thread with only AWAITING_REVIEW or RESOLVED exceptions returns None
    — a reply on a closed thread should open a fresh exception, not retroactively
    advance a resolved one."""
    from backend.persistence.exceptions_store import FirestoreExceptionStore

    store = FirestoreExceptionStore(fake_client)
    await store.save(
        _sample_exception(
            source_message_id="msg-A",
            thread_id="thread-X",
            status=ExceptionStatus.AWAITING_REVIEW,
        )
    )
    await store.save(
        _sample_exception(
            source_message_id="msg-B",
            thread_id="thread-X",
            status=ExceptionStatus.RESOLVED,
        )
    )

    assert await store.find_pending_clarify("thread-X") is None


async def test_find_pending_clarify_returns_most_recent_when_multiple_pending():
    """If multiple PENDING_CLARIFY exceptions live in the same thread (rare,
    but possible during retries), return the most recently created."""
    from tests.unit.conftest import FakeAsyncClient
    from backend.persistence.exceptions_store import FirestoreExceptionStore

    earlier = datetime(2026, 4, 22, 9, 0, 0, tzinfo=timezone.utc)
    later = datetime(2026, 4, 22, 12, 0, 0, tzinfo=timezone.utc)
    clock = iter([earlier, later])
    client = FakeAsyncClient({}, clock=lambda: next(clock))
    store = FirestoreExceptionStore(client)

    await store.save(
        _sample_exception(source_message_id="msg-old", thread_id="thread-X")
    )
    await store.save(
        _sample_exception(source_message_id="msg-new", thread_id="thread-X")
    )

    found = await store.find_pending_clarify("thread-X")
    assert found is not None
    assert found.source_message_id == "msg-new"


# ----------------------------------------------------- update_with_reply


async def test_update_with_reply_advances_pending_to_awaiting_review():
    from tests.unit.conftest import FakeAsyncClient
    from backend.persistence.exceptions_store import FirestoreExceptionStore

    saved_at = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
    reply_at = datetime(2026, 4, 22, 14, 30, 0, tzinfo=timezone.utc)
    clock = iter([saved_at, reply_at])
    client = FakeAsyncClient({}, clock=lambda: next(clock))
    store = FirestoreExceptionStore(client)

    await store.save(_sample_exception(source_message_id="msg-001"))

    advanced = await store.update_with_reply(
        source_message_id="msg-001", reply_message_id="reply-msg-007"
    )

    assert advanced.status is ExceptionStatus.AWAITING_REVIEW
    assert advanced.reply_message_id == "reply-msg-007"
    assert advanced.updated_at == reply_at


async def test_update_with_reply_preserves_other_fields(fake_client):
    from backend.persistence.exceptions_store import FirestoreExceptionStore

    store = FirestoreExceptionStore(fake_client)
    original = await store.save(
        _sample_exception(
            source_message_id="msg-001",
            clarify_message_id="clarify-msg-A",
            reason="Original reason about MYSTERY-SKU.",
        )
    )

    advanced = await store.update_with_reply(
        source_message_id="msg-001", reply_message_id="reply-msg-007"
    )

    assert advanced.source_message_id == original.source_message_id
    assert advanced.thread_id == original.thread_id
    assert advanced.clarify_message_id == "clarify-msg-A"  # unchanged
    assert advanced.reason == "Original reason about MYSTERY-SKU."
    assert advanced.parsed_doc.classification == "purchase_order"
    assert advanced.created_at == original.created_at  # only updated_at moves


async def test_update_with_reply_raises_when_not_pending(fake_client):
    """Cannot advance an already-advanced exception. Defensive guard against
    duplicate replies."""
    import pytest
    from backend.persistence.exceptions_store import FirestoreExceptionStore

    store = FirestoreExceptionStore(fake_client)
    await store.save(
        _sample_exception(
            source_message_id="msg-001", status=ExceptionStatus.AWAITING_REVIEW
        )
    )

    with pytest.raises(ValueError, match="status"):
        await store.update_with_reply(
            source_message_id="msg-001", reply_message_id="reply-007"
        )


async def test_update_with_reply_raises_when_source_message_id_unknown(fake_client):
    import pytest
    from backend.persistence.exceptions_store import FirestoreExceptionStore

    store = FirestoreExceptionStore(fake_client)

    with pytest.raises(LookupError, match="msg-missing"):
        await store.update_with_reply(
            source_message_id="msg-missing", reply_message_id="reply-007"
        )


# ----------------------------------------------------- snapshot round-trips


async def test_save_preserves_parsed_document_snapshot(fake_client):
    """The full ParsedDocument snapshot must survive Firestore serialization
    so the dashboard can render 'what the agent saw'."""
    from backend.persistence.exceptions_store import FirestoreExceptionStore

    store = FirestoreExceptionStore(fake_client)
    saved = await store.save(_sample_exception())

    fetched = await store.get(saved.source_message_id)
    assert fetched is not None
    assert fetched.parsed_doc.classification == "purchase_order"
    assert fetched.parsed_doc.classification_rationale.startswith("Subject reads")
    assert len(fetched.parsed_doc.sub_documents) == 1
    sub = fetched.parsed_doc.sub_documents[0]
    assert sub.customer_name == "Birch Valley Manufacturing"
    assert sub.po_number == "PO-12345"
    assert sub.line_items[0].sku == "MYSTERY-SKU"


async def test_save_preserves_validation_result_snapshot(fake_client):
    """The full ValidationResult snapshot — including LineItemValidation
    children and the RoutingDecision enum — must round-trip."""
    from backend.persistence.exceptions_store import FirestoreExceptionStore

    store = FirestoreExceptionStore(fake_client)
    saved = await store.save(_sample_exception())

    fetched = await store.get(saved.source_message_id)
    assert fetched is not None
    vr = fetched.validation_result
    assert vr.decision is RoutingDecision.CLARIFY
    assert vr.aggregate_confidence == 0.85
    assert len(vr.lines) == 1
    assert vr.lines[0].matched_sku is None
    assert "MYSTERY-SKU" in vr.lines[0].notes[0]


async def test_save_round_trips_clarify_body(fake_client):
    """The clarify_body field (added in schema v2) must survive Firestore
    serialization — the dashboard reads it to render the generated email
    alongside the exception."""
    from backend.persistence.exceptions_store import FirestoreExceptionStore

    body = (
        "Hi Pat,\n\nWe received your PO but couldn't match the SKU on line 1.\n"
        "Could you confirm the part number?\n\nThanks,\nOrders"
    )
    store = FirestoreExceptionStore(fake_client)
    saved = await store.save(_sample_exception(clarify_body=body))

    fetched = await store.get(saved.source_message_id)
    assert fetched is not None
    assert fetched.clarify_body == body
    assert fetched.schema_version == 4


class TestExceptionRecordSchemaV3:
    """Track A2/B — schema v4 with send-receipt and judge_verdict fields."""

    def test_schema_version_default_is_3(self):
        record = _sample_exception()
        assert record.schema_version == 4

    def test_sent_at_and_send_error_default_to_none(self):
        record = _sample_exception()
        assert record.sent_at is None
        assert record.send_error is None

    def test_sent_at_accepts_utc_datetime(self):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        record = _sample_exception(sent_at=now)
        assert record.sent_at == now


class TestExceptionStoreUpdateWithSendReceipt:
    """Track A2 — field-mask update of sent_at + send_error post-save."""

    async def test_update_sets_sent_at(self, fake_client):
        from backend.persistence.exceptions_store import FirestoreExceptionStore

        store = FirestoreExceptionStore(fake_client)
        await store.save(_sample_exception(source_message_id="msg-A"))

        sent_at = datetime(2026, 4, 25, 10, 0, 0, tzinfo=timezone.utc)
        await store.update_with_send_receipt(
            source_message_id="msg-A",
            sent_at=sent_at,
            send_error=None,
        )

        got = await store.get("msg-A")
        assert got is not None
        assert got.sent_at == sent_at
        assert got.send_error is None

    async def test_update_records_send_error(self, fake_client):
        from backend.persistence.exceptions_store import FirestoreExceptionStore

        store = FirestoreExceptionStore(fake_client)
        await store.save(_sample_exception(source_message_id="msg-B"))

        await store.update_with_send_receipt(
            source_message_id="msg-B",
            sent_at=None,
            send_error="no_recipient",
        )

        got = await store.get("msg-B")
        assert got is not None
        assert got.sent_at is None
        assert got.send_error == "no_recipient"
