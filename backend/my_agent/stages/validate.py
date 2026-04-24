"""The :class:`ValidateStage` — stage #5 of the Order Intake pipeline.

Runs after :class:`~backend.my_agent.stages.parse.ParseStage`. For every
entry in ``state['parsed_docs']`` (one-per-:class:`ExtractedOrder`), this
stage re-hydrates the extracted order from its ``sub_doc`` dict and awaits
the injected :class:`~backend.tools.order_validator.OrderValidator`
coroutine to produce a :class:`ValidationResult` carrying a routing
decision (AUTO_APPROVE / CLARIFY / ESCALATE).

The stage keeps the same flat 1:1 shape as ``parsed_docs``:

.. code-block:: python

    state["validation_results"] = [
        {"filename": ..., "sub_doc_index": ..., "validation": {...}},
        ...
    ]

``validation`` is :meth:`ValidationResult.model_dump` with ``mode='json'``
so the :class:`~backend.models.validation_result.RoutingDecision` enum
serialises to its ``.value`` string (``"auto_approve"``, ``"clarify"``,
``"escalate"``). ClarifyStage + PersistStage branch on
``validation["decision"]`` and consume the per-line details.

Unlike ClassifyStage / ParseStage, validator.validate is **natively
async**, so we ``await`` it directly — no :func:`asyncio.to_thread`
wrapping (that would spin up a thread just to run a coroutine, which is a
broken pattern). Orders are processed sequentially; the validator's
:class:`MasterDataRepo` caches the master data so a multi-order email
takes one Firestore round-trip regardless.

``skipped_docs`` is passed through unchanged. A CLARIFY or ESCALATE
decision is a **business outcome**, not a pipeline skip — the order
still flows downstream so ClarifyStage / PersistStage can act on it.

Short-circuit: if ``state['reply_handled']`` is ``True``, this stage
no-ops — writes ``validation_results=[]`` and preserves ``skipped_docs``
unchanged. ``validator.validate`` is not called.
"""

from __future__ import annotations

from typing import Any, AsyncGenerator, Final

from google.adk.agents.invocation_context import InvocationContext
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions
from google.genai import types
from pydantic import PrivateAttr

from backend.models.parsed_document import ExtractedOrder
from backend.models.validation_result import ValidationResult
from backend.my_agent.stages._audited import AuditedStage
from backend.tools.order_validator import OrderValidator

VALIDATE_STAGE_NAME: Final[str] = "validate_stage"


class ValidateStage(AuditedStage):
    """BaseAgent that validates each extracted order against master data.

    Dep-injection choice: **PrivateAttr** (pattern B) — for template
    uniformity with ClassifyStage / ParseStage. :class:`OrderValidator`
    is a concrete class so pattern A (Pydantic field) would technically
    work, but keeping all injected-dep stages on the same shape makes
    Step 5 agent assembly mechanical.
    """

    name: str = VALIDATE_STAGE_NAME
    _validator: OrderValidator = PrivateAttr()

    def __init__(self, *, validator: OrderValidator, audit_logger: Any, **kwargs: Any) -> None:
        super().__init__(audit_logger=audit_logger, **kwargs)
        self._validator = validator

    async def _audited_run(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        # Short-circuit: a clarify reply was handled upstream. Emit the
        # keys downstream stages expect but do not invoke the validator
        # or disturb any skipped_docs the earlier stages already wrote.
        if ctx.session.state.get("reply_handled") is True:
            yield Event(
                author=VALIDATE_STAGE_NAME,
                actions=EventActions(
                    state_delta={
                        "validation_results": [],
                        "skipped_docs": ctx.session.state.get("skipped_docs", []),
                    }
                ),
            )
            return

        parsed_docs = ctx.session.state.get("parsed_docs")
        if parsed_docs is None:
            raise ValueError(
                "ValidateStage requires ParseStage to have populated "
                "state['parsed_docs']"
            )

        # ValidateStage never adds its own skipped entries — a CLARIFY
        # or ESCALATE decision is a business outcome that still flows
        # downstream. We copy the upstream list verbatim so the audit
        # trail survives this stage unchanged.
        skipped_docs: list[dict[str, Any]] = list(
            ctx.session.state.get("skipped_docs", [])
        )

        if not parsed_docs:
            yield Event(
                author=VALIDATE_STAGE_NAME,
                actions=EventActions(
                    state_delta={
                        "validation_results": [],
                        "skipped_docs": skipped_docs,
                    }
                ),
            )
            return

        validation_results: list[dict[str, Any]] = []

        # Pull message_id once for the dup-preflight kwarg; fall back to ""
        # so stage tests that don't seed envelope still exercise the validator.
        envelope: dict[str, Any] = ctx.session.state.get("envelope") or {}
        msg_id: str = envelope.get("message_id") or ""

        for entry in parsed_docs:
            order = ExtractedOrder.model_validate(entry["sub_doc"])
            validation: ValidationResult = await self._validator.validate(
                order, source_message_id=msg_id
            )
            validation_results.append(
                {
                    "filename": entry["filename"],
                    "sub_doc_index": entry["sub_doc_index"],
                    "validation": validation.model_dump(mode="json"),
                }
            )

        yield Event(
            author=VALIDATE_STAGE_NAME,
            actions=EventActions(
                state_delta={
                    "validation_results": validation_results,
                    "skipped_docs": skipped_docs,
                }
            ),
            content=types.Content(
                role="model",
                parts=[
                    types.Part(
                        text=(
                            f"Validated {len(validation_results)} order(s); "
                            f"{len(skipped_docs)} skipped upstream"
                        )
                    )
                ],
            ),
        )


__all__ = ["VALIDATE_STAGE_NAME", "ValidateStage"]
