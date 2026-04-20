"""CLI smoke test for the document_parser tool.

Takes a file path, runs parse_document, pretty-prints the ParsedDocument JSON
to the terminal. Use to ad-hoc test the tool against any local file.

Usage:
    uv run python scripts/parse_file.py path/to/file.pdf
    uv run python scripts/parse_file.py path/to/email.txt --hint "Acme uses 'PN' for SKU"
    uv run python scripts/parse_file.py path/to/file.pdf --timeout 120

Requires LLAMA_CLOUD_API_KEY in the environment.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# Make sibling 'backend' package importable when running this script directly.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from backend.tools.document_parser import (  # noqa: E402
    ParseError,
    parse_document,
)


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="parse_file.py",
        description="Run the document_parser tool against a local file and print the result.",
    )
    p.add_argument("file", type=Path, help="Path to the file to parse (PDF, XLSX, CSV, image, txt, ...)")
    p.add_argument(
        "--hint",
        default=None,
        help="Optional extra_hint appended to the system prompt "
             "(e.g. \"This customer uses 'PN' for SKU\")",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Wall-clock timeout in seconds for the whole submit + poll cycle (default 120)",
    )
    p.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Seconds between status polls (default 2.0)",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable INFO-level logging from the parser tool",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )

    if not args.file.exists():
        print(f"error: file not found: {args.file}", file=sys.stderr)
        return 2

    if not os.environ.get("LLAMA_CLOUD_API_KEY"):
        print(
            "error: LLAMA_CLOUD_API_KEY is not set in the environment.\n"
            "  export LLAMA_CLOUD_API_KEY=llx-...",
            file=sys.stderr,
        )
        return 2

    content = args.file.read_bytes()
    print(
        f"→ parsing {args.file.name} ({len(content):,} bytes), "
        f"timeout={args.timeout}s, hint={'yes' if args.hint else 'no'}",
        file=sys.stderr,
    )

    try:
        result = parse_document(
            content=content,
            filename=args.file.name,
            extra_hint=args.hint,
            timeout_s=args.timeout,
            poll_interval_s=args.poll_interval,
        )
    except ParseError as exc:
        print(f"\nParseError: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result.model_dump(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
