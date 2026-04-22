"""Smoke tests for the LlmAgent factories.

Verify that the factories wire up the fields the orchestrator stages
depend on (``name``, ``model``, ``output_schema``, ``output_key``,
plus state-injection placeholders in ``instruction``) and that each
call returns a fresh instance — the latter guarding against
accidental module-level caching, which would trip ADK's
parent-conflict validation when the agent is held as a stage
attribute across tests/runs.
"""

from __future__ import annotations

from backend.models.clarify_email import ClarifyEmail
from backend.models.run_summary import RunSummary
from backend.my_agent.agents.clarify_email_agent import (
    CLARIFY_EMAIL_AGENT_NAME,
    build_clarify_email_agent,
)
from backend.my_agent.agents.summary_agent import (
    SUMMARY_AGENT_NAME,
    build_summary_agent,
)


def test_build_clarify_email_agent_returns_configured_llm_agent() -> None:
    agent = build_clarify_email_agent()

    assert agent.name == CLARIFY_EMAIL_AGENT_NAME
    assert agent.model == "gemini-3-flash-preview"
    assert agent.output_schema is ClarifyEmail
    assert agent.output_key == "clarify_email"
    for placeholder in ("{customer_name}", "{original_subject}", "{reason}"):
        assert placeholder in agent.instruction


def test_build_summary_agent_returns_configured_llm_agent() -> None:
    agent = build_summary_agent()

    assert agent.name == SUMMARY_AGENT_NAME
    assert agent.model == "gemini-3-flash-preview"
    assert agent.output_schema is RunSummary
    assert agent.output_key == "run_summary"
    for placeholder in (
        "{orders_created}",
        "{exceptions_opened}",
        "{docs_skipped}",
        "{reply_handled}",
    ):
        assert placeholder in agent.instruction


def test_factories_return_distinct_instances() -> None:
    clarify_a = build_clarify_email_agent()
    clarify_b = build_clarify_email_agent()
    summary_a = build_summary_agent()
    summary_b = build_summary_agent()

    assert id(clarify_a) != id(clarify_b)
    assert id(summary_a) != id(summary_b)
