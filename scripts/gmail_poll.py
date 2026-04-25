#!/usr/bin/env python3
"""Runnable Gmail polling loop.

Usage:
    uv run python scripts/gmail_poll.py

Reads env (via .env or process env):
  GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN (required)
  GMAIL_POLL_INTERVAL_SECONDS (optional, default 30)
  GMAIL_PROCESSED_LABEL (optional, default 'orderintake-processed')
  FIRESTORE_EMULATOR_HOST (if using emulator; else prod Firestore)
  GOOGLE_API_KEY, LLAMA_CLOUD_API_KEY (required by the pipeline)

Ctrl-C exits cleanly. Fatal errors during pipeline construction
propagate. No auto-restart: use a process supervisor (systemd, pm2,
tmux) if you want it to stay up across crashes.

Spec: docs/superpowers/specs/2026-04-24-track-a1-gmail-ingress-design.md
"""
from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService

from backend.gmail.client import GmailClient
from backend.gmail.poller import GmailPoller
from backend.gmail.scopes import A1_SCOPES
from backend.my_agent.agent import _build_default_root_agent


async def _main() -> int:
    load_dotenv()

    required = ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN")
    for var in required:
        if not os.environ.get(var):
            print(f"error: {var} missing from env/.env", file=sys.stderr)
            print(
                "hint: run 'uv run python scripts/gmail_auth_init.py path/to/credentials.json' first",
                file=sys.stderr,
            )
            return 2

    poll_interval = int(os.environ.get("GMAIL_POLL_INTERVAL_SECONDS", "30"))
    label_name = os.environ.get("GMAIL_PROCESSED_LABEL", "orderintake-processed")

    gmail_client = GmailClient(
        refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
        client_id=os.environ["GMAIL_CLIENT_ID"],
        client_secret=os.environ["GMAIL_CLIENT_SECRET"],
        scopes=A1_SCOPES,
    )

    root_agent = _build_default_root_agent()
    session_service = InMemorySessionService()
    runner = Runner(
        app_name="order_intake",
        agent=root_agent,
        session_service=session_service,
    )

    poller = GmailPoller(
        gmail_client=gmail_client,
        runner=runner,
        session_service=session_service,
        root_agent=root_agent,
        label_name=label_name,
        poll_interval_seconds=poll_interval,
    )

    try:
        await poller.run_forever()
    except asyncio.CancelledError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
