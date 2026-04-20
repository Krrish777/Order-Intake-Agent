---
type: sprint-divergence-log
topic: "Order Intake Agent — Theory vs Practice Swaps"
sprint: 1
parent: "Order-Intake-Sprint-Overview.md"
date: 2026-04-20
tags:
  - sprint
  - architecture
  - divergence
  - spec-alignment
---

# Order Intake Agent — Theory vs Practice Swaps

The Glacis research (`research/Glacis-Deep-Dive/`) is a **reverse-engineered theory** of how Glacis likely works. For a solo hackathon sprint, several of its building blocks are swapped for off-the-shelf pieces or simpler approximations. This doc catalogs those swaps so future-you (or another reader) knows which parts of the spec were **deliberately not followed**.

Rule of thumb: spec is sacred for **architectural intent** (anti-portal, event-driven, multi-agent, demo-driven), pragmatic for **specific tool choices**.

## Swap 1 — Document extraction: Gemini multimodal → LlamaParse + LlamaExtract

**Spec says** (`Document-Processing.md`, `Token-Optimization.md`): tiered extraction with Gemini Pro multimodal at Tier 2 for complex PDFs and scanned documents. Custom prompts, custom token budgets.

**We ship:** LlamaParse (text/table extraction from PDF/XLS/CSV) + LlamaExtract (structured output via Pydantic `model_json_schema`) in `backend/tools/document_parser/legacy/parser.py`.

**Why:** LlamaExtract's "agentic" tier does extraction + classification + schema conformance in one API call. Building the equivalent on Gemini would be ~1 week of prompt engineering. The LlamaCloud dep is acceptable for a hackathon.

**When to revisit:** production deployment or cost-sensitive scale. Gemini native is cheaper at volume and removes a vendor dep.

**Where it shows up:** `backend/tools/document_parser/legacy/parser.py`, `backend/prompts/document_parser.py` (the SYSTEM_PROMPT passed to LlamaExtract).

---

## Swap 2 — Validation judging: Gemini-judge everywhere → Python for numeric, LLM for fuzzy only

**Spec says** (`Generator-Judge.md`, `Validation-Pipeline.md`): a "judge" LLM inspects each validation check, weighing ambiguity against master data.

**We ship:** Python does deterministic checks (price tolerance arithmetic, quantity bounds, SKU existence after matching). LLM (Gemini Flash) only for semantic decisions — e.g., "is this SKU description semantically close to `Dark Roast 5lb bag`?"

**Why:** numeric checks are exact and free in Python. Using an LLM for `price_within_tolerance(line.unit_price, master.price, 0.05)` burns tokens on something that doesn't need judgment.

**When to revisit:** if validation starts failing on edge cases where a Python check is too rigid (e.g., rounded prices, currency-unit mismatches). Escalate specific checks to LLM-judge as they break.

**Where it shows up:** Track V (`backend/tools/order_validator/checks.py` vs `sku_matcher.py`).

---

## Swap 3 — Ingestion: Gmail watch + Pub/Sub → fixture injection CLI

**Spec says** (`Email-Ingestion.md`, `Event-Architecture.md`): Gmail API `users.watch()` registers a push subscription to a Pub/Sub topic. Cloud Run subscriber consumes messages, downloads attachments, fans out to the agent.

**We ship:** `scripts/inject_email.py <fixture_path>` reads bytes from disk, constructs an initial ADK session state, invokes the root agent. No Gmail, no Pub/Sub, no OAuth.

**Why:** Gmail + Pub/Sub is ~1 day of integration work that's orthogonal to the agent's intelligence. For a demo recorded from localhost, the fixture CLI is strictly better — reproducible, fast, no network.

**When to revisit:** any sprint that needs a live email demo. The fixture CLI becomes the dev mode; a parallel Gmail listener becomes the production mode. The agent pipeline doesn't change — only its entry point.

**Where it shows up:** Track I (`scripts/inject_email.py`).

---

## Swap 4 — PO follow-up scheduling: Cloud Scheduler → (deferred, Order Intake doesn't need it)

**Spec says** (`PO-Confirmation-Agent.md`, `Supplier-Communication.md`): Cloud Scheduler fires hourly, checks overdue POs, triggers follow-up emails.

**We ship:** nothing — PO Confirmation is out of scope for this sprint.

**Why:** narrowing the sprint to Order Intake alone removes the need for scheduled triggers. Order Intake is purely reactive (email arrives → agent responds).

**When to revisit:** PO Confirmation sprint. The pattern will be: Cloud Scheduler → Pub/Sub → subscriber that queries Firestore for overdue POs → triggers agent per-PO.

---

## Swap 5 — SOP playbook: learned rules in Firestore → hardcoded Python thresholds

**Spec says** (`SOP-Playbook.md`, `Learning-Loop.md`): Firestore collection of SOP rules per-customer and per-exception-type. Human corrections auto-update rules via the learning loop.

**We ship:** confidence thresholds as Python constants (`AUTO_THRESHOLD = 0.95`, `CLARIFY_THRESHOLD = 0.80`). No Firestore SOP collection. No learning loop.

**Why:** per `Build-Plan.md` cut-list, the learning loop is first-on-the-chopping-block for a hackathon. Hardcoded thresholds demo identically — a judge watching a 2-minute video cannot tell the difference between "rule from Firestore" and "rule from a Python constant."

**When to revisit:** Phase 2 refinement (Top 100), or any sprint after the core is stable. The thresholds-as-constants become a drop-in: load the constants from a Firestore doc instead of a Python module, and thread human-correction capture into a writer to that doc.

**Where it shows up:** Track V (`backend/tools/order_validator/scorer.py` will contain the constants).

---

## Swap 6 (bonus) — Deploy: full Cloud Run pipeline → `adk deploy cloud_run` + localhost for the rest

**Spec says** (`Deployment.md`): Dockerfile, `gcloud run deploy`, Firebase Hosting rewrites, Secret Manager, etc.

**We ship:** `adk deploy cloud_run` for the agent. Everything else (dashboard, persistence) lives locally or wherever is easiest for the sprint.

**Why:** ADK's one-command deploy exists specifically to avoid this yak-shaving. If it works, ship it. The Build-Plan note itself recommends this tradeoff.

**When to revisit:** when `adk deploy` edge-cases (secrets, env vars) bite, fall back to the manual Dockerfile route from `Deployment.md`.

---

## Summary table

| # | Spec says | We ship | Where | Revisit trigger |
|---|---|---|---|---|
| 1 | Gemini multimodal extraction | LlamaParse + LlamaExtract | `backend/tools/document_parser/` ✓ | Production scale / cost |
| 2 | Gemini-judge on every validation | Python for numeric; LLM for fuzzy | Track V | Numeric-check edge cases |
| 3 | Gmail watch + Pub/Sub | Fixture injection CLI | Track I | Live-email demo needed |
| 4 | Cloud Scheduler for PO follow-ups | Deferred (Order Intake only) | — | PO Confirmation sprint |
| 5 | Firestore learned SOP rules | Hardcoded Python thresholds | Track V scorer | Phase 2 refinement |
| 6 | Manual Dockerfile + gcloud deploy | `adk deploy cloud_run` | Track A | ADK deploy edge cases |

## Reading this doc later

If a future session looks at the code and thinks *"why isn't this matching the Glacis spec?"* — check here first. Every intentional divergence is listed. If you find a divergence **not** listed here, that is either a bug or an un-documented decision that should be appended to this file.

## Connections

- [Order-Intake-Sprint-Overview](Order-Intake-Sprint-Overview.md)
- [Order-Intake-Sprint-Decisions](Order-Intake-Sprint-Decisions.md)
- `research/Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Overview.md` — the theory these swaps depart from
- `research/Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Build-Plan.md` — cut-list that informs swaps #4, #5, #6
