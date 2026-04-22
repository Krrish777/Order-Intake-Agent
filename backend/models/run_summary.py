"""Pydantic schema for the SummaryAgent output.

Used as ``output_schema`` for the Gemini-backed LlmAgent that writes
the one-paragraph recap at the end of a pipeline run. Counts are
computed deterministically upstream and passed in via session state;
the LLM's job is the natural-language ``summary`` field plus echoing
the counts back so the schema is self-contained.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RunSummary(BaseModel):
    """One-line recap of a pipeline run, rendered into the event log."""

    model_config = ConfigDict(extra="forbid")

    orders_created: int = Field(
        ...,
        description=(
            "Number of ProcessResult entries with kind=='order' — i.e. "
            "orders that were auto-approved and persisted this run."
        ),
    )
    exceptions_opened: int = Field(
        ...,
        description=(
            "Number of ProcessResult entries with kind=='exception' — "
            "docs routed to clarify or escalate this run."
        ),
    )
    docs_skipped: int = Field(
        ...,
        description=(
            "Number of documents ClassifyStage marked non-PO and "
            "dropped before extraction."
        ),
    )
    summary: str = Field(
        ...,
        description=(
            "One to two sentences describing the run in factual, "
            "non-marketing language. Mention what happened, not what "
            "the system 'successfully' did."
        ),
    )


__all__ = ["RunSummary"]
