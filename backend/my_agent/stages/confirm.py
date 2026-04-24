"""The :class:`ConfirmStage` — stage #8 of the Order Intake pipeline.

Runs after :class:`~backend.my_agent.stages.persist.PersistStage`. For
every entry in ``state['process_results']`` whose ``result.kind`` is
``"order"`` (a fresh AUTO_APPROVE just persisted this invocation), this
stage invokes the injected confirmation-email
:class:`~google.adk.agents.LlmAgent` to draft a customer-facing order
confirmation, writes the rendered body onto the persisted order via
``order_store.update_with_confirmation(...)``, and collects the
``ConfirmationEmail`` dicts into ``state['confirmation_bodies']`` keyed
by ``"{filename}#{sub_doc_index}"``.

``kind == "duplicate"`` entries are deliberately skipped — a duplicate
was already confirmed on a prior run; re-drafting would overwrite the
stored body with a differently-worded draft and burn a Gemini call for
no new customer-facing value. ``kind == "exception"`` is skipped because
that leg is handled by :class:`ClarifyStage` upstream.

This stage follows the same child-LlmAgent pattern as ClarifyStage:

1. The confirmation-email LlmAgent's instruction template
   (``backend/prompts/confirmation_email.py``) contains
   ``{customer_name}``, ``{original_subject}``, ``{order_details}``,
   and ``{order_ref}`` placeholders. ADK resolves those against
   ``ctx.session.state`` **at model-call time**, inside ``run_async``.
   In production a Runner applies each stage's ``state_delta`` to session
   state between stages, but a single-invocation parent→child seed
   within the same ``run_async`` call happens before any delta has been
   committed — so we must **mutate ``ctx.session.state`` directly**
   before invoking the child. This is the pattern the ADK cheatsheet
   documents for ``ConditionalRouter`` (§5).
2. The child LlmAgent has ``output_schema=ConfirmationEmail`` and
   ``output_key="confirmation_email"``, so on its final event the
   validated model lands on
   ``event.actions.state_delta["confirmation_email"]``. We forward each
   child event upward (so adk web traces see them) and capture the last
   ``confirmation_email`` we saw as the body for that AUTO_APPROVE entry.

``skipped_docs`` is passed through unchanged — ConfirmStage does not
add to or remove from it; upstream stages own that list.

Short-circuit: if ``state['reply_handled']`` is ``True``, this stage
no-ops — writes ``confirmation_bodies={}`` and preserves ``skipped_docs``
unchanged. The child LlmAgent is not invoked and the order store is
not touched.
"""

from __future__ import annotations

from typing import Any, AsyncGenerator, Final

from google.adk.agents.invocation_context import InvocationContext
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions
from google.genai import types
from pydantic import PrivateAttr

from backend.ingestion.email_envelope import EmailEnvelope
from backend.my_agent.stages._audited import AuditedStage
from backend.persistence.base import OrderStore

CONFIRM_STAGE_NAME: Final[str] = "confirm_stage"


class ConfirmStage(AuditedStage):
    """BaseAgent that drafts confirmation emails for AUTO_APPROVE orders.

    Dep-injection choice: **PrivateAttr typed as ``Any`` for the child
    agent** (same rationale as ClarifyStage — Pydantic ``isinstance``
    checks would reject the duck-typed test fake). The ``order_store``
    dep is Protocol-typed since ``AsyncMock(spec=OrderStore)`` satisfies
    that without inheritance.
    """

    name: str = CONFIRM_STAGE_NAME
    _confirm_agent: Any = PrivateAttr()
    _order_store: OrderStore = PrivateAttr()

    def __init__(
        self,
        *,
        confirm_agent: Any,
        order_store: OrderStore,
        audit_logger: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(audit_logger=audit_logger, **kwargs)
        self._confirm_agent = confirm_agent
        self._order_store = order_store

    async def _audited_run(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        # Short-circuit: a clarify reply was handled upstream. Emit the
        # keys downstream stages expect but do not invoke the child
        # LlmAgent or touch the order store.
        if ctx.session.state.get("reply_handled") is True:
            yield Event(
                author=CONFIRM_STAGE_NAME,
                actions=EventActions(
                    state_delta={
                        "confirmation_bodies": {},
                        "skipped_docs": list(
                            ctx.session.state.get("skipped_docs", [])
                        ),
                    }
                ),
            )
            return

        process_results = ctx.session.state.get("process_results")
        if process_results is None:
            raise ValueError(
                "ConfirmStage requires PersistStage to have populated "
                "state['process_results']"
            )

        envelope_dict = ctx.session.state.get("envelope")
        if envelope_dict is None:
            raise ValueError(
                "ConfirmStage requires IngestStage to have populated "
                "state['envelope']"
            )

        envelope = EmailEnvelope.model_validate(envelope_dict)
        skipped_docs: list[dict[str, Any]] = list(
            ctx.session.state.get("skipped_docs", [])
        )

        auto_entries = [
            entry
            for entry in process_results
            if entry.get("result", {}).get("kind") == "order"
        ]

        if not auto_entries:
            yield Event(
                author=CONFIRM_STAGE_NAME,
                actions=EventActions(
                    state_delta={
                        "confirmation_bodies": {},
                        "skipped_docs": skipped_docs,
                    }
                ),
            )
            return

        confirmation_bodies: dict[str, Any] = {}

        for entry in auto_entries:
            order_dict = entry["result"]["order"]
            customer_name = order_dict["customer"]["name"]
            order_ref = order_dict["source_message_id"]
            order_details = _compose_order_details(order_dict)

            # Seed the LlmAgent's {state_key} placeholders by DIRECT
            # mutation of session.state. ADK resolves those placeholders
            # against ctx.session.state at model-call time; in a single
            # invocation (no Runner committing state_delta between
            # stages), state_delta-based seeding would not reach the
            # child. See the ADK cheatsheet §5 (ConditionalRouter).
            ctx.session.state["customer_name"] = customer_name
            ctx.session.state["original_subject"] = envelope.subject
            ctx.session.state["order_details"] = order_details
            ctx.session.state["order_ref"] = order_ref

            last_confirmation_email: Any = None
            async for event in self._confirm_agent.run_async(ctx):
                if (
                    event.actions
                    and event.actions.state_delta
                    and "confirmation_email" in event.actions.state_delta
                ):
                    last_confirmation_email = event.actions.state_delta[
                        "confirmation_email"
                    ]
                # Forward upward so adk web traces see the child's events.
                yield event

            if last_confirmation_email is None:
                raise RuntimeError(
                    "Confirmation agent did not produce confirmation_email "
                    f"for {entry['filename']}#{entry['sub_doc_index']}"
                )

            body_value = (
                last_confirmation_email.model_dump(mode="json")
                if hasattr(last_confirmation_email, "model_dump")
                else last_confirmation_email
            )
            # ConfirmationEmail schema guarantees {subject, body}; fail
            # fast if the output shape ever drifts. Same pattern as
            # persist.py's clarify_body extraction (persist.py:169-176).
            assert "body" in body_value, (
                f"confirmation_email for "
                f"{entry['filename']}#{entry['sub_doc_index']} is "
                f"missing the 'body' field: {body_value!r}"
            )
            body_str = body_value["body"]

            await self._order_store.update_with_confirmation(
                order_ref, body_str
            )

            body_key = f"{entry['filename']}#{entry['sub_doc_index']}"
            await self._audit_logger.emit(
                correlation_id=ctx.session.state.get("correlation_id", ""),
                session_id=ctx.session.id,
                source_message_id=self._extract_source_message_id(ctx.session.state),
                stage="lifecycle",
                phase="lifecycle",
                action="email_drafted",
                outcome="ok",
                payload={
                    "order_id": order_ref,
                    "body_key": body_key,
                },
            )

            confirmation_bodies[body_key] = body_value

        yield Event(
            author=CONFIRM_STAGE_NAME,
            actions=EventActions(
                state_delta={
                    "confirmation_bodies": confirmation_bodies,
                    "skipped_docs": skipped_docs,
                }
            ),
            content=types.Content(
                role="model",
                parts=[
                    types.Part(
                        text=(
                            f"Drafted {len(confirmation_bodies)} confirmation "
                            f"email(s); {len(skipped_docs)} skipped upstream"
                        )
                    )
                ],
            ),
        )


def _compose_order_details(order_dict: dict[str, Any]) -> str:
    """Render OrderRecord (dict form from process_results) into a block
    for the confirmation email prompt.

    One line per item + total + ship-to + payment terms. The LLM quotes
    this verbatim in the body, so formatting changes here become
    customer-visible.
    """
    customer = order_dict["customer"]
    bill_to = customer["bill_to"]
    addr = (
        f"{bill_to['street1']}, {bill_to['city']}, "
        f"{bill_to['state']} {bill_to['zip']}"
    )
    lines = "\n".join(
        f"  {line['quantity']} {line['product']['uom']} "
        f"{line['product']['sku']}  "
        f"{line['product']['short_description']}  "
        f"@ ${line['product']['price_at_time']:.2f}  =  "
        f"${line['line_total']:.2f}"
        for line in order_dict["lines"]
    )
    return (
        f"Line items:\n{lines}\n\n"
        f"Order total: ${order_dict['order_total']:.2f}\n"
        f"Ship-to: {addr}\n"
        f"Payment terms: {customer['payment_terms']}"
    )


__all__ = ["CONFIRM_STAGE_NAME", "ConfirmStage"]
