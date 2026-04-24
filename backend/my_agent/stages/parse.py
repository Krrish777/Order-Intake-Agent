"""The :class:`ParseStage` — stage #4 of the Order Intake pipeline.

Runs after :class:`~backend.my_agent.stages.classify.ClassifyStage`. For
every purchase-order attachment in ``state['classified_docs']``, this stage
calls the injected sync ``parse_fn`` (``backend.tools.document_parser.
legacy.parser.parse_document`` in production) to produce a
:class:`~backend.models.parsed_document.ParsedDocument`, then flattens its
``sub_documents`` list into ``state['parsed_docs']`` — one flat entry per
extracted order. ValidateStage, ClarifyStage, and PersistStage iterate
that flat list instead of re-navigating nested parser output.

A single input attachment may produce ``0..N`` output entries:

* ``N >= 1``: one entry per ``ExtractedOrder`` in ``parsed.sub_documents``.
* ``N == 0``: parser returned an empty ``sub_documents`` list (unusable
  result). The filename is appended to ``state['skipped_docs']`` with
  ``stage='parse_stage'`` and ``reason='parser returned zero
  sub_documents'``. **No entry is written to ``parsed_docs`` for that
  source.**

``skipped_docs`` is APPEND-not-overwrite: this stage READS the existing
list (populated by ClassifyStage), copies it, and extends the copy with
its own parse-time skips. ClassifyStage's non-PO skips must survive.

Just like ClassifyStage, the blocking sync ``parse_fn`` is dispatched via
:func:`asyncio.to_thread` so the event loop stays free during the multi-
second LlamaExtract poll. Attachments are processed sequentially for
trace legibility; LlamaCloud concurrency is bounded to one per invocation.

Short-circuit: if ``state['reply_handled']`` is ``True`` (a clarify reply
was handled upstream), this stage no-ops — writes ``parsed_docs=[]`` and
preserves the existing ``skipped_docs`` unchanged. ``parse_fn`` is not
called.
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
from backend.models.parsed_document import ParsedDocument
from backend.my_agent.stages._audited import AuditedStage

PARSE_STAGE_NAME: Final[str] = "parse_stage"

#: The blocking callable that turns (content, filename) into a
#: :class:`ParsedDocument`. Production impl is
#: ``backend.tools.document_parser.legacy.parser.parse_document``.
#: The stage only uses positional args; extra kwargs (``extra_hint``,
#: ``timeout_s``, ``poll_interval_s``) are owned by the factory that
#: wires the stage.
ParseFn = Callable[[bytes, str], ParsedDocument]


class ParseStage(AuditedStage):
    """BaseAgent that parses each PO attachment and flattens sub-documents.

    Dep-injection choice: **PrivateAttr** (pattern B) — same rationale as
    :class:`ClassifyStage`. Pydantic can't build an ``isinstance``
    validator for a :class:`typing.Callable` alias, so we keep the dep off
    the public field surface.
    """

    name: str = PARSE_STAGE_NAME
    _parse_fn: ParseFn = PrivateAttr()

    def __init__(self, *, parse_fn: ParseFn, audit_logger: Any, **kwargs: Any) -> None:
        super().__init__(audit_logger=audit_logger, **kwargs)
        self._parse_fn = parse_fn

    async def _audited_run(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        # Short-circuit: a clarify reply was handled upstream. Emit the
        # keys downstream stages expect but do not invoke the parser or
        # disturb any skipped_docs the earlier stages already wrote.
        if ctx.session.state.get("reply_handled") is True:
            yield Event(
                author=PARSE_STAGE_NAME,
                actions=EventActions(
                    state_delta={
                        "parsed_docs": [],
                        "skipped_docs": ctx.session.state.get("skipped_docs", []),
                    }
                ),
            )
            return

        envelope_dict = ctx.session.state.get("envelope")
        if envelope_dict is None:
            raise ValueError(
                "ParseStage requires IngestStage to have populated "
                "state['envelope']"
            )
        envelope = EmailEnvelope.model_validate(envelope_dict)

        classified_docs = ctx.session.state.get("classified_docs")
        if classified_docs is None:
            raise ValueError(
                "ParseStage requires ClassifyStage to have populated "
                "state['classified_docs']"
            )

        # APPEND-not-overwrite contract: start from whatever ClassifyStage
        # (and any earlier stage in the reply path) already wrote, then
        # extend with our own skips.
        skipped_docs: list[dict[str, Any]] = list(
            ctx.session.state.get("skipped_docs", [])
        )

        # All PO-tier attachments were dropped upstream → nothing to parse,
        # but the downstream key must still exist.
        if not classified_docs:
            yield Event(
                author=PARSE_STAGE_NAME,
                actions=EventActions(
                    state_delta={
                        "parsed_docs": [],
                        "skipped_docs": skipped_docs,
                    }
                ),
            )
            return

        # Bytes round-trip is fixed (commit fda48a0) so att.content here is
        # the real binary payload again.
        filename_to_bytes: dict[str, bytes] = {
            att.filename: att.content for att in envelope.attachments
        }

        parsed_docs: list[dict[str, Any]] = []

        for classified in classified_docs:
            filename = classified["filename"]
            content = filename_to_bytes.get(filename)
            if content is None:
                # ClassifyStage only emits ClassifiedDocuments sourced from
                # envelope.attachments, so this really shouldn't happen.
                raise ValueError(
                    f"ParseStage: no envelope attachment matches "
                    f"classified filename {filename!r}"
                )

            parsed = await asyncio.to_thread(self._parse_fn, content, filename)

            if not parsed.sub_documents:
                skipped_docs.append(
                    {
                        "filename": filename,
                        "stage": PARSE_STAGE_NAME,
                        "reason": "parser returned zero sub_documents",
                    }
                )
                continue

            parsed_snapshot = parsed.model_dump(mode="json")
            for sub_doc_index, sub_doc in enumerate(parsed.sub_documents):
                parsed_docs.append(
                    {
                        "filename": filename,
                        "sub_doc_index": sub_doc_index,
                        "parsed": parsed_snapshot,
                        "sub_doc": sub_doc.model_dump(mode="json"),
                    }
                )

        yield Event(
            author=PARSE_STAGE_NAME,
            actions=EventActions(
                state_delta={
                    "parsed_docs": parsed_docs,
                    "skipped_docs": skipped_docs,
                }
            ),
            content=types.Content(
                role="model",
                parts=[
                    types.Part(
                        text=(
                            f"Parsed {len(classified_docs)} PO attachment(s) "
                            f"into {len(parsed_docs)} sub-document(s); "
                            f"{len(skipped_docs)} skipped total"
                        )
                    )
                ],
            ),
        )


__all__ = ["PARSE_STAGE_NAME", "ParseFn", "ParseStage"]
