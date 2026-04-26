# Order Intake Agent

> **The inbox, automated.** A multi-stage AI agent that turns customer order emails — free text, PDFs, XLS, XML — into validated sales orders, end-to-end, without a human re-keying a line.

[![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)](https://www.python.org/downloads/)
[![Built with Google ADK](https://img.shields.io/badge/built%20with-Google%20ADK-4285F4)](https://github.com/google/adk-python)
[![Powered by Gemini](https://img.shields.io/badge/powered%20by-Gemini-8E75B2)](https://ai.google.dev/)
[![Firestore](https://img.shields.io/badge/data-Firestore-FFA000)](https://firebase.google.com/products/firestore)
[![Google Solution Challenge 2026](https://img.shields.io/badge/Google%20Solution%20Challenge-2026-34A853)](https://developers.google.com/community/gdsc-solution-challenge)

---

## Demo

<!--
Drop a 30–60s screen recording or GIF here once recorded.
Suggested: ![demo](docs/demo.gif)
-->

| Surface | Where to look |
|---|---|
| 2-min walkthrough | _coming soon — see `hackathon-deck/`_ |
| Live pipeline | `make dev` → ADK Web UI on http://localhost:8000 |
| Hackathon deck | [`hackathon-deck/index.html`](hackathon-deck/index.html) |
| Dashboard wireframes | [`design/wireframes/`](design/wireframes/) |

---

## The problem

In B2B distribution, **30–60% of customer orders still arrive as unstructured email** — free-text in the body, a PDF attachment, an Excel sheet, sometimes an XML or EDI file. Order-desk teams re-key every line by hand into the ERP. It is slow, error-prone, and unloved work, and it does not scale with order volume.

The same teams already tried portals; customers refused them. The orders keep coming through the inbox.

## The solution

A single agent that lives on the order-desk inbox and does what the human used to do — **read the email, find the order, match the products, validate the result, write the sales order, and reply to the customer**. When something is ambiguous, it asks the customer in plain English. When something looks wrong, it routes to a human with the specific reason. Every action is auditable.

Built with **Google ADK** (orchestration), **Gemini** (extraction + judging), **Firestore** (master data + transactional state), **Cloud Pub/Sub** (Gmail event fan-out), and **Cloud Run** (hosting).

---

## How it works

An incoming email enters an 11-stage `SequentialAgent` pipeline. Each stage has one job, writes its output to session state, and is independently testable.

| # | Stage | What it does |
|---|---|---|
| 1 | **Ingest** | Receives Gmail push notification, fetches the thread, normalizes attachments. |
| 2 | **ReplyShortCircuit** | If the email is a customer reply to a pending clarification, route to the open exception instead of starting a new order. |
| 3 | **Classify** | Decide intent: new order, reply, noise, escalation. |
| 4 | **Parse** | Extract line items via LlamaExtract / Gemini structured output. Flatten multi-doc PDFs. |
| 5 | **Validate** | Run the order through the validation pipeline — SKU match (3-tier: exact → fuzzy → embedding), quantity, price, customer master. |
| 6 | **Clarify** | If validation flags ambiguity, draft a customer-facing question. Human-readable, not robotic. |
| 7 | **Persist** | Write the order (or pending exception) to Firestore. |
| 8 | **Confirm** | Generate the customer-facing order confirmation draft. |
| 9 | **Finalize** | One- or two-sentence run recap for the audit log. |
| 10 | **Judge** | LLM-as-judge quality gate on the outbound reply — block sends that fail. |
| 11 | **Send** | Push the reply via Gmail. Honors `dry_run` for safe local runs. |

```
                 ┌──────────────┐
   Customer ───▶ │ Gmail inbox  │
   email         └──────┬───────┘
                        │ push notification
                        ▼
                ┌──────────────────┐
                │ Cloud Pub/Sub    │
                └──────┬───────────┘
                       ▼
   ┌─────────────────────────────────────────────────┐
   │  Order Intake Agent (ADK SequentialAgent)        │
   │                                                  │
   │  Ingest → ReplyShortCircuit → Classify → Parse   │
   │     → Validate → Clarify → Persist → Confirm     │
   │     → Finalize → Judge → Send                    │
   └────────────┬─────────────────────────────┬───────┘
                ▼                             ▼
       ┌─────────────────┐           ┌────────────────┐
       │ Firestore       │           │ Gmail (reply)  │
       │ (orders +       │           └────────────────┘
       │  master data)   │
       └─────────────────┘
```

---

## Built with Google

| Capability | Google product |
|---|---|
| Agent framework | [Google ADK (Python)](https://github.com/google/adk-python) |
| LLM (extract, judge, draft) | [Gemini](https://ai.google.dev/) |
| Embeddings (SKU tier-3 match) | `gemini-embedding-001` (768d) |
| Master + transactional data | [Cloud Firestore](https://firebase.google.com/products/firestore) |
| Inbox event fan-out | [Cloud Pub/Sub](https://cloud.google.com/pubsub) |
| Email I/O | [Gmail API](https://developers.google.com/gmail/api) |
| Hosting (planned) | [Cloud Run](https://cloud.google.com/run) |

Other dependencies: `pydantic`, `rapidfuzz`, `pymupdf`, `openpyxl`, `reportlab`, `structlog`, `llama-cloud` (LlamaExtract).

---

## Quick start

**Prereqs:** Python 3.13, [`uv`](https://docs.astral.sh/uv/), [Firebase CLI](https://firebase.google.com/docs/cli), a Google Cloud project with the Gmail API enabled, and a `LLAMA_CLOUD_API_KEY`.

```bash
# 1. Install
uv sync

# 2. Configure
cp .env.example .env
# fill in: GOOGLE_API_KEY, LLAMA_CLOUD_API_KEY, GMAIL_*, FIRESTORE_*

# 3. Start the Firestore emulator (leave running)
make emulator

# 4. Seed master data (products, customers) into the emulator
make seed

# 5. Run the agent
make dev      # ADK Web UI at http://localhost:8000
# or
make cli      # CLI chat against the order_intake app
# or
make smoke    # one-shot end-to-end run on a fixture
```

For Gmail live-mode bootstrap (OAuth, watch setup, polling), see `scripts/gmail_*.py` and `.env.example`.

---

## Repo layout

```
.
├── adk_apps/order_intake/   # ADK app entry — what `adk web` and `adk run` load
├── backend/
│   ├── gmail/               # Gmail client, poller, Pub/Sub worker
│   ├── ingestion/           # Email + attachment normalization
│   ├── models/              # Pydantic schemas (OrderRecord, master records, ...)
│   ├── my_agent/            # Root SequentialAgent + 11 stages
│   │   └── stages/          # Ingest, Classify, Parse, Validate, ... Judge, Send
│   ├── persistence/         # Firestore stores (OrderStore, ExceptionStore, ...)
│   ├── prompts/             # Prompt templates per stage
│   ├── tools/               # SKU matcher, validator, master-data repo
│   └── audit/               # Structured audit logging
├── data/
│   ├── masters/             # Seed JSON for products + customers
│   ├── csv/ excel/ pdf/ edi/ email/   # Fixture documents
├── design/wireframes/       # Dashboard mockups
├── firebase/                # Firestore rules + indexes
├── hackathon-deck/          # Pitch deck (HTML)
├── scripts/                 # gmail_auth_init, gmail_poll, load_master_data, smoke_run
└── tests/                   # unit / integration / e2e / eval
```

---

## Status

This repo represents an MVP built for the **Google Solution Challenge 2026** under the working title _Order Intake Agent_.

| Track | Scope | Status |
|---|---|---|
| A — Gmail ingress / egress | OAuth, polling, Pub/Sub worker, send | ✅ |
| B — Generator + Judge | LLM-as-judge gate on outbound replies | ✅ |
| C — Duplicate detection | Idempotency + reply-shortcircuit | ✅ |
| D — Audit log | Structured per-run audit trail | ✅ |
| E — Tier-3 embedding SKU match | `gemini-embedding-001` + Firestore vector index | ✅ |

Out of scope for this submission: PO Confirmation Agent (separate sibling project, deferred).

---

## Tests

```bash
uv run pytest                                # unit
uv run pytest -m integration                 # hits Firestore emulator
uv run pytest -m firestore_emulator          # explicit emulator-only
uv run pytest -m gmail_live                  # live Gmail (gated, opt-in)
```

---

## Acknowledgments

Domain reference for the order-desk problem space drawn from **Glacis** (the German order-automation startup), reverse-engineered from public material. This project is an independent reimplementation on the Google Cloud stack — no Glacis code or proprietary material was used.
