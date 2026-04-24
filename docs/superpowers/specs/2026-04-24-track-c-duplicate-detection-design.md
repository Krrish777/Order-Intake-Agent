---
type: design-spec
topic: "Track C — Duplicate Detection"
track: C
date: 2026-04-24
parent: "research/Order-Intake-Sprint-Status.md"
source_spec_row: "§4 row 'Duplicate detection (check #1)'"
status: approved-for-implementation
amendments:
  - "2026-04-24 (writing-plans-pass): four code-vs-spec mismatches corrected inline — (1) ExtractedOrder field is `line_items`, not `lines`; (2) OrderLineItem.sku and OrderLineItem.quantity are both `Optional`, hash must skip None-sku lines and coerce quantity to `float`; (3) OrderRecord has no top-level `customer_id` or `po_number` — adding both as denormalized fields for query-side (not just `content_hash`); (4) OrderLineItem.quantity is `Optional[float]`, not `int`."
tags:
  - design-spec
  - track-c
  - duplicate-detection
  - validation-pipeline
---

# Track C — Duplicate Detection — Design

## Summary

Prevent shipping the same order twice. Preflight check inside `OrderValidator.validate()` that queries the `orders` collection for a PO#-match OR content-hash-match (scoped by customer + 90-day window, excluding self-retries). On hit, short-circuits to `RoutingDecision.ESCALATE` with `reason="duplicate of <order_id>"`. No new `RoutingDecision` enum value; reuses the existing ESCALATE leg end-to-end.

This closes the Glacis `Validation-Pipeline.md` check #1 (Duplicate detection) — the highest-value remaining validation gap, cut from Track V per `research/Order-Intake-Sprint-Decisions.md`.

## Context

- Existing idempotency (`source_message_id` optimistic-create + `AlreadyExists` swallow on both `orders` and `exceptions`) catches *exact* email replays only. It does not catch: same order sent from a different Gmail thread, forwarded copies, customer resending with a tweaked subject line.
- Validator architecture is mature: `OrderValidator` orchestrator + 6 tools + scorer + router. The ladder today is: `customer_resolver → sku_matcher → price_check → qty_check → scorer → router`. This design adds one step between positions 1 and 2.
- `OrderRecord` is at `schema_version=2` (added `confirmation_body` in 13f05a5, 2026-04-24). This design bumps to v3.
- `AGENT_VERSION` is `"track-a-v0.2"`. Track C does **not** bump `AGENT_VERSION` — duplicate detection is a validator-internal change, not a pipeline-topology change. Firestore analytics continue to use `track-a-v0.2` rows as the post-Track-C baseline.

## Architectural decisions

The four foundational calls, each with trade-offs explicitly considered and rejected alternatives documented.

### Decision 1 — Signal: PO# OR content-hash

Two independent signals in OR. PO# alone is cheapest but misses re-sends that drop or mutate the PO#. Content-hash alone handles that case but adds one more Firestore query per validation. Together they cover both failure modes.

**Rejected:** `source_message_id` only (status quo — misses non-identical replays); PO# + customer + line-set similarity with ±10% qty tolerance (over-engineered for MVP, requires in-memory compare after coarse filter).

### Decision 2 — Routing: ESCALATE

Dup hits route to the existing `RoutingDecision.ESCALATE` leg. Lands in the `exceptions` collection with `reason="duplicate of <order_id>"`. Human decides via the dashboard (once Track D ships). No schema bump on `ValidationResult` or `ExceptionRecord`; no new enum value; no changes to `router.decide()` thresholds.

**Rejected:** new `RoutingDecision.DUPLICATE` enum value (~10 file changes for marginal analytics value); auto-reject with no human review (too risky without ironclad signal — a false-positive on a legitimate reorder is silent lost revenue); AUTO_APPROVE with advisory `duplicate_of` flag (advisory-only isn't a safety rail).

### Decision 3 — Integration: preflight short-circuit inside validator

The dup check is a new method called at the top of `OrderValidator.validate()`, after `customer_resolver` but before any other check. Binary outcome: dup detected → immediate return with ESCALATE + reason, skipping SKU/price/qty checks entirely. Saves LLM-adjacent work on duplicates.

**Rejected:** scoring-check in the ladder (runs wasted checks on dups; duplication isn't a confidence question); stage-level check hoisted into a new `BaseAgent` (dup is a "do we already have this?" validation question, not an orchestration concern — belongs with the validator).

### Decision 4 — Hash composition: customer_id + sorted [(raw_sku, qty)]

Hash the resolved `customer_id` concatenated with sorted (by SKU) `(sku.strip(), qty)` pairs. Uses raw SKU strings from `ExtractedOrder.lines[].sku` — not sku_matcher output. Preserves the preflight-first positioning (otherwise dup check would have to run after sku_matcher, wasting that check's work on dups).

**Rejected:** including `order_date` (LlamaExtract date-parsing noise → different hashes for semantically-identical re-sends); hashing all `ExtractedOrder` fields via `model_dump_json` (too brittle — ship-to whitespace kills the hash); LLM-generated semantic fingerprint (extra Gemini call per order; overkill).

## Components

### New file — `backend/tools/order_validator/tools/duplicate_check.py`

```python
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Callable, Optional

from google.cloud.firestore_v1.async_client import AsyncClient
from google.cloud.firestore_v1.base_query import FieldFilter

from backend.models.parsed_document import ExtractedOrder

DUPLICATE_WINDOW_DAYS = 90


def compute_content_hash(customer_id: str, order: ExtractedOrder) -> str:
    """customer_id + sorted [(raw_sku, qty)] → sha256 hex.

    Deterministic: same inputs always yield same hash.
    Order-independent: shuffling order.line_items yields same hash.
    Skips lines where sku is None (can't hash meaningfully).
    Quantity is coerced to float, None treated as 0.0.
    """
    lines = sorted(
        (line.sku.strip(), float(line.quantity or 0.0))
        for line in order.line_items
        if line.sku is not None
    )
    canonical = f"{customer_id}|" + "|".join(
        f"{sku}:{qty}" for sku, qty in lines
    )
    return sha256(canonical.encode()).hexdigest()


async def find_duplicate(
    client: AsyncClient,
    *,
    customer_id: str,
    order: ExtractedOrder,
    source_message_id: str,
    po_number: Optional[str],
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> Optional[str]:
    """Returns existing order_id if duplicate found in window, else None.

    OR-combines two signals: PO# match + content-hash match.
    Excludes self-matches via source_message_id filter.
    Queries the `orders` collection only.
    """
    cutoff = clock() - timedelta(days=DUPLICATE_WINDOW_DAYS)
    orders_ref = client.collection("orders")

    # PO# branch — only runs if PO# is present on the incoming order
    if po_number is not None:
        query = (
            orders_ref
            .where(filter=FieldFilter("customer_id", "==", customer_id))
            .where(filter=FieldFilter("po_number", "==", po_number))
            .where(filter=FieldFilter("created_at", ">=", cutoff))
            .where(filter=FieldFilter("source_message_id", "!=", source_message_id))
            .limit(1)
        )
        async for doc in query.stream():
            return doc.id  # first hit wins

    # Content-hash branch — always runs
    content_hash = compute_content_hash(customer_id, order)
    query = (
        orders_ref
        .where(filter=FieldFilter("customer_id", "==", customer_id))
        .where(filter=FieldFilter("content_hash", "==", content_hash))
        .where(filter=FieldFilter("created_at", ">=", cutoff))
        .where(filter=FieldFilter("source_message_id", "!=", source_message_id))
        .limit(1)
    )
    async for doc in query.stream():
        return doc.id

    return None
```

**Dependencies:** `google-cloud-firestore` (already pinned), `backend.models.parsed_document.ExtractedOrder` (existing contract).

### Modified — `backend/models/order_record.py`

Three new top-level fields on `OrderRecord` — denormalization is deliberate, so composite Firestore indexes can hit flat field paths (avoids the nested-field index complexity that `customer.customer_id` would require):

- Add `customer_id: str` (required) — denormalized copy of `customer.customer_id`. Duplicates the nested value but enables flat-path queries.
- Add `po_number: Optional[str]` (default `None`) — carries through from `ExtractedOrder.po_number`.
- Add `content_hash: str` (required) — from `compute_content_hash`.
- Bump `schema_version` default `2 → 3`.
- Production construction sites: one — `IntakeCoordinator.process`.
- Test construction sites: 3 files — `tests/unit/test_order_store.py`, `tests/unit/test_stage_persist.py`, `tests/integration/test_order_store_emulator.py` — each fixture needs `customer_id`, `po_number`, and `content_hash` passed.

### Modified — `backend/tools/order_validator/validator.py`

Insert dup check as the second step in `OrderValidator.validate()`:

```python
async def validate(
    self,
    order: ExtractedOrder,
    *,
    source_message_id: str,
) -> ValidationResult:
    # Step 1 — customer resolution (existing)
    customer = await self._customer_resolver.resolve(order.customer_name)
    if customer is None:
        return self._build_unknown_customer_result(order)

    # Step 2 — NEW: duplicate preflight
    existing_id = await find_duplicate(
        self._firestore,
        customer_id=customer.customer_id,
        order=order,
        source_message_id=source_message_id,
        po_number=order.po_number,
    )
    if existing_id is not None:
        log.info(
            "duplicate_detected customer_id=%s existing_order_id=%s "
            "source_message_id=%s",
            customer.customer_id,
            existing_id,
            source_message_id,
        )
        return ValidationResult(
            decision=RoutingDecision.ESCALATE,
            confidence=1.0,
            reason=f"duplicate of {existing_id}",
            customer=customer,
            lines=[],
        )

    # Step 3+ — existing SKU/price/qty ladder (unchanged)
    ...
```

**Signature change:** `OrderValidator.validate` must accept `source_message_id` as a kwarg. The caller chain (`ValidateStage` → `validator.validate`) already threads this through per post-audit F5 (`precomputed_validation` kwarg in `IntakeCoordinator.process`) — will confirm at implementation time whether the signature needs propagation further up.

### Modified — `backend/persistence/coordinator.py`

`IntakeCoordinator.process` computes `content_hash` once per AUTO_APPROVE and attaches to the built `OrderRecord` before write. ESCALATE writes unchanged.

```python
# In the AUTO_APPROVE branch of IntakeCoordinator.process:
from backend.tools.order_validator.tools.duplicate_check import compute_content_hash

order_record = OrderRecord(
    ...,
    customer_id=customer.customer_id,           # denormalized
    po_number=extracted_order.po_number,        # from ExtractedOrder
    content_hash=compute_content_hash(          # computed at persist time
        customer.customer_id,
        extracted_order,
    ),
    schema_version=3,
)
```

### Modified — `firebase/firestore.indexes.json`

Two new composite indexes on the `orders` collection:

```json
{
  "collectionGroup": "orders",
  "queryScope": "COLLECTION",
  "fields": [
    {"fieldPath": "customer_id", "order": "ASCENDING"},
    {"fieldPath": "po_number", "order": "ASCENDING"},
    {"fieldPath": "created_at", "order": "DESCENDING"}
  ]
},
{
  "collectionGroup": "orders",
  "queryScope": "COLLECTION",
  "fields": [
    {"fieldPath": "customer_id", "order": "ASCENDING"},
    {"fieldPath": "content_hash", "order": "ASCENDING"},
    {"fieldPath": "created_at", "order": "DESCENDING"}
  ]
}
```

The `source_message_id != current` filter is an inequality — Firestore allows exactly one inequality per compound query. Since `created_at >= cutoff` is already an inequality on `created_at`, the `!=` on `source_message_id` would require either (a) a separate inequality-only query or (b) client-side filtering after the compound fetch. Option (b) with `limit(10)` is pragmatically fine — the query will return 0–1 docs in practice. **Flagged for verification at implementation time.** If Firestore-emulator enforcement forces option (a), we split into two queries and merge in Python.

### Unchanged — every `BaseAgent` stage file

`ValidateStage`, `PersistStage`, `ConfirmStage`, `FinalizeStage` are untouched. Duplicate detection is entirely a validator-internal concern. The ESCALATE result propagates through the existing pipeline contract.

## Data flow

### Happy path (no dup)

```
IngestStage → ... → ValidateStage
  → OrderValidator.validate(order, source_message_id)
    → customer_resolver → CustomerRecord
    → find_duplicate → None (0 or 1 Firestore reads: PO# query if PO# present, then hash query)
    → sku_matcher / price_check / qty_check / scorer / router (unchanged ladder)
    → ValidationResult(decision=AUTO_APPROVE|CLARIFY|ESCALATE, ...)
PersistStage → IntakeCoordinator.process
  → compute_content_hash → attaches to OrderRecord(schema_version=3)
  → FirestoreOrderStore.save
ConfirmStage → drafts confirmation (AUTO leg)
FinalizeStage → RunSummary
```

### Dup path (PO# hit)

```
ValidateStage → validator.validate
  → customer_resolver → CustomerRecord(customer_id="CUST-00042")
  → find_duplicate → PO# query returns "ORD-abc123"; hash query never fires
  → ValidationResult(ESCALATE, confidence=1.0, reason="duplicate of ORD-abc123")
  → sku/price/qty NOT called
PersistStage → IntakeCoordinator.process
  → RoutingDecision.ESCALATE → writes ExceptionRecord (NOT OrderRecord)
  → ExceptionRecord(reason="duplicate of ORD-abc123", status=AWAITING_REVIEW,
                    clarify_body=None)
ConfirmStage → filters kind=="order" → sees kind=="exception" → skips
FinalizeStage → run_summary.exceptions_opened += 1, orders_created += 0
```

### Dup path (content-hash hit, PO# absent or differs)

Same as PO# hit but the PO# query returns empty and the content-hash query returns the match. 2 Firestore reads instead of 1.

### Self-match avoidance

When a `ParsedDocument` carries multiple `sub_documents` from one email, `PersistStage` fires `IntakeCoordinator.process` once per sub-doc, all sharing the same `source_message_id`. The `source_message_id != current` filter prevents sub-doc N from flagging sub-doc N-1 (just persisted moments earlier in the same run) as a dup of itself.

### Retry/idempotency interaction

If ADK retries the whole invocation (e.g., transient LLM failure), every sub-doc runs twice. The *first* run persists; the *second* run's first validator step would find its own just-persisted order via `find_duplicate`. **But** the existing `AlreadyExists` swallow on `source_message_id` idempotency fires in `IntakeCoordinator` *before* re-validation — the coordinator returns the existing `ProcessResult` without re-running `validator.validate`. Net: duplicate check never sees a self-retry.

### ExceptionRecord status on dup

Written directly as `AWAITING_REVIEW`, not `PENDING_CLARIFY`. Rationale: there's no clarification to ask the customer — we have the order, we know we already have it, human decides the call. `clarify_body` stays `None`. No clarify email is drafted (ClarifyStage filters by CLARIFY routing, ignores ESCALATE).

## Error handling

| Scenario | Behavior |
|---|---|
| Firestore query failure | Propagates. ADK retries the invocation. Silent-failure on dup is the worst outcome. No try/except. |
| Empty `order.lines` | Stable empty-hash is allowed to collide across degenerate orders from the same customer. They'd fail `qty_check` anyway; escalating them early as "dup" is a wash. |
| Missing PO# (`order.po_number is None`) | PO# query skipped entirely; only content-hash query runs. |
| Customer resolution fails | Dup check skipped. Existing unknown-customer path already ESCALATES. |
| Content-hash semantic collision (legit weekly reorder) | ESCALATES to human; human marks "not a dup" and releases. Accepted trade-off. A test case documents this case. |
| Case/whitespace in raw SKU | `.strip()` only — no case-fold. Identical-resend intent preserved; format-variation misses are acceptable. |
| Multi-sub-doc emails | Each sub-doc hashes independently. `source_message_id` filter handles within-same-email sub-doc N vs N-1 case. |
| Clock skew in tests | Injectable `clock` callable, default `datetime.now(timezone.utc)`. |
| Firestore `!=` + `>=` compound-query limitation | **Flagged for implementation-time verification.** If enforcement forces splitting into two queries, merge in Python (limit 10 rows per query is fine). |

### Logging

Single structured INFO line on dup-hit:
```
duplicate_detected customer_id=CUST-00042 existing_order_id=ORD-abc123 source_message_id=<current>
```
Post-hoc grep of `adk web` traces surfaces every dup.

## Testing

### Unit tests — new `tests/unit/test_duplicate_check.py` (~10 tests)

1. `compute_content_hash` is deterministic
2. `compute_content_hash` is order-independent (shuffle `order.lines` → same hash)
3. `compute_content_hash` is customer-scoped (same basket, different customers → different hash)
4. `.strip()` normalises whitespace in raw SKU
5. Case is NOT normalised (documents trade-off)
6. `find_duplicate` returns `None` when no prior orders exist
7. `find_duplicate` returns order_id on PO# hit
8. `find_duplicate` returns order_id on content-hash hit (PO# absent)
9. `find_duplicate` returns order_id on content-hash hit (PO# present but differs from prior)
10. `find_duplicate` excludes self-match via `source_message_id`
11. `find_duplicate` respects 90-day window (inject `clock`)

### Unit tests — extend `tests/unit/test_validator.py` (+2)

- Validator short-circuits on dup hit (assert sku_matcher/price_check/qty_check were never called via AsyncMock assertions)
- Validator runs full ladder when `find_duplicate` returns `None`

### Unit tests — extend `tests/unit/test_order_store.py` (+1)

- `OrderRecord.content_hash` round-trips through `FirestoreOrderStore.save` → `.get`

### Unit tests — extend `tests/unit/test_coordinator.py` (+1)

- On AUTO_APPROVE leg, persisted `OrderRecord.content_hash` matches `compute_content_hash(customer.customer_id, extracted_order)`

### Integration tests — new `tests/integration/test_duplicate_check_emulator.py` (3 tests)

1. Seed one order for CUST-00042 with known PO# + content_hash; re-submit same order envelope → `ValidationResult.decision == ESCALATE`, `reason` contains prior order_id
2. Seed one order; submit order with same basket but different PO# → still ESCALATES (content-hash hit)
3. Seed one order with backdated `created_at` (>90 days ago); submit identical order → passes (outside window)

### End-to-end test — extend `tests/integration/test_orchestrator_emulator.py` (+1)

- Pre-seed a prior order into emulator; invoke full 9-stage pipeline via `Runner.run_async`; assert `run_summary.exceptions_opened == 1`, `run_summary.orders_created == 0`, ConfirmStage child-LlmAgent stub was never invoked (AsyncMock call count == 0)

### Schema test — extend `tests/unit/test_order_record_schema.py`

- `OrderRecord.schema_version == 3`
- `content_hash` is required (not `Optional`) — `ValidationError` when omitted

### Expected totals

- Current baseline: **323 unit + 10+ integration + 3-case smoke evalset**
- After Track C: **~339 unit (+~16) + ~14 integration (+4) + same evalset**

### `FakeAsyncClient` extension

The unit tests require the fake Firestore client to support chained `where()` calls over 3+ fields. Current fake (per Track A `test_exception_store.py`) supports single-where queries. Extension needed: generalise `where` to accumulate filters as a list, apply them all at `.stream()` time with an in-memory AND predicate. Additive — existing Track A/V tests unaffected. ~30 lines in `tests/unit/conftest.py`.

## Success criteria

1. Re-submitting an identical order email within 90 days produces `ValidationResult.decision == ESCALATE` with `reason` pointing at the prior order.
2. Changing the PO# but keeping the basket identical still produces ESCALATE (content-hash branch).
3. Identical order >90 days later passes validation cleanly.
4. `OrderRecord.content_hash` is present on every persisted order at `schema_version=3`.
5. No regression in the existing 323-test suite.
6. Live smoke: resubmitting the MM Machine fixture against the running emulator + real Gemini + LlamaCloud produces exactly one order + one duplicate exception. `ConfirmStage` draft is not wasted on the duplicate.

## Out of scope (explicit non-goals)

- **SKU-matched hash** — raw SKU used; format-variation dups slip (acceptable).
- **LLM semantic dedup** — overkill for MVP.
- **`exceptions` collection query** — PENDING_CLARIFY from prior sends does not block re-sends.
- **New `RoutingDecision.DUPLICATE` enum** — reusing ESCALATE.
- **Dashboard surfacing** — Track D's concern; dup exceptions appear in the standard exception list, distinguished only by `reason` text.
- **Auto-reject without human review** — ESCALATE to human confirmed as the routing decision.
- **Demo fixture pair** (re-send of MM Machine) — Track Demo owns fixture authoring.
- **Per-customer window tuning** — 90-day constant for MVP; per-customer override is a Phase 2 item.

## Files touched (summary)

| Type | Path | Change |
|---|---|---|
| New | `backend/tools/order_validator/tools/duplicate_check.py` | `compute_content_hash` + `find_duplicate` + `DUPLICATE_WINDOW_DAYS` |
| Modified | `backend/models/order_record.py` | `content_hash: str` field, schema_version 2→3 |
| Modified | `backend/tools/order_validator/validator.py` | preflight short-circuit after customer_resolver |
| Modified | `backend/persistence/coordinator.py` | compute + attach content_hash on AUTO_APPROVE writes |
| Modified | `firebase/firestore.indexes.json` | 2 new composite indexes on `orders` |
| New | `tests/unit/test_duplicate_check.py` | ~11 tests |
| Modified | `tests/unit/test_validator.py` | +2 tests |
| Modified | `tests/unit/test_order_store.py` | +1 test |
| Modified | `tests/unit/test_coordinator.py` | +1 test |
| Modified | `tests/unit/conftest.py` | extend `FakeAsyncClient` for chained-where queries |
| New | `tests/integration/test_duplicate_check_emulator.py` | 3 tests |
| Modified | `tests/integration/test_orchestrator_emulator.py` | +1 dup-path e2e test |
| Modified | `tests/unit/test_order_record_schema.py` | schema v3 + required content_hash |
| Modified | `research/Order-Intake-Sprint-Status.md` | flip §4 dup-detection row, update Built inventory |
| Modified | `Glacis-Order-Intake.md` | flip §4 "Duplicate detection (check #1)" `[Post-MVP]` → `[MVP ✓]` |

## Connections

- `research/Order-Intake-Sprint-Status.md` — §4 row `Duplicate detection (check #1)` flips from `cut per Track V scope` to `[MVP ✓]`
- `research/Order-Intake-Sprint-Decisions.md` — cut-list entry should be marked "reversed 2026-04-24: landed via Track C"
- `Glacis-Order-Intake.md` — §4 `Duplicate detection (check #1)` flips `[Post-MVP]` → `[MVP ✓]`; Phase 2 roadmap bullet for duplicate detection can be struck
- `research/Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Validation-Pipeline.md` — the spec being implemented (check #1)
- Tracks D / A / B / E follow in sequence: D dashboard depends on exception-with-dup-reason surface; A Gmail ingress will stress-test dup detection under real replay; B judge is orthogonal; E evalset expansion gains a dup golden case post-Track-C
