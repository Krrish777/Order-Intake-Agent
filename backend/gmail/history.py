"""Gmail History API sync for Track A3.

Given a starting historyId, walks users.history.list pages and
returns (new message ids, latest historyId observed). Raises
HistoryIdTooOldError when Gmail returns 404 (cursor older than
the service's retention window); caller falls back to full-scan.

Spec: docs/superpowers/specs/2026-04-24-track-a3-pubsub-ingestion-design.md
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from backend.gmail.client import GmailClient


class HistoryIdTooOldError(Exception):
    """Raised when users.history.list returns 404 - historyId stale."""


async def fetch_new_message_ids(
    gmail_client: GmailClient,
    *,
    start_history_id: str,
    max_pages: int = 20,
) -> tuple[list[str], str]:
    """Walk history pages; return (new_message_ids, latest_history_id).

    Only collects messagesAdded[*].message.id. latest_history_id is
    the maximum historyId seen across all pages; caller persists it
    as the next cursor. Bounded by max_pages (default 20) to cap work
    when a long outage accumulates history.
    """
    new_ids: list[str] = []
    latest_id = start_history_id
    page_token: Optional[str] = None
    pages = 0

    svc = gmail_client._service

    def _page(token: Optional[str]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "userId": "me",
            "startHistoryId": start_history_id,
        }
        if token:
            kwargs["pageToken"] = token
        return svc.users().history().list(**kwargs).execute()

    try:
        while pages < max_pages:
            resp = await asyncio.to_thread(_page, page_token)

            for entry in resp.get("history", []):
                entry_id = entry.get("id")
                if entry_id and entry_id > latest_id:
                    latest_id = entry_id
                for added in entry.get("messagesAdded", []):
                    msg = added.get("message", {})
                    mid = msg.get("id")
                    if mid:
                        new_ids.append(mid)

            page_token = resp.get("nextPageToken")
            pages += 1
            if not page_token:
                break
    except Exception as exc:
        status = getattr(getattr(exc, "resp", None), "status", None)
        if status == 404:
            raise HistoryIdTooOldError(
                f"startHistoryId={start_history_id} is no longer available"
            ) from exc
        raise

    # Dedup while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for mid in new_ids:
        if mid not in seen:
            seen.add(mid)
            deduped.append(mid)
    return deduped, latest_id


__all__ = ["HistoryIdTooOldError", "fetch_new_message_ids"]
