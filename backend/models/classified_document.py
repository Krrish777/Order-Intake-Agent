"""Pydantic schema for classifier output."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from backend.models.parsed_document import DocumentClassification

DocumentFormat = Literal[
    "pdf",
    "image",
    "xlsx",
    "xls",
    "csv",
    "tsv",
    "xml",
    "edi",
    "email",
    "text",
    "unknown",
]


class ClassifiedDocument(BaseModel):
    """Classifier result: LLM-decided intent plus deterministic format."""

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

    document_format: DocumentFormat = Field(
        ...,
        description="Format family inferred from filename + MIME type.",
    )

    filename: str = Field(..., description="Original filename supplied by caller.")
    mime_type: str = Field(..., description="MIME type sent to LlamaCloud.")
    byte_size: int = Field(..., ge=0, description="Raw content length in bytes.")

    classify_job_id: Optional[str] = Field(
        None, description="LlamaClassify job id — useful for log correlation.",
    )
