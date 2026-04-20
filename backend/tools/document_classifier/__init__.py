"""Public surface for the document_classifier tool."""

from backend.utils.exceptions import (
    ClassifyAuthError,
    ClassifyBadInputError,
    ClassifyConnectionError,
    ClassifyError,
    ClassifyFailedError,
    ClassifyFatalError,
    ClassifyNotFoundError,
    ClassifyQuotaExhaustedError,
    ClassifyRateLimitError,
    ClassifyRetryableError,
    ClassifyServerError,
    ClassifyTimeoutError,
)
from backend.models.classified_document import (
    ClassifiedDocument,
    DocumentFormat,
)
from backend.models.parsed_document import DocumentClassification
from backend.tools.document_classifier.classifier import classify_document
from backend.tools.document_classifier.format_detection import (
    detect_format,
    guess_mime,
)

__all__ = [
    # Entry points
    "classify_document",
    "detect_format",
    "guess_mime",
    # Models
    "ClassifiedDocument",
    "DocumentFormat",
    "DocumentClassification",
    # Exceptions
    "ClassifyError",
    "ClassifyTimeoutError",
    "ClassifyFailedError",
    "ClassifyRateLimitError",
    "ClassifyServerError",
    "ClassifyConnectionError",
    "ClassifyAuthError",
    "ClassifyQuotaExhaustedError",
    "ClassifyBadInputError",
    "ClassifyNotFoundError",
    "ClassifyRetryableError",
    "ClassifyFatalError",
]
