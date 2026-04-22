"""Prompts for the SummaryAgent (Gemini-backed LlmAgent).

The ``INSTRUCTION_TEMPLATE`` keeps ``{state_key}`` braces literal —
ADK's LlmAgent performs state injection at run time, so do not
f-string-interpolate these at module load.
"""

from __future__ import annotations

from typing import Final

SYSTEM_PROMPT: Final[str] = (
    "You summarize one run of the order-intake pipeline for an operator "
    "glancing at the event log. Be factual, no marketing language."
)

INSTRUCTION_TEMPLATE: Final[str] = """\
Summarize the run that just completed.

Counts (already computed — use these literally, do not recompute):
- orders_created: {orders_created}
- exceptions_opened: {exceptions_opened}
- docs_skipped: {docs_skipped}
- reply_handled: {reply_handled}

Requirements:
- Emit a JSON object matching the RunSummary schema.
- Echo the three count fields (`orders_created`, `exceptions_opened`,
  `docs_skipped`) back as integers with the values above.
- The `summary` field is one or two sentences of plain factual prose.
  Describe what the pipeline processed this run. If `reply_handled` is
  true, mention that a customer clarify reply was handled. Do not use
  marketing language ("successfully", "seamlessly", "powerful", etc.).
- Do not invent counts or mention items that are not in the inputs above.
"""

__all__ = ["SYSTEM_PROMPT", "INSTRUCTION_TEMPLATE"]
