"""Pydantic schema for the ClarifyEmailAgent output.

Used as ``output_schema`` for the Gemini-backed LlmAgent that drafts
clarify emails for ``PENDING_CLARIFY`` exceptions. The field
descriptions double as guidance to Gemini via the generated JSON
schema, so keep them concrete.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ClarifyEmail(BaseModel):
    """One drafted clarify email to send back to the customer.

    Used as ``output_schema`` on the Gemini ClarifyEmailAgent. Intentionally
    does NOT set ``model_config = ConfigDict(extra="forbid")`` — Pydantic
    emits ``additionalProperties: false`` for that, which Gemini's
    ``generation_config.response_schema`` rejects with a 400 (field not
    recognized by the OpenAPI 3 subset Gemini accepts). Pydantic's default
    silently-ignore-extra is the right behavior here: the LLM's output is
    what we validate, not untrusted user input.
    """

    subject: str = Field(
        ...,
        description=(
            "Email subject line. Reuse the original order subject where "
            "possible, prefixed with 'Re: ' so it threads. Keep under "
            "~80 characters."
        ),
    )
    body: str = Field(
        ...,
        description=(
            "Plain-text email body. 3 to 5 sentences. Warm but concise. "
            "Ask only about the fields listed in the reason — never "
            "invent SKUs, quantities, prices, or dates. Sign off "
            "professionally."
        ),
    )


__all__ = ["ClarifyEmail"]
