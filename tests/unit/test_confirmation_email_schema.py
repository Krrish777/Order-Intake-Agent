"""Smoke tests for the ConfirmationEmail output_schema.

Verifies the two-field contract and — critically — that the schema does
NOT emit ``additionalProperties: false`` in its JSON schema. Gemini's
``generation_config.response_schema`` rejects that field with a 400.
The Track A live-run audit (research/Order-Intake-Sprint-Status.md
line 7, F3) caught this on ``ClarifyEmail`` and the regression walker
in test_llm_agent_factories.py now guards all factories — this test
is a faster unit-level check for the same property on this schema
alone.
"""

from __future__ import annotations

from backend.models.confirmation_email import ConfirmationEmail


def test_fields_and_types() -> None:
    inst = ConfirmationEmail(subject="Re: order confirmed", body="Hi there, got it.")
    assert inst.subject == "Re: order confirmed"
    assert inst.body == "Hi there, got it."


def test_schema_has_no_additional_properties_false() -> None:
    """Gemini 400 regression: extra='forbid' would emit this and break."""
    schema = ConfirmationEmail.model_json_schema()
    # Pydantic default (no extra='forbid') should NOT emit this key.
    assert schema.get("additionalProperties") is not False
