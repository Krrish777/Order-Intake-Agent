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
