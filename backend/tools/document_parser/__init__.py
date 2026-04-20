"""document_parser tool — package marker.

The LlamaExtract-based parser is now **legacy** — it lives at
``backend.tools.document_parser.legacy.parser`` during the migration to a
composable pipeline (classifier → parser → extractor → …).

Public names are re-exported from here unchanged so existing callers
(``scripts/parse_file.py``, ``scripts/parse_data.py``,
``tests/unit/test_document_parser.py``) keep working without edits:

    from backend.tools.document_parser import parse_document, ParsedDocument

New code should prefer the new tools (``backend.tools.document_classifier``
first) instead of this legacy surface.
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
# Expose the legacy module itself as ``backend.tools.document_parser.parser``
# so that ``from backend.tools.document_parser import parser as dp`` (used in
# tests for monkeypatching the cached _client) resolves to the legacy module.
from backend.tools.document_parser.legacy import parser
from backend.tools.document_parser.legacy.parser import parse_document

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
