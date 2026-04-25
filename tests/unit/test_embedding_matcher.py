"""Unit tests for MasterDataRepo's embedding + find_nearest surface
(Track E). Covers _embed_query (async) + find_product_by_embedding
(the full query path, Firestore mocked).

All tests mock the google.genai client and the AsyncClient.collection
chain. The emulator round-trip lives in tests/integration/.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def _fake_async_genai_with_fixed_vector(vector: list[float]) -> MagicMock:
    """Impersonate google.genai.Client well enough for
    client.aio.models.embed_content(...) to await into a response whose
    .embeddings[0].values is `vector`."""
    client = MagicMock()
    response = MagicMock()
    embedding_obj = MagicMock()
    embedding_obj.values = vector
    response.embeddings = [embedding_obj]
    client.aio.models.embed_content = AsyncMock(return_value=response)
    return client


def _fake_async_genai_that_raises(exc: Exception) -> MagicMock:
    client = MagicMock()
    client.aio.models.embed_content = AsyncMock(side_effect=exc)
    return client


@pytest.mark.asyncio
async def test_embed_query_returns_768_dim_vector_on_happy_path():
    from backend.tools.order_validator.tools.master_data_repo import (
        EMBED_DIM,
        EMBED_MODEL,
        MasterDataRepo,
    )

    fake_genai = _fake_async_genai_with_fixed_vector([0.1] * EMBED_DIM)
    repo = MasterDataRepo(client=MagicMock(), genai_client=fake_genai)

    result = await repo._embed_query("dark roast 5 lb bag")

    assert result == [0.1] * EMBED_DIM

    kwargs = fake_genai.aio.models.embed_content.call_args.kwargs
    assert kwargs["model"] == EMBED_MODEL == "text-embedding-004"
    assert kwargs["contents"] == ["dark roast 5 lb bag"]
    config = kwargs["config"]
    assert config.task_type == "RETRIEVAL_QUERY"
    assert config.output_dimensionality == EMBED_DIM == 768


@pytest.mark.asyncio
async def test_embed_query_returns_none_on_api_exception():
    """Fail-open: exception from embed_content -> logs a warning and
    returns None. The caller treats that as a tier-3 miss."""
    from backend.tools.order_validator.tools.master_data_repo import MasterDataRepo

    fake_genai = _fake_async_genai_that_raises(RuntimeError("simulated outage"))
    repo = MasterDataRepo(client=MagicMock(), genai_client=fake_genai)

    result = await repo._embed_query("widget red")

    assert result is None


@pytest.mark.asyncio
async def test_embed_query_returns_none_on_malformed_response():
    """If the response object lacks .embeddings or the list is empty,
    we don't crash — we fail-open."""
    from backend.tools.order_validator.tools.master_data_repo import MasterDataRepo

    client = MagicMock()
    bad_response = MagicMock()
    bad_response.embeddings = []
    client.aio.models.embed_content = AsyncMock(return_value=bad_response)

    repo = MasterDataRepo(client=MagicMock(), genai_client=client)

    result = await repo._embed_query("anything")

    assert result is None


def test_genai_client_is_lazily_constructed_when_not_injected():
    """Constructing MasterDataRepo without genai_client should NOT call
    google.genai.Client() eagerly — only on first embedding call."""
    from unittest.mock import patch

    from backend.tools.order_validator.tools import master_data_repo

    with patch.object(master_data_repo, "GenAIClient") as ctor:
        repo = master_data_repo.MasterDataRepo(client=MagicMock())
        ctor.assert_not_called()

        client = repo._ensure_genai_client()
        ctor.assert_called_once()

        again = repo._ensure_genai_client()
        assert again is client
        ctor.assert_called_once()


# ---------- find_product_by_embedding (real impl) ----------


def _fake_vector_query_with_docs(docs: list[tuple[str, float]]) -> MagicMock:
    """Make an async stream() that yields (sku, distance) tuples as
    Firestore-style snapshots. `distance` is the cosine distance
    (0..2); the repo converts to similarity = 1 - d/2.
    """
    class _FakeSnap:
        def __init__(self, sku: str, distance: float) -> None:
            self.id = sku
            self._data = {"__distance": distance}

        def to_dict(self) -> dict:
            return dict(self._data)

    class _AsyncStream:
        def __init__(self, snaps):
            self._snaps = list(snaps)

        def __aiter__(self):
            self._i = 0
            return self

        async def __anext__(self):
            if self._i >= len(self._snaps):
                raise StopAsyncIteration
            snap = self._snaps[self._i]
            self._i += 1
            return snap

    snaps = [_FakeSnap(sku, d) for sku, d in docs]

    vector_query = MagicMock()
    vector_query.stream = MagicMock(return_value=_AsyncStream(snaps))
    return vector_query


def _fake_firestore_client_returning(vector_query: MagicMock) -> MagicMock:
    """Build a MagicMock AsyncClient chain:
      client.collection(PRODUCTS_COLLECTION).find_nearest(...) -> vector_query
    """
    client = MagicMock()
    collection = MagicMock()
    client.collection = MagicMock(return_value=collection)
    collection.find_nearest = MagicMock(return_value=vector_query)
    return client


@pytest.mark.asyncio
async def test_find_product_by_embedding_returns_sorted_matches_with_similarity_conversion():
    """Distance 0 -> similarity 1.0; distance 1 -> 0.5; distance 2 -> 0.0."""
    from backend.tools.order_validator.tools.master_data_repo import (
        EMBED_DIM,
        MasterDataRepo,
    )

    fake_genai = _fake_async_genai_with_fixed_vector([0.1] * EMBED_DIM)
    vector_query = _fake_vector_query_with_docs([
        ("SKU-A", 0.2),
        ("SKU-B", 1.0),
        ("SKU-C", 1.8),
    ])
    fake_client = _fake_firestore_client_returning(vector_query)

    repo = MasterDataRepo(client=fake_client, genai_client=fake_genai)
    matches = await repo.find_product_by_embedding("dark roast 5 lb bag")

    assert [m.sku for m in matches] == ["SKU-A", "SKU-B", "SKU-C"]
    assert matches[0].score == pytest.approx(0.9)
    assert matches[1].score == pytest.approx(0.5)
    assert matches[2].score == pytest.approx(0.1)
    assert all(m.source == "firestore_findnearest" for m in matches)


@pytest.mark.asyncio
async def test_find_product_by_embedding_forwards_k_to_find_nearest_limit():
    from backend.tools.order_validator.tools.master_data_repo import (
        EMBED_DIM,
        MasterDataRepo,
    )

    fake_genai = _fake_async_genai_with_fixed_vector([0.1] * EMBED_DIM)
    vector_query = _fake_vector_query_with_docs([])
    fake_client = _fake_firestore_client_returning(vector_query)

    repo = MasterDataRepo(client=fake_client, genai_client=fake_genai)
    await repo.find_product_by_embedding("anything", k=3)

    collection = fake_client.collection.return_value
    kwargs = collection.find_nearest.call_args.kwargs
    assert kwargs["limit"] == 3
    assert kwargs["distance_result_field"] == "__distance"


@pytest.mark.asyncio
async def test_find_product_by_embedding_returns_empty_for_empty_query():
    from backend.tools.order_validator.tools.master_data_repo import MasterDataRepo

    fake_genai = MagicMock()
    fake_genai.aio.models.embed_content = AsyncMock()
    repo = MasterDataRepo(client=MagicMock(), genai_client=fake_genai)

    assert await repo.find_product_by_embedding("") == []
    assert await repo.find_product_by_embedding("   ") == []
    assert await repo.find_product_by_embedding("foo", k=0) == []

    fake_genai.aio.models.embed_content.assert_not_called()


@pytest.mark.asyncio
async def test_find_product_by_embedding_returns_empty_when_embed_query_fails():
    """_embed_query returning None (fail-open) must short-circuit —
    no Firestore call, empty list."""
    from backend.tools.order_validator.tools.master_data_repo import MasterDataRepo

    fake_genai = _fake_async_genai_that_raises(RuntimeError("outage"))
    fake_client = MagicMock()
    repo = MasterDataRepo(client=fake_client, genai_client=fake_genai)

    matches = await repo.find_product_by_embedding("widget red")

    assert matches == []
    fake_client.collection.assert_not_called()


@pytest.mark.asyncio
async def test_find_product_by_embedding_clamps_out_of_range_distances():
    """If Firestore returns a weirdly-large distance (>2.0), similarity
    clamps to 0.0 rather than going negative."""
    from backend.tools.order_validator.tools.master_data_repo import (
        EMBED_DIM,
        MasterDataRepo,
    )

    fake_genai = _fake_async_genai_with_fixed_vector([0.1] * EMBED_DIM)
    vector_query = _fake_vector_query_with_docs([
        ("SKU-X", 3.5),
    ])
    fake_client = _fake_firestore_client_returning(vector_query)

    repo = MasterDataRepo(client=fake_client, genai_client=fake_genai)
    matches = await repo.find_product_by_embedding("anything")

    assert len(matches) == 1
    assert matches[0].score == 0.0
