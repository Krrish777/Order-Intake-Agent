"""Order validator orchestrator.

Composes the tools under ``.tools`` per line of one parsed order:

* :func:`resolve_customer` runs once at the order level — the returned
  :class:`CustomerRecord` carries ``sku_aliases`` that :func:`match_sku`
  consults for tier-1 alias translation.
* For each line: :func:`match_sku`; if it hits, :func:`check_price` and
  :func:`check_qty` run against the matched :class:`ProductRecord`.
* :func:`aggregate` reduces line confidences + penalties to one float;
  :func:`decide` maps that float to a :class:`RoutingDecision`.

An unresolved customer forces ESCALATE regardless of line scores —
writing an order to a customer we cannot identify is the worst
possible outcome for the downstream ERP write.
"""

from __future__ import annotations

from backend.models.parsed_document import ExtractedOrder
from backend.models.validation_result import (
    LineItemValidation,
    RoutingDecision,
    ValidationResult,
)
from backend.tools.order_validator.router import decide
from backend.tools.order_validator.scorer import aggregate
from backend.tools.order_validator.tools.customer_resolver import resolve_customer
from backend.tools.order_validator.tools.duplicate_check import find_duplicate
from backend.tools.order_validator.tools.master_data_repo import MasterDataRepo
from backend.tools.order_validator.tools.price_check import check_price
from backend.tools.order_validator.tools.qty_check import check_qty
from backend.tools.order_validator.tools.sku_matcher import match_sku
from backend.utils.logging import get_logger

_log = get_logger(__name__)


class OrderValidator:
    """Stateless orchestrator — construct once per process with a shared
    :class:`MasterDataRepo`, then call :meth:`validate` per order.

    The repo's in-memory caches (catalog, customer roster) warm on the
    first call and stay warm for the validator's lifetime, so a
    multi-order email takes one Firestore round-trip for the master
    data regardless of how many orders it carries.
    """

    def __init__(self, repo: MasterDataRepo) -> None:
        self._repo = repo

    async def validate(
        self,
        order: ExtractedOrder,
        *,
        source_message_id: str,
    ) -> ValidationResult:
        customer = await resolve_customer(order, self._repo)

        # ── Duplicate preflight ────────────────────────────────────────
        # Runs only when customer is resolved — unresolved customers
        # already ESCALATE below via the existing path.
        if customer is not None:
            existing_id = await find_duplicate(
                self._repo.firestore_client,
                customer_id=customer.customer_id,
                order=order,
                source_message_id=source_message_id,
                po_number=order.po_number,
            )
            if existing_id is not None:
                _log.info(
                    "duplicate_detected",
                    customer_id=customer.customer_id,
                    existing_order_id=existing_id,
                    source_message_id=source_message_id,
                )
                return ValidationResult(
                    customer=customer,
                    lines=[],
                    aggregate_confidence=1.0,
                    decision=RoutingDecision.ESCALATE,
                    rationale=f"duplicate of {existing_id}",
                )

        # ── Full validation ladder ─────────────────────────────────────
        lines: list[LineItemValidation] = []
        for idx, line in enumerate(order.line_items):
            product, tier, conf = await match_sku(line, self._repo, customer)

            notes: list[str] = []
            price_ok = True
            qty_ok = True

            if product is not None:
                if line.sku and line.sku != product.sku:
                    notes.append(f"resolved alias {line.sku!r} -> {product.sku}")

                price_ok, price_reason = check_price(line, product)
                if price_reason:
                    notes.append(price_reason)

                qty_ok, qty_reason = check_qty(line, product)
                if qty_reason:
                    notes.append(qty_reason)
            else:
                hint = line.sku or line.description or "(no sku or description)"
                notes.append(f"no match for line input: {hint!r}")

            lines.append(
                LineItemValidation(
                    line_index=idx,
                    matched_sku=product.sku if product is not None else None,
                    match_tier=tier,
                    match_confidence=conf,
                    price_ok=price_ok,
                    qty_ok=qty_ok,
                    notes=notes,
                )
            )

        confidence = aggregate(lines)

        if customer is None:
            decision = RoutingDecision.ESCALATE
            rationale = (
                f"customer_name {order.customer_name!r} did not match any "
                "customer in the master — escalating."
            )
        else:
            decision = decide(confidence)
            rationale = _rationale_for(decision, customer.name, lines, confidence)

        _log.info(
            "validation_done",
            customer_id=customer.customer_id if customer else None,
            line_count=len(lines),
            aggregate=confidence,
            decision=decision.value,
        )

        return ValidationResult(
            customer=customer,
            lines=lines,
            aggregate_confidence=confidence,
            decision=decision,
            rationale=rationale,
        )


def _rationale_for(
    decision: RoutingDecision,
    customer_name: str,
    lines: list[LineItemValidation],
    confidence: float,
) -> str:
    n = len(lines)
    failures = sum(1 for ln in lines if not ln.price_ok or not ln.qty_ok)
    misses = sum(1 for ln in lines if ln.matched_sku is None)

    parts = [f"{customer_name}, {n} line{'s' if n != 1 else ''}, confidence {confidence:.2f}"]
    if misses:
        parts.append(f"{misses} unmatched")
    if failures:
        parts.append(f"{failures} check failure{'s' if failures != 1 else ''}")
    parts.append(f"-> {decision.value}")
    return "; ".join(parts)


__all__ = ["OrderValidator"]
