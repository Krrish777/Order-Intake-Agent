"""Emulator round-trip test for Track E's tier-3 vector search.

Strategy: seed 3 products with deterministic hand-crafted embeddings
so the test doesn't depend on a live Gemini call. We then call
find_product_by_embedding with _embed_query mocked to return a
specific query vector, and assert the Firestore emulator's find_nearest
returns the expected ranking.

The hand-crafted vectors live in 768-dim space:
  product TEST-A: one-hot at index 0 — maximally similar to query
  product TEST-B: one-hot at index 1
  product TEST-C: one-hot at index 2
  query         : one-hot at index 0  <-- matches TEST-A exactly

We expect TEST-A as top-1 with similarity ~1.0; TEST-B and TEST-C are
orthogonal (cosine distance 1.0 → similarity 0.5).
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest
from google.cloud.firestore import AsyncClient
from google.cloud.firestore_v1.vector import Vector


EMBED_DIM = 768


pytestmark = [
    pytest.mark.firestore_emulator,
    pytest.mark.skipif(
        not os.environ.get("FIRESTORE_EMULATOR_HOST"),
        reason="FIRESTORE_EMULATOR_HOST not set; emulator tests skipped",
    ),
]


def _one_hot(index: int) -> list[float]:
    """Return a 768-dim unit vector with 1.0 at `index` and 0.0 elsewhere."""
    v = [0.0] * EMBED_DIM
    v[index] = 1.0
    return v


@pytest.mark.asyncio
async def test_find_nearest_returns_expected_top1_over_emulator() -> None:
    from backend.tools.order_validator.tools.master_data_repo import (
        PRODUCTS_COLLECTION,
        MasterDataRepo,
    )

    client = AsyncClient()
    collection = client.collection(PRODUCTS_COLLECTION)

    test_products = [
        ("TEST-A", _one_hot(0)),
        ("TEST-B", _one_hot(1)),
        ("TEST-C", _one_hot(2)),
    ]
    try:
        for sku, vec in test_products:
            await collection.document(sku).set({
                "sku": sku,
                "short_description": f"Product {sku}",
                "long_description": f"Full description of product {sku}.",
                "category": "test",
                "subcategory": "track-e",
                "uom": "EA",
                "pack_uom": "EA",
                "pack_size": 1,
                "alt_uoms": ["EA"],
                "unit_price_usd": 1.0,
                "standards": [],
                "lead_time_days": 1,
                "min_order_qty": 1,
                "country_of_origin": "US",
                "description_embedding": Vector(vec),
            })

        repo = MasterDataRepo(client)

        query_vec = _one_hot(0)
        with patch.object(repo, "_embed_query", AsyncMock(return_value=query_vec)):
            matches = await repo.find_product_by_embedding("product A", k=3)

        skus_ordered = [m.sku for m in matches]
        assert skus_ordered[0] == "TEST-A", (
            f"expected TEST-A as top-1; got order {skus_ordered}"
        )
        assert matches[0].score >= 0.99, (
            f"expected near-1.0 similarity for identical vectors; got {matches[0].score}"
        )
        assert matches[0].score <= 1.0
        assert matches[0].source == "firestore_findnearest"
    finally:
        for sku, _ in test_products:
            await collection.document(sku).delete()
        close = getattr(client, "close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result
