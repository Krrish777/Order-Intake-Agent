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

from hashlib import sha256

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


__all__ = ["DUPLICATE_WINDOW_DAYS", "compute_content_hash"]
