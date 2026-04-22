"""Factory for the SummaryAgent LlmAgent.

Produces a fresh ``LlmAgent`` instance per call. The FinalizeStage
holds the returned agent as an attribute and invokes it via
``child.run_async(ctx)``; the validated ``RunSummary`` output lands
on ``ctx.session.state['run_summary']`` for the stage to copy out.

A fresh instance per call avoids ADK's "agent already has a parent"
validation error when the same instance would otherwise be reused
across stages or test setups.
"""

from __future__ import annotations

from typing import Final

from google.adk.agents import LlmAgent

from backend.models.run_summary import RunSummary
from backend.prompts.summary import INSTRUCTION_TEMPLATE, SYSTEM_PROMPT

SUMMARY_AGENT_NAME: Final[str] = "run_summary_agent"


def build_summary_agent() -> LlmAgent:
    """Return a freshly constructed SummaryAgent LlmAgent.

    Each call yields a new instance to avoid parent-conflict errors
    when the agent is held as an attribute on a BaseAgent stage.
    """
    combined_instruction = f"{SYSTEM_PROMPT}\n\n{INSTRUCTION_TEMPLATE}"
    return LlmAgent(
        name=SUMMARY_AGENT_NAME,
        model="gemini-3-flash-preview",
        description=(
            "Writes a one- or two-sentence factual recap of a completed "
            "order-intake pipeline run."
        ),
        instruction=combined_instruction,
        output_schema=RunSummary,
        output_key="run_summary",
    )


__all__ = ["SUMMARY_AGENT_NAME", "build_summary_agent"]
