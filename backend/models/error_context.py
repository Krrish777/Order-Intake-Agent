"""Pydantic schema for the structured fields carried by pipeline exceptions.

Exceptions raised by ``backend.utils.exceptions`` hold an ``ErrorContext``
instance. This gives callers a single programmatic surface
(``err.context.model_dump()``) that FastAPI can return as a JSON error
envelope, while still allowing ``except`` clauses to dispatch on class.
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
