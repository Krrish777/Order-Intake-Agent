"""Emulator-backed integration tests for Track C duplicate detection.

Exercises the real Firestore async client + the production find_duplicate
function. Guards against compound-query limitations (!= + >=) that the
FakeAsyncClient cannot catch.

Requires FIRESTORE_EMULATOR_HOST to be set.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from google.cloud.firestore_v1.async_client import AsyncClient

from backend.models.parsed_document import ExtractedOrder, OrderLineItem
from backend.tools.order_validator.tools.duplicate_check import (
    compute_content_hash,
    find_duplicate,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("FIRESTORE_EMULATOR_HOST"),
        reason="FIRESTORE_EMULATOR_HOST not set; emulator tests skipped",
    ),
]


@pytest.fixture
async def async_client():
    """Fresh Firestore async client against emulator, with cleanup."""
    client = AsyncClient(project="demo-order-intake-local")
    # Clean orders before each test (isolation across tests in same emulator)
    async for doc in client.collection("orders").stream():
        await doc.reference.delete()
    yield client
    async for doc in client.collection("orders").stream():
        await doc.reference.delete()


async def _seed(client: AsyncClient, **fields) -> str:
    ref = client.collection("orders").document()
    await ref.set(fields)
    return ref.id


class TestDuplicateCheckAgainstEmulator:
    async def test_po_number_hit_across_emulator(self, async_client):
        now = datetime.now(timezone.utc)
        prior_id = await _seed(
            async_client,
            customer_id="CUST-1",
            po_number="PO-ABC",
            content_hash="unrelated",
            source_message_id="msg-prior",
            created_at=now - timedelta(days=5),
        )

        result = await find_duplicate(
            async_client,
            customer_id="CUST-1",
            order=ExtractedOrder(
                customer_name="Acme",
                line_items=[OrderLineItem(sku="SKU-X", quantity=1.0)],
            ),
            source_message_id="msg-current",
            po_number="PO-ABC",
        )
        assert result == prior_id

    async def test_content_hash_hit_across_emulator(self, async_client):
        now = datetime.now(timezone.utc)
        order = ExtractedOrder(
            customer_name="Acme",
            line_items=[OrderLineItem(sku="SKU-A", quantity=5.0)],
        )
        expected = compute_content_hash("CUST-1", order)

        prior_id = await _seed(
            async_client,
            customer_id="CUST-1",
            po_number=None,
            content_hash=expected,
            source_message_id="msg-prior",
            created_at=now - timedelta(days=5),
        )

        result = await find_duplicate(
            async_client,
            customer_id="CUST-1",
            order=order,
            source_message_id="msg-current",
            po_number=None,
        )
        assert result == prior_id

    async def test_window_expiry_across_emulator(self, async_client):
        now = datetime.now(timezone.utc)
        order = ExtractedOrder(
            customer_name="Acme",
            line_items=[OrderLineItem(sku="SKU-A", quantity=5.0)],
        )
        expected = compute_content_hash("CUST-1", order)

        await _seed(
            async_client,
            customer_id="CUST-1",
            po_number="PO-OLD",
            content_hash=expected,
            source_message_id="msg-old",
            created_at=now - timedelta(days=91),
        )

        result = await find_duplicate(
            async_client,
            customer_id="CUST-1",
            order=order,
            source_message_id="msg-current",
            po_number="PO-OLD",
        )
        assert result is None
