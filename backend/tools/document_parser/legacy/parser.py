"""parse_document — the LlamaExtract-backed document parser.

Sync block-and-poll wrapper around the llama_cloud SDK. Takes raw bytes of any
supported format (PDF, XLSX, CSV, XML, image, plain text / email body) and
returns a ParsedDocument carrying classification + per-order line items.

SDK exception → typed parser exception translation lives in
``_translate_api_error``. Every call site catches ``APIError`` (the SDK's base
HTTP-error class), runs the translator, and re-raises.
"""

from __future__ import annotations

import io
import time

from dotenv import load_dotenv
from llama_cloud import (
    APIConnectionError,
    APIError,
    APIResponseValidationError,
    APIStatusError,
    LlamaCloud,
)
from llama_cloud.types.extract_configuration_param import ExtractConfigurationParam

# Populate LLAMA_CLOUD_API_KEY (and any other config) from a project-root .env
# before the SDK client is constructed. Real env vars still win — load_dotenv
# does not override values already set in the process environment.
load_dotenv()

from backend.tools.document_classifier.format_detection import guess_mime
from backend.utils.exceptions import (
    ParseAuthError,
    ParseBadInputError,
    ParseConnectionError,
    ParseError,
    ParseFailedError,
    ParseNotFoundError,
    ParseQuotaExhaustedError,
    ParseRateLimitError,
    ParseServerError,
    ParseStage,
    ParseTimeoutError,
)
from backend.models.parsed_document import ParsedDocument
from backend.prompts.document_parser import SYSTEM_PROMPT
from backend.utils.logging import get_logger, log_llama_extract_op

_TERMINAL_STATUSES = ("COMPLETED", "FAILED", "CANCELLED")
_TEXT_TRUNCATION_WARNING_BYTES = 60_000  # LlamaExtract silently truncates >64 KB / page

# Logger name pinned to the pre-move path so log consumers (test caplog,
# the parser.log file sink) don't move when the module moves under legacy/.
_log = get_logger("backend.tools.document_parser.parser")
_client: LlamaCloud | None = None


def _get_client() -> LlamaCloud:
    """Lazily construct and cache the LlamaCloud client."""
    global _client
    if _client is None:
        _log.info("llama_client_init")
        _client = LlamaCloud()
    return _client


def _translate_api_error(
    exc: APIError,
    *,
    stage: ParseStage,
    job_id: str | None = None,
) -> ParseError:
    """Map a raw llama_cloud APIError to our typed ParseError subclass."""
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
        return ParseConnectionError(stage=stage, job_id=job_id, detail=detail)

    if isinstance(exc, APIResponseValidationError):
        return ParseBadInputError(stage=stage, job_id=job_id, detail=detail)

    if isinstance(exc, APIStatusError):
        sc = status_code
        if sc == 429:
            return ParseRateLimitError(stage=stage, job_id=job_id, detail=detail)
        if sc in (401, 403):
            return ParseAuthError(stage=stage, status_code=sc, job_id=job_id, detail=detail)
        if sc == 402:
            return ParseQuotaExhaustedError(stage=stage, job_id=job_id, detail=detail)
        if sc == 404:
            return ParseNotFoundError(stage=stage, job_id=job_id, detail=detail)
        if sc in (400, 413, 422):
            return ParseBadInputError(stage=stage, status_code=sc, job_id=job_id, detail=detail)
        if sc is not None and 500 <= sc < 600:
            return ParseServerError(stage=stage, status_code=sc, job_id=job_id, detail=detail)

    return ParseError(
        f"unhandled LlamaCloud error ({type(exc).__name__})",
        stage=stage,
        job_id=job_id,
        detail=detail,
    )


def parse_document(
    content: bytes,
    filename: str,
    extra_hint: str | None = None,
    timeout_s: float = 60.0,
    poll_interval_s: float = 2.0,
) -> ParsedDocument:
    """Parse raw document bytes into a ParsedDocument.

    Args:
        content: Raw bytes of the document. Format inferred from filename.
        filename: Used as external_file_id; LlamaExtract uses the extension to
            pick the right parser path (PDF/XLSX/CSV/XML/PNG/JPG/TXT/...).
        extra_hint: Optional free-text appended to the global system prompt.
        timeout_s: Wall-clock budget for the whole submit + poll cycle.
        poll_interval_s: Seconds between status polls.

    Returns:
        ParsedDocument with classification, classification_rationale, and
        zero-or-more sub_documents.

    Raises:
        ParseTimeoutError / ParseFailedError / ParseRetryableError /
        ParseFatalError / ParseError — see ``backend/utils/exceptions.py``.
    """
    byte_count = len(content)
    _log.info(
        "parse_document_start",
        filename=filename,
        bytes=byte_count,
        timeout_s=timeout_s,
        poll_interval_s=poll_interval_s,
        has_hint=extra_hint is not None,
    )
    _log.debug(
        "input_fingerprint",
        filename=filename,
        extension=filename.rsplit(".", 1)[-1].lower() if "." in filename else "",
        bytes=byte_count,
    )

    if filename.lower().endswith((".txt", ".eml")) and byte_count > _TEXT_TRUNCATION_WARNING_BYTES:
        _log.warning(
            "text_input_may_truncate",
            filename=filename,
            bytes=byte_count,
            threshold=_TEXT_TRUNCATION_WARNING_BYTES,
            note="LlamaExtract silently truncates beyond 64KB/page; strip quoted reply chains.",
        )

    system_prompt = SYSTEM_PROMPT
    if extra_hint:
        system_prompt += f"\n\nAdditional context for this document:\n{extra_hint}"
        _log.debug(
            "system_prompt_extended",
            base_len=len(SYSTEM_PROMPT),
            hint_len=len(extra_hint),
            final_len=len(system_prompt),
        )

    _log.debug("client_resolve_start")
    client = _get_client()
    _log.debug("client_resolve_complete", cached=True)

    # ---- Stage 1: upload bytes. -------------------------------------------
    # Pass a (filename, content, content_type) tuple so httpx multipart sends
    # a proper filename+type — BytesIO has no .name, which makes LlamaCloud
    # reject the extract job with `Unsupported file type: None`.
    mime_type = guess_mime(filename)
    _log.debug(
        "stage_begin",
        stage="files.create",
        filename=filename,
        bytes=byte_count,
        mime_type=mime_type,
    )
    upload_start = time.monotonic()
    try:
        file_obj = client.files.create(
            file=(filename, io.BytesIO(content), mime_type),
            purpose="extract",
            external_file_id=filename,
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
    log_llama_extract_op(
        "files.create",
        stage="files.create",
        duration_ms=upload_ms,
        file_id=file_obj.id,
        bytes=byte_count,
    )

    config: ExtractConfigurationParam = {
        "data_schema": ParsedDocument.model_json_schema(),
        "extraction_target": "per_doc",
        "tier": "agentic",
        "system_prompt": system_prompt,
        "confidence_scores": False,
        "cite_sources": False,
    }
    _log.debug(
        "extract_config_built",
        extraction_target=config["extraction_target"],
        tier=config["tier"],
        schema_field_count=len(config["data_schema"].get("properties", {})),
    )

    # ---- Stage 2: submit extract job. -------------------------------------
    _log.debug("stage_begin", stage="extract.create", file_id=file_obj.id)
    submit_start = time.monotonic()
    try:
        job = client.extract.create(file_input=file_obj.id, configuration=config)
    except APIError as exc:
        _log.error(
            "extract_create_failed",
            file_id=file_obj.id,
            exc_type=type(exc).__name__,
            exc_info=True,
        )
        raise _translate_api_error(exc, stage="extract.create") from exc
    submit_ms = (time.monotonic() - submit_start) * 1000
    _log.debug(
        "stage_end",
        stage="extract.create",
        job_id=job.id,
        initial_status=job.status,
        duration_ms=submit_ms,
    )
    log_llama_extract_op(
        "extract.create",
        stage="extract.create",
        duration_ms=submit_ms,
        job_id=job.id,
        status=job.status,
    )

    # ---- Stage 3: poll to completion. -------------------------------------
    _log.debug(
        "stage_begin",
        stage="extract.get",
        job_id=job.id,
        timeout_s=timeout_s,
        poll_interval_s=poll_interval_s,
    )
    start = time.monotonic()
    deadline = start + timeout_s
    poll_count = 0
    previous_status = job.status
    while job.status not in _TERMINAL_STATUSES:
        elapsed = time.monotonic() - start
        if time.monotonic() > deadline:
            _log.error(
                "extract_poll_timeout",
                job_id=job.id,
                timeout_s=timeout_s,
                elapsed_s=elapsed,
                last_status=job.status,
                polls=poll_count,
            )
            raise ParseTimeoutError(
                job_id=job.id,
                timeout_s=timeout_s,
                elapsed_s=elapsed,
                last_status=job.status,
            )
        _log.debug(
            "poll_sleep",
            job_id=job.id,
            poll_interval_s=poll_interval_s,
            remaining_s=max(0.0, deadline - time.monotonic()),
        )
        time.sleep(poll_interval_s)
        poll_count += 1
        try:
            job = client.extract.get(job.id)
        except APIError as exc:
            _log.error(
                "extract_get_failed",
                job_id=job.id,
                polls=poll_count,
                exc_type=type(exc).__name__,
                exc_info=True,
            )
            raise _translate_api_error(exc, stage="extract.get", job_id=job.id) from exc
        _log.debug(
            "extract_poll_tick",
            job_id=job.id,
            status=job.status,
            previous_status=previous_status,
            status_changed=job.status != previous_status,
            polls=poll_count,
            elapsed_s=time.monotonic() - start,
        )
        previous_status = job.status

    total_ms = (time.monotonic() - start) * 1000
    _log.debug(
        "stage_end",
        stage="extract.get",
        job_id=job.id,
        final_status=job.status,
        polls=poll_count,
        duration_ms=total_ms,
    )
    log_llama_extract_op(
        "extract.poll",
        stage="extract.get",
        duration_ms=total_ms,
        job_id=job.id,
        status=job.status,
        polls=poll_count,
    )

    if job.status != "COMPLETED":
        err_detail = getattr(job, "error", None) or getattr(job, "error_message", None)
        _log.error(
            "extract_job_terminal_failure",
            job_id=job.id,
            status=job.status,
            error_detail=str(err_detail) if err_detail else None,
        )
        raise ParseFailedError(
            job_id=job.id,
            status=job.status,
            detail=err_detail,
        )

    # ---- Stage 4: validate against Pydantic schema. -----------------------
    _log.debug("stage_begin", stage="validation", job_id=job.id)
    validation_start = time.monotonic()
    try:
        result = ParsedDocument.model_validate(job.extract_result)
    except Exception:
        _log.error(
            "parsed_document_validation_failed",
            job_id=job.id,
            exc_info=True,
        )
        raise
    validation_ms = (time.monotonic() - validation_start) * 1000
    _log.debug(
        "stage_end",
        stage="validation",
        job_id=job.id,
        duration_ms=validation_ms,
        classification=result.classification,
        sub_document_count=len(result.sub_documents),
    )

    _log.info(
        "parse_document_complete",
        filename=filename,
        job_id=job.id,
        classification=result.classification,
        sub_document_count=len(result.sub_documents),
        polls=poll_count,
        duration_ms=(time.monotonic() - upload_start) * 1000,
    )
    return result
