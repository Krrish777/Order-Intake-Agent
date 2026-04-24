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

import base64
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from backend.audit.logger import AuditLogger
from backend.ingestion.eml_parser import EmlParseError
from backend.my_agent.stages.ingest import INGEST_STAGE_NAME, IngestStage
from tests.unit._stage_testing import collect_events, final_state_delta, make_stage_ctx


# --------------------------------------------------------------------- helpers


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WRAPPER_EML = REPO_ROOT / "data" / "pdf" / "patterson_po-28491.wrapper.eml"
CLARIFY_REPLY_EML = REPO_ROOT / "data" / "email" / "birch_valley_clarify_reply.eml"


# ---------------------------------------------------------------------- tests


def test_path_input_parses_eml_into_envelope_state() -> None:
    stage = IngestStage(audit_logger=AsyncMock(spec=AuditLogger))
    ctx = make_stage_ctx(stage=stage, user_text=str(WRAPPER_EML))

    events = collect_events(stage.run_async(ctx))

    delta = final_state_delta(events)
    envelope = delta["envelope"]
    assert isinstance(envelope, dict)
    assert envelope["message_id"]
    assert envelope["from_addr"].startswith("Gail Prescott")


def test_raw_eml_input_parses_via_tempfile() -> None:
    raw = WRAPPER_EML.read_text(encoding="utf-8")
    stage = IngestStage(audit_logger=AsyncMock(spec=AuditLogger))
    ctx = make_stage_ctx(stage=stage, user_text=raw)

    events = collect_events(stage.run_async(ctx))

    envelope = final_state_delta(events)["envelope"]
    assert isinstance(envelope, dict)
    # Raw path means source_path points at a tempfile, which is fine —
    # the stage's contract is that *the parsed fields* land on state.
    assert envelope["message_id"]
    assert envelope["subject"].startswith("PO-28491")


def test_empty_user_content_raises() -> None:
    stage = IngestStage(audit_logger=AsyncMock(spec=AuditLogger))
    ctx = make_stage_ctx(stage=stage, user_text=None)

    with pytest.raises(ValueError, match="IngestStage requires user message"):
        collect_events(stage.run_async(ctx))


def test_empty_text_user_content_raises() -> None:
    """Whitespace-only text counts as empty per the fail-fast contract."""
    stage = IngestStage(audit_logger=AsyncMock(spec=AuditLogger))
    ctx = make_stage_ctx(stage=stage, user_text="   \n  ")

    with pytest.raises(ValueError, match="IngestStage requires user message"):
        collect_events(stage.run_async(ctx))


def test_nonexistent_path_raises_eml_parse_error(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.eml"
    stage = IngestStage(audit_logger=AsyncMock(spec=AuditLogger))
    ctx = make_stage_ctx(stage=stage, user_text=str(missing))

    with pytest.raises(EmlParseError):
        collect_events(stage.run_async(ctx))


def test_body_only_email_synthesizes_body_attachment() -> None:
    stage = IngestStage(audit_logger=AsyncMock(spec=AuditLogger))
    ctx = make_stage_ctx(stage=stage, user_text=str(CLARIFY_REPLY_EML))

    events = collect_events(stage.run_async(ctx))
    envelope = final_state_delta(events)["envelope"]

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
    stage = IngestStage(audit_logger=AsyncMock(spec=AuditLogger))
    ctx = make_stage_ctx(stage=stage, user_text=raw)

    events = collect_events(stage.run_async(ctx))

    # If the heuristic had mis-routed this to the path branch, parse_eml
    # would have raised EmlParseError on the nonexistent "path".
    envelope = final_state_delta(events)["envelope"]
    assert envelope["message_id"]


def test_path_starting_with_from_routes_as_path_when_no_blank_line() -> None:
    """A string starting with ``From_Suppliers/...`` but lacking a blank line
    must take the path branch, not the raw-EML branch. Proves the heuristic
    requires *both* a MIME-header prefix AND a blank line — neither alone.
    """
    stage = IngestStage(audit_logger=AsyncMock(spec=AuditLogger))
    ctx = make_stage_ctx(stage=stage, user_text="From_Suppliers/msg.eml")

    with pytest.raises(EmlParseError):
        collect_events(stage.run_async(ctx))


def test_author_and_name_set_correctly() -> None:
    stage = IngestStage(audit_logger=AsyncMock(spec=AuditLogger))
    assert stage.name == INGEST_STAGE_NAME

    ctx = make_stage_ctx(stage=stage, user_text=str(WRAPPER_EML))
    events = collect_events(stage.run_async(ctx))

    # At least one event authored by the stage carrying the envelope delta.
    ingest_events = [e for e in events if e.author == INGEST_STAGE_NAME]
    assert ingest_events
    assert any(e.actions.state_delta.get("envelope") for e in ingest_events)


@pytest.mark.asyncio
async def test_stage_emits_entered_and_exited_audit_events() -> None:
    audit_logger = AsyncMock(spec=AuditLogger)
    stage = IngestStage(audit_logger=audit_logger)
    ctx = make_stage_ctx(
        stage=stage,
        user_text=str(WRAPPER_EML),
    )

    try:
        async for _ in stage.run_async(ctx):
            pass
    except Exception:
        pass

    calls = audit_logger.emit.await_args_list
    phases = [c.kwargs["phase"] for c in calls]
    assert "entered" in phases
    assert "exited" in phases
