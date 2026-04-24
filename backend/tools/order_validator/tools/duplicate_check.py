"""Duplicate-order preflight check for OrderValidator.

Called as the second step of ``OrderValidator.validate()`` — after
customer resolution, before SKU/price/qty checks. Short-circuits to
``RoutingDecision.ESCALATE`` when the same basket (by PO# OR content
hash) has already landed in ``orders`` for this customer within the
90-day window.

Rationale + design decisions:
docs/superpowers/specs/2026-04-24-track-c-duplicate-detection-design.md
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Callable, Optional

from google.cloud.firestore_v1.async_client import AsyncClient
from google.cloud.firestore_v1.base_query import FieldFilter

from backend.models.parsed_document import ExtractedOrder

DUPLICATE_WINDOW_DAYS = 90


def compute_content_hash(customer_id: str, order: ExtractedOrder) -> str:
    """SHA256 over ``customer_id + sorted [(raw_sku, qty)]``.

    Deterministic and order-independent (shuffling ``order.line_items``
    yields the same hash). Lines where ``sku is None`` are skipped — they
    can't be hashed meaningfully and would otherwise collapse all
    degenerate orders to the same hash. ``quantity is None`` is coerced
    to ``0.0``.

    Uses raw SKU strings from the parsed doc, not sku_matcher output.
    Preserves the preflight-first positioning — dup check runs before
    sku_matcher, saving that work on dups.
    """
    lines = sorted(
        (line.sku.strip(), float(line.quantity or 0.0))
        for line in order.line_items
        if line.sku is not None
    )
    canonical = f"{customer_id}|" + "|".join(
        f"{sku}:{qty}" for sku, qty in lines
    )
    return sha256(canonical.encode()).hexdigest()


async def find_duplicate(
    client: AsyncClient,
    *,
    customer_id: str,
    order: ExtractedOrder,
    source_message_id: str,
    po_number: Optional[str],
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> Optional[str]:
    """Return the existing order id if a duplicate is found in window.

    OR-combines two independent signals against the ``orders`` collection:

    1. PO# match (only when ``po_number`` is not None)
    2. Content-hash match (always)

    Both queries are scoped by ``customer_id`` + ``created_at >= cutoff``,
    where ``cutoff = clock() - DUPLICATE_WINDOW_DAYS``, and both exclude
    self-matches via ``source_message_id != <current>``.

    Returns the first matching doc id (Firestore order is arbitrary;
    for the purpose of "this is a dup" any match suffices). Returns
    ``None`` when no match.

    Exceptions propagate — a Firestore outage must fail the whole run
    rather than silently let a dup through.
    """
    cutoff = clock() - timedelta(days=DUPLICATE_WINDOW_DAYS)
    orders = client.collection("orders")

    if po_number is not None:
        q = (
            orders
            .where(filter=FieldFilter("customer_id", "==", customer_id))
            .where(filter=FieldFilter("po_number", "==", po_number))
            .where(filter=FieldFilter("created_at", ">=", cutoff))
            .where(filter=FieldFilter("source_message_id", "!=", source_message_id))
            .limit(1)
        )
        async for doc in q.stream():
            return doc.reference.id

    content_hash = compute_content_hash(customer_id, order)
    q = (
        orders
        .where(filter=FieldFilter("customer_id", "==", customer_id))
        .where(filter=FieldFilter("content_hash", "==", content_hash))
        .where(filter=FieldFilter("created_at", ">=", cutoff))
        .where(filter=FieldFilter("source_message_id", "!=", source_message_id))
        .limit(1)
    )
    async for doc in q.stream():
        return doc.reference.id

    return None


__all__ = ["DUPLICATE_WINDOW_DAYS", "compute_content_hash", "find_duplicate"]
