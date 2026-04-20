"""Unit tests for backend.tools.document_classifier. Fully mocked — no network."""

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
    ClassifyAuthError,
    ClassifyBadInputError,
    ClassifyConnectionError,
    ClassifyError,
    ClassifyFatalError,
    ClassifyNotFoundError,
    ClassifyQuotaExhaustedError,
    ClassifyRateLimitError,
    ClassifyRetryableError,
    ClassifyServerError,
)
from backend.tools.document_classifier import (
    ClassifiedDocument,
    ClassifyFailedError,
    ClassifyTimeoutError,
    classify_document,
    detect_format,
)
from backend.tools.document_classifier import classifier as dc


# Fixtures

def _result(*, type_: str = "purchase_order", confidence: float = 0.92,
            reasoning: str = "Document is titled 'Purchase Order'.") -> SimpleNamespace:
    return SimpleNamespace(type=type_, confidence=confidence, reasoning=reasoning)


def _job(status: str, *, job_id: str = "clf-1", result: SimpleNamespace | None = None,
         error_message: str | None = None):
    return SimpleNamespace(
        id=job_id,
        status=status,
        result=result,
        error_message=error_message,
    )


def _bare_sdk_exc(cls, *, status_code: int | None = None):
    # __new__ bypass — the real SDK builds these from an httpx.Response
    # which is awkward to synthesize; we just need isinstance() + status_code.
    exc = cls.__new__(cls)
    if status_code is not None:
        exc.status_code = status_code
    return exc


@pytest.fixture
def mock_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    client = MagicMock()
    client.files.create.return_value = SimpleNamespace(id="dfl-test-1")
    monkeypatch.setattr(dc, "_client", client)
    return client


# Format detection

@pytest.mark.parametrize(
    "filename, expected",
    [
        ("invoice.PDF", "pdf"),
        ("photo.jpg", "image"),
        ("scan.TIFF", "image"),
        ("order.xlsx", "xlsx"),
        ("old_order.xls", "xls"),
        ("export.csv", "csv"),
        ("tabular.tsv", "tsv"),
        ("purchase.xml", "xml"),
        ("edifact_834.edifact", "edi"),
        ("x12_850.x12", "edi"),
        ("message.eml", "email"),
        ("outlook.msg", "email"),
        ("body.txt", "text"),
        ("README", "unknown"),
        ("weird.zxcv", "unknown"),
    ],
)
def test_detect_format_by_extension(filename: str, expected: str) -> None:
    assert detect_format(filename) == expected


# classify_document — happy path + terminal-status handling

def test_classify_returns_classified_document_on_completed(mock_client: MagicMock) -> None:
    mock_client.classify.create.return_value = _job(
        "COMPLETED",
        result=_result(type_="purchase_order", confidence=0.95, reasoning="Has PO#"),
    )
    result = classify_document(b"%PDF-1.4 fake", filename="po.pdf")
    assert isinstance(result, ClassifiedDocument)
    assert result.document_intent == "purchase_order"
    assert result.intent_confidence == pytest.approx(0.95)
    assert result.document_format == "pdf"
    assert result.mime_type == "application/pdf"
    assert result.byte_size == len(b"%PDF-1.4 fake")
    assert result.filename == "po.pdf"
    assert result.classify_job_id == "clf-1"


def test_classify_accepts_success_status_from_overview_taxonomy(mock_client: MagicMock) -> None:
    """LlamaClassify docs use both COMPLETED (typed reference) and SUCCESS
    (product overview) as success terminals. Accept both."""
    mock_client.classify.create.return_value = _job(
        "SUCCESS",
        result=_result(type_="invoice", confidence=0.88, reasoning="Invoice number present."),
    )
    result = classify_document(b"dummy", filename="bill.pdf")
    assert result.document_intent == "invoice"


def test_classify_propagates_timeout(mock_client: MagicMock) -> None:
    mock_client.classify.create.return_value = _job("PENDING")
    mock_client.classify.get.return_value = _job("PENDING")
    with pytest.raises(ClassifyTimeoutError) as excinfo:
        classify_document(b"x", filename="po.pdf", timeout_s=0.5, poll_interval_s=0.05)
    err = excinfo.value
    assert err.job_id == "clf-1"
    assert err.stage == "classify.get"
    assert err.timeout_s == 0.5
    assert err.last_status == "PENDING"
    assert err.elapsed_s >= 0.5


def test_classify_propagates_failure(mock_client: MagicMock) -> None:
    mock_client.classify.create.return_value = _job(
        "FAILED", result=None, error_message="OCR failure",
    )
    with pytest.raises(ClassifyFailedError) as excinfo:
        classify_document(b"x", filename="po.pdf")
    err = excinfo.value
    assert err.status == "FAILED"
    assert err.job_id == "clf-1"
    assert err.stage == "classify.get"
    assert isinstance(err, ClassifyFatalError)


def test_classify_no_rule_matched_raises_failed(mock_client: MagicMock) -> None:
    """If LlamaClassify completes but result.type is null, surface as a
    failed classification so callers can route to human review."""
    mock_client.classify.create.return_value = _job(
        "COMPLETED",
        result=SimpleNamespace(type=None, confidence=0.0, reasoning="no rule matched"),
    )
    with pytest.raises(ClassifyFailedError) as excinfo:
        classify_document(b"x", filename="po.pdf")
    assert "no rule matched" in str(excinfo.value.detail)


# classify_document — configuration shape

def test_files_create_uses_purpose_classify(mock_client: MagicMock) -> None:
    """Upload MUST use purpose='classify' (not 'extract' — that was legacy)."""
    mock_client.classify.create.return_value = _job("COMPLETED", result=_result())
    classify_document(b"x", filename="po.pdf")

    _, kwargs = mock_client.files.create.call_args
    assert kwargs["purpose"] == "classify"
    # external_file_id carries the filename up front plus a uniqueness suffix
    # (LlamaCloud enforces uniqueness on (project_id, external_file_id)).
    assert kwargs["external_file_id"].startswith("po.pdf::")
    assert len(kwargs["external_file_id"]) > len("po.pdf::")


def test_classify_create_sends_rules_and_fast_mode(mock_client: MagicMock) -> None:
    mock_client.classify.create.return_value = _job("COMPLETED", result=_result())
    classify_document(b"x", filename="po.pdf")

    _, kwargs = mock_client.classify.create.call_args
    cfg = kwargs["configuration"]
    assert cfg["mode"] == "FAST"
    # All 8 intents should be present as rule types.
    rule_types = {r["type"] for r in cfg["rules"]}
    assert rule_types == {
        "purchase_order", "po_confirmation", "shipping_notice", "invoice",
        "inquiry", "complaint", "spam", "other",
    }


# Translator — SDK exceptions → typed classifier exceptions (parametrized)

@pytest.mark.parametrize(
    "sdk_cls, status_code, expected_cls, expected_category",
    [
        (BadRequestError,           400, ClassifyBadInputError,  ClassifyFatalError),
        (AuthenticationError,       401, ClassifyAuthError,      ClassifyFatalError),
        (PermissionDeniedError,     403, ClassifyAuthError,      ClassifyFatalError),
        (NotFoundError,             404, ClassifyNotFoundError,  ClassifyFatalError),
        (UnprocessableEntityError,  422, ClassifyBadInputError,  ClassifyFatalError),
        (RateLimitError,            429, ClassifyRateLimitError, ClassifyRetryableError),
        (InternalServerError,       500, ClassifyServerError,    ClassifyRetryableError),
    ],
    ids=lambda v: getattr(v, "__name__", str(v)),
)
def test_translator_maps_named_status_subclasses(
    mock_client: MagicMock, sdk_cls, status_code, expected_cls, expected_category,
) -> None:
    mock_client.classify.create.side_effect = _bare_sdk_exc(sdk_cls, status_code=status_code)
    with pytest.raises(expected_cls) as excinfo:
        classify_document(b"x", filename="po.pdf")
    err = excinfo.value
    assert isinstance(err, expected_category)
    assert err.stage == "classify.create"


def test_translator_maps_402_via_status_code(mock_client: MagicMock) -> None:
    mock_client.classify.create.side_effect = _bare_sdk_exc(APIStatusError, status_code=402)
    with pytest.raises(ClassifyQuotaExhaustedError) as excinfo:
        classify_document(b"x", filename="po.pdf")
    assert isinstance(excinfo.value, ClassifyFatalError)


def test_translator_maps_413_to_bad_input(mock_client: MagicMock) -> None:
    mock_client.classify.create.side_effect = _bare_sdk_exc(APIStatusError, status_code=413)
    with pytest.raises(ClassifyBadInputError) as excinfo:
        classify_document(b"x", filename="po.pdf")
    assert excinfo.value.status_code == 413


def test_translator_maps_connection_error(mock_client: MagicMock) -> None:
    mock_client.classify.create.side_effect = _bare_sdk_exc(APIConnectionError)
    with pytest.raises(ClassifyConnectionError) as excinfo:
        classify_document(b"x", filename="po.pdf")
    assert isinstance(excinfo.value, ClassifyRetryableError)


def test_translator_maps_api_timeout_error_as_connection_error(mock_client: MagicMock) -> None:
    mock_client.classify.create.side_effect = _bare_sdk_exc(APITimeoutError)
    with pytest.raises(ClassifyConnectionError):
        classify_document(b"x", filename="po.pdf")


def test_translator_maps_response_validation_error(mock_client: MagicMock) -> None:
    mock_client.classify.create.side_effect = _bare_sdk_exc(APIResponseValidationError)
    with pytest.raises(ClassifyBadInputError) as excinfo:
        classify_document(b"x", filename="po.pdf")
    assert isinstance(excinfo.value, ClassifyFatalError)


# Stage attribution

def test_rate_limit_at_files_create_carries_correct_stage(mock_client: MagicMock) -> None:
    mock_client.files.create.side_effect = _bare_sdk_exc(RateLimitError, status_code=429)
    with pytest.raises(ClassifyRateLimitError) as excinfo:
        classify_document(b"x", filename="po.pdf")
    assert excinfo.value.stage == "files.create"


def test_404_during_polling_carries_job_id_and_stage(mock_client: MagicMock) -> None:
    mock_client.classify.create.return_value = _job("PENDING")
    mock_client.classify.get.side_effect = _bare_sdk_exc(NotFoundError, status_code=404)
    with pytest.raises(ClassifyNotFoundError) as excinfo:
        classify_document(b"x", filename="po.pdf", timeout_s=5, poll_interval_s=0.05)
    assert excinfo.value.stage == "classify.get"
    assert excinfo.value.job_id == "clf-1"


# str() / repr() contract

def test_classify_error_str_renders_structured_fields() -> None:
    exc = ClassifyError(
        "boom", stage="classify.create", job_id="clf-99", detail="upstream 502",
    )
    rendered = str(exc)
    assert "boom" in rendered
    assert "classify.create" in rendered
    assert "clf-99" in rendered
    assert "upstream 502" in rendered
