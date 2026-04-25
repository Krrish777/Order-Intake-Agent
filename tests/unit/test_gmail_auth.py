"""Unit tests for Gmail OAuth scopes.

Spec: docs/superpowers/specs/2026-04-24-track-a1-gmail-ingress-design.md
"""
from __future__ import annotations


def test_gmail_modify_scope_is_the_official_uri():
    from backend.gmail.scopes import GMAIL_MODIFY_SCOPE

    assert GMAIL_MODIFY_SCOPE == "https://www.googleapis.com/auth/gmail.modify"


def test_a1_scopes_is_exactly_gmail_modify():
    from backend.gmail.scopes import A1_SCOPES, GMAIL_MODIFY_SCOPE

    assert A1_SCOPES == [GMAIL_MODIFY_SCOPE]


def test_gmail_send_scope_is_the_official_uri():
    from backend.gmail.scopes import GMAIL_SEND_SCOPE

    assert GMAIL_SEND_SCOPE == "https://www.googleapis.com/auth/gmail.send"


def test_a2_scopes_extends_a1_with_send():
    from backend.gmail.scopes import A1_SCOPES, A2_SCOPES, GMAIL_SEND_SCOPE

    assert A2_SCOPES == A1_SCOPES + [GMAIL_SEND_SCOPE]
    assert len(A2_SCOPES) == len(A1_SCOPES) + 1
