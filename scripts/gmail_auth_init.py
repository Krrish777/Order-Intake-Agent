#!/usr/bin/env python3
"""One-time OAuth bootstrap for the Gmail poller.

Usage:
    uv run python scripts/gmail_auth_init.py path/to/credentials.json

credentials.json is downloaded from Google Cloud Console -> APIs &
Services -> Credentials -> OAuth 2.0 Client IDs -> "Desktop application"
type. The file contains `installed.client_id` + `installed.client_secret`.

This script runs InstalledAppFlow.run_local_server(), which pops a
browser to Google's consent screen, captures the authorization code,
exchanges it for access + refresh tokens, and prints all three values
you need for .env.

Do NOT commit credentials.json (already excluded via .gitignore if
the codebase has one; add it to .gitignore if not).

Spec: docs/superpowers/specs/2026-04-24-track-a1-gmail-ingress-design.md
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

from backend.gmail.scopes import A2_SCOPES


def main(credentials_path: Path) -> int:
    if not credentials_path.is_file():
        print(f"error: credentials file not found: {credentials_path}", file=sys.stderr)
        return 2

    # Use A2_SCOPES (gmail.modify + gmail.send) so the same refresh
    # token works for both ingress polling and egress sends.
    flow = InstalledAppFlow.from_client_secrets_file(
        str(credentials_path), scopes=A2_SCOPES
    )
    creds = flow.run_local_server(port=0)

    data = json.loads(credentials_path.read_text())
    installed = data.get("installed", data.get("web", {}))

    print()
    print("=" * 72)
    print("OAuth setup complete. Paste these three lines into .env:")
    print("=" * 72)
    print(f"GMAIL_CLIENT_ID={installed.get('client_id', '<see credentials.json>')}")
    print(f"GMAIL_CLIENT_SECRET={installed.get('client_secret', '<see credentials.json>')}")
    print(f"GMAIL_REFRESH_TOKEN={creds.refresh_token}")
    print("=" * 72)
    print()
    print("Then run: uv run python scripts/gmail_poll.py")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: gmail_auth_init.py path/to/credentials.json", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(Path(sys.argv[1])))
