"""Single entry point Track A calls per parsed email.

Routes a :class:`~backend.models.parsed_document.ParsedDocument` through the
validator and into the right store based on
:class:`~backend.models.validation_result.RoutingDecision`. Builds the
persisted record (with full customer + product snapshots) and dedupes on
``source_message_id`` so Pub/Sub redelivery and operator retries are safe.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from backend.ingestion.email_envelope import EmailEnvelope
from backend.models.exception_record import ExceptionRecord, ExceptionStatus
from backend.models.master_records import CustomerRecord
from backend.models.order_record import (
    CustomerSnapshot,
    OrderLine,
    OrderRecord,
    ProductSnapshot,
)
from backend.models.parsed_document import ExtractedOrder, ParsedDocument
from backend.models.validation_result import (
    LineItemValidation,
    RoutingDecision,
    ValidationResult,
)
from backend.persistence.base import ExceptionStore, OrderStore
from backend.tools.order_validator.tools.duplicate_check import compute_content_hash
from backend.tools.order_validator.tools.master_data_repo import MasterDataRepo


class ProcessResult(BaseModel):
    """Outcome of one ``coordinator.process()`` call.

    ``kind`` discriminates which optional payload is set:

    * ``"order"`` — fresh AUTO_APPROVE; ``order`` carries the persisted
      :class:`OrderRecord`.
    * ``"exception"`` — fresh CLARIFY or ESCALATE; ``exception`` carries
      the persisted :class:`ExceptionRecord`.
    * ``"duplicate"`` — ``source_message_id`` already persisted; whichever
      side it landed on is returned via ``order`` or ``exception``.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["order", "exception", "duplicate"]
    order: Optional[OrderRecord] = None
    exception: Optional[ExceptionRecord] = None


class IntakeCoordinator:
    def __init__(
        self,
        validator,  # OrderValidator (duck-typed for testability)
        order_store: OrderStore,
        exception_store: ExceptionStore,
        repo: MasterDataRepo,
        agent_version: str,
    ) -> None:
        self._validator = validator
        self._order_store = order_store
        self._exception_store = exception_store
        self._repo = repo
        self._agent_version = agent_version

    async def process(
        self,
        parsed_doc: ParsedDocument,
        envelope: EmailEnvelope,
        *,
        order_index: int = 0,
        clarify_body: Optional[str] = None,
        precomputed_validation: Optional[ValidationResult] = None,
    ) -> ProcessResult:
        """Route one sub-document through validation → store.

        When ``precomputed_validation`` is provided, the coordinator trusts
        it and skips re-invoking ``self._validator.validate``. This is how
        the orchestrator's ValidateStage hands its already-computed result
        off to PersistStage without paying the LLM+I/O cost twice. Callers
        that don't pre-validate (direct unit tests, future integrations)
        leave the kwarg unset and the coordinator runs validation itself.
        """
        doc_id = self._compose_doc_id(envelope.message_id, order_index)
        thread_id = envelope.thread_id or envelope.message_id

        # Preflight dedupe — parallel reads on both stores.
        existing_order, existing_exception = await asyncio.gather(
            self._order_store.get(doc_id),
            self._exception_store.get(doc_id),
        )
        if existing_order is not None:
            return ProcessResult(kind="duplicate", order=existing_order)
        if existing_exception is not None:
            return ProcessResult(kind="duplicate", exception=existing_exception)

        extracted_order = parsed_doc.sub_documents[order_index]
        if precomputed_validation is not None:
            validation = precomputed_validation
        else:
            validation = await self._validator.validate(
                extracted_order,
                source_message_id=envelope.message_id,
            )

        if validation.decision is RoutingDecision.AUTO_APPROVE:
            order = await self._build_order_record(
                extracted_order, validation, doc_id, thread_id
            )
            persisted = await self._order_store.save(order)
            return ProcessResult(kind="order", order=persisted)

        status = (
            ExceptionStatus.PENDING_CLARIFY
            if validation.decision is RoutingDecision.CLARIFY
            else ExceptionStatus.ESCALATED
        )
        # clarify_body only meaningful for PENDING_CLARIFY; ignored on ESCALATED.
        body = clarify_body if status is ExceptionStatus.PENDING_CLARIFY else None
        exception = self._build_exception_record(
            parsed_doc, validation, doc_id, thread_id, status, body
        )
        persisted = await self._exception_store.save(exception)
        return ProcessResult(kind="exception", exception=persisted)

    # -------------------------------------------------- helpers

    @staticmethod
    def _compose_doc_id(message_id: str, order_index: int) -> str:
        return message_id if order_index == 0 else f"{message_id}#{order_index}"

    async def _build_order_record(
        self,
        extracted: ExtractedOrder,
        validation: ValidationResult,
        doc_id: str,
        thread_id: str,
    ) -> OrderRecord:
        # AUTO_APPROVE means validation.customer is set and all lines passed.
        assert validation.customer is not None
        customer_id = validation.customer.customer_id
        lines = await self._build_order_lines(extracted, validation.lines)
        order_total = sum(line.line_total for line in lines)
        return OrderRecord(
            source_message_id=doc_id,
            thread_id=thread_id,
            customer=_customer_snapshot(validation.customer),
            customer_id=customer_id,
            po_number=extracted.po_number,
            content_hash=compute_content_hash(customer_id, extracted),
            lines=lines,
            order_total=order_total,
            confidence=validation.aggregate_confidence,
            processed_by_agent_version=self._agent_version,
            created_at=datetime.now(timezone.utc),  # store will overwrite with SERVER_TIMESTAMP
        )

    async def _build_order_lines(
        self,
        extracted: ExtractedOrder,
        validations: list[LineItemValidation],
    ) -> list[OrderLine]:
        lines: list[OrderLine] = []
        for v in validations:
            assert v.matched_sku is not None  # AUTO_APPROVE invariant
            product = await self._repo.get_product(v.matched_sku)
            assert product is not None  # validator already proved existence
            qty = int(extracted.line_items[v.line_index].quantity or 0)
            line_total = product.unit_price_usd * qty
            lines.append(
                OrderLine(
                    line_number=v.line_index,
                    product=ProductSnapshot(
                        sku=product.sku,
                        short_description=product.short_description,
                        uom=product.uom,
                        price_at_time=product.unit_price_usd,
                    ),
                    quantity=qty,
                    line_total=line_total,
                    confidence=v.match_confidence,
                )
            )
        return lines

    def _build_exception_record(
        self,
        parsed_doc: ParsedDocument,
        validation: ValidationResult,
        doc_id: str,
        thread_id: str,
        status: ExceptionStatus,
        clarify_body: Optional[str],
    ) -> ExceptionRecord:
        now = datetime.now(timezone.utc)
        return ExceptionRecord(
            source_message_id=doc_id,
            thread_id=thread_id,
            status=status,
            reason=_compose_reason(validation),
            clarify_body=clarify_body,
            parsed_doc=parsed_doc,
            validation_result=validation,
            created_at=now,
            updated_at=now,
        )


def _customer_snapshot(customer: CustomerRecord) -> CustomerSnapshot:
    primary_contact_email = next(
        (c.email for c in customer.contacts if c.email), None
    )
    return CustomerSnapshot(
        customer_id=customer.customer_id,
        name=customer.name,
        bill_to=customer.bill_to,
        payment_terms=customer.payment_terms,
        contact_email=primary_contact_email,
    )


def _compose_reason(validation: ValidationResult) -> str:
    """Concatenate per-line failure notes into one human-readable summary."""
    failing = [
        f"Line {ln.line_index}: " + "; ".join(ln.notes)
        for ln in validation.lines
        if ln.notes
    ]
    if not failing:
        # ESCALATE with no per-line notes (e.g., unresolved customer)
        return validation.rationale
    return " | ".join(failing)
