"""Unit tests for backend.tools.document_parser. Fully mocked — no network."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from llama_cloud import (
    APIConnectionError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)

from backend.utils.exceptions import (
    ParseAuthError,
    ParseBadInputError,
    ParseConnectionError,
    ParseError,
    ParseFatalError,
    ParseNotFoundError,
    ParseQuotaExhaustedError,
    ParseRateLimitError,
    ParseRetryableError,
    ParseServerError,
)
from backend.tools.document_parser import parser as dp
from backend.tools.document_parser import (
    ExtractedOrder,
    OrderLineItem,
    ParsedDocument,
    ParseFailedError,
    ParseTimeoutError,
    parse_document,
)


# Fixtures

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
    return SimpleNamespace(
        id=job_id,
        status=status,
        extract_result=result,
        error=error,
    )


def _bare_sdk_exc(cls, *, status_code: int | None = None):
    """Construct an SDK exception without invoking its constructor.

    The real SDK builds these from an httpx.Response, which is awkward in
    unit tests. Bypassing __init__ with __new__ and setting status_code
    manually gives us instances that satisfy isinstance() checks and the
    translator's status_code dispatch.
    """
    exc = cls.__new__(cls)
    if status_code is not None:
        exc.status_code = status_code
    return exc


@pytest.fixture
def mock_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    client = MagicMock()
    client.files.create.return_value = SimpleNamespace(id="dfl-test-1")
    monkeypatch.setattr(dp, "_client", client)
    return client


# Schema tests

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


# parse_document — happy path + terminal-status handling

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
    # ParseFailedError is a fatal-category error
    assert isinstance(err, ParseFatalError)


# parse_document — configuration shape

def test_extra_hint_appended_to_prompt(mock_client: MagicMock):
    mock_client.extract.create.return_value = _job("COMPLETED", result=_VALID_RESULT)
    parse_document(b"x", filename="po.pdf", extra_hint="Acme uses 'PN' for SKU")

    _, kwargs = mock_client.extract.create.call_args
    sent_prompt = kwargs["configuration"]["system_prompt"]
    assert sent_prompt.endswith("Acme uses 'PN' for SKU")
    assert "supply chain document extractor" in sent_prompt


def test_configuration_uses_expected_keys(mock_client: MagicMock):
    mock_client.extract.create.return_value = _job("COMPLETED", result=_VALID_RESULT)
    parse_document(b"x", filename="po.pdf")

    _, kwargs = mock_client.extract.create.call_args
    cfg = kwargs["configuration"]
    assert cfg["tier"] == "agentic"
    assert cfg["extraction_target"] == "per_doc"
    assert cfg["confidence_scores"] is False
    assert cfg["cite_sources"] is False
    assert cfg["data_schema"]["type"] == "object"


def test_files_create_passes_hash_suffixed_external_id(mock_client: MagicMock):
    """external_file_id is the filename plus a content-hash suffix (see
    ``_external_file_id``), not the raw filename — LlamaCloud enforces
    uniqueness on (project_id, external_file_id) so re-running against
    the same fixture would otherwise trip a UniqueViolationError."""
    mock_client.extract.create.return_value = _job("COMPLETED", result=_VALID_RESULT)
    parse_document(b"hello", filename="email_body.txt")

    _, kwargs = mock_client.files.create.call_args
    assert kwargs["external_file_id"].startswith("email_body.txt::")
    assert len(kwargs["external_file_id"]) > len("email_body.txt::")
    assert kwargs["purpose"] == "extract"


def test_external_file_id_deterministic_for_same_content(mock_client: MagicMock):
    """Two parse calls with the same (filename, content) produce the SAME
    external_file_id — the hash suffix is content-derived, so repeated
    uploads of an identical payload are idempotent from LlamaCloud's
    uniqueness-constraint perspective."""
    mock_client.extract.create.return_value = _job("COMPLETED", result=_VALID_RESULT)
    parse_document(b"same-bytes", filename="doc.pdf")
    first_id = mock_client.files.create.call_args.kwargs["external_file_id"]

    mock_client.files.create.reset_mock()
    mock_client.files.create.return_value = SimpleNamespace(id="dfl-test-2")
    mock_client.extract.create.return_value = _job("COMPLETED", result=_VALID_RESULT)
    parse_document(b"same-bytes", filename="doc.pdf")
    second_id = mock_client.files.create.call_args.kwargs["external_file_id"]

    assert first_id == second_id


def test_external_file_id_differs_for_different_content(mock_client: MagicMock):
    """Two parse calls with the same filename but different content bytes
    produce DIFFERENT external_file_ids — re-issuing the same filename
    with a different payload must not collide with an earlier upload."""
    mock_client.extract.create.return_value = _job("COMPLETED", result=_VALID_RESULT)
    parse_document(b"payload-a", filename="doc.pdf")
    first_id = mock_client.files.create.call_args.kwargs["external_file_id"]

    mock_client.files.create.reset_mock()
    mock_client.files.create.return_value = SimpleNamespace(id="dfl-test-2")
    mock_client.extract.create.return_value = _job("COMPLETED", result=_VALID_RESULT)
    parse_document(b"payload-b", filename="doc.pdf")
    second_id = mock_client.files.create.call_args.kwargs["external_file_id"]

    assert first_id != second_id
    assert first_id.startswith("doc.pdf::")
    assert second_id.startswith("doc.pdf::")


def test_long_text_input_emits_truncation_warning(mock_client: MagicMock, caplog):
    mock_client.extract.create.return_value = _job("COMPLETED", result=_VALID_RESULT)
    big_text = b"x" * 70_000
    with caplog.at_level("WARNING", logger="order_intake_agent.backend.tools.document_parser.parser"):
        parse_document(big_text, filename="huge_email.txt")
    assert any("silently truncates" in rec.message for rec in caplog.records)


# Translator — SDK exceptions → typed parser exceptions (parametrized)

@pytest.mark.parametrize(
    "sdk_cls, status_code, expected_cls, expected_category",
    [
        # 4xx caller-bug — ParseFatalError
        (BadRequestError,           400, ParseBadInputError,        ParseFatalError),
        (AuthenticationError,       401, ParseAuthError,            ParseFatalError),
        (PermissionDeniedError,     403, ParseAuthError,            ParseFatalError),
        (NotFoundError,             404, ParseNotFoundError,        ParseFatalError),
        (UnprocessableEntityError,  422, ParseBadInputError,        ParseFatalError),
        # 429 + 5xx — ParseRetryableError
        (RateLimitError,            429, ParseRateLimitError,       ParseRetryableError),
        (InternalServerError,       500, ParseServerError,          ParseRetryableError),
    ],
    ids=lambda v: getattr(v, "__name__", str(v)),
)
def test_translator_maps_named_status_subclasses(
    mock_client: MagicMock, sdk_cls, status_code, expected_cls, expected_category,
):
    """Each SDK status-error subclass maps to the right typed exception AND
    the right caller-decision category (Retryable vs Fatal)."""
    mock_client.extract.create.side_effect = _bare_sdk_exc(sdk_cls, status_code=status_code)

    with pytest.raises(expected_cls) as excinfo:
        parse_document(b"x", filename="po.pdf")

    err = excinfo.value
    assert isinstance(err, expected_category), (
        f"{type(err).__name__} should be a {expected_category.__name__}"
    )
    assert err.stage == "extract.create"


def test_translator_maps_402_via_status_code(mock_client: MagicMock):
    """402 has no SDK subclass — translator dispatches by status_code."""
    mock_client.extract.create.side_effect = _bare_sdk_exc(APIStatusError, status_code=402)
    with pytest.raises(ParseQuotaExhaustedError) as excinfo:
        parse_document(b"x", filename="po.pdf")
    assert isinstance(excinfo.value, ParseFatalError)
    assert excinfo.value.stage == "extract.create"


def test_translator_maps_413_to_bad_input(mock_client: MagicMock):
    """413 Payload Too Large has no SDK subclass — should map to bad-input."""
    mock_client.extract.create.side_effect = _bare_sdk_exc(APIStatusError, status_code=413)
    with pytest.raises(ParseBadInputError) as excinfo:
        parse_document(b"x", filename="po.pdf")
    assert excinfo.value.status_code == 413


def test_translator_maps_503_to_server_error(mock_client: MagicMock):
    """503 has no dedicated subclass but is in the 5xx range — should retry."""
    mock_client.extract.create.side_effect = _bare_sdk_exc(APIStatusError, status_code=503)
    with pytest.raises(ParseServerError) as excinfo:
        parse_document(b"x", filename="po.pdf")
    assert isinstance(excinfo.value, ParseRetryableError)
    assert excinfo.value.status_code == 503


def test_translator_maps_connection_error(mock_client: MagicMock):
    mock_client.extract.create.side_effect = _bare_sdk_exc(APIConnectionError)
    with pytest.raises(ParseConnectionError) as excinfo:
        parse_document(b"x", filename="po.pdf")
    assert isinstance(excinfo.value, ParseRetryableError)


def test_translator_maps_api_timeout_error_as_connection_error(mock_client: MagicMock):
    """APITimeoutError is a subclass of APIConnectionError — should map the same way."""
    mock_client.extract.create.side_effect = _bare_sdk_exc(APITimeoutError)
    with pytest.raises(ParseConnectionError) as excinfo:
        parse_document(b"x", filename="po.pdf")
    assert isinstance(excinfo.value, ParseRetryableError)


def test_translator_maps_response_validation_error(mock_client: MagicMock):
    """APIResponseValidationError → ParseBadInputError (server returned malformed JSON)."""
    mock_client.extract.create.side_effect = _bare_sdk_exc(APIResponseValidationError)
    with pytest.raises(ParseBadInputError) as excinfo:
        parse_document(b"x", filename="po.pdf")
    assert isinstance(excinfo.value, ParseFatalError)


# Stage attribution

def test_rate_limit_at_files_create_carries_correct_stage(mock_client: MagicMock):
    """A 429 during upload should report stage='files.create', not extract.*"""
    mock_client.files.create.side_effect = _bare_sdk_exc(RateLimitError, status_code=429)
    with pytest.raises(ParseRateLimitError) as excinfo:
        parse_document(b"x", filename="po.pdf")
    assert excinfo.value.stage == "files.create"


def test_404_during_polling_carries_job_id_and_stage(mock_client: MagicMock):
    """If the file expires (48h cache) mid-poll, we should know which job was orphaned."""
    mock_client.extract.create.return_value = _job("PENDING")
    mock_client.extract.get.side_effect = _bare_sdk_exc(NotFoundError, status_code=404)
    with pytest.raises(ParseNotFoundError) as excinfo:
        parse_document(b"x", filename="po.pdf", timeout_s=5, poll_interval_s=0.05)
    assert excinfo.value.stage == "extract.get"
    assert excinfo.value.job_id == "ext-1"


# str() / repr() contract

def test_parse_error_str_renders_structured_fields():
    exc = ParseError(
        "boom", stage="extract.create", job_id="ext-99", detail="upstream 502",
    )
    rendered = str(exc)
    assert "boom" in rendered
    assert "extract.create" in rendered
    assert "ext-99" in rendered
    assert "upstream 502" in rendered
