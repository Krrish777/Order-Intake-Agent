"""Unit tests for ``router.decide`` — threshold edge cases."""

from __future__ import annotations

from backend.models.validation_result import (
    AUTO_THRESHOLD,
    CLARIFY_THRESHOLD,
    RoutingDecision,
)
from backend.tools.order_validator.router import decide


def test_auto_approve_at_threshold_exact() -> None:
    assert decide(AUTO_THRESHOLD) is RoutingDecision.AUTO_APPROVE


def test_auto_approve_above_threshold() -> None:
    assert decide(1.0) is RoutingDecision.AUTO_APPROVE
    assert decide(0.99) is RoutingDecision.AUTO_APPROVE


def test_just_below_auto_is_clarify() -> None:
    assert decide(AUTO_THRESHOLD - 0.0001) is RoutingDecision.CLARIFY


def test_clarify_at_threshold_exact() -> None:
    assert decide(CLARIFY_THRESHOLD) is RoutingDecision.CLARIFY


def test_clarify_band_mid() -> None:
    assert decide(0.85) is RoutingDecision.CLARIFY


def test_just_below_clarify_is_escalate() -> None:
    assert decide(CLARIFY_THRESHOLD - 0.0001) is RoutingDecision.ESCALATE


def test_zero_is_escalate() -> None:
    assert decide(0.0) is RoutingDecision.ESCALATE


def test_negative_is_escalate_too() -> None:
    # Scorer clamps to [0, 1] but router should still behave sanely.
    assert decide(-0.5) is RoutingDecision.ESCALATE
