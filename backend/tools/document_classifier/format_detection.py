"""Filename / extension → ``DocumentFormat`` + MIME type.

Deterministic, local, no I/O. Both the format enum and the MIME-type override
table live here so the classifier and any future tool can share one source of
truth for "what kind of thing is this file, and what Content-Type should we
send to LlamaCloud".

The MIME override table exists because Python's stdlib ``mimetypes`` module:
  - misses ``.edi``, ``.x12``, ``.edifact`` entirely,
  - on Windows returns ``None`` for ``.csv`` / ``.eml`` depending on registry,
  - never guesses ``.tsv``.

LlamaCloud infers the parser path from the multipart Content-Type, so a wrong
or missing type here causes ``inputs_invalid: Unsupported file type: None``
at ``files.create`` — the same failure mode the legacy parser worked around.
"""

from __future__ import annotations

import mimetypes
from typing import Final

from backend.models.classified_document import DocumentFormat

# --- Extension → format family ---------------------------------------------

_EXTENSION_TO_FORMAT: Final[dict[str, DocumentFormat]] = {
    ".pdf":     "pdf",
    ".png":     "image",
    ".jpg":     "image",
    ".jpeg":    "image",
    ".tif":     "image",
    ".tiff":    "image",
    ".xlsx":    "xlsx",
    ".xls":     "xls",
    ".csv":     "csv",
    ".tsv":     "tsv",
    ".xml":     "xml",
    ".edi":     "edi",
    ".x12":     "edi",
    ".edifact": "edi",
    ".eml":     "email",
    ".msg":     "email",
    ".txt":     "text",
}

# --- Extension → MIME type (stdlib fallback fills the gaps) ----------------
#
# Lifted from the legacy parser's ``_EXTENSION_MIME_OVERRIDES`` (previously at
# ``backend/tools/document_parser/parser.py:23``). Extended with TSV + image
# types so both tools share the same table.

_EXTENSION_MIME_OVERRIDES: Final[dict[str, str]] = {
    ".csv":     "text/csv",
    ".tsv":     "text/tab-separated-values",
    ".xlsx":    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls":     "application/vnd.ms-excel",
    ".eml":     "message/rfc822",
    ".msg":     "application/vnd.ms-outlook",
    ".edi":     "application/edi-x12",
    ".x12":     "application/edi-x12",
    ".edifact": "application/edifact",
    ".xml":     "application/xml",
    ".txt":     "text/plain",
    ".pdf":     "application/pdf",
    ".png":     "image/png",
    ".jpg":     "image/jpeg",
    ".jpeg":    "image/jpeg",
    ".tif":     "image/tiff",
    ".tiff":    "image/tiff",
}


def _extension(filename: str) -> str:
    """Return the lower-cased ``.ext`` of ``filename`` (or ``""`` if none)."""
    if "." not in filename:
        return ""
    return "." + filename.rsplit(".", 1)[-1].lower()


def detect_format(filename: str) -> DocumentFormat:
    """Map ``filename`` to a ``DocumentFormat``. Unknown extensions → 'unknown'."""
    return _EXTENSION_TO_FORMAT.get(_extension(filename), "unknown")


def guess_mime(filename: str) -> str:
    """Return a stable MIME type for ``filename``; fall back to octet-stream."""
    ext = _extension(filename)
    if ext in _EXTENSION_MIME_OVERRIDES:
        return _EXTENSION_MIME_OVERRIDES[ext]
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"
