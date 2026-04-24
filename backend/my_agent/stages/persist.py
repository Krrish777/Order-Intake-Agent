"""The :class:`PersistStage` — stage #7 of the Order Intake pipeline.

Runs after :class:`~backend.my_agent.stages.clarify.ClarifyStage`. For
every entry in ``state['parsed_docs']`` this stage re-hydrates the full
:class:`~backend.models.parsed_document.ParsedDocument` and awaits the
injected :class:`~backend.persistence.coordinator.IntakeCoordinator` to
route the order into either the ``orders`` or ``exceptions`` Firestore
collection. The coordinator owns the branching logic (AUTO_APPROVE →
``orders``; CLARIFY / ESCALATE → ``exceptions``) and dedupes on
``source_message_id`` against both stores so Pub/Sub redelivery and
operator retries are safe.

The stage keeps the same flat 1:1 shape as ``parsed_docs``:

.. code-block:: python

    state["process_results"] = [
        {"filename": ..., "sub_doc_index": ..., "result": {...}},
        ...
    ]

``result`` is :meth:`ProcessResult.model_dump` with ``mode='json'`` so
the discriminator (``kind="order"`` / ``"exception"`` / ``"duplicate"``)
and nested :class:`OrderRecord` / :class:`ExceptionRecord` serialise to
plain JSON for FinalizeStage to summarise.

The CLARIFY body produced upstream is threaded through here so the
persisted :class:`ExceptionRecord` carries the Gemini-drafted email on
creation. ``state['clarify_bodies']`` is keyed by
``"{filename}#{sub_doc_index}"`` and each value is a ``{subject, body}``
dict; the coordinator only cares about the body string, so we pass the
``body`` field alone. A missing key (non-CLARIFY decisions, or short-
circuited invocations) yields ``clarify_body=None``.

Orders are processed sequentially. The coordinator's preflight
:meth:`OrderStore.get` + :meth:`ExceptionStore.get` parallelises
internally via :func:`asyncio.gather`, so per-order latency is dominated
by the one validator call and a handful of Firestore round-trips.
Coordinator exceptions propagate — fail-fast is intentional so a broken
Firestore credential doesn't silently drop a batch.

``skipped_docs`` is passed through unchanged. A CLARIFY / ESCALATE
decision is a business outcome, not a pipeline skip.

Short-circuit: if ``state['reply_handled']`` is ``True``, this stage
no-ops — writes ``process_results=[]`` and preserves ``skipped_docs``
unchanged. ``coordinator.process`` is not invoked.
"""

from __future__ import annotations

from typing import Any, AsyncGenerator, Final

from google.adk.agents.invocation_context import InvocationContext
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions
from google.genai import types
from pydantic import PrivateAttr

from backend.ingestion.email_envelope import EmailEnvelope
from backend.models.parsed_document import ParsedDocument
from backend.models.validation_result import ValidationResult
from backend.my_agent.stages._audited import AuditedStage
from backend.persistence.coordinator import IntakeCoordinator

PERSIST_STAGE_NAME: Final[str] = "persist_stage"

_ACTION_FOR_KIND: dict[str, str] = {
    "order": "order_persisted",
    "exception": "exception_opened",
    "duplicate": "duplicate_seen",
}


class PersistStage(AuditedStage):
    """BaseAgent that routes each parsed sub-doc through the coordinator.

    Dep-injection choice: **PrivateAttr** (pattern B) — for template
    uniformity with ValidateStage. :class:`IntakeCoordinator` is a
    concrete class so pattern A (Pydantic field) would technically
    work, but keeping all injected-dep stages on the same shape makes
    Step 5 agent assembly mechanical.
    """

    name: str = PERSIST_STAGE_NAME
    _coordinator: IntakeCoordinator = PrivateAttr()

    def __init__(self, *, coordinator: IntakeCoordinator, audit_logger: Any, **kwargs: Any) -> None:
        super().__init__(audit_logger=audit_logger, **kwargs)
        self._coordinator = coordinator

    async def _audited_run(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        # Short-circuit: a clarify reply was handled upstream. Emit the
        # keys FinalizeStage expects but do not invoke the coordinator
        # or disturb any skipped_docs the earlier stages already wrote.
        if ctx.session.state.get("reply_handled") is True:
            yield Event(
                author=PERSIST_STAGE_NAME,
                actions=EventActions(
                    state_delta={
                        "process_results": [],
                        "skipped_docs": list(
                            ctx.session.state.get("skipped_docs", [])
                        ),
                    }
                ),
            )
            return

        envelope_dict = ctx.session.state.get("envelope")
        if envelope_dict is None:
            raise ValueError(
                "PersistStage requires IngestStage to have populated "
                "state['envelope']"
            )

        parsed_docs = ctx.session.state.get("parsed_docs")
        if parsed_docs is None:
            raise ValueError(
                "PersistStage requires ParseStage to have populated "
                "state['parsed_docs']"
            )

        # PersistStage never adds its own skipped entries — a CLARIFY
        # or ESCALATE decision is a business outcome that lands in the
        # exceptions collection, not a pipeline skip. We copy the
        # upstream list verbatim so the audit trail survives.
        skipped_docs: list[dict[str, Any]] = list(
            ctx.session.state.get("skipped_docs", [])
        )

        if not parsed_docs:
            yield Event(
                author=PERSIST_STAGE_NAME,
                actions=EventActions(
                    state_delta={
                        "process_results": [],
                        "skipped_docs": skipped_docs,
                    }
                ),
            )
            return

        envelope = EmailEnvelope.model_validate(envelope_dict)
        clarify_bodies: dict[str, Any] = ctx.session.state.get(
            "clarify_bodies", {}
        )
        # Re-hydrate ValidateStage's precomputed ValidationResult per
        # sub-doc so coordinator.process can skip the redundant second
        # validator.validate call. Keyed by (filename, sub_doc_index) to
        # match parsed_docs. Missing key → fall back to fresh validation
        # in the coordinator (defensive; ValidateStage should always write).
        validation_results: list[dict[str, Any]] = ctx.session.state.get(
            "validation_results", []
        )
        validation_by_key: dict[tuple[str, int], ValidationResult] = {
            (r["filename"], r["sub_doc_index"]): ValidationResult.model_validate(
                r["validation"]
            )
            for r in validation_results
        }

        process_results: list[dict[str, Any]] = []

        for entry in parsed_docs:
            parsed = ParsedDocument.model_validate(entry["parsed"])
            key = f"{entry['filename']}#{entry['sub_doc_index']}"
            body_dict = clarify_bodies.get(key)
            # ClarifyStage stores {subject, body} dicts; the coordinator
            # only persists the body string onto ExceptionRecord.clarify_body.
            # Fail-fast if ClarifyStage's output schema ever drifts from
            # ClarifyEmail(subject, body).
            if body_dict is None:
                body = None
            else:
                assert "body" in body_dict, (
                    f"clarify_bodies[{key!r}] is missing the 'body' field: "
                    f"{body_dict!r}"
                )
                body = body_dict["body"]

            precomputed = validation_by_key.get(
                (entry["filename"], entry["sub_doc_index"])
            )
            result = await self._coordinator.process(
                parsed,
                envelope,
                order_index=entry["sub_doc_index"],
                clarify_body=body,
                precomputed_validation=precomputed,
            )
            action = _ACTION_FOR_KIND[result.kind]
            _lc_payload: dict[str, Any] = {
                "filename": entry["filename"],
                "sub_doc_index": entry["sub_doc_index"],
            }
            if result.order is not None:
                _lc_payload["order_id"] = result.order.source_message_id
            if result.exception is not None:
                _lc_payload["exception_id"] = result.exception.source_message_id
            await self._audit_logger.emit(
                correlation_id=ctx.session.state.get("correlation_id", ""),
                session_id=ctx.session.id,
                source_message_id=self._extract_source_message_id(ctx.session.state),
                stage="lifecycle",
                phase="lifecycle",
                action=action,
                outcome=result.kind,
                payload=_lc_payload,
            )
            process_results.append(
                {
                    "filename": entry["filename"],
                    "sub_doc_index": entry["sub_doc_index"],
                    "result": result.model_dump(mode="json"),
                }
            )

        yield Event(
            author=PERSIST_STAGE_NAME,
            actions=EventActions(
                state_delta={
                    "process_results": process_results,
                    "skipped_docs": skipped_docs,
                }
            ),
            content=types.Content(
                role="model",
                parts=[
                    types.Part(
                        text=(
                            f"Persisted {len(process_results)} result(s); "
                            f"{len(skipped_docs)} skipped upstream"
                        )
                    )
                ],
            ),
        )


__all__ = ["PERSIST_STAGE_NAME", "PersistStage"]
