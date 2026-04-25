"""Firestore-backed cursor store for Gmail History API sync (Track A3).

One doc per authed inbox at gmail_sync_state/{user_email}.
Schema: {history_id: str, updated_at: SERVER_TIMESTAMP, user_email: str}.

Spec: docs/superpowers/specs/2026-04-24-track-a3-pubsub-ingestion-design.md
"""
from __future__ import annotations

from typing import Optional

from google.cloud.firestore_v1 import SERVER_TIMESTAMP


class GmailSyncStateStore:
    def __init__(self, client) -> None:
        self._client = client
        self._collection = "gmail_sync_state"

    async def get_cursor(self, user_email: str) -> Optional[str]:
        doc_ref = self._client.collection(self._collection).document(user_email)
        snap = await doc_ref.get()
        if not snap.exists:
            return None
        data = snap.to_dict() or {}
        hid = data.get("history_id")
        return hid if isinstance(hid, str) else None

    async def set_cursor(self, user_email: str, history_id: str) -> None:
        doc_ref = self._client.collection(self._collection).document(user_email)
        await doc_ref.set(
            {
                "history_id": history_id,
                "updated_at": SERVER_TIMESTAMP,
                "user_email": user_email,
            }
        )


__all__ = ["GmailSyncStateStore"]
