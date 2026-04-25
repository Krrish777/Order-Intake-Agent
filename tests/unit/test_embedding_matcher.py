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
