"""Unit tests for :class:`backend.my_agent.stages.parse.ParseStage`.

The stage iterates ``state['classified_docs']``, looks up each source's
bytes from ``envelope.attachments``, calls the injected sync ``parse_fn``
(wrapped in :func:`asyncio.to_thread`), and flattens every
``ParsedDocument.sub_documents`` into a single ``state['parsed_docs']``
list. ``skipped_docs`` is APPEND-not-overwrite: any parse-time skips are
added to the existing list set by earlier stages.

The ctx/collect/delta helpers now live in
:mod:`tests.unit._stage_testing`; ``_make_ctx`` here is a thin wrapper
that forwards the stage-specific state keys.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from backend.ingestion.email_envelope import EmailAttachment, EmailEnvelope
from backend.models.classified_document import ClassifiedDocument
from backend.models.parsed_document import (
    ExtractedOrder,
    OrderLineItem,
    ParsedDocument,
)
from backend.my_agent.stages.parse import PARSE_STAGE_NAME, ParseStage
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


def _classified_dict(
    *,
    filename: str,
    intent: str = "purchase_order",
    confidence: float = 0.93,
    reasoning: str = "Header reads 'Purchase Order'; line-item table present.",
    document_format: str = "pdf",
    mime_type: str = "application/pdf",
    byte_size: int = 2048,
) -> dict[str, object]:
    return ClassifiedDocument(
        document_intent=intent,  # type: ignore[arg-type]
        intent_confidence=confidence,
        intent_reasoning=reasoning,
        document_format=document_format,  # type: ignore[arg-type]
        filename=filename,
        mime_type=mime_type,
        byte_size=byte_size,
        classify_job_id="job-abc",
    ).model_dump(mode="json")


def _extracted_order(
    *,
    po_number: str = "PO-12345",
    customer_name: str = "Birch Valley Foods",
    line_items: list[OrderLineItem] | None = None,
) -> ExtractedOrder:
    return ExtractedOrder(
        customer_name=customer_name,
        po_number=po_number,
        line_items=line_items
        if line_items is not None
        else [
            OrderLineItem(
                sku="SKU-001",
                description="Widget",
                quantity=10.0,
                unit_of_measure="EA",
                unit_price=9.5,
            )
        ],
        ship_to_address="123 Main St, Springfield, IL 62701",
        requested_delivery_date="2026-05-01",
        special_instructions=None,
    )


def _parsed_document(
    *,
    sub_documents: list[ExtractedOrder] | None = None,
    classification: str = "purchase_order",
    rationale: str = "Document header reads 'Purchase Order'.",
    page_count: int | None = 1,
    detected_language: str | None = "en",
) -> ParsedDocument:
    return ParsedDocument(
        classification=classification,  # type: ignore[arg-type]
        classification_rationale=rationale,
        sub_documents=sub_documents if sub_documents is not None else [_extracted_order()],
        page_count=page_count,
        detected_language=detected_language,
    )


def _make_ctx(
    stage: ParseStage,
    envelope: EmailEnvelope | None,
    *,
    reply_handled: bool | None = None,
    classified_docs: list[dict[str, object]] | None = None,
    skipped_docs: list[dict[str, object]] | None = None,
):
    """Build a real :class:`InvocationContext` with the right state preseeded."""
    state: dict[str, object] = {}
    if envelope is not None:
        state["envelope"] = envelope.model_dump(mode="json")
    if reply_handled is not None:
        state["reply_handled"] = reply_handled
    if classified_docs is not None:
        state["classified_docs"] = classified_docs
    if skipped_docs is not None:
        state["skipped_docs"] = skipped_docs
    return make_stage_ctx(stage=stage, state=state)


# ---------------------------------------------------------------------- tests


def test_reply_handled_no_ops() -> None:
    """reply_handled=True → parse_fn never called; parsed_docs empty;
    skipped_docs preserved from prior state."""
    parse_fn = MagicMock()
    stage = ParseStage(parse_fn=parse_fn)
    env = _make_envelope(attachments=[_make_attachment("po.pdf")])
    prior_skipped = [
        {
            "filename": "invoice.pdf",
            "stage": "classify_stage",
            "reason": "intent=invoice (confidence=0.88)",
        }
    ]
    ctx = _make_ctx(
        stage,
        env,
        reply_handled=True,
        classified_docs=[_classified_dict(filename="po.pdf")],
        skipped_docs=prior_skipped,
    )

    events = collect_events(stage.run_async(ctx))
    delta = final_state_delta(events)

    assert parse_fn.call_count == 0
    assert delta["parsed_docs"] == []
    # Prior skipped_docs survive the short-circuit.
    assert delta["skipped_docs"] == prior_skipped


def test_missing_envelope_state_raises() -> None:
    parse_fn = MagicMock()
    stage = ParseStage(parse_fn=parse_fn)
    ctx = _make_ctx(
        stage,
        envelope=None,
        classified_docs=[_classified_dict(filename="po.pdf")],
    )

    with pytest.raises(ValueError, match="requires IngestStage"):
        collect_events(stage.run_async(ctx))

    assert parse_fn.call_count == 0


def test_missing_classified_docs_state_raises() -> None:
    parse_fn = MagicMock()
    stage = ParseStage(parse_fn=parse_fn)
    env = _make_envelope(attachments=[_make_attachment("po.pdf")])
    # NOTE: classified_docs intentionally omitted (None).
    ctx = _make_ctx(stage, env)

    with pytest.raises(ValueError, match="requires ClassifyStage"):
        collect_events(stage.run_async(ctx))

    assert parse_fn.call_count == 0


def test_empty_classified_docs_yields_empty_parsed_docs() -> None:
    """All attachments were non-PO → classified_docs is []. parse_fn never
    called, parsed_docs is [], existing skipped_docs preserved."""
    parse_fn = MagicMock()
    stage = ParseStage(parse_fn=parse_fn)
    env = _make_envelope(attachments=[_make_attachment("spam.txt")])
    prior_skipped = [
        {
            "filename": "spam.txt",
            "stage": "classify_stage",
            "reason": "intent=spam (confidence=0.71)",
        }
    ]
    ctx = _make_ctx(
        stage,
        env,
        classified_docs=[],
        skipped_docs=prior_skipped,
    )

    events = collect_events(stage.run_async(ctx))
    delta = final_state_delta(events)

    assert parse_fn.call_count == 0
    assert delta["parsed_docs"] == []
    assert delta["skipped_docs"] == prior_skipped


def test_single_classified_doc_single_subdoc_flattens_to_one_entry() -> None:
    content = b"%PDF-1.4 single PO body"

    def fake(got_content: bytes, got_filename: str) -> ParsedDocument:
        assert got_content == content
        assert got_filename == "po-001.pdf"
        return _parsed_document(sub_documents=[_extracted_order(po_number="PO-001")])

    stage = ParseStage(parse_fn=fake)
    env = _make_envelope(
        attachments=[_make_attachment("po-001.pdf", content=content)]
    )
    ctx = _make_ctx(
        stage,
        env,
        classified_docs=[_classified_dict(filename="po-001.pdf")],
    )

    events = collect_events(stage.run_async(ctx))
    delta = final_state_delta(events)

    parsed_docs = delta["parsed_docs"]
    assert isinstance(parsed_docs, list)
    assert len(parsed_docs) == 1
    only = parsed_docs[0]
    assert only["filename"] == "po-001.pdf"
    assert only["sub_doc_index"] == 0
    assert only["parsed"]["classification"] == "purchase_order"
    assert only["parsed"]["detected_language"] == "en"
    assert len(only["parsed"]["sub_documents"]) == 1
    assert only["sub_doc"]["po_number"] == "PO-001"
    assert only["sub_doc"]["customer_name"] == "Birch Valley Foods"
    assert delta["skipped_docs"] == []


def test_single_classified_doc_multiple_subdocs_flattens_per_sub_doc() -> None:
    """A multi-order PDF (3 sub-docs) produces 3 parsed_docs entries, all
    with the same filename and increasing sub_doc_index."""
    sub_docs = [
        _extracted_order(po_number=f"PO-00{i + 1}") for i in range(3)
    ]

    def fake(content: bytes, filename: str) -> ParsedDocument:
        return _parsed_document(sub_documents=sub_docs)

    stage = ParseStage(parse_fn=fake)
    env = _make_envelope(attachments=[_make_attachment("bundle.pdf")])
    ctx = _make_ctx(
        stage,
        env,
        classified_docs=[_classified_dict(filename="bundle.pdf")],
    )

    events = collect_events(stage.run_async(ctx))
    delta = final_state_delta(events)

    parsed_docs = delta["parsed_docs"]
    assert isinstance(parsed_docs, list)
    assert len(parsed_docs) == 3
    assert [e["filename"] for e in parsed_docs] == ["bundle.pdf"] * 3
    assert [e["sub_doc_index"] for e in parsed_docs] == [0, 1, 2]
    assert [e["sub_doc"]["po_number"] for e in parsed_docs] == [
        "PO-001",
        "PO-002",
        "PO-003",
    ]
    # All three entries share the same full ParsedDocument snapshot.
    for entry in parsed_docs:
        assert len(entry["parsed"]["sub_documents"]) == 3
    assert delta["skipped_docs"] == []


def test_multiple_classified_docs_flattens_all() -> None:
    """Two PO attachments — one yields 1 sub_doc, the other yields 2;
    parsed_docs has 3 entries total; sub_doc_index resets per source."""
    returns_by_filename = {
        "po-a.pdf": _parsed_document(
            sub_documents=[_extracted_order(po_number="PO-A1")]
        ),
        "po-b.pdf": _parsed_document(
            sub_documents=[
                _extracted_order(po_number="PO-B1"),
                _extracted_order(po_number="PO-B2"),
            ]
        ),
    }

    def fake(content: bytes, filename: str) -> ParsedDocument:
        return returns_by_filename[filename]

    stage = ParseStage(parse_fn=fake)
    env = _make_envelope(
        attachments=[
            _make_attachment("po-a.pdf", content=b"A-bytes"),
            _make_attachment("po-b.pdf", content=b"B-bytes"),
        ]
    )
    ctx = _make_ctx(
        stage,
        env,
        classified_docs=[
            _classified_dict(filename="po-a.pdf"),
            _classified_dict(filename="po-b.pdf"),
        ],
    )

    events = collect_events(stage.run_async(ctx))
    delta = final_state_delta(events)

    parsed_docs = delta["parsed_docs"]
    assert isinstance(parsed_docs, list)
    assert len(parsed_docs) == 3
    assert [e["filename"] for e in parsed_docs] == [
        "po-a.pdf",
        "po-b.pdf",
        "po-b.pdf",
    ]
    # Indexes reset per source doc.
    assert [e["sub_doc_index"] for e in parsed_docs] == [0, 0, 1]
    assert [e["sub_doc"]["po_number"] for e in parsed_docs] == [
        "PO-A1",
        "PO-B1",
        "PO-B2",
    ]
    assert delta["skipped_docs"] == []


def test_parser_returns_zero_subdocs_appended_to_skipped_docs() -> None:
    """parse_fn returns ParsedDocument with empty sub_documents →
    parsed_docs gets 0 entries for this source AND skipped_docs gains an
    entry. The pre-existing ClassifyStage skipped entry is preserved
    (APPEND-not-overwrite)."""
    def fake(content: bytes, filename: str) -> ParsedDocument:
        return _parsed_document(
            sub_documents=[],
            classification="other",
            rationale="No line-item table detected.",
        )

    stage = ParseStage(parse_fn=fake)
    env = _make_envelope(attachments=[_make_attachment("ambiguous.pdf")])
    prior_skipped = [
        {
            "filename": "invoice.pdf",
            "stage": "classify_stage",
            "reason": "intent=invoice (confidence=0.88)",
        }
    ]
    ctx = _make_ctx(
        stage,
        env,
        classified_docs=[_classified_dict(filename="ambiguous.pdf")],
        skipped_docs=prior_skipped,
    )

    events = collect_events(stage.run_async(ctx))
    delta = final_state_delta(events)

    assert delta["parsed_docs"] == []

    skipped = delta["skipped_docs"]
    assert isinstance(skipped, list)
    assert len(skipped) == 2
    # Pre-existing entry is preserved unchanged.
    assert skipped[0] == prior_skipped[0]
    # New parse-stage entry is appended, not prepended.
    assert skipped[1] == {
        "filename": "ambiguous.pdf",
        "stage": PARSE_STAGE_NAME,
        "reason": "parser returned zero sub_documents",
    }


def test_parse_fn_raising_propagates() -> None:
    def fake(content: bytes, filename: str) -> ParsedDocument:
        raise RuntimeError("LlamaExtract timeout")

    stage = ParseStage(parse_fn=fake)
    env = _make_envelope(attachments=[_make_attachment("po.pdf")])
    ctx = _make_ctx(
        stage,
        env,
        classified_docs=[_classified_dict(filename="po.pdf")],
    )

    with pytest.raises(RuntimeError, match="LlamaExtract timeout"):
        collect_events(stage.run_async(ctx))
