"""Live-integration smoke test for Gmail polling.

Gated behind @pytest.mark.gmail_live + env gate - CI + normal dev
runs skip. Intended to run manually once, immediately after
scripts/gmail_auth_init.py has produced a refresh token:

    GMAIL_LIVE_TEST=1 uv run pytest -m gmail_live

Requires GMAIL_CLIENT_ID + GMAIL_CLIENT_SECRET + GMAIL_REFRESH_TOKEN
in env (or .env loaded by the test itself). Also requires at least
one email sitting in the target inbox not carrying the label yet,
otherwise the test is trivially green.

Spec: docs/superpowers/specs/2026-04-24-track-a1-gmail-ingress-design.md
"""
from __future__ import annotations

import os

import pytest

pytestmark = [pytest.mark.gmail_live, pytest.mark.asyncio]


def _live_setup_available() -> bool:
    return (
        os.environ.get("GMAIL_LIVE_TEST") == "1"
        and os.environ.get("GMAIL_CLIENT_ID")
        and os.environ.get("GMAIL_CLIENT_SECRET")
        and os.environ.get("GMAIL_REFRESH_TOKEN")
    )


@pytest.mark.skipif(not _live_setup_available(), reason="GMAIL_LIVE_TEST + credentials not set")
async def test_one_tick_against_real_inbox_does_not_crash():
    from backend.gmail.client import GmailClient
    from backend.gmail.scopes import A1_SCOPES

    gmail_client = GmailClient(
        refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
        client_id=os.environ["GMAIL_CLIENT_ID"],
        client_secret=os.environ["GMAIL_CLIENT_SECRET"],
        scopes=A1_SCOPES,
    )

    # Smoke: just list messages - don't construct the full pipeline
    # (that'd require seeded master data + emulator / real Firestore).
    # The list_unprocessed call exercises the entire auth + API plumbing.
    ids = gmail_client.list_unprocessed(label_name="orderintake-processed")
    assert isinstance(ids, list)
    # And verify label_id_for creates the label if missing (idempotent)
    label_id = gmail_client.label_id_for("orderintake-processed")
    assert isinstance(label_id, str)
    assert label_id.startswith(("Label_", "CATEGORY_", "INBOX", "IMPORTANT"))
