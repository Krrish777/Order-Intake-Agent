#!/usr/bin/env python3
"""One-shot Gmail watch renewal — runs as a Cloud Run Job on a daily cron.

Calls Gmail users.watch() once and exits. Cloud Scheduler triggers this
every 24h to keep the push subscription alive (Gmail expires watches
after 7 days, but Google recommends renewing every 1-2 days).

Reads the same Gmail OAuth env as the push service. Writes the new
historyId/expiration to stdout for Cloud Logging.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv

from backend.gmail.client import GmailClient
from backend.gmail.scopes import A2_SCOPES
from backend.gmail.watch import GmailWatch


async def _main() -> int:
    load_dotenv()

    required = (
        "GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN",
        "GMAIL_PUBSUB_PROJECT_ID", "GMAIL_PUBSUB_TOPIC",
    )
    for var in required:
        if not os.environ.get(var):
            print(f"error: {var} missing", file=sys.stderr)
            return 2

    project_id = os.environ["GMAIL_PUBSUB_PROJECT_ID"]
    topic_name = f"projects/{project_id}/topics/{os.environ['GMAIL_PUBSUB_TOPIC']}"

    gmail_client = GmailClient(
        refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
        client_id=os.environ["GMAIL_CLIENT_ID"],
        client_secret=os.environ["GMAIL_CLIENT_SECRET"],
        scopes=A2_SCOPES,
    )
    watch = GmailWatch(gmail_client)

    result = await watch.start(topic_name=topic_name, label_ids=None)
    print(
        f"gmail_watch_renewed historyId={result.get('historyId')} "
        f"expiration={result.get('expiration')}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
