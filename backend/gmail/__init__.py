"""Gmail integration package (Track A1 ingress side)."""
from backend.gmail.adapter import gmail_message_to_envelope
from backend.gmail.client import GmailClient
from backend.gmail.scopes import A1_SCOPES, GMAIL_MODIFY_SCOPE

__all__ = [
    "A1_SCOPES",
    "GMAIL_MODIFY_SCOPE",
    "GmailClient",
    "gmail_message_to_envelope",
]
