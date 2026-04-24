"""The :class:`ClarifyStage` — stage #6 of the Order Intake pipeline.

Runs after :class:`~backend.my_agent.stages.validate.ValidateStage`. For
every entry in ``state['validation_results']`` whose ``validation.decision``
is ``"clarify"``, this stage invokes the injected clarify-email
:class:`~google.adk.agents.LlmAgent` to draft a short customer-facing
clarification, and collects the resulting ``ClarifyEmail`` dicts into
``state['clarify_bodies']`` keyed by ``"{filename}#{sub_doc_index}"``.

This is the first stage in the pipeline to **hold a child LlmAgent** and
drive it via ``await self._clarify_agent.run_async(ctx)``. Two things
about that pattern that matter here and will carry into FinalizeStage:

1. The clarify LlmAgent's instruction template (``backend/prompts/
   clarify_email.py``) contains ``{customer_name}``, ``{original_subject}``,
   and ``{reason}`` placeholders. ADK resolves those against
   ``ctx.session.state`` **at model-call time**, inside ``run_async``. In
   production a Runner applies each stage's ``state_delta`` to session
   state between stages, but a single-invocation parent→child seed
   within the same ``run_async`` call happens before any delta has been
   committed — so we must **mutate ``ctx.session.state`` directly**
   before invoking the child. This is the pattern the ADK cheatsheet
   documents for ``ConditionalRouter`` (§5).
2. The child LlmAgent has ``output_schema=ClarifyEmail`` and
   ``output_key="clarify_email"``, so on its final event the validated
   model lands on ``event.actions.state_delta["clarify_email"]``. We
   forward each child event upward (so adk web traces see them) and
   capture the last ``clarify_email`` we saw as the body for that
   CLARIFY-tier entry.

``skipped_docs`` is passed through unchanged — a CLARIFY decision is a
business outcome, not a pipeline skip. The order still flows downstream
to PersistStage, which pairs the clarify body with the exception record.

Short-circuit: if ``state['reply_handled']`` is ``True``, this stage
no-ops — writes ``clarify_bodies={}`` and preserves ``skipped_docs``
unchanged. The child LlmAgent is not invoked.
"""

from __future__ import annotations

from typing import Any, AsyncGenerator, Final

from google.adk.agents.invocation_context import InvocationContext
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions
from google.genai import types
from pydantic import PrivateAttr

from backend.ingestion.email_envelope import EmailEnvelope
from backend.models.validation_result import ValidationResult
from backend.my_agent.stages._audited import AuditedStage

CLARIFY_STAGE_NAME: Final[str] = "clarify_stage"


class ClarifyStage(AuditedStage):
    """BaseAgent that drafts clarify emails for CLARIFY-tier validations.

    Dep-injection choice: **PrivateAttr typed as ``Any``**. The real
    injected value is an :class:`~google.adk.agents.LlmAgent` (a
    Pydantic ``BaseModel``), but unit tests swap in a lightweight
    duck-typed fake that only implements ``run_async``. Typing the attr
    as ``LlmAgent`` would require ``arbitrary_types_allowed=True`` on
    the model config **and** the fake would have to inherit from
    ``LlmAgent`` (since Pydantic uses an ``isinstance`` check against
    the concrete type). ``Any`` is the path of least resistance and
    matches how we handled the same situation in 4b.
    """

    name: str = CLARIFY_STAGE_NAME
    _clarify_agent: Any = PrivateAttr()

    def __init__(self, *, clarify_agent: Any, audit_logger: Any, **kwargs: Any) -> None:
        super().__init__(audit_logger=audit_logger, **kwargs)
        self._clarify_agent = clarify_agent

    async def _audited_run(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        # Short-circuit: a clarify reply was handled upstream. Emit the
        # keys downstream stages expect but do not invoke the child
        # LlmAgent or disturb any skipped_docs the earlier stages wrote.
        if ctx.session.state.get("reply_handled") is True:
            yield Event(
                author=CLARIFY_STAGE_NAME,
                actions=EventActions(
                    state_delta={
                        "clarify_bodies": {},
                        "skipped_docs": list(
                            ctx.session.state.get("skipped_docs", [])
                        ),
                    }
                ),
            )
            return

        validation_results = ctx.session.state.get("validation_results")
        if validation_results is None:
            raise ValueError(
                "ClarifyStage requires ValidateStage to have populated "
                "state['validation_results']"
            )

        envelope_dict = ctx.session.state.get("envelope")
        if envelope_dict is None:
            raise ValueError(
                "ClarifyStage requires IngestStage to have populated "
                "state['envelope']"
            )

        envelope = EmailEnvelope.model_validate(envelope_dict)
        skipped_docs: list[dict[str, Any]] = list(
            ctx.session.state.get("skipped_docs", [])
        )

        clarify_entries = [
            entry
            for entry in validation_results
            if entry["validation"]["decision"] == "clarify"
        ]

        if not clarify_entries:
            yield Event(
                author=CLARIFY_STAGE_NAME,
                actions=EventActions(
                    state_delta={
                        "clarify_bodies": {},
                        "skipped_docs": skipped_docs,
                    }
                ),
            )
            return

        clarify_bodies: dict[str, Any] = {}

        for entry in clarify_entries:
            validation = entry["validation"]

            customer = validation.get("customer")
            customer_name = (
                customer["name"] if customer else "the customer"
            )
            reason = _compose_reason(validation)

            # Seed the LlmAgent's {state_key} placeholders by DIRECT
            # mutation of session.state. ADK resolves those placeholders
            # against ctx.session.state at model-call time; in a single
            # invocation (no Runner committing state_delta between
            # stages), state_delta-based seeding would not reach the
            # child. See the ADK cheatsheet §5 (ConditionalRouter).
            ctx.session.state["customer_name"] = customer_name
            ctx.session.state["original_subject"] = envelope.subject
            ctx.session.state["reason"] = reason

            last_clarify_email: Any = None
            async for event in self._clarify_agent.run_async(ctx):
                if (
                    event.actions
                    and event.actions.state_delta
                    and "clarify_email" in event.actions.state_delta
                ):
                    last_clarify_email = event.actions.state_delta[
                        "clarify_email"
                    ]
                # Forward upward so adk web traces see the child's events.
                yield event

            if last_clarify_email is None:
                raise RuntimeError(
                    "Clarify agent did not produce clarify_email for "
                    f"{entry['filename']}#{entry['sub_doc_index']}"
                )

            body_value = (
                last_clarify_email.model_dump(mode="json")
                if hasattr(last_clarify_email, "model_dump")
                else last_clarify_email
            )
            key = f"{entry['filename']}#{entry['sub_doc_index']}"
            clarify_bodies[key] = body_value

        yield Event(
            author=CLARIFY_STAGE_NAME,
            actions=EventActions(
                state_delta={
                    "clarify_bodies": clarify_bodies,
                    "skipped_docs": skipped_docs,
                }
            ),
            content=types.Content(
                role="model",
                parts=[
                    types.Part(
                        text=(
                            f"Drafted {len(clarify_bodies)} clarify email(s); "
                            f"{len(skipped_docs)} skipped upstream"
                        )
                    )
                ],
            ),
        )


def _compose_reason(validation: dict[str, Any]) -> str:
    """Concatenate per-line failure notes into one human-readable summary.

    Duplicates :func:`backend.persistence.coordinator._compose_reason`'s
    algorithm against the dict form (since ``validation_results`` carries
    ``ValidationResult.model_dump(mode='json')``, not the live model).
    The symbol is module-private over there, so inline duplication keeps
    the import surface clean.
    """
    failing = [
        f"Line {ln['line_index']}: " + "; ".join(ln["notes"])
        for ln in validation["lines"]
        if ln.get("notes")
    ]
    if not failing:
        return validation.get("rationale", "")
    return " | ".join(failing)


__all__ = ["CLARIFY_STAGE_NAME", "ClarifyStage"]
