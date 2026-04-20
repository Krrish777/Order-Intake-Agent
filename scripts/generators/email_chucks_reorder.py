"""Generate Chuck's Hydraulic Shop reorder email — Phase 5.1 exemplar.

Chuck Marietta runs a one-man hose-repair shop in Akron. Per customer notes,
"orders arrive as short plain-text emails, often on mobile". This file is the
format exemplar for Phase 5 (body-only .eml) and it tests two edge cases at
once:

1.  **Items by vague description** — "the 3/8 R2 hose", "those JIC -6 elbows",
    "the usual 3/8 QD". No SKU in sight; agent must match against the product
    master by dash sizes and common trade names.
2.  **"Same as last time" phrasing** — references a prior order without
    enumerating what it contained. Agent must flag or retrieve prior-order
    context (this is the 4th mixed-email pattern consolidated into one file
    since Phase 6 was dropped from scope).

Realism anchors:

1.  **Mobile compose shape.** Very short lines, bottom-posted, no greeting
    other than "Hey". Minimal punctuation. No signature block beyond
    "— Chuck" and the phone number.
2.  **Akron timezone** — Friday 2026-04-17 15:47 EDT (`-0400` since US DST
    is active in April).
3.  **From address** matches `contact.email` in `customers.json`
    (`chuck@chuckshyd.com`).
4.  **To address** is Grafton-Reese's canonical `order_email`
    (`orders@grafton-reese.com`).
5.  **Message-ID** is a realistic UUID-style token at the sending domain;
    a downstream mailbox would dedupe on this.
6.  **Subject** is lowercase and terse — "reorder" — consistent with a
    shop owner thumbing a message on his phone while closing up for the week.
7.  **RFC 5322-compliant** — valid headers, MIME-Version, Content-Type text/plain
    utf-8, CRLF line endings per SMTP policy. Python's email parser
    round-trips it without errors (verified).

Run with: ``uv run python -m scripts.generators.email_chucks_reorder``
"""

from __future__ import annotations

from email.message import EmailMessage
from email.policy import SMTP
from email.parser import BytesParser
from pathlib import Path


FROM_ADDR = "Chuck Marietta <chuck@chuckshyd.com>"
TO_ADDR = "orders@grafton-reese.com"
SUBJECT = "reorder"
DATE = "Fri, 17 Apr 2026 15:47:00 -0400"
MESSAGE_ID = "<7C4D8E2F-A193-42B6-B1F3-9A82E1FD6A24@chuckshyd.com>"

BODY = """Hey,

Need to reorder:

- 50 ft of the 3/8 R2 hose
- 6 of those JIC -6 elbows
- 2 of the usual 3/8 QD couplers (ISO A style)

Same as last time on price. Usual Net 15.

Thanks
— Chuck
(330) 724-8851
"""


def build_message() -> EmailMessage:
    msg = EmailMessage(policy=SMTP)
    msg["From"] = FROM_ADDR
    msg["To"] = TO_ADDR
    msg["Subject"] = SUBJECT
    msg["Date"] = DATE
    msg["Message-ID"] = MESSAGE_ID
    msg.set_content(BODY)
    return msg


def verify(raw_bytes: bytes) -> None:
    """Round-trip the bytes through BytesParser to catch malformed headers."""
    parsed = BytesParser(policy=SMTP).parsebytes(raw_bytes)
    assert parsed["From"] == FROM_ADDR
    assert parsed["To"] == TO_ADDR
    assert parsed["Subject"] == SUBJECT
    assert parsed["Date"] == DATE
    assert parsed["Message-ID"] == MESSAGE_ID
    body = parsed.get_content()
    # Body assertions — every vague reference must survive the round-trip
    for token in ["3/8 R2 hose", "JIC -6 elbows", "3/8 QD couplers",
                  "Same as last time", "Net 15", "— Chuck"]:
        assert token in body, f"missing token: {token!r}"
    # CRLF discipline
    assert b"\r\n" in raw_bytes, "headers must use CRLF per RFC 5322"


def main() -> Path:
    out = Path(__file__).resolve().parents[2] / "data" / "email" / "chucks_hyd_reorder_2026-04-17.eml"
    out.parent.mkdir(parents=True, exist_ok=True)
    msg = build_message()
    raw = msg.as_bytes()
    verify(raw)
    out.write_bytes(raw)
    return out


if __name__ == "__main__":
    path = main()
    print(f"wrote: {path}")
