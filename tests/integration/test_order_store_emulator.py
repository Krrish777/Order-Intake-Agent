"""Integration tests for :class:`FirestoreOrderStore` against the Firestore emulator.

Asserts that the in-memory fake's behaviour matches the real async SDK on
the operations the store actually uses (``create`` with ``AlreadyExists``
on collision, ``SERVER_TIMESTAMP`` resolution, ``get`` round-trip).

Before running::

    firebase emulators:start --only firestore
    set FIRESTORE_EMULATOR_HOST=localhost:8080   # or `export` on POSIX
    uv run pytest -m firestore_emulator tests/integration/test_order_store_emulator.py -v
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest

from backend.models.master_records import AddressRecord
from backend.models.order_record import (
    CustomerSnapshot,
    OrderLine,
    OrderRecord,
    ProductSnapshot,
)
from backend.persistence import FirestoreOrderStore
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
    s = FirestoreOrderStore(client)
    try:
        yield s
    finally:
        client.close()


def _order(source_message_id: str) -> OrderRecord:
    return OrderRecord(
        source_message_id=source_message_id,
        thread_id=f"thread-{source_message_id}",
        customer=CustomerSnapshot(
            customer_id="CUST-INT-TEST",
            name="Integration Test Co",
            bill_to=AddressRecord(
                street1="1 Test Way", city="Testville", state="OH",
                zip="44113", country="USA",
            ),
            payment_terms="Net 30",
        ),
        lines=[
            OrderLine(
                line_number=0,
                product=ProductSnapshot(
                    sku="INT-SKU-1",
                    short_description="Integration test product",
                    uom="EA",
                    price_at_time=1.50,
                ),
                quantity=10,
                line_total=15.00,
                confidence=1.0,
            )
        ],
        order_total=15.00,
        confidence=1.0,
        processed_by_agent_version="v0.1.0",
        created_at=datetime(2026, 4, 22, tzinfo=timezone.utc),
    )


async def test_save_and_get_round_trip_against_emulator(store):
    order = _order(f"int-msg-{uuid.uuid4().hex}")

    persisted = await store.save(order)
    fetched = await store.get(order.source_message_id)

    assert fetched is not None
    assert fetched.source_message_id == order.source_message_id
    assert fetched.customer.name == "Integration Test Co"
    assert fetched.lines[0].product.sku == "INT-SKU-1"
    # SERVER_TIMESTAMP resolved to a real datetime, not the sentinel.
    assert isinstance(persisted.created_at, datetime)
    assert isinstance(fetched.created_at, datetime)


async def test_save_is_idempotent_against_emulator(store):
    """Two saves with the same source_message_id collapse to one doc;
    the second returns the originally-persisted record."""
    msg_id = f"int-msg-{uuid.uuid4().hex}"
    first = _order(msg_id)
    first_persisted = await store.save(first)

    # Mutate the second to confirm the existing doc is returned (not overwritten).
    second = _order(msg_id)
    object.__setattr__(second, "confidence", 0.0)
    second_persisted = await store.save(second)

    assert second_persisted.confidence == first_persisted.confidence == 1.0
    assert second_persisted.created_at == first_persisted.created_at


async def test_get_returns_none_for_unknown_id(store):
    assert await store.get(f"int-msg-missing-{uuid.uuid4().hex}") is None


async def test_distinct_orders_persist_independently(store):
    a_id = f"int-msg-A-{uuid.uuid4().hex}"
    b_id = f"int-msg-B-{uuid.uuid4().hex}"

    await store.save(_order(a_id))
    await store.save(_order(b_id))

    a = await store.get(a_id)
    b = await store.get(b_id)
    assert a is not None and a.source_message_id == a_id
    assert b is not None and b.source_message_id == b_id
