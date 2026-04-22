"""Pydantic record for the persisted ``orders`` Firestore collection.

Produced by :class:`backend.persistence.coordinator.IntakeCoordinator` when a
:class:`~backend.models.validation_result.ValidationResult` carries
``RoutingDecision.AUTO_APPROVE``. The Firestore doc id is ``source_message_id``
so Pub/Sub redeliveries collapse to the same document via optimistic
``create(exists=False)`` — no dedup index needed.

Customer and product data are **snapshotted** at write time rather than
referenced by id, so if a product's ``unit_price_usd`` changes after the
order lands, the order document still reflects the price that was agreed.
Price fields use ``float`` to match the existing
:class:`~backend.models.master_records.ProductRecord.unit_price_usd` convention.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from backend.models.master_records import AddressRecord


class OrderStatus(StrEnum):
    """Lifecycle state of a persisted order.

    Single state this sprint — the demo does not exercise
    ``CONFIRMED`` / ``FULFILLED`` transitions. Defined as an enum so future
    lifecycle work does not need a schema migration.
    """

    PERSISTED = "persisted"


class CustomerSnapshot(BaseModel):
    """Frozen copy of the customer as it existed when the order was written."""

    model_config = ConfigDict(extra="forbid")

    customer_id: str
    name: str
    bill_to: AddressRecord
    payment_terms: str
    contact_email: Optional[str] = None


class ProductSnapshot(BaseModel):
    """Frozen copy of the product for one line. ``price_at_time`` is the
    catalog ``unit_price_usd`` at write time — the order-level audit anchor."""

    model_config = ConfigDict(extra="forbid")

    sku: str
    short_description: str
    uom: str
    price_at_time: float


class OrderLine(BaseModel):
    """One line item on a persisted order. ``line_total`` is
    ``price_at_time * quantity`` precomputed so the dashboard does not
    recompute on every render."""

    model_config = ConfigDict(extra="forbid")

    line_number: int = Field(..., ge=0)
    product: ProductSnapshot
    quantity: int = Field(..., gt=0)
    line_total: float
    confidence: float = Field(..., ge=0.0, le=1.0)


class OrderRecord(BaseModel):
    """One persisted order. Firestore doc path: ``orders/{source_message_id}``.

    ``source_message_id`` is the envelope's ``message_id`` — using it as the
    doc id gives us idempotency for free against Pub/Sub redelivery.
    ``thread_id`` propagates from the envelope so clarify-reply threads
    correlate correctly if the order is later revised.
    """

    model_config = ConfigDict(extra="forbid")

    source_message_id: str
    thread_id: str
    customer: CustomerSnapshot
    lines: list[OrderLine]
    order_total: float
    confidence: float = Field(..., ge=0.0, le=1.0)
    status: OrderStatus = OrderStatus.PERSISTED
    processed_by_agent_version: str
    schema_version: int = 1
    created_at: datetime


__all__ = [
    "OrderStatus",
    "CustomerSnapshot",
    "ProductSnapshot",
    "OrderLine",
    "OrderRecord",
]
