"""Compare a parsed line's quoted unit price against the catalog price.

Pure synchronous function over a :class:`OrderLineItem` and the
:class:`ProductRecord` the matcher resolved to it. No Firestore knowledge,
no async — runs in microseconds, tests in milliseconds.

Permissive on missing quotes (real B2B POs frequently omit per-line
prices and accept the seller's catalog price). Strict on outsized
deviations: anything outside the tolerance band routes the order to
clarify so a human confirms the discount or rejects the price.

The tolerance default of ``10%`` is wide enough to absorb routine
contract discounts (Patterson's 3.5% Hydraulic rebate per the master
data notes) but tight enough to catch genuine pricing errors.
"""

from __future__ import annotations

from backend.models.master_records import ProductRecord
from backend.models.parsed_document import OrderLineItem

DEFAULT_PRICE_TOLERANCE_PCT: float = 10.0
"""Symmetric ± band around catalog price, in percent."""


def check_price(
    line: OrderLineItem,
    product: ProductRecord,
    tolerance_pct: float = DEFAULT_PRICE_TOLERANCE_PCT,
) -> tuple[bool, str]:
    """Return ``(ok, reason)``. ``reason`` is a fact suitable for the
    clarify email or exception card; it is empty on the clean path."""

    if line.unit_price is None:
        return True, ""

    catalog = product.unit_price_usd
    if catalog <= 0:
        # Catalog data error, not a customer-facing failure — fail safe to clarify.
        return False, f"catalog price for {product.sku} is non-positive ({catalog})"

    quoted = float(line.unit_price)
    if quoted <= 0:
        return False, f"quoted unit_price is non-positive ({quoted})"

    delta_pct = (quoted - catalog) / catalog * 100.0
    if abs(delta_pct) <= tolerance_pct:
        return True, ""

    sign = "+" if delta_pct >= 0 else ""
    return (
        False,
        f"unit_price {quoted:.2f} vs catalog {catalog:.2f} ({sign}{delta_pct:.1f}%)",
    )


__all__ = ["check_price", "DEFAULT_PRICE_TOLERANCE_PCT"]
