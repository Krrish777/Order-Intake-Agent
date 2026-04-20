"""Parse an RFC 5322 ``.eml`` file into an :class:`EmailEnvelope`.

Uses the modern stdlib ``email`` API (``policy=policy.default``) — that policy
returns an ``EmailMessage`` whose ``iter_attachments()`` and ``get_content()``
yield decoded bytes directly, avoiding manual base64 work.
"""

from __future__ import annotations

from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from pathlib import Path

from backend.ingestion.email_envelope import EmailAttachment, EmailEnvelope


class EmlParseError(Exception):
    """Raised when an ``.eml`` file cannot be parsed into an envelope.

    Local file parsing has no stages, job IDs, or status codes — kept as a
    plain exception rather than fitted into ``backend.utils.exceptions`` which
    is shaped around remote (LlamaCloud) failure modes.
    """


def parse_eml(path: Path) -> EmailEnvelope:
    """Parse an ``.eml`` file at ``path`` into an :class:`EmailEnvelope`."""
    if not path.exists():
        raise EmlParseError(f"file not found: {path}")

    try:
        with path.open("rb") as fp:
            msg = BytesParser(policy=policy.default).parse(fp)
    except OSError as exc:
        raise EmlParseError(f"could not read {path}: {exc}") from exc
    except Exception as exc:  # email lib raises a variety of types
        raise EmlParseError(f"malformed .eml at {path}: {exc}") from exc

    message_id = _required_header(msg, "Message-ID", path)
    from_addr = _required_header(msg, "From", path)
    to_addr = _required_header(msg, "To", path)
    subject = _required_header(msg, "Subject", path)

    date_header = msg["Date"]
    if date_header is None:
        raise EmlParseError(f"missing Date header in {path}")
    try:
        received_at = parsedate_to_datetime(date_header)
    except (TypeError, ValueError) as exc:
        raise EmlParseError(f"invalid Date header in {path}: {date_header!r}") from exc

    in_reply_to = msg["In-Reply-To"]
    references_raw = msg["References"]
    references = references_raw.split() if references_raw else []

    body_part = msg.get_body(preferencelist=("plain",))
    if body_part is None:
        raise EmlParseError(f"no text/plain body in {path}")
    body_text = body_part.get_content()

    attachments = [
        EmailAttachment(
            filename=part.get_filename() or "unknown.bin",
            content_type=part.get_content_type(),
            content=part.get_content(),
        )
        for part in msg.iter_attachments()
    ]

    return EmailEnvelope(
        message_id=message_id,
        in_reply_to=in_reply_to,
        references=references,
        thread_id=references[0] if references else message_id,
        from_addr=from_addr,
        to_addr=to_addr,
        subject=subject,
        received_at=received_at,
        body_text=body_text,
        attachments=attachments,
        source_path=str(path),
    )


def _required_header(msg, name: str, path: Path) -> str:
    value = msg[name]
    if value is None:
        raise EmlParseError(f"missing {name} header in {path}")
    return str(value)
