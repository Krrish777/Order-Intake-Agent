"""Emulator-backed integration tests for Track D audit log.

Requires FIRESTORE_EMULATOR_HOST + LLAMA_CLOUD_API_KEY + Firestore
emulator running + master data seeded.

Run with::

    FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 \\
    uv run pytest tests/integration/test_audit_log_emulator.py -v
"""
from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import pytest
from google.adk.runners import Runner
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.cloud.firestore_v1.async_client import AsyncClient
from google.genai import types

from backend.audit.logger import AuditLogger
from backend.my_agent.agent import AGENT_VERSION, build_root_agent
from backend.my_agent.stages.finalize import FINALIZE_STAGE_NAME
from backend.my_agent.stages.ingest import INGEST_STAGE_NAME
from backend.persistence.coordinator import IntakeCoordinator
from backend.persistence.exceptions_store import FirestoreExceptionStore
from backend.persistence.orders_store import FirestoreOrderStore
from backend.tools.document_classifier.classifier import classify_document
from backend.tools.document_parser import parse_document
from backend.tools.order_validator import MasterDataRepo, OrderValidator, get_async_client
from tests.unit._stage_testing import FakeChildLlmAgent

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("FIRESTORE_EMULATOR_HOST"),
        reason="FIRESTORE_EMULATOR_HOST not set; emulator tests skipped",
    ),
    pytest.mark.skipif(
        not os.environ.get("LLAMA_CLOUD_API_KEY"),
        reason="LLAMA_CLOUD_API_KEY not set; pipeline needs classify + parse",
    ),
]

_APP_NAME = "track-d-audit-test"
_USER_ID = "test-user"
_PATTERSON_EML = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "pdf"
    / "patterson_po-28491.wrapper.eml"
)


async def _drive_pipeline(
    client: AsyncClient, eml_content: str, session_suffix: str
) -> tuple[dict, list]:
    """Run the full 9-stage pipeline once. Returns (final_state, events)."""
    repo = MasterDataRepo(client)
    validator = OrderValidator(repo=repo)
    order_store = FirestoreOrderStore(client)
    exception_store = FirestoreExceptionStore(client)
    coordinator = IntakeCoordinator(
        validator=validator,
        order_store=order_store,
        exception_store=exception_store,
        repo=repo,
        agent_version=f"{AGENT_VERSION}-audit-test",
    )
    audit_logger = AuditLogger(client=client, agent_version=AGENT_VERSION)
    clarify_agent = FakeChildLlmAgent(
        output_key="clarify_email",
        responses=[{"subject": "stub", "body": "stub"}],
    )
    summary_agent = FakeChildLlmAgent(
        output_key="run_summary",
        responses=[
            {
                "orders_created": 0,
                "exceptions_opened": 1,
                "docs_skipped": 0,
                "summary": "stub",
            }
        ],
    )
    confirm_agent = FakeChildLlmAgent(
        output_key="confirmation_email",
        responses=[{"subject": "stub", "body": "stub"}],
    )

    try:
        root_agent = build_root_agent(
            classify_fn=classify_document,
            parse_fn=parse_document,
            validator=validator,
            coordinator=coordinator,
            clarify_agent=clarify_agent,
            summary_agent=summary_agent,
            confirm_agent=confirm_agent,
            exception_store=exception_store,
            order_store=order_store,
            audit_logger=audit_logger,
        )
        session_service = InMemorySessionService()
        session_id = f"audit-test-{session_suffix}-{uuid.uuid4().hex}"
        await session_service.create_session(
            app_name=_APP_NAME, user_id=_USER_ID, session_id=session_id
        )
        runner = Runner(
            app_name=_APP_NAME,
            agent=root_agent,
            session_service=session_service,
        )
        new_message = types.Content(
            role="user",
            parts=[types.Part.from_text(text=eml_content)],
        )
        events = []
        async for event in runner.run_async(
            user_id=_USER_ID, session_id=session_id, new_message=new_message
        ):
            events.append(event)
        session = await session_service.get_session(
            app_name=_APP_NAME, user_id=_USER_ID, session_id=session_id
        )
        return session.state, events
    finally:
        await repo.aclose()


def _replace_message_id(eml_text: str, new_id: str) -> str:
    """Swap the Message-Id header to a unique value for retry tests."""
    return re.sub(
        r"(?im)^(message-id:)\s*<[^>]*>",
        f"\\1 <{new_id}>",
        eml_text,
        count=1,
    )


async def _clear_audit_log(client: AsyncClient) -> None:
    async for doc in client.collection("audit_log").stream():
        await doc.reference.delete()


async def _clear_orders_and_exceptions(client: AsyncClient) -> None:
    for collection in ("orders", "exceptions"):
        async for doc in client.collection(collection).stream():
            await doc.reference.delete()


@pytest.fixture
async def clean_emulator():
    """Clean audit_log + orders + exceptions before and after each test."""
    client = get_async_client()
    await _clear_audit_log(client)
    await _clear_orders_and_exceptions(client)
    yield client
    await _clear_audit_log(client)
    await _clear_orders_and_exceptions(client)


class TestAuditLogEmulator:
    async def test_happy_path_produces_multi_event_audit_trail(
        self, clean_emulator: AsyncClient
    ) -> None:
        """Drive the Patterson fixture through all 9 stages and assert the
        audit_log collection has >= 15 docs sharing one correlation_id.

        Patterson goes ESCALATE (catalog prices drifted) — that still
        exercises all 9 stages (IngestStage → FinalizeStage) and emits
        the full suite of entered/exited events.
        """
        client = clean_emulator
        if not _PATTERSON_EML.exists():
            pytest.skip(f"fixture missing: {_PATTERSON_EML}")

        eml_content = _PATTERSON_EML.read_text(encoding="utf-8")
        _state, events = await _drive_pipeline(client, eml_content, "happy")

        assert events, "Runner yielded zero events — pipeline did not run"

        # Fetch all audit_log docs written during this run.
        docs = [
            doc.to_dict()
            async for doc in client.collection("audit_log").stream()
        ]
        assert len(docs) >= 15, (
            f"expected >=15 audit docs, got {len(docs)}; "
            f"stages/phases present: "
            f"{[d['stage'] + ':' + d['phase'] for d in sorted(docs, key=lambda d: str(d.get('ts', '')))]}"
        )

        # All non-empty correlation_ids must be identical — one run = one UUID.
        correlation_ids = {d["correlation_id"] for d in docs if d["correlation_id"]}
        assert len(correlation_ids) == 1, (
            f"expected 1 unique correlation_id, got {correlation_ids}"
        )

        # Required header fields must be populated on every doc.
        for d in docs:
            assert d.get("session_id"), f"missing session_id on doc: {d}"
            assert d.get("stage"), f"missing stage on doc: {d}"
            assert d.get("phase") in {"entered", "exited", "lifecycle"}, (
                f"invalid phase {d.get('phase')!r} on doc: {d}"
            )
            assert d.get("action"), f"missing action on doc: {d}"
            assert d.get("agent_version") == AGENT_VERSION, (
                f"expected agent_version={AGENT_VERSION!r}, "
                f"got {d.get('agent_version')!r}"
            )

        # Sort by timestamp; first doc must be ingest_stage entered.
        docs_sorted = sorted(docs, key=lambda d: d["ts"])
        assert docs_sorted[0]["stage"] == INGEST_STAGE_NAME, (
            f"expected first doc stage={INGEST_STAGE_NAME!r}, "
            f"got {docs_sorted[0]['stage']!r}"
        )
        assert docs_sorted[0]["phase"] == "entered", (
            f"expected first doc phase=entered, got {docs_sorted[0]['phase']!r}"
        )

        # The tail of the sorted docs must contain finalize_stage:exited
        # with outcome=ok. FinalizeStage also emits a lifecycle:lifecycle
        # event inside its body, so the very-last doc by SERVER_TIMESTAMP
        # may be either event (both race within milliseconds). We assert
        # on presence rather than strict position.
        finalize_exited_docs = [
            d for d in docs
            if d["stage"] == FINALIZE_STAGE_NAME and d["phase"] == "exited"
        ]
        assert len(finalize_exited_docs) == 1, (
            f"expected exactly 1 finalize_stage:exited doc, "
            f"got {finalize_exited_docs}"
        )
        assert finalize_exited_docs[0].get("outcome") == "ok", (
            f"expected finalize_stage:exited outcome=ok, "
            f"got {finalize_exited_docs[0].get('outcome')!r}"
        )

        # FinalizeStage emits a lifecycle:lifecycle event with
        # action="run_finalized" and outcome="ok". PersistStage also
        # emits lifecycle:lifecycle docs (one per processed doc) with
        # outcome=result.kind (e.g. "escalate"). We look for the specific
        # run_finalized doc.
        run_finalized_docs = [
            d for d in docs
            if d.get("stage") == "lifecycle"
            and d.get("phase") == "lifecycle"
            and d.get("action") == "run_finalized"
        ]
        assert len(run_finalized_docs) >= 1, (
            "expected at least 1 lifecycle:lifecycle:run_finalized doc; "
            f"lifecycle docs present: "
            f"{[(d.get('action'), d.get('outcome')) for d in docs if d.get('stage') == 'lifecycle']}"
        )
        assert run_finalized_docs[-1].get("outcome") == "ok", (
            f"expected run_finalized outcome=ok, "
            f"got {run_finalized_docs[-1].get('outcome')!r}"
        )

    @pytest.mark.skip(
        reason=(
            "emulator default admin mode bypasses rules; "
            "Phase 2 hardening will auth the client"
        )
    )
    async def test_audit_log_is_immutable(
        self, clean_emulator: AsyncClient
    ) -> None:
        """Assert that audit_log docs cannot be mutated after write.

        Skipped: the Firestore emulator running in admin mode (no auth
        config) bypasses security rules, so a ``ref.update()`` call will
        succeed instead of raising PermissionDenied. This test will be
        un-skipped in Track D Phase 2 when the test client is wired up
        with AnonymousCredentials so the emulator enforces rules.
        """
        client = clean_emulator
        if not _PATTERSON_EML.exists():
            pytest.skip(f"fixture missing: {_PATTERSON_EML}")

        eml_content = _PATTERSON_EML.read_text(encoding="utf-8")
        await _drive_pipeline(client, eml_content, "immutable")

        async for doc in client.collection("audit_log").limit(1).stream():
            ref = doc.reference
            from google.api_core.exceptions import PermissionDenied
            with pytest.raises((PermissionDenied, Exception)):
                await ref.update({"stage": "tamper"})
            break

    async def test_retries_produce_distinct_correlation_ids(
        self, clean_emulator: AsyncClient
    ) -> None:
        """Two pipeline runs with different Message-Ids must emit two
        distinct correlation_ids into the audit_log collection.
        """
        client = clean_emulator
        if not _PATTERSON_EML.exists():
            pytest.skip(f"fixture missing: {_PATTERSON_EML}")

        eml_text = _PATTERSON_EML.read_text(encoding="utf-8")

        # First run — original Message-Id.
        await _drive_pipeline(client, eml_text, "run-1")

        # Second run — swapped Message-Id so IngestStage mints a fresh
        # correlation_id instead of seeing a duplicate envelope.
        eml_v2 = _replace_message_id(
            eml_text, f"retry-{uuid.uuid4().hex}@example.com"
        )
        await _drive_pipeline(client, eml_v2, "run-2")

        docs = [
            doc.to_dict()
            async for doc in client.collection("audit_log").stream()
        ]
        assert docs, "no audit docs found after two pipeline runs"

        correlation_ids = {d["correlation_id"] for d in docs if d["correlation_id"]}
        assert len(correlation_ids) >= 2, (
            f"expected >=2 distinct correlation_ids across two runs, "
            f"got {correlation_ids}"
        )
