"""Gmail users.watch() wrapper for Track A3.

Thin sync wrapper around the Gmail Resource - watch() starts a push
subscription; stop() ends it; get_profile_email() returns the authed
user's email address for cursor keying.

Async methods wrap sync calls via asyncio.to_thread because
googleapiclient is sync-only.

Spec: docs/superpowers/specs/2026-04-24-track-a3-pubsub-ingestion-design.md
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from backend.gmail.client import GmailClient


class GmailWatch:
    def __init__(self, gmail_client: GmailClient) -> None:
        self._gmail = gmail_client

    async def start(
        self,
        *,
        topic_name: str,
        label_ids: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Start or re-assert a watch(). Returns {historyId, expiration}."""
        body: dict[str, Any] = {"topicName": topic_name}
        if label_ids:
            body["labelIds"] = label_ids
        return await asyncio.to_thread(
            lambda: self._gmail._service.users().watch(userId="me", body=body).execute()
        )

    async def stop(self) -> None:
        """End the current watch(). Optional - watch expires in 7 days anyway."""
        await asyncio.to_thread(
            lambda: self._gmail._service.users().stop(userId="me").execute()
        )

    async def get_profile_email(self) -> str:
        """users.getProfile(userId='me') -> emailAddress."""
        resp = await asyncio.to_thread(
            lambda: self._gmail._service.users().getProfile(userId="me").execute()
        )
        return resp["emailAddress"]


__all__ = ["GmailWatch"]
