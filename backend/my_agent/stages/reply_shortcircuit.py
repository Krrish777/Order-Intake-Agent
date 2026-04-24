"""The :class:`ReplyShortCircuitStage` — stage #2 of the Order Intake pipeline.

Runs immediately after :class:`~backend.my_agent.stages.ingest.IngestStage`.
The job: detect whether the freshly-ingested email is a reply to a previous
*clarify* email we sent, and if so, short-circuit the rest of the pipeline.

Decision tree:

1. No ``in_reply_to`` header (or empty) → ordinary new email. Write
   ``state['reply_handled'] = False`` explicitly so downstream reads are
   stable, pass through.
2. ``in_reply_to`` set but no ``PENDING_CLARIFY`` exception on the thread →
   the clarify is closed, or this is a stray reply. Write
   ``reply_handled=False`` and let downstream process it as a new email.
3. ``in_reply_to`` set AND there's a matching pending exception → advance
   the exception PENDING_CLARIFY → AWAITING_REVIEW via
   :meth:`~backend.persistence.base.ExceptionStore.update_with_reply`, stash
   the reply body on state for the review surface, and set
   ``reply_handled=True`` so stages 4c-4h no-op for this invocation.

If :meth:`update_with_reply` raises (status guard, missing doc) the error
propagates — fail-fast; the pipeline is not the place to paper over
concurrency violations.
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
from backend.persistence.base import ExceptionStore

REPLY_SHORTCIRCUIT_STAGE_NAME: Final[str] = "reply_shortcircuit_stage"


class ReplyShortCircuitStage(AuditedStage):
    """BaseAgent that correlates clarify replies to pending exceptions.

    Dep-injection choice: **PrivateAttr** (pattern B). ADK's ``BaseAgent``
    sets ``arbitrary_types_allowed=True`` but Pydantic still refuses to
    build an ``isinstance`` validator for a :class:`typing.Protocol`, so a
    public Pydantic field won't work. ``PrivateAttr`` sidesteps validation
    entirely and keeps the dep out of ``model_dump``. Steps 4c-4h copy
    this pattern.
    """

    name: str = REPLY_SHORTCIRCUIT_STAGE_NAME
    _exception_store: ExceptionStore = PrivateAttr()

    def __init__(self, *, exception_store: ExceptionStore, audit_logger: Any, **kwargs: Any) -> None:
        super().__init__(audit_logger=audit_logger, **kwargs)
        self._exception_store = exception_store

    async def _audited_run(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        envelope_dict = ctx.session.state.get("envelope")
        if envelope_dict is None:
            raise ValueError(
                "ReplyShortCircuitStage requires IngestStage to have "
                "populated state['envelope']"
            )
        envelope = EmailEnvelope.model_validate(envelope_dict)

        # Treat missing and empty-string in_reply_to the same: not a reply.
        if not envelope.in_reply_to:
            yield Event(
                author=REPLY_SHORTCIRCUIT_STAGE_NAME,
                actions=EventActions(state_delta={"reply_handled": False}),
            )
            return

        thread_id = envelope.thread_id or envelope.message_id
        pending = await self._exception_store.find_pending_clarify(thread_id)

        if pending is None:
            yield Event(
                author=REPLY_SHORTCIRCUIT_STAGE_NAME,
                actions=EventActions(state_delta={"reply_handled": False}),
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            text=(
                                f"reply detected but no pending clarify "
                                f"found for thread {thread_id} — downstream "
                                f"will process as a new email"
                            )
                        )
                    ],
                ),
            )
            return

        updated = await self._exception_store.update_with_reply(
            source_message_id=pending.source_message_id,
            reply_message_id=envelope.message_id,
        )

        yield Event(
            author=REPLY_SHORTCIRCUIT_STAGE_NAME,
            actions=EventActions(
                state_delta={
                    "reply_handled": True,
                    "reply_parent_source_message_id": pending.source_message_id,
                    "reply_updated_exception": updated.model_dump(mode="json"),
                    "reply_body_text": envelope.body_text,
                }
            ),
            content=types.Content(
                role="model",
                parts=[
                    types.Part(
                        text=(
                            f"Reply detected on thread {thread_id} — "
                            f"advanced exception {pending.source_message_id} "
                            f"from PENDING_CLARIFY → AWAITING_REVIEW"
                        )
                    )
                ],
            ),
        )


__all__ = ["REPLY_SHORTCIRCUIT_STAGE_NAME", "ReplyShortCircuitStage"]
