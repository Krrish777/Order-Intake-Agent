"""Generate Reese & Company's semi-formal reorder email — Phase 5.2.

Tom Reese runs a fourth-generation family supply house in Akron. His emails
read semi-formal with a chatty regional-chain layer on top: a sentence of
project context (who the parts are for, what job they're running), an
enumerated item list, a target date, and a polite sign-off with his title.
He types quickly and makes a handful of common typos that spellcheck misses —
apostrophe drops ("Ive", "dont") and classic misspellings ("seperate",
"recieve"). None of these are show-stoppers for extraction, but they mean
you can't blindly spell-match SKU descriptions to product-master strings.

Realism anchors:

1.  **Five items by description, no SKUs** — Reese has no `sku_aliases` in
    the customer master. Each description has enough specificity (thread,
    length, material, finish) to uniquely resolve against `products.json`.
2.  **Project context** — the "Sykora Metalworks rebuild" detail is the
    regional-chain tell. Large distributors never include why they're
    buying; family shops do.
3.  **Requested: 5/12/26** — the date phrasing from handoff §6 row 5.2.
4.  **Split-shipment preference** — "Send seperate shipment if any
    backordered" exercises partial-fulfillment routing.
5.  **Invoice routing** — Tom asks invoices to go to
    `office@reesecoindustrial.com`, which maps to Maryanne Kolb (Office
    Manager) per customer master. A downstream flow would pick that up.
6.  **Typos** — intentional. "Ive", "dont", "seperate", "recieve". Four
    typos in a medium-length business email is well within normal human
    drift; more would read as performative.
7.  **Thursday 2026-04-16 10:23 EDT** — business hours, mid-week.
8.  **Net 30** and the full signature block per customer master.

Run with: ``uv run python -m scripts.generators.email_reese_reorder``
"""

from __future__ import annotations

from email.message import EmailMessage
from email.parser import BytesParser
from email.policy import SMTP
from pathlib import Path


FROM_ADDR = "Tom Reese <tom@reesecoindustrial.com>"
TO_ADDR = "orders@grafton-reese.com"
SUBJECT = "Parts reorder - Sykora job next week"
DATE = "Thu, 16 Apr 2026 10:23:00 -0400"
MESSAGE_ID = "<5f9a1e3b-4c2d-48a5-a76b-7312c9d4e5a2@reesecoindustrial.com>"

BODY = """Hi there,

Hope everything is running well up in Twinsburg. Ive got a job starting next week at Sykora Metalworks (rebuilding their press lines) and need the following from you when you can — not emergency, but would like to have it on the shelf before the job kicks off.

1.  200 ea Grade 5 hex cap screws, 1/2-13 x 2", zinc plated
2.  300 ea hex nuts same thread (1/2-13, grade 5 zinc)
3.  500 ea flat washers 1/2" SAE, 18-8 stainless — these are for the pump mounts so needs to be the stainless, not zinc
4.  75 ft of 1/4" R1 hose
5.  12 ea JIC -6 male elbows, 90 deg

Requested: 5/12/26

Send seperate shipment if any of it is backordered, we dont need everything at once as long as we recieve the hose and stainless washers first.

Terms Net 30 as always. Invoice to office@reesecoindustrial.com per usual.

Thanks,
Tom Reese, owner
Reese & Company Industrial
1265 S Main Street, Akron OH 44311
(330) 376-2918
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
    # All 5 items + date + typos must survive
    for token in [
        "Grade 5 hex cap screws, 1/2-13 x 2",
        "hex nuts same thread",
        "flat washers 1/2\" SAE, 18-8 stainless",
        "1/4\" R1 hose",
        "JIC -6 male elbows",
        "Requested: 5/12/26",
        "Tom Reese, owner",
        "Net 30",
        "office@reesecoindustrial.com",
        # Intentional typos preserved
        "Ive ", "seperate", "dont need", "recieve",
    ]:
        assert token in body, f"missing token: {token!r}"
    assert b"\r\n" in raw_bytes


def main() -> Path:
    out = Path(__file__).resolve().parents[2] / "data" / "email" / "reese_reorder_2026-04-16.eml"
    out.parent.mkdir(parents=True, exist_ok=True)
    raw = build_message().as_bytes()
    verify(raw)
    out.write_bytes(raw)
    return out


if __name__ == "__main__":
    path = main()
    print(f"wrote: {path}")
