# Next.js Port — Session Handoff

**From:** `design/wireframes-v2/` (static HTML wireframes against real captured data)
**To:** A production Next.js app, statically deployable to Firebase Hosting
**Handoff written:** 2026-04-26
**Entry point next session:** Read this file top-to-bottom, then `README.md` in this folder.

---

## What this document is

The static wireframes in this folder are **visually final** and **rendered against real captured pipeline runs**. The next phase is to port them to a Next.js app so that:

- The same HTML output renders from a JSON-driven template (one component, three runs).
- A judge clicking **Run the Pipeline** on the landing watches a live-run page animate, then lands on the same Read Sheet.
- The whole site exports as static and deploys to Firebase Hosting with one command.
- A persistent disclaimer makes clear: **the data is real captures, the surface is a prototype**.

This handoff captures everything needed to start that work without re-litigating any decision.

---

## Inventory — what exists today

```
design/wireframes-v2/
├── README.md                              ← session-1 summary (visual decisions)
├── NEXTJS-HANDOFF.md                      ← THIS FILE
├── index.html                             ← landing wireframe
├── data/
│   ├── A-001-patterson.json               ← real capture · ESCALATE · 22 lines · 19 price violations
│   ├── A-002-mm-machine.json              ← real capture · AUTO-APPROVE · 2 lines · judge pass
│   └── A-003-birch-valley.json            ← real capture · REPLY MERGED · exception advanced
└── runs/
    ├── A-001-patterson.html               ← Read Sheet wireframe
    ├── A-002-mm-machine.html              ← Read Sheet wireframe
    └── A-003-birch-valley.html            ← Read Sheet wireframe

scripts/
└── capture_run.py                         ← regenerates the JSON files from a real run's audit_log
```

The 4 HTML wireframes are **the visual contract**. The 3 JSON files are **the data contract**. `capture_run.py` is **the bridge between the running agent and the wireframes**.

---

## Locked decisions (carry forward — do not re-debate)

### From wireframes-v2 README

- **Surfaces**: `/` (landing) + per-run sheets at `/runs/<id>`. No separate `/runs` register page — the §02 cards on the landing serve that role for the demo's 3-run universe.
- **First fold of landing**: headline `Reads order emails. Refuses to write bad ones.`, one-sentence dek, two CTAs (`View the latest run →` + `Watch how it works ↓`), live last-run stamp.
- **Stats strip below hero**: `0 / 11 / 497 / 3` with `BAD WRITES / SEQUENTIAL STAGES / UNIT TESTS / CAPTURED RUNS`. The `0` is styled red as the page anchor.
- **Section order on landing**: §01 How it works (R/V/D 3-step) → §02 See it in action (3 cards) → §03 Under the hood (11-stage pipeline diagram).
- **Top bar**: `Order Intake Agent · ● LIVE · v0.4` left, sheet ID center, **GitHub button** right (octocat SVG + Source + ↗). Repo URL: `https://github.com/Krrish777/Order-Intake-Agent`.
- **Read Sheet composition**: manuscript with marginalia. Every section is `[primary artefact left] + [italic-serif editor's marks right]`. 3:1 letter-to-margin ratio; collapses to single column at 980px.
- **Read Sheet sections** (shared core): §I Correspondence · §II Extraction (or Memory Match for REPLY) · §III Decision · §IV path-aware (Handoff for ESCALATE / Confirmation+Judge for AUTO / Outcome for REPLY) · §V Numbers.
- **Color semantics**: red = ESCALATE / refusal · indigo = chrome / REPLY · green = AUTO-APPROVE / judge pass.
- **Voice**: italic Instrument Serif captions + short narration only. No multi-paragraph essay.
- **Captured strip** (the proof-of-real banner under the title block): indigo bar with `✦ Captured from a real run · correlation_id <hex>… · view raw JSON ↗`. Already on every Read Sheet.

### Architecture

- **Demo runtime**: deterministic replay of real captured runs. The "Run the Pipeline" CTA replays a captured JSON; no live agent dependency during the demo.
- **Two-endpoint pattern**: `/run/<id>` is a separate live-run page (animates the pipeline diagram + event feed at captured wall-clock pace). When the run completes, it transitions to `/runs/<id>` (the static Read Sheet). Same JSON feeds both surfaces.
- **Replay timing**: stage durations come from `stages[i].duration_ms` in the captured JSON. Animation is data-driven, not hard-coded.
- **Hosting**: static export to Firebase Hosting. Same code path on stage and on cloud. No Firestore-from-browser, no Cloud Run.

### Disclaimer (explicitly required)

When the production Next.js build ships, every page must surface the prototype-on-real-data status. **Do not skip this.** Two visible treatments, both required:

1. **Persistent thin banner** at the very top of every page, before the top-bar — drafting style, indigo-on-vellum, 28-32px tall:
   `● PROTOTYPE · data captured from real pipeline runs against demo-order-intake-local · view raw JSON for any run`
2. **First-visit modal** on the landing, dismissible with a button and remembered via `localStorage`:
   - 1-2 sentences explaining what's real (the captured JSON, the agent runs against real Gemini + LlamaCloud + Firestore emulator) and what's prototype (the Run button replays captured JSON; no live mailbox).
   - One CTA button: "OK — show me the demo."

The per-sheet captured strip already implements the third tier of disclosure — the forensic-trace level — and stays on every Read Sheet regardless of the landing-level treatment.

Memory entry capturing this: `C:/Users/777kr/.claude/projects/.../memory/project_nextjs_prototype_banner.md`.

### Target event (do not re-flip)

**Google Solution Hackathon.** Memory entry: `project_target_event.md`.

---

## The data contract — JSON shape per run

The shape is captured by `scripts/capture_run.py`. Each run JSON has this top-level structure:

```jsonc
{
  "correlation_id":   "57d74acf2ed34908b81590888ce3f65e",
  "source_message_id":"<...@grafton-reese.example>",
  "session_id":       "smoke-d9900e95",
  "agent_version":    "track-a-v0.4",
  "captured_at":      "2026-04-26T06:16:03Z",
  "total_wall_clock_seconds": 43.1,
  "stage_count":      11,
  "stages": [
    { "stage": "ingest_stage",      "action": "stage_exited", "outcome": "ok",
      "entered_ts": null, "exited_ts": "...", "duration_ms": null,
      "payload": {} },
    { "stage": "classify_stage",    "duration_ms": 19404, "outcome": "ok", ... },
    // ... 11 stages total
  ],
  "lifecycle_events": [
    { "stage": "lifecycle", "action": "envelope_received",
      "outcome": null, "ts": "...", "payload": { "attachment_count": 1 } },
    { "action": "routing_decided",  "outcome": "escalate",     ... },
    { "action": "exception_opened", "outcome": "exception",    ... },
    { "action": "run_finalized",    "outcome": "ok",           ... },
    { "action": "email_send_skipped","outcome": "skip",        ... }
  ],
  "orders":     [ /* OrderRecord docs (AUTO path) */ ],
  "exceptions": [ /* ExceptionRecord docs (ESCALATE path) */ ],
  "raw_audit_event_count": 27
}
```

**Per-run field highlights** the page renders against:

| Wireframe section | Data source |
|---|---|
| Title block: customer | `orders[0].customer.name` or `exceptions[0].customer.name`; falls back to capture metadata for REPLY |
| Title block: verdict badge | derived from `orders[0]` vs `exceptions[0]` presence + `lifecycle_events[].action == "routing_decided"`'s outcome |
| Title block: wall-clock | `total_wall_clock_seconds` |
| §I Correspondence letter | parse `data/<eml-fixture>.eml` headers + body OR derive from session state envelope |
| §II Extraction ledger | `orders[0].lines` (AUTO) or `exceptions[0].parsed_doc.sub_documents[0].line_items` (ESCALATE) |
| §II Customer block | `orders[0].customer.*` / `exceptions[0].customer.*` |
| §III Decision tiers | `exceptions[0].validation_result.lines[].match_tier` aggregated; `aggregate_confidence` |
| §III Routing | `lifecycle_events[].action == "routing_decided"`.outcome |
| §IV path-aware | AUTO: `orders[0].confirmation_body` + `orders[0].judge_verdict`; ESCALATE: `exceptions[0]` doc fields; REPLY: `exceptions[0]` advanced status + reply body |
| §V Latency table | `stages[]` with `duration_ms` |
| Captured strip | `correlation_id`, `raw_audit_event_count`, `captured_at` |

**Important:** the `data/<eml>.eml` file is the canonical inbound-email source. The capture JSON does NOT include the raw email body verbatim — it includes the parsed `envelope` (in session state, not currently exported by capture_run.py). For Next.js, either:

1. **Bake the email body into the JSON** — add an `inbound_email: { headers, body }` field to the capture script's output. (Recommended; one-line addition.)
2. **Parse the .eml at build time** — Next.js `getStaticProps` reads the .eml file directly.

Path 1 is cleaner for the production build because it makes the JSON fully self-contained.

---

## Component breakdown — HTML → React

Every visual construct in the wireframes maps to a React component. Strict mapping:

| Component | Used on | What it renders |
|---|---|---|
| `<TopBar>` | every page | indigo top bar with order-intake-agent + LIVE pill + sheet ID + GitHub button |
| `<DisclaimerBanner>` | every page | thin "PROTOTYPE · data from real runs · view raw JSON" strip directly above TopBar |
| `<DisclaimerModal>` | landing only, first visit | full-screen modal explaining real-vs-prototype; localStorage flag |
| `<Page>` | every page | sheet container with corner crops + drafting-grid background (from CSS vars) |
| `<HeroBlock>` | landing | headline + dek + CTAs + last-run stamp |
| `<StatsStrip>` | landing | 4 mono numerals; `0` red, others indigo |
| `<HowItWorks>` | landing | 3-card R/V/D step grid |
| `<RunCards>` | landing | 3 sheet cards (Patterson · MM Machine · Birch Valley); link to `/runs/<id>` |
| `<PipelineDiagram>` | landing + live-run | 11 numbered circles in a row; `gate` styling on stage 10, `send` styling on stage 11 |
| `<TrustStrip>` | landing | "Built on" stack + pull-quote |
| `<TitleBlock>` | Read Sheet | 4-cell drafting header (Sheet · Customer · Captured · Verdict) |
| `<CapturedStrip>` | Read Sheet | indigo proof-of-real banner with correlation_id + view raw JSON link |
| `<SheetIntro>` | Read Sheet | 1-sentence Instrument Serif italic narration |
| `<Letter>` | §I + §IV | letter-style component with tab (Inbound/Draft/Confirmation), header `<dl>`, body, signature |
| `<Marginalia>` | every section | wraps `<Note>` children; right-side italic-serif editor's marks |
| `<Note variant="default" \| "red" \| "green" \| "fact">` | Marginalia children | one editor's mark with label + body |
| `<Ledger>` | §II | parsed-line table with optional alias sub-row + status column (✓ / ⚠ −X% / `×`) |
| `<DecisionTiers>` | §III | 3-tier ladder + aggregate confidence bar with thresholds + routing table + verdict callout |
| `<MemoryMatch>` | §II on REPLY only | 4-card grid (original PO · prior clarification · reply · what agent did) |
| `<HandoffBlock>` | §IV on ESCALATE | exception-doc card with `<dl>` of persisted fields + rationale excerpt |
| `<ConfirmationPair>` | §IV on AUTO | letter + judge-tile side-by-side + order-record strip below |
| `<OutcomeBlock>` | §IV on REPLY | exception-advanced strip + narration paragraph |
| `<LatencyTable>` | §V | per-stage durations table |
| `<Colophon>` | every page | 4-cell drafting footer |

**Style strategy**: extract the existing inline CSS from each wireframe into one `app/styles.css` file using the same `:root` variables. Components consume class names; no CSS-in-JS needed.

---

## Routing

```
/                            → landing (index.html)
/runs/A-001-patterson        → Read Sheet for Patterson
/runs/A-002-mm-machine       → Read Sheet for MM Machine
/runs/A-003-birch-valley     → Read Sheet for Birch Valley
/run/A-001-patterson         → live-run page for Patterson  (NEW — wireframe pending)
/run/A-002-mm-machine        → live-run page for MM Machine (NEW — wireframe pending)
/run/A-003-birch-valley      → live-run page for Birch Valley (NEW — wireframe pending)
```

Use Next.js dynamic routes with `getStaticPaths`:

```ts
// app/runs/[id]/page.tsx
export async function generateStaticParams() {
  return [
    { id: 'A-001-patterson' },
    { id: 'A-002-mm-machine' },
    { id: 'A-003-birch-valley' },
  ];
}
```

`getStaticProps` (or App Router equivalent) reads `design/wireframes-v2/data/<id>.json` at build time.

The `<id>` is also the JSON filename — no slug-to-file mapping table needed.

---

## The live-run page (still TODO — designed in this folder's README)

`/run/<id>` is a separate visual experience from the Read Sheet. Approximate shape:

```
┌─ DISCLAIMER BANNER ────────────────────────────────────┐
└────────────────────────────────────────────────────────┘
┌─ TOP BAR ──────────────────────────────────────────────┐
│ ← back   Order Intake Agent · ● LIVE   [Source ↗]    │
└────────────────────────────────────────────────────────┘
┌─ STATUS RIBBON ────────────────────────────────────────┐
│  ● RUNNING · A-001 · PATTERSON              t = 18.6s │
│  stage 04 of 11 · parsing the attachment              │
└────────────────────────────────────────────────────────┘

      THE PIPELINE — animated, large
      ════════════════════════════════════════════════
      ① ── ② ── ③ ── ●●● ── ⑤ ── ... ── ⑪
     done done done [parsing] pending pending

┌─ EVENT FEED ───────────────────────────────────────────┐
│ [09:41:12]  envelope_received · attachments=1         │
│ [09:41:13]  stage classify entered                    │
│ [09:41:32]  classify · purchase_order · conf=1.00     │
│ [parsing... ▮▮▮▮▮▮░░░░]                               │
└────────────────────────────────────────────────────────┘

         [ Read the sheet → ]   appears at run end
```

**Open design questions** (deferred to live-run wireframe phase):

1. Per-id pages, or single template parameterized by query param? (Recommend per-id to match Read Sheet pattern.)
2. Pipeline diagram visual states: `pending` (outline only) → `active` (pulsing indigo fill) → `done` (filled green for ok / red for error)?
3. Event feed: scroll auto with newest at bottom (terminal style) or pinned with newest at top?
4. Skip / fast-forward affordance, or always real wall-clock?
5. Per-verdict pipeline behavior:
   - A-001 (ESCALATE) — 11 stages fired, 8 did meaningful work
   - A-002 (AUTO) — 11 stages, all meaningful, judge passes near the end
   - A-003 (REPLY) — 11 stages, only reply_shortcircuit + finalize did work (10 no-ops)

Replay engine pseudocode:

```ts
async function replay(stages: Stage[], onTick: (s: Stage, ms: number) => void) {
  let elapsed = 0;
  for (const s of stages) {
    const dur = s.duration_ms ?? 0;  // null entries (e.g. ingest_stage with no entered_ts) tick at 0
    const t0 = Date.now();
    onTick(s, elapsed);
    await sleep(dur);
    elapsed += dur;
  }
}
```

---

## Build pipeline

```
            backend pipeline runs
            ──────────────────
                    │
                    ▼
           Firestore audit_log + orders/exceptions
                    │
                    │  scripts/capture_run.py <correlation_id>
                    ▼
            design/wireframes-v2/data/A-XXX.json   (committed to repo)
                    │
                    │  Next.js getStaticProps reads JSON at build time
                    ▼
            static HTML output → out/
                    │
                    │  firebase deploy --only hosting
                    ▼
            cloud URL judges click
```

To regenerate a sheet's data after a fresh agent run:

```bash
firebase emulators:start --only firestore             # one shell
FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 \
  uv run python scripts/load_master_data.py
PYTHONPATH=. FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 \
  uv run python tests/eval/fixtures/seed_birch_valley_exception.py  # only for A-003

# A-001
FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 \
  uv run python scripts/smoke_run.py data/pdf/patterson_po-28491.wrapper.eml
# capture using the printed correlation_id:
FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 \
  uv run python scripts/capture_run.py <correlation_id> \
  --out design/wireframes-v2/data/A-001-patterson.json

# repeat for A-002 (data/email/mm_machine_reorder_2026-04-24.eml)
# repeat for A-003 (data/email/birch_valley_clarify_reply.eml — seed first!)
```

The Birch Valley seed advances state on each run, so for re-runs:
```bash
# clear+reseed before each Birch Valley run
FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 uv run python -c "
from google.cloud.firestore import Client
c = Client(project='demo-order-intake-local')
for coll in ['exceptions', 'audit_log']:
    for d in c.collection(coll).stream(): d.reference.delete()"
PYTHONPATH=. FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 \
  uv run python tests/eval/fixtures/seed_birch_valley_exception.py
```

For MM Machine (idempotent dedup), clear `orders` between runs of the same fixture.

---

## Hosting

Add `hosting` block to `firebase.json`:

```json
"hosting": {
  "public": "out",
  "ignore": ["firebase.json", "**/.*", "**/node_modules/**"],
  "rewrites": [
    { "source": "/runs/**",  "destination": "/runs/index.html" },
    { "source": "/run/**",   "destination": "/run/index.html"  }
  ]
}
```

Build + deploy:

```bash
cd web                           # the new Next.js app directory
npm run build                    # next build with `output: 'export'` in next.config.js
cd ..
firebase deploy --only hosting
```

The `Order-Intake-Agent` repo's `.firebaserc` already targets `demo-order-intake-local`. For real cloud deploy, set up a real Firebase project (e.g. `order-intake-agent-491911` per memory) and `firebase target:apply hosting <site>`.

---

## File-by-file implementation order (recommended)

1. **Scaffold the Next.js app** (`web/` or `frontend/`). App Router. `output: 'export'`. TypeScript. Tailwind optional (not necessary — the design tokens come from CSS variables already inlined in wireframes).
2. **Lift CSS** from `index.html` and `runs/A-001-patterson.html` into `web/app/styles.css`. Same `:root` variables. Same component class names.
3. **Build `<TopBar>`, `<DisclaimerBanner>`, `<Page>`, `<Colophon>`** as the chrome that wraps every page.
4. **Build `<DisclaimerModal>`** with `localStorage`-backed dismissal.
5. **Build the landing page** (`/`) — port `index.html` into composed components. Hardcode the 3 cards' metadata for now (or read it from a manifest file mirroring `data/<id>.json` headers).
6. **Build `<TitleBlock>`, `<CapturedStrip>`, `<SheetIntro>`, `<Letter>`, `<Marginalia>`, `<Note>`** — the shared Read Sheet shell.
7. **Build `<Ledger>`, `<DecisionTiers>`, `<HandoffBlock>`, `<ConfirmationPair>`, `<MemoryMatch>`, `<OutcomeBlock>`, `<LatencyTable>`** — section-specific blocks. Each has its own data shape.
8. **Wire the Read Sheet** at `/runs/[id]` with `getStaticProps` reading the JSON, and a render function that switches §IV based on the verdict.
9. **Add `inbound_email` to the capture JSON** (one-line patch to `capture_run.py`) and update §I accordingly.
10. **Build the live-run page** at `/run/[id]` (after live-run wireframe is designed in a follow-up session).
11. **Wire the Run button on the landing** to navigate to `/run/<latest>` (default A-001 for the demo).
12. **Configure Firebase Hosting**, build, deploy.

Steps 1-9 produce a working static-only site that exactly matches the wireframes. Step 10 is the additional drama; step 11 ties the user-facing flow together; step 12 ships it.

---

## Out of scope for the Next.js phase

- Live agent execution from the browser. The Run button is a replay; the agent doesn't run live during the demo.
- Firestore-from-browser. No Firebase JS SDK in the page.
- Authentication. The site is fully public; the only "data" is committed JSON.
- An operator dashboard. That's Track D's domain and is deferred per `project_wireframes_v2.md` memory.
- A `/runs` register page beyond the landing's §02 cards.
- New verdict paths beyond ESCALATE / AUTO / REPLY.

---

## What to verify before the first deploy

- [ ] All 4 wireframes render identically in the Next.js app vs. the static HTML (visual diff)
- [ ] The 3 captured JSONs are read correctly at build time
- [ ] DisclaimerBanner is on every page; DisclaimerModal pops on first visit only
- [ ] All `view raw JSON →` links open the JSON in a new tab
- [ ] GitHub button on every page links to the right repo
- [ ] Captured strips on every Read Sheet show the correct correlation_id
- [ ] Mobile viewport (375px) renders without horizontal scroll
- [ ] Lighthouse: Performance ≥ 95, Accessibility ≥ 95 (the design uses semantic HTML; should be free)

---

## Memory pointers (read these too)

- `C:/Users/777kr/.claude/projects/.../memory/project_wireframes_v2.md` — original wireframes-v2 status (now superseded by the data-grounding work)
- `C:/Users/777kr/.claude/projects/.../memory/project_nextjs_prototype_banner.md` — the disclaimer requirement
- `C:/Users/777kr/.claude/projects/.../memory/project_target_event.md` — Google Solution Hackathon
- `C:/Users/777kr/.claude/projects/.../memory/feedback_iterative_data_generation.md` — one file → review → next, never batch

---

## What changed in the data-grounding session (2026-04-26)

This is the work that produced the JSON files in `data/`:

- Captured 3 real pipeline runs against real Gemini + LlamaCloud + Firestore emulator.
- Patched `backend/my_agent/stages/judge.py` twice (8/8 unit tests still passing):
  1. **Body lookup fallback** — judge now reads from `state['confirmation_bodies']` when the persist-time snapshot doesn't carry the body.
  2. **`_flatten_facts` enrichment** — added `ship_to`, `payment_terms`, `short_description`, `uom` so the judge can verify facts the confirmation references.
- Added `scripts/capture_run.py` to bridge audit_log → wireframe JSON.
- Rewrote all three Read Sheets from synthetic placeholders to real captured data. The Patterson narrative shifted dramatically — from "agent couldn't match SKUs" to "agent matched all 22 lines via aliases, then caught 19 price violations averaging −12.5% and refused on price grounds." This is now the strongest demo narrative.
- Added the `<CapturedStrip>` proof-of-real banner on every Read Sheet linking to the raw JSON.

---

## Resuming next session

1. Read this file (~10 min).
2. Read `README.md` in this folder for the original visual decisions.
3. Decide whether to start Next.js scaffolding or wireframe the live-run page first (the README's "Phase 1" notes both as outstanding; pick one).
4. If Next.js scaffolding: follow the **File-by-file implementation order** section above, steps 1-9. Skip step 10 (live-run) until that wireframe is drawn.
5. If live-run wireframe: address the four open design questions in the **The live-run page** section above before drawing.

The data is real. The visual language is locked. The disclaimer is locked. What's left is the port itself.
