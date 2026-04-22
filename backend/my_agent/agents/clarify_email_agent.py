"""Factory for the ClarifyEmailAgent LlmAgent.

Produces a fresh ``LlmAgent`` instance per call. The ClarifyStage
holds the returned agent as an attribute and invokes it via
``child.run_async(ctx)``; the validated ``ClarifyEmail`` output lands
on ``ctx.session.state['clarify_email']`` for the stage to copy out.

A fresh instance per call avoids ADK's "agent already has a parent"
validation error when the same instance would otherwise be reused
across stages or test setups.
"""

from __future__ import annotations

from typing import Final

from google.adk.agents import LlmAgent

from backend.models.clarify_email import ClarifyEmail
from backend.prompts.clarify_email import INSTRUCTION_TEMPLATE, SYSTEM_PROMPT

CLARIFY_EMAIL_AGENT_NAME: Final[str] = "clarify_email_agent"


def build_clarify_email_agent() -> LlmAgent:
    """Return a freshly constructed ClarifyEmailAgent LlmAgent.

    Each call yields a new instance to avoid parent-conflict errors
    when the agent is held as an attribute on a BaseAgent stage.
    """
    combined_instruction = f"{SYSTEM_PROMPT}\n\n{INSTRUCTION_TEMPLATE}"
    return LlmAgent(
        name=CLARIFY_EMAIL_AGENT_NAME,
        model="gemini-3-flash-preview",
        description=(
            "Drafts a short clarify email to the customer when a PO is "
            "missing required fields."
        ),
        instruction=combined_instruction,
        output_schema=ClarifyEmail,
        output_key="clarify_email",
    )


__all__ = ["CLARIFY_EMAIL_AGENT_NAME", "build_clarify_email_agent"]
