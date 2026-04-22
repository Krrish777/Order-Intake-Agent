"""Seed a PENDING_CLARIFY exception so Case 4 of the smoke evalset exercises the reply short-circuit.

Case 4 of ``tests/eval/smoke.evalset.json`` drives
``data/email/birch_valley_clarify_reply.eml`` through the pipeline. The
:class:`ReplyShortCircuitStage` looks up any pending clarify exception on the
reply's thread; if one exists, it advances the exception to
``AWAITING_REVIEW`` and short-circuits the remaining stages. Without a
pre-seeded parent exception, the reply falls through to the ordinary
extract+validate path, which is not what this case is meant to test.

This script inserts one :class:`ExceptionRecord` into the ``exceptions``
Firestore collection with:

* ``source_message_id`` = the (fabricated) original clarify-email message id.
* ``thread_id`` = the reply's ``References`` header's first entry, computed
  by :func:`backend.ingestion.eml_parser.parse_eml` as
  ``references[0]``. For ``birch_valley_clarify_reply.eml`` this is
  ``<20260420091512.9A31.stanbirch@birchvalleyfarmeq.com>``.
* ``status`` = :attr:`ExceptionStatus.PENDING_CLARIFY`.
* Plausible :class:`ParsedDocument` + :class:`ValidationResult` snapshots
  (minimal but schema-valid) so the record round-trips.

**Idempotence**: running twice is safe. We check for the doc by id first
and return early if it exists — the stage under test only needs the record
to be present and ``PENDING_CLARIFY``, not freshly-written. Re-seeding
would also violate the ``AlreadyExists`` contract on
:class:`FirestoreExceptionStore.save` (which swallows) but we want to
preserve the original ``created_at`` so the
``order_by('created_at', direction='DESCENDING').limit(1)`` lookup in
:meth:`FirestoreExceptionStore.find_pending_clarify` still returns the same
record across re-runs.

Run with::

    FIRESTORE_EMULATOR_HOST=localhost:8080 \\
        uv run python tests/eval/fixtures/seed_birch_valley_exception.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone

from backend.models.exception_record import ExceptionRecord, ExceptionStatus
from backend.models.parsed_document import (
    ExtractedOrder,
    OrderLineItem,
    ParsedDocument,
)
from backend.models.validation_result import (
    LineItemValidation,
    RoutingDecision,
    ValidationResult,
)
from backend.persistence.exceptions_store import FirestoreExceptionStore
from backend.tools.order_validator.tools.firestore_client import get_async_client

# The first References entry on data/email/birch_valley_clarify_reply.eml.
# eml_parser derives thread_id from references[0]; the reply stage looks up
# the pending exception by that value, so these must match exactly.
BIRCH_VALLEY_THREAD_ID = "<20260420091512.9A31.stanbirch@birchvalleyfarmeq.com>"

# Fabricated original-clarify message id. Used as the Firestore doc id.
# Must be stable across runs so the idempotence check works.
ORIGINAL_CLARIFY_MESSAGE_ID = (
    "<20260420091512.9A31.stanbirch@birchvalleyfarmeq.com>"
)

# A fabricated clarify-email outbound id (what we would have sent). The
# reply stage does not read this — included for record completeness.
CLARIFY_OUT_MESSAGE_ID = (
    "<clarify-birch-valley-seed@grafton-reese.com>"
)


def _build_parsed_doc() -> ParsedDocument:
    """Minimal but schema-valid parser snapshot for the seeded record."""
    return ParsedDocument(
        classification="purchase_order",
        classification_rationale=(
            "Free-text reorder request from a known customer; mentions "
            "fasteners and tubing without canonical SKUs."
        ),
        sub_documents=[
            ExtractedOrder(
                customer_name="Birch Valley Farm Equipment",
                po_number=None,
                line_items=[
                    OrderLineItem(
                        sku=None,
                        description="Grade 8 hex cap screws, yellow zinc",
                        quantity=1.0,
                        unit_of_measure="box",
                        unit_price=None,
                    ),
                    OrderLineItem(
                        sku=None,
                        description="Blue hydraulic tubing, 100ft roll",
                        quantity=1.0,
                        unit_of_measure="roll",
                        unit_price=None,
                    ),
                ],
                special_instructions=(
                    "Customer uses informal vocabulary (grade 8s, blue "
                    "tubing) — needs clarification on thread pitch + length."
                ),
            )
        ],
        page_count=1,
        detected_language="en",
    )


def _build_validation_result() -> ValidationResult:
    """Minimal validation snapshot landing in the CLARIFY band."""
    return ValidationResult(
        customer=None,  # optional; leaving None keeps the schema minimal
        lines=[
            LineItemValidation(
                line_index=0,
                matched_sku=None,
                match_tier="none",
                match_confidence=0.0,
                price_ok=True,
                qty_ok=True,
                notes=["ambiguous: 'grade 8s' matches multiple SKUs"],
            ),
            LineItemValidation(
                line_index=1,
                matched_sku=None,
                match_tier="none",
                match_confidence=0.0,
                price_ok=True,
                qty_ok=True,
                notes=["ambiguous: 'blue tubing' matches multiple SKUs"],
            ),
        ],
        aggregate_confidence=0.82,
        decision=RoutingDecision.CLARIFY,
        rationale=(
            "2 lines with ambiguous descriptions; asking customer to confirm "
            "thread pitch, length, and tubing bore."
        ),
    )


async def _seed() -> int:
    """Insert the PENDING_CLARIFY record if absent. Returns the exit code."""
    client = get_async_client()
    store = FirestoreExceptionStore(client)

    existing = await store.get(ORIGINAL_CLARIFY_MESSAGE_ID)
    if existing is not None:
        if existing.status == ExceptionStatus.PENDING_CLARIFY:
            print(
                f"exceptions/{ORIGINAL_CLARIFY_MESSAGE_ID} already present "
                f"with status=PENDING_CLARIFY; nothing to do."
            )
            return 0
        # Doc is there but in the wrong state (e.g. a previous eval advanced
        # it to AWAITING_REVIEW). The operator needs to know so they can
        # reset or pick a fresh id.
        print(
            f"exceptions/{ORIGINAL_CLARIFY_MESSAGE_ID} exists with "
            f"status={existing.status!r}; expected PENDING_CLARIFY. "
            f"Delete the doc manually or rotate ORIGINAL_CLARIFY_MESSAGE_ID "
            f"before re-running the birch_valley eval case.",
            file=sys.stderr,
        )
        return 2

    now = datetime(2026, 4, 20, 13, 20, 0, tzinfo=timezone.utc)
    record = ExceptionRecord(
        source_message_id=ORIGINAL_CLARIFY_MESSAGE_ID,
        thread_id=BIRCH_VALLEY_THREAD_ID,
        clarify_message_id=CLARIFY_OUT_MESSAGE_ID,
        reply_message_id=None,
        status=ExceptionStatus.PENDING_CLARIFY,
        reason=(
            "Birch Valley informal reorder: thread pitch and tubing bore "
            "unclear from customer's shorthand ('grade 8s', 'blue tubing')."
        ),
        clarify_body=(
            "Hi Stan — quick clarification on the grade 8 hex caps: what "
            "thread pitch and length? And the blue tubing — same 3/8 OD "
            "as last month, or a different bore? Thanks."
        ),
        parsed_doc=_build_parsed_doc(),
        validation_result=_build_validation_result(),
        created_at=now,
        updated_at=now,
    )
    await store.save(record)
    print(
        f"seeded exceptions/{ORIGINAL_CLARIFY_MESSAGE_ID} "
        f"(thread_id={BIRCH_VALLEY_THREAD_ID})."
    )
    return 0


def main() -> int:
    if not os.environ.get("FIRESTORE_EMULATOR_HOST"):
        print(
            "FIRESTORE_EMULATOR_HOST is not set. Point it at the running "
            "Firestore emulator (e.g. localhost:8080) before running this "
            "seed script — we refuse to write to a real Firestore database "
            "from a test fixture.",
            file=sys.stderr,
        )
        return 1
    return asyncio.run(_seed())


if __name__ == "__main__":
    raise SystemExit(main())
