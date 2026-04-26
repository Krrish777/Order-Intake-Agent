# Order Intake Agent

> **The order desk that never sleeps.**
> If your sales team is a row of humans re-typing PDFs into the ERP, Order Intake Agent is the colleague who reads the email, finds the order, asks the smart question, and writes the sales order — before anyone gets to their desk.

**Built for the Google Solution Challenge 2026.**

<!--
Drop your 30–60s screen recording here once it's cut.
Suggested: ![demo](docs/demo.gif)
-->

---

## What it does

A customer emails an order. Two things happen.

| | Step |
|---|---|
| **1** | The agent reads the email and any attachment — free text, PDF, Excel, XML. |
| **2** | It extracts the line items, matches each one to your product catalog, and validates quantities, prices, and the customer. |
| **3** | If everything checks out, the sales order lands in the system and a confirmation reply goes back. If something is unclear, the agent writes a polite question to the customer. If something is wrong, a human sees a single, specific reason. |

No portal. No "please use our form." No human re-typing a PDF into a screen.

---

## Built for you if

- Your customers send orders by email — and you have given up on convincing them to use a portal.
- Someone on your team spends an hour a day re-keying line items into the ERP.
- You have a product catalog with thousands of SKUs and customer-specific naming, and matching them is more art than lookup.
- You need an audit trail of every decision the system made on every order.
- You want a real human in the loop — but only for the orders that actually need one.

---

## The problem, in one table

| Without Order Intake Agent | With Order Intake Agent |
|---|---|
| Order arrives. Sarah opens the PDF, alt-tabs to the ERP, types 14 line codes, fixes the unit conversion, sends a confirmation. **Twenty-five minutes.** | Order arrives. The agent extracts, matches, validates, and drafts. Sarah skims the draft and clicks send. **Twenty-five seconds.** |
| A typo in the SKU lookup ships the wrong product. Customer is angry. | The matcher hedges when it is unsure and asks the customer instead of guessing. |
| "Where are we on the Patterson order?" → nobody knows. | Every order has an audit trail of every decision the agent made. |
| Hiring more people scales the problem linearly. | The next 1,000 orders cost the same as the last one. |

---

## How it actually works

```
Customer email  ─▶  Gmail  ─▶  Pub/Sub  ─▶  Order Intake Agent  ─▶  Sales order + reply
                                                  │
                                                  └─▶  Audit trail
```

Inside the agent is a sequence of small, single-purpose stages — read, classify, extract, match, validate, ask-if-unsure, persist, confirm, judge, send. Each stage is independently testable and the whole pipeline is one ADK `SequentialAgent`.

The interesting bits:

- **Three-tier SKU matching** — exact, then fuzzy, then semantic (Gemini embeddings, Firestore vector index). The agent knows when it is in the third tier and lowers its confidence accordingly.
- **LLM-as-judge gate on outbound** — every reply is graded by a second model before it is allowed to send. A bad draft is blocked, not sent.
- **Reply short-circuit** — if a customer is replying to a clarifying question, the agent picks up the open exception instead of treating it as a new order.

---

## Built on Google

- **[Google ADK](https://github.com/google/adk-python)** — agent orchestration
- **[Gemini](https://ai.google.dev/)** — extraction, drafting, judging
- **`gemini-embedding-001`** — semantic SKU matching
- **[Cloud Firestore](https://firebase.google.com/products/firestore)** — master data + transactional state + vector index
- **[Cloud Pub/Sub](https://cloud.google.com/pubsub)** — Gmail event fan-out
- **[Gmail API](https://developers.google.com/gmail/api)** — inbox in, replies out

---

## What it is not

- **Not a portal.** Customers do not log in anywhere. They keep emailing the same address they always did.
- **Not a chatbot.** It does not chat with the customer; it answers them like the order desk would.
- **Not an EDI replacement.** It handles the unstructured 60% that EDI never solved.
- **Not a black box.** Every stage writes to an audit log; every reply is gated by a second model.
- **Not an autopilot.** It hands off to a human the moment it stops being confident.

---

## Quick start

```bash
# Prereqs: Python 3.13, uv, Firebase CLI, a Google Cloud project with Gmail API on.

uv sync
cp .env.example .env        # fill in keys

make emulator               # Firestore emulator, leave running
make seed                   # load product + customer master data
make dev                    # ADK Web UI on http://localhost:8000
```

Other entry points:

| Command | What it does |
|---|---|
| `make cli` | CLI chat against the agent |
| `make smoke` | Run one fixture email through the full pipeline |
| `uv run pytest` | Unit + integration tests |

---

## Repo layout

```
adk_apps/order_intake/   ADK app entry — what `adk web` loads
backend/                 Agent stages, Gmail client, Firestore stores, validators
data/                    Seed master data + fixture emails / PDFs / Excel / EDI
design/                  Dashboard wireframes
firebase/                Firestore rules + indexes
hackathon-deck/          Pitch deck (HTML)
scripts/                 Gmail OAuth bootstrap, master-data loader, smoke runner
tests/                   unit / integration / e2e / eval
```

---

## Status

This is a working MVP. The end-to-end pipeline runs against the Firestore emulator and a live Gmail inbox. The dashboard is wireframed but not wired up.

---

## License

MIT.
