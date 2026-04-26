"""Backfill ``inbound_email`` into the 3 wireframe-v2 capture JSONs.

The capture script (``scripts/capture_run.py``) was extended to embed the
inbound .eml at capture time, but the 3 JSONs already on disk were captured
before that change. This one-shot enriches them in place by parsing the
canonical .eml fixture for each run.

Inlined parser (does not import capture_run) so this works without the
google-cloud-firestore dependency. Idempotent: re-running refreshes the field.
"""

from __future__ import annotations

import email
import json
import sys
from email import policy
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent

_EMAIL_HEADERS = ("From", "To", "Subject", "Date", "Message-ID")


def parse_inbound_email(eml_path: Path) -> dict[str, Any]:
    with eml_path.open("rb") as fh:
        msg = email.message_from_binary_file(fh, policy=policy.default)
    headers = {h: msg.get(h, "") for h in _EMAIL_HEADERS}
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                body = part.get_content()
                break
    else:
        body = msg.get_content() if msg.get_content_type() == "text/plain" else ""
    return {"headers": headers, "body": body.strip()}


_BACKFILLS = [
    {
        "json": _REPO_ROOT / "design/wireframes-v2/data/A-001-patterson.json",
        "eml":  _REPO_ROOT / "data/pdf/patterson_po-28491.wrapper.eml",
    },
    {
        "json": _REPO_ROOT / "design/wireframes-v2/data/A-002-mm-machine.json",
        "eml":  _REPO_ROOT / "data/email/mm_machine_reorder_2026-04-24.eml",
    },
    {
        "json": _REPO_ROOT / "design/wireframes-v2/data/A-003-birch-valley.json",
        "eml":  _REPO_ROOT / "data/email/birch_valley_clarify_reply.eml",
    },
]


def main() -> int:
    for entry in _BACKFILLS:
        json_path: Path = entry["json"]
        eml_path: Path = entry["eml"]
        if not json_path.exists():
            print(f"skip: {json_path} not found", file=sys.stderr)
            continue
        if not eml_path.exists():
            print(f"skip: {eml_path} not found", file=sys.stderr)
            continue
        data = json.loads(json_path.read_text(encoding="utf-8"))
        data["inbound_email"] = parse_inbound_email(eml_path)
        json_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        body_chars = len(data["inbound_email"]["body"])
        print(f"patched {json_path.name} (body={body_chars} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
