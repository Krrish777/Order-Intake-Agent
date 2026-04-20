"""CLI: scaffold a wrapper ``.eml`` fixture from parts.

Two modes, same assembly machinery:

1. **Wrap a bare document** (``--doc``) — embed a PDF/CSV/XLSX/EDI as an
   attachment and wrap it in an email envelope.
2. **Author a reply** (``--in-reply-to``, no ``--doc``) — emit an attachment-less
   email whose headers thread into a prior message.

Body prose is your job. Either pass ``--body-file path.txt`` or let the
script write a placeholder that you hand-edit afterwards.

Usage (wrap):
    uv run python scripts/scaffold_wrapper_eml.py \\
        --doc data/pdf/patterson_po-28491.pdf \\
        --from "Pat Patterson <orders@patterson-mfg.example>" \\
        --to "orders@grafton-reese.com" \\
        --subject "PO 28491 attached" \\
        --body-file data/pdf/patterson_po-28491.body.txt \\
        --out data/pdf/patterson_po-28491.wrapper.eml

Usage (reply):
    uv run python scripts/scaffold_wrapper_eml.py \\
        --from "Stan Birchwood <stan@birchvalley.example>" \\
        --to "orders@grafton-reese.com" \\
        --subject "Re: Need by tomorrow - Hirshey planter" \\
        --in-reply-to "<orig-id@birchvalley.example>" \\
        --references "<orig-id@birchvalley.example>" \\
        --body-file data/email/birch_valley_clarify_reply.body.txt \\
        --out data/email/birch_valley_clarify_reply.eml
"""

from __future__ import annotations

import argparse
import mimetypes
import sys
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")


_PLACEHOLDER_BODY = (
    "[REPLACE THIS BODY WITH HAND-WRITTEN PROSE BEFORE COMMITTING.]\n"
    "\n"
    "A real customer wouldn't say 'Please find attached' — write the way the\n"
    "persona implied by the From address actually writes. Keep it 3-6 sentences.\n"
)


def _guess_mime(doc_path: Path) -> tuple[str, str]:
    """Return ``(maintype, subtype)`` for the document.

    Stdlib ``mimetypes`` covers the common cases (PDF, CSV, XLSX). EDI is not
    in the database and falls back to ``application/edi-x12``.
    """
    if doc_path.suffix.lower() == ".edi":
        return "application", "edi-x12"
    guessed, _ = mimetypes.guess_type(doc_path.name)
    if guessed is None:
        return "application", "octet-stream"
    maintype, _, subtype = guessed.partition("/")
    return maintype, subtype


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scaffold_wrapper_eml.py",
        description="Scaffold a wrapper .eml that embeds a bare document as an attachment.",
    )
    p.add_argument(
        "--doc", type=Path, default=None,
        help="Path to the source document to embed as an attachment. "
             "Omit for attachment-less emails (e.g. reply fixtures).",
    )
    p.add_argument("--from", dest="from_addr", required=True, help="From header value.")
    p.add_argument("--to", dest="to_addr", required=True, help="To header value.")
    p.add_argument("--subject", required=True, help="Subject header value.")
    p.add_argument(
        "--in-reply-to", dest="in_reply_to", default=None,
        help="Message-ID this email is a reply to (e.g. <abc@example.com>).",
    )
    p.add_argument(
        "--references", dest="references", default=None,
        help="References header value (space-separated message IDs). "
             "Defaults to --in-reply-to when that is set.",
    )
    p.add_argument(
        "--out", type=Path, required=True, help="Destination path for the wrapper .eml.",
    )
    p.add_argument(
        "--domain",
        default="grafton-reese.example",
        help="Domain used in the generated Message-ID (default: grafton-reese.example).",
    )
    p.add_argument(
        "--date",
        default=None,
        help="ISO-8601 datetime for the Date header (default: now, UTC).",
    )
    p.add_argument(
        "--body-file",
        type=Path,
        default=None,
        help="Path to a UTF-8 text file containing the email body. "
             "If omitted, a placeholder is written (hand-edit the .eml afterwards).",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite --out if it already exists.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    if args.doc is not None and not args.doc.exists():
        print(f"error: doc not found: {args.doc}", file=sys.stderr)
        return 2
    if args.out.exists() and not args.force:
        print(f"error: {args.out} exists (pass --force to overwrite)", file=sys.stderr)
        return 2

    received_at = (
        datetime.fromisoformat(args.date)
        if args.date
        else datetime.now(timezone.utc)
    )

    msg = EmailMessage()
    msg["From"] = args.from_addr
    msg["To"] = args.to_addr
    msg["Subject"] = args.subject
    msg["Date"] = format_datetime(received_at)
    msg["Message-ID"] = make_msgid(domain=args.domain)
    if args.in_reply_to is not None:
        msg["In-Reply-To"] = args.in_reply_to
        msg["References"] = args.references or args.in_reply_to

    if args.body_file is not None:
        if not args.body_file.exists():
            print(f"error: body file not found: {args.body_file}", file=sys.stderr)
            return 2
        body = args.body_file.read_text(encoding="utf-8")
    else:
        body = _PLACEHOLDER_BODY
    msg.set_content(body)

    if args.doc is not None:
        maintype, subtype = _guess_mime(args.doc)
        msg.add_attachment(
            args.doc.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            filename=args.doc.name,
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(bytes(msg))

    print(f"wrote {args.out}", file=sys.stderr)
    if args.body_file is None:
        print(
            "next: open the file, replace the placeholder body with hand-written prose, "
            f"then verify with: uv run python scripts/inject_email.py {args.out}",
            file=sys.stderr,
        )
    else:
        print(
            f"verify with: uv run python scripts/inject_email.py {args.out}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
