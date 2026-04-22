"""Unit tests for :class:`backend.persistence.coordinator.IntakeCoordinator`.

Coordinator is the single entry point Track A calls per parsed email. Tests
mock the validator + master-data repo, wiring real
:class:`FirestoreOrderStore` / :class:`FirestoreExceptionStore` instances over
the in-memory :class:`FakeAsyncClient` so write semantics are exercised
end-to-end at the unit level.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

from backend.ingestion.email_envelope import EmailEnvelope
from backend.models.exception_record import ExceptionStatus
from backend.models.master_records import (
    AddressRecord,
    ContactRecord,
    CustomerRecord,
    ProductRecord,
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


# ----------------------------------------------------- helpers


def _sample_customer() -> CustomerRecord:
    return CustomerRecord(
        customer_id="CUST-00042",
        name="Acme Industrial",
        segment="manufacturing",
        bill_to=AddressRecord(
            street1="100 Main St",
            city="Cleveland",
            state="OH",
            zip="44113",
            country="USA",
        ),
        payment_terms="Net 30",
        credit_limit_usd=50000.0,
        currency="USD",
        contacts=[
            ContactRecord(name="Pat Buyer", role="Procurement", email="pat@acme.example.com")
        ],
    )


def _sample_product(sku: str = "WGT-001", price: float = 4.99) -> ProductRecord:
    return ProductRecord(
        sku=sku,
        short_description=f"Widget {sku}",
        long_description=f"Long description of widget {sku}",
        category="widgets",
        uom="EA",
        unit_price_usd=price,
    )


def _envelope(message_id: str = "msg-001", thread_id: str | None = "thread-001") -> EmailEnvelope:
    return EmailEnvelope(
        message_id=message_id,
        thread_id=thread_id,
        from_addr="buyer@acme.example.com",
        to_addr="orders@seller.example.com",
        subject="PO 12345",
        received_at=datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc),
        body_text="Please ship 100 widgets.",
    )


def _parsed_doc(sku: str = "WGT-001", quantity: float = 100, unit_price: float = 4.99) -> ParsedDocument:
    return ParsedDocument(
        classification="purchase_order",
        classification_rationale="Subject reads 'PO' and body has qty/sku.",
        sub_documents=[
            ExtractedOrder(
                customer_name="Acme Industrial",
                po_number="PO-12345",
                line_items=[
                    OrderLineItem(
                        sku=sku,
                        description=f"Widget {sku}",
                        quantity=quantity,
                        unit_of_measure="EA",
                        unit_price=unit_price,
                    )
                ],
            )
        ],
        page_count=1,
        detected_language="en",
    )


def _validation(
    decision: RoutingDecision,
    *,
    customer: CustomerRecord | None = None,
    matched_sku: str | None = "WGT-001",
    confidence: float = 1.0,
    notes: list[str] | None = None,
    price_ok: bool = True,
    qty_ok: bool = True,
) -> ValidationResult:
    return ValidationResult(
        customer=customer,
        lines=[
            LineItemValidation(
                line_index=0,
                matched_sku=matched_sku,
                match_tier="exact" if matched_sku else "none",
                match_confidence=1.0 if matched_sku else 0.0,
                price_ok=price_ok,
                qty_ok=qty_ok,
                notes=notes or [],
            )
        ],
        aggregate_confidence=confidence,
        decision=decision,
        rationale=f"1 line, confidence {confidence:.2f} -> {decision.value}",
    )


def _make_coord(fake_client, validation: ValidationResult, *, agent_version: str = "v0.1.0"):
    """Build a coordinator with a mocked validator + repo and real Firestore-style stores."""
    from backend.persistence.coordinator import IntakeCoordinator
    from backend.persistence.exceptions_store import FirestoreExceptionStore
    from backend.persistence.orders_store import FirestoreOrderStore

    validator = AsyncMock()
    validator.validate.return_value = validation

    repo = AsyncMock()
    repo.get_product.return_value = _sample_product()

    return IntakeCoordinator(
        validator=validator,
        order_store=FirestoreOrderStore(fake_client),
        exception_store=FirestoreExceptionStore(fake_client),
        repo=repo,
        agent_version=agent_version,
    ), validator, repo


# ----------------------------------------------------- tests


async def test_process_auto_approve_writes_to_order_store(fake_client):
    """AUTO_APPROVE → OrderStore write, ProcessResult.kind == 'order'."""
    coord, _validator, _repo = _make_coord(
        fake_client,
        _validation(RoutingDecision.AUTO_APPROVE, customer=_sample_customer()),
    )

    result = await coord.process(_parsed_doc(), _envelope())

    assert result.kind == "order"
    assert result.order is not None
    assert result.exception is None
    assert result.order.source_message_id == "msg-001"
    assert result.order.customer.name == "Acme Industrial"


async def test_process_clarify_writes_pending_clarify_exception(fake_client):
    """CLARIFY decision → ExceptionStore write with status=PENDING_CLARIFY."""
    coord, _validator, _repo = _make_coord(
        fake_client,
        _validation(
            RoutingDecision.CLARIFY,
            customer=_sample_customer(),
            matched_sku=None,
            confidence=0.85,
            notes=["no match for 'WGT-001'"],
        ),
    )

    result = await coord.process(_parsed_doc(), _envelope())

    assert result.kind == "exception"
    assert result.exception is not None
    assert result.exception.status is ExceptionStatus.PENDING_CLARIFY


async def test_process_escalate_writes_escalated_exception(fake_client):
    """ESCALATE → ExceptionStore write with status=ESCALATED, even when
    customer is unresolved."""
    coord, _validator, _repo = _make_coord(
        fake_client,
        _validation(
            RoutingDecision.ESCALATE,
            customer=None,
            matched_sku=None,
            confidence=0.4,
            notes=["customer unresolved"],
        ),
    )

    result = await coord.process(_parsed_doc(), _envelope())

    assert result.kind == "exception"
    assert result.exception is not None
    assert result.exception.status is ExceptionStatus.ESCALATED


async def test_process_duplicate_envelope_returns_existing_record(fake_client):
    """Second process() with same source_message_id returns kind='duplicate'
    without re-running the validator."""
    coord, validator, _repo = _make_coord(
        fake_client,
        _validation(RoutingDecision.AUTO_APPROVE, customer=_sample_customer()),
    )

    first = await coord.process(_parsed_doc(), _envelope())
    second = await coord.process(_parsed_doc(), _envelope())

    assert first.kind == "order"
    assert second.kind == "duplicate"
    assert second.order is not None
    assert second.order.source_message_id == first.order.source_message_id
    # Validator called exactly once — second process took the dedupe shortcut.
    assert validator.validate.call_count == 1


async def test_order_record_carries_full_customer_snapshot(fake_client):
    """OrderRecord.customer must be the snapshot built from validation.customer
    (already-resolved CustomerRecord), not a re-query."""
    coord, _validator, repo = _make_coord(
        fake_client,
        _validation(RoutingDecision.AUTO_APPROVE, customer=_sample_customer()),
    )

    result = await coord.process(_parsed_doc(), _envelope())

    cust = result.order.customer
    assert cust.customer_id == "CUST-00042"
    assert cust.name == "Acme Industrial"
    assert cust.bill_to.city == "Cleveland"
    assert cust.payment_terms == "Net 30"
    assert cust.contact_email == "pat@acme.example.com"
    # Coordinator must NOT call repo.get_customer — the customer snapshot
    # comes straight from the validator's already-resolved CustomerRecord.
    repo.get_customer.assert_not_called()


async def test_order_line_carries_full_product_snapshot(fake_client):
    """OrderLine.product must be a ProductSnapshot built from a fresh
    repo.get_product() call keyed on validation's matched_sku."""
    coord, _validator, repo = _make_coord(
        fake_client,
        _validation(
            RoutingDecision.AUTO_APPROVE,
            customer=_sample_customer(),
            matched_sku="WGT-001",
        ),
    )

    result = await coord.process(_parsed_doc(quantity=100, unit_price=4.99), _envelope())

    line = result.order.lines[0]
    assert line.line_number == 0
    assert line.product.sku == "WGT-001"
    assert line.product.short_description == "Widget WGT-001"
    assert line.product.uom == "EA"
    assert line.product.price_at_time == 4.99
    assert line.quantity == 100
    assert line.line_total == 4.99 * 100
    repo.get_product.assert_awaited_once_with("WGT-001")


async def test_exception_record_carries_full_parsed_doc_and_validation(fake_client):
    """ExceptionRecord must embed the full ParsedDocument + ValidationResult
    so the dashboard renders 'what the agent saw' without re-running anything."""
    validation = _validation(
        RoutingDecision.CLARIFY,
        customer=_sample_customer(),
        matched_sku=None,
        confidence=0.85,
        notes=["no match for 'WGT-001'"],
    )
    coord, _validator, _repo = _make_coord(fake_client, validation)
    parsed = _parsed_doc()

    result = await coord.process(parsed, _envelope())

    exc = result.exception
    assert exc.parsed_doc.classification == "purchase_order"
    assert exc.parsed_doc.sub_documents[0].po_number == "PO-12345"
    assert exc.validation_result.decision is RoutingDecision.CLARIFY
    assert exc.validation_result.aggregate_confidence == 0.85


async def test_processed_by_agent_version_set_from_constructor_arg(fake_client):
    coord, _validator, _repo = _make_coord(
        fake_client,
        _validation(RoutingDecision.AUTO_APPROVE, customer=_sample_customer()),
        agent_version="v1.2.3-test",
    )

    result = await coord.process(_parsed_doc(), _envelope())

    assert result.order.processed_by_agent_version == "v1.2.3-test"


async def test_clarify_reason_concatenates_per_line_notes(fake_client):
    """ExceptionRecord.reason is auto-built from LineItemValidation.notes
    so the dashboard shows a deterministic summary, no LLM involved."""
    coord, _validator, _repo = _make_coord(
        fake_client,
        _validation(
            RoutingDecision.CLARIFY,
            customer=_sample_customer(),
            matched_sku=None,
            confidence=0.85,
            notes=["no match for 'WGT-001'", "qty 9999 exceeds min_order"],
        ),
    )

    result = await coord.process(_parsed_doc(), _envelope())

    assert "Line 0:" in result.exception.reason
    assert "no match" in result.exception.reason
    assert "9999" in result.exception.reason


async def test_source_message_id_and_thread_id_propagate_from_envelope(fake_client):
    coord, _validator, _repo = _make_coord(
        fake_client,
        _validation(RoutingDecision.AUTO_APPROVE, customer=_sample_customer()),
    )

    result = await coord.process(
        _parsed_doc(),
        _envelope(message_id="msg-abc-123", thread_id="thread-xyz-789"),
    )

    assert result.order.source_message_id == "msg-abc-123"
    assert result.order.thread_id == "thread-xyz-789"


async def test_thread_id_falls_back_to_message_id_when_envelope_lacks_thread(fake_client):
    """Local fixtures may have no thread_id; coordinator must still produce
    a non-null thread_id by falling back to message_id."""
    coord, _validator, _repo = _make_coord(
        fake_client,
        _validation(RoutingDecision.AUTO_APPROVE, customer=_sample_customer()),
    )

    result = await coord.process(
        _parsed_doc(), _envelope(message_id="msg-no-thread", thread_id=None)
    )

    assert result.order.thread_id == "msg-no-thread"


async def test_clarify_body_kwarg_written_to_exception(fake_client):
    """On CLARIFY, the clarify_body kwarg must be persisted on the exception
    so the dashboard can render the generated email alongside the reason."""
    coord, _validator, _repo = _make_coord(
        fake_client,
        _validation(
            RoutingDecision.CLARIFY,
            customer=_sample_customer(),
            matched_sku=None,
            confidence=0.85,
            notes=["no match for 'WGT-001'"],
        ),
    )

    body = "Hi Pat,\n\nCould you confirm the SKU for line 1?\n\nThanks."
    result = await coord.process(_parsed_doc(), _envelope(), clarify_body=body)

    assert result.exception is not None
    assert result.exception.status is ExceptionStatus.PENDING_CLARIFY
    assert result.exception.clarify_body == body


async def test_clarify_body_dropped_on_escalate(fake_client):
    """clarify_body is only meaningful for CLARIFY (pending_clarify) cases.
    ESCALATE bypasses clarify and goes straight to human review — if a caller
    passes a body by mistake, coordinator must not persist it."""
    coord, _validator, _repo = _make_coord(
        fake_client,
        _validation(
            RoutingDecision.ESCALATE,
            customer=None,
            matched_sku=None,
            confidence=0.4,
            notes=["customer unresolved"],
        ),
    )

    result = await coord.process(
        _parsed_doc(), _envelope(), clarify_body="should not be persisted"
    )

    assert result.exception is not None
    assert result.exception.status is ExceptionStatus.ESCALATED
    assert result.exception.clarify_body is None


async def test_clarify_body_defaults_to_none_when_not_passed(fake_client):
    """Caller can skip clarify_body entirely — coordinator leaves it None."""
    coord, _validator, _repo = _make_coord(
        fake_client,
        _validation(
            RoutingDecision.CLARIFY,
            customer=_sample_customer(),
            matched_sku=None,
            confidence=0.85,
            notes=["no match"],
        ),
    )

    result = await coord.process(_parsed_doc(), _envelope())

    assert result.exception is not None
    assert result.exception.clarify_body is None
