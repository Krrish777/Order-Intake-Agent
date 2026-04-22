"""The :class:`FinalizeStage` — stage #8 (last) of the Order Intake pipeline.

Runs after :class:`~backend.my_agent.stages.persist.PersistStage`. Its
job is to compute deterministic counts from the pipeline state
(``process_results``, ``skipped_docs``, ``reply_handled``), seed them on
``ctx.session.state`` so the injected summary-agent's
``INSTRUCTION_TEMPLATE`` (in ``backend/prompts/summary.py``) can
interpolate ``{orders_created}``, ``{exceptions_opened}``,
``{docs_skipped}``, and ``{reply_handled}`` placeholders at model-call
time, invoke the summary :class:`~google.adk.agents.LlmAgent` exactly
once, capture the emitted ``run_summary`` dict, and publish it at
``state['run_summary']``.

Unlike :class:`~backend.my_agent.stages.clarify.ClarifyStage`, this
stage **always runs** — there is no ``reply_handled`` short-circuit.
The summary agent produces output even for reply-handled invocations
(the prompt template already surfaces the ``reply_handled`` flag so
the model can frame the summary appropriately).

The child LlmAgent has ``output_schema=RunSummary`` and
``output_key="run_summary"``; on its final event the validated model
lands on ``event.actions.state_delta["run_summary"]``. We forward each
child event upward (so adk web traces see them) and capture the last
``run_summary`` we saw as the final value.

Duplicates handling: :class:`~backend.persistence.coordinator.ProcessResult`
has ``kind`` ∈ ``{"order", "exception", "duplicate"}``. The
:class:`~backend.models.run_summary.RunSummary` schema has no
``duplicates`` field, so duplicates are not surfaced to the summary
agent's input state keys. They are computed locally as a defensive
breakdown in case a future review wants the number — but discarded
before invoking the child.
"""

from __future__ import annotations

from typing import Any, AsyncGenerator, Final

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions
from google.genai import types
from pydantic import PrivateAttr

FINALIZE_STAGE_NAME: Final[str] = "finalize_stage"


class FinalizeStage(BaseAgent):
    """BaseAgent that invokes the summary LlmAgent with deterministic counts.

    Note: this stage coerces ``'reply_handled'`` to a ``bool`` on
    ``ctx.session.state`` (default ``False``) when seeding the summary
    template; downstream readers of that key after FinalizeStage see
    the coerced value.

    Dep-injection choice: **PrivateAttr typed as ``Any``**. Same
    reasoning as :class:`ClarifyStage`: the real injected value is an
    :class:`~google.adk.agents.LlmAgent` (a Pydantic ``BaseModel``), but
    unit tests swap in a lightweight duck-typed fake. Typing the attr
    as ``LlmAgent`` would require ``arbitrary_types_allowed=True`` and
    force the fake to inherit from ``LlmAgent`` (Pydantic uses
    ``isinstance`` against the concrete type). ``Any`` is the path of
    least resistance.
    """

    name: str = FINALIZE_STAGE_NAME
    _summary_agent: Any = PrivateAttr()

    def __init__(self, *, summary_agent: Any, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._summary_agent = summary_agent

    async def _run_async_impl(  # type: ignore[override]
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        process_results: list[dict[str, Any]] = list(
            ctx.session.state.get("process_results", [])
        )
        skipped_docs: list[dict[str, Any]] = list(
            ctx.session.state.get("skipped_docs", [])
        )
        reply_handled: bool = bool(
            ctx.session.state.get("reply_handled", False)
        )

        orders_created = sum(
            1
            for r in process_results
            if r.get("result", {}).get("kind") == "order"
        )
        exceptions_opened = sum(
            1
            for r in process_results
            if r.get("result", {}).get("kind") == "exception"
        )
        # duplicates computed for symmetry with ProcessResult.kind but
        # NOT surfaced to the summary agent — RunSummary has no
        # duplicates field, and the prompt template does not reference
        # it. Left here so a future reviewer can see the full breakdown.
        duplicates = sum(  # noqa: F841  (intentionally unused)
            1
            for r in process_results
            if r.get("result", {}).get("kind") == "duplicate"
        )
        docs_skipped = len(skipped_docs)

        # Seed the summary LlmAgent's {state_key} placeholders by DIRECT
        # mutation of session.state. ADK resolves those placeholders
        # against ctx.session.state at model-call time; in a single
        # invocation (no Runner committing state_delta between stages),
        # state_delta-based seeding would not reach the child. See the
        # ADK cheatsheet §5 (ConditionalRouter) and the same pattern in
        # ClarifyStage.
        ctx.session.state["orders_created"] = orders_created
        ctx.session.state["exceptions_opened"] = exceptions_opened
        ctx.session.state["docs_skipped"] = docs_skipped
        ctx.session.state["reply_handled"] = reply_handled

        last_run_summary: Any = None
        async for event in self._summary_agent.run_async(ctx):
            if (
                event.actions
                and event.actions.state_delta
                and "run_summary" in event.actions.state_delta
            ):
                last_run_summary = event.actions.state_delta["run_summary"]
            # Forward upward so adk web traces see the child's events.
            yield event

        if last_run_summary is None:
            raise RuntimeError(
                "Summary agent did not produce run_summary on any "
                "event's state_delta"
            )

        run_summary_dict = (
            last_run_summary.model_dump(mode="json")
            if hasattr(last_run_summary, "model_dump")
            else last_run_summary
        )

        yield Event(
            author=FINALIZE_STAGE_NAME,
            actions=EventActions(
                state_delta={"run_summary": run_summary_dict}
            ),
            content=types.Content(
                role="model",
                parts=[
                    types.Part(
                        text=(
                            f"Run summary: {orders_created} order(s), "
                            f"{exceptions_opened} exception(s), "
                            f"{docs_skipped} skipped"
                            + (", reply_handled" if reply_handled else "")
                        )
                    )
                ],
            ),
        )


__all__ = ["FINALIZE_STAGE_NAME", "FinalizeStage"]
