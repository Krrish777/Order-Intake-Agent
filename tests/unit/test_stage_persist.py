"""Unit tests for :class:`backend.my_agent.stages.persist.PersistStage`.

The stage iterates ``state['parsed_docs']``, re-hydrates each
``entry['parsed']`` into a :class:`ParsedDocument`, pulls the CLARIFY
body string out of ``state['clarify_bodies']`` by
``"{filename}#{sub_doc_index}"`` if present, and awaits
``coordinator.process(parsed, envelope, order_index=..., clarify_body=...)``.
Results land in ``state['process_results']`` as
``{filename, sub_doc_index, result}`` entries (1:1 with ``parsed_docs``).
``skipped_docs`` is pass-through only.

The coordinator dep is exercised via :class:`unittest.mock.AsyncMock`
with ``spec=IntakeCoordinator`` so ``await coordinator.process(...)``
works and kwarg-level assertions (``clarify_body=...``, ``order_index=...``)
stay honest. Fake return values are real :class:`ProcessResult` instances
so ``result.model_dump(mode="json")`` round-trips without any mocking of
the serialisation layer.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from backend.audit.logger import AuditLogger

from backend.ingestion.email_envelope import EmailEnvelope
from backend.models.exception_record import ExceptionRecord, ExceptionStatus
from backend.models.master_records import AddressRecord
from backend.models.order_record import (
    CustomerSnapshot,
    OrderLine,
    OrderRecord,
    ProductSnapshot,
)
from backend.models.parsed_document import (
    ExtractedOrder,
    OrderLineItem,
    ParsedDocument,
)
from backend.models.validation_result import (
    LineItemValidation,
    RoutingDecision,
    ValidationResult,
)
from backend.my_agent.stages.persist import PERSIST_STAGE_NAME, PersistStage
from backend.persistence.coordinator import IntakeCoordinator, ProcessResult
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


def _parsed_document(order: ExtractedOrder | None = None) -> ParsedDocument:
    order = order if order is not None else _extracted_order()
    return ParsedDocument(
        classification="purchase_order",
        classification_rationale="Header reads 'Purchase Order'.",
        sub_documents=[order],
        page_count=1,
        detected_language="en",
    )


def _parsed_docs_entry(
    *,
    filename: str = "po-001.pdf",
    sub_doc_index: int = 0,
    order: ExtractedOrder | None = None,
) -> dict[str, object]:
    parsed = _parsed_document(order)
    return {
        "filename": filename,
        "sub_doc_index": sub_doc_index,
        "parsed": parsed.model_dump(mode="json"),
        "sub_doc": parsed.sub_documents[0].model_dump(mode="json"),
    }


def _envelope_dict() -> dict[str, object]:
    return EmailEnvelope(
        message_id="<orig-msg-001@customer.com>",
        in_reply_to=None,
        thread_id="thread-xyz",
        from_addr="buyer@birchvalley.com",
        to_addr="orders@us.com",
        subject="PO-12345",
        received_at=datetime(2026, 4, 22, 9, 30, tzinfo=timezone.utc),
        body_text="Please see attached PO.",
    ).model_dump(mode="json")


def _sample_order_record(
    *,
    source_message_id: str = "<orig-msg-001@customer.com>",
) -> OrderRecord:
    return OrderRecord(
        source_message_id=source_message_id,
        thread_id="thread-xyz",
        customer=CustomerSnapshot(
            customer_id="CUST-00001",
            name="Birch Valley Foods",
            bill_to=AddressRecord(
                street1="742 Industrial Pkwy",
                city="Cincinnati",
                state="OH",
                zip="45202",
                country="USA",
            ),
            payment_terms="Net 30",
            contact_email="ap@birchvalley.example.com",
        ),
        customer_id="CUST-00001",
        content_hash="a" * 64,
        lines=[
            OrderLine(
                line_number=0,
                product=ProductSnapshot(
                    sku="SKU-001",
                    short_description="Widget",
                    uom="EA",
                    price_at_time=9.5,
                ),
                quantity=10,
                line_total=95.0,
                confidence=1.0,
            )
        ],
        order_total=95.0,
        confidence=0.97,
        processed_by_agent_version="v0.1.0",
        created_at=datetime(2026, 4, 22, 0, 0, 0, tzinfo=timezone.utc),
    )


def _sample_validation_result(
    *,
    decision: RoutingDecision = RoutingDecision.CLARIFY,
    rationale: str = "Unmatched SKU on line 0.",
) -> ValidationResult:
    return ValidationResult(
        customer=None,
        lines=[
            LineItemValidation(
                line_index=0,
                matched_sku=None,
                match_tier="none",
                match_confidence=0.0,
                price_ok=True,
                qty_ok=True,
                notes=["no match for line input: 'SKU-001'"],
            )
        ],
        aggregate_confidence=0.82,
        decision=decision,
        rationale=rationale,
    )


def _sample_exception_record(
    *,
    source_message_id: str = "<orig-msg-001@customer.com>",
    status: ExceptionStatus = ExceptionStatus.PENDING_CLARIFY,
    clarify_body: str | None = None,
) -> ExceptionRecord:
    base = datetime(2026, 4, 22, 0, 0, 0, tzinfo=timezone.utc)
    decision = (
        RoutingDecision.CLARIFY
        if status is ExceptionStatus.PENDING_CLARIFY
        else RoutingDecision.ESCALATE
    )
    return ExceptionRecord(
        source_message_id=source_message_id,
        thread_id="thread-xyz",
        status=status,
        reason="Line 0: no match for 'SKU-001'.",
        clarify_body=clarify_body,
        parsed_doc=_parsed_document(),
        validation_result=_sample_validation_result(decision=decision),
        created_at=base,
        updated_at=base,
    )


def _make_ctx(
    stage: PersistStage,
    *,
    reply_handled: bool | None = None,
    envelope: dict[str, object] | None = None,
    parsed_docs: list[dict[str, object]] | None = None,
    clarify_bodies: dict[str, object] | None = None,
    skipped_docs: list[dict[str, object]] | None = None,
    validation_results: list[dict[str, object]] | None = None,
):
    state: dict[str, object] = {}
    if reply_handled is not None:
        state["reply_handled"] = reply_handled
    if envelope is not None:
        state["envelope"] = envelope
    if parsed_docs is not None:
        state["parsed_docs"] = parsed_docs
    if clarify_bodies is not None:
        state["clarify_bodies"] = clarify_bodies
    if skipped_docs is not None:
        state["skipped_docs"] = skipped_docs
    if validation_results is not None:
        state["validation_results"] = validation_results
    return make_stage_ctx(stage=stage, state=state)


# ---------------------------------------------------------------------- tests


def test_reply_handled_no_ops() -> None:
    """reply_handled=True → coordinator never called; process_results empty;
    skipped_docs preserved from prior state."""
    coordinator = AsyncMock(spec=IntakeCoordinator)
    stage = PersistStage(coordinator=coordinator, audit_logger=AsyncMock(spec=AuditLogger))
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
        envelope=_envelope_dict(),
        parsed_docs=[_parsed_docs_entry()],
        skipped_docs=prior_skipped,
    )

    events = collect_events(stage.run_async(ctx))
    delta = final_state_delta(events)

    assert coordinator.process.await_count == 0
    assert delta["process_results"] == []
    assert delta["skipped_docs"] == prior_skipped


def test_missing_envelope_raises() -> None:
    coordinator = AsyncMock(spec=IntakeCoordinator)
    stage = PersistStage(coordinator=coordinator, audit_logger=AsyncMock(spec=AuditLogger))
    # NOTE: envelope intentionally omitted.
    ctx = _make_ctx(stage, parsed_docs=[_parsed_docs_entry()])

    with pytest.raises(ValueError, match="requires IngestStage"):
        collect_events(stage.run_async(ctx))

    assert coordinator.process.await_count == 0


def test_missing_parsed_docs_raises() -> None:
    coordinator = AsyncMock(spec=IntakeCoordinator)
    stage = PersistStage(coordinator=coordinator, audit_logger=AsyncMock(spec=AuditLogger))
    # NOTE: parsed_docs intentionally omitted.
    ctx = _make_ctx(stage, envelope=_envelope_dict())

    with pytest.raises(ValueError, match="requires ParseStage"):
        collect_events(stage.run_async(ctx))

    assert coordinator.process.await_count == 0


def test_empty_parsed_docs_yields_empty_results() -> None:
    coordinator = AsyncMock(spec=IntakeCoordinator)
    stage = PersistStage(coordinator=coordinator, audit_logger=AsyncMock(spec=AuditLogger))
    ctx = _make_ctx(stage, envelope=_envelope_dict(), parsed_docs=[])

    events = collect_events(stage.run_async(ctx))
    delta = final_state_delta(events)

    assert coordinator.process.await_count == 0
    assert delta["process_results"] == []
    assert delta["skipped_docs"] == []


def test_single_auto_approve_path() -> None:
    """No clarify body in state → coordinator invoked with
    ``clarify_body=None``; result round-trips through model_dump."""
    coordinator = AsyncMock(spec=IntakeCoordinator)
    coordinator.process.return_value = ProcessResult(
        kind="order", order=_sample_order_record()
    )
    stage = PersistStage(coordinator=coordinator, audit_logger=AsyncMock(spec=AuditLogger))
    ctx = _make_ctx(
        stage,
        envelope=_envelope_dict(),
        parsed_docs=[
            _parsed_docs_entry(filename="po-001.pdf", sub_doc_index=0)
        ],
    )

    events = collect_events(stage.run_async(ctx))
    delta = final_state_delta(events)

    assert coordinator.process.await_count == 1
    _, kwargs = coordinator.process.call_args
    assert kwargs["order_index"] == 0
    assert kwargs["clarify_body"] is None
    # First positional arg should be the re-hydrated ParsedDocument.
    (parsed_arg, envelope_arg), _ = coordinator.process.call_args
    assert isinstance(parsed_arg, ParsedDocument)
    assert parsed_arg.sub_documents[0].po_number == "PO-12345"
    assert isinstance(envelope_arg, EmailEnvelope)

    results = delta["process_results"]
    assert isinstance(results, list)
    assert len(results) == 1
    only = results[0]
    assert only["filename"] == "po-001.pdf"
    assert only["sub_doc_index"] == 0
    assert only["result"]["kind"] == "order"
    assert only["result"]["order"]["source_message_id"] == (
        "<orig-msg-001@customer.com>"
    )

    assert any(e.author == PERSIST_STAGE_NAME for e in events)


def test_single_clarify_path_threads_body_through() -> None:
    """CLARIFY-tier entry with a clarify_body dict in state → coordinator
    must receive the body STRING (not the whole {subject, body} dict)."""
    coordinator = AsyncMock(spec=IntakeCoordinator)
    coordinator.process.return_value = ProcessResult(
        kind="exception",
        exception=_sample_exception_record(
            status=ExceptionStatus.PENDING_CLARIFY,
            clarify_body="Hi Pat, ...",
        ),
    )
    stage = PersistStage(coordinator=coordinator, audit_logger=AsyncMock(spec=AuditLogger))
    ctx = _make_ctx(
        stage,
        envelope=_envelope_dict(),
        parsed_docs=[
            _parsed_docs_entry(filename="x.pdf", sub_doc_index=0)
        ],
        clarify_bodies={
            "x.pdf#0": {
                "subject": "Re: clarification needed on PO-12345",
                "body": "Hi Pat, ...",
            }
        },
    )

    events = collect_events(stage.run_async(ctx))
    delta = final_state_delta(events)

    assert coordinator.process.await_count == 1
    _, kwargs = coordinator.process.call_args
    assert kwargs["clarify_body"] == "Hi Pat, ..."
    assert kwargs["order_index"] == 0

    results = delta["process_results"]
    assert len(results) == 1
    assert results[0]["result"]["kind"] == "exception"
    assert results[0]["result"]["exception"]["status"] == "pending_clarify"


def test_single_escalate_path_no_clarify_body() -> None:
    """ESCALATE-tier entry with no clarify_body in state →
    coordinator invoked with ``clarify_body=None``."""
    coordinator = AsyncMock(spec=IntakeCoordinator)
    coordinator.process.return_value = ProcessResult(
        kind="exception",
        exception=_sample_exception_record(
            status=ExceptionStatus.ESCALATED,
            clarify_body=None,
        ),
    )
    stage = PersistStage(coordinator=coordinator, audit_logger=AsyncMock(spec=AuditLogger))
    ctx = _make_ctx(
        stage,
        envelope=_envelope_dict(),
        parsed_docs=[
            _parsed_docs_entry(filename="doubt.pdf", sub_doc_index=0)
        ],
    )

    events = collect_events(stage.run_async(ctx))
    delta = final_state_delta(events)

    assert coordinator.process.await_count == 1
    _, kwargs = coordinator.process.call_args
    assert kwargs["clarify_body"] is None
    assert kwargs["order_index"] == 0

    results = delta["process_results"]
    assert len(results) == 1
    assert results[0]["result"]["kind"] == "exception"
    assert results[0]["result"]["exception"]["status"] == "escalated"


def test_multi_entry_preserves_order() -> None:
    """Three parsed_docs entries across two filenames → process_results
    in the same order. Exercises the ordering contract."""
    coordinator = AsyncMock(spec=IntakeCoordinator)
    coordinator.process.side_effect = [
        ProcessResult(
            kind="order",
            order=_sample_order_record(
                source_message_id="<orig-msg-001@customer.com>"
            ),
        ),
        ProcessResult(
            kind="exception",
            exception=_sample_exception_record(
                source_message_id="<orig-msg-001@customer.com>#1",
                status=ExceptionStatus.PENDING_CLARIFY,
                clarify_body="body-a",
            ),
        ),
        ProcessResult(
            kind="exception",
            exception=_sample_exception_record(
                source_message_id="<orig-msg-001@customer.com>",
                status=ExceptionStatus.ESCALATED,
            ),
        ),
    ]
    stage = PersistStage(coordinator=coordinator, audit_logger=AsyncMock(spec=AuditLogger))
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
    ctx = _make_ctx(
        stage,
        envelope=_envelope_dict(),
        parsed_docs=parsed_docs,
        clarify_bodies={
            "bundle.pdf#1": {
                "subject": "Re: clarification on PO-A2",
                "body": "body-a",
            }
        },
    )

    events = collect_events(stage.run_async(ctx))
    delta = final_state_delta(events)

    results = delta["process_results"]
    assert isinstance(results, list)
    assert len(results) == 3
    assert [(r["filename"], r["sub_doc_index"]) for r in results] == [
        ("bundle.pdf", 0),
        ("bundle.pdf", 1),
        ("other.pdf", 0),
    ]
    assert [r["result"]["kind"] for r in results] == [
        "order",
        "exception",
        "exception",
    ]
    assert coordinator.process.await_count == 3


def test_coordinator_raising_propagates() -> None:
    coordinator = AsyncMock(spec=IntakeCoordinator)
    coordinator.process.side_effect = RuntimeError("firestore unreachable")
    stage = PersistStage(coordinator=coordinator, audit_logger=AsyncMock(spec=AuditLogger))
    ctx = _make_ctx(
        stage,
        envelope=_envelope_dict(),
        parsed_docs=[_parsed_docs_entry()],
    )

    with pytest.raises(RuntimeError, match="firestore unreachable"):
        collect_events(stage.run_async(ctx))


def test_precomputed_validation_threaded_through_to_coordinator() -> None:
    """When state['validation_results'] has an entry keyed by
    (filename, sub_doc_index), PersistStage re-hydrates it and passes it
    to coordinator.process as ``precomputed_validation`` — so the
    coordinator can skip the redundant second validator.validate call."""
    coordinator = AsyncMock(spec=IntakeCoordinator)
    coordinator.process.return_value = ProcessResult(
        kind="order", order=_sample_order_record()
    )
    stage = PersistStage(coordinator=coordinator, audit_logger=AsyncMock(spec=AuditLogger))
    vr = _sample_validation_result(decision=RoutingDecision.AUTO_APPROVE)
    ctx = _make_ctx(
        stage,
        envelope=_envelope_dict(),
        parsed_docs=[_parsed_docs_entry(filename="po-001.pdf", sub_doc_index=0)],
        validation_results=[
            {
                "filename": "po-001.pdf",
                "sub_doc_index": 0,
                "validation": vr.model_dump(mode="json"),
            }
        ],
    )

    collect_events(stage.run_async(ctx))

    assert coordinator.process.await_count == 1
    _, kwargs = coordinator.process.call_args
    pre = kwargs["precomputed_validation"]
    assert isinstance(pre, ValidationResult)
    assert pre.decision is RoutingDecision.AUTO_APPROVE
    assert pre.aggregate_confidence == vr.aggregate_confidence


def test_missing_validation_results_falls_back_to_none() -> None:
    """If state['validation_results'] is absent or doesn't cover this
    (filename, sub_doc_index), PersistStage passes
    ``precomputed_validation=None`` and the coordinator runs its own
    validator. Preserves backwards-compatibility for paths that skip
    ValidateStage."""
    coordinator = AsyncMock(spec=IntakeCoordinator)
    coordinator.process.return_value = ProcessResult(
        kind="order", order=_sample_order_record()
    )
    stage = PersistStage(coordinator=coordinator, audit_logger=AsyncMock(spec=AuditLogger))
    ctx = _make_ctx(
        stage,
        envelope=_envelope_dict(),
        parsed_docs=[_parsed_docs_entry()],
        # validation_results intentionally omitted
    )

    collect_events(stage.run_async(ctx))

    _, kwargs = coordinator.process.call_args
    assert kwargs["precomputed_validation"] is None


def test_malformed_clarify_body_raises() -> None:
    """clarify_bodies entry missing the 'body' field → AssertionError.

    Locks the fail-fast behaviour: if ClarifyStage's output schema
    ever drifts from ``ClarifyEmail(subject, body)``, PersistStage
    must surface the drift loudly rather than silently writing
    ``clarify_body=None``.
    """
    coordinator = AsyncMock(spec=IntakeCoordinator)
    stage = PersistStage(coordinator=coordinator, audit_logger=AsyncMock(spec=AuditLogger))
    ctx = _make_ctx(
        stage,
        envelope=_envelope_dict(),
        parsed_docs=[
            _parsed_docs_entry(filename="x.pdf", sub_doc_index=0)
        ],
        clarify_bodies={"x.pdf#0": {"subject": "only"}},
    )

    with pytest.raises(AssertionError, match="missing the 'body' field"):
        collect_events(stage.run_async(ctx))

    assert coordinator.process.await_count == 0


@pytest.mark.asyncio
async def test_stage_emits_entered_and_exited_audit_events() -> None:
    coordinator = AsyncMock(spec=IntakeCoordinator)
    audit_logger = AsyncMock(spec=AuditLogger)
    stage = PersistStage(coordinator=coordinator, audit_logger=audit_logger)
    ctx = _make_ctx(
        stage,
        reply_handled=True,
        envelope=_envelope_dict(),
        parsed_docs=[],
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
