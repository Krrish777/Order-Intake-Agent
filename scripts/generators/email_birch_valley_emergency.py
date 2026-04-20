"""Generate Birch Valley's emergency reorder email — Phase 5.3.

Stan Birchwood runs a rural tractor/implement repair shop outside State
College PA. His customer-master notes flag farm-operator vocabulary
("grade 8s", "JIC sixes", "the blue tubing") and seasonal urgency
April-June. This file is the conflict edge case: the buyer asks for
next-day delivery on items whose master-data lead times (2-3 days) make
that impossible. The extractor must surface the conflict rather than
silently promise a date it can't hit.

Realism anchors:

1.  **Farm-operator vocabulary.** "Grade 8s 3/8 x 1 yellow zinc" (not
    "Grade 8 hex cap screws"), "the blue tubing, quarter inch" (not
    "polyurethane pneumatic tubing 1/4 OD, color blue"). Matches the
    master's explicit vocabulary list.
2.  **Lead-time conflict.** Body says "by tomorrow" (2026-04-21) while
    master data lists FST-HCS-038-16-100-G8YZ at 2-day lead time and
    PNM-TBE-PU-025-100FT-BL at 3-day lead time. No same-day shipping
    path from Twinsburg OH to State College PA reaches the next morning.
3.  **Customer-side context.** "Hirshey family breathing down my neck",
    "corn planter torn apart in the shop" — this is the kind of detail
    that signals the urgency is real but the deadline may not be
    negotiable from Grafton-Reese's side.
4.  **COD terms per master.** Stan's credit limit is $3,500 and he's
    COD-only until credit review.
5.  **2 items** per handoff §6 row 5.3.
6.  **No subject line formality** — short, specific.
7.  **Monday 2026-04-20 morning** (today per env) — places the
    "tomorrow" conflict exactly where the lead-time gap bites.
8.  **No signature block formality** — just "Stan / Birch Valley",
    consistent with rural shop culture.

Run with: ``uv run python -m scripts.generators.email_birch_valley_emergency``
"""

from __future__ import annotations

from email.message import EmailMessage
from email.parser import BytesParser
from email.policy import SMTP
from pathlib import Path


FROM_ADDR = "Stan Birchwood <stan.birch@birchvalleyfarmeq.com>"
TO_ADDR = "orders@grafton-reese.com"
SUBJECT = "Need by tomorrow - Hirshey planter"
DATE = "Mon, 20 Apr 2026 09:15:00 -0400"
MESSAGE_ID = "<20260420091512.9A31.stanbirch@birchvalleyfarmeq.com>"

BODY = """Stan Birchwood here at Birch Valley. Need these by tomorrow if you can swing it - corn planter is torn apart in the shop and the Hirshey family is breathing down my neck to get it running before the weekend:

- 100 of the grade 8s, 3/8 x 1 yellow zinc
- 1 roll of the blue tubing, quarter inch

Can do COD as usual when your driver drops. Call me on the mobile if tomorrow wont work, I'll need to know by end of day so I can tell them whats up.

814-234-0177

Stan
Birch Valley
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
    parsed = BytesParser(policy=SMTP).parsebytes(raw_bytes)
    assert parsed["From"] == FROM_ADDR
    assert parsed["To"] == TO_ADDR
    assert parsed["Subject"] == SUBJECT
    body = parsed.get_content()
    for token in [
        "grade 8s, 3/8 x 1 yellow zinc",
        "blue tubing, quarter inch",
        "by tomorrow",
        "corn planter",
        "Hirshey family",
        "COD",
        "814-234-0177",
        "Stan",
        "Birch Valley",
    ]:
        assert token in body, f"missing token: {token!r}"
    assert b"\r\n" in raw_bytes


def main() -> Path:
    out = Path(__file__).resolve().parents[2] / "data" / "email" / "birch_valley_emergency.eml"
    out.parent.mkdir(parents=True, exist_ok=True)
    raw = build_message().as_bytes()
    verify(raw)
    out.write_bytes(raw)
    return out


if __name__ == "__main__":
    path = main()
    print(f"wrote: {path}")
