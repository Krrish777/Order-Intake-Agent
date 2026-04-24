"""Fail-open audit-log emitter.

Spec: docs/superpowers/specs/2026-04-24-track-d-audit-log-design.md

Construct once per process (shared async Firestore client + the
pipeline's AGENT_VERSION constant) and inject into every stage via
PrivateAttr kwarg. Fail-open: Firestore exceptions are logged at
ERROR and swallowed — pipeline keeps running. Phase-2 compliance
hardening flips this to fail-closed by replacing the class.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from google.cloud.firestore_v1 import SERVER_TIMESTAMP
from google.cloud.firestore_v1.async_client import AsyncClient

from backend.audit.models import AuditEvent
from backend.utils.logging import get_logger

_log = get_logger(__name__)


class AuditLogger:
    def __init__(self, client: AsyncClient, agent_version: str) -> None:
        self._client = client
        self._agent_version = agent_version

    async def emit(
        self,
        *,
        correlation_id: str,
        session_id: str,
        source_message_id: Optional[str],
        stage: str,
        phase: Literal["entered", "exited", "lifecycle"],
        action: str,
        outcome: Optional[str] = None,
        payload: Optional[dict[str, Any]] = None,
    ) -> None:
        try:
            event = AuditEvent(
                correlation_id=correlation_id,
                source_message_id=source_message_id,
                session_id=session_id,
                stage=stage,
                phase=phase,
                action=action,
                outcome=outcome,
                ts=datetime.now(timezone.utc),  # placeholder — swapped below
                agent_version=self._agent_version,
                payload=payload or {},
            )
            data = event.model_dump(mode="json")
            data["ts"] = SERVER_TIMESTAMP  # Firestore server-side timestamp
            await self._client.collection("audit_log").add(data)
        except Exception as exc:
            _log.error(
                "audit_emit_failed",
                correlation_id=correlation_id,
                stage=stage,
                action=action,
                error=str(exc),
            )


__all__ = ["AuditLogger"]
