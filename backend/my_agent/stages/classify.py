"""The :class:`ClassifyStage` — stage #3 of the Order Intake pipeline.

Runs after :class:`~backend.my_agent.stages.ingest.IngestStage` and
:class:`~backend.my_agent.stages.reply_shortcircuit.ReplyShortCircuitStage`.
The job: classify every attachment on the envelope via LlamaClassify and
split the results into two state buckets that downstream stages consume:

* ``state['classified_docs']`` — :class:`ClassifiedDocument` dicts whose
  ``document_intent == 'purchase_order'``. ParseStage reads this.
* ``state['skipped_docs']`` — ``{filename, stage, reason}`` entries for
  every attachment that wasn't a PO (invoice, RFQ, spam, etc).
  FinalizeStage surfaces these in its run summary.

The injected ``classify_fn`` is a plain synchronous callable — the real
impl (``backend.tools.document_classifier.classifier.classify_document``)
polls LlamaClassify and blocks for up to ~60s per attachment. We wrap
each call in :func:`asyncio.to_thread` so the event loop stays free for
the rest of the agent runtime (callbacks, tracing, session persistence).

Attachments are processed sequentially. Classify calls are independent
and could be run concurrently with :func:`asyncio.gather`, but linear
iteration keeps the ADK trace readable and bounds LlamaCloud concurrency
to one-per-invocation. If speed ever matters here, revisit — for now,
legibility wins.

Short-circuit: if ``state['reply_handled']`` is ``True`` (ReplyShortCircuitStage
caught a clarify reply), this stage no-ops — writes an empty
``classified_docs`` and preserves any existing ``skipped_docs`` so the
downstream keys still exist for readers that expect them.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, AsyncGenerator, Final

from google.adk.agents.invocation_context import InvocationContext
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions
from google.genai import types
from pydantic import PrivateAttr

from backend.ingestion.email_envelope import EmailEnvelope
from backend.models.classified_document import ClassifiedDocument
from backend.my_agent.stages._audited import AuditedStage

CLASSIFY_STAGE_NAME: Final[str] = "classify_stage"

#: The blocking callable that turns (content, filename) into a
#: :class:`ClassifiedDocument`. Production impl is
#: ``backend.tools.document_classifier.classifier.classify_document``.
ClassifyFn = Callable[[bytes, str], ClassifiedDocument]


class ClassifyStage(AuditedStage):
    """BaseAgent that classifies each attachment and splits PO from non-PO.

    Dep-injection choice: **PrivateAttr** (pattern B) — same rationale as
    :class:`ReplyShortCircuitStage`. Pydantic can't build an ``isinstance``
    validator for a :class:`typing.Callable` alias any more happily than
    it can for a ``Protocol``, so we keep the dep off the public field
    surface.
    """

    name: str = CLASSIFY_STAGE_NAME
    _classify_fn: ClassifyFn = PrivateAttr()

    def __init__(self, *, classify_fn: ClassifyFn, audit_logger: Any, **kwargs: Any) -> None:
        super().__init__(audit_logger=audit_logger, **kwargs)
        self._classify_fn = classify_fn

    async def _audited_run(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        # Short-circuit: a clarify reply was handled upstream. Write empty
        # classified_docs so downstream reads are stable, but preserve any
        # pre-existing skipped_docs (no stage has written any yet in the
        # current topology, but defensive if ordering ever changes).
        if ctx.session.state.get("reply_handled") is True:
            yield Event(
                author=CLASSIFY_STAGE_NAME,
                actions=EventActions(
                    state_delta={
                        "classified_docs": [],
                        "skipped_docs": ctx.session.state.get("skipped_docs", []),
                    }
                ),
            )
            return

        envelope_dict = ctx.session.state.get("envelope")
        if envelope_dict is None:
            raise ValueError(
                "ClassifyStage requires IngestStage to have populated "
                "state['envelope']"
            )
        envelope = EmailEnvelope.model_validate(envelope_dict)

        # Defensive: IngestStage synthesises a body.txt attachment for
        # body-only emails, so this should never be empty in practice.
        if not envelope.attachments:
            yield Event(
                author=CLASSIFY_STAGE_NAME,
                actions=EventActions(
                    state_delta={"classified_docs": [], "skipped_docs": []}
                ),
            )
            return

        classified_docs: list[dict[str, Any]] = []
        skipped_docs: list[dict[str, Any]] = []

        for attachment in envelope.attachments:
            # to_thread forwards *args/**kwargs to the callable; the wrapped
            # classify_document is a regular sync function that polls
            # LlamaClassify for up to ~60s. Offloading keeps the loop free.
            # Exceptions propagate — fail-fast is the pipeline contract.
            classified = await asyncio.to_thread(
                self._classify_fn, attachment.content, attachment.filename
            )

            if classified.document_intent == "purchase_order":
                classified_docs.append(classified.model_dump(mode="json"))
            else:
                skipped_docs.append(
                    {
                        "filename": attachment.filename,
                        "stage": CLASSIFY_STAGE_NAME,
                        "reason": (
                            f"intent={classified.document_intent} "
                            f"(confidence={classified.intent_confidence:.2f})"
                        ),
                    }
                )

        yield Event(
            author=CLASSIFY_STAGE_NAME,
            actions=EventActions(
                state_delta={
                    "classified_docs": classified_docs,
                    "skipped_docs": skipped_docs,
                }
            ),
            content=types.Content(
                role="model",
                parts=[
                    types.Part(
                        text=(
                            f"Classified {len(envelope.attachments)} attachment(s): "
                            f"{len(classified_docs)} purchase_order, "
                            f"{len(skipped_docs)} skipped"
                        )
                    )
                ],
            ),
        )


__all__ = ["CLASSIFY_STAGE_NAME", "ClassifyFn", "ClassifyStage"]
