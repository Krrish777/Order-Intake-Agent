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
from backend.models.confirmation_email import ConfirmationEmail
from backend.models.run_summary import RunSummary
from backend.my_agent.agents.clarify_email_agent import (
    CLARIFY_EMAIL_AGENT_NAME,
    build_clarify_email_agent,
)
from backend.my_agent.agents.confirmation_email_agent import (
    CONFIRMATION_EMAIL_AGENT_NAME,
    build_confirmation_email_agent,
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


def test_build_confirmation_email_agent_returns_configured_llm_agent() -> None:
    agent = build_confirmation_email_agent()

    assert agent.name == CONFIRMATION_EMAIL_AGENT_NAME
    assert agent.model == "gemini-3-flash-preview"
    assert agent.output_schema is ConfirmationEmail
    assert agent.output_key == "confirmation_email"
    for placeholder in (
        "{customer_name}",
        "{original_subject}",
        "{order_details}",
        "{order_ref}",
    ):
        assert placeholder in agent.instruction


def test_factories_return_distinct_instances() -> None:
    clarify_a = build_clarify_email_agent()
    clarify_b = build_clarify_email_agent()
    summary_a = build_summary_agent()
    summary_b = build_summary_agent()
    confirm_a = build_confirmation_email_agent()
    confirm_b = build_confirmation_email_agent()

    assert id(clarify_a) != id(clarify_b)
    assert id(summary_a) != id(summary_b)
    assert id(confirm_a) != id(confirm_b)


def _assert_gemini_schema_safe(schema: type) -> None:
    """Walk a Pydantic schema's JSON representation and fail if any node
    sets ``additionalProperties: false``.

    Gemini's ``generation_config.response_schema`` uses a restricted
    OpenAPI-3 subset that does NOT accept ``additionalProperties`` — a
    live 400 surfaced this the first time the pipeline reached the
    summary LlmAgent. Pydantic emits that field when the model (or any
    nested model) carries ``ConfigDict(extra="forbid")``. This walk
    blocks a regression.
    """
    json_schema = schema.model_json_schema()

    def _walk(node: object, path: str = "$") -> None:
        if isinstance(node, dict):
            if node.get("additionalProperties") is False:
                raise AssertionError(
                    f"{schema.__name__}.model_json_schema() contains "
                    f"`additionalProperties: false` at {path} — Gemini's "
                    f"response_schema will reject this with a 400. Remove "
                    f"`ConfigDict(extra=\"forbid\")` from the model (or "
                    f"any nested model) that feeds this LlmAgent's "
                    f"output_schema."
                )
            for k, v in node.items():
                _walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, item in enumerate(node):
                _walk(item, f"{path}[{i}]")

    _walk(json_schema)


def test_clarify_email_schema_is_gemini_safe() -> None:
    """ClarifyEmail must not set ``extra=\"forbid\"`` — Gemini rejects
    ``additionalProperties: false`` in its response_schema field."""
    _assert_gemini_schema_safe(ClarifyEmail)


def test_run_summary_schema_is_gemini_safe() -> None:
    """RunSummary must not set ``extra=\"forbid\"`` — same rule as
    ClarifyEmail. Both schemas landed on Gemini via ``output_schema`` on
    their LlmAgent factories, so both are guarded here."""
    _assert_gemini_schema_safe(RunSummary)


def test_confirmation_email_schema_is_gemini_safe() -> None:
    """ConfirmationEmail must not set ``extra=\"forbid\"`` — same rule as
    ClarifyEmail. Drafted on AUTO_APPROVE and fed to Gemini via
    ``output_schema`` on its LlmAgent factory."""
    _assert_gemini_schema_safe(ConfirmationEmail)


def test_every_factory_produces_gemini_safe_output_schema() -> None:
    """Catch-all: any future LlmAgent factory whose ``output_schema`` ever
    reaches Gemini must pass the ``additionalProperties: false`` walk.

    Loops the known factories; adding a new factory file should
    extend this fixture explicitly (keeps the test list visible rather
    than auto-discovering via module scan, which can mask skipped agents).
    """
    for factory in (
        build_clarify_email_agent,
        build_summary_agent,
        build_confirmation_email_agent,
    ):
        agent = factory()
        assert agent.output_schema is not None, (
            f"{factory.__name__} should set output_schema"
        )
        _assert_gemini_schema_safe(agent.output_schema)
