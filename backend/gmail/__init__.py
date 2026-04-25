"""Gmail integration package (Track A1 ingress side).

Public surface:
- GmailClient (client.py)
- gmail_message_to_envelope (adapter.py)
- GmailPoller (poller.py)
- GMAIL_MODIFY_SCOPE, A1_SCOPES (scopes.py)
"""
from backend.gmail.scopes import A1_SCOPES, GMAIL_MODIFY_SCOPE

# Other exports added as modules land in subsequent tasks.

__all__ = ["A1_SCOPES", "GMAIL_MODIFY_SCOPE"]
