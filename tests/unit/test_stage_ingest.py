"""Unit tests for :class:`backend.my_agent.stages.ingest.IngestStage`.

The stage turns a user message (either a path to an ``.eml`` file or the raw
EML content itself) into a normalised :class:`EmailEnvelope` written to
``ctx.session.state['envelope']`` via an ``EventActions.state_delta``.

These tests drive the stage through the canonical ADK entry point,
``stage.run_async(ctx)`` — that path wraps ``_run_async_impl`` with the
before/after-callback plumbing and tracing, so exercising it here
guarantees we honour the contract the :class:`SequentialAgent` parent will
rely on in Step 5.

InvocationContext construction: we build a real one (~7 lines) using the
in-memory :class:`InMemorySessionService`. A :class:`SimpleNamespace` duck-type
was considered, but ``BaseAgent.run_async`` calls ``parent_context.model_copy``
which assumes a live Pydantic model — cheaper to just construct the real thing.
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Iterable

import pytest
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events.event import Event
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.adk.sessions.session import Session
from google.genai import types

from backend.ingestion.eml_parser import EmlParseError
from backend.my_agent.stages.ingest import INGEST_STAGE_NAME, IngestStage


# --------------------------------------------------------------------- helpers


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WRAPPER_EML = REPO_ROOT / "data" / "pdf" / "patterson_po-28491.wrapper.eml"
CLARIFY_REPLY_EML = REPO_ROOT / "data" / "email" / "birch_valley_clarify_reply.eml"


def _build_ctx(stage: IngestStage, text: str | None) -> InvocationContext:
    """Construct a minimal but real :class:`InvocationContext`.

    ``text=None`` triggers a missing-user-content path in the stage; otherwise
    the text is wrapped in a single-part ``types.Content``.
    """
    user_content = (
        types.Content(role="user", parts=[types.Part(text=text)]) if text is not None else None
    )
    session = Session(id="s-test", app_name="order-intake-test", user_id="u-test")
    return InvocationContext(
        session_service=InMemorySessionService(),
        invocation_id="inv-test",
        agent=stage,
        session=session,
        user_content=user_content,
    )


async def _collect_events(stage: IngestStage, ctx: InvocationContext) -> list[Event]:
    events: list[Event] = []
    async for event in stage.run_async(ctx):
        events.append(event)
    return events


def _final_state_delta(events: Iterable[Event]) -> dict[str, object]:
    """Return the merged ``state_delta`` across all events, last write wins."""
    merged: dict[str, object] = {}
    for event in events:
        if event.actions and event.actions.state_delta:
            merged.update(event.actions.state_delta)
    return merged


# ---------------------------------------------------------------------- tests


def test_path_input_parses_eml_into_envelope_state() -> None:
    stage = IngestStage()
    ctx = _build_ctx(stage, str(WRAPPER_EML))

    events = asyncio.run(_collect_events(stage, ctx))

    delta = _final_state_delta(events)
    envelope = delta["envelope"]
    assert isinstance(envelope, dict)
    assert envelope["message_id"]
    assert envelope["from_addr"].startswith("Gail Prescott")


def test_raw_eml_input_parses_via_tempfile() -> None:
    raw = WRAPPER_EML.read_text(encoding="utf-8")
    stage = IngestStage()
    ctx = _build_ctx(stage, raw)

    events = asyncio.run(_collect_events(stage, ctx))

    envelope = _final_state_delta(events)["envelope"]
    assert isinstance(envelope, dict)
    # Raw path means source_path points at a tempfile, which is fine —
    # the stage's contract is that *the parsed fields* land on state.
    assert envelope["message_id"]
    assert envelope["subject"].startswith("PO-28491")


def test_empty_user_content_raises() -> None:
    stage = IngestStage()
    ctx = _build_ctx(stage, None)

    with pytest.raises(ValueError, match="IngestStage requires user message"):
        asyncio.run(_collect_events(stage, ctx))


def test_empty_text_user_content_raises() -> None:
    """Whitespace-only text counts as empty per the fail-fast contract."""
    stage = IngestStage()
    ctx = _build_ctx(stage, "   \n  ")

    with pytest.raises(ValueError, match="IngestStage requires user message"):
        asyncio.run(_collect_events(stage, ctx))


def test_nonexistent_path_raises_eml_parse_error(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.eml"
    stage = IngestStage()
    ctx = _build_ctx(stage, str(missing))

    with pytest.raises(EmlParseError):
        asyncio.run(_collect_events(stage, ctx))


def test_body_only_email_synthesizes_body_attachment() -> None:
    stage = IngestStage()
    ctx = _build_ctx(stage, str(CLARIFY_REPLY_EML))

    events = asyncio.run(_collect_events(stage, ctx))
    envelope = _final_state_delta(events)["envelope"]

    attachments = envelope["attachments"]
    assert len(attachments) == 1
    only = attachments[0]
    assert only["filename"] == "body.txt"
    assert only["content_type"] == "text/plain"
    # The serializer base64-encodes the content field; decoding it must
    # recover exactly the body text bytes.
    decoded = base64.b64decode(only["content"])
    assert decoded == envelope["body_text"].encode("utf-8")


def test_heuristic_recognizes_raw_eml_by_mime_header() -> None:
    """A string that leads with ``From:`` and has a blank line must take the
    raw-content branch, not be interpreted as a filesystem path."""
    raw = WRAPPER_EML.read_text(encoding="utf-8")
    # Sanity check the fixture actually starts with a MIME header.
    assert raw.lower().startswith("from:")
    stage = IngestStage()
    ctx = _build_ctx(stage, raw)

    events = asyncio.run(_collect_events(stage, ctx))

    # If the heuristic had mis-routed this to the path branch, parse_eml
    # would have raised EmlParseError on the nonexistent "path".
    envelope = _final_state_delta(events)["envelope"]
    assert envelope["message_id"]


def test_path_starting_with_from_routes_as_path_when_no_blank_line() -> None:
    """A string starting with ``From_Suppliers/...`` but lacking a blank line
    must take the path branch, not the raw-EML branch. Proves the heuristic
    requires *both* a MIME-header prefix AND a blank line — neither alone.
    """
    stage = IngestStage()
    ctx = _build_ctx(stage, "From_Suppliers/msg.eml")

    with pytest.raises(EmlParseError):
        asyncio.run(_collect_events(stage, ctx))


def test_author_and_name_set_correctly() -> None:
    stage = IngestStage()
    assert stage.name == INGEST_STAGE_NAME

    ctx = _build_ctx(stage, str(WRAPPER_EML))
    events = asyncio.run(_collect_events(stage, ctx))

    # At least one event authored by the stage carrying the envelope delta.
    ingest_events = [e for e in events if e.author == INGEST_STAGE_NAME]
    assert ingest_events
    assert any(e.actions.state_delta.get("envelope") for e in ingest_events)
