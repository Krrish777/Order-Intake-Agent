"""Round-trip tests for ``EmailAttachment.content`` through JSON dump/validate.

Downstream stages (notably ParseStage in Track A Step 4d) rehydrate the
envelope from ADK session state and hand ``attachment.content`` directly to
LlamaExtract. Any asymmetry between the field serializer and validator would
silently corrupt binary payloads. These tests lock the invariant.
"""

from __future__ import annotations

from backend.ingestion.email_envelope import EmailAttachment


def _roundtrip(content: bytes) -> bytes:
    att = EmailAttachment(
        filename="x.bin",
        content_type="application/octet-stream",
        content=content,
    )
    dumped = att.model_dump(mode="json")
    rehydrated = EmailAttachment.model_validate(dumped)
    return rehydrated.content


def test_roundtrip_ascii_content() -> None:
    original = b"plain text"
    assert _roundtrip(original) == original


def test_roundtrip_non_ascii_binary_content() -> None:
    # Representative of real PDF / PNG bytes — leading magic numbers plus
    # high bytes that are not valid UTF-8.
    original = b"\x89PNG\r\n\x1a\ndeadbeef\xffsome binary"
    assert _roundtrip(original) == original


def test_roundtrip_empty_bytes() -> None:
    assert _roundtrip(b"") == b""
