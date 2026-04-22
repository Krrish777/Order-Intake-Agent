"""Tests for the shared helpers in :mod:`tests.unit._stage_testing`.

Stages from 4f (:class:`ClarifyStage`) onward emit multiple
``state_delta``-carrying events per invocation — each child-agent
iteration forwards the child's events upward, then the stage yields one
final aggregator event. ``final_state_delta`` merges those deltas with
last-write-wins semantics, and downstream tests rely on that contract.
This file locks it with a tiny hand-built case.
"""

from __future__ import annotations

from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions

from tests.unit._stage_testing import final_state_delta


def test_final_state_delta_merges_last_wins() -> None:
    e1 = Event(
        author="a",
        actions=EventActions(state_delta={"a": 1, "b": 2}),
    )
    e2 = Event(
        author="a",
        actions=EventActions(state_delta={"b": 3, "c": 4}),
    )

    merged = final_state_delta([e1, e2])

    assert merged == {"a": 1, "b": 3, "c": 4}
