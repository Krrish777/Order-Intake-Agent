"""Unit tests for backend.tools.document_parser. Fully mocked — no network."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from llama_cloud import RateLimitError

from backend.tools.document_parser import parser as dp
from backend.tools.document_parser import (
    ExtractedOrder,
    OrderLineItem,
    ParsedDocument,
    ParseFailedError,
    ParseRateLimitError,
    ParseTimeoutError,
    parse_document,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_VALID_RESULT = {
    "classification": "purchase_order",
    "classification_rationale": "Document is titled 'Purchase Order' and lists line items.",
    "sub_documents": [
        {
            "customer_name": "Acme Corp",
            "po_number": "PO-1001",
            "line_items": [
                {"sku": "WIDGET-1", "quantity": 10, "unit_price": 5.0},
            ],
        }
    ],
    "page_count": 1,
    "detected_language": "en",
}


def _job(status: str, *, job_id: str = "ext-1", result: dict | None = None, error: str | None = None):
    """Build a SimpleNamespace mimicking a LlamaCloud extract job object."""
    return SimpleNamespace(
        id=job_id,
        status=status,
        extract_result=result,
        error=error,
    )


@pytest.fixture
def mock_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace the cached LlamaCloud client with a MagicMock for the test."""
    client = MagicMock()
    client.files.create.return_value = SimpleNamespace(id="dfl-test-1")
    monkeypatch.setattr(dp, "_client", client)
    return client


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------

def test_schema_root_is_object():
    """LlamaExtract requires data_schema root to be type=object. Catch regressions."""
    schema = ParsedDocument.model_json_schema()
    assert schema["type"] == "object"
    assert "properties" in schema
    assert "classification" in schema["properties"]


def test_extracted_order_pydantic_round_trip():
    payload = {
        "customer_name": "Acme",
        "po_number": "PO-42",
        "line_items": [
            {
                "sku": "SKU-1",
                "description": "Widget",
                "quantity": 5,
                "unit_of_measure": "EA",
                "unit_price": 1.25,
                "requested_date": "2026-05-01",
            }
        ],
        "ship_to_address": "1 Acme St, Atlanta, GA",
        "requested_delivery_date": "2026-05-01",
        "special_instructions": "Rush.",
    }
    order = ExtractedOrder.model_validate(payload)
    assert order.model_dump(exclude_none=True) == payload
    assert isinstance(order.line_items[0], OrderLineItem)


# ---------------------------------------------------------------------------
# parse_document — terminal-status handling
# ---------------------------------------------------------------------------

def test_parse_document_returns_parsed_document_on_completed(mock_client: MagicMock):
    mock_client.extract.create.return_value = _job("COMPLETED", result=_VALID_RESULT)
    result = parse_document(b"%PDF-1.4 fake", filename="po.pdf")
    assert isinstance(result, ParsedDocument)
    assert result.classification == "purchase_order"
    assert len(result.sub_documents) == 1
    assert result.sub_documents[0].po_number == "PO-1001"


def test_parse_document_propagates_timeout(mock_client: MagicMock):
    mock_client.extract.create.return_value = _job("PENDING")
    mock_client.extract.get.return_value = _job("PENDING")
    with pytest.raises(ParseTimeoutError) as excinfo:
        parse_document(b"x", filename="po.pdf", timeout_s=0.5, poll_interval_s=0.05)
    err = excinfo.value
    assert err.job_id == "ext-1"
    assert err.stage == "extract.get"
    assert err.timeout_s == 0.5
    assert err.last_status == "PENDING"
    assert err.elapsed_s >= 0.5
    # str(exc) should be a useful one-liner that includes the structured fields
    assert "ext-1" in str(err)
    assert "PENDING" in str(err)


def test_parse_document_propagates_failure(mock_client: MagicMock):
    mock_client.extract.create.return_value = _job(
        "FAILED", result=None, error="schema validation failed"
    )
    with pytest.raises(ParseFailedError) as excinfo:
        parse_document(b"x", filename="po.pdf")
    err = excinfo.value
    assert err.status == "FAILED"
    assert err.job_id == "ext-1"
    assert err.stage == "extract.get"
    assert err.detail == "schema validation failed"


def test_parse_document_propagates_rate_limit(mock_client: MagicMock):
    # RateLimitError is constructed by the SDK from a real HTTP response; for unit
    # purposes we just need an instance the except clause will catch.
    mock_client.extract.create.side_effect = RateLimitError.__new__(RateLimitError)
    with pytest.raises(ParseRateLimitError) as excinfo:
        parse_document(b"x", filename="po.pdf")
    err = excinfo.value
    assert err.stage == "extract.create"
    # rate-limit at extract.create happens before we have a job_id
    assert err.job_id is None


def test_rate_limit_at_files_create_carries_correct_stage(mock_client: MagicMock):
    """A 429 during upload should report stage='files.create', not extract.*"""
    mock_client.files.create.side_effect = RateLimitError.__new__(RateLimitError)
    with pytest.raises(ParseRateLimitError) as excinfo:
        parse_document(b"x", filename="po.pdf")
    assert excinfo.value.stage == "files.create"


def test_parse_error_str_renders_structured_fields():
    """str(exc) should be a useful summary including stage, job_id, and detail."""
    from backend.exceptions import ParseError
    exc = ParseError(
        "boom", stage="extract.create", job_id="ext-99", detail="upstream 502",
    )
    rendered = str(exc)
    assert "boom" in rendered
    assert "extract.create" in rendered
    assert "ext-99" in rendered
    assert "upstream 502" in rendered


# ---------------------------------------------------------------------------
# parse_document — configuration shape
# ---------------------------------------------------------------------------

def test_extra_hint_appended_to_prompt(mock_client: MagicMock):
    mock_client.extract.create.return_value = _job("COMPLETED", result=_VALID_RESULT)
    parse_document(b"x", filename="po.pdf", extra_hint="Acme uses 'PN' for SKU")

    _, kwargs = mock_client.extract.create.call_args
    sent_prompt = kwargs["configuration"]["system_prompt"]
    assert sent_prompt.endswith("Acme uses 'PN' for SKU")
    assert "supply chain document extractor" in sent_prompt  # global prompt still present


def test_configuration_uses_expected_keys(mock_client: MagicMock):
    """Locks in the design decisions: agentic tier, no confidence/citations, per-doc target."""
    mock_client.extract.create.return_value = _job("COMPLETED", result=_VALID_RESULT)
    parse_document(b"x", filename="po.pdf")

    _, kwargs = mock_client.extract.create.call_args
    cfg = kwargs["configuration"]
    assert cfg["tier"] == "agentic"
    assert cfg["extraction_target"] == "per_doc"
    assert cfg["confidence_scores"] is False
    assert cfg["cite_sources"] is False
    assert cfg["data_schema"]["type"] == "object"


def test_files_create_passes_filename_as_external_id(mock_client: MagicMock):
    mock_client.extract.create.return_value = _job("COMPLETED", result=_VALID_RESULT)
    parse_document(b"hello", filename="email_body.txt")

    _, kwargs = mock_client.files.create.call_args
    assert kwargs["external_file_id"] == "email_body.txt"
    assert kwargs["purpose"] == "extract"


def test_long_text_input_emits_truncation_warning(mock_client: MagicMock, caplog):
    """Email bodies >60KB should trigger a warning about LlamaExtract's 64KB/page truncation."""
    mock_client.extract.create.return_value = _job("COMPLETED", result=_VALID_RESULT)
    big_text = b"x" * 70_000
    with caplog.at_level("WARNING", logger="backend.tools.document_parser.parser"):
        parse_document(big_text, filename="huge_email.txt")
    assert any("silently truncates" in rec.message for rec in caplog.records)
