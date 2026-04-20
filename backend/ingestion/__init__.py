"""Ingestion layer — produces uniform EmailEnvelope inputs for the agent pipeline.

For sprint 1 this layer is fed by ``scripts/inject_email.py`` reading local
``.eml`` fixtures rather than a live Gmail watch. The envelope shape is the
contract Track A (orchestration) consumes; swapping the source from CLI to
Gmail later is a question of who calls ``parse_eml`` (or its successor), not
what shape the agent sees.
"""

from backend.ingestion.email_envelope import EmailAttachment, EmailEnvelope
from backend.ingestion.eml_parser import EmlParseError, parse_eml

__all__ = [
    "EmailAttachment",
    "EmailEnvelope",
    "EmlParseError",
    "parse_eml",
]
