"""Shared fixtures for the validator's unit test suite.

The in-memory :class:`FakeAsyncClient` is the single most reused testing
primitive in the validator — every tool that consumes
:class:`MasterDataRepo` builds on top of it. Promoted here so each tool's
test file can simply ``use`` the :func:`seeded_repo` fixture without
re-implementing the fake or loading seed data.

The fake implements only the narrow slice of the Firestore async API the
repo touches: ``collection().document().get()``, ``collection().stream()``,
and ``get_all(refs)``. Integration parity with the real async SDK is
asserted in ``tests/integration/test_master_data_repo_emulator.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncIterator

import pytest

from backend.tools.order_validator import MasterDataRepo
from backend.tools.order_validator.tools.master_data_repo import (
    CUSTOMERS_COLLECTION,
    META_COLLECTION,
    META_DOC_ID,
    PRODUCTS_COLLECTION,
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


# -------------------------------------------------------------------- seed


_MASTERS = Path(__file__).resolve().parent.parent.parent / "data" / "masters"


def load_seed() -> tuple[list[dict], list[dict], dict]:
    """Return the product list, customer list, and meta doc as parsed
    Python objects — the same data ``scripts/load_master_data.py``
    writes into Firestore."""
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


def build_store() -> dict[str, dict[str, dict]]:
    """Build the nested ``{collection: {doc_id: payload}}`` seeded from
    ``data/masters/*.json``."""
    products, customers, meta = load_seed()
    return {
        PRODUCTS_COLLECTION: {p["sku"]: p for p in products},
        CUSTOMERS_COLLECTION: {c["customer_id"]: c for c in customers},
        META_COLLECTION: {META_DOC_ID: meta},
    }


# -------------------------------------------------------------------- fixtures


@pytest.fixture
def seeded_repo() -> MasterDataRepo:
    """Validator-ready repo with all 35 products + 10 customers + meta."""
    return MasterDataRepo(FakeAsyncClient(build_store()))


@pytest.fixture
def empty_repo() -> MasterDataRepo:
    """Repo wired to an empty store — used for not-found semantics."""
    return MasterDataRepo(FakeAsyncClient({}))
