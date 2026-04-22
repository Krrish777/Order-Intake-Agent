"""Shared fixtures for the unit test suite.

The in-memory :class:`FakeAsyncClient` is the reused testing primitive for
any code that touches :class:`google.cloud.firestore.AsyncClient` — both
the validator tools (Track V) and the persistence stores
(``backend.persistence.*``) build on it.

The fake implements the narrow slice of the Firestore async API the
code touches:

* Reads — ``collection().document().get()``, ``collection().stream()``,
  ``get_all(refs)`` (used by :class:`MasterDataRepo`).
* Writes — ``document().create()`` with ``AlreadyExists`` semantics,
  ``document().set()``, ``document().update()`` (used by the stores).
* Queries — ``collection().where(filter=FieldFilter(...)).order_by().limit()``
  with ``.stream()`` / ``.get()`` (used by
  :meth:`ExceptionStore.find_pending_clarify`).
* Server timestamps — ``SERVER_TIMESTAMP`` sentinels in write payloads
  are resolved to a callable-controlled clock (injectable for
  deterministic tests).

Integration parity with the real async SDK is asserted in the
``tests/integration/*`` emulator-backed tests.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional

import pytest
from google.api_core.exceptions import AlreadyExists, NotFound
from google.cloud.firestore_v1 import SERVER_TIMESTAMP
from google.cloud.firestore_v1.base_query import FieldFilter

from backend.tools.order_validator import MasterDataRepo
from backend.tools.order_validator.tools.master_data_repo import (
    CUSTOMERS_COLLECTION,
    META_COLLECTION,
    META_DOC_ID,
    PRODUCTS_COLLECTION,
)


# ---------------------------------------------------------------- helpers


def _resolve_server_timestamps(data: Any, now: datetime) -> Any:
    """Recursively replace ``SERVER_TIMESTAMP`` sentinels in a write payload.

    Mirrors the real Firestore server-side substitution. Walks dicts and
    lists; leaves scalars untouched.
    """
    if data is SERVER_TIMESTAMP:
        return now
    if isinstance(data, dict):
        return {k: _resolve_server_timestamps(v, now) for k, v in data.items()}
    if isinstance(data, list):
        return [_resolve_server_timestamps(v, now) for v in data]
    return data


# ----------------------------------------------------------------- fake


class _FakeSnapshot:
    def __init__(
        self, data: dict[str, Any] | None, *, reference: "Optional[_FakeDocumentRef]" = None
    ) -> None:
        self._data = data
        self._reference = reference

    @property
    def exists(self) -> bool:
        return self._data is not None

    @property
    def reference(self) -> "_FakeDocumentRef":
        assert self._reference is not None
        return self._reference

    @property
    def id(self) -> str:
        assert self._reference is not None
        return self._reference.id

    def to_dict(self) -> dict[str, Any]:
        assert self._data is not None
        return dict(self._data)


class _FakeDocumentRef:
    def __init__(
        self,
        store: dict[str, dict[str, dict]],
        collection: str,
        doc_id: str,
        clock: Callable[[], datetime],
    ) -> None:
        self._store = store
        self._collection = collection
        self._doc_id = doc_id
        self._clock = clock

    @property
    def id(self) -> str:
        return self._doc_id

    @property
    def path(self) -> str:
        return f"{self._collection}/{self._doc_id}"

    async def get(self) -> _FakeSnapshot:
        data = self._store.get(self._collection, {}).get(self._doc_id)
        return _FakeSnapshot(data, reference=self)

    async def create(self, data: dict[str, Any]) -> None:
        """Firestore ``create`` semantics: raise :class:`AlreadyExists`
        when the document already exists, else write."""
        bucket = self._store.setdefault(self._collection, {})
        if self._doc_id in bucket:
            raise AlreadyExists(self.path)
        bucket[self._doc_id] = _resolve_server_timestamps(data, self._clock())

    async def set(self, data: dict[str, Any]) -> None:
        """Firestore ``set`` semantics: unconditional overwrite."""
        bucket = self._store.setdefault(self._collection, {})
        bucket[self._doc_id] = _resolve_server_timestamps(data, self._clock())

    async def update(self, data: dict[str, Any]) -> None:
        """Firestore ``update`` semantics: merge fields into an existing
        doc, raising :class:`NotFound` if the doc is absent."""
        bucket = self._store.setdefault(self._collection, {})
        if self._doc_id not in bucket:
            raise NotFound(self.path)
        existing = dict(bucket[self._doc_id])
        existing.update(_resolve_server_timestamps(data, self._clock()))
        bucket[self._doc_id] = existing


class _FakeQuery:
    """Captures ``where`` / ``order_by`` / ``limit`` clauses and applies
    them lazily in :meth:`stream` and :meth:`get`. Mirrors the chainable
    shape of the real ``AsyncQuery``; each chain call returns a new
    instance so the original collection stays usable."""

    def __init__(
        self,
        store: dict[str, dict[str, dict]],
        collection: str,
        clock: Callable[[], datetime],
        *,
        filters: list[tuple[str, str, Any]] | None = None,
        order_by: list[tuple[str, str]] | None = None,
        limit_: int | None = None,
    ) -> None:
        self._store = store
        self._collection = collection
        self._clock = clock
        self._filters = filters or []
        self._order_by = order_by or []
        self._limit = limit_

    def where(self, *, filter: FieldFilter) -> "_FakeQuery":  # noqa: A002 - mirrors SDK
        # FieldFilter exposes ``field_path``, ``op_string``, ``value`` via attrs.
        field = filter.field_path
        op = filter.op_string
        value = filter.value
        return _FakeQuery(
            self._store,
            self._collection,
            self._clock,
            filters=self._filters + [(field, op, value)],
            order_by=self._order_by,
            limit_=self._limit,
        )

    def order_by(self, field: str, direction: str = "ASCENDING") -> "_FakeQuery":
        return _FakeQuery(
            self._store,
            self._collection,
            self._clock,
            filters=self._filters,
            order_by=self._order_by + [(field, direction)],
            limit_=self._limit,
        )

    def limit(self, n: int) -> "_FakeQuery":
        return _FakeQuery(
            self._store,
            self._collection,
            self._clock,
            filters=self._filters,
            order_by=self._order_by,
            limit_=n,
        )

    def _matches(self, doc: dict[str, Any]) -> bool:
        for field, op, value in self._filters:
            actual = doc.get(field)
            if op == "==":
                if actual != value:
                    return False
            elif op == "!=":
                if actual == value:
                    return False
            elif op == "<":
                if actual is None or not actual < value:
                    return False
            elif op == "<=":
                if actual is None or not actual <= value:
                    return False
            elif op == ">":
                if actual is None or not actual > value:
                    return False
            elif op == ">=":
                if actual is None or not actual >= value:
                    return False
            elif op == "in":
                if actual not in value:
                    return False
            else:
                raise NotImplementedError(f"fake query op {op!r}")
        return True

    def _sorted_doc_items(self) -> list[tuple[str, dict[str, Any]]]:
        items = list(self._store.get(self._collection, {}).items())
        items = [(doc_id, doc) for doc_id, doc in items if self._matches(doc)]
        # apply order_by in reverse so the first ordering key is primary
        for field, direction in reversed(self._order_by):
            items.sort(
                key=lambda kv, f=field: kv[1].get(f),
                reverse=(direction.upper() == "DESCENDING"),
            )
        if self._limit is not None:
            items = items[: self._limit]
        return items

    async def stream(self) -> AsyncIterator[_FakeSnapshot]:
        for doc_id, doc in self._sorted_doc_items():
            ref = _FakeDocumentRef(self._store, self._collection, doc_id, self._clock)
            yield _FakeSnapshot(doc, reference=ref)

    async def get(self) -> list[_FakeSnapshot]:
        return [snap async for snap in self.stream()]


class _FakeCollection:
    def __init__(
        self,
        store: dict[str, dict[str, dict]],
        collection: str,
        clock: Callable[[], datetime],
    ) -> None:
        self._store = store
        self._collection = collection
        self._clock = clock

    def document(self, doc_id: str) -> _FakeDocumentRef:
        return _FakeDocumentRef(self._store, self._collection, doc_id, self._clock)

    async def stream(self) -> AsyncIterator[_FakeSnapshot]:
        for doc_id, data in self._store.get(self._collection, {}).items():
            ref = _FakeDocumentRef(self._store, self._collection, doc_id, self._clock)
            yield _FakeSnapshot(data, reference=ref)

    def where(self, *, filter: FieldFilter) -> _FakeQuery:  # noqa: A002
        return _FakeQuery(self._store, self._collection, self._clock).where(filter=filter)

    def order_by(self, field: str, direction: str = "ASCENDING") -> _FakeQuery:
        return _FakeQuery(self._store, self._collection, self._clock).order_by(field, direction)

    def limit(self, n: int) -> _FakeQuery:
        return _FakeQuery(self._store, self._collection, self._clock).limit(n)


class FakeAsyncClient:
    """Tiny in-memory stand-in for ``google.cloud.firestore.AsyncClient``.

    Seeded via a nested dict keyed by ``{collection: {doc_id: payload}}``.
    An optional ``clock`` callable injects deterministic timestamps into
    ``SERVER_TIMESTAMP`` substitution — defaults to ``datetime.now(timezone.utc)``
    on each call.
    """

    def __init__(
        self,
        seeded: dict[str, dict[str, dict]],
        *,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._store = seeded
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(timezone.utc))

    def collection(self, name: str) -> _FakeCollection:
        return _FakeCollection(self._store, name, self._clock)

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


@pytest.fixture
def fake_client() -> FakeAsyncClient:
    """Bare FakeAsyncClient with no seed data — for persistence store tests
    that start from an empty collection."""
    return FakeAsyncClient({})
