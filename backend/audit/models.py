"""Audit-log event contract.

Strict typed header (so consumers — dashboard, eval, forensic
tooling — can rely on required fields) plus a free-form payload
dict for per-stage detail that would otherwise force a schema
bump every time we add an event type.

Spec: docs/superpowers/specs/2026-04-24-track-d-audit-log-design.md
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class AuditEvent(BaseModel):
    """One immutable row in the ``audit_log`` Firestore collection."""

    model_config = ConfigDict(extra="forbid")

    correlation_id: str
    source_message_id: Optional[str] = None
    session_id: str
    stage: str
    phase: Literal["entered", "exited", "lifecycle"]
    action: str
    outcome: Optional[str] = None
    ts: datetime
    agent_version: str
    payload: dict[str, Any] = Field(default_factory=dict)
    schema_version: int = 1


__all__ = ["AuditEvent"]
