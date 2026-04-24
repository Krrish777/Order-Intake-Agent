"""Unit tests for :class:`backend.my_agent.stages.confirm.ConfirmStage`.

The stage iterates ``state['process_results']``, filters to entries whose
``result.kind == "order"`` (fresh AUTO_APPROVE — not duplicates, not
exceptions), seeds four placeholder keys on ``ctx.session.state``
(``customer_name``, ``original_subject``, ``order_details``,
``order_ref``), and invokes the injected child LlmAgent via
``child.run_async(ctx)``. The final ``confirmation_email`` dict each
child emits lands in ``state['confirmation_bodies']`` keyed by
``"{filename}#{sub_doc_index}"`` and the body string is written onto
the persisted order via ``order_store.update_with_confirmation()``.

The child LlmAgent dep is exercised via the shared
:class:`FakeChildLlmAgent` duck-type with
``output_key="confirmation_email"``. The order store dep is exercised
via :class:`unittest.mock.AsyncMock` following the precedent in
``test_stage_reply_shortcircuit.py``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest

from backend.ingestion.email_envelope import EmailEnvelope
from backend.my_agent.stages.confirm import CONFIRM_STAGE_NAME, ConfirmStage
from backend.persistence.base import OrderStore
from tests.unit._stage_testing import (
    FakeChildLlmAgent,
    collect_events,
    final_state_delta,
    make_stage_ctx,
)


# --------------------------------------------------------------------- helpers


def _make_confirm_fake(
    *,
    responses: list[dict[str, Any]] | None = None,
) -> FakeChildLlmAgent:
    return FakeChildLlmAgent(
        output_key="confirmation_email",
        responses=responses,
        capture_keys=[
            "customer_name",
            "original_subject",
            "order_details",
            "order_ref",
        ],
        name="fake_confirmation_agent",
    )


def _envelope_dict(subject: str = "PO #42 — re-order") -> dict[str, Any]:
    env = EmailEnvelope(
        message_id="m-42@example.com",
        from_addr="tony@mm-machineworks.com",
        to_addr="orders@glacis.example",
        subject=subject,
        received_at=datetime(2026, 4, 24, 12, 0, tzinfo=timezone.utc),
        body_text="Attached PO.",
    )
    return env.model_dump(mode="json")


def _order_dict(
    *,
    source_message_id: str = "m-42@example.com",
    customer_name: str = "M&M Machine & Fabrication",
    order_total: float = 127.40,
    n_lines: int = 2,
) -> dict[str, Any]:
    """Minimal OrderRecord-shape dict matching ``model_dump(mode='json')``."""
    lines = [
        {
            "line_number": i,
            "product": {
                "sku": f"SKU-{i:03d}",
                "short_description": f"Part {i}",
                "uom": "EA",
                "price_at_time": round(10.0 + i, 2),
            },
            "quantity": 3 + i,
            "line_total": round((10.0 + i) * (3 + i), 2),
            "confidence": 1.0,
        }
        for i in range(n_lines)
    ]
    return {
        "source_message_id": source_message_id,
        "thread_id": f"thread-{source_message_id}",
        "customer": {
            "customer_id": "CUST-042",
            "name": customer_name,
            "bill_to": {
                "street1": "100 Industrial Way",
                "street2": None,
                "city": "Dayton",
                "state": "OH",
                "zip": "45402",
                "country": "USA",
            },
            "payment_terms": "Net 30",
            "contact_email": "tony@mm-machineworks.com",
        },
        "lines": lines,
        "order_total": order_total,
        "confidence": 1.0,
        "status": "persisted",
        "processed_by_agent_version": "track-a-v0.1",
        "confirmation_body": None,
        "schema_version": 2,
        "created_at": "2026-04-24T12:00:00+00:00",
    }


def _process_entry(
    *,
    filename: str = "body.txt",
    sub_doc_index: int = 0,
    kind: str = "order",
    order: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"kind": kind, "order": None, "exception": None}
    if kind == "order" or kind == "duplicate":
        result["order"] = order or _order_dict()
    return {
        "filename": filename,
        "sub_doc_index": sub_doc_index,
        "result": result,
    }


def _make_ctx(
    stage: ConfirmStage,
    *,
    reply_handled: bool | None = None,
    process_results: list[dict[str, Any]] | None = None,
    envelope: dict[str, Any] | None = None,
    skipped_docs: list[dict[str, Any]] | None = None,
):
    state: dict[str, Any] = {}
    if reply_handled is not None:
        state["reply_handled"] = reply_handled
    if process_results is not None:
        state["process_results"] = process_results
    if envelope is not None:
        state["envelope"] = envelope
    if skipped_docs is not None:
        state["skipped_docs"] = skipped_docs
    return make_stage_ctx(stage=stage, state=state)


# ---------------------------------------------------------------------- tests


def test_reply_handled_no_ops() -> None:
    """reply_handled=True → child NEVER invoked; confirmation_bodies={};
    skipped_docs preserved; store never called."""
    fake = _make_confirm_fake()
    store = AsyncMock(spec=OrderStore)
    stage = ConfirmStage(confirm_agent=fake, order_store=store)
    prior_skipped = [
        {
            "filename": "invoice.pdf",
            "stage": "classify_stage",
            "reason": "intent=invoice",
        }
    ]
    ctx = _make_ctx(
        stage,
        reply_handled=True,
        process_results=[_process_entry()],
        envelope=_envelope_dict(),
        skipped_docs=prior_skipped,
    )

    events = collect_events(stage.run_async(ctx))
    delta = final_state_delta(events)

    assert fake.call_count == 0
    store.update_with_confirmation.assert_not_awaited()
    assert delta["confirmation_bodies"] == {}
    assert delta["skipped_docs"] == prior_skipped


def test_missing_process_results_raises() -> None:
    fake = _make_confirm_fake()
    store = AsyncMock(spec=OrderStore)
    stage = ConfirmStage(confirm_agent=fake, order_store=store)
    ctx = _make_ctx(stage, envelope=_envelope_dict())

    with pytest.raises(ValueError, match="requires PersistStage"):
        collect_events(stage.run_async(ctx))

    assert fake.call_count == 0
    store.update_with_confirmation.assert_not_awaited()


def test_missing_envelope_raises() -> None:
    fake = _make_confirm_fake()
    store = AsyncMock(spec=OrderStore)
    stage = ConfirmStage(confirm_agent=fake, order_store=store)
    ctx = _make_ctx(stage, process_results=[_process_entry()])

    with pytest.raises(ValueError, match="requires IngestStage"):
        collect_events(stage.run_async(ctx))

    assert fake.call_count == 0
    store.update_with_confirmation.assert_not_awaited()


def test_no_auto_approve_entries_yields_empty_bodies() -> None:
    """process_results holds only non-order entries → child never invoked;
    confirmation_bodies=={}; store never called."""
    fake = _make_confirm_fake()
    store = AsyncMock(spec=OrderStore)
    stage = ConfirmStage(confirm_agent=fake, order_store=store)
    ctx = _make_ctx(
        stage,
        process_results=[
            _process_entry(filename="a.pdf", kind="exception"),
            _process_entry(filename="b.pdf", kind="duplicate"),
        ],
        envelope=_envelope_dict(),
        skipped_docs=[],
    )

    events = collect_events(stage.run_async(ctx))
    delta = final_state_delta(events)

    assert fake.call_count == 0
    store.update_with_confirmation.assert_not_awaited()
    assert delta["confirmation_bodies"] == {}
    assert delta["skipped_docs"] == []


def test_single_auto_approve_entry_produces_one_body() -> None:
    response = {
        "subject": "Re: PO #42 — confirmed, $127.40",
        "body": "Hi Tony, got your re-order — the two SKUs ship at $127.40 total. Thanks, Grafton-Reese MRO.",
    }
    fake = _make_confirm_fake(responses=[response])
    store = AsyncMock(spec=OrderStore)
    stage = ConfirmStage(confirm_agent=fake, order_store=store)

    order = _order_dict(source_message_id="m-42@example.com")
    ctx = _make_ctx(
        stage,
        process_results=[_process_entry(order=order)],
        envelope=_envelope_dict(subject="PO #42 — re-order"),
    )

    events = collect_events(stage.run_async(ctx))
    delta = final_state_delta(events)

    assert fake.call_count == 1
    bodies = delta["confirmation_bodies"]
    assert list(bodies.keys()) == ["body.txt#0"]
    assert bodies["body.txt#0"] == response

    store.update_with_confirmation.assert_awaited_once_with(
        "m-42@example.com", response["body"]
    )
    assert any(e.author == CONFIRM_STAGE_NAME for e in events)


def test_multiple_mixed_entries_filters_to_orders_only() -> None:
    """process_results = [order, exception, order] → two child invocations,
    two store.update_with_confirmation calls, ordered by input."""
    resp_a = {"subject": "Re: A", "body": "body A"}
    resp_b = {"subject": "Re: B", "body": "body B"}
    fake = _make_confirm_fake(responses=[resp_a, resp_b])
    store = AsyncMock(spec=OrderStore)
    stage = ConfirmStage(confirm_agent=fake, order_store=store)

    order_a = _order_dict(
        source_message_id="a@x", customer_name="Alpha Corp", order_total=50.0
    )
    order_b = _order_dict(
        source_message_id="b@x", customer_name="Beta LLC", order_total=75.0
    )

    ctx = _make_ctx(
        stage,
        process_results=[
            _process_entry(filename="bundle.pdf", sub_doc_index=0, order=order_a),
            _process_entry(filename="bundle.pdf", sub_doc_index=1, kind="exception"),
            _process_entry(filename="other.pdf", sub_doc_index=0, order=order_b),
        ],
        envelope=_envelope_dict(subject="mixed batch"),
    )

    events = collect_events(stage.run_async(ctx))
    delta = final_state_delta(events)

    assert fake.call_count == 2
    bodies = delta["confirmation_bodies"]
    assert set(bodies.keys()) == {"bundle.pdf#0", "other.pdf#0"}
    assert "bundle.pdf#1" not in bodies
    assert bodies["bundle.pdf#0"] == resp_a
    assert bodies["other.pdf#0"] == resp_b

    # Store called once per AUTO_APPROVE entry, in order.
    assert store.update_with_confirmation.await_count == 2
    first_call = store.update_with_confirmation.await_args_list[0]
    second_call = store.update_with_confirmation.await_args_list[1]
    assert first_call.args == ("a@x", "body A")
    assert second_call.args == ("b@x", "body B")

    # Capture-state ordering: first saw Alpha Corp, second saw Beta LLC.
    assert fake.capture_state[0]["customer_name"] == "Alpha Corp"
    assert fake.capture_state[0]["order_ref"] == "a@x"
    assert fake.capture_state[1]["customer_name"] == "Beta LLC"
    assert fake.capture_state[1]["order_ref"] == "b@x"


def test_child_never_emits_confirmation_email_raises() -> None:
    fake = _make_confirm_fake(responses=None)
    store = AsyncMock(spec=OrderStore)
    stage = ConfirmStage(confirm_agent=fake, order_store=store)
    ctx = _make_ctx(
        stage,
        process_results=[_process_entry()],
        envelope=_envelope_dict(),
    )

    with pytest.raises(RuntimeError, match="did not produce confirmation_email"):
        collect_events(stage.run_async(ctx))

    assert fake.call_count == 1
    store.update_with_confirmation.assert_not_awaited()


def test_prompt_state_keys_seeded_from_order_and_envelope() -> None:
    """Before the child is invoked, ctx.session.state must carry
    customer_name, original_subject, order_details, order_ref."""
    fake = _make_confirm_fake(
        responses=[{"subject": "Re: stub", "body": "stub"}]
    )
    store = AsyncMock(spec=OrderStore)
    stage = ConfirmStage(confirm_agent=fake, order_store=store)

    order = _order_dict(
        source_message_id="ref-xyz",
        customer_name="Birch Valley Foods",
        order_total=99.99,
        n_lines=2,
    )
    ctx = _make_ctx(
        stage,
        process_results=[_process_entry(order=order)],
        envelope=_envelope_dict(subject="Weekly fasteners order"),
    )

    collect_events(stage.run_async(ctx))

    assert fake.call_count == 1
    snapshot = fake.capture_state[0]
    assert snapshot["customer_name"] == "Birch Valley Foods"
    assert snapshot["original_subject"] == "Weekly fasteners order"
    assert snapshot["order_ref"] == "ref-xyz"
    details = snapshot["order_details"]
    assert "Line items:" in details
    assert "SKU-000" in details and "SKU-001" in details
    assert "$99.99" in details
    assert "Dayton, OH 45402" in details
    assert "Net 30" in details


def test_store_update_call_count_matches_bodies() -> None:
    """Duplicates are NOT re-confirmed — their confirmation came from
    the prior run's ConfirmStage call. Only kind='order' entries drive
    update_with_confirmation."""
    resp = {"subject": "Re: fresh", "body": "fresh confirmation body"}
    fake = _make_confirm_fake(responses=[resp])
    store = AsyncMock(spec=OrderStore)
    stage = ConfirmStage(confirm_agent=fake, order_store=store)

    ctx = _make_ctx(
        stage,
        process_results=[
            _process_entry(filename="dup.pdf", sub_doc_index=0, kind="duplicate"),
            _process_entry(filename="fresh.pdf", sub_doc_index=0, kind="order"),
        ],
        envelope=_envelope_dict(),
    )

    events = collect_events(stage.run_async(ctx))
    delta = final_state_delta(events)

    assert fake.call_count == 1
    assert store.update_with_confirmation.await_count == 1
    assert list(delta["confirmation_bodies"].keys()) == ["fresh.pdf#0"]
