"""Factory for the ConfirmationEmailAgent LlmAgent.

Produces a fresh ``LlmAgent`` instance per call. The ConfirmStage
holds the returned agent as an attribute and invokes it via
``child.run_async(ctx)``; the validated ``ConfirmationEmail`` output
lands on ``ctx.session.state['confirmation_email']`` for the stage
to copy out.

A fresh instance per call avoids ADK's "agent already has a parent"
validation error when the same instance would otherwise be reused
across stages or test setups.
"""

from __future__ import annotations

from typing import Final

from google.adk.agents import LlmAgent

from backend.models.confirmation_email import ConfirmationEmail
from backend.prompts.confirmation_email import (
    INSTRUCTION_TEMPLATE,
    SYSTEM_PROMPT,
)

CONFIRMATION_EMAIL_AGENT_NAME: Final[str] = "confirmation_email_agent"


def build_confirmation_email_agent() -> LlmAgent:
    """Return a freshly constructed ConfirmationEmailAgent LlmAgent."""
    combined_instruction = f"{SYSTEM_PROMPT}\n\n{INSTRUCTION_TEMPLATE}"
    return LlmAgent(
        name=CONFIRMATION_EMAIL_AGENT_NAME,
        model="gemini-3-flash-preview",
        description=(
            "Drafts a short order-confirmation email to the customer "
            "when a PO is auto-approved."
        ),
        instruction=combined_instruction,
        output_schema=ConfirmationEmail,
        output_key="confirmation_email",
    )


__all__ = ["CONFIRMATION_EMAIL_AGENT_NAME", "build_confirmation_email_agent"]
