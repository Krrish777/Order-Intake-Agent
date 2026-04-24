"""Protocols defining the persistence layer's public surface.

Two stores — one per collection — plus the async contract each must honor.
Implementations live under :mod:`backend.persistence` (Firestore) or in
tests (fakes). Consumer code (the coordinator, Track A orchestration)
depends on these Protocols rather than the concrete classes, so tests
can substitute fakes without touching production code paths.

Idempotency is a cross-cutting contract: both stores key their Firestore
docs by ``source_message_id``, and both ``save()`` methods must return the
existing record (not raise) when a duplicate write arrives — this is how
Pub/Sub at-least-once redelivery collapses safely.
"""

from __future__ import annotations

from typing import Optional, Protocol

from backend.models.exception_record import ExceptionRecord
from backend.models.order_record import OrderRecord


class OrderStore(Protocol):
    """Write + read surface for the ``orders`` collection."""

    async def save(self, record: OrderRecord) -> OrderRecord:
        """Persist ``record``. Idempotent on ``source_message_id``:
        a duplicate write returns the previously persisted record
        unchanged, rather than overwriting or raising."""
        ...

    async def get(self, source_message_id: str) -> Optional[OrderRecord]:
        """Load by Firestore doc id. ``None`` if absent."""
        ...

    async def update_with_confirmation(
        self, source_message_id: str, confirmation_body: str
    ) -> OrderRecord:
        """Write ``confirmation_body`` onto an already-persisted order.

        Raises when the doc does not exist — callers should only invoke
        this for orders that were just persisted by ``save()`` in the
        same pipeline invocation. Overwrites any prior confirmation_body
        on re-call (no idempotency skip — a re-run regenerates)."""
        ...


class ExceptionStore(Protocol):
    """Write + read + lifecycle surface for the ``exceptions`` collection."""

    async def save(self, record: ExceptionRecord) -> ExceptionRecord:
        """Persist ``record``. Idempotent on ``source_message_id``."""
        ...

    async def get(self, source_message_id: str) -> Optional[ExceptionRecord]:
        """Load by Firestore doc id. ``None`` if absent."""
        ...

    async def find_pending_clarify(self, thread_id: str) -> Optional[ExceptionRecord]:
        """Lookup by thread id for clarify-reply correlation.

        Returns only exceptions in ``PENDING_CLARIFY`` status — an
        ``AWAITING_REVIEW`` or ``RESOLVED`` exception in the same thread
        is intentionally not returned (a reply on a resolved thread
        should open a fresh exception, not retroactively modify the old
        one). If multiple pending exist, returns the most recent by
        ``created_at``.
        """
        ...

    async def update_with_reply(
        self, source_message_id: str, reply_message_id: str
    ) -> ExceptionRecord:
        """Advance an exception from ``PENDING_CLARIFY`` → ``AWAITING_REVIEW``.

        Sets ``reply_message_id`` and ``updated_at``. Raises if the
        exception is not found or is not in ``PENDING_CLARIFY`` — the
        status guard is enforced atomically via a Firestore transaction
        so a concurrent reply cannot double-advance the state.
        """
        ...


__all__ = ["OrderStore", "ExceptionStore"]
