"""The :class:`IngestStage` — stage #1 of the Order Intake pipeline.

Reads ``ctx.user_content.parts[0].text`` — either a filesystem path to an
``.eml`` file or the raw EML content itself — parses it via
:func:`backend.ingestion.eml_parser.parse_eml`, and writes the resulting
:class:`EmailEnvelope` onto ``session.state['envelope']`` via an
``EventActions.state_delta``. Downstream stages consume the dict back.

Body-only emails (no MIME attachments — e.g. free-text POs, clarify replies)
are normalised here by synthesising a single ``body.txt`` attachment whose
content is the decoded ``body_text``. This lets ClassifyStage and ParseStage
treat every email uniformly regardless of whether the order arrived inline
or as a PDF/XLSX attachment.
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Final

from google.adk.agents.invocation_context import InvocationContext
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions
from google.genai import types

from backend.ingestion.email_envelope import EmailAttachment, EmailEnvelope
from backend.ingestion.eml_parser import parse_eml
from backend.my_agent.stages._audited import AuditedStage

INGEST_STAGE_NAME: Final[str] = "ingest_stage"

# Case-insensitive leading tokens that unambiguously mark a message as RFC 5322
# headers rather than a filesystem path. Paired with a blank-line check to
# avoid false positives on header-looking path fragments. Trace headers
# (Delivered-To, Received) come first on real inbound mail — sender-set
# headers like From / Message-ID land below them.
_MIME_HEADER_PREFIXES: Final[tuple[str, ...]] = (
    "delivered-to:",
    "received:",
    "return-path:",
    "from:",
    "message-id:",
)


def _looks_like_raw_eml(text: str) -> bool:
    """Return True when ``text`` looks like raw RFC 5322 content.

    The heuristic: must contain a blank line (``\\r\\n\\r\\n`` or ``\\n\\n``)
    AND start with a canonical MIME header. Pure paths never satisfy both.
    """
    if "\r\n\r\n" not in text and "\n\n" not in text:
        return False
    head = text.lstrip()[:32].lower()
    return any(head.startswith(prefix) for prefix in _MIME_HEADER_PREFIXES)


class IngestStage(AuditedStage):
    """AuditedStage that materialises an :class:`EmailEnvelope` on session state.

    Constructed with ``audit_logger`` only — the agent's ``name`` is baked in
    via the default on the Pydantic field, mirroring how :class:`SequentialAgent`
    keeps its own ``name`` assignable-but-defaulted.
    """

    name: str = INGEST_STAGE_NAME

    def __init__(self, *, audit_logger: Any) -> None:
        super().__init__(audit_logger=audit_logger)

    async def _audited_run(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        text = _extract_user_text(ctx)

        if _looks_like_raw_eml(text):
            envelope = _parse_raw_content(text)
        else:
            envelope = parse_eml(Path(text.strip()))

        if not envelope.attachments:
            synthetic = EmailAttachment(
                filename="body.txt",
                content_type="text/plain",
                content=envelope.body_text.encode("utf-8"),
            )
            envelope = envelope.model_copy(update={"attachments": [synthetic]})

        correlation_id = uuid.uuid4().hex
        yield Event(
            author=INGEST_STAGE_NAME,
            actions=EventActions(
                state_delta={
                    "envelope": envelope.model_dump(mode="json"),
                    "correlation_id": correlation_id,
                }
            ),
            content=types.Content(
                role="model",
                parts=[
                    types.Part(
                        text=(
                            f"Ingested {envelope.message_id} "
                            f"({len(envelope.attachments)} attachment"
                            f"{'s' if len(envelope.attachments) != 1 else ''})"
                        )
                    )
                ],
            ),
        )
        await self._audit_logger.emit(
            correlation_id=correlation_id,
            session_id=ctx.session.id,
            source_message_id=envelope.message_id,
            stage="lifecycle",
            phase="lifecycle",
            action="envelope_received",
            payload={"attachment_count": len(envelope.attachments)},
        )


def _extract_user_text(ctx: InvocationContext) -> str:
    """Pull the text payload from ``ctx.user_content`` with fail-fast checks."""
    user_content = getattr(ctx, "user_content", None)
    if user_content is None or not getattr(user_content, "parts", None):
        raise ValueError(
            "IngestStage requires user message with a .eml path or raw EML content"
        )
    text = user_content.parts[0].text or ""
    if not text.strip():
        raise ValueError(
            "IngestStage requires user message with a .eml path or raw EML content"
        )
    return text


def _parse_raw_content(text: str) -> EmailEnvelope:
    """Persist ``text`` to a temp ``.eml`` file and parse it.

    ``parse_eml`` is Path-only by design (Step 4a does not touch existing
    parser code). The tempfile is always unlinked, even if parsing raises.
    """
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".eml", mode="wb")
    try:
        tmp.write(text.encode("utf-8"))
        tmp.close()
        return parse_eml(Path(tmp.name))
    finally:
        Path(tmp.name).unlink(missing_ok=True)


__all__ = ["INGEST_STAGE_NAME", "IngestStage"]
