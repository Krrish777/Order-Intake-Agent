"""Root SequentialAgent assembly for the Order Intake pipeline.

This module exposes three things:

* :func:`build_root_agent` — a **pure factory** that takes the seven
  dependencies the pipeline stages need (two raw callables, one
  validator, one coordinator, two :class:`~google.adk.agents.LlmAgent`
  children, and one exception-store) and returns a freshly-constructed
  :class:`~google.adk.agents.SequentialAgent` wiring the eight stage
  instances in the canonical order.
* :func:`_build_default_root_agent` — a private helper that constructs
  the production deps (async Firestore client, ``MasterDataRepo``,
  ``OrderValidator``, Firestore-backed stores, ``IntakeCoordinator``,
  the two LlmAgent factories, and the real classify / parse callables)
  and returns the assembled root agent.
* :data:`root_agent` — a module-level :class:`SequentialAgent` that
  ``adk web .`` (and the ADK runner conventions) discover by attribute
  name. It is built at import time, which is deliberate: adk web
  performs attribute-level discovery on the module and expects the
  root agent to already exist. Construction opens an async Firestore
  client — if the environment cannot produce one (missing credentials,
  emulator not running), the underlying exception propagates out of
  the import and will surface to the caller with a clear stack trace.

The canonical stage order is load-bearing. Downstream docstrings,
evalsets, adk web traces, and Step 6 integration tests all assume this
exact sequence:

    1. :class:`~backend.my_agent.stages.ingest.IngestStage` —
       materialise an :class:`EmailEnvelope` from the user content (a
       path to a ``.eml`` file or the raw EML blob).
    2. :class:`~backend.my_agent.stages.reply_shortcircuit.ReplyShortCircuitStage` —
       detect replies to pending clarify emails and advance the
       exception state, short-circuiting the remaining order-extraction
       stages for that invocation.
    3. :class:`~backend.my_agent.stages.classify.ClassifyStage` —
       classify each attachment via LlamaClassify into purchase-order
       vs non-PO buckets.
    4. :class:`~backend.my_agent.stages.parse.ParseStage` — extract
       structured orders from every purchase-order attachment via
       LlamaExtract, flattening ``sub_documents`` for downstream stages.
    5. :class:`~backend.my_agent.stages.validate.ValidateStage` —
       run the :class:`OrderValidator` per extracted order to produce
       a routing decision (AUTO_APPROVE / CLARIFY / ESCALATE).
    6. :class:`~backend.my_agent.stages.clarify.ClarifyStage` — draft
       customer-facing clarify emails for CLARIFY-tier validations via
       the injected clarify-email :class:`LlmAgent`.
    7. :class:`~backend.my_agent.stages.persist.PersistStage` — route
       each parsed order through the :class:`IntakeCoordinator` into
       ``orders`` (auto-approve) or ``exceptions`` (clarify / escalate)
       in Firestore.
    8. :class:`~backend.my_agent.stages.finalize.FinalizeStage` —
       invoke the summary :class:`LlmAgent` with deterministic counts
       and publish the resulting ``run_summary`` on session state.
"""

from __future__ import annotations

from typing import Any, Final

from google.adk.agents import LlmAgent, SequentialAgent

from .agents.clarify_email_agent import build_clarify_email_agent
from .agents.summary_agent import build_summary_agent
from .stages.clarify import ClarifyStage
from .stages.classify import ClassifyFn, ClassifyStage
from .stages.finalize import FinalizeStage
from .stages.ingest import IngestStage
from .stages.parse import ParseFn, ParseStage
from .stages.persist import PersistStage
from .stages.reply_shortcircuit import ReplyShortCircuitStage
from .stages.validate import ValidateStage
from backend.persistence.base import ExceptionStore
from backend.persistence.coordinator import IntakeCoordinator
from backend.persistence.exceptions_store import FirestoreExceptionStore
from backend.persistence.orders_store import FirestoreOrderStore
from backend.tools.document_classifier.classifier import classify_document
from backend.tools.document_parser import parse_document
from backend.tools.order_validator.tools.firestore_client import get_async_client
from backend.tools.order_validator.tools.master_data_repo import MasterDataRepo
from backend.tools.order_validator.validator import OrderValidator

ROOT_AGENT_NAME: Final[str] = "order_intake_pipeline"

#: Sentinel recorded on every persisted ``OrderRecord`` /
#: ``ExceptionRecord`` so downstream analytics can distinguish records
#: written by Track A (this pipeline) from manually-ingested rows.
AGENT_VERSION: Final[str] = "track-a-v0.1"


def build_root_agent(
    *,
    classify_fn: ClassifyFn,
    parse_fn: ParseFn,
    validator: OrderValidator,
    coordinator: IntakeCoordinator,
    clarify_agent: Any,
    summary_agent: Any,
    exception_store: ExceptionStore,
) -> SequentialAgent:
    """Build the root :class:`SequentialAgent` wiring all 8 Track A stages.

    Pure factory: no global state, no side effects, no singletons. Every
    dep is keyword-only (prevents accidental positional swaps when the
    parameter list grows). Each call constructs *fresh* stage instances
    so the same factory may be invoked repeatedly in tests without
    tripping ADK's "agent already has a parent" guard in
    :meth:`BaseAgent.__set_parent_agent_for_sub_agents`.

    The stages are wired in the canonical order (documented in the
    module docstring): ``ingest → reply_shortcircuit → classify →
    parse → validate → clarify → persist → finalize``.

    Args:
        classify_fn: Sync callable ``(content, filename) -> ClassifiedDocument``.
            Production impl is ``classify_document`` from
            :mod:`backend.tools.document_classifier.classifier`; unit
            tests pass a fake.
        parse_fn: Sync callable ``(content, filename) -> ParsedDocument``.
            Production impl is ``parse_document`` from
            :mod:`backend.tools.document_parser`; unit tests pass a fake.
        validator: :class:`OrderValidator` instance. Shared across all
            validations in the invocation; its injected
            :class:`MasterDataRepo` caches master data in memory.
        coordinator: :class:`IntakeCoordinator` that owns the routing
            logic (AUTO_APPROVE → orders collection; CLARIFY / ESCALATE
            → exceptions collection) and dedupe on ``source_message_id``.
        clarify_agent: :class:`LlmAgent` (or duck-type) that drafts
            customer-facing clarify emails. Held by
            :class:`ClarifyStage` and driven via ``run_async``.
        summary_agent: :class:`LlmAgent` (or duck-type) that writes the
            one/two-sentence run recap. Held by :class:`FinalizeStage`.
        exception_store: :class:`ExceptionStore` for the
            :class:`ReplyShortCircuitStage`'s pending-clarify lookup.
            Note this is a *separate* reference from whatever store the
            ``coordinator`` holds internally — in practice they share
            the same underlying Firestore client.

    Returns:
        A :class:`SequentialAgent` with ``name=ROOT_AGENT_NAME`` and
        eight sub_agents in canonical order.
    """
    sub_agents = [
        IngestStage(),
        ReplyShortCircuitStage(exception_store=exception_store),
        ClassifyStage(classify_fn=classify_fn),
        ParseStage(parse_fn=parse_fn),
        ValidateStage(validator=validator),
        ClarifyStage(clarify_agent=clarify_agent),
        PersistStage(coordinator=coordinator),
        FinalizeStage(summary_agent=summary_agent),
    ]
    return SequentialAgent(name=ROOT_AGENT_NAME, sub_agents=sub_agents)


def _build_default_root_agent() -> SequentialAgent:
    """Construct the production deps and return the assembled root agent.

    Builds one shared async Firestore client (via
    :func:`get_async_client`) and threads it through the
    :class:`MasterDataRepo`, the two Firestore-backed stores, and the
    :class:`IntakeCoordinator`. Mirrors the single-client pattern used
    by the emulator integration tests so the same transaction semantics
    apply at runtime and in test harnesses.

    Raises:
        Any exception from the underlying Firestore client / ADK agent
        construction. Deliberately unchained — import-time failures
        should surface with their original stack trace so operators
        can see whether the emulator is down, credentials are missing,
        or a prompt template has a syntax error.
    """
    # The async Firestore client is process-scoped: we intentionally do
    # NOT call ``client.aclose()`` on module unload. ``adk web`` / Cloud
    # Run processes own this client for their whole lifetime, so client
    # lifecycle == process lifecycle. Short-lived scripts that DO want
    # explicit cleanup should skip this helper and call
    # :func:`build_root_agent` directly with their own client.
    client = get_async_client()

    master_data_repo = MasterDataRepo(client)
    order_validator = OrderValidator(repo=master_data_repo)

    order_store = FirestoreOrderStore(client)
    exception_store = FirestoreExceptionStore(client)

    intake_coordinator = IntakeCoordinator(
        validator=order_validator,
        order_store=order_store,
        exception_store=exception_store,
        repo=master_data_repo,
        agent_version=AGENT_VERSION,
    )

    clarify_agent: LlmAgent = build_clarify_email_agent()
    summary_agent: LlmAgent = build_summary_agent()

    return build_root_agent(
        classify_fn=classify_document,
        parse_fn=parse_document,
        validator=order_validator,
        coordinator=intake_coordinator,
        clarify_agent=clarify_agent,
        summary_agent=summary_agent,
        exception_store=exception_store,
    )


# Module-level root_agent — the attribute name ``adk web .`` looks up.
# Construction runs at import time by design: adk's discovery convention
# scans this module for a bound ``root_agent`` attribute, and lazy-init
# via ``__getattr__`` would require a second indirection that upstream
# ADK tooling does not speak. Import-time failures propagate naturally.
root_agent: SequentialAgent = _build_default_root_agent()


__all__ = [
    "AGENT_VERSION",
    "ROOT_AGENT_NAME",
    "build_root_agent",
    "root_agent",
]
