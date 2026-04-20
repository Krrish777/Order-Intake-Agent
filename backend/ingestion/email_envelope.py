"""Pydantic models for the email envelope handed to the agent pipeline.

Field naming is deliberate: ``from_addr`` / ``to_addr`` / ``body_text`` rather
than ``from_email`` / ``body``. The shared ``_drop_pii`` processor in
``backend/utils/logging.py`` strips fields literally named ``email``,
``raw_content``, etc., so a model field named ``email`` would silently vanish
from logs.
"""

from __future__ import annotations

import base64
from datetime import datetime

from pydantic import BaseModel, Field, field_serializer


class EmailAttachment(BaseModel):
    """A single attachment carried by an EmailEnvelope.

    In-memory, ``content`` is the raw decoded bytes — Track A reads this
    directly. For JSON output (CLI), the field serializer base64-encodes it
    so binary payloads like PDFs survive UTF-8 validation.
    """

    filename: str
    content_type: str
    content: bytes

    @field_serializer("content")
    def _serialize_content(self, value: bytes) -> str:
        return base64.b64encode(value).decode("ascii")


class EmailEnvelope(BaseModel):
    """The shape Track A consumes regardless of whether the source is a local
    ``.eml`` file or, eventually, a Gmail push notification.

    ``message_id`` is the dedup key. ``in_reply_to`` / ``references`` carry
    threading so a clarification reply can be correlated back to its pending
    ``ExceptionRecord``.
    """

    message_id: str
    in_reply_to: str | None = None
    references: list[str] = Field(default_factory=list)
    thread_id: str | None = None
    from_addr: str
    to_addr: str
    subject: str
    received_at: datetime
    body_text: str
    attachments: list[EmailAttachment] = Field(default_factory=list)
    source_path: str | None = None
