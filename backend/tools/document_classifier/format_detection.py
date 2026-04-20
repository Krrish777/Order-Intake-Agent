"""Filename / extension → ``DocumentFormat`` + MIME type. No I/O.

The MIME override table fills gaps in stdlib ``mimetypes`` (no ``.edi`` /
``.x12`` / ``.edifact`` / ``.tsv``; unreliable ``.csv`` / ``.eml`` on Windows).
LlamaCloud infers the parser path from the multipart Content-Type, so a
wrong or missing MIME here produces ``inputs_invalid: Unsupported file
type: None`` at ``files.create``.
"""

from __future__ import annotations

import mimetypes
from typing import Final

from backend.models.classified_document import DocumentFormat

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
    if "." not in filename:
        return ""
    return "." + filename.rsplit(".", 1)[-1].lower()


def detect_format(filename: str) -> DocumentFormat:
    return _EXTENSION_TO_FORMAT.get(_extension(filename), "unknown")


def guess_mime(filename: str) -> str:
    ext = _extension(filename)
    if ext in _EXTENSION_MIME_OVERRIDES:
        return _EXTENSION_MIME_OVERRIDES[ext]
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"
