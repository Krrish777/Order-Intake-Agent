"""CLI: parse a local ``.eml`` file (or a directory of them) into envelopes.

Stand-in for Gmail push notifications during sprint 1. Track A wires the
agent to ``parse_eml`` directly; this script is the human-driven path —
inspect the envelope shape, smoke-test new fixtures, debug parsing issues.

Usage:
    uv run python scripts/inject_email.py path/to/message.eml
    uv run python scripts/inject_email.py path/to/message.eml --compact
    uv run python scripts/inject_email.py data/email/        # walks dir, emits JSONL
    uv run python scripts/inject_email.py path/to/message.eml -v
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Force UTF-8 on Windows consoles so non-ASCII headers / body text don't crash
# mid-print under cp1252.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from backend.ingestion import EmlParseError, parse_eml  # noqa: E402
from backend.utils.logging import get_logger  # noqa: E402

_log = get_logger(__name__)


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="inject_email.py",
        description="Parse a local .eml file (or directory) into EmailEnvelope JSON.",
    )
    p.add_argument(
        "path",
        type=Path,
        help="Path to a .eml file, or a directory to walk for *.eml files.",
    )
    p.add_argument(
        "--compact",
        action="store_true",
        help="Single-line JSON output (default: indented). Always on when path is a directory.",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable DEBUG-level logging.",
    )
    return p


def _emit(path: Path, *, compact: bool) -> int:
    """Parse one ``.eml`` and print its envelope JSON. Return process exit code."""
    try:
        envelope = parse_eml(path)
    except EmlParseError as exc:
        _log.error("eml_parse_failed", path=str(path), exc_info=True)
        print(f"EmlParseError: {exc}", file=sys.stderr)
        return 1

    indent = None if compact else 2
    print(envelope.model_dump_json(indent=indent))

    _log.info(
        "eml_parsed",
        path=str(path),
        message_id=envelope.message_id,
        attachment_count=len(envelope.attachments),
        body_chars=len(envelope.body_text),
        in_reply_to=envelope.in_reply_to,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    if args.verbose:
        os.environ["LOG_LEVEL"] = "DEBUG"

    if not args.path.exists():
        _log.error("path_not_found", path=str(args.path))
        print(f"error: path not found: {args.path}", file=sys.stderr)
        return 2

    if args.path.is_dir():
        eml_paths = sorted(args.path.rglob("*.eml"))
        if not eml_paths:
            print(f"error: no .eml files under {args.path}", file=sys.stderr)
            return 2
        print(
            f"→ walking {args.path} ({len(eml_paths)} .eml files), emitting JSONL",
            file=sys.stderr,
        )
        worst = 0
        for p in eml_paths:
            rc = _emit(p, compact=True)
            worst = max(worst, rc)
        return worst

    print(
        f"→ parsing {args.path.name} ({args.path.stat().st_size:,} bytes)",
        file=sys.stderr,
    )
    return _emit(args.path, compact=args.compact)


if __name__ == "__main__":
    sys.exit(main())
