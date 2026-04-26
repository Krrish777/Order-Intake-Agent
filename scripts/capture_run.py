"""Capture a real pipeline run's audit_log + persisted records into JSON.

Reads ``audit_log`` Firestore entries for the given ``correlation_id``,
groups ``entered``/``exited`` pairs into per-stage timings, and bundles
the captured run with the persisted ``orders`` / ``exceptions`` rows that
were written during the run.

Usage:
    FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 \\
    uv run python scripts/capture_run.py <correlation_id> [--out path.json]

Designed to drive the wireframes at design/wireframes-v2/runs/. The output
JSON is a single-source-of-truth for what really happened on a run, so the
sheet template can render against it instead of synthetic placeholders.
"""

from __future__ import annotations

import argparse
import email
import json
import os
import sys
from datetime import datetime, timezone
from email import policy
from pathlib import Path
from typing import Any

from google.cloud.firestore import Client
from google.cloud.firestore_v1.base_query import FieldFilter

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _to_jsonable(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.astimezone(timezone.utc).isoformat()
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    return str(obj)


def _stream_collection(
    client: Client, collection: str, *filters: FieldFilter
) -> list[dict[str, Any]]:
    q = client.collection(collection)
    for f in filters:
        q = q.where(filter=f)
    rows = []
    for doc in q.stream():
        d = doc.to_dict() or {}
        d["__doc_id"] = doc.id
        rows.append(d)
    return rows


def _ms_between(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() * 1000


_EMAIL_HEADERS = ("From", "To", "Subject", "Date", "Message-ID")


def parse_inbound_email(eml_path: Path) -> dict[str, Any]:
    """Parse an .eml fixture into ``{ headers, body }`` for the wireframe §I.

    Returns the canonical headers (From/To/Subject/Date/Message-ID) and the
    text/plain body. Multipart messages with PDF attachments (e.g. Patterson)
    return only the text/plain part; attachments are intentionally dropped —
    the wireframes render the attachment via separate fields, not the raw
    base64.
    """
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


def capture(correlation_id: str, eml_path: Path | None = None) -> dict[str, Any]:
    project = os.environ.get(
        "GOOGLE_CLOUD_PROJECT", "demo-order-intake-local"
    )
    client = Client(project=project)

    raw = _stream_collection(
        client,
        "audit_log",
        FieldFilter("correlation_id", "==", correlation_id),
    )
    if not raw:
        raise SystemExit(
            f"no audit_log entries for correlation_id={correlation_id}"
        )
    raw.sort(key=lambda e: e.get("ts") or datetime.min.replace(tzinfo=timezone.utc))

    source_message_id = next(
        (e.get("source_message_id") for e in raw if e.get("source_message_id")),
        None,
    )
    agent_version = raw[0].get("agent_version")
    session_id = raw[0].get("session_id")

    open_stages: dict[str, tuple[datetime, dict[str, Any]]] = {}
    stages: list[dict[str, Any]] = []
    lifecycle: list[dict[str, Any]] = []

    for e in raw:
        stage = e.get("stage")
        phase = e.get("phase")
        ts = e.get("ts")
        if phase == "entered":
            open_stages[stage] = (ts, e)
        elif phase == "exited":
            opened = open_stages.pop(stage, None)
            entered_ts = opened[0] if opened else None
            stages.append(
                {
                    "stage": stage,
                    "action": e.get("action"),
                    "outcome": e.get("outcome"),
                    "entered_ts": entered_ts.isoformat() if entered_ts else None,
                    "exited_ts": ts.isoformat() if ts else None,
                    "duration_ms": (
                        round(_ms_between(entered_ts, ts), 1)
                        if entered_ts and ts
                        else None
                    ),
                    "payload": e.get("payload", {}),
                }
            )
        elif phase == "lifecycle":
            lifecycle.append(
                {
                    "stage": stage,
                    "action": e.get("action"),
                    "outcome": e.get("outcome"),
                    "ts": ts.isoformat() if ts else None,
                    "payload": e.get("payload", {}),
                }
            )

    orders, exceptions = [], []
    if source_message_id:
        orders = _stream_collection(
            client,
            "orders",
            FieldFilter("source_message_id", "==", source_message_id),
        )
        exceptions = _stream_collection(
            client,
            "exceptions",
            FieldFilter("source_message_id", "==", source_message_id),
        )

    if stages:
        first_in = min(
            (s["entered_ts"] for s in stages if s["entered_ts"]),
            default=None,
        )
        last_out = max(
            (s["exited_ts"] for s in stages if s["exited_ts"]),
            default=None,
        )
        total_s = (
            round(
                (
                    datetime.fromisoformat(last_out)
                    - datetime.fromisoformat(first_in)
                ).total_seconds(),
                1,
            )
            if first_in and last_out
            else None
        )
    else:
        total_s = None

    inbound_email = parse_inbound_email(eml_path) if eml_path else None

    return {
        "correlation_id": correlation_id,
        "source_message_id": source_message_id,
        "session_id": session_id,
        "agent_version": agent_version,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "total_wall_clock_seconds": total_s,
        "stage_count": len(stages),
        "stages": stages,
        "lifecycle_events": lifecycle,
        "orders": orders,
        "exceptions": exceptions,
        "inbound_email": inbound_email,
        "raw_audit_event_count": len(raw),
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("correlation_id", help="run's correlation_id (uuid hex)")
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="write JSON to this path (default: stdout)",
    )
    p.add_argument(
        "--eml-path",
        type=Path,
        default=None,
        help="path to the inbound .eml fixture; embeds it as inbound_email",
    )
    args = p.parse_args()

    if not os.environ.get("FIRESTORE_EMULATOR_HOST"):
        os.environ["FIRESTORE_EMULATOR_HOST"] = "127.0.0.1:8080"

    result = capture(args.correlation_id, eml_path=args.eml_path)
    serialized = json.dumps(result, indent=2, default=_to_jsonable)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(serialized, encoding="utf-8")
        print(f"wrote {args.out} ({len(serialized)} bytes)", file=sys.stderr)
    else:
        print(serialized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
