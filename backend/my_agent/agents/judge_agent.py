"""Factory for the JudgeAgent LlmAgent — Track B outbound-email gate.

Produces a fresh ``LlmAgent`` instance per call. The JudgeStage holds
the returned agent as an attribute and invokes it via
``child.run_async(ctx)``; the validated ``JudgeVerdict`` output lands
on ``ctx.session.state['judge_verdict']`` for the stage to copy out
per iteration.

A fresh instance per call avoids ADK's "agent already has a parent"
validation error when the same instance would otherwise be reused
across stages or test setups (same pattern as ClarifyEmailAgent +
ConfirmationEmailAgent).
"""

from __future__ import annotations

from typing import Final

from google.adk.agents import LlmAgent

from backend.models.judge_verdict import JudgeVerdict
from backend.prompts.judge import INSTRUCTION_TEMPLATE, SYSTEM_PROMPT

JUDGE_AGENT_NAME: Final[str] = "judge_agent"


def build_judge_agent() -> LlmAgent:
    """Return a freshly constructed JudgeAgent LlmAgent.

    Each call yields a new instance to avoid parent-conflict errors
    when the agent is held as an attribute on a BaseAgent stage.
    """
    combined_instruction = f"{SYSTEM_PROMPT}\n\n{INSTRUCTION_TEMPLATE}"
    return LlmAgent(
        name=JUDGE_AGENT_NAME,
        model="gemini-3-flash-preview",
        description=(
            "Evaluates drafted outbound emails (confirmation + clarify) "
            "against the underlying order/exception record before Gmail send."
        ),
        instruction=combined_instruction,
        output_schema=JudgeVerdict,
        output_key="judge_verdict",
    )


__all__ = ["JUDGE_AGENT_NAME", "build_judge_agent"]
