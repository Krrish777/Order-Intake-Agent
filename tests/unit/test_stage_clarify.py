"""Unit tests for :class:`backend.my_agent.stages.clarify.ClarifyStage`.

The stage iterates ``state['validation_results']``, filters to entries
whose ``validation.decision == "clarify"``, seeds three placeholder
keys on ``ctx.session.state`` (``customer_name``, ``original_subject``,
``reason``), and invokes the injected child LlmAgent via
``child.run_async(ctx)``. The final ``clarify_email`` dict each child
emits lands in ``state['clarify_bodies']`` keyed by
``"{filename}#{sub_doc_index}"``.

The child LlmAgent dep is exercised via a :class:`_FakeClarifyAgent`
duck-type that yields a single :class:`Event` with
``state_delta={"clarify_email": {...}}``. It also snapshots the three
placeholder keys from ``ctx.session.state`` on each invocation so tests
can assert the seeding order.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions

from backend.ingestion.email_envelope import EmailEnvelope
from backend.my_agent.stages.clarify import CLARIFY_STAGE_NAME, ClarifyStage
from tests.unit._stage_testing import collect_events, final_state_delta, make_stage_ctx


# --------------------------------------------------------------------- helpers


class _FakeClarifyAgent:
    """Duck-typed stand-in for the clarify-email LlmAgent.

    Only implements ``run_async`` — ClarifyStage invokes the child via
    the async-generator contract and inspects ``event.actions.state_delta``
    for the ``clarify_email`` key. A fake keeps the tests hermetic (no
    Gemini call) and lets us assert invocation count + the state
    snapshot seen by the child.
    """

    def __init__(
        self,
        *,
        responses: list[dict[str, Any]] | None = None,
        extra_events: list[Event] | None = None,
    ) -> None:
        self._responses = list(responses) if responses is not None else None
        self._extra_events = list(extra_events) if extra_events else []
        self.call_count = 0
        self.capture_state: list[dict[str, Any]] = []
        self.name = "fake_clarify_agent"

    async def run_async(self, ctx):
        self.call_count += 1
        self.capture_state.append(
            {
                "customer_name": ctx.session.state.get("customer_name"),
                "original_subject": ctx.session.state.get("original_subject"),
                "reason": ctx.session.state.get("reason"),
            }
        )
        for extra in self._extra_events:
            yield extra
        if self._responses is None:
            # No final clarify_email at all — ClarifyStage should raise.
            return
        response = (
            self._responses.pop(0)
            if self._responses
            else {"subject": "Re: stub", "body": "stub"}
        )
        yield Event(
            author=self.name,
            actions=EventActions(state_delta={"clarify_email": response}),
        )


def _envelope_dict(subject: str = "PO #12345 — urgent") -> dict[str, Any]:
    env = EmailEnvelope(
        message_id="m-1@example.com",
        from_addr="buyer@birchvalley.example",
        to_addr="orders@glacis.example",
        subject=subject,
        received_at=datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc),
        body_text="Please ship the attached PO.",
    )
    return env.model_dump(mode="json")


def _validation_entry(
    *,
    filename: str = "po-001.pdf",
    sub_doc_index: int = 0,
    decision: str = "clarify",
    customer_name: str | None = "Birch Valley Foods",
    line_notes: list[list[str]] | None = None,
    rationale: str = "Needs clarification on line quantities.",
) -> dict[str, Any]:
    if line_notes is None:
        line_notes = [["quantity missing"]]
    lines = [
        {
            "line_index": idx,
            "matched_sku": "SKU-001",
            "match_tier": "exact",
            "match_confidence": 0.9,
            "price_ok": True,
            "qty_ok": not bool(notes),
            "notes": notes,
        }
        for idx, notes in enumerate(line_notes)
    ]
    customer = (
        None
        if customer_name is None
        else {
            "customer_id": "CUST-001",
            "name": customer_name,
            "segment": "food-service",
            "bill_to": {
                "street1": "123 Mill Rd",
                "city": "Burlington",
                "state": "VT",
                "zip": "05401",
                "country": "US",
            },
            "payment_terms": "NET30",
            "credit_limit_usd": 50000.0,
            "currency": "USD",
        }
    )
    return {
        "filename": filename,
        "sub_doc_index": sub_doc_index,
        "validation": {
            "customer": customer,
            "lines": lines,
            "aggregate_confidence": 0.85,
            "decision": decision,
            "rationale": rationale,
        },
    }


def _make_ctx(
    stage: ClarifyStage,
    *,
    reply_handled: bool | None = None,
    validation_results: list[dict[str, Any]] | None = None,
    envelope: dict[str, Any] | None = None,
    skipped_docs: list[dict[str, Any]] | None = None,
):
    state: dict[str, Any] = {}
    if reply_handled is not None:
        state["reply_handled"] = reply_handled
    if validation_results is not None:
        state["validation_results"] = validation_results
    if envelope is not None:
        state["envelope"] = envelope
    if skipped_docs is not None:
        state["skipped_docs"] = skipped_docs
    return make_stage_ctx(stage=stage, state=state)


# ---------------------------------------------------------------------- tests


def test_reply_handled_no_ops() -> None:
    """reply_handled=True → child NEVER invoked; clarify_bodies={};
    skipped_docs preserved."""
    fake = _FakeClarifyAgent()
    stage = ClarifyStage(clarify_agent=fake)
    prior_skipped = [
        {
            "filename": "invoice.pdf",
            "stage": "classify_stage",
            "reason": "intent=invoice (confidence=0.88)",
        }
    ]
    ctx = _make_ctx(
        stage,
        reply_handled=True,
        validation_results=[_validation_entry()],
        envelope=_envelope_dict(),
        skipped_docs=prior_skipped,
    )

    events = collect_events(stage.run_async(ctx))
    delta = final_state_delta(events)

    assert fake.call_count == 0
    assert delta["clarify_bodies"] == {}
    assert delta["skipped_docs"] == prior_skipped


def test_missing_validation_results_raises() -> None:
    fake = _FakeClarifyAgent()
    stage = ClarifyStage(clarify_agent=fake)
    ctx = _make_ctx(stage, envelope=_envelope_dict())

    with pytest.raises(ValueError, match="requires ValidateStage"):
        collect_events(stage.run_async(ctx))

    assert fake.call_count == 0


def test_missing_envelope_raises() -> None:
    fake = _FakeClarifyAgent()
    stage = ClarifyStage(clarify_agent=fake)
    ctx = _make_ctx(stage, validation_results=[_validation_entry()])

    with pytest.raises(ValueError, match="requires IngestStage"):
        collect_events(stage.run_async(ctx))

    assert fake.call_count == 0


def test_no_clarify_tier_results_yields_empty_bodies() -> None:
    """validation_results holds only AUTO + ESCALATE entries → child
    never invoked; clarify_bodies=={}."""
    fake = _FakeClarifyAgent()
    stage = ClarifyStage(clarify_agent=fake)
    ctx = _make_ctx(
        stage,
        validation_results=[
            _validation_entry(decision="auto_approve", line_notes=[[]]),
            _validation_entry(decision="escalate"),
        ],
        envelope=_envelope_dict(),
        skipped_docs=[],
    )

    events = collect_events(stage.run_async(ctx))
    delta = final_state_delta(events)

    assert fake.call_count == 0
    assert delta["clarify_bodies"] == {}
    assert delta["skipped_docs"] == []


def test_single_clarify_entry_produces_one_body() -> None:
    response = {
        "subject": "Re: PO #12345 — urgent",
        "body": "Hi Birch Valley team, could you confirm the quantity on line 0?",
    }
    fake = _FakeClarifyAgent(responses=[response])
    stage = ClarifyStage(clarify_agent=fake)
    ctx = _make_ctx(
        stage,
        validation_results=[
            _validation_entry(filename="po-001.pdf", sub_doc_index=0)
        ],
        envelope=_envelope_dict(subject="PO #12345 — urgent"),
    )

    events = collect_events(stage.run_async(ctx))
    delta = final_state_delta(events)

    assert fake.call_count == 1
    bodies = delta["clarify_bodies"]
    assert list(bodies.keys()) == ["po-001.pdf#0"]
    assert bodies["po-001.pdf#0"] == response

    # Final aggregator event is authored by the stage.
    assert any(e.author == CLARIFY_STAGE_NAME for e in events)


def test_multiple_clarify_entries_produces_multiple_bodies() -> None:
    """Three entries (CLARIFY, AUTO, CLARIFY) → two child invocations
    keyed by their filename#sub_doc_index. Assert ordering by checking
    the per-invocation state snapshots."""
    response_a = {"subject": "Re: PO A", "body": "Body for A"}
    response_b = {"subject": "Re: PO B", "body": "Body for B"}
    fake = _FakeClarifyAgent(responses=[response_a, response_b])
    stage = ClarifyStage(clarify_agent=fake)

    entry_a = _validation_entry(
        filename="bundle.pdf",
        sub_doc_index=0,
        decision="clarify",
        customer_name="Alpha Corp",
        line_notes=[["qty missing on line 0"]],
        rationale="line 0 ambiguous",
    )
    entry_auto = _validation_entry(
        filename="bundle.pdf",
        sub_doc_index=1,
        decision="auto_approve",
        customer_name="Alpha Corp",
        line_notes=[[]],
    )
    entry_b = _validation_entry(
        filename="other.pdf",
        sub_doc_index=0,
        decision="clarify",
        customer_name="Beta LLC",
        line_notes=[["unknown SKU on line 0"]],
        rationale="sku miss",
    )

    ctx = _make_ctx(
        stage,
        validation_results=[entry_a, entry_auto, entry_b],
        envelope=_envelope_dict(subject="mixed order batch"),
    )

    events = collect_events(stage.run_async(ctx))
    delta = final_state_delta(events)

    assert fake.call_count == 2
    bodies = delta["clarify_bodies"]
    assert set(bodies.keys()) == {"bundle.pdf#0", "other.pdf#0"}
    # AUTO entry must NOT appear.
    assert "bundle.pdf#1" not in bodies
    assert bodies["bundle.pdf#0"] == response_a
    assert bodies["other.pdf#0"] == response_b

    # Ordering: first invocation saw Alpha Corp, second saw Beta LLC.
    assert fake.capture_state[0]["customer_name"] == "Alpha Corp"
    assert fake.capture_state[0]["reason"] == "Line 0: qty missing on line 0"
    assert fake.capture_state[0]["original_subject"] == "mixed order batch"
    assert fake.capture_state[1]["customer_name"] == "Beta LLC"
    assert fake.capture_state[1]["reason"] == "Line 0: unknown SKU on line 0"
    assert fake.capture_state[1]["original_subject"] == "mixed order batch"


def test_child_never_emits_clarify_email_raises() -> None:
    """Child yields events but none carries clarify_email in state_delta
    → ClarifyStage re-raises a clear RuntimeError."""
    # responses=None → fake yields no final clarify_email event.
    fake = _FakeClarifyAgent(responses=None)
    stage = ClarifyStage(clarify_agent=fake)
    ctx = _make_ctx(
        stage,
        validation_results=[
            _validation_entry(filename="po-001.pdf", sub_doc_index=0)
        ],
        envelope=_envelope_dict(),
    )

    with pytest.raises(RuntimeError, match="did not produce clarify_email"):
        collect_events(stage.run_async(ctx))

    assert fake.call_count == 1


def test_prompt_state_keys_seeded_from_validation_and_envelope() -> None:
    """Before the child is invoked, ctx.session.state must carry
    customer_name (from validation.customer.name), original_subject
    (from envelope.subject), and reason (from concatenated line notes)."""
    fake = _FakeClarifyAgent(
        responses=[{"subject": "Re: stub", "body": "stub"}]
    )
    stage = ClarifyStage(clarify_agent=fake)

    entry = _validation_entry(
        filename="po.pdf",
        sub_doc_index=0,
        customer_name="Birch Valley Foods",
        line_notes=[
            ["quantity missing"],
            ["unit_price 5.49 vs catalog 4.10 (+33.9%)"],
        ],
        rationale="ignored because per-line notes exist",
    )
    ctx = _make_ctx(
        stage,
        validation_results=[entry],
        envelope=_envelope_dict(subject="Weekly fasteners order"),
    )

    collect_events(stage.run_async(ctx))

    assert fake.call_count == 1
    snapshot = fake.capture_state[0]
    assert snapshot["customer_name"] == "Birch Valley Foods"
    assert snapshot["original_subject"] == "Weekly fasteners order"
    # Per-line notes joined with " | ", with "Line N: " prefix and
    # "; " between notes within a line.
    assert snapshot["reason"] == (
        "Line 0: quantity missing | "
        "Line 1: unit_price 5.49 vs catalog 4.10 (+33.9%)"
    )
