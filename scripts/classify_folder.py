"""Batch-classify every file in a folder against the LlamaClassify tool.

Unlike ``scripts/parse_data.py`` (which expects the pre-defined
``data/{pdf,excel,csv,email,edi}/`` layout), this script takes an **arbitrary
folder path** and classifies every non-hidden, non-sidecar file inside.

For each input file the script:
  * calls ``classify_document`` to get a :class:`ClassifiedDocument`,
  * pretty-prints the JSON to stdout,
  * records the per-file outcome.

All per-file results are aggregated into a single
``runs/<timestamp>/classify_results.json``. Errors are recorded per file; the
run continues, then exits non-zero if any file failed.

Usage:
    uv run python scripts/classify_folder.py data/pdf/
    uv run python scripts/classify_folder.py data/ --recursive
    uv run python scripts/classify_folder.py data/email/ --timeout 180

Requires ``LLAMA_CLOUD_API_KEY`` in the environment.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from pathlib import Path
from typing import Any

# Windows consoles default to cp1252, which can't encode arrows ("\u2192"),
# bullets, or most non-ASCII characters in intent_reasoning. Force UTF-8 on
# stdout/stderr so `classify_folder.py > out.txt` and background runs work.
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
    detect_format,
)
from backend.utils.logging import get_logger  # noqa: E402

_log = get_logger(__name__)

# Files we never classify even if they live in the target folder.
_SKIP_SUFFIXES = (".expected.json",)
_SKIP_FILENAMES = frozenset({"README.md", "results.json", "classify_results.json"})


def _discover_inputs(folder: Path, *, recursive: bool) -> list[Path]:
    """Return sorted list of files to classify under ``folder``.

    Filters:
      - skips hidden files (leading dot),
      - skips expected-JSON sidecars,
      - skips per-run results files,
      - skips files whose ``detect_format`` is ``"unknown"`` — e.g. master
        data JSON under ``data/masters/``. LlamaClassify's internal parse
        has no text channel for these, so uploading them just burns credits
        to guarantee a failure.
      - follows subdirectories only if ``recursive=True``.
    """
    iterator = folder.rglob("*") if recursive else folder.iterdir()
    candidates: list[Path] = []
    for p in iterator:
        if not p.is_file():
            continue
        if p.name.startswith("."):
            continue
        if p.name in _SKIP_FILENAMES:
            continue
        if any(p.name.endswith(suffix) for suffix in _SKIP_SUFFIXES):
            continue
        if detect_format(p.name) == "unknown":
            _log.debug(
                "classify_folder_skip_unknown_format",
                path=str(p),
            )
            continue
        candidates.append(p)
    return sorted(candidates)


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="classify_folder.py",
        description=(
            "Run classify_document against every file in a folder and write "
            "an aggregated classify_results.json."
        ),
    )
    p.add_argument(
        "folder",
        type=Path,
        help="Folder to classify. Any directory — not restricted to data/<subfolder>/.",
    )
    p.add_argument(
        "--recursive",
        action="store_true",
        help="Recurse into subdirectories (default: only top-level files).",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output directory (default: <repo>/runs/<timestamp>/).",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Per-file wall-clock timeout in seconds (default 120).",
    )
    p.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Seconds between LlamaClassify status polls (default 2.0).",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable DEBUG-level logging (sets LOG_LEVEL=DEBUG for this run).",
    )
    return p


def _process_one(
    path: Path,
    *,
    timeout_s: float,
    poll_interval_s: float,
) -> dict[str, Any]:
    """Classify one file; return an aggregated result dict (success or error)."""
    content = path.read_bytes()
    entry: dict[str, Any] = {
        "filename": path.name,
        "path": str(path),
        "bytes": len(content),
    }

    print(f"\n{'=' * 72}", flush=True)
    print(f"→ {path.name}  ({len(content):,} bytes)", flush=True)
    print("=" * 72, flush=True)

    try:
        result = classify_document(
            content=content,
            filename=path.name,
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
        )
    except ClassifyError as exc:
        _log.error(
            "classify_folder_item_failed",
            file=path.name,
            exc_type=type(exc).__name__,
            exc_info=True,
        )
        entry.update(
            {
                "status": "error",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )
        print(f"\n  ClassifyError: {type(exc).__name__}: {exc}", file=sys.stderr)
        return entry

    classified = result.model_dump()
    entry["status"] = "classified"
    entry["document_intent"] = result.document_intent
    entry["intent_confidence"] = result.intent_confidence
    entry["document_format"] = result.document_format
    entry["classified"] = classified

    print(json.dumps(classified, indent=2, sort_keys=True, default=str))
    return entry


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    if args.verbose:
        os.environ["LOG_LEVEL"] = "DEBUG"

    if not os.environ.get("LLAMA_CLOUD_API_KEY"):
        print(
            "error: LLAMA_CLOUD_API_KEY is not set in the environment.\n"
            "  export LLAMA_CLOUD_API_KEY=llx-...",
            file=sys.stderr,
        )
        return 2

    folder: Path = args.folder
    if not folder.is_dir():
        print(f"error: {folder} is not a directory", file=sys.stderr)
        return 2

    inputs = _discover_inputs(folder, recursive=args.recursive)
    if not inputs:
        print(f"error: no classifiable files in {folder}", file=sys.stderr)
        return 2

    timestamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir: Path = args.out or (_PROJECT_ROOT / "runs" / timestamp)
    out_dir.mkdir(parents=True, exist_ok=True)

    _log.info(
        "classify_folder_start",
        folder=str(folder),
        file_count=len(inputs),
        recursive=args.recursive,
        out_dir=str(out_dir),
        timeout_s=args.timeout,
    )
    print(
        f"classifying {len(inputs)} file(s) from {folder} → {out_dir}",
        file=sys.stderr,
    )

    entries: list[dict[str, Any]] = []
    for path in inputs:
        entries.append(
            _process_one(
                path,
                timeout_s=args.timeout,
                poll_interval_s=args.poll_interval,
            )
        )

    manifest = {
        "folder": str(folder),
        "recursive": args.recursive,
        "run_timestamp": timestamp,
        "file_count": len(entries),
        "results": entries,
    }
    results_path = out_dir / "classify_results.json"
    results_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )

    classified_ok = sum(1 for e in entries if e.get("status") == "classified")
    errors = sum(1 for e in entries if e.get("status") == "error")

    # Per-intent tally for a quick sanity glance.
    intent_counts: dict[str, int] = {}
    for e in entries:
        intent = e.get("document_intent")
        if intent:
            intent_counts[intent] = intent_counts.get(intent, 0) + 1

    print("\n" + "=" * 72, file=sys.stderr)
    print("SUMMARY", file=sys.stderr)
    print("=" * 72, file=sys.stderr)
    print(f"  files processed : {len(entries)}", file=sys.stderr)
    print(f"  classified ok   : {classified_ok}", file=sys.stderr)
    print(f"  errors          : {errors}", file=sys.stderr)
    for intent, count in sorted(intent_counts.items(), key=lambda kv: -kv[1]):
        print(f"    {intent:20s} {count:3d}", file=sys.stderr)
    print(f"  results written : {results_path}", file=sys.stderr)

    _log.info(
        "classify_folder_complete",
        folder=str(folder),
        classified_ok=classified_ok,
        errors=errors,
        results_path=str(results_path),
    )

    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
