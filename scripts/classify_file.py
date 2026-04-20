"""CLI smoke test for the document_classifier tool.

Takes a file path, runs ``classify_document``, pretty-prints the
:class:`ClassifiedDocument` JSON to stdout. Use to ad-hoc test the tool
against any local file.

Usage:
    uv run python scripts/classify_file.py path/to/file.pdf
    uv run python scripts/classify_file.py path/to/email.txt --timeout 120
    uv run python scripts/classify_file.py path/to/order.xlsx -v

Requires ``LLAMA_CLOUD_API_KEY`` in the environment.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Windows consoles default to cp1252, which can't encode arrows ("\u2192") or
# most non-ASCII characters. Force UTF-8 on stdout/stderr so piped / background
# runs don't crash mid-print.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

# Make the project root importable when running this script directly.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from backend.tools.document_classifier import (  # noqa: E402
    ClassifyError,
    classify_document,
)
from backend.utils.logging import get_logger  # noqa: E402

_log = get_logger(__name__)


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="classify_file.py",
        description="Run the document_classifier tool against a local file and print the result.",
    )
    p.add_argument(
        "file",
        type=Path,
        help="Path to the file to classify (PDF, XLSX, CSV, XML, EDI, EML, TXT, image, ...).",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Wall-clock timeout in seconds for the whole submit + poll cycle (default 120).",
    )
    p.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Seconds between status polls (default 2.0).",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable DEBUG-level logging (sets LOG_LEVEL=DEBUG for this run).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    # Route --verbose through the shared logging config. Must set the env var
    # BEFORE the first get_logger call that triggers _configure_once.
    if args.verbose:
        os.environ["LOG_LEVEL"] = "DEBUG"

    _log.info(
        "classify_file_cli_start",
        file=str(args.file),
        timeout_s=args.timeout,
    )

    if not args.file.exists():
        _log.error("file_not_found", file=str(args.file))
        print(f"error: file not found: {args.file}", file=sys.stderr)
        return 2

    if not os.environ.get("LLAMA_CLOUD_API_KEY"):
        _log.error("missing_api_key", var="LLAMA_CLOUD_API_KEY")
        print(
            "error: LLAMA_CLOUD_API_KEY is not set in the environment.\n"
            "  export LLAMA_CLOUD_API_KEY=llx-...",
            file=sys.stderr,
        )
        return 2

    content = args.file.read_bytes()
    _log.info("file_read", file=args.file.name, bytes=len(content))
    print(
        f"→ classifying {args.file.name} ({len(content):,} bytes), "
        f"timeout={args.timeout}s",
        file=sys.stderr,
    )

    try:
        result = classify_document(
            content=content,
            filename=args.file.name,
            timeout_s=args.timeout,
            poll_interval_s=args.poll_interval,
        )
    except ClassifyError as exc:
        _log.error(
            "classify_document_raised",
            file=args.file.name,
            exc_type=type(exc).__name__,
            exc_info=True,
        )
        print(f"\nClassifyError: {exc}", file=sys.stderr)
        return 1

    _log.info(
        "classify_file_cli_complete",
        file=args.file.name,
        document_intent=result.document_intent,
        intent_confidence=result.intent_confidence,
        document_format=result.document_format,
    )
    print(json.dumps(result.model_dump(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
