"""Unit tests for ``customer_resolver.resolve_customer``."""

from __future__ import annotations

import pytest

from backend.models.parsed_document import ExtractedOrder
from backend.tools.order_validator import MasterDataRepo
from backend.tools.order_validator.tools.customer_resolver import resolve_customer


@pytest.mark.asyncio
async def test_resolves_by_exact_legal_name(seeded_repo: MasterDataRepo) -> None:
    order = ExtractedOrder(customer_name="Patterson Industrial Supply Co.")
    c = await resolve_customer(order, seeded_repo)
    assert c is not None
    assert c.customer_id == "CUST-00042"


@pytest.mark.asyncio
async def test_resolves_by_partial_name(seeded_repo: MasterDataRepo) -> None:
    order = ExtractedOrder(customer_name="Patterson Industrial")
    c = await resolve_customer(order, seeded_repo)
    assert c is not None
    assert c.customer_id == "CUST-00042"


@pytest.mark.asyncio
async def test_resolves_by_dba(seeded_repo: MasterDataRepo) -> None:
    """GLFP is Great Lakes Fluid Power Group's dba."""
    order = ExtractedOrder(customer_name="GLFP")
    c = await resolve_customer(order, seeded_repo)
    assert c is not None
    assert c.customer_id == "CUST-00078"


@pytest.mark.asyncio
async def test_unresolved_below_threshold(seeded_repo: MasterDataRepo) -> None:
    order = ExtractedOrder(customer_name="Random Nonsense Inc.")
    assert await resolve_customer(order, seeded_repo) is None


@pytest.mark.asyncio
async def test_missing_customer_name_returns_none(seeded_repo: MasterDataRepo) -> None:
    assert await resolve_customer(ExtractedOrder(), seeded_repo) is None


@pytest.mark.asyncio
async def test_empty_customer_name_returns_none(seeded_repo: MasterDataRepo) -> None:
    assert await resolve_customer(ExtractedOrder(customer_name=""), seeded_repo) is None


@pytest.mark.asyncio
async def test_returns_full_record_with_sku_aliases(seeded_repo: MasterDataRepo) -> None:
    """The matcher downstream needs sku_aliases to translate
    customer-specific part numbers; confirm the record carries them."""
    order = ExtractedOrder(customer_name="Patterson Industrial")
    c = await resolve_customer(order, seeded_repo)
    assert c is not None
    assert "887712" in c.sku_aliases
    assert c.sku_aliases["887712"] == "FST-HCS-050-13-200-G5Z"
