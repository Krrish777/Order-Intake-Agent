"""Unit tests for the 3-tier SKU matcher ladder."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.models.parsed_document import OrderLineItem
from backend.tools.order_validator import MasterDataRepo
from backend.tools.order_validator.tools.sku_matcher import match_sku


@pytest.mark.asyncio
async def test_tier1_exact_doc_id(seeded_repo: MasterDataRepo) -> None:
    line = OrderLineItem(sku="FST-HCS-050-13-200-G5Z")
    product, tier, conf = await match_sku(line, seeded_repo, customer=None)
    assert product is not None
    assert product.sku == "FST-HCS-050-13-200-G5Z"
    assert tier == "exact"
    assert conf == 1.0


@pytest.mark.asyncio
async def test_tier1_via_customer_alias(seeded_repo: MasterDataRepo) -> None:
    """Patterson's alias 887712 resolves to the canonical sku."""
    patterson = await seeded_repo.get_customer("CUST-00042")
    assert patterson is not None
    line = OrderLineItem(sku="887712")
    product, tier, conf = await match_sku(line, seeded_repo, customer=patterson)
    assert product is not None
    assert product.sku == "FST-HCS-050-13-200-G5Z"
    assert tier == "exact"
    assert conf == 1.0


@pytest.mark.asyncio
async def test_tier1_alias_ignored_without_customer(seeded_repo: MasterDataRepo) -> None:
    """Without a customer context the alias string is looked up as-is and misses."""
    line = OrderLineItem(sku="887712")
    product, tier, conf = await match_sku(line, seeded_repo, customer=None)
    # 887712 is not a catalog doc id; no alias map without customer; full miss.
    assert product is None
    assert tier == "none"
    assert conf == 0.0


@pytest.mark.asyncio
async def test_tier2_fuzzy_verbatim_short_description(seeded_repo: MasterDataRepo) -> None:
    line = OrderLineItem(description="HCS 1/2-13 x 2 GR5 ZP")
    product, tier, conf = await match_sku(line, seeded_repo, customer=None)
    assert product is not None
    assert product.sku == "FST-HCS-050-13-200-G5Z"
    assert tier == "fuzzy"
    assert conf >= 0.85


@pytest.mark.asyncio
async def test_tier2_fuzzy_paraphrase_below_threshold(seeded_repo: MasterDataRepo) -> None:
    """Paraphrased free text is deliberately out of tier-2's reach;
    it's a tier-3 (embedding) concern."""
    line = OrderLineItem(description="hex cap screw 1/2-13 x 2 grade 5 zinc plated")
    product, tier, conf = await match_sku(line, seeded_repo, customer=None)
    assert product is None
    assert tier == "none"
    assert conf == 0.0


@pytest.mark.asyncio
async def test_tier3_embedding_stub_returns_no_match(seeded_repo: MasterDataRepo) -> None:
    """The embedding repo method is stubbed to []; ladder falls through."""
    line = OrderLineItem(description="some description")
    product, tier, conf = await match_sku(line, seeded_repo, customer=None)
    assert product is None
    assert tier == "none"
    assert conf == 0.0


@pytest.mark.asyncio
async def test_no_sku_no_description_returns_miss(seeded_repo: MasterDataRepo) -> None:
    line = OrderLineItem()
    product, tier, conf = await match_sku(line, seeded_repo)
    assert product is None
    assert tier == "none"
    assert conf == 0.0


@pytest.mark.asyncio
async def test_bogus_sku_falls_through_to_miss(seeded_repo: MasterDataRepo) -> None:
    line = OrderLineItem(sku="totally-fake-9999")
    product, tier, conf = await match_sku(line, seeded_repo)
    assert product is None
    assert tier == "none"


@pytest.mark.asyncio
async def test_bogus_sku_but_valid_fuzzy_description(seeded_repo: MasterDataRepo) -> None:
    """If tier 1 misses, the matcher still tries tier 2 from the description."""
    line = OrderLineItem(sku="fake-xyz", description="SHCS 1/4-20 x 1-1/4 ALY BO")
    product, tier, conf = await match_sku(line, seeded_repo)
    assert product is not None
    assert product.sku == "FST-SHC-025-20-125-AB"
    assert tier == "fuzzy"


# ---------- Track E: tier-3 real match ----------


@pytest.mark.asyncio
async def test_match_sku_tier_3_hit_returns_embedding_tier_with_score():
    """When tier 1 (exact) and tier 2 (fuzzy) miss, but tier 3 returns
    an EmbeddingMatch with score >= EMBEDDING_THRESHOLD, match_sku
    returns (product, 'embedding', score)."""
    from backend.models.master_records import EmbeddingMatch, ProductRecord
    from backend.tools.order_validator.tools.sku_matcher import (
        EMBEDDING_THRESHOLD,
    )

    matched_product = ProductRecord(
        sku="WID-RED-100",
        short_description="Widget Red 100ct",
        long_description="Red widgets, pack of 100.",
        category="widgets",
        subcategory="colored",
        uom="EA",
        pack_uom="BX",
        pack_size=100,
        alt_uoms=["BX"],
        unit_price_usd=4.20,
        standards=[],
        lead_time_days=1,
        min_order_qty=1,
        country_of_origin="US",
    )

    repo = MagicMock()
    repo.list_all_products = AsyncMock(return_value=[])

    async def fake_find(query: str, k: int = 5):
        return [EmbeddingMatch(
            sku="WID-RED-100",
            score=0.85,
            source="firestore_findnearest",
        )]
    repo.find_product_by_embedding = fake_find

    # tier 1 doesn't call get_product when line.sku is None;
    # tier 3 calls it once to hydrate the top match.
    repo.get_product = AsyncMock(return_value=matched_product)

    line = OrderLineItem(
        sku=None,
        description="widget red, case of 100",
        quantity=5,
        unit_of_measure="EA",
    )

    product, tier, score = await match_sku(line, repo, customer=None)

    assert product == matched_product
    assert tier == "embedding"
    assert score == pytest.approx(0.85)
    assert score >= EMBEDDING_THRESHOLD


@pytest.mark.asyncio
async def test_match_sku_tier_3_below_threshold_misses():
    """Score < EMBEDDING_THRESHOLD falls through to the overall miss
    branch, not an 'embedding' hit."""
    from backend.models.master_records import EmbeddingMatch

    repo = MagicMock()
    repo.get_product = AsyncMock(return_value=None)
    repo.list_all_products = AsyncMock(return_value=[])

    async def fake_find(query: str, k: int = 5):
        return [EmbeddingMatch(
            sku="SKU-MAYBE",
            score=0.65,
            source="firestore_findnearest",
        )]
    repo.find_product_by_embedding = fake_find

    line = OrderLineItem(
        sku=None,
        description="some unclear description",
        quantity=1,
        unit_of_measure="EA",
    )

    product, tier, score = await match_sku(line, repo, customer=None)

    assert product is None
    assert tier == "none"
    assert score == 0.0
