"""Unit tests for :class:`backend.data.firestore_repo.FirestoreRepo`.

These tests use an in-memory fake that implements the narrow slice of the
Firestore async API the repo actually touches:

* ``collection(name).document(id).get()`` → ``AsyncDocumentSnapshot``-like
* ``collection(name).stream()`` → async iterator of snapshots
* ``get_all(refs)`` → async iterator of snapshots

No network, no emulator — these tests are meant to lock the repo's
behavioural contract (not-found semantics, cache warm-up, rapidfuzz
thresholds, stub return value) and run in milliseconds.

Parity with the real Firestore client is asserted separately in
``tests/integration/test_firestore_repo_emulator.py``, which exercises
the same repo against a running emulator. If both suites pass, the repo
behaves identically against the fake and the real SDK.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncIterator

import pytest

from backend.data.firestore_repo import (
    CUSTOMERS_COLLECTION,
    META_COLLECTION,
    META_DOC_ID,
    PRODUCTS_COLLECTION,
    FirestoreRepo,
)
from backend.models.master_records import (
    CustomerRecord,
    MetaRecord,
    ProductRecord,
)

# ---------------------------------------------------------------------- fake


class _FakeSnapshot:
    def __init__(self, data: dict[str, Any] | None) -> None:
        self._data = data

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self) -> dict[str, Any]:
        assert self._data is not None
        return dict(self._data)


class _FakeDocumentRef:
    def __init__(self, store: dict[str, dict[str, dict]], collection: str, doc_id: str) -> None:
        self._store = store
        self._collection = collection
        self._doc_id = doc_id

    @property
    def id(self) -> str:
        return self._doc_id

    async def get(self) -> _FakeSnapshot:
        return _FakeSnapshot(self._store.get(self._collection, {}).get(self._doc_id))


class _FakeCollection:
    def __init__(self, store: dict[str, dict[str, dict]], collection: str) -> None:
        self._store = store
        self._collection = collection

    def document(self, doc_id: str) -> _FakeDocumentRef:
        return _FakeDocumentRef(self._store, self._collection, doc_id)

    async def stream(self) -> AsyncIterator[_FakeSnapshot]:
        for data in self._store.get(self._collection, {}).values():
            yield _FakeSnapshot(data)


class FakeAsyncClient:
    """Tiny in-memory stand-in for ``google.cloud.firestore.AsyncClient``.

    Seeded via a nested dict keyed by ``{collection: {doc_id: payload}}``.
    The payload is stored as-is; ``_FakeSnapshot.to_dict`` returns a
    shallow copy so callers can't mutate the backing store by accident.
    """

    def __init__(self, seeded: dict[str, dict[str, dict]]) -> None:
        self._store = seeded

    def collection(self, name: str) -> _FakeCollection:
        return _FakeCollection(self._store, name)

    async def get_all(
        self, refs: list[_FakeDocumentRef]
    ) -> AsyncIterator[_FakeSnapshot]:
        for ref in refs:
            yield await ref.get()

    def close(self) -> None:
        pass


# -------------------------------------------------------------------- fixtures


_MASTERS = Path(__file__).resolve().parent.parent.parent / "data" / "masters"


def _load_seed() -> tuple[list[dict], list[dict], dict]:
    products = json.loads((_MASTERS / "products.json").read_text(encoding="utf-8"))
    customers = json.loads((_MASTERS / "customers.json").read_text(encoding="utf-8"))
    meta = {
        "catalog_version": products["catalog_version"],
        "catalog_effective_date": products["effective_date"],
        "currency": products["currency"],
        "master_version": customers["master_version"],
        "master_effective_date": customers["effective_date"],
        "seller_of_record": customers["seller_of_record"],
    }
    return products["products"], customers["customers"], meta


@pytest.fixture
def seeded_repo() -> FirestoreRepo:
    products, customers, meta = _load_seed()
    store = {
        PRODUCTS_COLLECTION: {p["sku"]: p for p in products},
        CUSTOMERS_COLLECTION: {c["customer_id"]: c for c in customers},
        META_COLLECTION: {META_DOC_ID: meta},
    }
    return FirestoreRepo(FakeAsyncClient(store))


@pytest.fixture
def empty_repo() -> FirestoreRepo:
    return FirestoreRepo(FakeAsyncClient({}))


# ---------------------------------------------------------------------- tests


@pytest.mark.asyncio
async def test_get_product_hit_returns_typed_record(seeded_repo: FirestoreRepo) -> None:
    p = await seeded_repo.get_product("FST-HCS-050-13-200-G5Z")
    assert p is not None
    assert isinstance(p, ProductRecord)
    assert p.sku == "FST-HCS-050-13-200-G5Z"
    assert p.unit_price_usd == 0.34
    assert p.uom == "EA"


@pytest.mark.asyncio
async def test_get_product_miss_returns_none(seeded_repo: FirestoreRepo) -> None:
    assert await seeded_repo.get_product("does-not-exist") is None


@pytest.mark.asyncio
async def test_get_products_batch_partial_hit(seeded_repo: FirestoreRepo) -> None:
    skus = ["FST-HCS-050-13-200-G5Z", "ghost-sku", "FST-HCS-038-16-100-G8YZ"]
    out = await seeded_repo.get_products(skus)
    assert "FST-HCS-050-13-200-G5Z" in out
    assert "FST-HCS-038-16-100-G8YZ" in out
    assert "ghost-sku" not in out  # misses are omitted, not None


@pytest.mark.asyncio
async def test_get_products_empty_input_returns_empty(seeded_repo: FirestoreRepo) -> None:
    assert await seeded_repo.get_products([]) == {}


@pytest.mark.asyncio
async def test_get_products_dedupes_input(seeded_repo: FirestoreRepo) -> None:
    sku = "FST-HCS-050-13-200-G5Z"
    out = await seeded_repo.get_products([sku, sku, sku])
    assert list(out.keys()) == [sku]


@pytest.mark.asyncio
async def test_list_all_products_caches(seeded_repo: FirestoreRepo) -> None:
    first = await seeded_repo.list_all_products()
    second = await seeded_repo.list_all_products()
    assert first is second  # same list object → cache returned
    assert len(first) >= 1
    assert all(isinstance(p, ProductRecord) for p in first)


@pytest.mark.asyncio
async def test_get_customer_hit(seeded_repo: FirestoreRepo) -> None:
    c = await seeded_repo.get_customer("CUST-00042")
    assert c is not None
    assert isinstance(c, CustomerRecord)
    assert c.name.startswith("Patterson")
    # sku_aliases is present on the record (may be empty dict)
    assert isinstance(c.sku_aliases, dict)


@pytest.mark.asyncio
async def test_get_customer_miss(seeded_repo: FirestoreRepo) -> None:
    assert await seeded_repo.get_customer("CUST-99999") is None


@pytest.mark.asyncio
async def test_find_customer_by_name_fuzzy_match(seeded_repo: FirestoreRepo) -> None:
    c = await seeded_repo.find_customer_by_name("Patterson Industrial")
    assert c is not None
    assert c.customer_id == "CUST-00042"


@pytest.mark.asyncio
async def test_find_customer_by_name_below_threshold(seeded_repo: FirestoreRepo) -> None:
    # Deliberately unlike any seeded customer — token_set_ratio should be low.
    assert await seeded_repo.find_customer_by_name("Qwertzuiop GmbH", threshold=90) is None


@pytest.mark.asyncio
async def test_find_customer_by_name_empty_string(seeded_repo: FirestoreRepo) -> None:
    assert await seeded_repo.find_customer_by_name("") is None
    assert await seeded_repo.find_customer_by_name("   ") is None


@pytest.mark.asyncio
async def test_get_meta_hit(seeded_repo: FirestoreRepo) -> None:
    m = await seeded_repo.get_meta()
    assert isinstance(m, MetaRecord)
    assert m.catalog_version.startswith("2026-Q2")


@pytest.mark.asyncio
async def test_get_meta_missing_raises(empty_repo: FirestoreRepo) -> None:
    with pytest.raises(LookupError):
        await empty_repo.get_meta()


@pytest.mark.asyncio
async def test_find_product_by_embedding_stub_returns_empty(seeded_repo: FirestoreRepo) -> None:
    assert await seeded_repo.find_product_by_embedding("hex cap screw zinc grade 5") == []
    assert await seeded_repo.find_product_by_embedding("anything", k=10) == []


@pytest.mark.asyncio
async def test_aclose_is_idempotent(seeded_repo: FirestoreRepo) -> None:
    await seeded_repo.aclose()
    await seeded_repo.aclose()  # second call must not raise
