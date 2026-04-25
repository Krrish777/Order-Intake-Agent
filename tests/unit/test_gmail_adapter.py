"""Unit tests for gmail_message_to_envelope adapter.

The adapter writes raw RFC 822 bytes to a NamedTemporaryFile then
calls the existing parse_eml - so these tests simultaneously verify
(a) the adapter doesn't lose bytes in the round-trip and (b) parse_eml
still handles whatever's in the fixture. The parse_eml suite itself
(tests/unit/test_eml_parser.py) is the deep coverage.
"""
from __future__ import annotations

from pathlib import Path

import pytest


FIXTURE_WITH_THREAD = Path("data/email/birch_valley_clarify_reply.eml")
# Any fixture with at least one attachment is fine for test #3
FIXTURE_WITH_ATTACHMENT = Path("data/pdf/patterson_po-28491.wrapper.eml")


@pytest.mark.asyncio
async def test_adapter_returns_email_envelope():
    from backend.gmail.adapter import gmail_message_to_envelope
    from backend.ingestion.email_envelope import EmailEnvelope

    raw = FIXTURE_WITH_THREAD.read_bytes()
    envelope = await gmail_message_to_envelope(raw)

    assert isinstance(envelope, EmailEnvelope)
    assert envelope.message_id  # non-empty string
    assert envelope.from_addr
    assert envelope.subject


@pytest.mark.asyncio
async def test_adapter_preserves_thread_headers():
    from backend.gmail.adapter import gmail_message_to_envelope

    raw = FIXTURE_WITH_THREAD.read_bytes()
    envelope = await gmail_message_to_envelope(raw)

    # birch_valley_clarify_reply is a reply fixture - must carry in_reply_to
    assert envelope.in_reply_to is not None
    assert envelope.in_reply_to != ""


@pytest.mark.asyncio
async def test_adapter_preserves_attachment_bytes():
    from backend.gmail.adapter import gmail_message_to_envelope
    from backend.ingestion.eml_parser import parse_eml

    raw = FIXTURE_WITH_ATTACHMENT.read_bytes()
    via_adapter = await gmail_message_to_envelope(raw)
    via_parse_eml_direct = parse_eml(FIXTURE_WITH_ATTACHMENT)

    assert len(via_adapter.attachments) == len(via_parse_eml_direct.attachments)
    for a, b in zip(via_adapter.attachments, via_parse_eml_direct.attachments):
        assert a.filename == b.filename
        assert a.content == b.content
