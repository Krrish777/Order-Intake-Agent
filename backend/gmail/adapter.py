"""Raw Gmail bytes -> EmailEnvelope via parse_eml.

parse_eml takes a filesystem Path today. This helper writes raw RFC
822 bytes to a NamedTemporaryFile so we can reuse every multipart /
attachment / encoding edge case already handled by the existing
parser. Zero new parsing code.

Spec: docs/superpowers/specs/2026-04-24-track-a1-gmail-ingress-design.md
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from backend.ingestion.email_envelope import EmailEnvelope
from backend.ingestion.eml_parser import parse_eml


async def gmail_message_to_envelope(raw_rfc822: bytes) -> EmailEnvelope:
    """Parse raw RFC 822 bytes via the existing .eml parser.

    Writes bytes to a NamedTemporaryFile because parse_eml is
    Path-only by design. Cleans up the tempfile in a finally.
    """
    fd, tmp_name = tempfile.mkstemp(suffix=".eml")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw_rfc822)
        return parse_eml(Path(tmp_name))
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass


__all__ = ["gmail_message_to_envelope"]
