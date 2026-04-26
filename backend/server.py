"""FastAPI app for Cloud Run — receives Gmail Pub/Sub push notifications.

Build singletons at module-load so warm Cloud Run instances reuse them.
The Pub/Sub push subscription targets POST /pubsub/push with OIDC auth;
Cloud Run validates the OIDC token before the request reaches us when
the service is deployed with --no-allow-unauthenticated.

Local run:
    uv run uvicorn backend.server:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import asyncio
import base64
import os
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService

from backend.gmail.client import GmailClient
from backend.gmail.pubsub_worker import GmailPubSubWorker
from backend.gmail.scopes import A2_SCOPES
from backend.gmail.watch import GmailWatch
from backend.my_agent.agent import _build_default_root_agent
from backend.persistence.sync_state_store import GmailSyncStateStore
from backend.tools.order_validator.tools.firestore_client import get_async_client
from backend.utils.logging import get_logger

_log = get_logger(__name__)

load_dotenv()

REQUIRED_ENV = (
    "GMAIL_CLIENT_ID",
    "GMAIL_CLIENT_SECRET",
    "GMAIL_REFRESH_TOKEN",
    "GMAIL_PUBSUB_PROJECT_ID",
    "GMAIL_PUBSUB_TOPIC",
)

_missing = [v for v in REQUIRED_ENV if not os.environ.get(v)]
if _missing:
    raise RuntimeError(f"missing required env vars: {', '.join(_missing)}")


def _build_worker() -> GmailPubSubWorker:
    label_name = os.environ.get("GMAIL_PROCESSED_LABEL", "orderintake-processed")
    project_id = os.environ["GMAIL_PUBSUB_PROJECT_ID"]
    topic_name = f"projects/{project_id}/topics/{os.environ['GMAIL_PUBSUB_TOPIC']}"

    gmail_client = GmailClient(
        refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
        client_id=os.environ["GMAIL_CLIENT_ID"],
        client_secret=os.environ["GMAIL_CLIENT_SECRET"],
        scopes=A2_SCOPES,
        query_override=os.environ.get("GMAIL_QUERY") or None,
    )
    sync_state_store = GmailSyncStateStore(get_async_client())
    root_agent = _build_default_root_agent()
    session_service = InMemorySessionService()
    runner = Runner(
        app_name="order_intake", agent=root_agent, session_service=session_service,
    )

    # Push deployment: subscriber/subscription_path are unused (pull loop is dead
    # code in this process). Pass placeholders that won't be touched.
    return GmailPubSubWorker(
        subscriber=None,
        subscription_path="",
        gmail_client=gmail_client,
        runner=runner,
        session_service=session_service,
        sync_state_store=sync_state_store,
        watch=GmailWatch(gmail_client),
        topic_name=topic_name,
        watch_label_ids=None,
        label_name=label_name,
    )


app = FastAPI(title="order-intake-pubsub")
_worker: GmailPubSubWorker = _build_worker()
_init_lock = asyncio.Lock()
_initialized = False


async def _ensure_init() -> None:
    global _initialized
    if _initialized:
        return
    async with _init_lock:
        if _initialized:
            return
        await _worker.init(start_watch=False)
        _initialized = True


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/pubsub/push")
async def pubsub_push(request: Request) -> JSONResponse:
    envelope: Any = await request.json()
    if not isinstance(envelope, dict) or "message" not in envelope:
        raise HTTPException(status_code=400, detail="invalid Pub/Sub envelope")

    message = envelope["message"]
    encoded = message.get("data")
    if not encoded:
        # Empty Gmail history notifications happen — ack quickly.
        return JSONResponse(status_code=204, content=None)

    try:
        data = base64.b64decode(encoded)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"bad base64: {exc}") from exc

    await _ensure_init()
    try:
        await _worker.process_message(data)
    except Exception as exc:
        # Returning 5xx tells Pub/Sub to retry per the subscription's policy.
        _log.error("push_processing_failed", error=str(exc), message_id=message.get("messageId"))
        raise HTTPException(status_code=500, detail="processing failed") from exc

    return JSONResponse(status_code=204, content=None)
