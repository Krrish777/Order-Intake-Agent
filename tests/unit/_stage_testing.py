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


__all__ = [
    "make_stage_ctx",
    "collect_events",
    "final_state_delta",
    "FakeChildLlmAgent",
]


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


class FakeChildLlmAgent:
    """Duck-typed stand-in for an ADK LlmAgent with output_schema + output_key.

    Tests for stages that hold a child LlmAgent (ClarifyStage, FinalizeStage)
    inject this instead of the real thing so the child-invocation pattern
    can be exercised without a real Gemini call.

    Parameters
    ----------
    output_key : str
        The state-delta key the real LlmAgent would populate via its
        output_key config (e.g. "clarify_email", "run_summary").
    responses : list[dict | pydantic.BaseModel | None] | None
        One entry per expected invocation. Each entry is yielded on the
        corresponding call's final Event's state_delta[output_key].
        ``None`` (as an entry value) means emit an event WITHOUT the
        output_key (for RuntimeError-testing paths). If the list is
        exhausted, a trivial stub dict is emitted. Passing ``None`` as
        the whole argument means "never emit the output_key" — fake
        yields no final event at all.
    capture_keys : list[str] | None
        Flat state keys to snapshot at the start of each run_async call.
        Captured snapshots accumulate in ``self.capture_state``.
    extra_events : list[Event] | None
        Events to yield before the response event on every invocation.
        Useful for testing stages that forward child events upward.
    name : str
        Author string on emitted Events.
    """

    def __init__(
        self,
        *,
        output_key: str,
        responses: list[Any] | None = None,
        capture_keys: list[str] | None = None,
        extra_events: list[Event] | None = None,
        name: str = "fake_child_agent",
    ) -> None:
        self.output_key = output_key
        # Distinguish "pass an empty list" (→ emit stub) from "pass None"
        # (→ emit NO final event). Needed for the RuntimeError path.
        self._responses_none = responses is None
        self._responses: list[Any] = list(responses) if responses is not None else []
        self.capture_keys = list(capture_keys) if capture_keys is not None else []
        self._extra_events = list(extra_events) if extra_events else []
        self.capture_state: list[dict[str, Any]] = []
        self.name = name
        self.call_count = 0

    async def run_async(self, ctx):
        self.call_count += 1
        # Snapshot the configured keys at invocation time.
        self.capture_state.append(
            {k: ctx.session.state.get(k) for k in self.capture_keys}
        )
        for extra in self._extra_events:
            yield extra
        if self._responses_none:
            # No final output-key event at all — exercises the
            # "child never emitted the key" RuntimeError path.
            return
        # Pick response for this call (default to a trivial dict if exhausted).
        response = (
            self._responses.pop(0)
            if self._responses
            else {"stub": True}
        )
        if response is None:
            # This specific invocation emits an event WITHOUT the
            # output_key (per-call variant of the RuntimeError path).
            yield Event(
                author=self.name,
                actions=_event_actions_with({}),
            )
            return
        # Defensive: accept either a raw dict, a Pydantic instance, or
        # a pre-built payload mapping.
        if hasattr(response, "model_dump"):
            payload = response.model_dump(mode="json")
        else:
            payload = response
        yield Event(
            author=self.name,
            actions=_event_actions_with({self.output_key: payload}),
        )


def _event_actions_with(state_delta: dict[str, Any]):
    """Local helper to build an ``EventActions`` with a given state_delta.

    Lazy-imported to keep the module-level import list minimal for
    test modules that don't need the fake.
    """
    from google.adk.events.event_actions import EventActions

    return EventActions(state_delta=state_delta)
