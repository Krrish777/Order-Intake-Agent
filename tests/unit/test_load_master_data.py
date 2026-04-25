"""Unit tests for scripts/load_master_data.py — Track E extension.

Focus: the pure helper functions. The full seed-script integration
(Firestore batch write + genai call) is covered by the emulator test
in tests/integration/.
"""

from __future__ import annotations

import pytest


def test_embed_text_for_product_composes_short_long_and_category_slash_subcategory():
    from scripts.load_master_data import _embed_text_for_product

    product = {
        "sku": "FST-HCS-050-13-200-G5Z",
        "short_description": "HCS 1/2-13 x 2 GR5 ZP",
        "long_description": (
            'Hex Head Cap Screw, 1/2"-13 UNC x 2" OAL, Steel Grade 5, '
            "Zinc Plated (Clear), Plain Washer Face"
        ),
        "category": "fasteners",
        "subcategory": "hex_cap_screws",
    }

    text = _embed_text_for_product(product)

    assert text == (
        "HCS 1/2-13 x 2 GR5 ZP. "
        'Hex Head Cap Screw, 1/2"-13 UNC x 2" OAL, Steel Grade 5, '
        "Zinc Plated (Clear), Plain Washer Face. "
        "Category: fasteners/hex_cap_screws."
    )


def test_embed_text_for_product_handles_missing_subcategory():
    from scripts.load_master_data import _embed_text_for_product

    product = {
        "sku": "SKU-1",
        "short_description": "Widget A",
        "long_description": "A generic widget.",
        "category": "widgets",
    }

    text = _embed_text_for_product(product)

    assert text == "Widget A. A generic widget. Category: widgets."


# ---------- Track E: genai embedding call + --no-embeddings flag ----------

from unittest.mock import MagicMock, patch


def _fake_genai_with_fixed_vector(vector: list[float]) -> MagicMock:
    """Build a MagicMock that impersonates google.genai.Client well enough
    for embed_content(...).embeddings[0].values to return `vector`."""
    client = MagicMock()
    response = MagicMock()
    embedding_obj = MagicMock()
    embedding_obj.values = vector
    response.embeddings = [embedding_obj]
    client.models.embed_content = MagicMock(return_value=response)
    return client


def test_embed_text_runs_embed_content_with_retrieval_document_task_type():
    from scripts.load_master_data import EMBED_DIM, EMBED_MODEL, _embed_text

    fake = _fake_genai_with_fixed_vector([0.1] * EMBED_DIM)

    result = _embed_text(fake, "hello world")

    assert result == [0.1] * EMBED_DIM

    assert fake.models.embed_content.call_count == 1
    kwargs = fake.models.embed_content.call_args.kwargs
    assert kwargs["model"] == EMBED_MODEL == "text-embedding-004"
    assert kwargs["contents"] == ["hello world"]
    config = kwargs["config"]
    assert config.task_type == "RETRIEVAL_DOCUMENT"
    assert config.output_dimensionality == EMBED_DIM == 768


def test_load_products_with_embeddings_false_skips_genai_entirely():
    """Seed with --no-embeddings should NOT construct a genai.Client nor
    call embed_content, so the script works offline / without GOOGLE_API_KEY."""
    from scripts import load_master_data

    fake_db = MagicMock()
    fake_batch = MagicMock()
    fake_db.batch.return_value = fake_batch

    with patch.object(load_master_data, "GenAIClient") as genai_ctor:
        count = load_master_data.load_products(fake_db, with_embeddings=False)

    genai_ctor.assert_not_called()
    assert fake_batch.set.call_count == count
    for call in fake_batch.set.call_args_list:
        _ref, doc = call.args
        assert "description_embedding" not in doc


def test_load_products_with_embeddings_true_calls_embed_once_per_product():
    """Each product triggers one embed_content call + the resulting
    vector is wrapped in Vector() on the written doc."""
    from google.cloud.firestore_v1.vector import Vector

    from scripts import load_master_data

    fake_db = MagicMock()
    fake_batch = MagicMock()
    fake_db.batch.return_value = fake_batch

    fake_vector = [0.25] * load_master_data.EMBED_DIM
    fake_genai = _fake_genai_with_fixed_vector(fake_vector)

    with patch.object(load_master_data, "GenAIClient", return_value=fake_genai):
        count = load_master_data.load_products(fake_db, with_embeddings=True)

    assert fake_genai.models.embed_content.call_count == count
    for call in fake_batch.set.call_args_list:
        _ref, doc = call.args
        assert "description_embedding" in doc
        assert isinstance(doc["description_embedding"], Vector)


def test_embed_text_for_product_handles_empty_subcategory():
    from scripts.load_master_data import _embed_text_for_product

    product = {
        "sku": "SKU-1",
        "short_description": "Widget A",
        "long_description": "A generic widget.",
        "category": "widgets",
        "subcategory": "",
    }

    text = _embed_text_for_product(product)

    assert text == "Widget A. A generic widget. Category: widgets."
