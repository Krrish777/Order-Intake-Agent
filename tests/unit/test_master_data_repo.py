"""Unit tests for :class:`MasterDataRepo`.

Uses the ``seeded_repo`` / ``empty_repo`` fixtures from
``tests/unit/conftest.py``; the in-memory ``FakeAsyncClient`` is defined
there too. No network, no emulator — these tests lock the repo's
behavioural contract (not-found semantics, cache warm-up, rapidfuzz
thresholds, stub return value) and run in milliseconds.

Parity with the real Firestore client is asserted separately in
``tests/integration/test_master_data_repo_emulator.py``.
"""

from __future__ import annotations

import pytest

from backend.models.master_records import (
    CustomerRecord,
    MetaRecord,
    ProductRecord,
)
from backend.tools.order_validator import MasterDataRepo


@pytest.mark.asyncio
async def test_get_product_hit_returns_typed_record(seeded_repo: MasterDataRepo) -> None:
    p = await seeded_repo.get_product("FST-HCS-050-13-200-G5Z")
    assert p is not None
    assert isinstance(p, ProductRecord)
    assert p.sku == "FST-HCS-050-13-200-G5Z"
    assert p.unit_price_usd == 0.34
    assert p.uom == "EA"


@pytest.mark.asyncio
async def test_get_product_miss_returns_none(seeded_repo: MasterDataRepo) -> None:
    assert await seeded_repo.get_product("does-not-exist") is None


@pytest.mark.asyncio
async def test_get_products_batch_partial_hit(seeded_repo: MasterDataRepo) -> None:
    skus = ["FST-HCS-050-13-200-G5Z", "ghost-sku", "FST-HCS-038-16-100-G8YZ"]
    out = await seeded_repo.get_products(skus)
    assert "FST-HCS-050-13-200-G5Z" in out
    assert "FST-HCS-038-16-100-G8YZ" in out
    assert "ghost-sku" not in out


@pytest.mark.asyncio
async def test_get_products_empty_input_returns_empty(seeded_repo: MasterDataRepo) -> None:
    assert await seeded_repo.get_products([]) == {}


@pytest.mark.asyncio
async def test_get_products_dedupes_input(seeded_repo: MasterDataRepo) -> None:
    sku = "FST-HCS-050-13-200-G5Z"
    out = await seeded_repo.get_products([sku, sku, sku])
    assert list(out.keys()) == [sku]


@pytest.mark.asyncio
async def test_list_all_products_caches(seeded_repo: MasterDataRepo) -> None:
    first = await seeded_repo.list_all_products()
    second = await seeded_repo.list_all_products()
    assert first is second
    assert len(first) >= 1
    assert all(isinstance(p, ProductRecord) for p in first)


@pytest.mark.asyncio
async def test_get_customer_hit(seeded_repo: MasterDataRepo) -> None:
    c = await seeded_repo.get_customer("CUST-00042")
    assert c is not None
    assert isinstance(c, CustomerRecord)
    assert c.name.startswith("Patterson")
    assert isinstance(c.sku_aliases, dict)


@pytest.mark.asyncio
async def test_get_customer_miss(seeded_repo: MasterDataRepo) -> None:
    assert await seeded_repo.get_customer("CUST-99999") is None


@pytest.mark.asyncio
async def test_find_customer_by_name_fuzzy_match(seeded_repo: MasterDataRepo) -> None:
    c = await seeded_repo.find_customer_by_name("Patterson Industrial")
    assert c is not None
    assert c.customer_id == "CUST-00042"


@pytest.mark.asyncio
async def test_find_customer_by_name_below_threshold(seeded_repo: MasterDataRepo) -> None:
    assert await seeded_repo.find_customer_by_name("Qwertzuiop GmbH", threshold=90) is None


@pytest.mark.asyncio
async def test_find_customer_by_name_empty_string(seeded_repo: MasterDataRepo) -> None:
    assert await seeded_repo.find_customer_by_name("") is None
    assert await seeded_repo.find_customer_by_name("   ") is None


@pytest.mark.asyncio
async def test_get_meta_hit(seeded_repo: MasterDataRepo) -> None:
    m = await seeded_repo.get_meta()
    assert isinstance(m, MetaRecord)
    assert m.catalog_version.startswith("2026-Q2")


@pytest.mark.asyncio
async def test_get_meta_missing_raises(empty_repo: MasterDataRepo) -> None:
    with pytest.raises(LookupError):
        await empty_repo.get_meta()


@pytest.mark.asyncio
async def test_find_product_by_embedding_stub_returns_empty(seeded_repo: MasterDataRepo) -> None:
    assert await seeded_repo.find_product_by_embedding("hex cap screw zinc grade 5") == []
    assert await seeded_repo.find_product_by_embedding("anything", k=10) == []


@pytest.mark.asyncio
async def test_aclose_is_idempotent(seeded_repo: MasterDataRepo) -> None:
    await seeded_repo.aclose()
    await seeded_repo.aclose()
