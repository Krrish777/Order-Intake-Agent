"""Unit tests for GmailSyncStateStore.

Reuses the FakeAsyncClient fixture from conftest.py.

Spec: docs/superpowers/specs/2026-04-24-track-a3-pubsub-ingestion-design.md
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


class TestGmailSyncStateStore:
    async def test_get_cursor_returns_none_for_missing_doc(self, fake_client):
        from backend.persistence.sync_state_store import GmailSyncStateStore

        store = GmailSyncStateStore(fake_client)
        result = await store.get_cursor("new-user@example.com")

        assert result is None

    async def test_set_cursor_then_get_returns_history_id(self, fake_client):
        from backend.persistence.sync_state_store import GmailSyncStateStore

        store = GmailSyncStateStore(fake_client)
        await store.set_cursor("user@example.com", "history-12345")
        result = await store.get_cursor("user@example.com")

        assert result == "history-12345"

    async def test_set_cursor_upserts_existing_doc(self, fake_client):
        from backend.persistence.sync_state_store import GmailSyncStateStore

        store = GmailSyncStateStore(fake_client)
        await store.set_cursor("user@example.com", "history-1")
        await store.set_cursor("user@example.com", "history-2")
        result = await store.get_cursor("user@example.com")

        assert result == "history-2"
