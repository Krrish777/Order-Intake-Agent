"""End-to-end integration test for the assembled 8-stage Order Intake pipeline.

This is the first full drive of the :class:`SequentialAgent` returned by
:func:`backend.my_agent.agent.build_root_agent` against a real Firestore
emulator, via an ADK :class:`~google.adk.runners.Runner` and
:class:`~google.adk.sessions.InMemorySessionService`. Eight stages run
in canonical order: ``ingest → reply_shortcircuit → classify → parse →
validate → clarify → persist → finalize``.

What is real
------------
* Real async Firestore client (via :func:`get_async_client`) pointed at
  the emulator.
* Real :class:`OrderValidator` against seeded master data.
* Real :class:`IntakeCoordinator` (dedupe, routing, write).
* Real :func:`classify_document` (LlamaClassify) and
  :func:`parse_document` (LlamaExtract) — hit the live LlamaCloud API.

What this test proves about Runner survivability
-------------------------------------------------
The ``SequentialAgent`` structure survives :meth:`Runner.run_async`
setup (the ``model_copy`` of the parent context). Child-invoke
survivability of :class:`FakeChildLlmAgent` itself is demonstrated
transitively by the AUTO_APPROVE → FinalizeStage path completing
end-to-end once the parser's ``external_file_id`` fix from Step 6.5
clears the ParseStage hurdle; this fixture's clarify child is never
invoked (AUTO_APPROVE routes past it).

What is stubbed
---------------
Both :class:`LlmAgent` children are replaced with
:class:`tests.unit._stage_testing.FakeChildLlmAgent` stand-ins:

* ``clarify_agent`` — the patterson fixture is AUTO_APPROVE so this is
  not actually invoked; the stub is a tripwire against a scoring-drift
  regression that would downgrade Patterson into CLARIFY territory.
* ``summary_agent`` — yields a canned :class:`RunSummary` payload so the
  test does not depend on Gemini being reachable, and the assertion
  path stays deterministic. The deterministic-count seed step inside
  :class:`FinalizeStage` still runs, so ``orders_created`` /
  ``exceptions_opened`` / ``docs_skipped`` on ``ctx.session.state`` are
  still authoritative and we assert on them.

Required setup
--------------
1. ``firebase emulators:start --only firestore`` running locally (or in
   CI) with the Firestore emulator reachable on
   ``localhost:8080`` (or wherever ``FIRESTORE_EMULATOR_HOST`` points).
2. Master data loaded:
   ``uv run python scripts/load_master_data.py`` (10 customers + 35
   products + catalog meta). The patterson fixture maps to
   ``CUST-00042`` and uses SKUs from the seeded catalog; without them
   the validator tier drops below AUTO_APPROVE.
3. ``LLAMA_CLOUD_API_KEY`` exported. Classify + parse hit the live
   LlamaCloud API; this test is not eligible for a sealed-environment
   run.

Re-run idempotence
------------------
:func:`backend.tools.document_parser.parse_document` now suffixes
``external_file_id`` with a SHA-256 hash of the payload bytes (see
``_external_file_id``), so re-running this test against an unchanged
fixture reuses the same LlamaCloud file id without tripping the
(project_id, external_file_id) unique constraint. Mutating the fixture
bytes changes the suffix and uploads fresh.

Run with::

    uv run pytest tests/integration/test_orchestrator_emulator.py \\
        -v -m firestore_emulator

Scope
-----
One test this pass: the AUTO_APPROVE smoke path for
``data/pdf/patterson_po-28491.wrapper.eml``. Follow-ups left as TODOs:

* CLARIFY path — pick a fixture whose validator tier lands in the
  0.80–0.95 band and assert :class:`ExceptionRecord` with
  ``PENDING_CLARIFY`` status lands in the emulator's ``exceptions``
  collection with the drafted clarify body (will need a non-stub
  clarify agent — either a fake that returns a fixed subject/body or
  a gated LlamaGemini call).
* ESCALATE path — confidence below 0.80; assert
  ``ExceptionStatus.ESCALATED``.
* Reply-handled short-circuit — seed an in-flight clarify exception,
  drive a reply EML, assert
  :meth:`ExceptionStore.update_with_reply` advanced the record and the
  order-creation path was NOT walked.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from google.adk.runners import Runner
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.genai import types

from backend.models.order_record import OrderRecord
from backend.my_agent.agent import AGENT_VERSION, build_root_agent
from backend.persistence.coordinator import IntakeCoordinator
from backend.persistence.exceptions_store import FirestoreExceptionStore
from backend.persistence.orders_store import (
    ORDERS_COLLECTION,
    FirestoreOrderStore,
)
from backend.tools.document_classifier.classifier import classify_document
from backend.tools.document_parser import parse_document
from backend.tools.order_validator import (
    MasterDataRepo,
    OrderValidator,
    get_async_client,
)
from tests.unit._stage_testing import FakeChildLlmAgent

pytestmark = [
    pytest.mark.firestore_emulator,
    pytest.mark.skipif(
        not os.environ.get("FIRESTORE_EMULATOR_HOST"),
        reason="FIRESTORE_EMULATOR_HOST not set; emulator tests skipped",
    ),
    pytest.mark.skipif(
        not os.environ.get("LLAMA_CLOUD_API_KEY"),
        reason="LLAMA_CLOUD_API_KEY not set; pipeline needs classify + parse",
    ),
]

# Test app/user IDs — small surface, keep consistent for trace legibility.
_APP_NAME = "order-intake-int-test"
_USER_ID = "int-user"

# Fixture path, resolved from repo root. Tests run from repo root by
# pytest's default rootdir inference.
_PATTERSON_EML = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "pdf"
    / "patterson_po-28491.wrapper.eml"
)


async def test_end_to_end_patterson_po_lands_order_in_emulator() -> None:
    """Drive the full 8-stage pipeline via :class:`Runner` on the patterson fixture.

    Asserts:

    * The ADK runner consumes all events without raising.
    * A real :class:`OrderRecord` is persisted at
      ``orders/<message_id>`` in the emulator with ``customer_id``
      ``CUST-00042`` (Patterson Industrial Supply Co.) and the line
      count matches the fixture's 22-line expectation.
    * ``ValidationResult.aggregate_confidence`` on the persisted
      record is ``>= 0.95`` — the AUTO_APPROVE boundary documented in
      ``...-Validation-Pipeline.md``.
    * Final ``state['run_summary']`` reflects one order created, zero
      exceptions opened, zero docs skipped.
    * ``state['skipped_docs']`` is empty — Patterson is a clean PO with
      a single PDF attachment (plus a body.txt synthesised by
      :class:`IngestStage`; the body.txt is classified as non-PO and
      will land in ``skipped_docs`` — see below).

    Caveat: :class:`IngestStage` synthesises a ``body.txt`` attachment
    when no MIME parts are attached, but the patterson fixture already
    has a PDF attachment, so no synthetic body is added. The PDF is the
    sole attachment and classifies as a purchase_order.
    """
    if not _PATTERSON_EML.exists():
        pytest.skip(f"fixture missing: {_PATTERSON_EML}")

    # --- Real deps (mirrors _build_default_root_agent) ----------------
    # We construct the deps inline (instead of calling
    # _build_default_root_agent()) because we need to inject the two
    # FakeChildLlmAgent stubs in place of the real LlmAgent factories.
    client = get_async_client()
    repo = MasterDataRepo(client)
    validator = OrderValidator(repo=repo)
    order_store = FirestoreOrderStore(client)
    exception_store = FirestoreExceptionStore(client)
    coordinator = IntakeCoordinator(
        validator=validator,
        order_store=order_store,
        exception_store=exception_store,
        repo=repo,
        agent_version=f"{AGENT_VERSION}-int-test",
    )

    # Stubbed LlmAgent children: AUTO_APPROVE path never invokes
    # clarify_agent; summary_agent emits a canned RunSummary so
    # FinalizeStage's ``last_run_summary is None`` guard is satisfied
    # without a Gemini round-trip.
    clarify_agent = FakeChildLlmAgent(
        output_key="clarify_email",
        responses=[{"subject": "Re: stub", "body": "stub body"}],
    )
    summary_agent = FakeChildLlmAgent(
        output_key="run_summary",
        responses=[
            {
                "orders_created": 1,
                "exceptions_opened": 0,
                "docs_skipped": 0,
                "summary": "stubbed for integration test",
            }
        ],
    )

    try:
        root_agent = build_root_agent(
            classify_fn=classify_document,
            parse_fn=parse_document,
            validator=validator,
            coordinator=coordinator,
            clarify_agent=clarify_agent,
            summary_agent=summary_agent,
            exception_store=exception_store,
        )

        # --- Runner setup ------------------------------------------------
        session_service = InMemorySessionService()
        session_id = f"int-orch-{uuid.uuid4().hex}"
        await session_service.create_session(
            app_name=_APP_NAME, user_id=_USER_ID, session_id=session_id
        )
        runner = Runner(
            app_name=_APP_NAME,
            agent=root_agent,
            session_service=session_service,
        )

        new_message = types.Content(
            role="user",
            parts=[types.Part.from_text(text=str(_PATTERSON_EML))],
        )

        # --- Drive the pipeline -----------------------------------------
        events = []
        async for event in runner.run_async(
            user_id=_USER_ID,
            session_id=session_id,
            new_message=new_message,
        ):
            events.append(event)

        assert events, "Runner yielded zero events — pipeline did not run"

        # --- Pull final session state -----------------------------------
        session = await session_service.get_session(
            app_name=_APP_NAME, user_id=_USER_ID, session_id=session_id
        )
        assert session is not None, "Session disappeared mid-run"
        state = session.state

        # --- Assert on state ---------------------------------------------
        envelope = state.get("envelope")
        assert envelope is not None, "IngestStage did not seed envelope"
        message_id = envelope["message_id"]

        assert state.get("run_summary") is not None, (
            "FinalizeStage did not publish run_summary on state"
        )
        run_summary = state["run_summary"]
        assert run_summary["orders_created"] == 1, run_summary
        assert run_summary["exceptions_opened"] == 0, run_summary
        assert run_summary["docs_skipped"] == 0, run_summary

        # FinalizeStage deterministically seeds these BEFORE invoking the summary agent;
        # assert against the pre-seed so a regression in count computation is caught
        # independently of the (stubbed) summary agent's echoed response.
        assert state["orders_created"] == 1
        assert state["exceptions_opened"] == 0
        assert state["docs_skipped"] == 0

        # skipped_docs must be empty for the Patterson AUTO_APPROVE path
        # — the PDF is the sole attachment and classifies as purchase_order.
        assert state.get("skipped_docs") == [], state.get("skipped_docs")

        # One parsed_doc, one process_result, both coherent.
        process_results = state.get("process_results", [])
        assert len(process_results) == 1, process_results
        result_entry = process_results[0]
        assert result_entry["result"]["kind"] == "order", result_entry
        validation = result_entry["result"]["validation"]
        assert validation["aggregate_confidence"] >= 0.95, validation
        assert validation["decision"] == "auto_approve", validation

        # --- Assert on the persisted OrderRecord ------------------------
        # Round-trip via the SAME store the coordinator wrote through.
        persisted: OrderRecord | None = await order_store.get(message_id)
        assert persisted is not None, (
            f"expected orders/{message_id} in emulator; got nothing"
        )
        assert persisted.source_message_id == message_id
        assert persisted.customer.customer_id == "CUST-00042"
        assert "Patterson" in persisted.customer.name
        assert persisted.confidence >= 0.95
        assert len(persisted.lines) == 22  # per expected.json
        # First line from the expected.json — a cheap structural spot-check.
        first_line = persisted.lines[0]
        assert first_line.product.sku == "FST-HCS-050-13-200-G5Z"
        assert first_line.quantity == 1200

        # Cleanup: delete the emulator doc so repeated runs stay clean.
        # Uses the raw client so we do not depend on a store-level delete
        # method existing (:class:`FirestoreOrderStore` deliberately has
        # only ``save`` + ``get``).
        doc_ref = client.collection(ORDERS_COLLECTION).document(message_id)
        await doc_ref.delete()
    finally:
        await repo.aclose()
