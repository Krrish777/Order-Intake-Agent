"""Unit tests for the AuditEvent Pydantic model.

Spec: docs/superpowers/specs/2026-04-24-track-d-audit-log-design.md
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from backend.audit.models import AuditEvent


NOW = datetime(2026, 4, 24, 12, 0, 0, tzinfo=timezone.utc)


def _valid_kwargs(**overrides):
    base = dict(
        correlation_id="abc123",
        source_message_id="msg-1",
        session_id="sess-1",
        stage="ValidateStage",
        phase="entered",
        action="stage_entered",
        outcome=None,
        ts=NOW,
        agent_version="track-a-v0.2",
        payload={},
    )
    base.update(overrides)
    return base


class TestAuditEventSchema:
    def test_all_required_fields_populated_round_trips(self):
        event = AuditEvent(**_valid_kwargs())
        assert event.correlation_id == "abc123"
        assert event.schema_version == 1

    def test_payload_accepts_arbitrary_dict(self):
        event = AuditEvent(
            **_valid_kwargs(payload={"confidence": 0.87, "order_id": "ORD-xyz"})
        )
        assert event.payload["confidence"] == 0.87
        assert event.payload["order_id"] == "ORD-xyz"

    def test_extra_top_level_field_rejected(self):
        with pytest.raises(ValidationError) as exc:
            AuditEvent(**_valid_kwargs(), mystery_field="nope")
        assert "mystery_field" in str(exc.value)

    def test_missing_correlation_id_rejected(self):
        kwargs = _valid_kwargs()
        del kwargs["correlation_id"]
        with pytest.raises(ValidationError) as exc:
            AuditEvent(**kwargs)
        assert "correlation_id" in str(exc.value)
