"""Resolve the parsed customer name against the customer master.

Wraps :meth:`MasterDataRepo.find_customer_by_name` in the validator's
tool interface. The returned :class:`CustomerRecord` carries the
``sku_aliases`` map that :func:`match_sku` consults for tier-1 alias
translation — which is why this runs before the matcher in the
validator orchestrator.

Kept as its own tool rather than a helper inside ``validator.py`` so
future growth (multi-address disambiguation, contract-pricing lookup,
segment-specific routing) has a natural home without further refactor.
"""

from __future__ import annotations

from typing import Optional

from backend.models.master_records import CustomerRecord
from backend.models.parsed_document import ExtractedOrder
from backend.tools.order_validator.tools.master_data_repo import MasterDataRepo
from backend.utils.logging import get_logger

_log = get_logger(__name__)


async def resolve_customer(
    order: ExtractedOrder,
    repo: MasterDataRepo,
) -> Optional[CustomerRecord]:
    """Match the order's free-text ``customer_name`` to a customer doc.

    Returns ``None`` when ``customer_name`` is missing or falls below
    the repo's fuzzy threshold — the validator treats a missing match
    as a clarify/escalate signal rather than a fatal error.
    """
    if not order.customer_name:
        _log.debug("customer_name_missing")
        return None

    customer = await repo.find_customer_by_name(order.customer_name)
    if customer is None:
        _log.debug("customer_unresolved", query=order.customer_name)
    else:
        _log.debug(
            "customer_resolved",
            query=order.customer_name,
            customer_id=customer.customer_id,
        )
    return customer


__all__ = ["resolve_customer"]
