"""Map an aggregate confidence number to a :class:`RoutingDecision`.

Pure function — thresholds are the single source of truth in
:mod:`backend.models.validation_result`. Kept separate from the scorer
so the threshold policy can evolve (e.g. per-customer auto-approve
limits) without touching the aggregation math.
"""

from __future__ import annotations

from backend.models.validation_result import (
    AUTO_THRESHOLD,
    CLARIFY_THRESHOLD,
    RoutingDecision,
)


def decide(aggregate_confidence: float) -> RoutingDecision:
    """≥0.95 → AUTO_APPROVE, ≥0.80 → CLARIFY, otherwise ESCALATE."""
    if aggregate_confidence >= AUTO_THRESHOLD:
        return RoutingDecision.AUTO_APPROVE
    if aggregate_confidence >= CLARIFY_THRESHOLD:
        return RoutingDecision.CLARIFY
    return RoutingDecision.ESCALATE


__all__ = ["decide"]
