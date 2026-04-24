# Track C — Duplicate Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent shipping the same order twice by adding a preflight duplicate check inside `OrderValidator.validate()` that queries the `orders` collection for a PO#-match OR content-hash-match (scoped by customer + 90-day window, excluding self-retries). On hit, short-circuits to `RoutingDecision.ESCALATE` with `reason="duplicate of <order_id>"`.

**Architecture:** Preflight short-circuit inside the existing validator — runs after `customer_resolver`, before `sku_matcher`/`price_check`/`qty_check`. Binary outcome; does not feed the scorer. Two Firestore queries (PO# and content-hash), OR'd. `OrderRecord` bumps to `schema_version=3` with three new denormalized query-side fields: `customer_id`, `po_number`, `content_hash`. No stage-level changes.

**Tech Stack:** Python 3.13, Pydantic 2.x, `google-cloud-firestore` 2.27.0 (async), pytest + pytest-asyncio, Firestore emulator (integration), ADK `Runner.run_async` (e2e).

**Source spec:** `docs/superpowers/specs/2026-04-24-track-c-duplicate-detection-design.md` (spec amendment `cbcf7ce` — read the amendments block in frontmatter before starting).

---

## File structure

| Path | Responsibility |
|---|---|
| **New** `backend/tools/order_validator/tools/duplicate_check.py` | `compute_content_hash(customer_id, order) → str` + `find_duplicate(client, ...) → Optional[str]` + `DUPLICATE_WINDOW_DAYS = 90` constant. Zero dependencies on other validator tools. |
| **Modified** `backend/models/order_record.py` | Add `customer_id: str`, `po_number: Optional[str] = None`, `content_hash: str` top-level fields. Bump `schema_version` default `2 → 3`. |
| **Modified** `backend/tools/order_validator/validator.py` | Add `source_message_id: str` kwarg to `validate()`. Insert dup-preflight call after `resolve_customer`. On hit: short-circuit with `ValidationResult(decision=ESCALATE, aggregate_confidence=1.0, rationale="duplicate of <id>", customer=<resolved>, lines=[])`. |
| **Modified** `backend/persistence/coordinator.py` | Thread `source_message_id` into `validator.validate`. Populate `customer_id` + `po_number` + `content_hash` on the built `OrderRecord` in the AUTO_APPROVE branch. |
| **Modified** `firebase/firestore.indexes.json` | Two new composite indexes on `orders`. |
| **New** `tests/unit/test_duplicate_check.py` | 11 tests — 5 hash, 6 find_duplicate |
| **Modified** `tests/unit/conftest.py` | Extend `FakeAsyncClient` to support chained `.where()` + `.limit()` + `async-iter stream()` with multi-field AND filtering. |
| **Modified** `tests/unit/test_validator.py` | +2 tests — preflight short-circuit, preflight pass-through |
| **Modified** `tests/unit/test_order_store.py` | +1 test — `content_hash` + `customer_id` + `po_number` round-trip; fix existing fixtures |
| **Modified** `tests/unit/test_coordinator.py` | +2 tests — persisted record carries correct denormalized fields + content_hash matches `compute_content_hash` output |
| **Modified** `tests/unit/test_stage_persist.py` | Fix OrderRecord fixtures to include new required fields |
| **New** `tests/integration/test_duplicate_check_emulator.py` | 3 tests — PO# hit, hash hit, window expiry |
| **Modified** `tests/integration/test_order_store_emulator.py` | Fix OrderRecord fixtures to include new required fields |
| **Modified** `tests/integration/test_orchestrator_emulator.py` | +1 e2e — pre-seed order, assert second run ESCALATES and ConfirmStage stub never invoked |
| **Modified** `research/Order-Intake-Sprint-Status.md` | Flip §4 dup-detection row; extend Built inventory |
| **Modified** `Glacis-Order-Intake.md` | Flip §4 `[Post-MVP]` → `[MVP ✓]`; strike Phase 2 bullet |

---

## Task 1: New `duplicate_check.py` module with `compute_content_hash`

**Files:**
- Create: `backend/tools/order_validator/tools/duplicate_check.py`
- Create: `tests/unit/test_duplicate_check.py`

- [ ] **Step 1.1: Write the failing tests for `compute_content_hash`**

Create `tests/unit/test_duplicate_check.py`:

```python
"""Unit tests for duplicate_check.compute_content_hash.

Spec: docs/superpowers/specs/2026-04-24-track-c-duplicate-detection-design.md
"""
from __future__ import annotations

import pytest

from backend.models.parsed_document import ExtractedOrder, OrderLineItem
from backend.tools.order_validator.tools.duplicate_check import (
    DUPLICATE_WINDOW_DAYS,
    compute_content_hash,
)


def _order(*lines: tuple[str | None, float | None]) -> ExtractedOrder:
    return ExtractedOrder(
        customer_name="Acme",
        po_number="PO-123",
        line_items=[
            OrderLineItem(sku=sku, quantity=qty) for sku, qty in lines
        ],
    )


class TestComputeContentHash:
    def test_deterministic(self):
        order = _order(("SKU-A", 5.0), ("SKU-B", 3.0))
        assert compute_content_hash("CUST-1", order) == compute_content_hash("CUST-1", order)

    def test_order_independent(self):
        a = _order(("SKU-A", 5.0), ("SKU-B", 3.0))
        b = _order(("SKU-B", 3.0), ("SKU-A", 5.0))
        assert compute_content_hash("CUST-1", a) == compute_content_hash("CUST-1", b)

    def test_customer_scoped(self):
        order = _order(("SKU-A", 5.0))
        assert compute_content_hash("CUST-1", order) != compute_content_hash("CUST-2", order)

    def test_strips_whitespace_in_sku(self):
        a = _order(("SKU-A", 5.0))
        b = _order(("  SKU-A  ", 5.0))
        assert compute_content_hash("CUST-1", a) == compute_content_hash("CUST-1", b)

    def test_case_is_not_normalized(self):
        """Documents the trade-off: case-variations slip by content-hash;
        PO# branch is expected to catch them instead."""
        a = _order(("SKU-A", 5.0))
        b = _order(("sku-a", 5.0))
        assert compute_content_hash("CUST-1", a) != compute_content_hash("CUST-1", b)

    def test_none_sku_line_is_skipped(self):
        """Lines with sku=None can't be hashed meaningfully — skipped.
        Order with only a None-sku line hashes same as empty basket."""
        a = _order((None, 5.0))
        b = ExtractedOrder(customer_name="Acme", line_items=[])
        assert compute_content_hash("CUST-1", a) == compute_content_hash("CUST-1", b)

    def test_none_quantity_coerced_to_zero(self):
        a = _order(("SKU-A", None))
        b = _order(("SKU-A", 0.0))
        assert compute_content_hash("CUST-1", a) == compute_content_hash("CUST-1", b)

    def test_returns_64_char_hex_string(self):
        h = compute_content_hash("CUST-1", _order(("SKU-A", 5.0)))
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


def test_window_constant_is_90_days():
    assert DUPLICATE_WINDOW_DAYS == 90
```

- [ ] **Step 1.2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_duplicate_check.py -v`

Expected: `ModuleNotFoundError` on `backend.tools.order_validator.tools.duplicate_check` — or all 9 tests ERROR at import time. Either way, no test passes.

- [ ] **Step 1.3: Create `duplicate_check.py` with `compute_content_hash` + constant**

Create `backend/tools/order_validator/tools/duplicate_check.py`:

```python
"""Duplicate-order preflight check for OrderValidator.

Called as the second step of ``OrderValidator.validate()`` — after
customer resolution, before SKU/price/qty checks. Short-circuits to
``RoutingDecision.ESCALATE`` when the same basket (by PO# OR content
hash) has already landed in ``orders`` for this customer within the
90-day window.

Rationale + design decisions:
docs/superpowers/specs/2026-04-24-track-c-duplicate-detection-design.md
"""
from __future__ import annotations

from hashlib import sha256

from backend.models.parsed_document import ExtractedOrder

DUPLICATE_WINDOW_DAYS = 90


def compute_content_hash(customer_id: str, order: ExtractedOrder) -> str:
    """SHA256 over ``customer_id + sorted [(raw_sku, qty)]``.

    Deterministic and order-independent (shuffling ``order.line_items``
    yields the same hash). Lines where ``sku is None`` are skipped — they
    can't be hashed meaningfully and would otherwise collapse all
    degenerate orders to the same hash. ``quantity is None`` is coerced
    to ``0.0``.

    Uses raw SKU strings from the parsed doc, not sku_matcher output.
    Preserves the preflight-first positioning — dup check runs before
    sku_matcher, saving that work on dups.
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


__all__ = ["DUPLICATE_WINDOW_DAYS", "compute_content_hash"]
```

- [ ] **Step 1.4: Run tests to verify all 9 pass**

Run: `uv run pytest tests/unit/test_duplicate_check.py -v`

Expected: `9 passed`.

- [ ] **Step 1.5: Commit**

```bash
git add backend/tools/order_validator/tools/duplicate_check.py tests/unit/test_duplicate_check.py
git commit -m "feat(track-c): add compute_content_hash for duplicate detection"
```

---

## Task 2: Bump `OrderRecord` to schema v3 with denormalized query fields

**Files:**
- Modify: `backend/models/order_record.py:76-102`
- Modify: `tests/unit/test_order_store.py` (fixture updates + 1 new test)
- Modify: `tests/unit/test_stage_persist.py` (fixture updates)
- Modify: `tests/integration/test_order_store_emulator.py` (fixture updates)

- [ ] **Step 2.1: Write the failing test — schema v3 + required fields**

Append to `tests/unit/test_order_store.py` (find an existing test for reference; add a new class or append at end):

```python
# New test — appended to tests/unit/test_order_store.py

import pytest
from pydantic import ValidationError
from datetime import datetime, timezone

from backend.models.order_record import (
    CustomerSnapshot,
    OrderRecord,
    OrderStatus,
)
from backend.models.master_records import AddressRecord


def _minimal_customer_snapshot() -> CustomerSnapshot:
    return CustomerSnapshot(
        customer_id="CUST-00042",
        name="Acme Corp",
        bill_to=AddressRecord(
            street1="100 Industrial Way",
            city="Dayton",
            state="OH",
            zip="45402",
            country="USA",
        ),
        payment_terms="Net 30",
    )


class TestOrderRecordSchemaV3:
    def test_schema_version_default_is_3(self):
        record = OrderRecord(
            source_message_id="msg-1",
            thread_id="thr-1",
            customer=_minimal_customer_snapshot(),
            customer_id="CUST-00042",
            po_number="PO-123",
            content_hash="a" * 64,
            lines=[],
            order_total=0.0,
            confidence=1.0,
            processed_by_agent_version="track-a-v0.2",
            created_at=datetime.now(timezone.utc),
        )
        assert record.schema_version == 3

    def test_customer_id_is_required(self):
        with pytest.raises(ValidationError) as exc_info:
            OrderRecord(
                source_message_id="msg-1",
                thread_id="thr-1",
                customer=_minimal_customer_snapshot(),
                # customer_id omitted
                po_number="PO-123",
                content_hash="a" * 64,
                lines=[],
                order_total=0.0,
                confidence=1.0,
                processed_by_agent_version="track-a-v0.2",
                created_at=datetime.now(timezone.utc),
            )
        assert "customer_id" in str(exc_info.value)

    def test_content_hash_is_required(self):
        with pytest.raises(ValidationError) as exc_info:
            OrderRecord(
                source_message_id="msg-1",
                thread_id="thr-1",
                customer=_minimal_customer_snapshot(),
                customer_id="CUST-00042",
                po_number="PO-123",
                # content_hash omitted
                lines=[],
                order_total=0.0,
                confidence=1.0,
                processed_by_agent_version="track-a-v0.2",
                created_at=datetime.now(timezone.utc),
            )
        assert "content_hash" in str(exc_info.value)

    def test_po_number_defaults_to_none(self):
        record = OrderRecord(
            source_message_id="msg-1",
            thread_id="thr-1",
            customer=_minimal_customer_snapshot(),
            customer_id="CUST-00042",
            # po_number omitted
            content_hash="a" * 64,
            lines=[],
            order_total=0.0,
            confidence=1.0,
            processed_by_agent_version="track-a-v0.2",
            created_at=datetime.now(timezone.utc),
        )
        assert record.po_number is None
```

- [ ] **Step 2.2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_order_store.py::TestOrderRecordSchemaV3 -v`

Expected: `test_schema_version_default_is_3` fails with `assert 2 == 3`. Other three tests fail because `customer_id` / `content_hash` / `po_number` are `extra="forbid"` rejections, not missing-field errors — that's a valid failure mode (ValidationError on unknown fields).

- [ ] **Step 2.3: Update `OrderRecord` model**

Modify `backend/models/order_record.py` — replace the `OrderRecord` class body (lines 76-102 as of the current head, but use your editor to locate):

```python
class OrderRecord(BaseModel):
    """One persisted order. Firestore doc path: ``orders/{source_message_id}``.

    ``source_message_id`` is the envelope's ``message_id`` — using it as the
    doc id gives us idempotency for free against Pub/Sub redelivery.
    ``thread_id`` propagates from the envelope so clarify-reply threads
    correlate correctly if the order is later revised.

    ``confirmation_body`` is populated post-save by
    :class:`~backend.my_agent.stages.confirm.ConfirmStage` when it
    renders a customer confirmation email. ``None`` until that stage
    runs; stays ``None`` forever for non-AUTO_APPROVE paths.

    Schema v3 (2026-04-24, Track C) adds three denormalized query-side
    fields — duplicated from nested / parsed-doc state so the composite
    Firestore indexes for duplicate detection can hit flat field paths:

    * ``customer_id`` — denormalized from ``customer.customer_id``
    * ``po_number`` — from ``ExtractedOrder.po_number``
    * ``content_hash`` — from
      :func:`backend.tools.order_validator.tools.duplicate_check.compute_content_hash`
    """

    model_config = ConfigDict(extra="forbid")

    source_message_id: str
    thread_id: str
    customer: CustomerSnapshot
    customer_id: str
    po_number: Optional[str] = None
    content_hash: str
    lines: list[OrderLine]
    order_total: float
    confidence: float = Field(..., ge=0.0, le=1.0)
    status: OrderStatus = OrderStatus.PERSISTED
    processed_by_agent_version: str
    confirmation_body: Optional[str] = None
    schema_version: int = 3
    created_at: datetime
```

- [ ] **Step 2.4: Run new schema tests — expect 4 passes**

Run: `uv run pytest tests/unit/test_order_store.py::TestOrderRecordSchemaV3 -v`

Expected: `4 passed`.

- [ ] **Step 2.5: Run full suite to discover which existing fixtures broke**

Run: `uv run pytest tests/unit -x --tb=short 2>&1 | head -80`

Expected: failures in `test_order_store.py` existing tests, `test_stage_persist.py`, and `test_coordinator.py` — all complaining about missing required fields `customer_id` / `content_hash`. List the failing test names.

- [ ] **Step 2.6: Fix `tests/unit/test_order_store.py` fixtures**

Search the file for every `OrderRecord(` call. Add three kwargs to each:
- `customer_id="CUST-00042"` (or whatever matches the surrounding `customer.customer_id`)
- `content_hash="a" * 64` (any valid hex string for fixture purposes)
- Leave `po_number` off or set explicitly as needed per test

Grep first to see all sites:

```bash
grep -n "OrderRecord(" tests/unit/test_order_store.py
```

Patch each one.

- [ ] **Step 2.7: Fix `tests/unit/test_stage_persist.py` fixtures**

Same pattern — grep, patch each `OrderRecord(` construction with new required fields.

- [ ] **Step 2.8: Fix `tests/integration/test_order_store_emulator.py` fixtures**

Same pattern. These tests hit the real emulator but construction is identical.

- [ ] **Step 2.9: Run unit tests again — expect green**

Run: `uv run pytest tests/unit -x --tb=short 2>&1 | tail -20`

Expected: `all passed` (ignore the integration tests — they need the emulator running). Test count should be baseline-323 + 9 (Task 1) + 4 (Task 2) = 336.

- [ ] **Step 2.10: Commit**

```bash
git add backend/models/order_record.py tests/unit/test_order_store.py tests/unit/test_stage_persist.py tests/integration/test_order_store_emulator.py
git commit -m "feat(track-c): bump OrderRecord to schema v3 with denormalized query fields"
```

---

## Task 3: Extend `FakeAsyncClient` for chained-where queries

**Files:**
- Modify: `tests/unit/conftest.py`

**Context:** the current `FakeAsyncClient` in `tests/unit/conftest.py` supports single-field `.where(...)` queries (per Track A's `test_exception_store.py`). Task 4's `find_duplicate` tests need AND-composition over 3+ fields. Extension is additive; existing single-where tests must continue to pass.

- [ ] **Step 3.1: Read the existing fake to find extension points**

Run: `grep -n "class FakeAsync\|def where\|def stream\|def limit" tests/unit/conftest.py`

Note the shape of the existing query chain. The goal is to let `.where(...)` return a builder that accumulates filters, then `.stream()` applies all accumulated filters as a single AND predicate.

- [ ] **Step 3.2: Write a failing test for multi-where**

Add to `tests/unit/test_duplicate_check.py` at the end:

```python
import pytest
from tests.unit.conftest import FakeAsyncClient  # adjust import if local


@pytest.mark.asyncio
class TestFakeAsyncClientMultiWhere:
    """Guard: FakeAsyncClient must support 2+ chained .where() calls as AND.

    Required for Task 4 find_duplicate tests that combine
    customer_id + content_hash + created_at + source_message_id.
    """

    async def test_two_where_filters_and(self):
        client = FakeAsyncClient()
        await client.collection("orders").document("d1").set(
            {"customer_id": "CUST-1", "po_number": "PO-1"}
        )
        await client.collection("orders").document("d2").set(
            {"customer_id": "CUST-1", "po_number": "PO-2"}
        )
        await client.collection("orders").document("d3").set(
            {"customer_id": "CUST-2", "po_number": "PO-1"}
        )

        # Must return only d1 (matches both filters)
        query = (
            client.collection("orders")
            .where("customer_id", "==", "CUST-1")
            .where("po_number", "==", "PO-1")
        )
        docs = [doc async for doc in query.stream()]
        assert len(docs) == 1
        assert docs[0].id == "d1"

    async def test_three_where_filters_and(self):
        client = FakeAsyncClient()
        await client.collection("orders").document("d1").set(
            {"customer_id": "CUST-1", "po_number": "PO-1", "status": "persisted"}
        )
        await client.collection("orders").document("d2").set(
            {"customer_id": "CUST-1", "po_number": "PO-1", "status": "draft"}
        )

        query = (
            client.collection("orders")
            .where("customer_id", "==", "CUST-1")
            .where("po_number", "==", "PO-1")
            .where("status", "==", "persisted")
        )
        docs = [doc async for doc in query.stream()]
        assert len(docs) == 1
        assert docs[0].id == "d1"
```

- [ ] **Step 3.3: Run test to verify failure**

Run: `uv run pytest tests/unit/test_duplicate_check.py::TestFakeAsyncClientMultiWhere -v`

Expected: failure — current fake either ignores second `.where()` or crashes.

- [ ] **Step 3.4: Extend `FakeAsyncClient` in conftest.py**

The exact patch depends on the current fake's structure. Pattern: change the query object to carry a `list[tuple[field, op, value]]` instead of a single filter. Example shape:

```python
# In tests/unit/conftest.py — adjust to existing class names

class _FakeQuery:
    def __init__(self, collection: "_FakeCollection", filters=None, limit=None):
        self._collection = collection
        self._filters: list[tuple[str, str, object]] = filters or []
        self._limit = limit

    def where(self, field: str, op: str, value):
        # Returns a NEW query with the filter appended — immutable chain
        return _FakeQuery(
            self._collection,
            filters=[*self._filters, (field, op, value)],
            limit=self._limit,
        )

    def limit(self, n: int):
        return _FakeQuery(self._collection, filters=self._filters, limit=n)

    async def stream(self):
        count = 0
        for doc_id, data in self._collection._docs.items():
            if all(_matches(data, f, o, v) for f, o, v in self._filters):
                yield _FakeDocSnapshot(doc_id, data)
                count += 1
                if self._limit is not None and count >= self._limit:
                    return


def _matches(data: dict, field: str, op: str, value) -> bool:
    actual = data.get(field)
    if op == "==":
        return actual == value
    if op == "!=":
        return actual != value
    if op == ">=":
        return actual is not None and actual >= value
    if op == "<=":
        return actual is not None and actual <= value
    if op == ">":
        return actual is not None and actual > value
    if op == "<":
        return actual is not None and actual < value
    raise NotImplementedError(f"FakeAsyncClient: op {op!r} not supported")
```

**Also** — production code uses `google.cloud.firestore_v1.base_query.FieldFilter` for the `.where(filter=FieldFilter("field", "==", value))` kwarg form. The fake's `.where` currently takes positional (`field, op, value`). Task 4 production code must match the fake's API — use positional form there. (Or extend the fake to accept either.)

**Recommended:** extend the fake to accept both forms:

```python
def where(self, *args, filter=None):
    if filter is not None:
        field, op, value = filter.field_path, filter.op_string, filter.value
    else:
        field, op, value = args
    return _FakeQuery(
        self._collection,
        filters=[*self._filters, (field, op, value)],
        limit=self._limit,
    )
```

(If the real `FieldFilter` doesn't expose `.field_path` / `.op_string` / `.value` as attrs, use positional form in production code and test. The tests already use the positional form above — stick with it for consistency.)

- [ ] **Step 3.5: Run the two new tests — expect pass**

Run: `uv run pytest tests/unit/test_duplicate_check.py::TestFakeAsyncClientMultiWhere -v`

Expected: `2 passed`.

- [ ] **Step 3.6: Run the full existing suite — no regressions**

Run: `uv run pytest tests/unit -x --tb=short 2>&1 | tail -10`

Expected: all green. If any Track-A test fails, the fake extension broke an existing pattern — revert and try a more conservative extension.

- [ ] **Step 3.7: Commit**

```bash
git add tests/unit/conftest.py tests/unit/test_duplicate_check.py
git commit -m "test(track-c): extend FakeAsyncClient for chained-where queries"
```

---

## Task 4: `find_duplicate` function + unit tests

**Files:**
- Modify: `backend/tools/order_validator/tools/duplicate_check.py`
- Modify: `tests/unit/test_duplicate_check.py`

- [ ] **Step 4.1: Write the 6 failing tests for `find_duplicate`**

Append to `tests/unit/test_duplicate_check.py`:

```python
from datetime import datetime, timedelta, timezone
from typing import Callable

from backend.tools.order_validator.tools.duplicate_check import (
    find_duplicate,
)


def _fixed_clock(when: datetime) -> Callable[[], datetime]:
    return lambda: when


async def _seed_order(
    client: "FakeAsyncClient",
    *,
    doc_id: str,
    customer_id: str,
    po_number: str | None,
    content_hash: str,
    source_message_id: str,
    created_at: datetime,
):
    await client.collection("orders").document(doc_id).set(
        {
            "customer_id": customer_id,
            "po_number": po_number,
            "content_hash": content_hash,
            "source_message_id": source_message_id,
            "created_at": created_at,
        }
    )


NOW = datetime(2026, 4, 24, 12, 0, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
class TestFindDuplicate:
    async def test_returns_none_when_no_prior_orders(self, fake_client):
        result = await find_duplicate(
            fake_client,
            customer_id="CUST-1",
            order=_order(("SKU-A", 5.0)),
            source_message_id="msg-current",
            po_number="PO-123",
            clock=_fixed_clock(NOW),
        )
        assert result is None

    async def test_returns_order_id_on_po_number_hit(self, fake_client):
        await _seed_order(
            fake_client,
            doc_id="ORD-abc123",
            customer_id="CUST-1",
            po_number="PO-123",
            content_hash="different_hash",
            source_message_id="msg-prior",
            created_at=NOW - timedelta(days=5),
        )

        result = await find_duplicate(
            fake_client,
            customer_id="CUST-1",
            order=_order(("SKU-X", 1.0)),  # different basket; PO# still matches
            source_message_id="msg-current",
            po_number="PO-123",
            clock=_fixed_clock(NOW),
        )
        assert result == "ORD-abc123"

    async def test_returns_order_id_on_content_hash_hit_when_po_absent(self, fake_client):
        order = _order(("SKU-A", 5.0))
        hash_val = compute_content_hash("CUST-1", order)
        await _seed_order(
            fake_client,
            doc_id="ORD-def456",
            customer_id="CUST-1",
            po_number=None,
            content_hash=hash_val,
            source_message_id="msg-prior",
            created_at=NOW - timedelta(days=5),
        )

        result = await find_duplicate(
            fake_client,
            customer_id="CUST-1",
            order=order,
            source_message_id="msg-current",
            po_number=None,  # no PO# on incoming
            clock=_fixed_clock(NOW),
        )
        assert result == "ORD-def456"

    async def test_returns_order_id_on_hash_hit_when_po_differs(self, fake_client):
        order = _order(("SKU-A", 5.0))
        hash_val = compute_content_hash("CUST-1", order)
        await _seed_order(
            fake_client,
            doc_id="ORD-ghi789",
            customer_id="CUST-1",
            po_number="PO-OLD",
            content_hash=hash_val,
            source_message_id="msg-prior",
            created_at=NOW - timedelta(days=5),
        )

        # Incoming has a DIFFERENT PO# but same basket → content-hash fires
        result = await find_duplicate(
            fake_client,
            customer_id="CUST-1",
            order=order,
            source_message_id="msg-current",
            po_number="PO-NEW",
            clock=_fixed_clock(NOW),
        )
        assert result == "ORD-ghi789"

    async def test_excludes_self_match_via_source_message_id(self, fake_client):
        """A retry of the same message must NOT flag its own prior persist."""
        order = _order(("SKU-A", 5.0))
        hash_val = compute_content_hash("CUST-1", order)
        await _seed_order(
            fake_client,
            doc_id="ORD-own",
            customer_id="CUST-1",
            po_number="PO-123",
            content_hash=hash_val,
            source_message_id="msg-same",  # identical to current
            created_at=NOW - timedelta(seconds=1),
        )

        result = await find_duplicate(
            fake_client,
            customer_id="CUST-1",
            order=order,
            source_message_id="msg-same",  # same id
            po_number="PO-123",
            clock=_fixed_clock(NOW),
        )
        assert result is None

    async def test_respects_90_day_window(self, fake_client):
        order = _order(("SKU-A", 5.0))
        hash_val = compute_content_hash("CUST-1", order)
        await _seed_order(
            fake_client,
            doc_id="ORD-stale",
            customer_id="CUST-1",
            po_number="PO-123",
            content_hash=hash_val,
            source_message_id="msg-prior",
            created_at=NOW - timedelta(days=91),  # outside window
        )

        result = await find_duplicate(
            fake_client,
            customer_id="CUST-1",
            order=order,
            source_message_id="msg-current",
            po_number="PO-123",
            clock=_fixed_clock(NOW),
        )
        assert result is None
```

You'll also need a `fake_client` pytest fixture in `tests/unit/conftest.py` if one doesn't exist — pattern:

```python
# In tests/unit/conftest.py
@pytest.fixture
def fake_client() -> FakeAsyncClient:
    return FakeAsyncClient()
```

(Check conftest first — one may already exist under a different name like `empty_repo` that wraps a `FakeAsyncClient`.)

- [ ] **Step 4.2: Run tests to verify 6 fail**

Run: `uv run pytest tests/unit/test_duplicate_check.py::TestFindDuplicate -v`

Expected: `ImportError` on `find_duplicate` — or all 6 fail at import.

- [ ] **Step 4.3: Implement `find_duplicate` in `duplicate_check.py`**

Append to `backend/tools/order_validator/tools/duplicate_check.py`:

```python
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from google.cloud.firestore_v1.async_client import AsyncClient


async def find_duplicate(
    client: AsyncClient,
    *,
    customer_id: str,
    order: ExtractedOrder,
    source_message_id: str,
    po_number: Optional[str],
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> Optional[str]:
    """Return the existing order id if a duplicate is found in window.

    OR-combines two independent signals against the ``orders`` collection:

    1. PO# match (only when ``po_number`` is not None)
    2. Content-hash match (always)

    Both queries are scoped by ``customer_id`` + ``created_at >= cutoff``,
    where ``cutoff = clock() - DUPLICATE_WINDOW_DAYS``, and both exclude
    self-matches via ``source_message_id != <current>``.

    Returns the first matching doc id (Firestore order is arbitrary;
    for the purpose of "this is a dup" any match suffices). Returns
    ``None`` when no match.

    Exceptions propagate — a Firestore outage must fail the whole run
    rather than silently let a dup through.
    """
    cutoff = clock() - timedelta(days=DUPLICATE_WINDOW_DAYS)
    orders = client.collection("orders")

    # PO# branch — skip if no PO# on incoming
    if po_number is not None:
        q = (
            orders
            .where("customer_id", "==", customer_id)
            .where("po_number", "==", po_number)
            .where("created_at", ">=", cutoff)
            .where("source_message_id", "!=", source_message_id)
            .limit(1)
        )
        async for doc in q.stream():
            return doc.id  # first hit wins

    # Content-hash branch — always runs
    content_hash = compute_content_hash(customer_id, order)
    q = (
        orders
        .where("customer_id", "==", customer_id)
        .where("content_hash", "==", content_hash)
        .where("created_at", ">=", cutoff)
        .where("source_message_id", "!=", source_message_id)
        .limit(1)
    )
    async for doc in q.stream():
        return doc.id

    return None


__all__ = [
    "DUPLICATE_WINDOW_DAYS",
    "compute_content_hash",
    "find_duplicate",
]
```

Note: the `.where` calls use **positional** form (matching the `FakeAsyncClient` extension from Task 3, and supported by the real async Firestore client). If the real client at the emulator rejects positional and requires `FieldFilter`, swap at Task 8 (integration tests). The unit tests will pass either way because the fake accepts both.

- [ ] **Step 4.4: Run the 6 tests — expect pass**

Run: `uv run pytest tests/unit/test_duplicate_check.py::TestFindDuplicate -v`

Expected: `6 passed`.

- [ ] **Step 4.5: Run full unit suite — no regressions**

Run: `uv run pytest tests/unit -x --tb=short 2>&1 | tail -5`

Expected: all green. Test count baseline + 9 (Task 1) + 4 (Task 2) + 2 (Task 3 fake) + 6 (Task 4) = baseline + 21 passing tests for Track C so far.

- [ ] **Step 4.6: Commit**

```bash
git add backend/tools/order_validator/tools/duplicate_check.py tests/unit/test_duplicate_check.py tests/unit/conftest.py
git commit -m "feat(track-c): add find_duplicate function with PO# and hash branches"
```

---

## Task 5: Preflight short-circuit in `OrderValidator.validate`

**Files:**
- Modify: `backend/tools/order_validator/validator.py`
- Modify: `tests/unit/test_validator.py` (+2 tests)

- [ ] **Step 5.1: Read existing test_validator.py for the fixture pattern**

Run: `uv run pytest tests/unit/test_validator.py --collect-only -q 2>&1 | head -20`

Note how the current `OrderValidator` is instantiated in tests (what `repo` / fakes are passed). Task 5's 2 new tests must follow the same pattern.

- [ ] **Step 5.2: Write the 2 failing tests**

Append to `tests/unit/test_validator.py`:

```python
from unittest.mock import AsyncMock, patch

from backend.models.parsed_document import ExtractedOrder, OrderLineItem
from backend.models.validation_result import RoutingDecision


@pytest.mark.asyncio
class TestValidatorDuplicatePreflight:
    async def test_short_circuits_on_duplicate_hit(self, seeded_repo):
        """When find_duplicate returns an id, validator returns ESCALATE
        with confidence=1.0 and rationale='duplicate of <id>', and does
        NOT call sku_matcher / price_check / qty_check."""
        validator = OrderValidator(seeded_repo)
        order = ExtractedOrder(
            customer_name="Acme",
            po_number="PO-123",
            line_items=[OrderLineItem(sku="WIDGET-A", quantity=5.0)],
        )

        with (
            patch(
                "backend.tools.order_validator.validator.find_duplicate",
                new_callable=AsyncMock,
                return_value="ORD-existing-xyz",
            ) as mock_find_dup,
            patch(
                "backend.tools.order_validator.validator.match_sku",
                new_callable=AsyncMock,
            ) as mock_sku,
        ):
            result = await validator.validate(
                order, source_message_id="msg-current"
            )

            assert result.decision == RoutingDecision.ESCALATE
            assert result.aggregate_confidence == 1.0
            assert "ORD-existing-xyz" in result.rationale
            assert "duplicate" in result.rationale.lower()
            assert result.lines == []
            mock_find_dup.assert_awaited_once()
            mock_sku.assert_not_awaited()

    async def test_proceeds_past_preflight_when_no_dup(self, seeded_repo):
        """When find_duplicate returns None, validator runs full ladder."""
        validator = OrderValidator(seeded_repo)
        order = ExtractedOrder(
            customer_name="Acme",
            po_number="PO-fresh",
            line_items=[OrderLineItem(sku="WIDGET-A", quantity=5.0)],
        )

        with patch(
            "backend.tools.order_validator.validator.find_duplicate",
            new_callable=AsyncMock,
            return_value=None,
        ) as mock_find_dup:
            result = await validator.validate(
                order, source_message_id="msg-current"
            )

            # decision is whatever the ladder produces for this order — the
            # important thing is that dup check did NOT short-circuit
            mock_find_dup.assert_awaited_once()
            assert result.decision in (
                RoutingDecision.AUTO_APPROVE,
                RoutingDecision.CLARIFY,
                RoutingDecision.ESCALATE,  # could still escalate for other reasons
            )
            # Lines were populated (proof the ladder ran)
            assert len(result.lines) == 1
```

Note: `seeded_repo` must be an existing fixture; check conftest. If it's called something else (`in_memory_repo`, `repo`, etc.), swap the name.

- [ ] **Step 5.3: Run — expect failure**

Run: `uv run pytest tests/unit/test_validator.py::TestValidatorDuplicatePreflight -v`

Expected: `validate()` signature doesn't accept `source_message_id` kwarg → `TypeError` or unexpected-keyword-argument.

- [ ] **Step 5.4: Patch `validator.py` — add kwarg + preflight call**

Modify `backend/tools/order_validator/validator.py`:

1. At the top, add import:

```python
from backend.tools.order_validator.tools.duplicate_check import find_duplicate
```

2. Change `validate` signature + add preflight after `resolve_customer`:

```python
async def validate(
    self,
    order: ExtractedOrder,
    *,
    source_message_id: str,
) -> ValidationResult:
    customer = await resolve_customer(order, self._repo)

    # ── Duplicate preflight ────────────────────────────────────────
    # Runs only when customer is resolved — unresolved customers
    # already ESCALATE below via the existing path.
    if customer is not None:
        existing_id = await find_duplicate(
            self._repo.firestore_client,  # see note below
            customer_id=customer.customer_id,
            order=order,
            source_message_id=source_message_id,
            po_number=order.po_number,
        )
        if existing_id is not None:
            _log.info(
                "duplicate_detected",
                customer_id=customer.customer_id,
                existing_order_id=existing_id,
                source_message_id=source_message_id,
            )
            return ValidationResult(
                customer=customer,
                lines=[],
                aggregate_confidence=1.0,
                decision=RoutingDecision.ESCALATE,
                rationale=f"duplicate of {existing_id}",
            )

    # ── Existing ladder (unchanged) ────────────────────────────────
    lines: list[LineItemValidation] = []
    for idx, line in enumerate(order.line_items):
        ...  # existing body
```

**Important note on `self._repo.firestore_client`:** `OrderValidator` currently holds only a `MasterDataRepo`. `find_duplicate` needs an async Firestore client. Two options:

(a) **Expose the client on `MasterDataRepo`** — add a property `firestore_client -> AsyncClient` that returns the repo's underlying client.

(b) **Inject the client into `OrderValidator`** — add a second constructor arg `firestore_client: AsyncClient`.

**Check `backend/tools/order_validator/tools/master_data_repo.py` and `firestore_client.py` to see which is cleaner.** If `MasterDataRepo` already wraps an `AsyncClient`, option (a) is one line; if it takes primitives, option (b) is cleaner.

- [ ] **Step 5.5: Read `MasterDataRepo` to pick (a) vs (b)**

Run: `grep -n "class MasterDataRepo\|def __init__\|self\._client\|self\._firestore" backend/tools/order_validator/tools/master_data_repo.py`

- If `MasterDataRepo` already holds an `AsyncClient` attribute: pick (a) — add `@property firestore_client`.
- Else: pick (b) — add `firestore_client: AsyncClient` param to `OrderValidator.__init__`.

- [ ] **Step 5.6: Apply chosen plumbing option**

**Option (a) patch** — in `master_data_repo.py`:

```python
@property
def firestore_client(self) -> AsyncClient:
    """Exposed for Track C duplicate_check.find_duplicate."""
    return self._client  # or whatever the attribute is
```

**Option (b) patch** — in `validator.py`:

```python
def __init__(
    self,
    repo: MasterDataRepo,
    firestore_client: AsyncClient,
) -> None:
    self._repo = repo
    self._firestore_client = firestore_client
```

Update production construction site (`backend/my_agent/agent.py:_build_default_root_agent`) to pass the shared client through.

- [ ] **Step 5.7: Update `validator.py` preflight call to use the chosen accessor**

Either `self._repo.firestore_client` (option a) or `self._firestore_client` (option b).

- [ ] **Step 5.8: Run the 2 new tests + existing validator tests**

Run: `uv run pytest tests/unit/test_validator.py -v`

Expected: new 2 tests pass; existing tests may fail on `validate()` signature change. Fix existing tests by passing `source_message_id="test-msg"` (or similar) to every `validator.validate()` call.

- [ ] **Step 5.9: Grep + patch all `validator.validate(` callers across tests**

```bash
grep -rn "validator\.validate(\|\.validate(order" tests/
```

Add `source_message_id=...` to every call. Stage-level tests (`tests/unit/test_stage_validate.py`) may pass an AsyncMock validator, in which case the signature is duck-typed — but the ValidateStage production code in `backend/my_agent/stages/validate.py` must also be updated to thread `source_message_id` through. Check:

```bash
grep -n "validator\.validate\|self\._validator\.validate" backend/my_agent/stages/validate.py
```

Patch `ValidateStage` to pull `source_message_id` from `state["envelope"]["message_id"]` and pass to each `validator.validate` call.

- [ ] **Step 5.10: Run full unit suite — expect all green**

Run: `uv run pytest tests/unit -x --tb=short 2>&1 | tail -10`

Expected: all green.

- [ ] **Step 5.11: Commit**

```bash
git add backend/tools/order_validator/validator.py backend/tools/order_validator/tools/master_data_repo.py backend/my_agent/stages/validate.py backend/my_agent/agent.py tests/unit/test_validator.py tests/unit/test_stage_validate.py
git commit -m "feat(track-c): validator preflights find_duplicate, short-circuits on hit"
```

---

## Task 6: Coordinator populates denormalized fields on OrderRecord

**Files:**
- Modify: `backend/persistence/coordinator.py`
- Modify: `tests/unit/test_coordinator.py` (+2 tests)

- [ ] **Step 6.1: Read current `IntakeCoordinator.process` OrderRecord construction**

Run: `grep -n "OrderRecord(" backend/persistence/coordinator.py`

Identify the single `OrderRecord(...)` call inside the AUTO_APPROVE branch. Read the surrounding code to see what vars are in scope (`customer`, `extracted_order`, etc.).

- [ ] **Step 6.2: Write the 2 failing tests**

Append to `tests/unit/test_coordinator.py`:

```python
from backend.tools.order_validator.tools.duplicate_check import compute_content_hash


@pytest.mark.asyncio
class TestCoordinatorPopulatesDenormalizedFields:
    async def test_order_record_carries_customer_id_and_po_number(
        self, coordinator_with_fakes  # use existing fixture
    ):
        # Build a ParsedDocument that will AUTO_APPROVE on the seeded data
        # (re-use the existing test harness helpers for this)
        ...  # call coordinator.process(...)
        # assert the returned ProcessResult's order carries:
        #   order.customer_id == <resolved customer_id>
        #   order.po_number   == parsed_doc.sub_documents[0].po_number

    async def test_order_record_content_hash_matches_compute_function(
        self, coordinator_with_fakes
    ):
        ...  # call coordinator.process(...)
        # expected_hash = compute_content_hash(customer_id, extracted_order)
        # assert result.order.content_hash == expected_hash
```

Flesh these out by mirroring the closest existing `test_coordinator.py` AUTO_APPROVE test. You need access to:
- The parsed doc that was fed in → `extracted_order = parsed_doc.sub_documents[0]`
- The resolved customer id (seeded in the repo)

Both tests can share a helper that runs `coordinator.process(...)` once and asserts both fields.

- [ ] **Step 6.3: Run — expect failure**

Run: `uv run pytest tests/unit/test_coordinator.py::TestCoordinatorPopulatesDenormalizedFields -v`

Expected: failure — OrderRecord is currently constructed without `customer_id`/`po_number`/`content_hash`, which either raises `ValidationError` (if Task 2 bumped required fields) or returns a record without them.

Note: if Task 2 made these fields required, the existing AUTO_APPROVE coordinator tests should ALSO be failing right now. If they're not, it means the coordinator is not being hit by those tests (e.g., tests pass a `precomputed_validation` that routes around the OrderRecord construction). Either way, this task makes both sets green.

- [ ] **Step 6.4: Patch `coordinator.py` AUTO_APPROVE branch**

Modify the `OrderRecord(...)` construction inside `IntakeCoordinator.process`:

```python
from backend.tools.order_validator.tools.duplicate_check import compute_content_hash

# Inside the AUTO_APPROVE branch, find:
order_record = OrderRecord(
    source_message_id=envelope.message_id,
    thread_id=envelope.thread_id,
    customer=customer_snapshot,
    # ADD three new kwargs:
    customer_id=customer.customer_id,
    po_number=extracted_order.po_number,
    content_hash=compute_content_hash(
        customer.customer_id, extracted_order
    ),
    # rest unchanged:
    lines=order_lines,
    order_total=order_total,
    confidence=validation.aggregate_confidence,
    processed_by_agent_version=self._agent_version,
    created_at=datetime.now(timezone.utc),
    # schema_version defaults to 3 — don't pass
)
```

- [ ] **Step 6.5: Thread `source_message_id` into `validator.validate` call inside coordinator**

Find `self._validator.validate(extracted_order` in coordinator.py — if this call is the one that feeds `validation` above, it needs `source_message_id=envelope.message_id` passed through. (Track A post-F5 audit already has a `precomputed_validation` fast-path; when `precomputed_validation` is None and coordinator must call validate itself, use this signature change.)

- [ ] **Step 6.6: Run new + existing coordinator tests — expect all pass**

Run: `uv run pytest tests/unit/test_coordinator.py -v`

Expected: all green, including new 2 tests.

- [ ] **Step 6.7: Run full unit suite — no regressions**

Run: `uv run pytest tests/unit -x --tb=short 2>&1 | tail -10`

Expected: all green.

- [ ] **Step 6.8: Commit**

```bash
git add backend/persistence/coordinator.py tests/unit/test_coordinator.py
git commit -m "feat(track-c): coordinator populates customer_id/po_number/content_hash on save"
```

---

## Task 7: Firestore composite indexes

**Files:**
- Modify: `firebase/firestore.indexes.json`

- [ ] **Step 7.1: Read current indexes**

Run: `cat firebase/firestore.indexes.json`

Note the structure. The existing composite index for `exceptions` (find_pending_clarify) is the pattern.

- [ ] **Step 7.2: Add two new indexes**

Add to the `indexes` array:

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

- [ ] **Step 7.3: Validate JSON**

Run: `uv run python -c "import json; json.load(open('firebase/firestore.indexes.json'))"`

Expected: no output (valid JSON).

- [ ] **Step 7.4: Commit**

```bash
git add firebase/firestore.indexes.json
git commit -m "feat(track-c): add composite indexes for duplicate-check queries"
```

---

## Task 8: Integration tests against the real Firestore emulator

**Files:**
- Create: `tests/integration/test_duplicate_check_emulator.py`

**Prerequisite:** the Firestore emulator is running. Run `firebase emulators:start --only firestore` in a separate terminal, or `uv run pytest -m firestore_emulator` is configured to auto-skip when `FIRESTORE_EMULATOR_HOST` isn't set.

- [ ] **Step 8.1: Write the 3 integration tests**

Create `tests/integration/test_duplicate_check_emulator.py`:

```python
"""Emulator-backed integration tests for Track C duplicate detection.

Exercises the real Firestore async client + the real composite
indexes + the production find_duplicate function. Guards against
compound-query limitations (!= + >=) that the FakeAsyncClient
cannot catch.

Requires FIRESTORE_EMULATOR_HOST to be set and firestore emulator
running. Tests marked @pytest.mark.firestore_emulator auto-skip
otherwise (existing pytest config).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from google.cloud.firestore_v1.async_client import AsyncClient

from backend.models.parsed_document import ExtractedOrder, OrderLineItem
from backend.tools.order_validator.tools.duplicate_check import (
    compute_content_hash,
    find_duplicate,
)

pytestmark = [pytest.mark.firestore_emulator, pytest.mark.asyncio]


@pytest.fixture
async def async_client():
    """Fresh async Firestore client against emulator, with cleanup."""
    # Existing test helpers may already provide one — check conftest
    # first and swap this in if so.
    client = AsyncClient(project="demo-order-intake-local")
    yield client
    # Best-effort cleanup of the orders collection
    async for doc in client.collection("orders").stream():
        await doc.reference.delete()


async def _seed(client: AsyncClient, **fields) -> str:
    ref = client.collection("orders").document()
    await ref.set(fields)
    return ref.id


class TestDuplicateCheckAgainstEmulator:
    async def test_po_number_hit_across_emulator(self, async_client):
        now = datetime.now(timezone.utc)
        prior_id = await _seed(
            async_client,
            customer_id="CUST-1",
            po_number="PO-ABC",
            content_hash="unrelated",
            source_message_id="msg-prior",
            created_at=now - timedelta(days=5),
        )

        result = await find_duplicate(
            async_client,
            customer_id="CUST-1",
            order=ExtractedOrder(
                customer_name="Acme",
                line_items=[OrderLineItem(sku="SKU-X", quantity=1.0)],
            ),
            source_message_id="msg-current",
            po_number="PO-ABC",
        )
        assert result == prior_id

    async def test_content_hash_hit_across_emulator(self, async_client):
        now = datetime.now(timezone.utc)
        order = ExtractedOrder(
            customer_name="Acme",
            line_items=[OrderLineItem(sku="SKU-A", quantity=5.0)],
        )
        expected = compute_content_hash("CUST-1", order)

        prior_id = await _seed(
            async_client,
            customer_id="CUST-1",
            po_number=None,
            content_hash=expected,
            source_message_id="msg-prior",
            created_at=now - timedelta(days=5),
        )

        result = await find_duplicate(
            async_client,
            customer_id="CUST-1",
            order=order,
            source_message_id="msg-current",
            po_number=None,
        )
        assert result == prior_id

    async def test_window_expiry_across_emulator(self, async_client):
        now = datetime.now(timezone.utc)
        order = ExtractedOrder(
            customer_name="Acme",
            line_items=[OrderLineItem(sku="SKU-A", quantity=5.0)],
        )
        expected = compute_content_hash("CUST-1", order)

        # Seeded 91 days ago — outside window
        await _seed(
            async_client,
            customer_id="CUST-1",
            po_number="PO-OLD",
            content_hash=expected,
            source_message_id="msg-old",
            created_at=now - timedelta(days=91),
        )

        result = await find_duplicate(
            async_client,
            customer_id="CUST-1",
            order=order,
            source_message_id="msg-current",
            po_number="PO-OLD",
        )
        assert result is None
```

- [ ] **Step 8.2: Start the emulator + set env**

In a separate terminal:
```bash
firebase emulators:start --only firestore
```
Then in the test terminal:
```bash
export FIRESTORE_EMULATOR_HOST=localhost:8080
```

- [ ] **Step 8.3: Run the 3 tests**

Run: `uv run pytest tests/integration/test_duplicate_check_emulator.py -v`

Expected outcome A — **all 3 pass**: `find_duplicate`'s compound query works against the real emulator. Proceed to Task 9.

Expected outcome B — **Firestore rejects the compound query** (`400 INVALID_ARGUMENT` on `!= + >=` combination, or an index-required error even with indexes present). Fall back to splitting the query:

```python
# In find_duplicate — replace the 4-where query with:
# 1. Run a query WITHOUT the source_message_id filter
# 2. Filter self-matches in Python after fetching

q = (
    orders
    .where("customer_id", "==", customer_id)
    .where("po_number", "==", po_number)
    .where("created_at", ">=", cutoff)
    .limit(10)
)
async for doc in q.stream():
    if doc.get("source_message_id") != source_message_id:
        return doc.id
```

Apply the same pattern to the content-hash branch. This is the spec's flagged fallback — document the decision in a comment citing this task.

- [ ] **Step 8.4: Re-run all 3 tests — expect pass**

Run: `uv run pytest tests/integration/test_duplicate_check_emulator.py -v`

Expected: `3 passed`.

- [ ] **Step 8.5: Re-run unit suite — fallback (if applied) must not break unit tests**

Run: `uv run pytest tests/unit/test_duplicate_check.py -v`

Expected: all green. If the fallback broke a unit test (because FakeAsyncClient doesn't support `.limit(10)` with client-side filtering the same way), patch the unit test helper accordingly.

- [ ] **Step 8.6: Commit**

```bash
git add tests/integration/test_duplicate_check_emulator.py backend/tools/order_validator/tools/duplicate_check.py
git commit -m "test(track-c): add emulator integration tests for find_duplicate"
```

---

## Task 9: End-to-end orchestrator test

**Files:**
- Modify: `tests/integration/test_orchestrator_emulator.py` (+1 test)

- [ ] **Step 9.1: Read existing orchestrator integration test shape**

Run: `grep -n "^class\|^def\|def test_" tests/integration/test_orchestrator_emulator.py | head -30`

Note: pre-existing AUTO_APPROVE happy path + ConfirmStage verification pattern; reuse its scaffolding.

- [ ] **Step 9.2: Write the failing e2e test**

Append to `tests/integration/test_orchestrator_emulator.py`:

```python
async def test_duplicate_submission_escalates_and_skips_confirmation(
    emulator_client,
    seeded_master_data,
    patterson_fixture_env,  # or whatever the existing happy-path fixture env is
):
    """Pre-seed an order for Patterson; resubmit same email; assert:
      - run_summary.exceptions_opened == 1
      - run_summary.orders_created == 0
      - ConfirmStage child-LlmAgent stub was never called
      - The persisted exception's reason contains 'duplicate of'
    """
    # 1. Seed a prior order using the existing AUTO_APPROVE fixture path
    #    — run the pipeline once to land the first order
    first_result = await _run_pipeline_for_fixture(
        patterson_fixture_env, confirm_stub=AsyncMock()
    )
    assert first_result.run_summary.orders_created == 1

    # 2. Resubmit exactly the same fixture via a NEW envelope
    #    (different source_message_id, same basket + PO#)
    confirm_stub = AsyncMock()  # must NOT be called this time
    second_result = await _run_pipeline_for_fixture(
        patterson_fixture_env,
        confirm_stub=confirm_stub,
        message_id_override="msg-resubmit-different-id",
    )

    assert second_result.run_summary.orders_created == 0
    assert second_result.run_summary.exceptions_opened == 1
    confirm_stub.assert_not_called()

    # Pull the exception from Firestore, assert reason
    # (use the same exception-fetch pattern as existing ESCALATE tests)
    ...
```

The exact fixture names (`emulator_client`, `seeded_master_data`, `patterson_fixture_env`) depend on what the file already has. Grep for existing happy-path tests first and mirror their setup exactly — the new test differs only in running the pipeline twice and asserting the second run's outcome.

- [ ] **Step 9.3: Run — expect failure**

Run: `uv run pytest tests/integration/test_orchestrator_emulator.py::test_duplicate_submission_escalates_and_skips_confirmation -v`

Expected: failure — either the integration plumbing is off (fix the fixture names) or the dup check doesn't fire across a full pipeline run (deeper bug, investigate).

- [ ] **Step 9.4: Debug until green**

Likely issues:
- `source_message_id` not threaded into `validator.validate` from `ValidateStage` → verify Task 5's stage-level patch
- Emulator state not reset between the two runs → ensure the test only deletes `orders` written by THIS test, not `seeded_master_data` fixtures
- `AGENT_VERSION` mismatch — both writes use `track-a-v0.2`, so this shouldn't matter

- [ ] **Step 9.5: Commit**

```bash
git add tests/integration/test_orchestrator_emulator.py
git commit -m "test(track-c): e2e test for duplicate submission through full 9-stage pipeline"
```

---

## Task 10: Update status + roadmap docs

**Files:**
- Modify: `research/Order-Intake-Sprint-Status.md`
- Modify: `Glacis-Order-Intake.md`

- [ ] **Step 10.1: Flip Sprint-Status §4 row 'Duplicate detection'**

The current row reads `Credit/inventory/delivery/duplicate dropped per cut-list.` inside the table. Update to reflect Track C landing — edit the "What's left" cell and the "What we have" cell.

Search: `grep -n "duplicate" research/Order-Intake-Sprint-Status.md | head`

Patch by adding a new bullet to the Built inventory (alphabetical with the existing entries) describing each commit:

```
backend/tools/order_validator/tools/duplicate_check.py                  ✓ Track C (<sha-task-1>) — compute_content_hash + find_duplicate + DUPLICATE_WINDOW_DAYS=90
backend/models/order_record.py (schema v3)                              ✓ Track C (<sha-task-2>) — adds customer_id + po_number + content_hash denormalized fields
backend/tools/order_validator/validator.py (preflight)                  ✓ Track C (<sha-task-5>) — find_duplicate short-circuits to ESCALATE on hit
firebase/firestore.indexes.json (duplicate indexes)                     ✓ Track C (<sha-task-7>) — 2 composite indexes on orders for PO# + content-hash branches
tests/integration/test_duplicate_check_emulator.py                      ✓ Track C (<sha-task-8>) — 3 emulator tests
```

- [ ] **Step 10.2: Flip Glacis-Order-Intake.md §4 'Duplicate detection'**

Find the `[Post-MVP]` row for duplicate detection and flip to `[MVP ✓]`:

```
- `[MVP ✓]` **Duplicate detection (check #1)** — preflight short-circuit in OrderValidator.validate
  — PO# OR content-hash signal, customer + 90-day-window scoped,
  source_message_id self-match filter, routes to ESCALATE with
  reason="duplicate of <existing_order_id>". OrderRecord bumps
  to schema_version=3 adding customer_id + po_number + content_hash
  denormalized fields so the composite Firestore indexes hit flat
  paths. 2 new composite indexes + ~24 new tests (unit + integration +
  e2e). MVP: Track C landed 2026-04-24 across commits <sha-task-1>
  through <sha-task-9>. Source: `Validation-Pipeline.md`.
```

- [ ] **Step 10.3: Strike the Phase 2 roadmap bullet for duplicate detection**

In the "Phase 2" section, remove the `Duplicate / credit / inventory / delivery / address checks (§4)` bullet — duplicate is now done, so it becomes `Credit / inventory / delivery / address checks (§4)`.

- [ ] **Step 10.4: Commit**

```bash
git add research/Order-Intake-Sprint-Status.md Glacis-Order-Intake.md
git commit -m "docs(track-c): flip duplicate-detection row to [MVP ✓] across both docs"
```

---

## Task 11: Final verification

- [ ] **Step 11.1: Full suite — unit**

Run: `uv run pytest tests/unit -v 2>&1 | tail -20`

Expected: all green. Test count: 323 baseline + ~21 new Track C tests = ~344 unit.

- [ ] **Step 11.2: Full suite — integration (with emulator)**

Start emulator, set `FIRESTORE_EMULATOR_HOST`, then:

Run: `uv run pytest tests/integration -v 2>&1 | tail -20`

Expected: all green. Test count: 10+ baseline + 3 duplicate_check_emulator + 1 orchestrator e2e = 14+.

- [ ] **Step 11.3: Live smoke on MM Machine fixture (optional, high-confidence gate)**

Re-run the existing live-smoke harness against `data/email/mm_machine_reorder_2026-04-24.eml`:

Run: `uv run python scripts/smoke_run.py data/email/mm_machine_reorder_2026-04-24.eml`

Expected first run: AUTO_APPROVE + one confirmation email drafted + order persisted.

Then run it AGAIN with the same fixture:

Expected second run: ESCALATE + exception landed with `reason="duplicate of <id>"` + ConfirmStage produces no confirmation body + `run_summary.exceptions_opened == 1`.

- [ ] **Step 11.4: Tag the final commit for subsequent tracks**

No tag needed — just note the final SHA in the spec's amendments field for traceability.

- [ ] **Step 11.5: Done.**

Track C closed. Next session should pick up Track D (audit log + correlation_id) following the same brainstorm → spec → plan → execute flow.

---

## Self-review

**Spec coverage:**
- ✅ 4 architectural decisions → Tasks 1/4 (signal), 5 (routing), 5 (integration), 1 (hash composition)
- ✅ Content-hash composition (None-handling, strip, customer-scoped) → Task 1 tests
- ✅ 90-day window → Task 1 constant + Task 4 test
- ✅ Self-match via source_message_id → Task 4 test + Task 5 plumbing
- ✅ `customer_id` + `po_number` denormalization → Task 2 schema + Task 6 coordinator
- ✅ `content_hash` field → Task 2 schema + Task 6 coordinator
- ✅ Preflight short-circuit inside validator → Task 5
- ✅ Firestore composite indexes → Task 7
- ✅ Unit tests for hash + find_duplicate + validator + coordinator → Tasks 1, 4, 5, 6
- ✅ Integration tests against emulator → Task 8
- ✅ E2E pipeline test → Task 9
- ✅ Firestore `!= + >=` fallback → Task 8 Step 8.3 expected-outcome-B path
- ✅ Doc flips → Task 10

**Placeholder scan:**
- Task 6.2 test bodies use `...  # call coordinator.process(...)` shorthand — intentionally, because the closest-pattern call depends on which existing test is the reference. The step prose tells the engineer "mirror the existing AUTO_APPROVE coordinator test" and lists the assertions. This is acceptable under the "assume they're a skilled developer" framing, but close to the line. Flagged.
- Task 9.2 similarly uses `...` for `_run_pipeline_for_fixture` helper — same rationale (the helper name depends on what exists). Flagged.
- All other steps have complete code blocks.

**Type consistency:**
- `compute_content_hash(customer_id, order) → str` — consistent across Tasks 1, 4, 6.
- `find_duplicate(client, *, customer_id, order, source_message_id, po_number, clock) → Optional[str]` — consistent across Tasks 4, 5, 8.
- `OrderValidator.validate(order, *, source_message_id: str) → ValidationResult` — consistent across Tasks 5, 6.
- `OrderRecord` new fields `customer_id: str`, `po_number: Optional[str]`, `content_hash: str` — consistent across Tasks 2, 6.

**Scope check:** Single-plan-sized. 11 tasks, TDD-cycled, each 5-10 steps. Estimated execution time: 4-6 hours for a focused implementer. Fits in one session.

No self-review fixes needed inline — the two `...` flagged placeholders are acceptable under the "skilled developer" assumption; the step prose provides enough guidance to fill them from the codebase pattern.
