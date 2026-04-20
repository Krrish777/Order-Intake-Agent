"""Structured payload carried by every pipeline exception.

``err.context.model_dump()`` is the single programmatic surface for turning
an exception into a JSON error envelope; ``except`` still dispatches on
class.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class ErrorContext(BaseModel):
    """Structured metadata attached to every pipeline exception."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    stage: Optional[str] = Field(
        None, description="Pipeline step that failed (e.g. 'extract.get')."
    )
    job_id: Optional[str] = Field(
        None, description="LlamaCloud job id, if one had been created."
    )
    status_code: Optional[int] = Field(
        None, description="HTTP status code, when the error came from an HTTP response."
    )
    detail: Optional[Any] = Field(
        None, description="Underlying SDK error or any extra diagnostic context."
    )
