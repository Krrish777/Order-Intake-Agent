"""Unit tests for :class:`backend.my_agent.stages.finalize.FinalizeStage`.

The stage computes deterministic counts from ``state['process_results']``,
``state['skipped_docs']``, and ``state['reply_handled']``, seeds them as
flat state keys (``orders_created``, ``exceptions_opened``,
``docs_skipped``, ``reply_handled``) on ``ctx.session.state`` via direct
mutation (so the injected summary LlmAgent's ``INSTRUCTION_TEMPLATE`` can
interpolate the placeholders at model-call time), invokes the child
exactly once, captures ``run_summary`` from its final event's
``state_delta``, and publishes it at ``state['run_summary']``.

Unlike :class:`ClarifyStage`, FinalizeStage has **no reply_handled
short-circuit** — the summary agent always runs (the prompt template
surfaces the ``reply_handled`` flag so the model can frame the summary
accordingly).

The child LlmAgent dep is exercised via the shared
:class:`FakeChildLlmAgent` duck-type (from ``_stage_testing``) with
``output_key="run_summary"``. The fake yields a single :class:`Event`
with ``state_delta={"run_summary": {...}}`` and snapshots the four
flat-state placeholder keys on each invocation so tests can assert the
seeding order and values.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from backend.audit.logger import AuditLogger

from backend.my_agent.stages.finalize import FINALIZE_STAGE_NAME, FinalizeStage
from tests.unit._stage_testing import (
    FakeChildLlmAgent,
    collect_events,
    final_state_delta,
    make_stage_ctx,
)


# --------------------------------------------------------------------- helpers


def _make_summary_fake(
    *,
    responses: list[Any] | None = None,
) -> FakeChildLlmAgent:
    """Thin wrapper around :class:`FakeChildLlmAgent` for this test module.

    Centralises the ``output_key`` + ``capture_keys`` choices so each
    test reads as a behavioural assertion, not a fake-config dump.
    """
    return FakeChildLlmAgent(
        output_key="run_summary",
        responses=responses,
        capture_keys=[
            "orders_created",
            "exceptions_opened",
            "docs_skipped",
            "reply_handled",
        ],
        name="fake_summary_agent",
    )


def _process_result(
    *,
    filename: str = "po.pdf",
    sub_doc_index: int = 0,
    kind: str = "order",
) -> dict[str, Any]:
    """Shape-minimum ProcessResult entry as PersistStage would emit."""
    return {
        "filename": filename,
        "sub_doc_index": sub_doc_index,
        "result": {"kind": kind},
    }


def _make_ctx(
    stage: FinalizeStage,
    *,
    process_results: list[dict[str, Any]] | None = None,
    skipped_docs: list[dict[str, Any]] | None = None,
    reply_handled: bool | None = None,
):
    state: dict[str, Any] = {}
    if process_results is not None:
        state["process_results"] = process_results
    if skipped_docs is not None:
        state["skipped_docs"] = skipped_docs
    if reply_handled is not None:
        state["reply_handled"] = reply_handled
    return make_stage_ctx(stage=stage, state=state)


# ---------------------------------------------------------------------- tests


def test_runs_even_when_reply_handled() -> None:
    """reply_handled=True + empty process_results → summary agent IS
    invoked (no short-circuit). Counts are all 0, reply_handled True."""
    stub = {
        "orders_created": 0,
        "exceptions_opened": 0,
        "docs_skipped": 0,
        "summary": "Clarify reply handled; no new orders this run.",
    }
    fake = _make_summary_fake(responses=[stub])
    stage = FinalizeStage(summary_agent=fake, audit_logger=AsyncMock(spec=AuditLogger))
    ctx = _make_ctx(
        stage,
        process_results=[],
        reply_handled=True,
    )

    events = collect_events(stage.run_async(ctx))
    delta = final_state_delta(events)

    assert fake.call_count == 1
    assert delta["run_summary"] == stub
    snapshot = fake.capture_state[0]
    assert snapshot["reply_handled"] is True
    assert snapshot["orders_created"] == 0
    assert snapshot["exceptions_opened"] == 0
    assert snapshot["docs_skipped"] == 0
    assert any(e.author == FINALIZE_STAGE_NAME for e in events)


def test_counts_auto_approve_orders() -> None:
    """Two kind=='order' ProcessResults → orders_created seeded as 2."""
    fake = _make_summary_fake(responses=[])
    stage = FinalizeStage(summary_agent=fake, audit_logger=AsyncMock(spec=AuditLogger))
    ctx = _make_ctx(
        stage,
        process_results=[
            _process_result(filename="a.pdf", sub_doc_index=0, kind="order"),
            _process_result(filename="b.pdf", sub_doc_index=0, kind="order"),
        ],
    )

    collect_events(stage.run_async(ctx))

    assert fake.call_count == 1
    snapshot = fake.capture_state[0]
    assert snapshot["orders_created"] == 2
    assert snapshot["exceptions_opened"] == 0
    assert snapshot["docs_skipped"] == 0
    assert snapshot["reply_handled"] is False


def test_counts_exceptions() -> None:
    """Three kind=='exception' ProcessResults → exceptions_opened=3."""
    fake = _make_summary_fake(responses=[])
    stage = FinalizeStage(summary_agent=fake, audit_logger=AsyncMock(spec=AuditLogger))
    ctx = _make_ctx(
        stage,
        process_results=[
            _process_result(filename="a.pdf", sub_doc_index=0, kind="exception"),
            _process_result(filename="b.pdf", sub_doc_index=0, kind="exception"),
            _process_result(filename="c.pdf", sub_doc_index=0, kind="exception"),
        ],
    )

    collect_events(stage.run_async(ctx))

    assert fake.call_count == 1
    snapshot = fake.capture_state[0]
    assert snapshot["orders_created"] == 0
    assert snapshot["exceptions_opened"] == 3
    assert snapshot["docs_skipped"] == 0


def test_counts_mixed_and_skipped() -> None:
    """1 order + 2 exceptions + 1 duplicate (total 4 process_results) +
    3 skipped_docs → orders_created=1, exceptions_opened=2,
    docs_skipped=3. Duplicates intentionally don't count as orders or
    exceptions (RunSummary schema has no duplicates field)."""
    fake = _make_summary_fake(responses=[])
    stage = FinalizeStage(summary_agent=fake, audit_logger=AsyncMock(spec=AuditLogger))
    ctx = _make_ctx(
        stage,
        process_results=[
            _process_result(filename="a.pdf", sub_doc_index=0, kind="order"),
            _process_result(filename="b.pdf", sub_doc_index=0, kind="exception"),
            _process_result(filename="c.pdf", sub_doc_index=0, kind="exception"),
            _process_result(filename="d.pdf", sub_doc_index=0, kind="duplicate"),
        ],
        skipped_docs=[
            {"filename": "x.pdf", "stage": "classify_stage", "reason": "invoice"},
            {"filename": "y.pdf", "stage": "classify_stage", "reason": "rfq"},
            {"filename": "z.pdf", "stage": "classify_stage", "reason": "other"},
        ],
    )

    collect_events(stage.run_async(ctx))

    assert fake.call_count == 1
    snapshot = fake.capture_state[0]
    assert snapshot["orders_created"] == 1
    assert snapshot["exceptions_opened"] == 2
    assert snapshot["docs_skipped"] == 3
    assert snapshot["reply_handled"] is False


def test_empty_state_yields_zero_counts() -> None:
    """Completely empty state → all counts 0, reply_handled False.
    Summary agent STILL invoked once (no short-circuit)."""
    fake = _make_summary_fake(responses=[])
    stage = FinalizeStage(summary_agent=fake, audit_logger=AsyncMock(spec=AuditLogger))
    ctx = _make_ctx(stage)

    collect_events(stage.run_async(ctx))

    assert fake.call_count == 1
    snapshot = fake.capture_state[0]
    assert snapshot["orders_created"] == 0
    assert snapshot["exceptions_opened"] == 0
    assert snapshot["docs_skipped"] == 0
    assert snapshot["reply_handled"] is False


def test_summary_agent_never_emits_run_summary_raises() -> None:
    """Fake responds with ``None`` (emit event without run_summary key)
    → FinalizeStage raises RuntimeError."""
    fake = _make_summary_fake(responses=[None])
    stage = FinalizeStage(summary_agent=fake, audit_logger=AsyncMock(spec=AuditLogger))
    ctx = _make_ctx(stage, process_results=[])

    with pytest.raises(RuntimeError, match="did not produce run_summary"):
        collect_events(stage.run_async(ctx))

    assert fake.call_count == 1


def test_run_summary_lands_on_state() -> None:
    """Fake returns a concrete run_summary dict → it shows up verbatim
    on state['run_summary'] in the final delta."""
    payload = {
        "orders_created": 2,
        "exceptions_opened": 1,
        "docs_skipped": 0,
        "summary": "Processed 3 documents: 2 orders persisted, 1 needs clarification.",
    }
    fake = _make_summary_fake(responses=[payload])
    stage = FinalizeStage(summary_agent=fake, audit_logger=AsyncMock(spec=AuditLogger))
    ctx = _make_ctx(
        stage,
        process_results=[
            _process_result(filename="a.pdf", sub_doc_index=0, kind="order"),
            _process_result(filename="b.pdf", sub_doc_index=0, kind="order"),
            _process_result(filename="c.pdf", sub_doc_index=0, kind="exception"),
        ],
    )

    events = collect_events(stage.run_async(ctx))
    delta = final_state_delta(events)

    assert delta["run_summary"] == payload
    assert any(e.author == FINALIZE_STAGE_NAME for e in events)


@pytest.mark.asyncio
async def test_run_finalized_lifecycle_emit() -> None:
    """After the summary agent yields and counts are captured, one lifecycle
    emit with action='run_finalized' and correct payload fields."""
    stub = {
        "orders_created": 1,
        "exceptions_opened": 1,
        "docs_skipped": 1,
        "summary": "1 order, 1 exception, 1 skipped.",
    }
    fake = _make_summary_fake(responses=[stub])
    audit_logger = AsyncMock(spec=AuditLogger)
    stage = FinalizeStage(summary_agent=fake, audit_logger=audit_logger)
    ctx = _make_ctx(
        stage,
        process_results=[
            _process_result(filename="a.pdf", sub_doc_index=0, kind="order"),
            _process_result(filename="b.pdf", sub_doc_index=0, kind="exception"),
        ],
        skipped_docs=[
            {"filename": "c.pdf", "stage": "classify_stage", "reason": "invoice"},
        ],
        reply_handled=False,
    )

    async for _ in stage.run_async(ctx):
        pass

    lifecycle_calls = [
        c
        for c in audit_logger.emit.await_args_list
        if c.kwargs.get("action") == "run_finalized"
    ]
    assert len(lifecycle_calls) == 1, (
        f"Expected 1 run_finalized emit, got {len(lifecycle_calls)}"
    )
    call_kwargs = lifecycle_calls[0].kwargs
    assert call_kwargs["stage"] == "lifecycle"
    assert call_kwargs["phase"] == "lifecycle"
    assert call_kwargs["outcome"] == "ok"
    payload = call_kwargs["payload"]
    assert payload["orders_created"] == 1
    assert payload["exceptions_opened"] == 1
    assert payload["docs_skipped"] == 1
    assert payload["reply_handled"] is False


@pytest.mark.asyncio
async def test_stage_emits_entered_and_exited_audit_events() -> None:
    stub = {
        "orders_created": 0,
        "exceptions_opened": 0,
        "docs_skipped": 0,
        "summary": "test run",
    }
    fake = _make_summary_fake(responses=[stub])
    audit_logger = AsyncMock(spec=AuditLogger)
    stage = FinalizeStage(summary_agent=fake, audit_logger=audit_logger)
    ctx = _make_ctx(stage, process_results=[], reply_handled=False)

    try:
        async for _ in stage.run_async(ctx):
            pass
    except Exception:
        pass

    calls = audit_logger.emit.await_args_list
    phases = [c.kwargs["phase"] for c in calls]
    assert "entered" in phases
    assert "exited" in phases
