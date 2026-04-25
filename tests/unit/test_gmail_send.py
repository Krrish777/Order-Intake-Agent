"""Unit tests for GmailClient.send_message.

All tests patch googleapiclient.discovery.build to return a
MagicMock Resource - no network.

Spec: docs/superpowers/specs/2026-04-24-track-a2-gmail-egress-design.md
"""
from __future__ import annotations

import base64
from email import message_from_bytes
from unittest.mock import MagicMock, patch


def _make_client():
    from backend.gmail.client import GmailClient

    patcher = patch("backend.gmail.client.build")
    mock_build = patcher.start()
    mock_service = MagicMock()
    mock_build.return_value = mock_service

    client = GmailClient(
        refresh_token="rt-abc",
        client_id="cid-123",
        client_secret="sec-456",
        scopes=[
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/gmail.send",
        ],
    )
    return client, mock_service, patcher


def _teardown(patcher):
    patcher.stop()


def _decode_sent_mime(svc):
    """Pull the `raw` field out of the last send(...) call and parse MIME."""
    send_call = svc.users().messages().send.call_args
    raw_b64 = send_call.kwargs["body"]["raw"]
    raw_bytes = base64.urlsafe_b64decode(raw_b64.encode("ascii"))
    return message_from_bytes(raw_bytes)


class TestGmailClientSendMessage:
    def test_send_message_sets_headers_correctly(self):
        client, svc, patcher = _make_client()
        try:
            svc.users().messages().send().execute.return_value = {"id": "gmail-1"}

            gmail_id = client.send_message(
                to="customer@example.com",
                subject="Re: Order confirmation",
                body_text="Thank you for your order.",
                in_reply_to="<orig-msg@mailer>",
                references=["<root-msg@mailer>", "<orig-msg@mailer>"],
            )

            assert gmail_id == "gmail-1"
            mime = _decode_sent_mime(svc)
            assert mime["To"] == "customer@example.com"
            assert mime["Subject"] == "Re: Order confirmation"
            assert mime["In-Reply-To"] == "<orig-msg@mailer>"
            assert "<orig-msg@mailer>" in mime["References"]
            assert "<root-msg@mailer>" in mime["References"]
        finally:
            _teardown(patcher)

    def test_send_message_auto_prepends_re_when_missing(self):
        client, svc, patcher = _make_client()
        try:
            svc.users().messages().send().execute.return_value = {"id": "gmail-2"}

            client.send_message(
                to="c@e.com",
                subject="New order",
                body_text="body",
                in_reply_to=None,
                references=None,
            )

            mime = _decode_sent_mime(svc)
            assert mime["Subject"] == "Re: New order"
        finally:
            _teardown(patcher)

    def test_send_message_preserves_subject_when_re_already_present(self):
        client, svc, patcher = _make_client()
        try:
            svc.users().messages().send().execute.return_value = {"id": "gmail-3"}

            client.send_message(
                to="c@e.com",
                subject="Re: Existing reply",
                body_text="body",
                in_reply_to=None,
                references=None,
            )

            mime = _decode_sent_mime(svc)
            assert mime["Subject"] == "Re: Existing reply"
        finally:
            _teardown(patcher)

    def test_send_message_returns_gmail_id_from_api_response(self):
        client, svc, patcher = _make_client()
        try:
            svc.users().messages().send().execute.return_value = {"id": "expected-id-xyz"}
            result = client.send_message(
                to="c@e.com",
                subject="s",
                body_text="b",
                in_reply_to=None,
                references=None,
            )
            assert result == "expected-id-xyz"
        finally:
            _teardown(patcher)

    def test_send_message_without_reply_headers_still_valid_mime(self):
        client, svc, patcher = _make_client()
        try:
            svc.users().messages().send().execute.return_value = {"id": "gmail-5"}

            client.send_message(
                to="c@e.com",
                subject="New conversation",
                body_text="Hello.",
                in_reply_to=None,
                references=None,
            )

            mime = _decode_sent_mime(svc)
            assert mime["To"] == "c@e.com"
            assert mime["In-Reply-To"] is None
            assert mime["References"] is None
            # body present
            payload = mime.get_payload()
            assert payload
        finally:
            _teardown(patcher)
