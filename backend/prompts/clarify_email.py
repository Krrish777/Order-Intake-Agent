"""Prompts for the ClarifyEmailAgent (Gemini-backed LlmAgent).

The ``INSTRUCTION_TEMPLATE`` keeps ``{state_key}`` braces literal —
ADK's LlmAgent performs state injection at run time, so do not
f-string-interpolate these at module load.
"""

from __future__ import annotations

from typing import Final

SYSTEM_PROMPT: Final[str] = (
    "You draft a brief, professional clarify email to a customer whose "
    "order is missing information. Ask only about fields listed in "
    "`reason`. Do not invent SKUs, quantities, or prices. Keep it warm "
    "but concise — 3 to 5 sentences."
)

INSTRUCTION_TEMPLATE: Final[str] = """\
Draft a clarify email to the customer so we can finish processing their order.

Customer name: {customer_name}
Original email subject: {original_subject}
What is missing or unclear (from the validator): {reason}

Requirements:
- Subject should be "Re: " plus the original subject so the reply threads.
- Body is 3 to 5 sentences of plain text. No HTML, no markdown, no bullet points.
- Ask only about the specific items called out in `reason`. Do not ask about
  anything else, and do not propose values for the missing fields.
- Tone: warm but professional. Sign off with "Best regards, Order Intake Team".
- Do not mention that you are an AI or reference internal systems.

Return a JSON object matching the ClarifyEmail schema with exactly two keys:
`subject` and `body`.
"""

__all__ = ["SYSTEM_PROMPT", "INSTRUCTION_TEMPLATE"]
