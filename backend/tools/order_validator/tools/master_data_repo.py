"""Async master-data repository over Firestore.

One of the validator's tools — the data-access tool. Sibling tools
(``sku_matcher``, ``customer_resolver``, ``price_check``, ``qty_check``)
consume the typed records this module returns; they own the business
rules, this module owns the I/O.

Reads only. Transactional / write paths (orders, exceptions) belong to a
separate ``backend/persistence/`` package owned by the
``feat/persistence-writes`` track.

The repo owns one :class:`~google.cloud.firestore.AsyncClient` for its
lifetime and caches ``list_all_products`` / the customer roster in
memory. The product catalog is ~35 rows, the customer master ~10 — both
trivially small for per-request caching, but large enough to make
fetching them once per validator invocation a noticeable latency win on
orders with many line items.

Not-found semantics:

* Single-doc reads (:meth:`get_product`, :meth:`get_customer`,
  :meth:`find_customer_by_name`) return ``None``.
* :meth:`get_products` omits missing skus from the returned dict.
* :meth:`get_meta` raises ``LookupError`` — a missing meta document is a
  seeding / configuration failure, not a data-entry miss.
* :meth:`find_product_by_embedding` is a Layer-2 stub and always returns
  ``[]`` until the embedding seed + vector index ship.
"""

from __future__ import annotations

from typing import Optional

from google.cloud.firestore import AsyncClient
from google.cloud.firestore_v1.async_document import AsyncDocumentReference
from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
from google.cloud.firestore_v1.vector import Vector
from google.genai import Client as GenAIClient
from google.genai.types import EmbedContentConfig
from rapidfuzz import fuzz, process

from backend.models.master_records import (
    CustomerRecord,
    EmbeddingMatch,
    MetaRecord,
    ProductRecord,
)
from backend.utils.logging import get_logger

_log = get_logger(__name__)


def _is_valid_doc_id(value: str) -> bool:
    # Firestore rejects doc-ids containing '/' (path-segment separator) or empty/None.
    # LLM extraction occasionally returns description-like strings as SKU candidates
    # (e.g. "HCS 1/2-13 x 2 GR5 ZP"); treat those as misses rather than crashing.
    return bool(value) and "/" not in value

PRODUCTS_COLLECTION = "products"
CUSTOMERS_COLLECTION = "customers"
META_COLLECTION = "meta"
META_DOC_ID = "master_data"

DEFAULT_CUSTOMER_MATCH_THRESHOLD = 90
DEFAULT_EMBEDDING_TOP_K = 5

EMBED_MODEL = "gemini-embedding-001"
EMBED_DIM = 768


class MasterDataRepo:
    """Async, dependency-injected read surface for the master-data
    collections. Construct with an ``AsyncClient`` (emulator or live) and
    call the methods below; close with :meth:`aclose` when the owning
    stage shuts down.
    """

    def __init__(
        self,
        client: AsyncClient,
        *,
        genai_client: Optional[GenAIClient] = None,
    ) -> None:
        self._client = client
        self._products_cache: Optional[list[ProductRecord]] = None
        self._customers_cache: Optional[list[CustomerRecord]] = None
        self._genai_client = genai_client

    def _ensure_genai_client(self) -> GenAIClient:
        """Lazy-construct a google-genai client the first time it's needed.

        The client reads GOOGLE_API_KEY / ADC at construction time, so we
        defer it: a MasterDataRepo used only for tier-1/2 lookups never
        triggers the credential read at all.
        """
        if self._genai_client is None:
            self._genai_client = GenAIClient()
        return self._genai_client

    async def _embed_query(self, text: str) -> Optional[list[float]]:
        """Embed a customer-side query string via text-embedding-004.

        Returns the 768-dim float vector on success, ``None`` on any
        exception (fail-open; the caller treats ``None`` as a tier-3 miss
        and the validator's aggregate scoring handles the routing).
        """
        try:
            client = self._ensure_genai_client()
            response = await client.aio.models.embed_content(
                model=EMBED_MODEL,
                contents=[text],
                config=EmbedContentConfig(
                    task_type="RETRIEVAL_QUERY",
                    output_dimensionality=EMBED_DIM,
                ),
            )
            return list(response.embeddings[0].values)
        except Exception as exc:  # noqa: BLE001 — fail-open by design
            _log.warning("embedding_query_failed", error=str(exc), text=text[:80])
            return None

    @property
    def firestore_client(self) -> AsyncClient:
        """Exposed for Track C duplicate_check.find_duplicate, which needs
        a raw async client for ``orders`` collection queries (master data
        is in products/customers collections; dup detection spans orders)."""
        return self._client

    # ------------------------------------------------------------------ products

    async def get_product(self, sku: str) -> Optional[ProductRecord]:
        """Exact document lookup by sku. ``None`` if the sku is not in the
        catalog."""
        if not _is_valid_doc_id(sku):
            _log.debug("product_skipped_invalid_sku", sku=sku)
            return None
        snap = await self._client.collection(PRODUCTS_COLLECTION).document(sku).get()
        if not snap.exists:
            _log.debug("product_not_found", sku=sku)
            return None
        return ProductRecord(**snap.to_dict())

    async def get_products(self, skus: list[str]) -> dict[str, ProductRecord]:
        """Batch lookup by sku. One Firestore round-trip via ``get_all``.

        Returns a dict keyed by sku; missing skus are absent (no ``None``
        entries). Caller detects a miss with ``if sku not in result``.
        Order of ``skus`` is irrelevant.
        """
        if not skus:
            return {}

        unique_skus = [s for s in dict.fromkeys(skus) if _is_valid_doc_id(s)]
        if not unique_skus:
            return {}
        refs: list[AsyncDocumentReference] = [
            self._client.collection(PRODUCTS_COLLECTION).document(s) for s in unique_skus
        ]

        result: dict[str, ProductRecord] = {}
        async for snap in self._client.get_all(refs):
            if snap.exists:
                record = ProductRecord(**snap.to_dict())
                result[record.sku] = record

        missing = [s for s in unique_skus if s not in result]
        if missing:
            _log.debug("products_batch_misses", missing=missing, hit_count=len(result))
        return result

    async def list_all_products(self) -> list[ProductRecord]:
        """Full catalog scan. Cached on the repo instance after the first
        call. Feeds the rapidfuzz pool used by downstream SKU matching."""
        if self._products_cache is not None:
            return self._products_cache

        products: list[ProductRecord] = []
        async for snap in self._client.collection(PRODUCTS_COLLECTION).stream():
            products.append(ProductRecord(**snap.to_dict()))
        self._products_cache = products
        _log.debug("products_cache_primed", count=len(products))
        return products

    # ----------------------------------------------------------------- customers

    async def get_customer(self, customer_id: str) -> Optional[CustomerRecord]:
        """Exact lookup by customer_id. Returns the full record including
        the ``sku_aliases`` map — alias resolution is a map lookup on the
        returned record, not a separate repo call."""
        snap = (
            await self._client.collection(CUSTOMERS_COLLECTION).document(customer_id).get()
        )
        if not snap.exists:
            _log.debug("customer_not_found", customer_id=customer_id)
            return None
        return CustomerRecord(**snap.to_dict())

    async def find_customer_by_name(
        self,
        name: str,
        threshold: int = DEFAULT_CUSTOMER_MATCH_THRESHOLD,
    ) -> Optional[CustomerRecord]:
        """Fuzzy-match a free-text customer string against the roster.

        The parsed document carries ``customer_name`` (e.g. "Patterson
        Industrial") but the customers collection is keyed by
        ``customer_id`` (e.g. ``CUST-00042``). This method bridges the
        gap: load the roster once, run ``token_set_ratio`` against both
        ``name`` and ``dba``, return the best match at or above
        ``threshold`` (default 90). ``None`` below threshold.
        """
        if not name or not name.strip():
            return None

        customers = await self._list_all_customers()
        if not customers:
            return None

        candidate_index: list[tuple[str, CustomerRecord]] = []
        for c in customers:
            candidate_index.append((c.name, c))
            if c.dba:
                candidate_index.append((c.dba, c))

        best = process.extractOne(
            name,
            [label for label, _ in candidate_index],
            scorer=fuzz.token_set_ratio,
        )
        if best is None:
            return None
        _label, score, idx = best
        if score < threshold:
            _log.debug(
                "customer_fuzzy_below_threshold",
                query=name,
                best_score=score,
                threshold=threshold,
            )
            return None
        return candidate_index[idx][1]

    async def _list_all_customers(self) -> list[CustomerRecord]:
        if self._customers_cache is not None:
            return self._customers_cache

        customers: list[CustomerRecord] = []
        async for snap in self._client.collection(CUSTOMERS_COLLECTION).stream():
            customers.append(CustomerRecord(**snap.to_dict()))
        self._customers_cache = customers
        _log.debug("customers_cache_primed", count=len(customers))
        return customers

    # ---------------------------------------------------------------------- meta

    async def get_meta(self) -> MetaRecord:
        """Read the catalog + customer-master version stamp. A missing
        doc is a config error (the seed script failed to write it), so
        this raises ``LookupError`` rather than returning ``None``."""
        snap = (
            await self._client.collection(META_COLLECTION).document(META_DOC_ID).get()
        )
        if not snap.exists:
            raise LookupError(
                f"meta/{META_DOC_ID} not found in Firestore — run scripts/load_master_data.py"
            )
        return MetaRecord(**snap.to_dict())

    # ---------------------------------------------------------------- layer 2

    async def find_product_by_embedding(
        self,
        query: str,
        k: int = DEFAULT_EMBEDDING_TOP_K,
    ) -> list[EmbeddingMatch]:
        """Layer-2 semantic SKU match: embed the customer query, run
        ``find_nearest`` against the ``description_embedding`` field, and
        return similarity-scored ``EmbeddingMatch`` candidates in
        descending-score order.

        Fail-open contract:

        * Degenerate input (empty query, whitespace only, ``k < 1``) →
          ``[]`` with no API call.
        * Embedding API exception → ``[]`` (logged via ``_embed_query``).
        * Firestore ``find_nearest`` exception → bubbles (caller is
          responsible; this is an infrastructure failure worth surfacing).

        Scores are in ``[0.0, 1.0]`` via ``similarity = 1 - cosine_distance / 2``,
        clamped. Caller (``sku_matcher``) compares ``matches[0].score``
        against ``EMBEDDING_THRESHOLD``.
        """
        if not query or not query.strip() or k < 1:
            return []

        query_vec = await self._embed_query(query)
        if query_vec is None:
            return []

        vector_query = (
            self._client
                .collection(PRODUCTS_COLLECTION)
                .find_nearest(
                    vector_field="description_embedding",
                    query_vector=Vector(query_vec),
                    distance_measure=DistanceMeasure.COSINE,
                    limit=k,
                    distance_result_field="__distance",
                )
        )

        matches: list[EmbeddingMatch] = []
        async for snap in vector_query.stream():
            data = snap.to_dict() or {}
            distance = float(data.get("__distance", 2.0))
            similarity = max(0.0, min(1.0, 1.0 - distance / 2.0))
            matches.append(EmbeddingMatch(
                sku=snap.id,
                score=similarity,
                source="firestore_findnearest",
            ))
        return matches

    # ------------------------------------------------------------------- cleanup

    async def aclose(self) -> None:
        """Close the underlying Firestore client. Call from a shutdown
        hook or test teardown."""
        close = getattr(self._client, "close", None)
        if close is None:
            return
        result = close()
        # AsyncClient.close() is sync in current firestore; handle both.
        if hasattr(result, "__await__"):
            await result


__all__ = [
    "MasterDataRepo",
    "PRODUCTS_COLLECTION",
    "CUSTOMERS_COLLECTION",
    "META_COLLECTION",
    "META_DOC_ID",
    "DEFAULT_CUSTOMER_MATCH_THRESHOLD",
    "DEFAULT_EMBEDDING_TOP_K",
]
