"""Unit tests for backend.server (Cloud Run push handler)."""
from __future__ import annotations

import base64
import importlib
import json
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def server_module(monkeypatch):
    """Reload backend.server with all heavy dependencies stubbed out."""
    monkeypatch.setenv("GMAIL_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "test-refresh-token")
    monkeypatch.setenv("GMAIL_PUBSUB_PROJECT_ID", "test-project")
    monkeypatch.setenv("GMAIL_PUBSUB_TOPIC", "test-topic")

    # Stub the worker's collaborators so module import does not try to talk
    # to Firestore / Gmail / build the real agent graph.
    monkeypatch.setattr(
        "backend.gmail.client.GmailClient.__init__",
        lambda self, **kw: None,
    )
    monkeypatch.setattr(
        "backend.persistence.sync_state_store.GmailSyncStateStore.__init__",
        lambda self, *a, **kw: None,
    )
    monkeypatch.setattr(
        "backend.tools.order_validator.tools.firestore_client.get_async_client",
        lambda: MagicMock(),
    )
    monkeypatch.setattr(
        "backend.my_agent.agent._build_default_root_agent",
        lambda: MagicMock(),
    )
    monkeypatch.setattr(
        "google.adk.runners.Runner.__init__",
        lambda self, **kw: None,
    )

    # Force a fresh import so module-level _build_worker() runs under stubs.
    sys.modules.pop("backend.server", None)
    module = importlib.import_module("backend.server")

    # Replace the worker with a controllable mock.
    module._worker = MagicMock()
    module._worker.init = AsyncMock()
    module._worker.process_message = AsyncMock()
    module._initialized = False
    return module


def test_healthz_returns_ok(server_module):
    from fastapi.testclient import TestClient

    client = TestClient(server_module.app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_push_decodes_base64_and_invokes_worker(server_module):
    from fastapi.testclient import TestClient

    payload = json.dumps({"emailAddress": "me@example.com", "historyId": "9999"}).encode()
    encoded = base64.b64encode(payload).decode()
    envelope = {
        "message": {"data": encoded, "messageId": "msg-1"},
        "subscription": "projects/p/subscriptions/s",
    }

    client = TestClient(server_module.app)
    resp = client.post("/pubsub/push", json=envelope)

    assert resp.status_code == 204
    server_module._worker.init.assert_awaited_once_with(start_watch=False)
    server_module._worker.process_message.assert_awaited_once_with(payload)


def test_push_with_empty_data_acks_without_processing(server_module):
    from fastapi.testclient import TestClient

    client = TestClient(server_module.app)
    resp = client.post(
        "/pubsub/push",
        json={"message": {"messageId": "m1"}, "subscription": "x"},
    )
    assert resp.status_code == 204
    server_module._worker.process_message.assert_not_called()


def test_push_with_invalid_envelope_returns_400(server_module):
    from fastapi.testclient import TestClient

    client = TestClient(server_module.app)
    resp = client.post("/pubsub/push", json={"not": "a pubsub envelope"})
    assert resp.status_code == 400


def test_push_returns_500_when_worker_raises(server_module):
    from fastapi.testclient import TestClient

    server_module._worker.process_message.side_effect = RuntimeError("boom")

    payload = json.dumps({"historyId": "1"}).encode()
    encoded = base64.b64encode(payload).decode()

    client = TestClient(server_module.app)
    resp = client.post(
        "/pubsub/push",
        json={"message": {"data": encoded, "messageId": "m1"}},
    )
    assert resp.status_code == 500
