"""Shared typed exceptions raised by backend modules.

Design principles:
- Every exception carries **structured fields** (not just embedded strings)
  so callers can inspect them programmatically:
      except ParseRateLimitError as e:
          schedule_retry(stage=e.stage, after=backoff_for(e.stage))
- `str(exc)` renders a one-line human summary built from those fields,
  suitable for log lines and FastAPI error responses.
- `__repr__` shows every structured field, useful for `repr()` in REPL
  debugging and structured loggers (loguru, structlog).
- The original SDK exception is preserved via `raise ... from exc` at the
  call site — `exc.__cause__` always has the underlying detail.
- A `ParseStage` Literal centralizes the "where in the pipeline" vocabulary
  so callers can `match` on it and dashboards can group by it.

The exception hierarchy organizes by **what the caller should do**, not by
HTTP status code:

    ParseError                           ← base; catch-all for anything unmapped
    ├── ParseTimeoutError                ← our polling exceeded timeout_s; re-poll job_id
    ├── ParseRetryableError              ← transient; retry the same call with backoff
    │   ├── ParseRateLimitError          (429)
    │   ├── ParseServerError             (5xx)
    │   └── ParseConnectionError         (network failure / HTTP-level timeout)
    └── ParseFatalError                  ← do NOT retry; needs human / ops action
        ├── ParseAuthError               (401/403)
        ├── ParseQuotaExhaustedError     (402)
        ├── ParseBadInputError           (400/413/422 + malformed server response)
        ├── ParseNotFoundError           (404; for files this means 48h cache expired
        │                                 → re-upload bytes and resubmit)
        └── ParseFailedError             (job reached terminal FAILED/CANCELLED status)

Callers can therefore handle whole categories without enumerating subclasses:

    try:
        parse_document(...)
    except ParseRetryableError:
        schedule_with_backoff()
    except ParseNotFoundError:
        reupload_and_retry()
    except ParseFatalError as e:
        escalate(stage=e.stage, detail=e.detail)

When a FastAPI exception handler maps these to HTTP responses, it imports
from this single module — no reach-through into tool internals.
"""

from __future__ import annotations

from typing import Literal, Optional

# ---------------------------------------------------------------------------
# Pipeline stage vocabulary
# ---------------------------------------------------------------------------

ParseStage = Literal[
    "files.create",     # uploading bytes to LlamaCloud
    "extract.create",   # submitting the extract job
    "extract.get",      # polling for job status
    "validation",       # ParsedDocument.model_validate on the result
]


# ---------------------------------------------------------------------------
# Base class — every parser exception inherits this
# ---------------------------------------------------------------------------

class ParseError(Exception):
    """Base class for any failure in the document_parser tool.

    All subclasses carry these structured fields so callers never have to
    parse strings:

        message:  short human-readable summary (also the Exception's args[0])
        stage:    which pipeline step failed
        job_id:   LlamaExtract job id if one had been created
        detail:   underlying SDK error message or any other context
    """

    def __init__(
        self,
        message: str = "document parser failed",
        *,
        stage: Optional[ParseStage] = None,
        job_id: Optional[str] = None,
        detail: object | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.stage = stage
        self.job_id = job_id
        self.detail = detail

    def __str__(self) -> str:
        parts: list[str] = [self.message]
        if self.stage is not None:
            parts.append(f"stage={self.stage}")
        if self.job_id is not None:
            parts.append(f"job_id={self.job_id}")
        if self.detail is not None:
            parts.append(f"detail={self.detail!r}")
        return " | ".join(parts)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}("
            f"message={self.message!r}, "
            f"stage={self.stage!r}, "
            f"job_id={self.job_id!r}, "
            f"detail={self.detail!r}"
            f")"
        )


# ---------------------------------------------------------------------------
# Intermediate categories — keyed to caller-side handling strategy
# ---------------------------------------------------------------------------

class ParseRetryableError(ParseError):
    """Transient failure — the caller should retry the same call with backoff.

    Subclasses cover rate limits, server-side errors, and network failures.
    All of these have a non-trivial chance of succeeding on a subsequent
    attempt without any change to the request.
    """


class ParseFatalError(ParseError):
    """Permanent failure — the caller must NOT retry.

    Subclasses cover authentication, quota exhaustion, bad input, and
    document-side terminal failures. Retrying these will fail the same way
    and burn credits. The right response is to surface the error to a human
    operator (dashboard, alerting, etc.).
    """


# ---------------------------------------------------------------------------
# Polling / job-state exceptions (specific to our wrapper, not from SDK)
# ---------------------------------------------------------------------------

class ParseTimeoutError(ParseError):
    """Raised when our polling loop exceeded `timeout_s` while the LlamaExtract
    job was still running. The job itself may still complete — a caller that
    has the `job_id` may want to call `client.extract.get(job_id)` later
    rather than re-submit (which would burn credits a second time).

    Extra fields:
        timeout_s:    the budget the caller passed in
        elapsed_s:    how long we actually waited before giving up
        last_status:  the most recent status seen (typically PENDING / RUNNING)
    """

    def __init__(
        self,
        *,
        job_id: str,
        timeout_s: float,
        elapsed_s: float,
        last_status: str,
    ) -> None:
        message = (
            f"LlamaExtract job did not complete within {timeout_s:.1f}s "
            f"(elapsed {elapsed_s:.1f}s, last status: {last_status})"
        )
        super().__init__(
            message,
            stage="extract.get",
            job_id=job_id,
            detail=last_status,
        )
        self.timeout_s = timeout_s
        self.elapsed_s = elapsed_s
        self.last_status = last_status


class ParseFailedError(ParseFatalError):
    """Raised when the LlamaExtract job reached FAILED or CANCELLED.

    This is FATAL because the document-side processing decided it could not
    succeed (UNSUPPORTED_FILE_TYPE, PDF_IS_PROTECTED, NO_DATA_FOUND_IN_FILE,
    etc., per the LlamaCloud troubleshooting docs). Resubmitting will hit
    the same failure.

    Extra fields:
        status:  the terminal status returned (FAILED or CANCELLED)
    """

    def __init__(
        self,
        *,
        job_id: str,
        status: str,
        detail: object | None = None,
    ) -> None:
        message = f"LlamaExtract job ended with terminal status {status}"
        super().__init__(
            message,
            stage="extract.get",
            job_id=job_id,
            detail=detail,
        )
        self.status = status


# ---------------------------------------------------------------------------
# Retryable subclasses — backoff + retry the same operation
# ---------------------------------------------------------------------------

class ParseRateLimitError(ParseRetryableError):
    """Raised on HTTP 429. LlamaCloud does NOT include a Retry-After header,
    so the caller must implement its own backoff (e.g. tenacity expo + jitter).

    The `stage` field tells the caller which call site got rate-limited so
    the backoff strategy can differ for upload vs submit vs poll.
    """

    def __init__(
        self,
        *,
        stage: ParseStage,
        job_id: Optional[str] = None,
        detail: object | None = None,
    ) -> None:
        message = f"LlamaCloud rate limited request at {stage}"
        super().__init__(message, stage=stage, job_id=job_id, detail=detail)


class ParseServerError(ParseRetryableError):
    """Raised on HTTP 5xx (500, 502, 503, 504). LlamaCloud-side issue —
    transient, expected to resolve on retry."""

    def __init__(
        self,
        *,
        stage: ParseStage,
        job_id: Optional[str] = None,
        status_code: Optional[int] = None,
        detail: object | None = None,
    ) -> None:
        message = (
            f"LlamaCloud server error ({status_code}) at {stage}"
            if status_code is not None
            else f"LlamaCloud server error at {stage}"
        )
        super().__init__(message, stage=stage, job_id=job_id, detail=detail)
        self.status_code = status_code


class ParseConnectionError(ParseRetryableError):
    """Raised on network failures BEFORE an HTTP response is received —
    DNS resolution, TCP connect, request timeout (APIConnectionError /
    APITimeoutError from the SDK). Almost always transient (Wi-Fi blip)."""

    def __init__(
        self,
        *,
        stage: ParseStage,
        job_id: Optional[str] = None,
        detail: object | None = None,
    ) -> None:
        message = f"network error reaching LlamaCloud at {stage}"
        super().__init__(message, stage=stage, job_id=job_id, detail=detail)


# ---------------------------------------------------------------------------
# Fatal subclasses — escalate, do NOT retry
# ---------------------------------------------------------------------------

class ParseAuthError(ParseFatalError):
    """Raised on HTTP 401 (bad/missing key) or 403 (no permission for resource).

    Retrying with the same credentials will fail identically. The right
    action is to verify LLAMA_CLOUD_API_KEY and the project scope.
    """

    def __init__(
        self,
        *,
        stage: ParseStage,
        status_code: Optional[int] = None,
        job_id: Optional[str] = None,
        detail: object | None = None,
    ) -> None:
        kind = {401: "unauthorized", 403: "forbidden"}.get(status_code or 0, "auth failure")
        message = f"LlamaCloud {kind} ({status_code}) at {stage}"
        super().__init__(message, stage=stage, job_id=job_id, detail=detail)
        self.status_code = status_code


class ParseQuotaExhaustedError(ParseFatalError):
    """Raised on HTTP 402 — credit / quota exhausted on the LlamaCloud plan.

    The SDK has no dedicated subclass for 402 (it raises a bare APIStatusError
    with status_code=402); we detect via the status code in the translator.

    The right action is to alert ops to top up credits or upgrade the plan;
    retrying will fail identically until that happens.
    """

    def __init__(
        self,
        *,
        stage: ParseStage,
        job_id: Optional[str] = None,
        detail: object | None = None,
    ) -> None:
        message = f"LlamaCloud quota exhausted (402) at {stage}"
        super().__init__(message, stage=stage, job_id=job_id, detail=detail)


class ParseBadInputError(ParseFatalError):
    """Raised on HTTP 400 / 413 / 422 (or APIResponseValidationError) — the
    request itself is rejected by LlamaCloud as invalid.

    Common triggers: invalid data_schema, file too large, unsupported
    MIME type, engine_error, llm_refusal (content policy). The document
    or our request is broken — retrying without changing it is pointless.
    Route the document to human review.
    """

    def __init__(
        self,
        *,
        stage: ParseStage,
        status_code: Optional[int] = None,
        job_id: Optional[str] = None,
        detail: object | None = None,
    ) -> None:
        message = (
            f"LlamaCloud rejected request as invalid ({status_code}) at {stage}"
            if status_code is not None
            else f"LlamaCloud rejected request as invalid at {stage}"
        )
        super().__init__(message, stage=stage, job_id=job_id, detail=detail)
        self.status_code = status_code


class ParseNotFoundError(ParseFatalError):
    """Raised on HTTP 404 — resource doesn't exist.

    The most common trigger in this build is **file expiry**: LlamaCloud
    caches uploaded files for 48 hours, then the file_id 404s. The right
    caller response is to **re-upload the bytes and resubmit** — different
    from a normal retry, hence its own class.

    Other triggers (parse job not found, agent not found) similarly mean
    the referenced resource genuinely doesn't exist on the server.
    """

    def __init__(
        self,
        *,
        stage: ParseStage,
        job_id: Optional[str] = None,
        detail: object | None = None,
    ) -> None:
        message = f"LlamaCloud resource not found (404) at {stage}"
        super().__init__(message, stage=stage, job_id=job_id, detail=detail)
