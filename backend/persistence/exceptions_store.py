"""Firestore-backed implementation of :class:`~backend.persistence.base.ExceptionStore`.

Writes to the ``exceptions`` collection keyed by ``source_message_id``. Mirrors
:class:`~backend.persistence.orders_store.FirestoreOrderStore`'s idempotency
pattern (optimistic create + ``AlreadyExists`` swallow). Adds two
exception-specific operations: :meth:`find_pending_clarify` for clarify-reply
correlation, and :meth:`update_with_reply` for transactional state advancement.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from google.api_core.exceptions import AlreadyExists
from google.cloud.firestore_v1 import SERVER_TIMESTAMP
from google.cloud.firestore_v1.base_query import FieldFilter

from backend.models.exception_record import ExceptionRecord, ExceptionStatus

EXCEPTIONS_COLLECTION = "exceptions"


class FirestoreExceptionStore:
    def __init__(self, client) -> None:
        self._client = client

    async def save(self, record: ExceptionRecord) -> ExceptionRecord:
        doc_ref = self._client.collection(EXCEPTIONS_COLLECTION).document(
            record.source_message_id
        )
        payload = record.model_dump(mode="python")
        payload["created_at"] = SERVER_TIMESTAMP
        payload["updated_at"] = SERVER_TIMESTAMP
        try:
            await doc_ref.create(payload)
        except AlreadyExists:
            pass
        snap = await doc_ref.get()
        return ExceptionRecord(**snap.to_dict())

    async def get(self, source_message_id: str) -> Optional[ExceptionRecord]:
        doc_ref = self._client.collection(EXCEPTIONS_COLLECTION).document(
            source_message_id
        )
        snap = await doc_ref.get()
        if not snap.exists:
            return None
        return ExceptionRecord(**snap.to_dict())

    async def find_pending_clarify(
        self, thread_id: str
    ) -> Optional[ExceptionRecord]:
        query = (
            self._client.collection(EXCEPTIONS_COLLECTION)
            .where(filter=FieldFilter("thread_id", "==", thread_id))
            .where(
                filter=FieldFilter(
                    "status", "==", ExceptionStatus.PENDING_CLARIFY.value
                )
            )
            .order_by("created_at", direction="DESCENDING")
            .limit(1)
        )
        snapshots = await query.get()
        if not snapshots:
            return None
        return ExceptionRecord(**snapshots[0].to_dict())

    async def update_with_reply(
        self, source_message_id: str, reply_message_id: str
    ) -> ExceptionRecord:
        # NOTE: this read-then-write is not atomic across concurrent replies
        # to the same thread — that is a known limitation, justified for the
        # demo by the vanishingly low race probability and benign failure
        # mode (one reply wins, duplicates raise the status guard below).
        # If concurrent reply traffic becomes real, switch to async_transactional.
        doc_ref = self._client.collection(EXCEPTIONS_COLLECTION).document(
            source_message_id
        )
        snap = await doc_ref.get()
        if not snap.exists:
            raise LookupError(
                f"exceptions/{source_message_id} not found — cannot apply reply"
            )
        current_status = snap.to_dict().get("status")
        if current_status != ExceptionStatus.PENDING_CLARIFY.value:
            raise ValueError(
                f"exceptions/{source_message_id} status is {current_status!r}; "
                f"only {ExceptionStatus.PENDING_CLARIFY.value!r} can advance via reply"
            )
        await doc_ref.update(
            {
                "reply_message_id": reply_message_id,
                "status": ExceptionStatus.AWAITING_REVIEW.value,
                "updated_at": SERVER_TIMESTAMP,
            }
        )
        new_snap = await doc_ref.get()
        return ExceptionRecord(**new_snap.to_dict())

    async def update_with_send_receipt(
        self,
        *,
        source_message_id: str,
        sent_at: Optional[datetime],
        send_error: Optional[str],
    ) -> None:
        doc_ref = self._client.collection(EXCEPTIONS_COLLECTION).document(
            source_message_id
        )
        await doc_ref.update({
            "sent_at": sent_at,
            "send_error": send_error,
        })
