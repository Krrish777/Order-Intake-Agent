"""Pydantic record for the persisted ``exceptions`` Firestore collection.

Produced by :class:`backend.persistence.coordinator.IntakeCoordinator` when
validation returns ``RoutingDecision.CLARIFY`` or ``RoutingDecision.ESCALATE``.
One mutable document accumulates state across the exception's lifetime —
``clarify_message_id`` is filled when the clarify email goes out (Track A),
``reply_message_id`` when the reply arrives, ``resolved_to_order_id`` when
a human approves the exception and it promotes to an order.

The full :class:`~backend.models.parsed_document.ParsedDocument` and
:class:`~backend.models.validation_result.ValidationResult` are embedded
as snapshots so the dashboard can render "what the agent saw" without
re-running any prior stage.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Optional

from pydantic import BaseModel, ConfigDict

from backend.models.parsed_document import ParsedDocument
from backend.models.validation_result import ValidationResult


class ExceptionStatus(StrEnum):
    """Lifecycle state of one exception.

    * ``PENDING_CLARIFY`` — validator said CLARIFY; clarify email is sent
      (or queued); we are waiting on the customer's reply.
    * ``AWAITING_REVIEW`` — clarify reply parsed; waiting on a human
      operator to approve / reject / edit.
    * ``ESCALATED`` — validator said ESCALATE (confidence below clarify
      threshold, or unresolved customer); skip clarify, go straight to
      human review.
    * ``RESOLVED`` — operator decided. If the decision was "approve",
      ``resolved_to_order_id`` points at the order doc that was created.

    Values double as the Firestore status string. Stable — changing a
    value is a schema migration.
    """

    PENDING_CLARIFY = "pending_clarify"
    AWAITING_REVIEW = "awaiting_review"
    ESCALATED = "escalated"
    RESOLVED = "resolved"


class ExceptionRecord(BaseModel):
    """One persisted exception. Firestore doc path: ``exceptions/{source_message_id}``.

    ``source_message_id`` doubles as the doc id for the same idempotency
    guarantee :class:`backend.models.order_record.OrderRecord` enjoys.

    ``thread_id`` is the correlation key for :meth:`ExceptionStore.find_pending_clarify`
    — when a customer replies to a clarify email, Track A looks up the
    pending exception in the same thread and calls
    :meth:`ExceptionStore.update_with_reply`.
    """

    model_config = ConfigDict(extra="forbid")

    source_message_id: str
    thread_id: str
    clarify_message_id: Optional[str] = None
    reply_message_id: Optional[str] = None
    status: ExceptionStatus
    reason: str
    parsed_doc: ParsedDocument
    validation_result: ValidationResult
    resolved_to_order_id: Optional[str] = None
    schema_version: int = 1
    created_at: datetime
    updated_at: datetime


__all__ = [
    "ExceptionStatus",
    "ExceptionRecord",
]
