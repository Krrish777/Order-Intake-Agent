"""The :class:`JudgeStage` — stage #10 of the Order Intake pipeline.

Runs after :class:`~backend.my_agent.stages.finalize.FinalizeStage`.
For every entry in ``state['process_results']`` whose
``result.kind`` is ``"order"`` or ``"exception"`` and which carries a
drafted body (``confirmation_body`` or ``clarify_body``), this stage
invokes the injected judge :class:`~google.adk.agents.LlmAgent` to
evaluate the body against the underlying record's ground-truth facts,
writes a :class:`~backend.models.judge_verdict.JudgeVerdict` onto the
persisted record via ``OrderStore.update_with_judge_verdict`` /
``ExceptionStore.update_with_judge_verdict``, and stashes all verdicts
on ``state['judge_verdicts']`` (keyed by ``source_message_id``) for
:class:`~backend.my_agent.stages.send.SendStage` to read.

Fail-closed posture: any exception during ``run_async`` or during
``JudgeVerdict.model_validate`` synthesizes a
``JudgeVerdict(status="rejected", reason="judge_unavailable:<exc>",
findings=[])``. SendStage reads ``status != "pass"`` and blocks the
send. No email leaves the system unverified.

``kind == "duplicate"`` entries are skipped — a duplicate was judged
on the prior run; re-judging would overwrite the stored verdict. Same
short-circuit as ConfirmStage's ``kind=="duplicate"`` skip.

This stage follows the AuditedStage mixin pattern from Track D:
  1. Override ``_audited_run`` instead of ``_run_async_impl``.
  2. Emit custom lifecycle events (``judge_verdict_passed`` /
     ``judge_verdict_rejected`` / ``judge_unavailable``) via
     ``self._audit_logger.emit(...)``.

Short-circuit: if ``state['reply_handled']`` is ``True``, this stage
no-ops — emits an empty ``judge_verdicts={}`` delta. The child
LlmAgent is not invoked; neither store is touched.
"""

from __future__ import annotations

import json
from typing import Any, AsyncGenerator, Final

from google.adk.agents.invocation_context import InvocationContext
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions
from google.genai import types
from pydantic import PrivateAttr, ValidationError

from backend.ingestion.email_envelope import EmailEnvelope
from backend.models.judge_verdict import JudgeVerdict
from backend.my_agent.stages._audited import AuditedStage
from backend.persistence.base import ExceptionStore, OrderStore

JUDGE_STAGE_NAME: Final[str] = "judge_stage"


class JudgeStage(AuditedStage):
    """AuditedStage that evaluates drafted outbound emails.

    Dep-injection: PrivateAttr-as-Any for the child agent (Pydantic
    isinstance checks would reject FakeChildLlmAgent in tests; same
    rationale as ClarifyStage + ConfirmStage). Stores are Protocol-typed
    so AsyncMock(spec=OrderStore) satisfies them.
    """

    name: str = JUDGE_STAGE_NAME
    _judge_agent:     Any             = PrivateAttr()
    _order_store:     OrderStore      = PrivateAttr()
    _exception_store: ExceptionStore  = PrivateAttr()

    def __init__(
        self,
        *,
        judge_agent:     Any,
        order_store:     OrderStore,
        exception_store: ExceptionStore,
        audit_logger:    Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(audit_logger=audit_logger, **kwargs)
        self._judge_agent     = judge_agent
        self._order_store     = order_store
        self._exception_store = exception_store

    async def _audited_run(    # type: ignore[override]
        self, ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        # Short-circuit: reply was handled upstream.
        if ctx.session.state.get("reply_handled") is True:
            yield Event(
                author=JUDGE_STAGE_NAME,
                actions=EventActions(state_delta={"judge_verdicts": {}}),
            )
            return

        process_results = ctx.session.state.get("process_results")
        if process_results is None:
            raise ValueError(
                "JudgeStage requires PersistStage to have populated "
                "state['process_results']"
            )

        envelope_dict = ctx.session.state.get("envelope")
        if envelope_dict is None:
            raise ValueError(
                "JudgeStage requires IngestStage to have populated "
                "state['envelope']"
            )
        envelope = EmailEnvelope.model_validate(envelope_dict)

        source_id = self._extract_source_message_id(ctx.session.state) or ""
        judge_verdicts: dict[str, dict] = {}

        confirmation_bodies = ctx.session.state.get("confirmation_bodies", {}) or {}
        clarify_bodies      = ctx.session.state.get("clarify_bodies", {})      or {}

        for entry in process_results:
            result = entry.get("result", {})
            kind   = result.get("kind")

            if kind == "duplicate":
                continue

            subject, body, entry_source_id = _extract_draft(entry, envelope)
            # ConfirmStage writes confirmation_body to Firestore via
            # field-mask update *after* PersistStage stored the
            # ProcessResult in state. The snapshot inside process_results
            # therefore does not see the body. Fall back to the dict
            # ConfirmStage / ClarifyStage populate on session state,
            # keyed by ``{filename}#{sub_doc_index}``.
            if body is None:
                body_key = f"{entry.get('filename')}#{entry.get('sub_doc_index')}"
                source   = (
                    confirmation_bodies if kind == "order" else clarify_bodies
                )
                draft = source.get(body_key)
                if isinstance(draft, dict):
                    body = draft.get("body")
                    if subject == f"Re: {envelope.subject}" and draft.get("subject"):
                        subject = draft["subject"]
            if body is None:
                continue   # no draft to judge (ESCALATE exceptions, etc.)

            record_facts = _flatten_facts(entry)

            # Seed {state_key} placeholders by direct mutation of state.
            # ADK state_delta is NOT committed between parent + child in
            # the same run_async; see backend/my_agent/stages/confirm.py
            # docstring §1 for the ConditionalRouter gotcha.
            ctx.session.state["judge_subject"]      = subject
            ctx.session.state["judge_body"]         = body
            ctx.session.state["judge_record_kind"]  = kind
            ctx.session.state["judge_record_facts"] = json.dumps(
                record_facts, default=str
            )

            try:
                last_payload: Any = None
                async for event in self._judge_agent.run_async(ctx):
                    if (
                        event.actions
                        and event.actions.state_delta
                        and "judge_verdict" in event.actions.state_delta
                    ):
                        last_payload = event.actions.state_delta["judge_verdict"]
                    yield event
                if last_payload is None:
                    raise RuntimeError("judge agent produced no output")
                verdict = JudgeVerdict.model_validate(last_payload)
            except Exception as exc:    # noqa: BLE001 — fail-closed by design
                verdict = JudgeVerdict(
                    status="rejected",
                    reason=f"judge_unavailable:{type(exc).__name__}",
                    findings=[],
                )
                await self._audit_logger.emit(
                    correlation_id=ctx.session.state.get("correlation_id", ""),
                    session_id=ctx.session.id,
                    source_message_id=source_id,
                    stage="lifecycle",
                    phase="lifecycle",
                    action="judge_unavailable",
                    outcome="error",
                    payload={
                        "source_message_id": entry_source_id,
                        "record_kind":       kind,
                        "exception":         type(exc).__name__,
                    },
                )

            # Persist onto the record (field-mask update).
            if kind == "order":
                await self._order_store.update_with_judge_verdict(
                    entry_source_id, verdict
                )
            else:  # exception
                await self._exception_store.update_with_judge_verdict(
                    entry_source_id, verdict
                )

            audit_action = (
                "judge_verdict_passed" if verdict.status == "pass"
                else "judge_verdict_rejected"
            )
            await self._audit_logger.emit(
                correlation_id=ctx.session.state.get("correlation_id", ""),
                session_id=ctx.session.id,
                source_message_id=source_id,
                stage="lifecycle",
                phase="lifecycle",
                action=audit_action,
                outcome="ok" if verdict.status == "pass" else "error",
                payload={
                    "source_message_id": entry_source_id,
                    "record_kind":       kind,
                    "reason":            verdict.reason,
                    "findings_count":    len(verdict.findings),
                    "findings":          (
                        [f.model_dump() for f in verdict.findings]
                        if verdict.status == "rejected" else []
                    ),
                },
            )

            judge_verdicts[entry_source_id] = verdict.model_dump(mode="json")

        rejected_count = sum(
            1 for v in judge_verdicts.values() if v["status"] == "rejected"
        )
        yield Event(
            author=JUDGE_STAGE_NAME,
            actions=EventActions(state_delta={"judge_verdicts": judge_verdicts}),
            content=types.Content(
                role="model",
                parts=[
                    types.Part(
                        text=(
                            f"Judged {len(judge_verdicts)} outbound body(ies); "
                            f"{rejected_count} rejected."
                        )
                    )
                ],
            ),
        )


def _extract_draft(entry: dict, envelope: EmailEnvelope) -> tuple[str, Any, str]:
    """Return ``(subject, body, source_message_id)``.

    Body is ``None`` if the record has no drafted body (e.g. ESCALATE
    exceptions without clarify_body, or confirmations where
    confirmation_body didn't land). ``source_message_id`` lives nested
    on the OrderRecord / ExceptionRecord — ProcessResult itself has no
    top-level copy.
    """
    result  = entry["result"]
    kind    = result["kind"]
    subject = f"Re: {envelope.subject}" if envelope.subject else "(no subject)"

    if kind == "order":
        order     = result.get("order") or {}
        body      = order.get("confirmation_body")
        source_id = order.get("source_message_id", "")
    elif kind == "exception":
        exc       = result.get("exception") or {}
        body      = exc.get("clarify_body")
        source_id = exc.get("source_message_id", "")
    else:
        body      = None
        source_id = ""

    return subject, body, source_id


def _flatten_facts(entry: dict) -> dict[str, Any]:
    """Return the flat ground-truth dict the judge cross-checks against.

    For ``kind='order'``: customer_name/id, order_total, line_items
    (sku/qty/unit_price/line_total), status.
    For ``kind='exception'``: customer_name, exception_type, reason,
    missing_fields, status.
    """
    result = entry["result"]
    kind   = result["kind"]

    if kind == "order":
        order    = result["order"]
        customer = order.get("customer", {})
        lines    = order.get("lines", [])
        return {
            "customer_name":  customer.get("name"),
            "customer_id":    customer.get("customer_id"),
            "ship_to":        customer.get("ship_to") or customer.get("bill_to"),
            "payment_terms":  customer.get("payment_terms"),
            "order_total":    order.get("order_total"),
            "line_items":     [
                {
                    "sku":               ln.get("product", {}).get("sku"),
                    "short_description": ln.get("product", {}).get("short_description"),
                    "uom":               ln.get("product", {}).get("uom"),
                    "qty":               ln.get("quantity"),
                    "unit_price":        ln.get("product", {}).get("price_at_time"),
                    "line_total":        ln.get("line_total"),
                }
                for ln in lines
            ],
            "status":         order.get("status"),
        }

    # kind == 'exception'
    exc      = result["exception"]
    customer = exc.get("customer", {})
    return {
        "customer_name":   customer.get("name"),
        "exception_type":  exc.get("exception_type"),
        "reason":          exc.get("reason"),
        "missing_fields":  exc.get("missing_fields", []),
        "status":          exc.get("status"),
    }


__all__ = ["JUDGE_STAGE_NAME", "JudgeStage"]
