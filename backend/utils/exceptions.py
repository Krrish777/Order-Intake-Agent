"""Typed exceptions for the document-pipeline tools.

The hierarchy organises by **what the caller should do**, not by HTTP status:

    PipelineError                        ← base; catch-all
    ├── <TimeoutError>                   ← polling exceeded budget; job may still finish
    ├── PipelineRetryableError           ← transient; retry same call with backoff
    │   ├── <RateLimitError>             (429)
    │   ├── <ServerError>                (5xx)
    │   └── <ConnectionError>            (network failure)
    └── PipelineFatalError               ← do NOT retry; escalate
        ├── <AuthError>                  (401/403)
        ├── <QuotaExhaustedError>        (402)
        ├── <BadInputError>              (400/413/422)
        ├── <NotFoundError>              (404; for files = 48h cache expired)
        └── <FailedError>                (job reached terminal FAILED/CANCELLED)

Per-domain families (``Parse*`` for the parser, ``Classify*`` for the
classifier) inherit these concrete behaviours and tighten the ``stage``
type so ``except ParseRateLimitError`` reads unambiguously in tracebacks.

Structured fields live on ``err.context`` as a pydantic ``ErrorContext``;
``str(err)`` is a one-line summary suitable for log lines and FastAPI
responses.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from backend.models.error_context import ErrorContext

# ---------------------------------------------------------------------------
# Stage vocabularies — each family narrows to its own pipeline steps.
# ---------------------------------------------------------------------------

ParseStage = Literal[
    "files.create",
    "extract.create",
    "extract.get",
    "validation",
]

ClassifyStage = Literal[
    "files.create",
    "classify.create",
    "classify.get",
    "validation",
]


# ---------------------------------------------------------------------------
# Generic base hierarchy — concrete behaviour lives here, once.
# ---------------------------------------------------------------------------

class PipelineError(Exception):
    """Base class for any failure in a document-pipeline tool."""

    default_message: str = "pipeline step failed"

    def __init__(
        self,
        message: Optional[str] = None,
        *,
        stage: Optional[str] = None,
        job_id: Optional[str] = None,
        status_code: Optional[int] = None,
        detail: Any = None,
    ) -> None:
        self.message = message or self.default_message
        self.context = ErrorContext(
            stage=stage,
            job_id=job_id,
            status_code=status_code,
            detail=detail,
        )
        super().__init__(self.message)

    @property
    def stage(self) -> Optional[str]:
        return self.context.stage

    @property
    def job_id(self) -> Optional[str]:
        return self.context.job_id

    @property
    def status_code(self) -> Optional[int]:
        return self.context.status_code

    @property
    def detail(self) -> Any:
        return self.context.detail

    def __str__(self) -> str:
        parts: list[str] = [self.message]
        if self.context.stage is not None:
            parts.append(f"stage={self.context.stage}")
        if self.context.job_id is not None:
            parts.append(f"job_id={self.context.job_id}")
        if self.context.status_code is not None:
            parts.append(f"status_code={self.context.status_code}")
        if self.context.detail is not None:
            parts.append(f"detail={self.context.detail!r}")
        return " | ".join(parts)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(message={self.message!r}, context={self.context!r})"


class PipelineRetryableError(PipelineError):
    """Transient failure — retry the same call with backoff."""


class PipelineFatalError(PipelineError):
    """Permanent failure — escalate, do NOT retry."""


# ---- concrete behaviours --------------------------------------------------

class _RateLimitError(PipelineRetryableError):
    def __init__(
        self, *, stage: str, job_id: Optional[str] = None, detail: Any = None
    ) -> None:
        super().__init__(
            f"LlamaCloud rate limited request at {stage}",
            stage=stage, job_id=job_id, detail=detail,
        )


class _ServerError(PipelineRetryableError):
    def __init__(
        self,
        *,
        stage: str,
        job_id: Optional[str] = None,
        status_code: Optional[int] = None,
        detail: Any = None,
    ) -> None:
        message = (
            f"LlamaCloud server error ({status_code}) at {stage}"
            if status_code is not None
            else f"LlamaCloud server error at {stage}"
        )
        super().__init__(
            message,
            stage=stage, job_id=job_id, status_code=status_code, detail=detail,
        )


class _ConnectionError(PipelineRetryableError):
    def __init__(
        self, *, stage: str, job_id: Optional[str] = None, detail: Any = None
    ) -> None:
        super().__init__(
            f"network error reaching LlamaCloud at {stage}",
            stage=stage, job_id=job_id, detail=detail,
        )


class _AuthError(PipelineFatalError):
    def __init__(
        self,
        *,
        stage: str,
        status_code: Optional[int] = None,
        job_id: Optional[str] = None,
        detail: Any = None,
    ) -> None:
        kind = {401: "unauthorized", 403: "forbidden"}.get(status_code or 0, "auth failure")
        super().__init__(
            f"LlamaCloud {kind} ({status_code}) at {stage}",
            stage=stage, job_id=job_id, status_code=status_code, detail=detail,
        )


class _QuotaExhaustedError(PipelineFatalError):
    def __init__(
        self, *, stage: str, job_id: Optional[str] = None, detail: Any = None
    ) -> None:
        super().__init__(
            f"LlamaCloud quota exhausted (402) at {stage}",
            stage=stage, job_id=job_id, status_code=402, detail=detail,
        )


class _BadInputError(PipelineFatalError):
    def __init__(
        self,
        *,
        stage: str,
        status_code: Optional[int] = None,
        job_id: Optional[str] = None,
        detail: Any = None,
    ) -> None:
        message = (
            f"LlamaCloud rejected request as invalid ({status_code}) at {stage}"
            if status_code is not None
            else f"LlamaCloud rejected request as invalid at {stage}"
        )
        super().__init__(
            message,
            stage=stage, job_id=job_id, status_code=status_code, detail=detail,
        )


class _NotFoundError(PipelineFatalError):
    """404 — most commonly a file_id that aged out of the 48h LlamaCloud cache.

    Caller should re-upload bytes and resubmit rather than retry the same id.
    """

    def __init__(
        self, *, stage: str, job_id: Optional[str] = None, detail: Any = None
    ) -> None:
        super().__init__(
            f"LlamaCloud resource not found (404) at {stage}",
            stage=stage, job_id=job_id, status_code=404, detail=detail,
        )


class _TimeoutError(PipelineError):
    """Polling exceeded ``timeout_s``. The job may still complete — callers
    holding the ``job_id`` should poll later rather than resubmit (resubmit
    burns credits a second time)."""

    _service_label: str = "LlamaCloud"

    def __init__(
        self,
        *,
        stage: str,
        job_id: str,
        timeout_s: float,
        elapsed_s: float,
        last_status: str,
    ) -> None:
        message = (
            f"{self._service_label} job did not complete within {timeout_s:.1f}s "
            f"(elapsed {elapsed_s:.1f}s, last status: {last_status})"
        )
        super().__init__(message, stage=stage, job_id=job_id, detail=last_status)
        self.timeout_s = timeout_s
        self.elapsed_s = elapsed_s
        self.last_status = last_status


class _FailedError(PipelineFatalError):
    """Job reached a terminal FAILED/CANCELLED status — resubmission will hit
    the same failure. Route the document to human review."""

    _service_label: str = "LlamaCloud"

    def __init__(
        self,
        *,
        stage: str,
        job_id: str,
        status: str,
        detail: Any = None,
    ) -> None:
        super().__init__(
            f"{self._service_label} job ended with terminal status {status}",
            stage=stage, job_id=job_id, detail=detail,
        )
        self.status = status


# ===========================================================================
# Parse family — document_parser tool.
# ===========================================================================

class ParseError(PipelineError):
    default_message = "document parser failed"


class ParseRetryableError(ParseError, PipelineRetryableError):
    """Transient — retry with backoff."""


class ParseFatalError(ParseError, PipelineFatalError):
    """Permanent — do not retry."""


class ParseRateLimitError(_RateLimitError, ParseRetryableError):
    def __init__(
        self, *, stage: ParseStage, job_id: Optional[str] = None, detail: Any = None
    ) -> None:
        super().__init__(stage=stage, job_id=job_id, detail=detail)


class ParseServerError(_ServerError, ParseRetryableError):
    def __init__(
        self,
        *,
        stage: ParseStage,
        job_id: Optional[str] = None,
        status_code: Optional[int] = None,
        detail: Any = None,
    ) -> None:
        super().__init__(
            stage=stage, job_id=job_id, status_code=status_code, detail=detail,
        )


class ParseConnectionError(_ConnectionError, ParseRetryableError):
    def __init__(
        self, *, stage: ParseStage, job_id: Optional[str] = None, detail: Any = None
    ) -> None:
        super().__init__(stage=stage, job_id=job_id, detail=detail)


class ParseAuthError(_AuthError, ParseFatalError):
    def __init__(
        self,
        *,
        stage: ParseStage,
        status_code: Optional[int] = None,
        job_id: Optional[str] = None,
        detail: Any = None,
    ) -> None:
        super().__init__(
            stage=stage, status_code=status_code, job_id=job_id, detail=detail,
        )


class ParseQuotaExhaustedError(_QuotaExhaustedError, ParseFatalError):
    def __init__(
        self, *, stage: ParseStage, job_id: Optional[str] = None, detail: Any = None
    ) -> None:
        super().__init__(stage=stage, job_id=job_id, detail=detail)


class ParseBadInputError(_BadInputError, ParseFatalError):
    def __init__(
        self,
        *,
        stage: ParseStage,
        status_code: Optional[int] = None,
        job_id: Optional[str] = None,
        detail: Any = None,
    ) -> None:
        super().__init__(
            stage=stage, status_code=status_code, job_id=job_id, detail=detail,
        )


class ParseNotFoundError(_NotFoundError, ParseFatalError):
    def __init__(
        self, *, stage: ParseStage, job_id: Optional[str] = None, detail: Any = None
    ) -> None:
        super().__init__(stage=stage, job_id=job_id, detail=detail)


class ParseTimeoutError(_TimeoutError, ParseError):
    _service_label = "LlamaExtract"

    def __init__(
        self,
        *,
        job_id: str,
        timeout_s: float,
        elapsed_s: float,
        last_status: str,
    ) -> None:
        super().__init__(
            stage="extract.get",
            job_id=job_id,
            timeout_s=timeout_s,
            elapsed_s=elapsed_s,
            last_status=last_status,
        )


class ParseFailedError(_FailedError, ParseFatalError):
    _service_label = "LlamaExtract"

    def __init__(self, *, job_id: str, status: str, detail: Any = None) -> None:
        super().__init__(
            stage="extract.get", job_id=job_id, status=status, detail=detail,
        )


# ===========================================================================
# Classify family — document_classifier tool.
# ===========================================================================

class ClassifyError(PipelineError):
    default_message = "document classifier failed"


class ClassifyRetryableError(ClassifyError, PipelineRetryableError):
    """Transient — retry with backoff."""


class ClassifyFatalError(ClassifyError, PipelineFatalError):
    """Permanent — do not retry."""


class ClassifyRateLimitError(_RateLimitError, ClassifyRetryableError):
    def __init__(
        self, *, stage: ClassifyStage, job_id: Optional[str] = None, detail: Any = None
    ) -> None:
        super().__init__(stage=stage, job_id=job_id, detail=detail)


class ClassifyServerError(_ServerError, ClassifyRetryableError):
    def __init__(
        self,
        *,
        stage: ClassifyStage,
        job_id: Optional[str] = None,
        status_code: Optional[int] = None,
        detail: Any = None,
    ) -> None:
        super().__init__(
            stage=stage, job_id=job_id, status_code=status_code, detail=detail,
        )


class ClassifyConnectionError(_ConnectionError, ClassifyRetryableError):
    def __init__(
        self, *, stage: ClassifyStage, job_id: Optional[str] = None, detail: Any = None
    ) -> None:
        super().__init__(stage=stage, job_id=job_id, detail=detail)


class ClassifyAuthError(_AuthError, ClassifyFatalError):
    def __init__(
        self,
        *,
        stage: ClassifyStage,
        status_code: Optional[int] = None,
        job_id: Optional[str] = None,
        detail: Any = None,
    ) -> None:
        super().__init__(
            stage=stage, status_code=status_code, job_id=job_id, detail=detail,
        )


class ClassifyQuotaExhaustedError(_QuotaExhaustedError, ClassifyFatalError):
    def __init__(
        self, *, stage: ClassifyStage, job_id: Optional[str] = None, detail: Any = None
    ) -> None:
        super().__init__(stage=stage, job_id=job_id, detail=detail)


class ClassifyBadInputError(_BadInputError, ClassifyFatalError):
    def __init__(
        self,
        *,
        stage: ClassifyStage,
        status_code: Optional[int] = None,
        job_id: Optional[str] = None,
        detail: Any = None,
    ) -> None:
        super().__init__(
            stage=stage, status_code=status_code, job_id=job_id, detail=detail,
        )


class ClassifyNotFoundError(_NotFoundError, ClassifyFatalError):
    def __init__(
        self, *, stage: ClassifyStage, job_id: Optional[str] = None, detail: Any = None
    ) -> None:
        super().__init__(stage=stage, job_id=job_id, detail=detail)


class ClassifyTimeoutError(_TimeoutError, ClassifyError):
    _service_label = "LlamaClassify"

    def __init__(
        self,
        *,
        job_id: str,
        timeout_s: float,
        elapsed_s: float,
        last_status: str,
    ) -> None:
        super().__init__(
            stage="classify.get",
            job_id=job_id,
            timeout_s=timeout_s,
            elapsed_s=elapsed_s,
            last_status=last_status,
        )


class ClassifyFailedError(_FailedError, ClassifyFatalError):
    _service_label = "LlamaClassify"

    def __init__(self, *, job_id: str, status: str, detail: Any = None) -> None:
        super().__init__(
            stage="classify.get", job_id=job_id, status=status, detail=detail,
        )


__all__ = [
    # stage vocabularies
    "ParseStage",
    "ClassifyStage",
    # generic base
    "PipelineError",
    "PipelineRetryableError",
    "PipelineFatalError",
    # Parse family
    "ParseError",
    "ParseRetryableError",
    "ParseFatalError",
    "ParseRateLimitError",
    "ParseServerError",
    "ParseConnectionError",
    "ParseAuthError",
    "ParseQuotaExhaustedError",
    "ParseBadInputError",
    "ParseNotFoundError",
    "ParseTimeoutError",
    "ParseFailedError",
    # Classify family
    "ClassifyError",
    "ClassifyRetryableError",
    "ClassifyFatalError",
    "ClassifyRateLimitError",
    "ClassifyServerError",
    "ClassifyConnectionError",
    "ClassifyAuthError",
    "ClassifyQuotaExhaustedError",
    "ClassifyBadInputError",
    "ClassifyNotFoundError",
    "ClassifyTimeoutError",
    "ClassifyFailedError",
]
