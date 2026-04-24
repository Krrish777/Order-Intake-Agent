"""Unit tests for duplicate_check.compute_content_hash.

Spec: docs/superpowers/specs/2026-04-24-track-c-duplicate-detection-design.md
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

import pytest
from google.cloud.firestore_v1.base_query import FieldFilter

from backend.models.parsed_document import ExtractedOrder, OrderLineItem
from backend.tools.order_validator.tools.duplicate_check import (
    DUPLICATE_WINDOW_DAYS,
    compute_content_hash,
    find_duplicate,
)
from tests.unit.conftest import FakeAsyncClient


def _order(*lines: tuple[str | None, float | None]) -> ExtractedOrder:
    return ExtractedOrder(
        customer_name="Acme",
        po_number="PO-123",
        line_items=[
            OrderLineItem(sku=sku, quantity=qty) for sku, qty in lines
        ],
    )


class TestComputeContentHash:
    def test_deterministic(self):
        order = _order(("SKU-A", 5.0), ("SKU-B", 3.0))
        assert compute_content_hash("CUST-1", order) == compute_content_hash("CUST-1", order)

    def test_order_independent(self):
        a = _order(("SKU-A", 5.0), ("SKU-B", 3.0))
        b = _order(("SKU-B", 3.0), ("SKU-A", 5.0))
        assert compute_content_hash("CUST-1", a) == compute_content_hash("CUST-1", b)

    def test_customer_scoped(self):
        order = _order(("SKU-A", 5.0))
        assert compute_content_hash("CUST-1", order) != compute_content_hash("CUST-2", order)

    def test_strips_whitespace_in_sku(self):
        a = _order(("SKU-A", 5.0))
        b = _order(("  SKU-A  ", 5.0))
        assert compute_content_hash("CUST-1", a) == compute_content_hash("CUST-1", b)

    def test_case_is_not_normalized(self):
        """Documents the trade-off: case-variations slip by content-hash;
        PO# branch is expected to catch them instead."""
        a = _order(("SKU-A", 5.0))
        b = _order(("sku-a", 5.0))
        assert compute_content_hash("CUST-1", a) != compute_content_hash("CUST-1", b)

    def test_none_sku_line_is_skipped(self):
        """Lines with sku=None can't be hashed meaningfully — skipped.
        Order with only a None-sku line hashes same as empty basket."""
        a = _order((None, 5.0))
        b = ExtractedOrder(customer_name="Acme", line_items=[])
        assert compute_content_hash("CUST-1", a) == compute_content_hash("CUST-1", b)

    def test_none_quantity_coerced_to_zero(self):
        a = _order(("SKU-A", None))
        b = _order(("SKU-A", 0.0))
        assert compute_content_hash("CUST-1", a) == compute_content_hash("CUST-1", b)

    def test_returns_64_char_hex_string(self):
        h = compute_content_hash("CUST-1", _order(("SKU-A", 5.0)))
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


def test_window_constant_is_90_days():
    assert DUPLICATE_WINDOW_DAYS == 90


@pytest.mark.asyncio
class TestFakeAsyncClientMultiWhere:
    """Guard: FakeAsyncClient must support 2+ chained .where() calls as AND.

    Required for Task 4 find_duplicate tests that combine
    customer_id + content_hash + created_at + source_message_id.
    """

    async def test_two_where_filters_and(self):
        client = FakeAsyncClient({})
        await client.collection("orders").document("d1").set(
            {"customer_id": "CUST-1", "po_number": "PO-1"}
        )
        await client.collection("orders").document("d2").set(
            {"customer_id": "CUST-1", "po_number": "PO-2"}
        )
        await client.collection("orders").document("d3").set(
            {"customer_id": "CUST-2", "po_number": "PO-1"}
        )

        query = (
            client.collection("orders")
            .where(filter=FieldFilter("customer_id", "==", "CUST-1"))
            .where(filter=FieldFilter("po_number", "==", "PO-1"))
        )
        docs = [doc async for doc in query.stream()]
        assert len(docs) == 1
        assert docs[0].reference.id == "d1"

    async def test_three_where_filters_and(self):
        client = FakeAsyncClient({})
        await client.collection("orders").document("d1").set(
            {"customer_id": "CUST-1", "po_number": "PO-1", "status": "persisted"}
        )
        await client.collection("orders").document("d2").set(
            {"customer_id": "CUST-1", "po_number": "PO-1", "status": "draft"}
        )

        query = (
            client.collection("orders")
            .where(filter=FieldFilter("customer_id", "==", "CUST-1"))
            .where(filter=FieldFilter("po_number", "==", "PO-1"))
            .where(filter=FieldFilter("status", "==", "persisted"))
        )
        docs = [doc async for doc in query.stream()]
        assert len(docs) == 1
        assert docs[0].reference.id == "d1"


def _fixed_clock(when: datetime) -> Callable[[], datetime]:
    return lambda: when


async def _seed_order(
    client: "FakeAsyncClient",
    *,
    doc_id: str,
    customer_id: str,
    po_number: str | None,
    content_hash: str,
    source_message_id: str,
    created_at: datetime,
):
    await client.collection("orders").document(doc_id).set(
        {
            "customer_id": customer_id,
            "po_number": po_number,
            "content_hash": content_hash,
            "source_message_id": source_message_id,
            "created_at": created_at,
        }
    )


NOW = datetime(2026, 4, 24, 12, 0, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
class TestFindDuplicate:
    async def test_returns_none_when_no_prior_orders(self, fake_client):
        result = await find_duplicate(
            fake_client,
            customer_id="CUST-1",
            order=_order(("SKU-A", 5.0)),
            source_message_id="msg-current",
            po_number="PO-123",
            clock=_fixed_clock(NOW),
        )
        assert result is None

    async def test_returns_order_id_on_po_number_hit(self, fake_client):
        await _seed_order(
            fake_client,
            doc_id="ORD-abc123",
            customer_id="CUST-1",
            po_number="PO-123",
            content_hash="different_hash",
            source_message_id="msg-prior",
            created_at=NOW - timedelta(days=5),
        )

        result = await find_duplicate(
            fake_client,
            customer_id="CUST-1",
            order=_order(("SKU-X", 1.0)),  # different basket; PO# still matches
            source_message_id="msg-current",
            po_number="PO-123",
            clock=_fixed_clock(NOW),
        )
        assert result == "ORD-abc123"

    async def test_returns_order_id_on_content_hash_hit_when_po_absent(self, fake_client):
        order = _order(("SKU-A", 5.0))
        hash_val = compute_content_hash("CUST-1", order)
        await _seed_order(
            fake_client,
            doc_id="ORD-def456",
            customer_id="CUST-1",
            po_number=None,
            content_hash=hash_val,
            source_message_id="msg-prior",
            created_at=NOW - timedelta(days=5),
        )

        result = await find_duplicate(
            fake_client,
            customer_id="CUST-1",
            order=order,
            source_message_id="msg-current",
            po_number=None,  # no PO# on incoming
            clock=_fixed_clock(NOW),
        )
        assert result == "ORD-def456"

    async def test_returns_order_id_on_hash_hit_when_po_differs(self, fake_client):
        order = _order(("SKU-A", 5.0))
        hash_val = compute_content_hash("CUST-1", order)
        await _seed_order(
            fake_client,
            doc_id="ORD-ghi789",
            customer_id="CUST-1",
            po_number="PO-OLD",
            content_hash=hash_val,
            source_message_id="msg-prior",
            created_at=NOW - timedelta(days=5),
        )

        # Incoming has a DIFFERENT PO# but same basket → content-hash fires
        result = await find_duplicate(
            fake_client,
            customer_id="CUST-1",
            order=order,
            source_message_id="msg-current",
            po_number="PO-NEW",
            clock=_fixed_clock(NOW),
        )
        assert result == "ORD-ghi789"

    async def test_excludes_self_match_via_source_message_id(self, fake_client):
        """A retry of the same message must NOT flag its own prior persist."""
        order = _order(("SKU-A", 5.0))
        hash_val = compute_content_hash("CUST-1", order)
        await _seed_order(
            fake_client,
            doc_id="ORD-own",
            customer_id="CUST-1",
            po_number="PO-123",
            content_hash=hash_val,
            source_message_id="msg-same",  # identical to current
            created_at=NOW - timedelta(seconds=1),
        )

        result = await find_duplicate(
            fake_client,
            customer_id="CUST-1",
            order=order,
            source_message_id="msg-same",  # same id
            po_number="PO-123",
            clock=_fixed_clock(NOW),
        )
        assert result is None

    async def test_respects_90_day_window(self, fake_client):
        order = _order(("SKU-A", 5.0))
        hash_val = compute_content_hash("CUST-1", order)
        await _seed_order(
            fake_client,
            doc_id="ORD-stale",
            customer_id="CUST-1",
            po_number="PO-123",
            content_hash=hash_val,
            source_message_id="msg-prior",
            created_at=NOW - timedelta(days=91),  # outside window
        )

        result = await find_duplicate(
            fake_client,
            customer_id="CUST-1",
            order=order,
            source_message_id="msg-current",
            po_number="PO-123",
            clock=_fixed_clock(NOW),
        )
        assert result is None
