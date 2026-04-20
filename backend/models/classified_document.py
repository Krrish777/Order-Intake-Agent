"""Data model for the document_classifier tool's output.

Two independent classification axes:

- ``document_intent`` — WHAT the document is (purchase_order, po_confirmation,
  …). LLM-decided via LlamaClassify. Reuses the 8-label ``DocumentClassification``
  Literal defined in :mod:`backend.models.parsed_document` so both tools share
  one source of truth.

- ``document_format`` — HOW the data is presented (pdf, xlsx, csv, edi, xml,
  email, image, text, unknown). Deterministic from the filename / MIME type
  — zero API cost, computed locally.

Source metadata (filename, mime_type, byte_size) travels with the result so
downstream stages (parser, extractor) can route without re-reading the file.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# Reuse the 8-label business-intent set — do NOT redeclare it here.
from backend.models.parsed_document import DocumentClassification

DocumentFormat = Literal[
    "pdf",      # digital or scanned PDFs
    "image",    # png, jpg, jpeg, tif, tiff
    "xlsx",     # modern Excel
    "xls",      # legacy Excel
    "csv",      # comma-separated
    "tsv",      # tab-separated
    "xml",      # cXML, OAGIS, bespoke PO XML
    "edi",      # ANSI X12, EDIFACT
    "email",    # .eml / .msg envelope (agent fans out attachments)
    "text",     # .txt, pasted email body
    "unknown",  # extension not recognised; route to human review
]


class ClassifiedDocument(BaseModel):
    """Output of the document_classifier tool.

    Fields
    ------
    document_intent:
        The 8-label business intent ('purchase_order', 'po_confirmation',
        'shipping_notice', 'invoice', 'inquiry', 'complaint', 'spam', 'other').
    intent_confidence:
        LlamaClassify confidence for ``document_intent`` in [0.0, 1.0].
    intent_reasoning:
        Free-text rationale from LlamaClassify — quotes document phrases.
    document_format:
        Deterministic format family inferred from the filename extension.
    filename:
        Original filename as supplied by the caller.
    mime_type:
        MIME type inferred from the extension. Same table used for the
        LlamaCloud upload Content-Type.
    byte_size:
        Raw content length in bytes.
    classify_job_id:
        LlamaClassify job id. Useful for log correlation and retry flows.
    """

    # --- Intent (LlamaClassify) --------------------------------------------
    document_intent: DocumentClassification = Field(
        ...,
        description="The 8-label business intent classification.",
    )
    intent_confidence: float = Field(
        ..., ge=0.0, le=1.0,
        description="LlamaClassify confidence for document_intent (0.0-1.0).",
    )
    intent_reasoning: str = Field(
        ...,
        description="LlamaClassify free-text rationale for the chosen intent.",
    )

    # --- Format (deterministic) --------------------------------------------
    document_format: DocumentFormat = Field(
        ...,
        description="Format family inferred from filename + MIME type.",
    )

    # --- Source metadata ---------------------------------------------------
    filename: str = Field(..., description="Original filename supplied by caller.")
    mime_type: str = Field(..., description="MIME type sent to LlamaCloud.")
    byte_size: int = Field(..., ge=0, description="Raw content length in bytes.")

    # --- Diagnostics -------------------------------------------------------
    classify_job_id: Optional[str] = Field(
        None, description="LlamaClassify job id — useful for log correlation.",
    )
