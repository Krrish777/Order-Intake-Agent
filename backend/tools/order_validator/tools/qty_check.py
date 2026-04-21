"""Sanity-check a parsed line's quantity and unit of measure.

Pure synchronous function over a :class:`OrderLineItem` and the
:class:`ProductRecord` the matcher resolved to it. No Firestore
knowledge, no async.

Three checks, in order — first failure short-circuits:

1. **Presence + sign** — quantity must be set and strictly positive.
2. **Unit of measure** — if the line carries a UoM, it must match
   ``product.uom`` (case-insensitive) or appear in ``product.alt_uoms``.
   Missing UoM on the line is permitted (assume the catalog default).
3. **Min order** — if ``product.min_order_qty`` is set *and* the line
   is expressed in the product's base UoM, quantity must meet or exceed
   it. We skip the floor when the line uses an alt UoM (e.g. 3 BX of a
   50-each pack) because converting BX → EA requires ``pack_size``
   arithmetic the sprint validator deliberately doesn't carry; an alt
   UoM already gestures at bulk ordering so undercounting is unlikely.
"""

from __future__ import annotations

from backend.models.master_records import ProductRecord
from backend.models.parsed_document import OrderLineItem


def check_qty(
    line: OrderLineItem,
    product: ProductRecord,
) -> tuple[bool, str]:
    """Return ``(ok, reason)``. ``reason`` is a fact suitable for the
    clarify email or exception card; it is empty on the clean path."""

    if line.quantity is None:
        return False, "quantity missing"

    qty = float(line.quantity)
    if qty <= 0:
        return False, f"quantity must be positive (got {qty})"

    line_uom_norm = line.unit_of_measure.strip().upper() if line.unit_of_measure else None
    base_uom = product.uom.upper()

    if line_uom_norm:
        allowed = {base_uom, *(u.upper() for u in product.alt_uoms)}
        if line_uom_norm not in allowed:
            allowed_str = ", ".join(sorted(allowed))
            return (
                False,
                f"uom {line.unit_of_measure!r} not allowed for {product.sku} (allowed: {allowed_str})",
            )

    uom_is_base = line_uom_norm is None or line_uom_norm == base_uom
    if uom_is_base and product.min_order_qty is not None and qty < product.min_order_qty:
        return (
            False,
            f"quantity {qty:g} below min_order_qty {product.min_order_qty} for {product.sku}",
        )

    return True, ""


__all__ = ["check_qty"]
