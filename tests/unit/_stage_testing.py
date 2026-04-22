"""Shared helpers for the pipeline-stage unit tests.

Each of the :class:`~google.adk.agents.BaseAgent`-derived stages
(``IngestStage``, ``ReplyShortCircuitStage``, ``ClassifyStage``,
``ParseStage``, ``ValidateStage``, ...) is exercised through its
canonical ADK entry point — ``stage.run_async(ctx)`` — so we honour the
same wrapping (``before_agent_callback`` / tracing) that the outer
:class:`SequentialAgent` will apply in Step 5.

That entry point requires a **real** :class:`InvocationContext`. A
:class:`types.SimpleNamespace` duck-type was considered and discarded:
``BaseAgent.run_async`` calls ``parent_context.model_copy`` which only
works on a live Pydantic model. Constructing the real thing costs ~7
lines of boilerplate — we encapsulate them here so test files stay
focused on behaviour, not plumbing.

The helper also unifies two input patterns the stages use:

* :class:`IngestStage` reads ``ctx.user_content`` — so tests drive it by
  passing ``user_text`` (wrapped in a single-part
  :class:`types.Content`).
* Every downstream stage reads ``ctx.session.state`` — so tests
  pre-seed it via the ``state`` dict.

Both can be supplied at once (some future stage may look at both).
Neither is required — omitting both yields a ctx with ``user_content=None``
and ``session.state == {}``.

The module name starts with ``_`` so pytest's default collection glob
does not treat this file as a test file.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any, Iterable

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events.event import Event
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.adk.sessions.session import Session
from google.genai import types


__all__ = ["make_stage_ctx", "collect_events", "final_state_delta"]


def make_stage_ctx(
    *,
    stage: BaseAgent,
    user_text: str | None = None,
    state: dict[str, Any] | None = None,
) -> InvocationContext:
    """Construct a minimal but real :class:`InvocationContext`.

    Args:
        stage: The :class:`BaseAgent` being tested. ADK's
            ``InvocationContext`` requires a reference to the running
            agent, and ``BaseAgent.run_async`` uses it to build the
            child context propagated to callbacks.
        user_text: Optional raw text for ``ctx.user_content``. If
            ``None``, ``user_content`` is left as ``None`` (exercises
            the missing-user-content branch in :class:`IngestStage`). If
            provided, it is wrapped in a single-part
            :class:`types.Content` with ``role='user'``.
        state: Optional pre-seeded session state. Each key lands on
            ``ctx.session.state`` verbatim. Use this for stages that
            read ``state['envelope']``, ``state['classified_docs']``,
            etc.

    Returns:
        A ready-to-use :class:`InvocationContext` that can be passed
        into ``stage.run_async(ctx)`` and awaited.
    """
    user_content = (
        types.Content(role="user", parts=[types.Part(text=user_text)])
        if user_text is not None
        else None
    )
    session = Session(id="s-test", app_name="order-intake-test", user_id="u-test")
    if state:
        for key, value in state.items():
            session.state[key] = value
    return InvocationContext(
        session_service=InMemorySessionService(),
        invocation_id="inv-test",
        agent=stage,
        session=session,
        user_content=user_content,
    )


def collect_events(
    run_async: AsyncGenerator[Event, None],
) -> list[Event]:
    """Drain ``stage.run_async(ctx)`` into a list.

    Thin wrapper around ``asyncio.run`` — keeps the ``async for`` bleed
    out of the test bodies. Pass the raw generator (``stage.run_async(ctx)``)
    and get back a list of :class:`Event`.
    """

    async def _drain() -> list[Event]:
        events: list[Event] = []
        async for event in run_async:
            events.append(event)
        return events

    return asyncio.run(_drain())


def final_state_delta(events: Iterable[Event]) -> dict[str, Any]:
    """Return the merged ``state_delta`` across all events, last write wins.

    Most stages emit a single state-delta event, but the helper folds
    across all of them so a stage that fans out into multiple events
    (or adds a final summary event) is handled without the test body
    having to care.
    """
    merged: dict[str, Any] = {}
    for event in events:
        if event.actions and event.actions.state_delta:
            merged.update(event.actions.state_delta)
    return merged
