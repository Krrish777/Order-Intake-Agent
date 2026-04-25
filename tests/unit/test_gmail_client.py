"""Unit tests for GmailClient sync wrapper.

All tests patch googleapiclient.discovery.build to return a
MagicMock Resource - no network calls.

Spec: docs/superpowers/specs/2026-04-24-track-a1-gmail-ingress-design.md
"""
from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

import pytest


def _make_client():
    """Build a GmailClient with a patched Resource."""
    from backend.gmail.client import GmailClient

    patcher = patch("backend.gmail.client.build")
    mock_build = patcher.start()
    mock_service = MagicMock()
    mock_build.return_value = mock_service

    client = GmailClient(
        refresh_token="rt-abc",
        client_id="cid-123",
        client_secret="sec-456",
        scopes=["https://www.googleapis.com/auth/gmail.modify"],
    )
    return client, mock_service, patcher


def _teardown(patcher):
    patcher.stop()


class TestGmailClientListUnprocessed:
    def test_list_unprocessed_issues_expected_query(self):
        client, svc, patcher = _make_client()
        try:
            svc.users().messages().list().execute.return_value = {
                "messages": [{"id": "a"}, {"id": "b"}]
            }

            result = client.list_unprocessed(label_name="foo")
            assert result == ["a", "b"]
            # Verify the query shape - last .list(...) call kwargs
            last_list_call = svc.users().messages().list.call_args
            assert last_list_call.kwargs["userId"] == "me"
            assert last_list_call.kwargs["q"] == "in:inbox -label:foo"
            assert last_list_call.kwargs["maxResults"] == 50
        finally:
            _teardown(patcher)

    def test_list_unprocessed_returns_empty_on_no_messages(self):
        client, svc, patcher = _make_client()
        try:
            svc.users().messages().list().execute.return_value = {}
            assert client.list_unprocessed(label_name="foo") == []
        finally:
            _teardown(patcher)

    def test_list_unprocessed_preserves_order(self):
        client, svc, patcher = _make_client()
        try:
            svc.users().messages().list().execute.return_value = {
                "messages": [{"id": "z"}, {"id": "a"}, {"id": "m"}]
            }
            assert client.list_unprocessed(label_name="foo") == ["z", "a", "m"]
        finally:
            _teardown(patcher)


class TestGmailClientGetRaw:
    def test_get_raw_decodes_base64url_to_bytes(self):
        client, svc, patcher = _make_client()
        try:
            raw_bytes = b"From: test@test\r\n\r\nhello"
            encoded = base64.urlsafe_b64encode(raw_bytes).decode("ascii")
            svc.users().messages().get().execute.return_value = {"raw": encoded}

            result = client.get_raw("msg-1")
            assert result == raw_bytes
        finally:
            _teardown(patcher)

    def test_get_raw_raises_on_missing_raw_field(self):
        client, svc, patcher = _make_client()
        try:
            svc.users().messages().get().execute.return_value = {}
            with pytest.raises(ValueError, match="msg-1"):
                client.get_raw("msg-1")
        finally:
            _teardown(patcher)


class TestGmailClientLabels:
    def test_label_id_for_returns_existing_label_and_caches(self):
        client, svc, patcher = _make_client()
        try:
            svc.users().labels().list().execute.return_value = {
                "labels": [{"id": "Label_1", "name": "foo"}]
            }
            # First call
            assert client.label_id_for("foo") == "Label_1"
            # Second call should be cache-hit - verify labels.list not called again
            labels_list_calls_before = svc.users().labels().list.call_count
            assert client.label_id_for("foo") == "Label_1"
            labels_list_calls_after = svc.users().labels().list.call_count
            assert labels_list_calls_after == labels_list_calls_before
        finally:
            _teardown(patcher)

    def test_label_id_for_creates_when_missing(self):
        client, svc, patcher = _make_client()
        try:
            svc.users().labels().list().execute.return_value = {"labels": []}
            svc.users().labels().create().execute.return_value = {
                "id": "Label_new",
                "name": "bar",
            }
            assert client.label_id_for("bar") == "Label_new"
            # Verify create was called
            create_body = svc.users().labels().create.call_args.kwargs["body"]
            assert create_body["name"] == "bar"
        finally:
            _teardown(patcher)

    def test_apply_label_calls_modify_with_add_label_ids(self):
        client, svc, patcher = _make_client()
        try:
            client.apply_label("msg-1", "Label_X")
            modify_call = svc.users().messages().modify.call_args
            assert modify_call.kwargs["userId"] == "me"
            assert modify_call.kwargs["id"] == "msg-1"
            assert modify_call.kwargs["body"] == {"addLabelIds": ["Label_X"]}
        finally:
            _teardown(patcher)
