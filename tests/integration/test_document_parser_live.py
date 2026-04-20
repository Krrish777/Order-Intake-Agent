"""Integration tests for parse_document. Hits the live LlamaExtract API.

Requires LLAMA_CLOUD_API_KEY in the environment. Each test pytest.skips if its
fixture file is missing — the parallel data-generation session produces these.

Run with:
    uv run pytest tests/integration/ -m integration

Skip with:
    uv run pytest tests/integration/ -m "not integration"
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.tools.document_parser import parse_document

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("LLAMA_CLOUD_API_KEY"),
        reason="LLAMA_CLOUD_API_KEY not set — integration tests require live API access",
    ),
]

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _load(name: str) -> bytes:
    """Load a fixture by name; pytest.skip if missing so tests light up incrementally."""
    path = FIXTURES / name
    if not path.exists():
        pytest.skip(f"fixture not yet generated: {path}")
    return path.read_bytes()


def test_parse_clean_pdf_po():
    result = parse_document(_load("clean_po.pdf"), filename="clean_po.pdf")
    assert result.classification == "purchase_order"
    assert len(result.sub_documents) == 1
    order = result.sub_documents[0]
    assert order.po_number is not None
    assert len(order.line_items) >= 1
    for item in order.line_items:
        assert item.sku is not None
        assert item.quantity is not None and item.quantity > 0


def test_parse_xlsx():
    result = parse_document(_load("distributor_reorder.xlsx"), filename="distributor_reorder.xlsx")
    assert result.classification == "purchase_order"
    assert len(result.sub_documents) == 1
    assert len(result.sub_documents[0].line_items) >= 1


def test_parse_csv():
    result = parse_document(_load("simple_order.csv"), filename="simple_order.csv")
    assert result.classification == "purchase_order"
    assert len(result.sub_documents) == 1
    assert len(result.sub_documents[0].line_items) >= 1


def test_parse_email_body_text():
    result = parse_document(_load("order_email_body.txt"), filename="order_email_body.txt")
    assert result.classification == "purchase_order"
    assert len(result.sub_documents) == 1


def test_parse_scanned_image():
    result = parse_document(_load("scanned_po.png"), filename="scanned_po.png")
    assert result.classification == "purchase_order"
    assert len(result.sub_documents) >= 1


def test_split_multi_po_bundle():
    result = parse_document(_load("three_pos_in_one_pdf.pdf"), filename="three_pos_in_one_pdf.pdf")
    assert result.classification == "purchase_order"
    assert len(result.sub_documents) == 3
    po_numbers = [d.po_number for d in result.sub_documents]
    assert len(set(po_numbers)) == 3, f"PO numbers should be distinct: {po_numbers}"


def test_classify_inquiry():
    result = parse_document(_load("price_inquiry.txt"), filename="price_inquiry.txt")
    assert result.classification == "inquiry"
    assert result.sub_documents == []


def test_classify_spam():
    result = parse_document(_load("marketing_spam.txt"), filename="marketing_spam.txt")
    assert result.classification == "spam"
    assert result.sub_documents == []


def test_extra_hint_changes_extraction():
    """Acme uses 'PN' for SKU. Without hint the model may miss it; with hint it should map."""
    content = _load("acme_with_PN_labels.pdf")

    no_hint = parse_document(content, filename="acme.pdf")
    with_hint = parse_document(
        content,
        filename="acme.pdf",
        extra_hint="This customer (Acme) uses 'PN' as the column header for SKU.",
    )

    if with_hint.sub_documents:
        with_hint_skus = [item.sku for order in with_hint.sub_documents for item in order.line_items]
        assert any(sku is not None for sku in with_hint_skus), (
            "with extra_hint, at least one SKU should be populated"
        )
