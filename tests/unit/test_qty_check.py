"""Unit tests for ``qty_check.check_qty``."""

from __future__ import annotations

import pytest

from backend.models.parsed_document import OrderLineItem
from backend.tools.order_validator import MasterDataRepo
from backend.tools.order_validator.tools.qty_check import check_qty


@pytest.fixture
async def hcs_product(seeded_repo: MasterDataRepo):
    """FST-HCS-050-13-200-G5Z: min_order_qty=25, uom=EA, alt_uoms=[BX, CA]."""
    p = await seeded_repo.get_product("FST-HCS-050-13-200-G5Z")
    assert p is not None
    return p


@pytest.mark.asyncio
async def test_above_min_passes(hcs_product) -> None:
    ok, _ = check_qty(OrderLineItem(quantity=50, unit_of_measure="EA"), hcs_product)
    assert ok is True


@pytest.mark.asyncio
async def test_no_uom_defaults_to_base(hcs_product) -> None:
    ok, _ = check_qty(OrderLineItem(quantity=100), hcs_product)
    assert ok is True


@pytest.mark.asyncio
async def test_alt_uom_skips_min_order(hcs_product) -> None:
    """3 BX (3 boxes of 50 = 150 EA) should pass even though 3 < min 25;
    the floor applies to base UoM only."""
    ok, _ = check_qty(OrderLineItem(quantity=3, unit_of_measure="BX"), hcs_product)
    assert ok is True


@pytest.mark.asyncio
async def test_missing_qty_fails(hcs_product) -> None:
    ok, reason = check_qty(OrderLineItem(quantity=None), hcs_product)
    assert ok is False
    assert "missing" in reason


@pytest.mark.asyncio
async def test_zero_qty_fails(hcs_product) -> None:
    ok, reason = check_qty(OrderLineItem(quantity=0), hcs_product)
    assert ok is False
    assert "positive" in reason


@pytest.mark.asyncio
async def test_negative_qty_fails(hcs_product) -> None:
    ok, _ = check_qty(OrderLineItem(quantity=-5), hcs_product)
    assert ok is False


@pytest.mark.asyncio
async def test_below_min_in_base_uom_fails(hcs_product) -> None:
    ok, reason = check_qty(OrderLineItem(quantity=10, unit_of_measure="EA"), hcs_product)
    assert ok is False
    assert "min_order_qty" in reason


@pytest.mark.asyncio
async def test_below_min_default_uom_fails(hcs_product) -> None:
    ok, _ = check_qty(OrderLineItem(quantity=10), hcs_product)
    assert ok is False


@pytest.mark.asyncio
async def test_disallowed_uom_fails(hcs_product) -> None:
    ok, reason = check_qty(OrderLineItem(quantity=50, unit_of_measure="PALLET"), hcs_product)
    assert ok is False
    assert "PALLET" in reason.upper()


@pytest.mark.asyncio
async def test_uom_case_insensitive(hcs_product) -> None:
    ok, _ = check_qty(OrderLineItem(quantity=50, unit_of_measure="ea"), hcs_product)
    assert ok is True
