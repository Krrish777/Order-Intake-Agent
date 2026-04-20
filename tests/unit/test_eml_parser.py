"""Unit tests for ``backend.ingestion.eml_parser``.

Parametrized over every ``.eml`` fixture under ``data/``. New wrapper fixtures
get coverage automatically as they're added — no test changes needed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.ingestion import EmailEnvelope, EmlParseError, parse_eml

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DATA_ROOT = _PROJECT_ROOT / "data"

_BIRCH_VALLEY_ORIGINAL_ID = "<20260420091512.9A31.stanbirch@birchvalleyfarmeq.com>"
_REPLY_FIXTURE = _DATA_ROOT / "email" / "birch_valley_clarify_reply.eml"


def _all_eml_fixtures() -> list[Path]:
    return sorted(_DATA_ROOT.rglob("*.eml"))


@pytest.fixture(scope="session")
def eml_fixtures() -> list[Path]:
    fixtures = _all_eml_fixtures()
    if not fixtures:
        pytest.skip("no .eml fixtures found under data/")
    return fixtures


@pytest.mark.parametrize("path", _all_eml_fixtures(), ids=lambda p: p.name)
def test_parse_yields_envelope_with_message_id(path: Path) -> None:
    envelope = parse_eml(path)
    assert isinstance(envelope, EmailEnvelope)
    assert envelope.message_id, f"empty message_id for {path}"
    assert envelope.from_addr
    assert envelope.to_addr
    assert envelope.subject


@pytest.mark.parametrize("path", _all_eml_fixtures(), ids=lambda p: p.name)
def test_parse_yields_non_empty_body(path: Path) -> None:
    envelope = parse_eml(path)
    assert envelope.body_text.strip(), f"empty body_text for {path}"


@pytest.mark.parametrize("path", _all_eml_fixtures(), ids=lambda p: p.name)
def test_attachments_round_trip_bytes(path: Path) -> None:
    """For wrapper .emls (those with a sibling source file), the embedded
    attachment bytes must equal the source file's bytes byte-for-byte."""
    envelope = parse_eml(path)
    for attachment in envelope.attachments:
        sibling = path.parent / attachment.filename
        if sibling.exists() and sibling != path:
            assert attachment.content == sibling.read_bytes(), (
                f"attachment {attachment.filename} in {path.name} "
                f"differs from source {sibling}"
            )


def test_reply_links_to_original() -> None:
    """The clarify-reply fixture must reference the original Birch Valley
    Message-ID via In-Reply-To and References."""
    if not _REPLY_FIXTURE.exists():
        pytest.skip(f"reply fixture not authored yet: {_REPLY_FIXTURE.name}")

    envelope = parse_eml(_REPLY_FIXTURE)
    assert envelope.in_reply_to == _BIRCH_VALLEY_ORIGINAL_ID
    assert _BIRCH_VALLEY_ORIGINAL_ID in envelope.references
    assert envelope.thread_id == _BIRCH_VALLEY_ORIGINAL_ID


def test_missing_file_raises() -> None:
    with pytest.raises(EmlParseError, match="file not found"):
        parse_eml(_DATA_ROOT / "email" / "does-not-exist.eml")
