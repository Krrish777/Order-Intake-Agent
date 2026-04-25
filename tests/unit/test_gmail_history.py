"""Unit tests for Gmail History API sync.

Spec: docs/superpowers/specs/2026-04-24-track-a3-pubsub-ingestion-design.md
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.asyncio


def _make_client_with_history_responses(pages):
    """Build a MagicMock GmailClient whose history().list returns the
    given sequence of page dicts."""
    from backend.gmail.client import GmailClient

    gmail_client = MagicMock(spec=GmailClient)
    gmail_client._service = MagicMock()

    execute_returns = iter(pages)

    def _history_list(**kwargs):
        m = MagicMock()
        m.execute.return_value = next(execute_returns)
        return m

    gmail_client._service.users().history().list = _history_list
    return gmail_client


class _FakeHttpError(Exception):
    """Mimics googleapiclient.errors.HttpError enough for status detection."""

    def __init__(self, status: int) -> None:
        super().__init__(f"http {status}")
        self.resp = MagicMock()
        self.resp.status = status


class TestFetchNewMessageIds:
    async def test_collects_message_ids_across_pages(self):
        from backend.gmail.history import fetch_new_message_ids

        gmail_client = _make_client_with_history_responses([
            {
                "history": [
                    {"id": "101", "messagesAdded": [{"message": {"id": "m1"}}]},
                    {"id": "102", "messagesAdded": [{"message": {"id": "m2"}}]},
                ],
                "nextPageToken": "tok1",
            },
            {
                "history": [
                    {"id": "103", "messagesAdded": [{"message": {"id": "m3"}}]},
                ],
            },
        ])

        ids, latest = await fetch_new_message_ids(
            gmail_client, start_history_id="100"
        )

        assert ids == ["m1", "m2", "m3"]
        assert latest == "103"

    async def test_returns_latest_history_id_across_pages(self):
        from backend.gmail.history import fetch_new_message_ids

        gmail_client = _make_client_with_history_responses([
            {"history": [{"id": "500"}]},
        ])

        ids, latest = await fetch_new_message_ids(
            gmail_client, start_history_id="100"
        )

        assert ids == []
        assert latest == "500"

    async def test_dedupes_message_ids_across_overlapping_pages(self):
        from backend.gmail.history import fetch_new_message_ids

        gmail_client = _make_client_with_history_responses([
            {
                "history": [
                    {"id": "101", "messagesAdded": [{"message": {"id": "m1"}}]},
                    {"id": "102", "messagesAdded": [{"message": {"id": "m1"}}]},
                ],
            },
        ])

        ids, _ = await fetch_new_message_ids(
            gmail_client, start_history_id="100"
        )

        assert ids == ["m1"]

    async def test_raises_history_id_too_old_on_404(self):
        from backend.gmail.history import (
            HistoryIdTooOldError,
            fetch_new_message_ids,
        )

        gmail_client = MagicMock()
        gmail_client._service = MagicMock()

        def _raising_list(**kwargs):
            m = MagicMock()
            m.execute.side_effect = _FakeHttpError(404)
            return m

        gmail_client._service.users().history().list = _raising_list

        with pytest.raises(HistoryIdTooOldError):
            await fetch_new_message_ids(gmail_client, start_history_id="too-old")
