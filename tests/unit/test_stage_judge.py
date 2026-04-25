"""Unit tests for JudgeStage — the Track B outbound-email quality gate.

Harness uses the shared `FakeChildLlmAgent` + `make_stage_ctx` helpers
from `tests/unit/_stage_testing.py`. Deps are Protocol-typed stores
satisfied by `AsyncMock(spec=...)`.

Covered scenarios:
  1. happy-path pass for kind='order'
  2. happy-path pass for kind='exception'
  3. rejected verdict persists findings onto record
  4. LLM exception -> fail-closed synth verdict
  5. malformed output (ValidationError) -> fail-closed
  6. duplicate entry skipped (no verdict write)
  7. reply_handled short-circuit (no child invocation)
  8. stage name + canonical position contract
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest

from backend.ingestion.email_envelope import EmailEnvelope
from backend.models.judge_verdict import (
    JudgeFinding,
    JudgeFindingKind,
    JudgeVerdict,
)
from backend.my_agent.stages.judge import JUDGE_STAGE_NAME, JudgeStage
from backend.persistence.base import ExceptionStore, OrderStore
from tests.unit._stage_testing import (
    FakeChildLlmAgent,
    collect_events,
    final_state_delta,
    make_stage_ctx,
)


def _order_process_result(*, source_id: str = "src-order-1") -> dict:
    """Minimal ProcessResult-dict for an AUTO_APPROVE order."""
    return {
        "filename":       "body.txt",
        "sub_doc_index":  0,
        "result": {
            "kind":               "order",
            "source_message_id":  source_id,
            "order": {
                "source_message_id": source_id,
                "customer": {"name": "MM Machine", "customer_id": "CUST-00042"},
                "lines": [
                    {"quantity": 20, "product": {
                        "sku": "WID-RED-100", "short_description": "widget red",
                        "uom": "EA", "price_at_time": 4.20,
                    }, "line_total": 84.00},
                ],
                "order_total":       84.00,
                "confirmation_body": "Hi MM Machine, thank you for your order of 20 EA WID-RED-100.",
                "status":            "AUTO_APPROVE",
            },
        },
    }


def _exception_process_result(*, source_id: str = "src-exc-1") -> dict:
    return {
        "filename":       "body.txt",
        "sub_doc_index":  0,
        "result": {
            "kind":               "exception",
            "source_message_id":  source_id,
            "exception": {
                "source_message_id": source_id,
                "customer":          {"name": "MM Machine"},
                "exception_type":    "MISSING_SHIP_TO",
                "reason":            "ship-to address was not provided",
                "missing_fields":    ["ship_to_address"],
                "clarify_body":      "Hi MM Machine, could you share the ship-to address for this order?",
                "status":            "PENDING_CLARIFY",
            },
        },
    }


def _duplicate_process_result(*, source_id: str = "src-dup-1") -> dict:
    return {
        "filename":       "body.txt",
        "sub_doc_index":  0,
        "result": {
            "kind":              "duplicate",
            "source_message_id": source_id,
            "order":             None,
            "exception":         None,
        },
    }


def _minimal_envelope_dict() -> dict:
    """Build a valid EmailEnvelope dict using the real model.

    Plan's fixture used wrong field names (from_address, to_address instead
    of from_addr, to_addr; missing body_text/message_id). Correction 6:
    create via EmailEnvelope.model_dump() so the keys always match the schema.
    """
    env = EmailEnvelope(
        message_id="msg-judge-test-1@example.com",
        from_addr="ops@mm-machine.example",
        to_addr="orders@gr-mro.example",
        subject="PO #2026-04-24",
        received_at=datetime(2026, 4, 24, 15, 0, tzinfo=timezone.utc),
        body_text="Please confirm order.",
    )
    return env.model_dump(mode="json")


async def test_judge_stage_pass_on_order_persists_and_stashes_verdict():
    audit = AsyncMock()
    order_store = AsyncMock(spec=OrderStore)
    exc_store   = AsyncMock(spec=ExceptionStore)
    pass_verdict = JudgeVerdict(status="pass", reason="", findings=[])
    child = FakeChildLlmAgent(
        name="judge_agent",
        output_key="judge_verdict",
        responses=[pass_verdict.model_dump(mode="json")],
    )

    stage = JudgeStage(
        judge_agent=child, order_store=order_store,
        exception_store=exc_store, audit_logger=audit,
    )

    ctx = make_stage_ctx(
        stage=stage,
        state={
            "envelope":        _minimal_envelope_dict(),
            "process_results": [_order_process_result()],
        },
    )

    events = await collect_events(stage.run_async(ctx))
    delta  = final_state_delta(events)

    assert "judge_verdicts" in delta
    assert delta["judge_verdicts"]["src-order-1"]["status"] == "pass"
    order_store.update_with_judge_verdict.assert_awaited_once()
    exc_store.update_with_judge_verdict.assert_not_awaited()


async def test_judge_stage_pass_on_exception_writes_to_exception_store():
    audit = AsyncMock()
    order_store = AsyncMock(spec=OrderStore)
    exc_store   = AsyncMock(spec=ExceptionStore)
    pass_verdict = JudgeVerdict(status="pass", reason="", findings=[])
    child = FakeChildLlmAgent(
        name="judge_agent",
        output_key="judge_verdict",
        responses=[pass_verdict.model_dump(mode="json")],
    )

    stage = JudgeStage(
        judge_agent=child, order_store=order_store,
        exception_store=exc_store, audit_logger=audit,
    )

    ctx = make_stage_ctx(
        stage=stage,
        state={
            "envelope":        _minimal_envelope_dict(),
            "process_results": [_exception_process_result()],
        },
    )

    await collect_events(stage.run_async(ctx))

    exc_store.update_with_judge_verdict.assert_awaited_once()
    order_store.update_with_judge_verdict.assert_not_awaited()


async def test_judge_stage_rejected_persists_findings_and_stashes_verdict():
    audit = AsyncMock()
    order_store = AsyncMock(spec=OrderStore)
    exc_store   = AsyncMock(spec=ExceptionStore)
    rejected = JudgeVerdict(
        status="rejected",
        reason="hallucinated total",
        findings=[
            JudgeFinding(
                kind=JudgeFindingKind.HALLUCINATED_FACT,
                quote="$999.99",
                explanation="order.order_total is 84.00 but body claims $999.99",
            )
        ],
    )
    child = FakeChildLlmAgent(
        name="judge_agent",
        output_key="judge_verdict",
        responses=[rejected.model_dump(mode="json")],
    )

    stage = JudgeStage(
        judge_agent=child, order_store=order_store,
        exception_store=exc_store, audit_logger=audit,
    )

    ctx = make_stage_ctx(
        stage=stage,
        state={
            "envelope":        _minimal_envelope_dict(),
            "process_results": [_order_process_result()],
        },
    )

    events = await collect_events(stage.run_async(ctx))
    delta  = final_state_delta(events)

    assert delta["judge_verdicts"]["src-order-1"]["status"] == "rejected"
    assert (delta["judge_verdicts"]["src-order-1"]["findings"][0]["kind"]
            == "hallucinated_fact")

    # Order store got the full verdict (field-mask update).
    call = order_store.update_with_judge_verdict.await_args
    assert call.args[0] == "src-order-1"
    persisted_verdict = call.args[1]
    assert persisted_verdict.status == "rejected"
    assert len(persisted_verdict.findings) == 1


async def test_judge_stage_fails_closed_on_child_exception():
    class RaisingChild:
        """Duck-typed child that raises on run_async."""
        name = "judge_agent"
        async def run_async(self, ctx):    # noqa: D401 — protocol stub
            raise RuntimeError("simulated Gemini outage")
            yield  # pragma: no cover

    audit = AsyncMock()
    order_store = AsyncMock(spec=OrderStore)
    exc_store   = AsyncMock(spec=ExceptionStore)

    stage = JudgeStage(
        judge_agent=RaisingChild(), order_store=order_store,
        exception_store=exc_store, audit_logger=audit,
    )

    ctx = make_stage_ctx(
        stage=stage,
        state={
            "envelope":        _minimal_envelope_dict(),
            "process_results": [_order_process_result()],
        },
    )

    events = await collect_events(stage.run_async(ctx))
    delta  = final_state_delta(events)

    v = delta["judge_verdicts"]["src-order-1"]
    assert v["status"] == "rejected"
    assert v["reason"].startswith("judge_unavailable:")
    assert v["findings"] == []

    # Synth verdict still persisted so SendStage can read it.
    order_store.update_with_judge_verdict.assert_awaited_once()
    # Audit emitted judge_unavailable (action= kwarg).
    actions = [c.kwargs.get("action") for c in audit.emit.await_args_list]
    assert "judge_unavailable" in actions


async def test_judge_stage_fails_closed_on_validation_error():
    """Child produces a malformed payload (missing required field)
    that Pydantic cannot validate."""
    audit = AsyncMock()
    order_store = AsyncMock(spec=OrderStore)
    exc_store   = AsyncMock(spec=ExceptionStore)
    # Intentionally missing 'status' — model_validate will raise.
    bad_payload = {"reason": "malformed", "findings": []}
    child = FakeChildLlmAgent(
        name="judge_agent",
        output_key="judge_verdict",
        responses=[bad_payload],
    )

    stage = JudgeStage(
        judge_agent=child, order_store=order_store,
        exception_store=exc_store, audit_logger=audit,
    )

    ctx = make_stage_ctx(
        stage=stage,
        state={
            "envelope":        _minimal_envelope_dict(),
            "process_results": [_order_process_result()],
        },
    )

    events = await collect_events(stage.run_async(ctx))
    delta  = final_state_delta(events)

    v = delta["judge_verdicts"]["src-order-1"]
    assert v["status"] == "rejected"
    assert "judge_unavailable:" in v["reason"]


async def test_judge_stage_skips_duplicate_entries():
    """Duplicates were judged on a prior run; no new verdict written,
    nothing persisted, nothing audited for judge_verdict_*."""
    audit = AsyncMock()
    order_store = AsyncMock(spec=OrderStore)
    exc_store   = AsyncMock(spec=ExceptionStore)
    child = FakeChildLlmAgent(
        name="judge_agent", output_key="judge_verdict",
        responses=[{"status": "pass", "reason": "", "findings": []}],
    )

    stage = JudgeStage(
        judge_agent=child, order_store=order_store,
        exception_store=exc_store, audit_logger=audit,
    )

    ctx = make_stage_ctx(
        stage=stage,
        state={
            "envelope":        _minimal_envelope_dict(),
            "process_results": [_duplicate_process_result()],
        },
    )

    events = await collect_events(stage.run_async(ctx))
    delta  = final_state_delta(events)

    assert delta["judge_verdicts"] == {}
    order_store.update_with_judge_verdict.assert_not_awaited()
    exc_store.update_with_judge_verdict.assert_not_awaited()


async def test_judge_stage_short_circuits_on_reply_handled():
    """If reply_handled=True (ReplyShortCircuitStage fired), JudgeStage
    emits an empty delta and does not invoke the child or touch stores."""
    audit = AsyncMock()
    order_store = AsyncMock(spec=OrderStore)
    exc_store   = AsyncMock(spec=ExceptionStore)
    child = AsyncMock()
    child.name = "judge_agent"

    stage = JudgeStage(
        judge_agent=child, order_store=order_store,
        exception_store=exc_store, audit_logger=audit,
    )

    ctx = make_stage_ctx(
        stage=stage,
        state={
            "envelope":        _minimal_envelope_dict(),
            "process_results": [_order_process_result()],
            "reply_handled":   True,
        },
    )

    events = await collect_events(stage.run_async(ctx))
    delta  = final_state_delta(events)

    assert delta == {"judge_verdicts": {}}
    child.run_async.assert_not_called()
    order_store.update_with_judge_verdict.assert_not_awaited()


def test_judge_stage_name_constant_is_exported():
    assert JUDGE_STAGE_NAME == "judge_stage"
    # Construction contract: kwargs-only, no positional deps.
    with pytest.raises(TypeError):
        JudgeStage(  # positional should raise — kwarg-only contract
            AsyncMock(), AsyncMock(spec=OrderStore),
            AsyncMock(spec=ExceptionStore), AsyncMock(),
        )
