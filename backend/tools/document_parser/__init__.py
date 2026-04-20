"""document_parser tool — package marker.

Re-exports the public surface so callers can write:

    from backend.tools.document_parser import parse_document, ParsedDocument

instead of digging into submodules. The Pydantic models and typed exceptions
live in shared layers (backend.models, backend.exceptions); they are re-exported
here as a convenience for callers who only care about this tool.
"""

from backend.exceptions import (
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
from backend.tools.document_parser.parser import parse_document

__all__ = [
    "parse_document",
    "ParsedDocument",
    "ExtractedOrder",
    "OrderLineItem",
    "DocumentClassification",
    "ParseError",
    "ParseTimeoutError",
    "ParseFailedError",
    "ParseRateLimitError",
]
