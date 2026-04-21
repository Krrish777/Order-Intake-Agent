"""Unit tests for the 3-tier SKU matcher ladder."""

from __future__ import annotations

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
