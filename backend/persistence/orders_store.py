"""Firestore-backed implementation of :class:`~backend.persistence.base.OrderStore`.

Writes to the ``orders`` collection with ``source_message_id`` as the doc id.
Idempotent via optimistic ``create(exists=False)`` — a duplicate write raises
:class:`AlreadyExists` from the SDK, which we swallow and return the existing
record.
"""

from __future__ import annotations

from typing import Optional

from google.api_core.exceptions import AlreadyExists
from google.cloud.firestore_v1 import SERVER_TIMESTAMP

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
