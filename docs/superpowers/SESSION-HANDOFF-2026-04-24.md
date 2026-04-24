---
type: session-handoff
date: 2026-04-24
topic: "Spec + plan cycle for post-Track-A tracks"
status: 5 of 7 tracks complete; Track B + Track E remain
parent: "research/Order-Intake-Sprint-Status.md"
tags:
  - handoff
  - superpowers
  - spec
  - plan
---

# Session Handoff — 2026-04-24

## Context for the next session

This session ran the **superpowers brainstorm → spec → writing-plans** cycle across five of seven planned post-Track-A tracks. The goal: produce implementation-ready specs + plans that execute cleanly in separate fresh sessions (via `superpowers:subagent-driven-development` or `superpowers:executing-plans`).

**Priority order agreed at the start of the session:** `C → D → A → B → E`, with frontend (dashboard) deferred to last. Track A was decomposed mid-session into A1 / A2 / A3 after the user picked "decompose into sub-tracks" on a scope-check question.

**What the user wants next session to do:**
1. Brainstorm → spec → plan for **Track B (Generator-Judge quality gate)**
2. Brainstorm → spec → plan for **Track E (Embedding Tier 3)**
3. Then (likely a later session) pick up implementation via subagent-driven-development or executing-plans

---

## Completed tracks (5 of 7)

Every track has:
- A design spec at `docs/superpowers/specs/YYYY-MM-DD-track-<id>-<topic>-design.md`
- An implementation plan at `docs/superpowers/plans/YYYY-MM-DD-track-<id>-<topic>.md`
- Explicit "Rejected alternatives" notes for each architectural decision
- TDD-cycled tasks with full code in every step
- Status doc + Glacis roadmap flip instructions built into the plan's doc-update task
- Spec self-review + plan self-review completed

### Track C — Duplicate Detection

- **Spec commit:** `c978942` — `docs/superpowers/specs/2026-04-24-track-c-duplicate-detection-design.md`
- **Spec amendment:** `cbcf7ce` — four code-vs-spec mismatches discovered during writing-plans pass: `ExtractedOrder.line_items` (not `lines`), Optional types on `OrderLineItem.sku/quantity`, missing top-level `customer_id`/`po_number` on `OrderRecord` → added as denormalized fields
- **Plan commit:** `7b063a1` — 11 tasks, ~4-6h execution
- **Key decisions:** PO# OR content-hash signal; ESCALATE routing; preflight short-circuit inside `OrderValidator.validate`; raw-SKU hash (not sku_matcher output); `sent_at`/`source_message_id`-excluded query; 90-day window constant
- **Schema impact:** `OrderRecord` bumps v2 → v3 with `customer_id` (str, required), `po_number` (Optional[str]), `content_hash` (str, required). Two new Firestore composite indexes.
- **Flagged for implementation time:** Firestore compound `!=` + `>=` constraint — fallback is split-into-two-queries + merge-in-Python, documented in Task 8.

### Track D — Audit Log + correlation_id

- **Spec commit:** `510559d` — `docs/superpowers/specs/2026-04-24-track-d-audit-log-design.md`
- **Plan commit:** `eaf6d08` — 13 tasks, ~6-8h execution
- **Key decisions:** stage-level granularity only (~20-25 events/run); `AuditedStage` mixin wraps `_run_async_impl`; strict-header + free-form-payload-dict schema; UUID4 per-invocation `correlation_id` minted by `IngestStage`; fail-open on audit write errors; `PrivateAttr` constructor-kwarg DI
- **Pipeline impact:** all 9 stages migrate `BaseAgent` → `AuditedStage`; rename `_run_async_impl` → `_audited_run`; 5 lifecycle events added (envelope_received, routing_decided, order_persisted/exception_opened/duplicate_seen, email_drafted, run_finalized); new `audit_log` Firestore collection with immutable rules; 3 new composite indexes
- **Task 4 is the big atomic migration** — all 9 stages + `build_root_agent` in one commit. Subsequent tasks 5-9 layer in lifecycle emits per stage.

### Track A1 — Gmail Polling Ingress

- **Spec commit:** `9ddbf27` — `docs/superpowers/specs/2026-04-24-track-a1-gmail-ingress-design.md`
- **Plan commit:** `f74232a` — 10 tasks, ~4-5h execution
- **Key decisions:** in-process `Runner.run_async`; installed-app OAuth with refresh-token in `.env`; Gmail label `orderintake-processed` for dedup; `messages.get(format='raw')` → `parse_eml` adapter (zero new parsing code); fail-open per-message errors; sequential processing per tick
- **New package:** `backend/gmail/` with `scopes.py` + `client.py` + `adapter.py` + `poller.py`. Two runnable scripts: `gmail_auth_init.py` (one-time OAuth) + `gmail_poll.py` (long-running loop).
- **Deps added:** `google-api-python-client`, `google-auth`, `google-auth-oauthlib`, `google-auth-httplib2`, `python-dotenv`
- **Tests:** 19 new unit (gmail_client 8, adapter 3, poller 6, auth/scopes 2) + 1 gated live integration (`@pytest.mark.gmail_live`)

### Track A2 — Gmail Egress

- **Spec commit:** `0780025` — `docs/superpowers/specs/2026-04-24-track-a2-gmail-egress-design.md`
- **Plan commit:** `d0c65e7` — 10 tasks, ~5-7h execution
- **Key decisions:** new `SendStage` at position #10 (pipeline goes 9 → 10 stages); RFC 5322 `In-Reply-To` + `References` header-based threading (no explicit Gmail `threadId`); `sent_at` + `send_error` idempotency fields; fail-open on send errors (at-least-once); `GMAIL_SEND_DRY_RUN=1` env toggle for dev
- **Schema impact:** `OrderRecord` bumps v3 → v4, `ExceptionRecord` bumps v2 → v3, both add `sent_at: Optional[datetime]` + `send_error: Optional[str]`. `AGENT_VERSION` bumps `track-a-v0.2` → `track-a-v0.3`. `A2_SCOPES = A1_SCOPES + [gmail.send]`.
- **Dep on Track D:** `SendStage` inherits `AuditedStage` mixin. Plan includes fallback for executing A2 without Track D (inline the mixin directly).
- **Tests:** 23 new unit (gmail_send 5, stage_send 9, store updates 4, schema 2, orchestrator 3) + 1 integration

### Track A3 — Pub/Sub Ingestion

- **Spec commit:** `5b604c4` — `docs/superpowers/specs/2026-04-24-track-a3-pubsub-ingestion-design.md`
- **Plan commit:** `6a07397` — 10 tasks, ~4-5h execution
- **Scope call upfront:** scoped down to PULL subscription + in-worker watch renewal. **No Cloud Run / webhook / Secret Manager / Cloud Scheduler** — deliberately Phase 3.
- **Key decisions:** coexist with A1's poller as separate entrypoints; `SubscriberAsyncClient.pull()` in asyncio loop (no streaming-pull threading bridge); Firestore-backed `historyId` cursor at `gmail_sync_state/{user_email}`; in-worker `_renew_loop` daily; stale-cursor → full-scan fallback
- **New files:** `backend/gmail/watch.py`, `backend/gmail/history.py`, `backend/gmail/pubsub_worker.py`, `backend/persistence/sync_state_store.py`, `scripts/gmail_pubsub_worker.py`, `scripts/gmail_watch_setup.py`
- **Deps added:** `google-cloud-pubsub>=2.23`
- **Tests:** 16 new unit (watch 3, history 4, sync_state 3, worker 6) + 1 gated `@pytest.mark.pubsub_emulator` integration

---

## Remaining tracks (2 of 7)

Next session picks these up in order.

### Track B — Generator-Judge quality gate (NEXT)

- **What:** second Gemini Flash call that reviews every outbound email body (clarify + confirmation) before Gmail send. Hard-blocks on hallucinated URLs / unauthorized commitments / tone drift.
- **Key natural design questions:** where the judge call happens (inside `SendStage`? new `JudgeStage`? wrapping the `GmailClient.send_message` call?); judge prompt design (structured `JudgeVerdict` output with pass/fail + reason); what "fail" does (re-draft with feedback? escalate? just block the send?); dry-run for the judge itself
- **Dependencies:** Track A2's `SendStage` is the natural hook — judge runs before `gmail_client.send_message` fires. Spec notes this explicitly under Connections.
- **Out of scope (already baked into A2 spec):** the A2 spec's Connections section says "Track B wraps SendStage's actual send call with a judge-pass gate. Expected shape: a JudgeService.evaluate(body) call inside _maybe_send_confirmation / _maybe_send_clarify between the sent_at-guard and the send_message call. Fail-closed on judge failure → treated as send_error='judge_rejected: <reason>'."
- **Probable scope:** new `backend/judge/` package with `JudgeService` + Pydantic `JudgeVerdict` schema + `build_judge_agent()` factory (another LlmAgent); SendStage's `_maybe_send_*` methods wrap the actual gmail_client call with a judge-gate. ~1 day of work.

### Track E — Embedding Tier 3 (after B)

- **What:** replace the stub at `backend/tools/order_validator/tools/master_data_repo.py:find_product_by_embedding` with a real `text-embedding-004` + Firestore vector-index `find_nearest()` call. Closes the 3-tier SKU ladder (currently tier-1 exact + tier-2 fuzzy work; tier-3 falls through cleanly).
- **Key natural design questions:** embedding model (text-embedding-004 vs newer gemini-embedding-001); asymmetric task types (RETRIEVAL_DOCUMENT for catalog ingest, RETRIEVAL_QUERY for customer description); similarity threshold (Glacis spec says 0.90 for auto-match); seeding strategy (batch-process all 35 products at seed time vs on-demand); hybrid search (category-filtered vector search); alias learning
- **Out of scope per cut-list:** fine-tuned embedding model, Vertex AI Vector Search sidecar (>100K SKU scale), multi-language
- **Probable scope:** extend `scripts/load_master_data.py` to compute + persist embeddings alongside product docs; new `backend/tools/order_validator/tools/embedding_matcher.py` to replace the stub; new Firestore vector index on the products collection; ~10-15 unit tests. ~1 day of work.

---

## State of docs + git at session end

**Latest commit:** `819373a` — docs: note Track A3 spec + plan landing in sprint status

**Branch:** `master`

**Working tree:** clean (per last status-doc commit)

**Untracked artifacts worth noting:**
- `data/email/mm_machine_reorder_2026-04-24.eml` — live-smoke fixture from earlier Track A close-out
- `data_email_*.eml.json` — session dumps from live-smoke runs
- `design/` — wireframe mockups (unused in sprint)
- `hackathon-deck/` — presentation assets
- `research/Order-Intake-ConfirmStage-Plan.md` — superseded by landed ConfirmStage work

**Docs updated this session:**
- `research/Order-Intake-Sprint-Status.md` — multiple commits noting each spec+plan landing
- `Glacis-Order-Intake.md` — spec amendment in frontmatter for ConfirmStage (earlier); no further flips since tracks haven't executed yet (flips happen in each track's doc-update task during execution)

**All five landed specs + plans are ready to execute.** No edits needed to any of them before implementation.

---

## Commit log for the session (relevant subset)

```
819373a docs: note Track A3 spec + plan landing in sprint status
6a07397 docs: add Track A3 pubsub-ingestion implementation plan
5b604c4 docs: add Track A3 pubsub-ingestion design spec
0dadd56 docs: note Track A3 design shape (under review) in status
7d0b083 docs: note Track A2 spec + plan landing in sprint status
d0c65e7 docs: add Track A2 gmail-egress implementation plan
0780025 docs: add Track A2 gmail-egress design spec
a27ced9 docs: note Track A2 design shape (under review) in status
872e32c docs: decompose Track A into A1/A2/A3 sub-tracks
36190b8 docs: note Track A1 spec + plan landing in sprint status
f74232a docs: add Track A1 gmail-ingress implementation plan
9ddbf27 docs: add Track A1 gmail-ingress design spec
eaf6d08 docs: add Track D audit-log implementation plan
510559d docs: add Track D audit-log design spec
2d5a907 docs: rescope 'what to build first' around C→D→A→B→E sequence
1df0ae1 docs: note Track C spec amendment + impl plan landing in status
7b063a1 docs: add Track C duplicate-detection implementation plan
cbcf7ce docs(spec): fix Track C spec code-vs-codebase mismatches
c978942 docs: add Track C duplicate-detection design spec
```

---

## How to resume next session

### To continue specs + plans for Track B and Track E

Start the session with something like:

> "Continuing the spec + plan cycle from 2026-04-24. Read `docs/superpowers/SESSION-HANDOFF-2026-04-24.md` for context. Two tracks remain: B (Generator-Judge quality gate) and E (Embedding Tier 3). Start Track B."

Then run the usual flow — `Skill superpowers:brainstorming` → ask clarifying questions → present design → write spec → self-review → user approval → `Skill superpowers:writing-plans` → spec-coverage self-review → commit.

Track B should wrap existing `SendStage` work from A2's spec. Track E replaces the embedding stub and is nearly orthogonal to everything else. After both land, there'll be **seven track specs + plans** queued up for execution.

### To start executing the already-spec'd tracks

Pick any of the five completed tracks — they're independent enough to execute in any order (with documented dependency notes in each spec's `depends_on` / `blocks` fields).

**Suggested execution order:**
1. **Track C (duplicate detection)** — smallest touch surface, validates the TDD-cycled plan pattern
2. **Track D (audit log)** — big migration (Task 4 migrates all 9 stages); best to land before A2/A3 so they inherit the mixin for free
3. **Track A1 (Gmail polling)** — isolated new package, low regression risk
4. **Track A2 (Gmail egress)** — depends on A1 + benefits from D
5. **Track A3 (Pub/Sub ingestion)** — depends on A1; benefits from A2

Start a fresh session per track. Open the plan file. Invoke `superpowers:subagent-driven-development` (recommended — fresh subagent per task, two-stage review) or `superpowers:executing-plans` (inline with checkpoints).

### Reference docs

- **Plan docs** — `docs/superpowers/plans/2026-04-24-track-{c,d,a1,a2,a3}-*.md` — the things the executor reads
- **Spec docs** — `docs/superpowers/specs/2026-04-24-track-{c,d,a1,a2,a3}-*-design.md` — the decision record + rationale for each plan
- **Sprint status** — `research/Order-Intake-Sprint-Status.md` — current state of the whole system
- **Glacis roadmap** — `Glacis-Order-Intake.md` — MVP-vs-Phase-3 boundary for every capability
- **User memory** — `C:\Users\777kr\.claude\projects\C--Users-777kr-Desktop-Order-Intake-Agent\memory\MEMORY.md` — cross-session context about user preferences and project decisions

---

## Known session quirks worth flagging

- The repo's stop-hook blocks on `research/Order-Intake-Sprint-Status.md` being stale. Every time a new commit lands but the status doc doesn't get touched in the same commit chain, the hook asks for an update. During spec + plan work (pure doc commits, not code), this fired repeatedly — handled by backfilling small status notes as each spec/plan landed. Keep that rhythm in the next session.
- The user's preference per memory: prefer deeper single research over broad shallow matrix; skip fundamentals; treat as experienced hackathon builder.
- User signals for approval: "LGTM", "approve", "continue", "go" — all green-light to proceed through the design-review gate.

---

End of handoff.
