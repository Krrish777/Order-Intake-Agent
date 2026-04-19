# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

This is an **early-stage / pre-implementation repo**. The only runnable code is a stub `main.py` that prints a hello message. The substantive content lives in `Glacis-Deep-Dive/` — 29 research notes that serve as the implementation spec for what is to be built.

`README.md` is empty. Treat `Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Overview.md` as the canonical entry point for understanding scope and architecture.

## What is being built

Two complementary AI agents for supply-chain order lifecycle automation, targeted at Google Solution Challenge 2026:

- **Order Intake Agent** — ingests customer orders from email (free text, PDF, XLS, XML), extracts line items, matches SKUs to the item master, validates, and writes sales orders.
- **PO Confirmation Agent** — monitors outbound POs, follows up with suppliers after SLA, parses replies, reconciles against the original PO, flags discrepancies.

Both share one architecture: Gmail → Pub/Sub → ADK agent (Gemini) → validation → Firestore (used as ERP substitute) → dashboard + outbound email. The canonical diagram and component list are in `Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Overview.md`.

Planned Google Cloud stack: **ADK** (agent framework), **Gemini** (extraction/generation), **Firestore** (master data + transactional state + dashboard source), **Pub/Sub** (event routing), **Cloud Run** (agent hosting), **Firebase Hosting** (dashboard SPA), **Cloud Scheduler** (PO follow-up triggers).

## How to navigate `Glacis-Deep-Dive/`

The notes are Obsidian-style with `[[wikilinks]]` and YAML frontmatter. They are organized by concern, not by file layout:

- `...-Overview.md` — start here; links to everything
- `...-Build-Plan.md` — the 2–4 week execution plan, team role split, cut-list
- `...-Order-Intake-Agent.md`, `...-PO-Confirmation-Agent.md` — per-agent deep dives
- `...-ADK-Order-Intake.md`, `...-ADK-PO-Confirmation.md` — ADK-specific agent code patterns
- `...-Firestore-Schema.md`, `...-Event-Architecture.md`, `...-ERP-Integration.md` — data + plumbing
- `...-Validation-Pipeline.md`, `...-Item-Matching.md`, `...-Generator-Judge.md`, `...-Prompt-Templates.md` — agent-internal logic
- `...-Exception-Handling.md`, `...-Learning-Loop.md`, `...-SOP-Playbook.md` — decision/escalation layer
- `...-Demo-Scenario.md`, `...-Dashboard-UI.md`, `...-Synthetic-Data.md` — demo surface
- `...-Deployment.md`, `...-Security-Audit.md`, `...-Token-Optimization.md` — ops

When a user asks about behavior or design, **read the relevant spec note first** before proposing code. The notes contain specific prompt templates, Firestore schemas, and decision trees that are meant to be implemented literally.

## Toolchain

- Python `>=3.13` (`.python-version` pins `3.13`).
- Project metadata in `pyproject.toml`. Currently `dependencies = []` — no lockfile, no venv committed (`.venv` is gitignored).
- No test framework, linter, or CI configured yet. Do not assume `pytest`/`ruff`/etc. are set up — add them with a dependency change when first needed.

### Commands

```bash
# Run the current stub
python main.py

# Add dependencies (project appears intended for uv based on layout)
uv add <package>
uv sync
uv run python main.py
```

There is **no test command, no build command, and no lint command** yet. When you add the first test or linter, record the invocation here.

## Working guidance

- **Build-plan constraint: demo-driven scope.** Per `...-Build-Plan.md`, the rule is: if a feature is not in the 2-minute demo walkthrough (`...-Demo-Scenario.md`), it does not get built. Prefer a single working vertical slice (email → extract → validate → Firestore write → outbound email) over broad coverage.
- **Firestore is the ERP.** There is no SAP integration. "Write to ERP" = Firestore write; "look up master data" = Firestore read. Do not design abstractions for a real ERP unless the user asks.
- **Respect the existing notes as spec.** Prompt templates, schemas, and decision thresholds in `Glacis-Deep-Dive/` are deliberate — reference and implement them, don't reinvent them.
