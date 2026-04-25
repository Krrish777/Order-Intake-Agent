"""OAuth scopes for the Gmail-ingestion tracks.

A1 (ingress):  gmail.modify - read inbox + apply labels
A2 (egress):   + gmail.send - send messages
A3 (deploy):   no additional scope - watch uses the same subset
"""

GMAIL_MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"

A1_SCOPES = [GMAIL_MODIFY_SCOPE]

__all__ = ["GMAIL_MODIFY_SCOPE", "A1_SCOPES"]
