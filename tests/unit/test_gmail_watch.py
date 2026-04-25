"""Unit tests for GmailWatch wrapper.

Patches GmailClient._service with a MagicMock so no network.

Spec: docs/superpowers/specs/2026-04-24-track-a3-pubsub-ingestion-design.md
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.asyncio


def _make_watch():
    from backend.gmail.client import GmailClient
    from backend.gmail.watch import GmailWatch

    gmail_client = MagicMock(spec=GmailClient)
    # GmailClient exposes _service as an internal attr; we patch it directly
    gmail_client._service = MagicMock()
    watch = GmailWatch(gmail_client)
    return watch, gmail_client._service


class TestGmailWatch:
    async def test_start_calls_users_watch_with_topic_and_labels(self):
        watch, svc = _make_watch()
        svc.users().watch().execute.return_value = {
            "historyId": "12345",
            "expiration": "99999999",
        }

        result = await watch.start(
            topic_name="projects/p/topics/t",
            label_ids=["Label_X"],
        )

        assert result["historyId"] == "12345"
        call_kwargs = svc.users().watch.call_args.kwargs
        assert call_kwargs["userId"] == "me"
        assert call_kwargs["body"]["topicName"] == "projects/p/topics/t"
        assert call_kwargs["body"]["labelIds"] == ["Label_X"]

    async def test_start_omits_label_ids_when_none(self):
        watch, svc = _make_watch()
        svc.users().watch().execute.return_value = {"historyId": "1", "expiration": "2"}

        await watch.start(topic_name="projects/p/topics/t", label_ids=None)

        call_kwargs = svc.users().watch.call_args.kwargs
        assert "labelIds" not in call_kwargs["body"]

    async def test_get_profile_email_returns_email_address(self):
        watch, svc = _make_watch()
        svc.users().getProfile().execute.return_value = {
            "emailAddress": "agent@example.com",
            "historyId": "xyz",
        }

        result = await watch.get_profile_email()

        assert result == "agent@example.com"
