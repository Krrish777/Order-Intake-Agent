"""Batch-parse every file in one data/<subfolder>/ against the LlamaExtract tool.

For each input file the script:
  * calls ``parse_document`` to get a ParsedDocument,
  * pretty-prints the full parsed JSON to stdout,
  * if a sibling ``<name>.expected.json`` exists, prints a unified diff
    (expected vs actual) so mismatches are eyeballable in the terminal,
  * records the per-file outcome.

All per-file results are aggregated into a single ``runs/<timestamp>/results.json``.
Errors are recorded per file; the run continues, then exits non-zero if any
file failed to parse OR any diff was non-empty.

Usage:
    uv run python scripts/parse_data.py pdf
    uv run python scripts/parse_data.py excel --timeout 180
    uv run python scripts/parse_data.py email --out custom/out/dir

Requires LLAMA_CLOUD_API_KEY in the environment.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import json
import os
import sys
from pathlib import Path
from typing import Any

# Make 'backend' importable when running this script directly.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from backend.tools.document_parser import ParseError, parse_document  # noqa: E402
from backend.utils.logging import get_logger  # noqa: E402

_log = get_logger(__name__)

_VALID_SUBFOLDERS = ("pdf", "excel", "csv", "email", "edi")
_SIDECAR_SUFFIX = ".expected.json"
_SKIP_FILENAMES = {"README.md"}


# ---------------------------------------------------------------------------
# TODO(user): implement the canonical form used by the diff.
# ---------------------------------------------------------------------------
# Both the parsed dict and the expected dict are passed through this function
# BEFORE being serialized to JSON and fed to difflib. What you do here decides
# what counts as a "real" difference vs. noise.
#
# Trade-offs to weigh:
#   - sort_keys happens in json.dumps already, so dict-key order is a free win.
#   - Sorting list items (e.g. sub_documents, line_items) removes ordering
#     false-positives BUT hides genuine ordering bugs. POs often have a
#     meaningful line-number order — think twice before sorting line_items.
#   - Rounding floats (quantity, unit_price) kills LLM float wobble but may
#     mask off-by-0.01 pricing bugs you actually want to catch.
#   - Dropping Optional fields whose value is None/"" tolerates parsed
#     outputs that omit-vs-null-vs-empty-string for missing data.
#   - Lower-casing / stripping strings tolerates whitespace + casing drift in
#     free-text fields (customer_name, ship_to_address).
#
# Keep it to ~5-10 lines. Default below is "strictest" — identity passthrough.
def _canonicalize(obj: Any) -> Any:
    """Return a normalized version of ``obj`` for diffing. EDIT ME."""
    return obj


def _to_diff_text(obj: Any) -> list[str]:
    """Serialize to a list of newline-terminated lines for difflib."""
    text = json.dumps(_canonicalize(obj), indent=2, sort_keys=True, default=str)
    return [line + "\n" for line in text.splitlines()]


def _unified_diff(expected: Any, actual: Any, *, filename: str) -> str:
    """Return a git-style unified diff, or '' if the two are identical after canonicalization."""
    diff_lines = list(
        difflib.unified_diff(
            _to_diff_text(expected),
            _to_diff_text(actual),
            fromfile=f"expected/{filename}",
            tofile=f"actual/{filename}",
            n=3,
        )
    )
    return "".join(diff_lines)


def _discover_inputs(subdir: Path) -> list[Path]:
    """Return sorted list of parseable files in ``subdir`` (excludes sidecars + README)."""
    candidates: list[Path] = []
    for p in sorted(subdir.iterdir()):
        if not p.is_file():
            continue
        if p.name in _SKIP_FILENAMES:
            continue
        if p.name.endswith(_SIDECAR_SUFFIX):
            continue
        candidates.append(p)
    return candidates


def _expected_sidecar(input_path: Path) -> Path:
    """data/pdf/foo.pdf  ->  data/pdf/foo.expected.json"""
    return input_path.with_suffix("").with_suffix(".expected.json") \
        if input_path.suffix else input_path.with_name(input_path.name + _SIDECAR_SUFFIX)


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="parse_data.py",
        description=(
            "Run parse_document against every file in data/<subfolder>/, "
            "diff against .expected.json sidecars, and write an aggregated results.json."
        ),
    )
    p.add_argument(
        "subfolder",
        choices=_VALID_SUBFOLDERS,
        help="Which data/ subfolder to process (one of: pdf, excel, csv, email, edi).",
    )
    p.add_argument(
        "--data-root",
        type=Path,
        default=_PROJECT_ROOT / "data",
        help="Root dir containing the subfolders (default: <repo>/data).",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output dir (default: <repo>/runs/<timestamp>/).",
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
        help="Seconds between LlamaExtract status polls (default 2.0).",
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
    """Parse one file; return an aggregated result dict (success or error)."""
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
        result = parse_document(
            content=content,
            filename=path.name,
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
        )
    except ParseError as exc:
        _log.error(
            "parse_data_item_failed",
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
        print(f"\n  ParseError: {type(exc).__name__}: {exc}", file=sys.stderr)
        return entry

    parsed = result.model_dump()
    entry["status"] = "parsed"
    entry["classification"] = result.classification
    entry["sub_document_count"] = len(result.sub_documents)
    entry["parsed"] = parsed

    print(json.dumps(parsed, indent=2, sort_keys=True, default=str))

    sidecar = _expected_sidecar(path)
    if sidecar.exists():
        try:
            expected = json.loads(sidecar.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            entry["diff_status"] = "expected_unreadable"
            entry["diff_error"] = str(exc)
            print(f"\n  warning: could not parse {sidecar.name}: {exc}", file=sys.stderr)
            return entry

        diff = _unified_diff(expected, parsed, filename=path.name)
        if not diff:
            entry["diff_status"] = "match"
            print(f"\n  diff vs {sidecar.name}: ✓ match", flush=True)
        else:
            entry["diff_status"] = "mismatch"
            entry["diff"] = diff
            print(f"\n  diff vs {sidecar.name}: ✗ mismatch", flush=True)
            print(diff, flush=True)
    else:
        entry["diff_status"] = "no_sidecar"
        print(f"\n  (no {sidecar.name} found — skipping diff)", flush=True)

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

    subdir = args.data_root / args.subfolder
    if not subdir.is_dir():
        print(f"error: {subdir} is not a directory", file=sys.stderr)
        return 2

    inputs = _discover_inputs(subdir)
    if not inputs:
        print(f"error: no parseable files in {subdir}", file=sys.stderr)
        return 2

    timestamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir: Path = args.out or (_PROJECT_ROOT / "runs" / timestamp)
    out_dir.mkdir(parents=True, exist_ok=True)

    _log.info(
        "parse_data_start",
        subfolder=args.subfolder,
        file_count=len(inputs),
        out_dir=str(out_dir),
        timeout_s=args.timeout,
    )
    print(
        f"parsing {len(inputs)} file(s) from {subdir} → {out_dir}",
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
        "subfolder": args.subfolder,
        "data_root": str(args.data_root),
        "run_timestamp": timestamp,
        "file_count": len(entries),
        "results": entries,
    }
    results_path = out_dir / "results.json"
    results_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )

    parsed_ok = sum(1 for e in entries if e.get("status") == "parsed")
    errors = sum(1 for e in entries if e.get("status") == "error")
    matched = sum(1 for e in entries if e.get("diff_status") == "match")
    mismatched = sum(1 for e in entries if e.get("diff_status") == "mismatch")
    no_sidecar = sum(1 for e in entries if e.get("diff_status") == "no_sidecar")

    print("\n" + "=" * 72, file=sys.stderr)
    print("SUMMARY", file=sys.stderr)
    print("=" * 72, file=sys.stderr)
    print(f"  files processed : {len(entries)}", file=sys.stderr)
    print(f"  parsed ok       : {parsed_ok}", file=sys.stderr)
    print(f"  parse errors    : {errors}", file=sys.stderr)
    print(f"  diff match      : {matched}", file=sys.stderr)
    print(f"  diff mismatch   : {mismatched}", file=sys.stderr)
    print(f"  no sidecar      : {no_sidecar}", file=sys.stderr)
    print(f"  results written : {results_path}", file=sys.stderr)

    _log.info(
        "parse_data_complete",
        subfolder=args.subfolder,
        parsed_ok=parsed_ok,
        errors=errors,
        matched=matched,
        mismatched=mismatched,
        no_sidecar=no_sidecar,
        results_path=str(results_path),
    )

    return 0 if (errors == 0 and mismatched == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
