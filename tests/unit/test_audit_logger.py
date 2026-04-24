"""Unit tests for AuditLogger fail-open emitter."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.audit.logger import AuditLogger


@pytest.mark.asyncio
class TestAuditLogger:
    async def test_emit_writes_one_doc_to_audit_log(self):
        add_mock = AsyncMock()
        collection_mock = MagicMock()
        collection_mock.add = add_mock
        client = MagicMock()
        client.collection = MagicMock(return_value=collection_mock)

        logger = AuditLogger(client=client, agent_version="track-a-v0.2")
        await logger.emit(
            correlation_id="c1",
            session_id="s1",
            source_message_id="m1",
            stage="ValidateStage",
            phase="entered",
            action="stage_entered",
        )

        client.collection.assert_called_once_with("audit_log")
        add_mock.assert_awaited_once()
        written = add_mock.await_args.args[0]
        assert written["correlation_id"] == "c1"
        assert written["agent_version"] == "track-a-v0.2"
        assert written["schema_version"] == 1

    async def test_emit_swallows_firestore_exceptions(self):
        """Fail-open: pipeline must not crash on audit write failure."""
        add_mock = AsyncMock(side_effect=RuntimeError("firestore outage"))
        collection_mock = MagicMock()
        collection_mock.add = add_mock
        client = MagicMock()
        client.collection = MagicMock(return_value=collection_mock)

        logger = AuditLogger(client=client, agent_version="track-a-v0.2")

        # Must NOT raise
        await logger.emit(
            correlation_id="c1",
            session_id="s1",
            source_message_id="m1",
            stage="ValidateStage",
            phase="entered",
            action="stage_entered",
        )

    async def test_emit_accepts_payload_dict(self):
        add_mock = AsyncMock()
        collection_mock = MagicMock()
        collection_mock.add = add_mock
        client = MagicMock()
        client.collection = MagicMock(return_value=collection_mock)

        logger = AuditLogger(client=client, agent_version="track-a-v0.2")
        await logger.emit(
            correlation_id="c1",
            session_id="s1",
            source_message_id="m1",
            stage="lifecycle",
            phase="lifecycle",
            action="routing_decided",
            outcome="auto_approve",
            payload={"confidence": 0.97, "customer_id": "CUST-1"},
        )

        written = add_mock.await_args.args[0]
        assert written["payload"]["confidence"] == 0.97
        assert written["payload"]["customer_id"] == "CUST-1"
        assert written["outcome"] == "auto_approve"

    async def test_emit_uses_server_timestamp_sentinel(self):
        """ts field must be replaced with SERVER_TIMESTAMP before write."""
        from google.cloud.firestore_v1 import SERVER_TIMESTAMP

        add_mock = AsyncMock()
        collection_mock = MagicMock()
        collection_mock.add = add_mock
        client = MagicMock()
        client.collection = MagicMock(return_value=collection_mock)

        logger = AuditLogger(client=client, agent_version="track-a-v0.2")
        await logger.emit(
            correlation_id="c1",
            session_id="s1",
            source_message_id=None,
            stage="IngestStage",
            phase="entered",
            action="stage_entered",
        )

        written = add_mock.await_args.args[0]
        assert written["ts"] is SERVER_TIMESTAMP

    async def test_missing_payload_defaults_to_empty_dict(self):
        add_mock = AsyncMock()
        collection_mock = MagicMock()
        collection_mock.add = add_mock
        client = MagicMock()
        client.collection = MagicMock(return_value=collection_mock)

        logger = AuditLogger(client=client, agent_version="track-a-v0.2")
        await logger.emit(
            correlation_id="c1",
            session_id="s1",
            source_message_id="m1",
            stage="ValidateStage",
            phase="exited",
            action="stage_exited",
            outcome="ok",
        )

        written = add_mock.await_args.args[0]
        assert written["payload"] == {}
