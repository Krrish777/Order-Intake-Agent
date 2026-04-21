"""Integration tests for :class:`MasterDataRepo` against the Firestore
emulator.

These tests prove that the repo behaves identically against the real
async SDK as it does against the in-memory fake in
``tests/unit/test_master_data_repo.py``. They run only when
``FIRESTORE_EMULATOR_HOST`` is set (i.e. a local emulator is up) and are
marked ``firestore_emulator`` so normal unit runs skip them cleanly.

Before running::

    # Terminal 1
    firebase emulators:start --only firestore
    # Terminal 2
    export FIRESTORE_EMULATOR_HOST=localhost:8080     # or ``set`` on Windows
    uv run python scripts/load_master_data.py
    uv run pytest -m firestore_emulator -v

The tests **do not wipe** the emulator — they read the same seeded data
``load_master_data.py`` wrote, which is the same corpus the validator
will see at runtime.
"""

from __future__ import annotations

import os

import pytest

from backend.models.master_records import (
    CustomerRecord,
    MetaRecord,
    ProductRecord,
)
from backend.tools.order_validator import MasterDataRepo, get_async_client

pytestmark = [
    pytest.mark.firestore_emulator,
    pytest.mark.skipif(
        not os.environ.get("FIRESTORE_EMULATOR_HOST"),
        reason="FIRESTORE_EMULATOR_HOST not set; emulator tests skipped",
    ),
]


@pytest.fixture
async def repo() -> MasterDataRepo:
    r = MasterDataRepo(get_async_client())
    try:
        yield r
    finally:
        await r.aclose()


# ---------------------------------------------------------------- products


async def test_get_product_hit(repo: MasterDataRepo) -> None:
    p = await repo.get_product("FST-HCS-050-13-200-G5Z")
    assert p is not None
    assert isinstance(p, ProductRecord)
    assert p.sku == "FST-HCS-050-13-200-G5Z"
    assert p.unit_price_usd == 0.34


async def test_get_product_miss(repo: MasterDataRepo) -> None:
    assert await repo.get_product("NOPE-DOES-NOT-EXIST") is None


async def test_get_products_batch(repo: MasterDataRepo) -> None:
    skus = ["FST-HCS-050-13-200-G5Z", "NOPE-MISSING", "FST-HCS-038-16-100-G8YZ"]
    out = await repo.get_products(skus)
    assert "FST-HCS-050-13-200-G5Z" in out
    assert "FST-HCS-038-16-100-G8YZ" in out
    assert "NOPE-MISSING" not in out
    assert all(isinstance(v, ProductRecord) for v in out.values())


async def test_list_all_products_has_catalog(repo: MasterDataRepo) -> None:
    products = await repo.list_all_products()
    assert len(products) >= 1
    assert all(isinstance(p, ProductRecord) for p in products)
    # Cache warm-up — second call returns the exact same list object.
    again = await repo.list_all_products()
    assert again is products


# ---------------------------------------------------------------- customers


async def test_get_customer_hit(repo: MasterDataRepo) -> None:
    c = await repo.get_customer("CUST-00042")
    assert c is not None
    assert isinstance(c, CustomerRecord)
    assert c.customer_id == "CUST-00042"
    assert c.name.startswith("Patterson")


async def test_get_customer_miss(repo: MasterDataRepo) -> None:
    assert await repo.get_customer("CUST-99999") is None


async def test_find_customer_by_name(repo: MasterDataRepo) -> None:
    c = await repo.find_customer_by_name("Patterson Industrial")
    assert c is not None
    assert c.customer_id == "CUST-00042"


async def test_find_customer_by_name_below_threshold(repo: MasterDataRepo) -> None:
    assert await repo.find_customer_by_name("Qwertzuiop GmbH", threshold=90) is None


# ---------------------------------------------------------------- meta + stub


async def test_get_meta(repo: MasterDataRepo) -> None:
    m = await repo.get_meta()
    assert isinstance(m, MetaRecord)
    assert m.catalog_version.startswith("2026-Q2")


async def test_find_product_by_embedding_returns_empty_stub(repo: MasterDataRepo) -> None:
    assert await repo.find_product_by_embedding("hex cap screw zinc grade 5") == []
