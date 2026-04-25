"""Unit tests for :class:`backend.persistence.orders_store.FirestoreOrderStore`.

Uses the extended :class:`FakeAsyncClient` from ``conftest.py``. Real-semantics
parity with Firestore is asserted in
``tests/integration/test_order_store_emulator.py``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.models.master_records import AddressRecord
from backend.models.order_record import (
    CustomerSnapshot,
    OrderLine,
    OrderRecord,
    ProductSnapshot,
)


# ----------------------------------------------------- helpers


def _sample_order(
    source_message_id: str = "msg-001",
    thread_id: str = "thread-001",
    confidence: float = 0.98,
) -> OrderRecord:
    return OrderRecord(
        source_message_id=source_message_id,
        thread_id=thread_id,
        customer=CustomerSnapshot(
            customer_id="CUST-00001",
            name="Ohio Valley Industrial Supply",
            bill_to=AddressRecord(
                street1="742 Industrial Pkwy",
                city="Cincinnati",
                state="OH",
                zip="45202",
                country="USA",
            ),
            payment_terms="Net 30",
            contact_email="ap@ohiovalley.example.com",
        ),
        customer_id="CUST-00001",
        content_hash="a" * 64,
        lines=[
            OrderLine(
                line_number=0,
                product=ProductSnapshot(
                    sku="HX-123",
                    short_description="Hex bolt M8",
                    uom="EA",
                    price_at_time=0.42,
                ),
                quantity=100,
                line_total=42.00,
                confidence=1.0,
            )
        ],
        order_total=42.00,
        confidence=confidence,
        processed_by_agent_version="v0.1.0",
        created_at=datetime(2026, 4, 22, 0, 0, 0, tzinfo=timezone.utc),
    )


# ----------------------------------------------------- tests


async def test_save_writes_order_to_orders_collection(fake_client):
    from backend.persistence.orders_store import ORDERS_COLLECTION, FirestoreOrderStore

    store = FirestoreOrderStore(fake_client)
    order = _sample_order()

    await store.save(order)

    snap = (
        await fake_client.collection(ORDERS_COLLECTION)
        .document(order.source_message_id)
        .get()
    )
    assert snap.exists
    assert snap.to_dict()["source_message_id"] == "msg-001"


async def test_save_stamps_created_at_with_server_timestamp():
    """`save()` must substitute the record's `created_at` with the server
    clock — the dashboard should reflect when we wrote, not what the caller
    fabricated."""
    from tests.unit.conftest import FakeAsyncClient
    from backend.persistence.orders_store import ORDERS_COLLECTION, FirestoreOrderStore

    fixed_now = datetime(2026, 4, 22, 15, 30, 0, tzinfo=timezone.utc)
    client = FakeAsyncClient({}, clock=lambda: fixed_now)
    store = FirestoreOrderStore(client)

    stale_created_at = datetime(1970, 1, 1, tzinfo=timezone.utc)
    order = _sample_order()
    object.__setattr__(order, "created_at", stale_created_at)

    persisted = await store.save(order)

    assert persisted.created_at == fixed_now
    stored = (
        await client.collection(ORDERS_COLLECTION)
        .document(order.source_message_id)
        .get()
    )
    assert stored.to_dict()["created_at"] == fixed_now


async def test_save_is_idempotent_on_source_message_id():
    """Pub/Sub redelivery safety: a second save with the same source_message_id
    must return the originally-persisted record, not overwrite it."""
    from tests.unit.conftest import FakeAsyncClient
    from backend.persistence.orders_store import ORDERS_COLLECTION, FirestoreOrderStore

    t1 = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 4, 22, 11, 0, 0, tzinfo=timezone.utc)
    clock = iter([t1, t2])
    client = FakeAsyncClient({}, clock=lambda: next(clock))
    store = FirestoreOrderStore(client)

    first = _sample_order(confidence=0.95)
    second = _sample_order(confidence=0.50)  # same source_message_id, different data

    first_persisted = await store.save(first)
    second_persisted = await store.save(second)

    # Returned record reflects the FIRST save (confidence 0.95, created_at t1)
    assert first_persisted.created_at == t1
    assert second_persisted.created_at == t1
    assert second_persisted.confidence == 0.95

    # Collection contains exactly one doc
    bucket = client._store.get(ORDERS_COLLECTION, {})
    assert len(bucket) == 1


async def test_get_returns_none_for_unknown_id(fake_client):
    from backend.persistence.orders_store import FirestoreOrderStore

    store = FirestoreOrderStore(fake_client)
    assert await store.get("does-not-exist") is None


async def test_get_returns_full_order_after_save(fake_client):
    from backend.persistence.orders_store import FirestoreOrderStore

    store = FirestoreOrderStore(fake_client)
    saved = await store.save(_sample_order())

    fetched = await store.get(saved.source_message_id)

    assert fetched is not None
    assert fetched.source_message_id == saved.source_message_id
    assert fetched.customer.name == "Ohio Valley Industrial Supply"
    assert len(fetched.lines) == 1
    assert fetched.lines[0].product.sku == "HX-123"


async def test_save_preserves_schema_version_default(fake_client):
    from backend.persistence.orders_store import FirestoreOrderStore

    store = FirestoreOrderStore(fake_client)
    saved = await store.save(_sample_order())
    fetched = await store.get(saved.source_message_id)

    assert fetched is not None
    # Bumped 1 → 2 when ``confirmation_body`` was added on the
    # ConfirmStage leg (AUTO_APPROVE confirmation email).
    # Bumped 2 → 3 (Track C) when denormalized query fields were added.
    # Bumped 3 → 4 (Track A2) when sent_at + send_error were added.
    assert fetched.schema_version == 4


async def test_save_preserves_nested_snapshots_through_roundtrip(fake_client):
    """AddressRecord, ProductSnapshot, and OrderLine all survive
    serialize → Firestore-dict → deserialize."""
    from backend.persistence.orders_store import FirestoreOrderStore

    store = FirestoreOrderStore(fake_client)
    saved = await store.save(_sample_order())
    fetched = await store.get(saved.source_message_id)

    assert fetched is not None
    assert fetched.customer.bill_to.city == "Cincinnati"
    assert fetched.customer.bill_to.zip == "45202"
    assert fetched.customer.contact_email == "ap@ohiovalley.example.com"
    assert fetched.lines[0].product.uom == "EA"
    assert fetched.lines[0].product.price_at_time == 0.42
    assert fetched.lines[0].quantity == 100


async def test_save_preserves_status_enum_as_string_value(fake_client):
    """OrderStatus is a StrEnum — Firestore stores the .value, and
    deserialization recovers the enum member."""
    from backend.models.order_record import OrderStatus
    from backend.persistence.orders_store import (
        ORDERS_COLLECTION,
        FirestoreOrderStore,
    )

    store = FirestoreOrderStore(fake_client)
    saved = await store.save(_sample_order())

    raw = (
        await fake_client.collection(ORDERS_COLLECTION)
        .document(saved.source_message_id)
        .get()
    )
    assert raw.to_dict()["status"] == "persisted"

    fetched = await store.get(saved.source_message_id)
    assert fetched is not None
    assert fetched.status is OrderStatus.PERSISTED


async def test_order_record_validation_rejects_out_of_range_confidence():
    """Pydantic guards block invalid confidence at construction time —
    the store should never receive a malformed record."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _sample_order(confidence=1.5)
    with pytest.raises(ValidationError):
        _sample_order(confidence=-0.1)


async def test_save_preserves_distinct_orders_independently(fake_client):
    """Two orders with different source_message_ids both persist; idempotency
    must be per-doc, not collection-wide."""
    from backend.persistence.orders_store import ORDERS_COLLECTION, FirestoreOrderStore

    store = FirestoreOrderStore(fake_client)
    a = _sample_order(source_message_id="msg-A", thread_id="thread-A")
    b = _sample_order(source_message_id="msg-B", thread_id="thread-B")

    await store.save(a)
    await store.save(b)

    bucket = fake_client._store.get(ORDERS_COLLECTION, {})
    assert set(bucket.keys()) == {"msg-A", "msg-B"}
    assert (await store.get("msg-A")) is not None
    assert (await store.get("msg-B")) is not None


# ---------------------------------------- update_with_confirmation


async def test_update_with_confirmation_sets_body_on_existing_doc(fake_client):
    """Happy path: doc exists → field is written → subsequent get()
    returns the body."""
    from backend.persistence.orders_store import FirestoreOrderStore

    store = FirestoreOrderStore(fake_client)
    saved = await store.save(_sample_order())

    updated = await store.update_with_confirmation(
        saved.source_message_id,
        confirmation_body="Thanks Tony — order confirmed, $127.40.",
    )
    assert updated.confirmation_body == "Thanks Tony — order confirmed, $127.40."

    reread = await store.get(saved.source_message_id)
    assert reread is not None
    assert reread.confirmation_body == "Thanks Tony — order confirmed, $127.40."


async def test_update_with_confirmation_raises_when_doc_missing(fake_client):
    """Update on a non-existent doc is a caller bug — fail fast, do not
    silently create. ConfirmStage only invokes this for orders that
    PersistStage just persisted this invocation."""
    import pytest
    from google.api_core.exceptions import NotFound

    from backend.persistence.orders_store import FirestoreOrderStore

    store = FirestoreOrderStore(fake_client)
    with pytest.raises(NotFound):
        await store.update_with_confirmation(
            "<never-existed@example.com>",
            confirmation_body="should not land",
        )


async def test_update_with_confirmation_overwrites(fake_client):
    """Re-calling overwrites the previous body. Every pipeline run
    regenerates a fresh confirmation; no idempotency skip."""
    from backend.persistence.orders_store import FirestoreOrderStore

    store = FirestoreOrderStore(fake_client)
    saved = await store.save(_sample_order())

    await store.update_with_confirmation(
        saved.source_message_id,
        confirmation_body="first draft",
    )
    await store.update_with_confirmation(
        saved.source_message_id,
        confirmation_body="second draft",
    )
    reread = await store.get(saved.source_message_id)
    assert reread is not None
    assert reread.confirmation_body == "second draft"


# New test — appended to tests/unit/test_order_store.py

import pytest
from pydantic import ValidationError


def _minimal_customer_snapshot() -> CustomerSnapshot:
    return CustomerSnapshot(
        customer_id="CUST-00042",
        name="Acme Corp",
        bill_to=AddressRecord(
            street1="100 Industrial Way",
            city="Dayton",
            state="OH",
            zip="45402",
            country="USA",
        ),
        payment_terms="Net 30",
    )


class TestOrderRecordSchemaV3:
    def test_customer_id_is_required(self):
        with pytest.raises(ValidationError) as exc_info:
            OrderRecord(
                source_message_id="msg-1",
                thread_id="thr-1",
                customer=_minimal_customer_snapshot(),
                # customer_id omitted
                po_number="PO-123",
                content_hash="a" * 64,
                lines=[],
                order_total=0.0,
                confidence=1.0,
                processed_by_agent_version="track-a-v0.2",
                created_at=datetime.now(timezone.utc),
            )
        assert "customer_id" in str(exc_info.value)

    def test_content_hash_is_required(self):
        with pytest.raises(ValidationError) as exc_info:
            OrderRecord(
                source_message_id="msg-1",
                thread_id="thr-1",
                customer=_minimal_customer_snapshot(),
                customer_id="CUST-00042",
                po_number="PO-123",
                # content_hash omitted
                lines=[],
                order_total=0.0,
                confidence=1.0,
                processed_by_agent_version="track-a-v0.2",
                created_at=datetime.now(timezone.utc),
            )
        assert "content_hash" in str(exc_info.value)

    def test_po_number_defaults_to_none(self):
        record = OrderRecord(
            source_message_id="msg-1",
            thread_id="thr-1",
            customer=_minimal_customer_snapshot(),
            customer_id="CUST-00042",
            # po_number omitted
            content_hash="a" * 64,
            lines=[],
            order_total=0.0,
            confidence=1.0,
            processed_by_agent_version="track-a-v0.2",
            created_at=datetime.now(timezone.utc),
        )
        assert record.po_number is None


class TestOrderRecordSchemaV4:
    """Track A2 — schema v4 with send-receipt fields."""

    def test_schema_version_default_is_4(self):
        record = OrderRecord(
            source_message_id="msg-1",
            thread_id="thr-1",
            customer=_minimal_customer_snapshot(),
            customer_id="CUST-00042",
            po_number="PO-123",
            content_hash="a" * 64,
            lines=[],
            order_total=0.0,
            confidence=1.0,
            processed_by_agent_version="track-a-v0.3",
            created_at=datetime.now(timezone.utc),
        )
        assert record.schema_version == 4

    def test_sent_at_defaults_to_none(self):
        record = OrderRecord(
            source_message_id="msg-1",
            thread_id="thr-1",
            customer=_minimal_customer_snapshot(),
            customer_id="CUST-00042",
            content_hash="a" * 64,
            lines=[],
            order_total=0.0,
            confidence=1.0,
            processed_by_agent_version="track-a-v0.3",
            created_at=datetime.now(timezone.utc),
        )
        assert record.sent_at is None

    def test_send_error_defaults_to_none(self):
        record = OrderRecord(
            source_message_id="msg-1",
            thread_id="thr-1",
            customer=_minimal_customer_snapshot(),
            customer_id="CUST-00042",
            content_hash="a" * 64,
            lines=[],
            order_total=0.0,
            confidence=1.0,
            processed_by_agent_version="track-a-v0.3",
            created_at=datetime.now(timezone.utc),
        )
        assert record.send_error is None

    def test_sent_at_accepts_utc_datetime(self):
        now = datetime.now(timezone.utc)
        record = OrderRecord(
            source_message_id="msg-1",
            thread_id="thr-1",
            customer=_minimal_customer_snapshot(),
            customer_id="CUST-00042",
            content_hash="a" * 64,
            lines=[],
            order_total=0.0,
            confidence=1.0,
            processed_by_agent_version="track-a-v0.3",
            created_at=datetime.now(timezone.utc),
            sent_at=now,
        )
        assert record.sent_at == now
