"""Authed Gmail API Resource wrapper for Track A1 ingress.

Sync-only (googleapiclient is sync). Async boundary lives at
poller.py via asyncio.to_thread. Methods map 1:1 onto the small
surface the poller needs - list_unprocessed, get_raw, label_id_for,
apply_label.

Spec: docs/superpowers/specs/2026-04-24-track-a1-gmail-ingress-design.md
"""
from __future__ import annotations

import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from typing import Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource, build

_GMAIL_TOKEN_URL = "https://oauth2.googleapis.com/token"


def _bracket(m: str) -> str:
    """Ensure RFC 5322 Message-ID refs are angle-bracketed."""
    m = m.strip()
    if m.startswith("<") and m.endswith(">"):
        return m
    return f"<{m}>"


class GmailClient:
    """Sync wrapper around a Gmail API Resource.

    Construct once per process; the underlying HTTP transport +
    Credentials object handle access-token refresh automatically.
    """

    def __init__(
        self,
        *,
        refresh_token: str,
        client_id: str,
        client_secret: str,
        scopes: list[str],
        query_override: Optional[str] = None,
    ) -> None:
        credentials = Credentials(
            token=None,
            refresh_token=refresh_token,
            client_id=client_id,
            client_secret=client_secret,
            token_uri=_GMAIL_TOKEN_URL,
            scopes=scopes,
        )
        self._service: Resource = build(
            "gmail", "v1", credentials=credentials, cache_discovery=False
        )
        self._label_id_cache: dict[str, str] = {}
        self._query_override = query_override

    # ---- read surface ----

    def list_unprocessed(
        self,
        *,
        label_name: str,
        max_results: int = 50,
    ) -> list[str]:
        # Dedup filter is always appended so the override can't cause
        # re-processing of already-handled mail. Override replaces only
        # the inbox-scoping clause (default: "in:inbox").
        base = self._query_override or "in:inbox"
        query = f"{base} -label:{label_name}"
        resp = (
            self._service.users()
            .messages()
            .list(userId="me", q=query, maxResults=max_results)
            .execute()
        )
        return [m["id"] for m in resp.get("messages", [])]

    def get_raw(self, message_id: str) -> bytes:
        resp = (
            self._service.users()
            .messages()
            .get(userId="me", id=message_id, format="raw")
            .execute()
        )
        raw_b64url: Optional[str] = resp.get("raw")
        if raw_b64url is None:
            raise ValueError(f"Gmail message {message_id} has no raw payload")
        return base64.urlsafe_b64decode(raw_b64url.encode("ascii"))

    # ---- label surface ----

    def label_id_for(self, label_name: str) -> str:
        if label_name in self._label_id_cache:
            return self._label_id_cache[label_name]

        resp = self._service.users().labels().list(userId="me").execute()
        for label in resp.get("labels", []):
            if label["name"] == label_name:
                self._label_id_cache[label_name] = label["id"]
                return label["id"]

        created = (
            self._service.users()
            .labels()
            .create(
                userId="me",
                body={
                    "name": label_name,
                    "labelListVisibility": "labelShow",
                    "messageListVisibility": "show",
                },
            )
            .execute()
        )
        self._label_id_cache[label_name] = created["id"]
        return created["id"]

    def apply_label(self, message_id: str, label_id: str) -> None:
        self._service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"addLabelIds": [label_id]},
        ).execute()

    # ---- send surface ----

    def send_message(
        self,
        *,
        to: str,
        subject: str,
        body_text: str,
        in_reply_to: Optional[str] = None,
        references: Optional[list[str]] = None,
    ) -> str:
        """Send a plain-text email via users.messages.send.

        Constructs RFC 5322 MIME with thread-reply headers. Auto-prepends
        'Re: ' to subject when not already present. Returns the sent
        Gmail message id.
        """
        msg = MIMEMultipart()
        msg["To"] = to
        msg["From"] = "me"
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid()

        if in_reply_to:
            msg["In-Reply-To"] = _bracket(in_reply_to)
        if references:
            msg["References"] = " ".join(_bracket(r) for r in references)

        msg.attach(MIMEText(body_text, "plain"))

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        resp = (
            self._service.users()
            .messages()
            .send(userId="me", body={"raw": raw})
            .execute()
        )
        return resp["id"]


__all__ = ["GmailClient"]
