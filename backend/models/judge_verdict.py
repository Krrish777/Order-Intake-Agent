"""Pydantic models for the Generator-Judge outbound-email quality gate.

The Judge ``LlmAgent`` returns a :class:`JudgeVerdict`; ``JudgeStage``
persists it onto the underlying :class:`~backend.models.order_record.OrderRecord`
or :class:`~backend.models.exception_record.ExceptionRecord` and stashes
it in ``ctx.session.state['judge_verdicts']`` for ``SendStage`` to read.

Intentionally **no** ``model_config = ConfigDict(extra="forbid")`` — that
emits ``additionalProperties: false`` which Gemini's ``response_schema``
rejects (see Track A live-run audit finding F3; regression walker at
``tests/unit/test_llm_agent_factories.py``).
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class JudgeFindingKind(str, Enum):
    """Categorical tag for a judge finding — the reason the body fails."""

    HALLUCINATED_FACT = "hallucinated_fact"
    UNAUTHORIZED_COMMITMENT = "unauthorized_commitment"
    TONE = "tone"
    DISALLOWED_URL = "disallowed_url"
    OTHER = "other"


class JudgeFinding(BaseModel):
    """One concrete issue the judge flagged in the outbound body."""

    kind: JudgeFindingKind
    quote: str = Field(
        description="Verbatim snippet from the body that triggered the finding."
    )
    explanation: str = Field(
        description="Why this snippet is a problem — one sentence."
    )


class JudgeVerdict(BaseModel):
    """The judge's verdict on one drafted outbound email.

    ``status='pass'`` means the body is safe to send; ``reason`` is empty
    and ``findings`` is an empty list.

    ``status='rejected'`` means the send must be blocked; ``reason`` is
    a one-liner used directly in ``send_error='judge_rejected:<reason>'``
    and ``findings`` lists the specific issues in body-appearance order.
    """

    status: Literal["pass", "rejected"]
    reason: str = Field(
        default="",
        description="Empty on pass; one-liner on rejected.",
    )
    findings: list[JudgeFinding] = Field(
        default_factory=list,
        description="Empty on pass; structured issue list on rejected.",
    )


__all__ = ["JudgeFindingKind", "JudgeFinding", "JudgeVerdict"]
