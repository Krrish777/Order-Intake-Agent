---
type: sprint-plan
topic: "Order Intake Agent — Sprint Plan Overview"
sprint: 1
scope: order-intake-only
parent: "Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Overview.md"
date: 2026-04-20
tags:
  - sprint
  - order-intake
  - planning
  - worktree-decomposition
---

# Order Intake Agent — Sprint Plan Overview

## Context

The Glacis research corpus (`research/Glacis-Deep-Dive/`, 29 notes) describes a **two-agent** architecture (Order Intake + PO Confirmation). For this sprint we narrow scope to **Order Intake only**. PO Confirmation is deferred to a later sprint.

Sprint goal — a fixture email (plain text, PDF, XLS, or CSV) flows through:
```
classify → parse → validate → route (auto | clarify | escalate) → persist → dashboard
```
with a 3-scenario demo and golden-file eval as the regression gate.

This overview is the entry point. The substance lives in four sibling docs.

## Sibling docs

| Doc | What it covers |
|---|---|
| [Order-Intake-Sprint-Decisions](Order-Intake-Sprint-Decisions.md) | The 20 architectural decisions locked in planning (Q → options → chosen → why) |
| [Order-Intake-Sprint-Worktrees](Order-Intake-Sprint-Worktrees.md) | 8 parallel worktrees, contracts, dependency graph, merge order |
| [Order-Intake-Sprint-Theory-vs-Practice](Order-Intake-Sprint-Theory-vs-Practice.md) | 5 places where spec theory is swapped for what we actually ship |
| [Order-Intake-Sprint-Verification](Order-Intake-Sprint-Verification.md) | Per-worktree acceptance + end-to-end demo + evalset structure |

## Why narrow to Order Intake

`Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Build-Plan.md` describes a 3-person 2-4 week plan for both agents. Our context is different:

- **Solo builder** (per project memory) — coordination overhead of "3-person" plan doesn't apply.
- **Demo-driven scope** (per `CLAUDE.md`): *"if a feature is not in the 2-minute demo walkthrough, it does not get built."*
- **Order Intake alone is a complete vertical** — email in, validated order out. It tells a full story.
- PO Confirmation shares the same architecture, so the Order Intake build is effectively a template. Second agent becomes a derivative, not a parallel build.

## Scope boundary

| | In scope | Out of scope |
|---|---|---|
| Agents | Order Intake | PO Confirmation (next sprint) |
| Ingestion | Fixture injection CLI | Gmail API watch/push |
| Formats | Plain text, PDF, XLS, CSV | XML, EDI |
| Languages | English | Multilingual |
| Validation | SKU existence, price tolerance, quantity sanity | Credit, inventory, delivery feasibility, duplicate |
| Routing | Auto-execute / clarify / escalate | — (all three in) |
| Persistence | Abstract interface now; concrete backend chosen after ADK Sessions/Memory research | — |
| Dashboard | Read-only order list + exception detail | Approve / reject / edit UI |
| Deploy | `adk deploy` for agent; rest TBD | Full Cloud Run pipeline |
| Learning loop | — | Deferred |

## Build state at sprint start

### Already built (do not rebuild)

```
data/
├── masters/
│   ├── products.json          ✓ ~730 lines
│   └── customers.json         ✓ ~550 lines
├── csv/, edi/, email/, excel/, pdf/   ✓ fixture folders populated

backend/
├── models/
│   ├── classified_document.py ✓ ClassifiedDocument
│   ├── parsed_document.py     ✓ ParsedDocument / ExtractedOrder / OrderLineItem
│   └── ground_truth.py        ✓
├── prompts/
│   ├── document_classifier.py ✓
│   └── document_parser.py     ✓ SYSTEM_PROMPT used by LlamaExtract
├── tools/
│   ├── document_classifier/   ✓ LlamaClassify wrapper
│   └── document_parser/       ✓ LlamaExtract wrapper (legacy/)
├── exceptions.py              ✓ full parser-error taxonomy
└── utils/                     ✓ structured logging

scripts/
├── classify_file.py           ✓
└── classify_folder.py         ✓

tests/unit/test_document_classifier.py   ✓
```

### Missing (= this sprint's work)

1. Validation pipeline (SKU matcher 3-tier, price tolerance, quantity sanity, confidence scorer, routing decision)
2. ADK SequentialAgent orchestration (replaces stub `backend/my_agent/agent.py`)
3. Persistence adapter (abstract interface; concrete backend from ADK research)
4. Fixture injection CLI (`scripts/inject_email.py`)
5. Read-only dashboard (`frontend/`)
6. Eval harness + golden-file evalsets (`tests/eval/`)
7. Demo scenario runner (`scripts/run_demo.py`)
8. ADK decisions research note (Sessions/Memory vs Firestore; `adk deploy` config)

## How to use these docs

Future sessions building this sprint should read:
1. This overview for orientation.
2. [Order-Intake-Sprint-Decisions](Order-Intake-Sprint-Decisions.md) to confirm no decision is being re-litigated.
3. [Order-Intake-Sprint-Worktrees](Order-Intake-Sprint-Worktrees.md) to pick which worktree they're starting and see its contracts + deps.
4. [Order-Intake-Sprint-Verification](Order-Intake-Sprint-Verification.md) to know what "done" looks like before claiming completion.

## Connections

- Parent research overview: `research/Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Overview.md`
- Original build plan we narrow from: `research/Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Build-Plan.md`
- Demo scenario the three fixtures align to: `research/Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Demo-Scenario.md`
- ADK orchestration blueprint: `research/Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-ADK-Order-Intake.md`
