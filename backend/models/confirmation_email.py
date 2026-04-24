"""Pydantic schema for the ConfirmationEmailAgent output.

Used as ``output_schema`` for the Gemini-backed LlmAgent that drafts
customer-facing order confirmations on AUTO_APPROVE decisions. Field
descriptions double as guidance to Gemini via the generated JSON schema.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ConfirmationEmail(BaseModel):
    """One drafted order-confirmation email to send back to the customer.

    Used as ``output_schema`` on the Gemini ConfirmationEmailAgent.
    Intentionally does NOT set ``model_config = ConfigDict(extra="forbid")``
    — Pydantic emits ``additionalProperties: false`` for that, which
    Gemini's ``generation_config.response_schema`` rejects with a 400.
    Pydantic's default silently-ignore-extra is the right behavior here:
    the LLM's output is what we validate, not untrusted user input.
    """

    subject: str = Field(
        ...,
        description=(
            "Email subject line. Reuse the original order subject where "
            "possible, prefixed with 'Re: ' so it threads, and append a "
            "brief confirmation marker (e.g., 'confirmed') with the order "
            "total. Keep under ~80 characters."
        ),
    )
    body: str = Field(
        ...,
        description=(
            "Plain-text email body, 5 to 8 sentences. Warm but concise. "
            "Echo the line items, quantities, and pricing verbatim from "
            "the order details provided. Mention the ship-to address and "
            "payment terms. Do NOT invent ship dates, promotions, or "
            "anything not present in the provided order details. Sign "
            "off professionally."
        ),
    )


__all__ = ["ConfirmationEmail"]
