"""Unit tests for :class:`backend.my_agent.stages.classify.ClassifyStage`.

The stage iterates ``envelope.attachments``, calls the injected sync
``classify_fn`` (wrapped in :func:`asyncio.to_thread`), and splits the
results into ``state['classified_docs']`` (purchase_order intent only)
and ``state['skipped_docs']`` (everything else, with filename/stage/reason).

The ctx/collect/delta helpers now live in
:mod:`tests.unit._stage_testing`; ``_make_ctx`` here is a thin wrapper
that forwards the stage-specific state keys.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.audit.logger import AuditLogger
from backend.ingestion.email_envelope import EmailAttachment, EmailEnvelope
from backend.models.classified_document import ClassifiedDocument
from backend.my_agent.stages.classify import CLASSIFY_STAGE_NAME, ClassifyStage
from tests.unit._stage_testing import collect_events, final_state_delta, make_stage_ctx


# --------------------------------------------------------------------- helpers


def _make_attachment(
    filename: str = "po-001.pdf",
    content: bytes = b"%PDF-1.4 fake",
    content_type: str = "application/pdf",
) -> EmailAttachment:
    return EmailAttachment(
        filename=filename, content_type=content_type, content=content
    )


def _make_envelope(
    attachments: list[EmailAttachment] | None = None,
) -> EmailEnvelope:
    return EmailEnvelope(
        message_id="<msg-001@customer.com>",
        from_addr="buyer@birchvalley.com",
        to_addr="orders@us.com",
        subject="PO 12345 — please confirm",
        received_at=datetime(2026, 4, 22, 9, 30, tzinfo=timezone.utc),
        body_text="See attached PO.",
        attachments=attachments if attachments is not None else [_make_attachment()],
    )


def _classified(
    *,
    filename: str,
    intent: str = "purchase_order",
    confidence: float = 0.93,
    reasoning: str = "Header reads 'Purchase Order'; line-item table present.",
    document_format: str = "pdf",
    mime_type: str = "application/pdf",
    byte_size: int = 2048,
) -> ClassifiedDocument:
    return ClassifiedDocument(
        document_intent=intent,  # type: ignore[arg-type]
        intent_confidence=confidence,
        intent_reasoning=reasoning,
        document_format=document_format,  # type: ignore[arg-type]
        filename=filename,
        mime_type=mime_type,
        byte_size=byte_size,
        classify_job_id="job-abc",
    )


def _make_ctx(
    stage: ClassifyStage,
    envelope: EmailEnvelope | None,
    *,
    reply_handled: bool | None = None,
    skipped_docs: list[dict[str, object]] | None = None,
):
    """Build a real :class:`InvocationContext` with the right state preseeded."""
    state: dict[str, object] = {}
    if envelope is not None:
        state["envelope"] = envelope.model_dump(mode="json")
    if reply_handled is not None:
        state["reply_handled"] = reply_handled
    if skipped_docs is not None:
        state["skipped_docs"] = skipped_docs
    return make_stage_ctx(stage=stage, state=state)


# ---------------------------------------------------------------------- tests


def test_reply_handled_no_ops() -> None:
    """reply_handled=True → classify_fn never called; classified_docs empty;
    skipped_docs preserved from prior state."""
    classify_fn = MagicMock()
    stage = ClassifyStage(classify_fn=classify_fn, audit_logger=AsyncMock(spec=AuditLogger))
    env = _make_envelope(
        attachments=[_make_attachment("a.pdf"), _make_attachment("b.pdf")]
    )
    ctx = _make_ctx(stage, env, reply_handled=True)

    events = collect_events(stage.run_async(ctx))
    delta = final_state_delta(events)

    assert classify_fn.call_count == 0
    assert delta["classified_docs"] == []
    # No prior skipped_docs seeded → defaults to [] per state.get(..., []).
    assert delta["skipped_docs"] == []


def test_single_purchase_order_attachment() -> None:
    def fake(content: bytes, filename: str) -> ClassifiedDocument:
        return _classified(filename=filename, intent="purchase_order", confidence=0.95)

    stage = ClassifyStage(classify_fn=fake, audit_logger=AsyncMock(spec=AuditLogger))
    env = _make_envelope(attachments=[_make_attachment("po-001.pdf")])
    ctx = _make_ctx(stage, env)

    events = collect_events(stage.run_async(ctx))
    delta = final_state_delta(events)

    assert len(delta["classified_docs"]) == 1  # type: ignore[arg-type]
    only = delta["classified_docs"][0]  # type: ignore[index]
    assert only["filename"] == "po-001.pdf"
    assert only["document_intent"] == "purchase_order"
    assert delta["skipped_docs"] == []


def test_mixed_attachments_splits_po_from_others() -> None:
    intents_by_filename = {
        "po.pdf": ("purchase_order", 0.97),
        "invoice.pdf": ("invoice", 0.88),
        "spam.txt": ("spam", 0.71),
    }

    def fake(content: bytes, filename: str) -> ClassifiedDocument:
        intent, conf = intents_by_filename[filename]
        return _classified(filename=filename, intent=intent, confidence=conf)

    stage = ClassifyStage(classify_fn=fake, audit_logger=AsyncMock(spec=AuditLogger))
    env = _make_envelope(
        attachments=[
            _make_attachment("po.pdf"),
            _make_attachment("invoice.pdf"),
            _make_attachment("spam.txt", content_type="text/plain"),
        ]
    )
    ctx = _make_ctx(stage, env)

    events = collect_events(stage.run_async(ctx))
    delta = final_state_delta(events)

    classified = delta["classified_docs"]
    skipped = delta["skipped_docs"]
    assert isinstance(classified, list)
    assert isinstance(skipped, list)
    assert len(classified) == 1
    assert classified[0]["filename"] == "po.pdf"
    assert classified[0]["document_intent"] == "purchase_order"

    assert len(skipped) == 2
    assert [entry["filename"] for entry in skipped] == ["invoice.pdf", "spam.txt"]
    for entry in skipped:
        assert entry["stage"] == CLASSIFY_STAGE_NAME
        assert set(entry.keys()) == {"filename", "stage", "reason"}
    reasons = {entry["filename"]: entry["reason"] for entry in skipped}
    assert "intent=invoice" in reasons["invoice.pdf"]
    assert "confidence=0.88" in reasons["invoice.pdf"]
    assert "intent=spam" in reasons["spam.txt"]


def test_all_non_po_attachments_all_skipped() -> None:
    def fake(content: bytes, filename: str) -> ClassifiedDocument:
        return _classified(filename=filename, intent="invoice", confidence=0.80)

    stage = ClassifyStage(classify_fn=fake, audit_logger=AsyncMock(spec=AuditLogger))
    env = _make_envelope(
        attachments=[
            _make_attachment("a.pdf"),
            _make_attachment("b.pdf"),
            _make_attachment("c.pdf"),
        ]
    )
    ctx = _make_ctx(stage, env)

    events = collect_events(stage.run_async(ctx))
    delta = final_state_delta(events)

    assert delta["classified_docs"] == []
    assert len(delta["skipped_docs"]) == 3  # type: ignore[arg-type]


def test_missing_envelope_state_raises() -> None:
    classify_fn = MagicMock()
    stage = ClassifyStage(classify_fn=classify_fn, audit_logger=AsyncMock(spec=AuditLogger))
    ctx = _make_ctx(stage, envelope=None)

    with pytest.raises(ValueError, match="requires IngestStage"):
        collect_events(stage.run_async(ctx))

    assert classify_fn.call_count == 0


def test_empty_attachments_list_yields_empty_lists() -> None:
    classify_fn = MagicMock()
    stage = ClassifyStage(classify_fn=classify_fn, audit_logger=AsyncMock(spec=AuditLogger))
    env = _make_envelope(attachments=[])
    ctx = _make_ctx(stage, env)

    events = collect_events(stage.run_async(ctx))
    delta = final_state_delta(events)

    assert delta["classified_docs"] == []
    assert delta["skipped_docs"] == []
    assert classify_fn.call_count == 0


def test_classify_fn_raising_propagates() -> None:
    def fake(content: bytes, filename: str) -> ClassifiedDocument:
        raise RuntimeError("LlamaClassify timeout")

    stage = ClassifyStage(classify_fn=fake, audit_logger=AsyncMock(spec=AuditLogger))
    env = _make_envelope(attachments=[_make_attachment("po.pdf")])
    ctx = _make_ctx(stage, env)

    with pytest.raises(RuntimeError, match="LlamaClassify timeout"):
        collect_events(stage.run_async(ctx))


@pytest.mark.asyncio
async def test_stage_emits_entered_and_exited_audit_events() -> None:
    def fake(content: bytes, filename: str) -> object:
        from backend.models.classified_document import ClassifiedDocument
        return _classified(filename=filename, intent="purchase_order")

    audit_logger = AsyncMock(spec=AuditLogger)
    stage = ClassifyStage(classify_fn=fake, audit_logger=audit_logger)
    env = _make_envelope(attachments=[_make_attachment("po.pdf")])
    ctx = _make_ctx(stage, env, reply_handled=True)

    try:
        async for _ in stage.run_async(ctx):
            pass
    except Exception:
        pass

    calls = audit_logger.emit.await_args_list
    phases = [c.kwargs["phase"] for c in calls]
    assert "entered" in phases
    assert "exited" in phases
