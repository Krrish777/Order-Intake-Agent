"""Three-tier SKU matcher.

Given one parsed :class:`OrderLineItem` and the customer it belongs to,
find the canonical :class:`ProductRecord` from the catalog. Tiers are
tried in order and the first hit wins:

1. **Exact** — direct sku doc lookup. If the customer has a
   ``sku_aliases`` entry for the line's sku string, the alias is
   translated to the canonical sku first. ``repo.get_product`` is the
   only Firestore call this tier makes; confidence is always ``1.0``.

2. **Fuzzy** — ``rapidfuzz.process.extractOne`` over the cached product
   list, scoring ``token_set_ratio`` against the line's description
   matched to ``short_description`` only. Hits at or above
   ``FUZZY_THRESHOLD`` (default 85, mapped to 0.85 confidence) win.

   ``short_description`` is a compact code-like form ("HCS 1/2-13 x 2
   GR5 ZP") that matches well when a customer types the item code but
   not the sku doc id. Matching against the long prose form floods the
   scorer with low-signal tokens ("Plain Washer Face", "Grade 5", ...)
   that every similar product shares — measured empirically to drop
   discrimination from ~85 correct-hits to ~48 correct-hits. Paraphrased
   queries ("hex cap screw zinc 1/2 inch") legitimately need tier 3
   (embeddings); tier 2 is deliberately narrow.

3. **Embedding** — currently a stub call to
   :meth:`MasterDataRepo.find_product_by_embedding`, which returns
   ``[]`` until vector indexing ships. The ladder still calls it so the
   shape is right when the real implementation lands.

A complete miss returns ``(None, "none", 0.0)``. The validator
orchestrator turns that into a routing penalty.
"""

from __future__ import annotations

from typing import Optional

from rapidfuzz import fuzz, process

from backend.models.master_records import CustomerRecord, ProductRecord
from backend.models.parsed_document import OrderLineItem
from backend.models.validation_result import MatchTier
from backend.tools.order_validator.tools.master_data_repo import MasterDataRepo
from backend.utils.logging import get_logger

_log = get_logger(__name__)

FUZZY_THRESHOLD: int = 85
"""rapidfuzz token_set_ratio cutoff (0-100). Below this, tier 2 misses."""

EMBEDDING_THRESHOLD: float = 0.70
"""Cosine similarity floor for tier 3 hits when the real implementation lands."""


async def match_sku(
    line: OrderLineItem,
    repo: MasterDataRepo,
    customer: Optional[CustomerRecord] = None,
) -> tuple[Optional[ProductRecord], MatchTier, float]:
    """Walk the matcher ladder. Returns ``(product, tier, confidence)``.

    The caller compares ``line.sku`` to ``product.sku`` to detect alias
    usage (they differ when an alias was translated); no extra return
    value is needed for that.
    """

    # ----------------------------------------------------- tier 1: exact
    if line.sku:
        canonical = (customer.sku_aliases.get(line.sku) if customer else None) or line.sku
        product = await repo.get_product(canonical)
        if product is not None:
            _log.debug(
                "sku_matched_exact",
                line_sku=line.sku,
                canonical=canonical,
                via_alias=canonical != line.sku,
            )
            return product, "exact", 1.0

    # ----------------------------------------------------- tier 2: fuzzy
    if line.description and line.description.strip():
        catalog = await repo.list_all_products()
        if catalog:
            haystack = [p.short_description for p in catalog]
            best = process.extractOne(
                line.description,
                haystack,
                scorer=fuzz.token_set_ratio,
            )
            if best is not None:
                _label, score, idx = best
                if score >= FUZZY_THRESHOLD:
                    _log.debug(
                        "sku_matched_fuzzy",
                        line_description=line.description,
                        matched_sku=catalog[idx].sku,
                        score=score,
                    )
                    return catalog[idx], "fuzzy", score / 100.0

    # ------------------------------------------------- tier 3: embedding
    if line.description and line.description.strip():
        candidates = await repo.find_product_by_embedding(line.description)
        if candidates:
            top = candidates[0]
            if top.score >= EMBEDDING_THRESHOLD:
                product = await repo.get_product(top.sku)
                if product is not None:
                    _log.debug(
                        "sku_matched_embedding",
                        line_description=line.description,
                        matched_sku=top.sku,
                        score=top.score,
                    )
                    return product, "embedding", top.score

    # ---------------------------------------------------------- miss
    _log.debug(
        "sku_no_match",
        line_sku=line.sku,
        line_description=line.description,
    )
    return None, "none", 0.0


__all__ = ["match_sku", "FUZZY_THRESHOLD", "EMBEDDING_THRESHOLD"]
