"""classify_document — LlamaClassify-backed document classifier.

Sync block-and-poll wrapper around ``client.classify.*``. Takes raw bytes +
filename, returns a :class:`ClassifiedDocument` carrying the business intent
(LLM-decided) and data format (deterministic from extension).

Pipeline:
  1. Local format detection (``detect_format`` / ``guess_mime``).
  2. ``client.files.create(purpose="classify")`` — upload bytes.
  3. ``client.classify.create(file_input=..., configuration={rules, mode})``.
  4. Poll ``client.classify.get(job.id)`` until terminal.
  5. Validate ``job.result`` + source metadata → ``ClassifiedDocument``.

SDK ``APIError`` → typed ``ClassifyError`` translation lives in
``_translate_api_error``.
"""

from __future__ import annotations

import io
import time
import uuid

from dotenv import load_dotenv
from llama_cloud import (
    APIConnectionError,
    APIError,
    APIResponseValidationError,
    APIStatusError,
    LlamaCloud,
)
from llama_cloud.types.classify_configuration_param import ClassifyConfigurationParam

# Populate LLAMA_CLOUD_API_KEY etc. from a project-root .env before the SDK
# client is constructed. Real env vars still win — load_dotenv does not
# override values already set in the process environment.
load_dotenv()

from backend.utils.exceptions import (
    ClassifyAuthError,
    ClassifyBadInputError,
    ClassifyConnectionError,
    ClassifyError,
    ClassifyFailedError,
    ClassifyNotFoundError,
    ClassifyQuotaExhaustedError,
    ClassifyRateLimitError,
    ClassifyServerError,
    ClassifyStage,
    ClassifyTimeoutError,
)
from backend.models.classified_document import ClassifiedDocument
from backend.prompts.document_classifier import CLASSIFY_RULES
from backend.tools.document_classifier.format_detection import (
    detect_format,
    guess_mime,
)
from backend.utils.logging import get_logger

# LlamaClassify status vocabulary is not 100% consistent across docs (the
# product overview lists PENDING/SUCCESS/ERROR/PARTIAL_SUCCESS/CANCELLED; the
# typed reference lists PENDING/RUNNING/COMPLETED/FAILED). Treat any of the
# below as terminal so we don't poll forever on either taxonomy.
_TERMINAL_STATUSES = frozenset(
    {"COMPLETED", "SUCCESS", "PARTIAL_SUCCESS", "FAILED", "ERROR", "CANCELLED"}
)
_SUCCESS_STATUSES = frozenset({"COMPLETED", "SUCCESS", "PARTIAL_SUCCESS"})

# Formats LlamaParse's pre-classify parse step extracts no `.text` channel from
# when given their true IANA MIME type (CSV / EDI / XML / EML / TXT come back
# as structured-table JSON or empty). Upload them as ``text/plain`` so they
# take LlamaParse's plain-text code path, which feeds the FAST classifier.
# The accurate MIME type is still reported on the returned ClassifiedDocument.
_TEXT_FAMILY_UPLOAD_MIME = "text/plain"
_TEXT_FAMILY_FORMATS = frozenset({"csv", "tsv", "edi", "xml", "email", "text"})

# EDI X12 files use ``~`` (and sometimes a newline) as the segment terminator
# and ``*`` as the element separator, so a 2-4 KB transaction set is typically
# one enormous line. LlamaParse's plain-text path needs line-broken input to
# extract useful text — classify otherwise fails with "Parse job has no text
# content available". Normalise segment terminators to ``\n`` before upload;
# the output ClassifiedDocument still reports the accurate IANA MIME type.
_EDI_SEGMENT_TERMINATORS = (b"~", b"\r\n", b"\r")


def _normalize_edi_for_plaintext(content: bytes) -> bytes:
    """Return ``content`` with EDI segment terminators rewritten as ``\\n``."""
    out = content
    for term in _EDI_SEGMENT_TERMINATORS:
        if term != b"\n":
            out = out.replace(term, b"\n")
    # Collapse runs of blank lines that the replacement can introduce.
    while b"\n\n\n" in out:
        out = out.replace(b"\n\n\n", b"\n\n")
    return out

_log = get_logger(__name__)
_client: LlamaCloud | None = None


def _get_client() -> LlamaCloud:
    """Lazily construct and cache the LlamaCloud client."""
    global _client
    if _client is None:
        _log.info("llama_client_init", tool="document_classifier")
        _client = LlamaCloud()
    return _client


def _translate_api_error(
    exc: APIError,
    *,
    stage: ClassifyStage,
    job_id: str | None = None,
) -> ClassifyError:
    """Map a raw ``llama_cloud.APIError`` to our typed ``ClassifyError`` family.

    Mirrors the translator in the legacy parser (``legacy/parser.py``) with
    Classify-flavoured exception classes and stage vocabulary.
    """
    detail = str(exc)
    status_code = getattr(exc, "status_code", None)
    _log.warning(
        "api_error_translating",
        stage=stage,
        job_id=job_id,
        exc_type=type(exc).__name__,
        status_code=status_code,
    )

    if isinstance(exc, APIConnectionError):
        return ClassifyConnectionError(stage=stage, job_id=job_id, detail=detail)

    if isinstance(exc, APIResponseValidationError):
        return ClassifyBadInputError(stage=stage, job_id=job_id, detail=detail)

    if isinstance(exc, APIStatusError):
        sc = status_code
        if sc == 429:
            return ClassifyRateLimitError(stage=stage, job_id=job_id, detail=detail)
        if sc in (401, 403):
            return ClassifyAuthError(stage=stage, status_code=sc, job_id=job_id, detail=detail)
        if sc == 402:
            return ClassifyQuotaExhaustedError(stage=stage, job_id=job_id, detail=detail)
        if sc == 404:
            return ClassifyNotFoundError(stage=stage, job_id=job_id, detail=detail)
        if sc in (400, 413, 422):
            return ClassifyBadInputError(stage=stage, status_code=sc, job_id=job_id, detail=detail)
        if sc is not None and 500 <= sc < 600:
            return ClassifyServerError(stage=stage, status_code=sc, job_id=job_id, detail=detail)

    return ClassifyError(
        f"unhandled LlamaCloud error ({type(exc).__name__})",
        stage=stage,
        job_id=job_id,
        detail=detail,
    )


def classify_document(
    content: bytes,
    filename: str,
    *,
    timeout_s: float = 60.0,
    poll_interval_s: float = 2.0,
) -> ClassifiedDocument:
    """Classify raw document bytes into a :class:`ClassifiedDocument`.

    Args:
        content: Raw bytes of the document. Format inferred from ``filename``.
        filename: Used as ``external_file_id`` and drives format/MIME detection.
        timeout_s: Wall-clock budget for the whole submit + poll cycle.
        poll_interval_s: Seconds between status polls.

    Returns:
        ``ClassifiedDocument`` carrying document_intent + confidence + reasoning
        (from LlamaClassify), document_format + mime_type (from the filename),
        and source metadata (filename, byte_size, classify_job_id).

    Raises:
        ``ClassifyTimeoutError`` / ``ClassifyFailedError`` / ``ClassifyError``
        subclasses — see ``backend/utils/exceptions.py``.
    """
    byte_count = len(content)
    document_format = detect_format(filename)
    mime_type = guess_mime(filename)

    # For text-family formats, override the upload Content-Type to text/plain
    # so LlamaParse's plain-text path runs (the CSV/XML/EDI paths return
    # structured JSON with no .text channel, which makes FAST classify fail
    # with "Parse job has no text content available"). The mime_type returned
    # on ClassifiedDocument still reflects the accurate IANA type.
    upload_mime = (
        _TEXT_FAMILY_UPLOAD_MIME
        if document_format in _TEXT_FAMILY_FORMATS
        else mime_type
    )

    # EDI content is typically one long line of ~-separated segments; rewrite
    # segment terminators to newlines so LlamaParse's plain-text path sees
    # usable structure. The returned ClassifiedDocument reports the original
    # byte_size (not the post-normalisation size) — we are only transforming
    # what LlamaCloud sees, not changing what the file ~is~.
    upload_content = (
        _normalize_edi_for_plaintext(content)
        if document_format == "edi"
        else content
    )

    # LlamaParse content-sniffs the payload and routes EDI (``ISA*`` magic)
    # through a no-text path even when we send text/plain. Strip the
    # recognisable extension at the multipart boundary so LlamaParse treats
    # it as plain text. The true filename stays on the returned
    # ClassifiedDocument because we store it from the function arg, not
    # from ``file_obj``.
    upload_filename = (
        filename.rsplit(".", 1)[0] + ".txt"
        if document_format == "edi"
        else filename
    )

    # LlamaCloud enforces uniqueness on (project_id, external_file_id), so we
    # suffix a random token to keep re-runs of the same filename from
    # tripping the unique constraint with a 400. The base filename stays
    # visible up front for log readability.
    external_file_id = f"{filename}::{uuid.uuid4().hex[:12]}"

    _log.info(
        "classify_document_start",
        filename=filename,
        bytes=byte_count,
        document_format=document_format,
        mime_type=mime_type,
        upload_mime=upload_mime,
        external_file_id=external_file_id,
        timeout_s=timeout_s,
        poll_interval_s=poll_interval_s,
    )

    client = _get_client()

    # ---- Stage 1: upload bytes. -------------------------------------------
    # (filename, BytesIO, content_type) tuple so httpx multipart sends a proper
    # filename + Content-Type. A BytesIO with no .name causes LlamaCloud to
    # reject with `Unsupported file type: None`.
    _log.debug(
        "stage_begin",
        stage="files.create",
        filename=filename,
        bytes=byte_count,
        upload_mime=upload_mime,
    )
    upload_start = time.monotonic()
    try:
        file_obj = client.files.create(
            file=(upload_filename, io.BytesIO(upload_content), upload_mime),
            purpose="classify",
            external_file_id=external_file_id,
        )
    except APIError as exc:
        _log.error(
            "files_create_failed",
            filename=filename,
            exc_type=type(exc).__name__,
            exc_info=True,
        )
        raise _translate_api_error(exc, stage="files.create") from exc
    upload_ms = (time.monotonic() - upload_start) * 1000
    _log.debug(
        "stage_end",
        stage="files.create",
        file_id=file_obj.id,
        duration_ms=upload_ms,
    )

    # ---- Stage 2: submit classify job. ------------------------------------
    # The per-file endpoint (client.classify.create) accepts only mode="FAST";
    # MULTIMODAL is available on the batch classifier.classify endpoint.
    config: ClassifyConfigurationParam = {
        "rules": CLASSIFY_RULES,
        "mode": "FAST",
    }
    _log.debug(
        "stage_begin",
        stage="classify.create",
        file_id=file_obj.id,
        rule_count=len(CLASSIFY_RULES),
    )
    submit_start = time.monotonic()
    try:
        job = client.classify.create(file_input=file_obj.id, configuration=config)
    except APIError as exc:
        _log.error(
            "classify_create_failed",
            file_id=file_obj.id,
            exc_type=type(exc).__name__,
            exc_info=True,
        )
        raise _translate_api_error(exc, stage="classify.create") from exc
    submit_ms = (time.monotonic() - submit_start) * 1000
    _log.debug(
        "stage_end",
        stage="classify.create",
        job_id=job.id,
        initial_status=job.status,
        duration_ms=submit_ms,
    )

    # ---- Stage 3: poll to completion. -------------------------------------
    start = time.monotonic()
    deadline = start + timeout_s
    poll_count = 0
    previous_status = job.status
    while job.status not in _TERMINAL_STATUSES:
        elapsed = time.monotonic() - start
        if time.monotonic() > deadline:
            _log.error(
                "classify_poll_timeout",
                job_id=job.id,
                timeout_s=timeout_s,
                elapsed_s=elapsed,
                last_status=job.status,
                polls=poll_count,
            )
            raise ClassifyTimeoutError(
                job_id=job.id,
                timeout_s=timeout_s,
                elapsed_s=elapsed,
                last_status=job.status,
            )
        time.sleep(poll_interval_s)
        poll_count += 1
        try:
            job = client.classify.get(job.id)
        except APIError as exc:
            _log.error(
                "classify_get_failed",
                job_id=job.id,
                polls=poll_count,
                exc_type=type(exc).__name__,
                exc_info=True,
            )
            raise _translate_api_error(exc, stage="classify.get", job_id=job.id) from exc
        if job.status != previous_status:
            _log.debug(
                "classify_status_change",
                job_id=job.id,
                previous_status=previous_status,
                status=job.status,
                polls=poll_count,
            )
        previous_status = job.status

    if job.status not in _SUCCESS_STATUSES:
        err_detail = getattr(job, "error_message", None) or getattr(job, "error", None)
        _log.error(
            "classify_job_terminal_failure",
            job_id=job.id,
            status=job.status,
            error_detail=str(err_detail) if err_detail else None,
        )
        raise ClassifyFailedError(
            job_id=job.id,
            status=job.status,
            detail=err_detail,
        )

    # ---- Stage 4: validate + assemble. ------------------------------------
    result = getattr(job, "result", None)
    if result is None or getattr(result, "type", None) is None:
        # No rule matched — surface as a failed classification. Callers can
        # decide whether to route to human review or fall back to 'other'.
        _log.error(
            "classify_no_rule_matched",
            job_id=job.id,
            status=job.status,
            has_result=result is not None,
        )
        raise ClassifyFailedError(
            job_id=job.id,
            status=job.status,
            detail="no rule matched (result.type is null)",
        )

    classified = ClassifiedDocument(
        document_intent=result.type,
        intent_confidence=float(result.confidence),
        intent_reasoning=result.reasoning,
        document_format=document_format,
        filename=filename,
        mime_type=mime_type,
        byte_size=byte_count,
        classify_job_id=job.id,
    )

    _log.info(
        "classify_document_complete",
        filename=filename,
        job_id=job.id,
        document_intent=classified.document_intent,
        intent_confidence=classified.intent_confidence,
        document_format=classified.document_format,
        polls=poll_count,
        duration_ms=(time.monotonic() - upload_start) * 1000,
    )
    return classified
