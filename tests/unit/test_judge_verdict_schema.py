"""Schema round-trip tests for the JudgeVerdict Pydantic models.

Guards the same Gemini-response_schema gotcha as ConfirmationEmail /
ClarifyEmail: no ``extra="forbid"``, because Pydantic emits
``additionalProperties: false`` which Gemini 400s on. The regression
walker in ``test_llm_agent_factories.py`` catches drift for LlmAgent
factories; this test-file covers the models themselves.
"""

from __future__ import annotations

import json

import pytest

from backend.models.judge_verdict import (
    JudgeFinding,
    JudgeFindingKind,
    JudgeVerdict,
)


def test_judge_verdict_pass_round_trips_cleanly():
    verdict = JudgeVerdict(status="pass", reason="", findings=[])

    dumped = verdict.model_dump(mode="json")
    assert dumped == {"status": "pass", "reason": "", "findings": []}

    restored = JudgeVerdict.model_validate(dumped)
    assert restored == verdict


def test_judge_verdict_rejected_with_multiple_findings_round_trips():
    verdict = JudgeVerdict(
        status="rejected",
        reason="two findings detected",
        findings=[
            JudgeFinding(
                kind=JudgeFindingKind.HALLUCINATED_FACT,
                quote="your total is $999.99",
                explanation="Body states $999.99 but order.total is 127.40.",
            ),
            JudgeFinding(
                kind=JudgeFindingKind.UNAUTHORIZED_COMMITMENT,
                quote="free shipping on your next order",
                explanation="record_facts has no shipping-terms field authorizing a discount.",
            ),
        ],
    )

    dumped = verdict.model_dump(mode="json")
    assert dumped["status"] == "rejected"
    assert len(dumped["findings"]) == 2
    assert dumped["findings"][0]["kind"] == "hallucinated_fact"
    assert dumped["findings"][1]["kind"] == "unauthorized_commitment"

    restored = JudgeVerdict.model_validate(dumped)
    assert restored == verdict


@pytest.mark.parametrize(
    "value,kind",
    [
        ("hallucinated_fact",       JudgeFindingKind.HALLUCINATED_FACT),
        ("unauthorized_commitment", JudgeFindingKind.UNAUTHORIZED_COMMITMENT),
        ("tone",                    JudgeFindingKind.TONE),
        ("disallowed_url",          JudgeFindingKind.DISALLOWED_URL),
        ("other",                   JudgeFindingKind.OTHER),
    ],
)
def test_judge_finding_kind_enum_covers_all_five_values(value: str, kind: JudgeFindingKind):
    assert kind.value == value
    # Round-trip through JSON to make sure str-enum serialization is stable.
    finding = JudgeFinding(kind=kind, quote="q", explanation="e")
    assert json.loads(finding.model_dump_json())["kind"] == value


def test_judge_verdict_does_not_emit_additional_properties_false():
    # Regression guard: the ConfirmationEmail/ClarifyEmail models intentionally
    # do NOT set model_config = ConfigDict(extra="forbid") because that
    # emits additionalProperties:false which Gemini's response_schema rejects.
    # This test pins the same discipline on JudgeVerdict.
    schema = JudgeVerdict.model_json_schema()

    def scan(node):
        if isinstance(node, dict):
            if node.get("additionalProperties") is False:
                pytest.fail(
                    f"additionalProperties:false found in JudgeVerdict schema at {node!r}"
                )
            for v in node.values():
                scan(v)
        elif isinstance(node, list):
            for v in node:
                scan(v)

    scan(schema)
