"""Unit tests for :class:`backend.my_agent.stages.validate.ValidateStage`.

The stage iterates ``state['parsed_docs']``, re-hydrates each
``entry['sub_doc']`` back into an :class:`ExtractedOrder`, and awaits
the injected :class:`OrderValidator` coroutine. Results land in
``state['validation_results']`` as ``{filename, sub_doc_index, validation}``
entries (1:1 with ``parsed_docs``). ``skipped_docs`` is pass-through
only — a CLARIFY or ESCALATE routing decision is a business outcome,
not a pipeline skip.

The validator dep is exercised via :class:`unittest.mock.AsyncMock` with
``spec=OrderValidator`` so ``await validator.validate(order)`` works
and ``side_effect`` / ``return_value`` + ``call_count`` assertions stay
honest.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from backend.audit.logger import AuditLogger

from backend.models.parsed_document import ExtractedOrder, OrderLineItem
from backend.models.validation_result import (
    LineItemValidation,
    RoutingDecision,
    ValidationResult,
)
from backend.my_agent.stages.validate import VALIDATE_STAGE_NAME, ValidateStage
from backend.tools.order_validator import OrderValidator
from tests.unit._stage_testing import collect_events, final_state_delta, make_stage_ctx


# --------------------------------------------------------------------- helpers


def _extracted_order(
    *,
    po_number: str = "PO-12345",
    customer_name: str = "Birch Valley Foods",
    sku: str = "SKU-001",
) -> ExtractedOrder:
    return ExtractedOrder(
        customer_name=customer_name,
        po_number=po_number,
        line_items=[
            OrderLineItem(
                sku=sku,
                description="Widget",
                quantity=10.0,
                unit_of_measure="EA",
                unit_price=9.5,
            )
        ],
    )


def _parsed_docs_entry(
    *,
    filename: str = "po-001.pdf",
    sub_doc_index: int = 0,
    order: ExtractedOrder | None = None,
) -> dict[str, object]:
    order = order if order is not None else _extracted_order()
    return {
        "filename": filename,
        "sub_doc_index": sub_doc_index,
        "parsed": {
            "classification": "purchase_order",
            "classification_rationale": "Header reads 'Purchase Order'.",
            "sub_documents": [order.model_dump(mode="json")],
            "page_count": 1,
            "detected_language": "en",
        },
        "sub_doc": order.model_dump(mode="json"),
    }


def _validation_result(
    *,
    decision: RoutingDecision = RoutingDecision.AUTO_APPROVE,
    aggregate_confidence: float = 0.97,
    rationale: str = "All lines matched exactly.",
) -> ValidationResult:
    return ValidationResult(
        customer=None,
        lines=[
            LineItemValidation(
                line_index=0,
                matched_sku="SKU-001",
                match_tier="exact",
                match_confidence=1.0,
                price_ok=True,
                qty_ok=True,
                notes=[],
            )
        ],
        aggregate_confidence=aggregate_confidence,
        decision=decision,
        rationale=rationale,
    )


def _make_ctx(
    stage: ValidateStage,
    *,
    reply_handled: bool | None = None,
    parsed_docs: list[dict[str, object]] | None = None,
    skipped_docs: list[dict[str, object]] | None = None,
):
    state: dict[str, object] = {}
    if reply_handled is not None:
        state["reply_handled"] = reply_handled
    if parsed_docs is not None:
        state["parsed_docs"] = parsed_docs
    if skipped_docs is not None:
        state["skipped_docs"] = skipped_docs
    return make_stage_ctx(stage=stage, state=state)


# ---------------------------------------------------------------------- tests


def test_reply_handled_no_ops() -> None:
    """reply_handled=True → validator never called; validation_results empty;
    skipped_docs preserved from prior state."""
    validator = AsyncMock(spec=OrderValidator)
    stage = ValidateStage(validator=validator, audit_logger=AsyncMock(spec=AuditLogger))
    prior_skipped = [
        {
            "filename": "invoice.pdf",
            "stage": "classify_stage",
            "reason": "intent=invoice (confidence=0.88)",
        }
    ]
    ctx = _make_ctx(
        stage,
        reply_handled=True,
        parsed_docs=[_parsed_docs_entry()],
        skipped_docs=prior_skipped,
    )

    events = collect_events(stage.run_async(ctx))
    delta = final_state_delta(events)

    assert validator.validate.await_count == 0
    assert delta["validation_results"] == []
    assert delta["skipped_docs"] == prior_skipped


def test_missing_parsed_docs_raises() -> None:
    validator = AsyncMock(spec=OrderValidator)
    stage = ValidateStage(validator=validator, audit_logger=AsyncMock(spec=AuditLogger))
    # NOTE: parsed_docs intentionally omitted.
    ctx = _make_ctx(stage)

    with pytest.raises(ValueError, match="requires ParseStage"):
        collect_events(stage.run_async(ctx))

    assert validator.validate.await_count == 0


def test_empty_parsed_docs_yields_empty_results() -> None:
    validator = AsyncMock(spec=OrderValidator)
    stage = ValidateStage(validator=validator, audit_logger=AsyncMock(spec=AuditLogger))
    ctx = _make_ctx(stage, parsed_docs=[])

    events = collect_events(stage.run_async(ctx))
    delta = final_state_delta(events)

    assert validator.validate.await_count == 0
    assert delta["validation_results"] == []
    assert delta["skipped_docs"] == []


def test_single_parsed_doc_auto_approve() -> None:
    validator = AsyncMock(spec=OrderValidator)
    validator.validate.return_value = _validation_result(
        decision=RoutingDecision.AUTO_APPROVE, aggregate_confidence=0.97
    )
    stage = ValidateStage(validator=validator, audit_logger=AsyncMock(spec=AuditLogger))
    ctx = _make_ctx(
        stage,
        parsed_docs=[
            _parsed_docs_entry(filename="po-001.pdf", sub_doc_index=0)
        ],
    )

    events = collect_events(stage.run_async(ctx))
    delta = final_state_delta(events)

    results = delta["validation_results"]
    assert isinstance(results, list)
    assert len(results) == 1
    only = results[0]
    assert only["filename"] == "po-001.pdf"
    assert only["sub_doc_index"] == 0
    assert only["validation"]["decision"] == "auto_approve"
    assert only["validation"]["aggregate_confidence"] == 0.97

    # The validator saw a real ExtractedOrder re-hydrated from the dict.
    assert validator.validate.await_count == 1
    (arg,), _ = validator.validate.call_args
    assert isinstance(arg, ExtractedOrder)
    assert arg.po_number == "PO-12345"

    # At least one event is authored by the stage.
    assert any(e.author == VALIDATE_STAGE_NAME for e in events)


def test_multi_parsed_docs_mixed_routing() -> None:
    """Three parsed_docs entries → three validation_results in the same
    order, one each AUTO / CLARIFY / ESCALATE. Exercises the ordering
    contract."""
    validator = AsyncMock(spec=OrderValidator)
    validator.validate.side_effect = [
        _validation_result(
            decision=RoutingDecision.AUTO_APPROVE, aggregate_confidence=0.97
        ),
        _validation_result(
            decision=RoutingDecision.CLARIFY,
            aggregate_confidence=0.85,
            rationale="Unmatched SKU on line 0.",
        ),
        _validation_result(
            decision=RoutingDecision.ESCALATE,
            aggregate_confidence=0.40,
            rationale="Customer could not be resolved.",
        ),
    ]
    stage = ValidateStage(validator=validator, audit_logger=AsyncMock(spec=AuditLogger))
    parsed_docs = [
        _parsed_docs_entry(
            filename="bundle.pdf",
            sub_doc_index=0,
            order=_extracted_order(po_number="PO-A1"),
        ),
        _parsed_docs_entry(
            filename="bundle.pdf",
            sub_doc_index=1,
            order=_extracted_order(po_number="PO-A2"),
        ),
        _parsed_docs_entry(
            filename="other.pdf",
            sub_doc_index=0,
            order=_extracted_order(po_number="PO-B1"),
        ),
    ]
    ctx = _make_ctx(stage, parsed_docs=parsed_docs)

    events = collect_events(stage.run_async(ctx))
    delta = final_state_delta(events)

    results = delta["validation_results"]
    assert isinstance(results, list)
    assert len(results) == 3
    assert [r["filename"] for r in results] == [
        "bundle.pdf",
        "bundle.pdf",
        "other.pdf",
    ]
    assert [r["sub_doc_index"] for r in results] == [0, 1, 0]
    assert [r["validation"]["decision"] for r in results] == [
        "auto_approve",
        "clarify",
        "escalate",
    ]
    assert validator.validate.await_count == 3


def test_validator_raising_propagates() -> None:
    validator = AsyncMock(spec=OrderValidator)
    validator.validate.side_effect = RuntimeError("master data unreachable")
    stage = ValidateStage(validator=validator, audit_logger=AsyncMock(spec=AuditLogger))
    ctx = _make_ctx(stage, parsed_docs=[_parsed_docs_entry()])

    with pytest.raises(RuntimeError, match="master data unreachable"):
        collect_events(stage.run_async(ctx))


def test_skipped_docs_passthrough() -> None:
    """Pre-seed state with ClassifyStage + ParseStage skipped entries; run
    with one parsed_doc; assert skipped_docs is unchanged (all prior
    entries preserved, no new entries added by ValidateStage)."""
    validator = AsyncMock(spec=OrderValidator)
    validator.validate.return_value = _validation_result(
        decision=RoutingDecision.CLARIFY, aggregate_confidence=0.82
    )
    stage = ValidateStage(validator=validator, audit_logger=AsyncMock(spec=AuditLogger))
    prior_skipped = [
        {
            "filename": "invoice.pdf",
            "stage": "classify_stage",
            "reason": "intent=invoice (confidence=0.88)",
        },
        {
            "filename": "ambiguous.pdf",
            "stage": "parse_stage",
            "reason": "parser returned zero sub_documents",
        },
    ]
    ctx = _make_ctx(
        stage,
        parsed_docs=[_parsed_docs_entry()],
        skipped_docs=prior_skipped,
    )

    events = collect_events(stage.run_async(ctx))
    delta = final_state_delta(events)

    # CLARIFY is a business outcome; the order still flows downstream,
    # so ValidateStage does NOT append to skipped_docs.
    assert delta["skipped_docs"] == prior_skipped
    assert len(delta["validation_results"]) == 1
    assert delta["validation_results"][0]["validation"]["decision"] == "clarify"


@pytest.mark.asyncio
async def test_stage_emits_entered_and_exited_audit_events() -> None:
    audit_logger = AsyncMock(spec=AuditLogger)
    validator = AsyncMock(spec=OrderValidator)
    stage = ValidateStage(validator=validator, audit_logger=audit_logger)
    ctx = _make_ctx(stage, reply_handled=True, parsed_docs=[])

    try:
        async for _ in stage.run_async(ctx):
            pass
    except Exception:
        pass

    calls = audit_logger.emit.await_args_list
    phases = [c.kwargs["phase"] for c in calls]
    assert "entered" in phases
    assert "exited" in phases


@pytest.mark.asyncio
async def test_validate_emits_routing_decided_per_sub_doc() -> None:
    """For each entry in parsed_docs, ValidateStage emits a lifecycle
    'routing_decided' event with outcome=<decision.value> and payload
    carrying confidence + customer_id."""
    validation = _validation_result(
        decision=RoutingDecision.AUTO_APPROVE,
        aggregate_confidence=0.97,
    )

    validator = AsyncMock(spec=OrderValidator)
    validator.validate = AsyncMock(return_value=validation)
    audit_logger = AsyncMock(spec=AuditLogger)

    stage = ValidateStage(validator=validator, audit_logger=audit_logger)
    ctx = make_stage_ctx(
        stage=stage,
        state={
            "correlation_id": "c1",
            "envelope": {"message_id": "m1"},
            "parsed_docs": [
                _parsed_docs_entry(filename="po-001.pdf", sub_doc_index=0),
            ],
        },
    )
    await collect_events(stage.run_async(ctx))

    routing_calls = [
        c for c in audit_logger.emit.await_args_list
        if c.kwargs.get("action") == "routing_decided"
    ]
    assert len(routing_calls) == 1
    assert routing_calls[0].kwargs["outcome"] == "auto_approve"
    assert "confidence" in routing_calls[0].kwargs["payload"]
    assert "customer_id" in routing_calls[0].kwargs["payload"]
