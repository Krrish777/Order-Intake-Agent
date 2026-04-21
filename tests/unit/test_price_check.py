"""Unit tests for ``price_check.check_price``. Pure function — no
fixtures needed beyond ``seeded_repo`` to pull a real ProductRecord."""

from __future__ import annotations

import pytest

from backend.models.parsed_document import OrderLineItem
from backend.tools.order_validator import MasterDataRepo
from backend.tools.order_validator.tools.price_check import check_price


@pytest.fixture
async def hcs_product(seeded_repo: MasterDataRepo):
    """The reference product used by most price cases (catalog 0.34)."""
    p = await seeded_repo.get_product("FST-HCS-050-13-200-G5Z")
    assert p is not None
    return p


@pytest.mark.asyncio
async def test_exact_catalog_price_passes(hcs_product) -> None:
    ok, reason = check_price(OrderLineItem(unit_price=0.34), hcs_product)
    assert ok is True
    assert reason == ""


@pytest.mark.asyncio
async def test_within_tolerance_passes(hcs_product) -> None:
    # 0.36 is +5.88% from 0.34, inside default ±10% band.
    ok, _ = check_price(OrderLineItem(unit_price=0.36), hcs_product)
    assert ok is True


@pytest.mark.asyncio
async def test_just_below_band_fails(hcs_product) -> None:
    # 0.30 is -11.76% from 0.34, outside default ±10%.
    ok, reason = check_price(OrderLineItem(unit_price=0.30), hcs_product)
    assert ok is False
    assert "0.30" in reason
    assert "0.34" in reason


@pytest.mark.asyncio
async def test_far_above_band_fails_with_signed_delta(hcs_product) -> None:
    ok, reason = check_price(OrderLineItem(unit_price=0.50), hcs_product)
    assert ok is False
    assert "+" in reason  # signed positive delta


@pytest.mark.asyncio
async def test_missing_quote_is_permissive(hcs_product) -> None:
    ok, reason = check_price(OrderLineItem(unit_price=None), hcs_product)
    assert ok is True
    assert reason == ""


@pytest.mark.asyncio
async def test_zero_quote_fails(hcs_product) -> None:
    ok, reason = check_price(OrderLineItem(unit_price=0.0), hcs_product)
    assert ok is False
    assert "non-positive" in reason


@pytest.mark.asyncio
async def test_negative_quote_fails(hcs_product) -> None:
    ok, reason = check_price(OrderLineItem(unit_price=-1.0), hcs_product)
    assert ok is False


@pytest.mark.asyncio
async def test_custom_tolerance_widens_band(hcs_product) -> None:
    # 0.30 is -11.76% — outside 10% but inside 15%.
    ok, _ = check_price(OrderLineItem(unit_price=0.30), hcs_product, tolerance_pct=15.0)
    assert ok is True
