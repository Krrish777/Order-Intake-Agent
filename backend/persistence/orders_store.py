"""Firestore-backed implementation of :class:`~backend.persistence.base.OrderStore`.

Writes to the ``orders`` collection with ``source_message_id`` as the doc id.
Idempotent via optimistic ``create(exists=False)`` — a duplicate write raises
:class:`AlreadyExists` from the SDK, which we swallow and return the existing
record.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from google.api_core.exceptions import AlreadyExists
from google.cloud.firestore_v1 import SERVER_TIMESTAMP

from backend.models.judge_verdict import JudgeVerdict
from backend.models.order_record import OrderRecord

ORDERS_COLLECTION = "orders"


class FirestoreOrderStore:
    def __init__(self, client) -> None:
        self._client = client

    async def save(self, record: OrderRecord) -> OrderRecord:
        doc_ref = self._client.collection(ORDERS_COLLECTION).document(record.source_message_id)
        payload = record.model_dump(mode="python")
        payload["created_at"] = SERVER_TIMESTAMP
        try:
            await doc_ref.create(payload)
        except AlreadyExists:
            pass  # idempotent — fall through to re-read existing
        snap = await doc_ref.get()
        return OrderRecord(**snap.to_dict())

    async def get(self, source_message_id: str) -> Optional[OrderRecord]:
        doc_ref = self._client.collection(ORDERS_COLLECTION).document(source_message_id)
        snap = await doc_ref.get()
        if not snap.exists:
            return None
        return OrderRecord(**snap.to_dict())

    async def update_with_confirmation(
        self, source_message_id: str, confirmation_body: str
    ) -> OrderRecord:
        doc_ref = (
            self._client.collection(ORDERS_COLLECTION).document(source_message_id)
        )
        # Field-mask update: only the confirmation_body changes. The
        # Firestore async SDK's ``.update()`` raises NotFound if the
        # document is absent — the caller contract is that this is only
        # invoked after ``save()`` in the same invocation, so that's
        # exactly the failure mode we want to surface.
        await doc_ref.update({"confirmation_body": confirmation_body})
        snap = await doc_ref.get()
        return OrderRecord(**snap.to_dict())

    async def update_with_send_receipt(
        self,
        *,
        source_message_id: str,
        sent_at: Optional[datetime],
        send_error: Optional[str],
    ) -> None:
        doc_ref = self._client.collection(ORDERS_COLLECTION).document(source_message_id)
        await doc_ref.update({
            "sent_at": sent_at,
            "send_error": send_error,
        })

    async def update_with_judge_verdict(
        self,
        source_message_id: str,
        verdict: JudgeVerdict,
    ) -> None:
        doc_ref = self._client.collection(ORDERS_COLLECTION).document(source_message_id)
        await doc_ref.update(
            {"judge_verdict": verdict.model_dump(mode="json")}
        )
