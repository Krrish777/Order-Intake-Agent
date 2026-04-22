"""Topology tests for :func:`backend.my_agent.agent.build_root_agent`.

The factory wires the eight Track A stage instances into a
:class:`~google.adk.agents.SequentialAgent`. These tests assert the
structural shape of what comes back — name, sub_agent ordering,
sub_agent types, kwarg-only dep injection, distinct-instance-per-call
semantics, and missing-kwarg behaviour.

End-to-end *behaviour* (running the pipeline with a real Runner against
an emulator-backed Firestore) is covered separately by Step 6; these
tests intentionally do not call ``root.run_async(ctx)``.

Module-level env:
    The ``backend.my_agent.agent`` module evaluates
    ``root_agent = _build_default_root_agent()`` at import time, which
    constructs an async Firestore client via
    :func:`~backend.tools.order_validator.tools.firestore_client.get_async_client`.
    The root-level ``tests/conftest.py`` sets
    ``FIRESTORE_EMULATOR_HOST`` + ``GOOGLE_CLOUD_PROJECT`` before any
    test module is imported so the client construction succeeds
    without real GCP credentials (no network is touched — the client
    is lazy on first use). This mirrors what ``make dev`` sets for
    ``adk web``.
"""

from __future__ import annotations

import pytest
from google.adk.agents import SequentialAgent

from backend.models.classified_document import ClassifiedDocument
from backend.models.parsed_document import ParsedDocument
from backend.my_agent.agent import ROOT_AGENT_NAME, build_root_agent
from backend.my_agent.stages.clarify import CLARIFY_STAGE_NAME, ClarifyStage
from backend.my_agent.stages.classify import CLASSIFY_STAGE_NAME, ClassifyStage
from backend.my_agent.stages.finalize import FINALIZE_STAGE_NAME, FinalizeStage
from backend.my_agent.stages.ingest import INGEST_STAGE_NAME, IngestStage
from backend.my_agent.stages.parse import PARSE_STAGE_NAME, ParseStage
from backend.my_agent.stages.persist import PERSIST_STAGE_NAME, PersistStage
from backend.my_agent.stages.reply_shortcircuit import (
    REPLY_SHORTCIRCUIT_STAGE_NAME,
    ReplyShortCircuitStage,
)
from backend.my_agent.stages.validate import VALIDATE_STAGE_NAME, ValidateStage
from tests.unit._stage_testing import FakeChildLlmAgent


# --------------------------------------------------------------------- fakes


def _fake_classify_fn(content: bytes, filename: str) -> ClassifiedDocument:
    """Stub classifier — never invoked by the topology tests.

    The factory only *holds* the callable on ClassifyStage; behaviour
    tests (Step 6) exercise the call path end-to-end.
    """
    raise AssertionError(
        "fake classify_fn should not be invoked by topology tests"
    )


def _fake_parse_fn(content: bytes, filename: str) -> ParsedDocument:
    """Stub parser — never invoked by the topology tests."""
    raise AssertionError(
        "fake parse_fn should not be invoked by topology tests"
    )


class _FakeValidator:
    """Duck-typed stand-in for :class:`OrderValidator`.

    ValidateStage uses pattern-B PrivateAttr (no isinstance check), so
    Pydantic accepts any object here — the stage only calls
    ``validator.validate(order)`` at runtime, which the topology tests
    never reach.
    """


class _FakeCoordinator:
    """Duck-typed stand-in for :class:`IntakeCoordinator`.

    PersistStage also uses pattern-B PrivateAttr, so this minimal
    placeholder suffices for structural tests.
    """


class _FakeExceptionStore:
    """Duck-typed stand-in for :class:`ExceptionStore`.

    ReplyShortCircuitStage's dep attr is a PrivateAttr of type
    :class:`ExceptionStore` (a typing.Protocol), which Pydantic cannot
    build an isinstance validator for — so any object is accepted.
    """


# ------------------------------------------------------------------ fixtures


def _make_deps() -> dict:
    """Build a fresh set of fake deps for one factory invocation.

    Returning a new dict (and new fakes) per call means tests do not
    cross-contaminate each other's LlmAgent stand-ins — important for
    :func:`test_build_root_agent_factories_produce_distinct_instances`
    which calls the factory twice.
    """
    return {
        "classify_fn": _fake_classify_fn,
        "parse_fn": _fake_parse_fn,
        "validator": _FakeValidator(),
        "coordinator": _FakeCoordinator(),
        "clarify_agent": FakeChildLlmAgent(output_key="clarify_email"),
        "summary_agent": FakeChildLlmAgent(output_key="run_summary"),
        "exception_store": _FakeExceptionStore(),
    }


# ------------------------------------------------------------------- tests


def test_build_root_agent_returns_sequential_agent_with_canonical_name() -> None:
    """The factory returns a SequentialAgent whose name matches the
    canonical ``ROOT_AGENT_NAME`` sentinel (``"order_intake_pipeline"``)
    that ``adk web`` + downstream evalset tooling assume."""
    root = build_root_agent(**_make_deps())

    assert isinstance(root, SequentialAgent)
    assert root.name == ROOT_AGENT_NAME
    assert root.name == "order_intake_pipeline"


def test_build_root_agent_sub_agents_in_canonical_order() -> None:
    """Sub-agents are wired in the canonical pipeline order.

    This is the load-bearing assertion — downstream docstrings,
    evalsets, and adk web traces all depend on this sequence. We
    import the per-stage ``*_STAGE_NAME`` constants instead of
    hardcoding strings so a rename in the stage module shows up as a
    symmetrical failure.
    """
    root = build_root_agent(**_make_deps())

    assert [sa.name for sa in root.sub_agents] == [
        INGEST_STAGE_NAME,
        REPLY_SHORTCIRCUIT_STAGE_NAME,
        CLASSIFY_STAGE_NAME,
        PARSE_STAGE_NAME,
        VALIDATE_STAGE_NAME,
        CLARIFY_STAGE_NAME,
        PERSIST_STAGE_NAME,
        FINALIZE_STAGE_NAME,
    ]


def test_build_root_agent_sub_agents_are_expected_types() -> None:
    """Each sub-agent is an instance of the expected BaseAgent subclass.

    Paired with the order test — this verifies the factory didn't swap
    an implementation (e.g. returning a stub for one slot) while still
    giving the slot the right name."""
    root = build_root_agent(**_make_deps())

    assert isinstance(root.sub_agents[0], IngestStage)
    assert isinstance(root.sub_agents[1], ReplyShortCircuitStage)
    assert isinstance(root.sub_agents[2], ClassifyStage)
    assert isinstance(root.sub_agents[3], ParseStage)
    assert isinstance(root.sub_agents[4], ValidateStage)
    assert isinstance(root.sub_agents[5], ClarifyStage)
    assert isinstance(root.sub_agents[6], PersistStage)
    assert isinstance(root.sub_agents[7], FinalizeStage)


def test_build_root_agent_rejects_positional_args() -> None:
    """All seven deps are keyword-only — passing any positionally raises.

    Kwarg-only guarantees downstream call sites stay readable and
    prevents accidental positional swaps if the dep list ever grows.
    """
    deps = _make_deps()
    with pytest.raises(TypeError):
        build_root_agent(  # type: ignore[misc]
            deps["classify_fn"],  # positional — should raise
            parse_fn=deps["parse_fn"],
            validator=deps["validator"],
            coordinator=deps["coordinator"],
            clarify_agent=deps["clarify_agent"],
            summary_agent=deps["summary_agent"],
            exception_store=deps["exception_store"],
        )


def test_build_root_agent_factories_produce_distinct_instances() -> None:
    """Calling the factory twice returns distinct SequentialAgent objects
    AND distinct stage instances.

    ADK's :meth:`BaseAgent.__set_parent_agent_for_sub_agents` raises
    when a sub-agent is re-parented — so sharing sub-agent instances
    across two SequentialAgent constructions would fail at the second
    call. This test proves the factory constructs fresh stage
    instances each invocation instead of memoizing, which is the
    behaviour the rest of the codebase (and repeated test setups)
    depends on.

    Note: we reuse the *same* dep objects across both calls (validator,
    coordinator, etc.) — those are plain objects the stages hold via
    PrivateAttr, not child agents that re-parent. Only the stage
    instances themselves must be fresh.
    """
    deps = _make_deps()

    root_a = build_root_agent(**deps)
    root_b = build_root_agent(**deps)

    assert root_a is not root_b
    for stage_a, stage_b in zip(root_a.sub_agents, root_b.sub_agents):
        assert stage_a is not stage_b, (
            f"factory re-used stage instance {stage_a.name!r} across "
            f"two calls — ADK would raise on re-parenting"
        )


def test_build_root_agent_missing_dep_raises() -> None:
    """Omitting any of the seven required kwargs raises TypeError.

    Smoke-tests the kwarg-only signature's required-ness; Python's
    argument binding gives us the error for free, but the test pins
    the contract so a future default-value slip is caught.
    """
    deps = _make_deps()
    deps.pop("exception_store")

    with pytest.raises(TypeError):
        build_root_agent(**deps)  # type: ignore[misc]
