"""Public surface for the document_parser tool."""

from backend.utils.exceptions import (
    ParseError,
    ParseFailedError,
    ParseRateLimitError,
    ParseTimeoutError,
)
from backend.models.parsed_document import (
    DocumentClassification,
    ExtractedOrder,
    OrderLineItem,
    ParsedDocument,
)
# ``parser`` alias lets tests monkeypatch ``parser._client`` via
# ``from backend.tools.document_parser import parser as dp``.
from backend.tools.document_parser.legacy import parser
from backend.tools.document_parser.legacy.parser import parse_document

__all__ = [
    "parse_document",
    "parser",
    "ParsedDocument",
    "ExtractedOrder",
    "OrderLineItem",
    "DocumentClassification",
    "ParseError",
    "ParseTimeoutError",
    "ParseFailedError",
    "ParseRateLimitError",
]
