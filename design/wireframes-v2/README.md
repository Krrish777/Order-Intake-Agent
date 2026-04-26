# Wireframes v2 — Session Notes

**Date:** 2026-04-26
**Folder:** `design/wireframes-v2/`
**Status:** Static wireframes complete · all three sheets grounded in real captured pipeline runs · ready for Next.js port.

> **🚀 If you're starting the Next.js phase, read `NEXTJS-HANDOFF.md` first.**
> It contains the data contract, component breakdown, routing plan, build pipeline, hosting config, and the file-by-file implementation order.

> **📡 Real captured runs live in `data/A-00*.json`.** Each Read Sheet has a `view raw JSON ↗` link (the indigo "Captured strip" under the title block). The data was produced by `scripts/capture_run.py` from real audit_log entries against Gemini + LlamaCloud + Firestore emulator.

---

## Why this folder exists

The original wireframe at `design/wireframes/06-drafting.html` established the visual language (architectural drafting: vellum + indigo + construction-red, Jost / Azeret Mono / Instrument Serif). This folder is the **production-bound iteration** of that language, restructured around competitor-grounded UX (Glacis / Pallet / Conexiom landing patterns) and a manuscript-with-marginalia per-run sheet composition.

The older `design/wireframes/06-drafting.html` is preserved as the visual reference; it is **not** part of the production-bound surface and stays as-is.

---

## File inventory

```
design/wireframes-v2/
├── index.html                              ← landing page
├── README.md                               ← this file
└── runs/
    ├── A-001-patterson.html                ← ESCALATE · 8 stages · 35.0 s
    ├── A-002-mm-machine.html               ← AUTO-APPROVE · 11 stages · 86.0 s
    └── A-003-birch-valley.html             ← REPLY MERGED · 3 stages · 3.0 s
```

All files are **self-contained HTML with inline CSS**. No build step yet. Open any file directly in a browser to review.

---

## Decisions locked in this session

### Landing page (`index.html`)

| # | Decision | Choice |
|---|---|---|
| L1 | Surfaces | `/` (landing) + per-run sheets in `/runs/`. Separate `/runs` register page deferred — the §02 "See it in action" cards on the landing serve as the register for the demo's 3-run universe. |
| L2 | Top bar | Thin bar with `Order Intake Agent · ● LIVE · v0.4` left, sheet ID center, **GitHub button right** (octocat SVG + "Source" + ↗). Repo URL placeholder: `https://github.com/Krrish777/Order-Intake-Agent` — confirm/swap when known. |
| L3 | Hero copy | Headline `Reads order emails. Refuses to write bad ones.` (italic-red "Refuses"). Dek tightened to one sentence (~22 words). Two CTAs: `View the latest run →` (primary) + `Watch how it works ↓` (ghost). Last-run stamp below CTAs. |
| L4 | Stats strip | Below hero: 4 mono numerals — `0 / 11 / 497 / 3` (BAD WRITES / SEQUENTIAL STAGES / UNIT TESTS / CAPTURED RUNS). The `0` is styled red to anchor the strip; flips convention deliberately. |
| L5 | Section order | §01 How it works (Read → Validate → Decide, 3 cards with R/V/D glyphs) → §02 See it in action (3 run cards) → §03 Under the hood (11-stage diagram). Concrete proof before architecture, per all three competitor patterns. |
| L6 | Trust strip | "Built on" stack list + editorial pull-quote. Below §03, above colophon. |
| L7 | Colophon | 4-cell drafting footer: Submitted to · Build · Date · Set in. |
| L8 | Target event | **Google Solution Hackathon** (corrects an earlier "Hack or Relay 5.0" memory; updated `MEMORY.md` and `project_target_event.md`). |

### Per-run "Read Sheet" template (`runs/A-00X-*.html`)

| # | Decision | Choice |
|---|---|---|
| R1 | Composition | **Manuscript with marginalia.** Every section is a primary artefact on the left + italic-serif "Editor's mark" notes on the right. Modeled on the original 06's "Section II — The Correspondence" pattern, scaled to the whole sheet. |
| R2 | Density | Curated narrative (~3 screens) — top moments only, full data on what's shown but no forensic dump. ~1100 lines per sheet for ESCALATE/AUTO; ~1000 lines for the shorter REPLY sheet. |
| R3 | Adaptivity | **Shared core + per-verdict appendix.** Sections I (Correspondence), II (Extraction or Memory Match), III (Decision or no-decision callout), IV (Draft / Confirmation+Judge / Outcome), V (Numbers) are shared. Section IV is the path-aware slot. |
| R4 | Voice | Italic Instrument Serif captions and short narration only — no multi-paragraph essay block. The agent's analysis lives in margin notes; primary content is data. |
| R5 | First fold | Drafting-style title block (Sheet ID / Customer / Captured / Verdict badge) + a single-sentence narration (Instrument Serif italic). Then §I begins. |
| R6 | Layout grid | `.layout { grid-template-columns: minmax(0, 3fr) minmax(220px, 1fr); }` — 3:1 letter-to-margin ratio. Collapses to single column at 980px (margins reflow as horizontal cards). |
| R7 | Color semantics | Red = ESCALATE / refusal / customer's own words quoted. Indigo = neutral / chrome / REPLY verdict. Green = AUTO-APPROVE / commit / judge pass. Each sheet picks one accent color and uses it consistently in title block, ledger marks, decision callout, and margin note borders. |

### Architecture (data + endpoints)

| # | Decision | Choice |
|---|---|---|
| A1 | Demo runtime | **Deterministic replay of real captured runs.** Run the agent against three fixtures ahead of time, capture each run's full output (audit log + final state) as JSON, ship those JSONs as static assets. The "Run the Pipeline" CTA replays a captured JSON; no live agent dependency during the demo. |
| A2 | Hosting | **Static on Firebase Hosting.** Same code path on stage and on cloud. No Firestore-from-browser, no Cloud Run agent deploy, no auth. |
| A3 | Two-endpoint pattern | `/run/<id>` is a separate **live-run page** that animates the pipeline diagram + event feed at captured wall-clock pace. When the run completes it transitions to `/runs/<id>` (the static read sheet). Each surface does one job. |
| A4 | Replay timing | Stage durations come from the captured JSON's `stages[i].wall_clock_seconds`, not hard-coded JS. Animation is data-driven. |
| A5 | Three captures | A-001 Patterson (ESCALATE, 8 stages, 35.0 s), A-002 MM Machine (AUTO_APPROVE, 11 stages, 86.0 s), A-003 Birch Valley (REPLY MERGED, 3 stages, 3.0 s). |
| A6 | Source fixtures | `data/pdf/patterson_po-28491.wrapper.eml` · `data/email/mm_machine_reorder_2026-04-24.eml` · `data/email/birch_valley_clarify_reply.eml` — all already exist. The Birch Valley fixture needs `tests/eval/fixtures/seed_birch_valley_exception.py` to seed `EXC-00041` first. |

---

## What's already built

### Landing (`index.html`)

- Top bar with GitHub button (octocat SVG) and `← all runs` deferred (cards section serves)
- Hero with the locked headline + dek + two CTAs + last-run stamp
- Stats strip (`0 / 11 / 497 / 3`)
- §01 How it works — Read / Validate / Decide cards with R/V/D glyphs
- §02 See it in action — 3 cards (Patterson · MM Machine · Birch Valley) linking into `runs/`
- §03 Under the hood — 11-stage diagram with judge=indigo gate, send=red egress
- Trust strip + colophon

### A-001 Patterson sheet (`runs/A-001-patterson.html`)

- Title block (red ESCALATE badge)
- Sheet intro (one-sentence narration)
- §I Correspondence — inbound email letter + 3 margin notes + pullquote
- §II Extraction — customer-resolved block + 22-row ledger (every row × in red) + 3 margin notes
- §III Decision — 3-tier ladder (all 0 hits) + aggregate confidence bar (0.00) + threshold table + big ESCALATE callout + 3 margin notes
- §IV The Draft — clarification letter + 3 margin notes
- §V The Numbers — 8-row latency table + 3 margin notes
- Colophon with `Next sheet → A-002` link

### A-002 MM Machine sheet (`runs/A-002-mm-machine.html`)

- Title block (green AUTO-APPROVE badge)
- Sheet intro
- §I Correspondence — MM Machine inbound email + margin
- §II Extraction — 3-row ledger (all ✓ in green) + margin
- §III Decision — 3-tier ladder (3 hits at tier 1, tiers 2–3 idle) + aggregate 1.00 + green AUTO callout + margin
- §IV **The Confirmation** — confirmation letter (with `SENT` chip) **paired with** a green-bordered judge tile (Pass · 0 findings · 5 checks). Plus an order-record strip showing `ORD-04318` written to `orders`.
- §V The Numbers — 11-row latency table (Confirm at 53s as the dominant cost; Judge as a highlighted gate row) + margin
- Colophon with `Next sheet → A-003` link

### A-003 Birch Valley sheet (`runs/A-003-birch-valley.html`)

- Title block (indigo REPLY MERGED badge)
- Sheet intro
- §I Correspondence — reply letter (In-Reply-To header populated) + margin including thread-state and dedupe note
- §II **The Memory Match** — 4-card grid showing original PO (17 Apr) + agent's clarification (17 Apr) + the reply (today, red border) + what the agent did (red wash). Plus a thread-id link strip.
- §III Decision — collapsed to a `N/A · this is identity, not routing` callout (not a routing decision)
- §IV **The Outcome** — `EXC-00041` updated in `exceptions` + state-transition body (PENDING_CLARIFY → REPLY_RECEIVED) + margin notes about why no auto-merge
- §V The Numbers — 3-row latency table + a "8 stages skipped by design" note + margin
- Colophon with `Back to register ←` link

---

## What's NOT built yet (next session's work)

### Phase 1 — `run.html` live-run wireframe

A new wireframe for the `/run/<id>` endpoint. It's structurally different from the read sheet: a single page focused on watching the pipeline execute. Approximate shape:

```
┌─ TOP BAR ──────────────────────────────────────────────┐
│ ← back   Order Intake Agent · ● LIVE   [Source ↗]    │
└────────────────────────────────────────────────────────┘

┌─ STATUS RIBBON ────────────────────────────────────────┐
│  ● RUNNING · A-001 · PATTERSON              t = 18.6s │
│  stage 04 of 08 · parsing the attachment              │
└────────────────────────────────────────────────────────┘


      THE PIPELINE — animated, large
      ════════════════════════════════════════════════

      ① ── ② ── ③ ── ●●● ── ⑤ ── ⑥ ── ⑦ ── ⑧
     done done done [parsing]  pending pending pending pending


┌─ EVENT FEED ───────────────────────────────────────────┐
│ [09:41:12]  envelope_received · attachments=1         │
│ [09:41:12]  reply_check · is_reply=false              │
│ [09:41:13]  classify · purchase_order · conf=1.00     │
│ [09:41:27]  parse · 22 lines · 1 sub-doc              │
│ [parsing... ▮▮▮▮▮▮░░░░]                               │
└────────────────────────────────────────────────────────┘


              [ Read the sheet → ]   appears at run end
              (button is dimmed/hidden until done)
```

**Design decisions to make next session:**

- Should the live-run page have its own per-run instances (`/run/A-001`, `/run/A-002`, `/run/A-003`), or be a single template parameterized by query string (`/run?id=A-001`)?
- What visual states does the pipeline diagram cycle through (pending → active/pulsing → done)?
- Does the event feed scroll auto with each new event, or stay pinned at top with newest at top?
- Is there a "skip" or "fast-forward" affordance, or does the judge always watch the full real wall-clock?
- Per-verdict variants: A-001 fires 8 stages, A-002 fires 11, A-003 fires 3. Same template? Pipeline diagram adapts?

### Phase 2 — Production frontend

Convert wireframes to a JSON-driven static site:

1. Capture script (`scripts/capture_run.py`) — runs the agent against each fixture, queries Firestore audit_log + orders/exceptions, exports normalized JSON to `frontend/data/A-00X.json`.
2. Build pipeline — extract shared CSS to `frontend/styles.css`, split each wireframe into a template that hydrates from JSON. Likely Eleventy, Astro, or a small Python/Jinja script.
3. Firebase hosting config — add `hosting` block to `firebase.json`, deploy via `firebase deploy --only hosting`.

### Phase 3 — Deploy + dress rehearsal

- `firebase deploy --only hosting` to a public URL judges can visit.
- Time the demo end-to-end (Patterson 35s, MM Machine 86s, Birch Valley 3s — not all three fit in a 2-min slot; pick one or do brief tour).
- Verify GitHub link in top bar points to the right repo.
- Verify all three sheets render at 1080p, 1366px laptop, and on a phone.

---

## Open questions for next session

1. **Live-run page style** — Same drafting chrome, or more terminal-like / log-tail aesthetic? The drafting style might feel slow for a real-time view; a denser, more system-status look could feel more "alive."
2. **Replay speed toggle** — Real wall-clock means MM Machine takes 86 seconds. That's longer than half a 2-minute demo slot. Do we want a `?fast=1` query param that compresses to 15-30s for the on-stage demo while keeping real-time as the cloud default?
3. **Repo URL** — Currently placeholder `https://github.com/Krrish777/Order-Intake-Agent`. Confirm or swap.
4. **Run register / browse-all view** — Cards on landing serve as the register for 3 runs. If we ever capture more, do we add a dedicated `/runs` register page, or paginate the landing's §02?

---

## Conventions

- **Self-contained HTML**: each wireframe inlines its CSS. Production frontend extracts to `frontend/styles.css`.
- **Color tokens**: identical across all four files. If you change one, change them all (or extract to CSS custom properties at the production stage).
- **Drafting grid background**: `body::before` repeats a 20px / 100px crosshatch in `rgba(26,42,91,0.045)` and `rgba(26,42,91,0.09)`. Same on every file.
- **Data is illustrative**: the line items, customer names, line counts, wall-clock numbers are realistic but not yet drawn from real captures. Once `scripts/capture_run.py` runs, the captured JSON replaces the inline placeholders.

---

## Earlier in this session

These artefacts also exist from earlier iteration on the original `design/wireframes/06-drafting.html` and the original plan file. Both are now superseded:

- **`design/wireframes/06-drafting.html`** — has my mid-session edits (verdict strip + hero reflow). Could be reverted to its original state since the v2 landing makes it redundant; left alone for now as a comparison reference.
- **`C:/Users/777kr/.claude/plans/design-wireframes-06-drafting-html-i-li-wise-lighthouse.md`** — the approved plan that scoped the wireframe iteration. The plan was for 4 sheets (06 edit + 07 register + 08 MM Machine + 09 Birch Valley); we pivoted mid-session to a fresh `wireframes-v2/` folder and a different structure. Plan is preserved for the audit trail; the actual built artefacts are what's in this folder.

---

## Resuming next session

When you resume, this README is the entry point. The fastest path is:

1. Read this file top-to-bottom (~10 minutes).
2. Open `index.html` and the three sheets in a browser to refresh on the visual language.
3. Pick **Phase 1** (the live-run page wireframe) as the next task.
4. Decide the four open questions above (live-run style, replay speed, repo URL, register page).
5. Build `run.html` (or `run-A-001.html` etc., depending on Q1) using the same chrome conventions documented in this README.

The architecture is locked. The visual language is locked. The data shape is locked. What's left is one more wireframe surface and the production-frontend extraction.
