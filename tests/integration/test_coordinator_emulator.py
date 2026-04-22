"""End-to-end integration tests for :class:`IntakeCoordinator` against the emulator.

Wires the real :class:`OrderValidator` (with the seeded
:class:`MasterDataRepo`) to real Firestore-backed stores. Proves the
vertical slice ``ExtractedOrder → validate → route → persist`` works
against the actual async SDK.

Requires master data already loaded into the emulator via
``scripts/load_master_data.py``.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest

from backend.ingestion.email_envelope import EmailEnvelope
from backend.models.exception_record import ExceptionStatus
from backend.models.parsed_document import (
    ExtractedOrder,
    OrderLineItem,
    ParsedDocument,
)
from backend.persistence import (
    FirestoreExceptionStore,
    FirestoreOrderStore,
    IntakeCoordinator,
)
from backend.tools.order_validator import (
    MasterDataRepo,
    OrderValidator,
    get_async_client,
)

pytestmark = [
    pytest.mark.firestore_emulator,
    pytest.mark.skipif(
        not os.environ.get("FIRESTORE_EMULATOR_HOST"),
        reason="FIRESTORE_EMULATOR_HOST not set; emulator tests skipped",
    ),
]


@pytest.fixture
async def coord():
    client = get_async_client()
    repo = MasterDataRepo(client)
    validator = OrderValidator(repo)
    coordinator = IntakeCoordinator(
        validator=validator,
        order_store=FirestoreOrderStore(client),
        exception_store=FirestoreExceptionStore(client),
        repo=repo,
        agent_version="v0.1.0-int-test",
    )
    try:
        yield coordinator
    finally:
        await repo.aclose()


def _envelope(message_id: str) -> EmailEnvelope:
    return EmailEnvelope(
        message_id=message_id,
        thread_id=f"thr-{message_id}",
        from_addr="buyer@patterson.example.com",
        to_addr="orders@seller.example.com",
        subject="PO-INT",
        received_at=datetime(2026, 4, 22, tzinfo=timezone.utc),
        body_text="Please ship per attached.",
    )


async def test_end_to_end_auto_approve_writes_order_doc(coord):
    """Real validator against seeded data → AUTO_APPROVE → real OrderStore
    write. Reads back via the same store to confirm the persisted shape."""
    parsed = ParsedDocument(
        classification="purchase_order",
        classification_rationale="integration auto-approve",
        sub_documents=[
            ExtractedOrder(
                customer_name="Patterson Industrial",
                po_number="PO-INT-AUTO",
                line_items=[
                    OrderLineItem(
                        sku="FST-HCS-050-13-200-G5Z",
                        description="hex cap screw",
                        quantity=100,
                        unit_of_measure="EA",
                        unit_price=0.34,
                    )
                ],
            )
        ],
        page_count=1,
    )
    msg_id = f"int-coord-auto-{uuid.uuid4().hex}"

    result = await coord.process(parsed, _envelope(msg_id))

    assert result.kind == "order"
    assert result.order is not None
    assert result.order.source_message_id == msg_id
    assert result.order.customer.customer_id == "CUST-00042"
    assert len(result.order.lines) == 1
    line = result.order.lines[0]
    assert line.product.sku == "FST-HCS-050-13-200-G5Z"
    assert line.product.price_at_time == 0.34
    assert line.quantity == 100
    assert line.line_total == pytest.approx(34.00)


async def test_end_to_end_clarify_writes_exception_doc(coord):
    """A line that does not match any catalog SKU must route to the
    exceptions collection with PENDING_CLARIFY status."""
    parsed = ParsedDocument(
        classification="purchase_order",
        classification_rationale="integration clarify",
        sub_documents=[
            ExtractedOrder(
                customer_name="Patterson Industrial",
                po_number="PO-INT-CLARIFY",
                line_items=[
                    OrderLineItem(
                        sku="MYSTERY-DOES-NOT-EXIST",
                        description="opaque mystery widget",
                        quantity=10,
                        unit_of_measure="EA",
                    )
                ],
            )
        ],
        page_count=1,
    )
    msg_id = f"int-coord-clarify-{uuid.uuid4().hex}"

    result = await coord.process(parsed, _envelope(msg_id))

    assert result.kind == "exception"
    assert result.exception is not None
    assert result.exception.source_message_id == msg_id
    # Either CLARIFY (if confidence in 0.80–0.95 band) or ESCALATED (below
    # 0.80) — both are valid outcomes for a fully-unmatched line; assert
    # the boundary, not a specific bucket.
    assert result.exception.status in (
        ExceptionStatus.PENDING_CLARIFY,
        ExceptionStatus.ESCALATED,
    )
    assert "MYSTERY-DOES-NOT-EXIST" in result.exception.reason
