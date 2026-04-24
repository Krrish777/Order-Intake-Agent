"""Unit tests for the AuditedStage mixin.

Uses a minimal subclass _TestStage that yields one event, plus a
failing variant that raises inside _audited_run. The real stage
tests in test_stage_*.py provide per-stage coverage.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from backend.audit.logger import AuditLogger
from backend.my_agent.stages._audited import AuditedStage
from tests.unit._stage_testing import (
    collect_events,
    make_stage_ctx,
)


class _OkStage(AuditedStage):
    name: str = "TestStage"

    async def _audited_run(self, ctx):
        from google.adk.events import Event, EventActions

        yield Event(
            author=self.name,
            actions=EventActions(state_delta={"_probe": "ran"}),
        )


class _RaisesStage(AuditedStage):
    name: str = "BadStage"

    async def _audited_run(self, ctx):
        raise RuntimeError("boom")
        yield  # unreachable; keep async-generator


@pytest.mark.asyncio
class TestAuditedStageMixin:
    async def test_emits_entered_then_exited_wrapping_body(self):
        logger = AsyncMock(spec=AuditLogger)
        stage = _OkStage(audit_logger=logger)
        ctx = make_stage_ctx(
            stage=stage, state={"correlation_id": "c1"}
        )

        events = await collect_events(stage.run_async(ctx))

        # Body event reached caller
        assert any(
            e.actions and e.actions.state_delta.get("_probe") == "ran"
            for e in events
        )
        # Exactly two emits — entered then exited
        assert logger.emit.await_count == 2
        first, second = logger.emit.await_args_list
        assert first.kwargs["phase"] == "entered"
        assert first.kwargs["action"] == "stage_entered"
        assert second.kwargs["phase"] == "exited"
        assert second.kwargs["action"] == "stage_exited"
        assert second.kwargs["outcome"] == "ok"

    async def test_body_exception_emits_error_outcome_and_reraises(self):
        logger = AsyncMock(spec=AuditLogger)
        stage = _RaisesStage(audit_logger=logger)
        ctx = make_stage_ctx(
            stage=stage, state={"correlation_id": "c1"}
        )

        with pytest.raises(RuntimeError, match="boom"):
            await collect_events(stage.run_async(ctx))

        assert logger.emit.await_count == 2
        exit_call = logger.emit.await_args_list[1]
        assert exit_call.kwargs["outcome"] == "error:RuntimeError"

    async def test_missing_correlation_id_emits_empty_string(self):
        logger = AsyncMock(spec=AuditLogger)
        stage = _OkStage(audit_logger=logger)
        ctx = make_stage_ctx(stage=stage, state={})  # no correlation_id

        await collect_events(stage.run_async(ctx))

        assert logger.emit.await_args_list[0].kwargs["correlation_id"] == ""

    async def test_source_message_id_extracted_from_envelope_state(self):
        logger = AsyncMock(spec=AuditLogger)
        stage = _OkStage(audit_logger=logger)
        ctx = make_stage_ctx(
            stage=stage,
            state={
                "correlation_id": "c1",
                "envelope": {"message_id": "<envelope-msg-42>"},
            },
        )

        await collect_events(stage.run_async(ctx))

        first = logger.emit.await_args_list[0]
        assert first.kwargs["source_message_id"] == "<envelope-msg-42>"

    async def test_session_id_propagates_from_ctx(self):
        logger = AsyncMock(spec=AuditLogger)
        stage = _OkStage(audit_logger=logger)
        ctx = make_stage_ctx(stage=stage, state={"correlation_id": "c1"})
        # ctx.session.id is set by make_stage_ctx helper

        await collect_events(stage.run_async(ctx))

        first = logger.emit.await_args_list[0]
        assert first.kwargs["session_id"] == ctx.session.id
