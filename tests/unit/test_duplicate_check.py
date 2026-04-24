"""Unit tests for duplicate_check.compute_content_hash.

Spec: docs/superpowers/specs/2026-04-24-track-c-duplicate-detection-design.md
"""
from __future__ import annotations

import pytest
from google.cloud.firestore_v1.base_query import FieldFilter

from backend.models.parsed_document import ExtractedOrder, OrderLineItem
from backend.tools.order_validator.tools.duplicate_check import (
    DUPLICATE_WINDOW_DAYS,
    compute_content_hash,
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
