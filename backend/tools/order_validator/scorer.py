"""Aggregate per-line validator output into one confidence number.

The validator orchestrator calls this after every line has a
:class:`LineItemValidation`. The router consumes the returned float and
maps it to a :class:`RoutingDecision`.

Formula:

* Start with the mean of ``line.match_confidence`` across all lines.
  A full-miss line contributes 0.0 and drags the mean down; that is the
  intended signal — a single unmatched line should not auto-approve.
* Subtract ``CHECK_FAILURE_PENALTY`` per failed price or qty check.
* Clamp to ``[0.0, 1.0]``.

The penalty coefficient is tuned so that one failed check on a small
order knocks AUTO (≥0.95) down to CLARIFY (≥0.80), and two failures on
a small order push through to ESCALATE — matching the demo intent.
"""

from __future__ import annotations

from backend.models.validation_result import LineItemValidation

CHECK_FAILURE_PENALTY: float = 0.15
"""Deducted from aggregate for each False price_ok or False qty_ok."""


def aggregate(lines: list[LineItemValidation]) -> float:
    """Return aggregate confidence in ``[0.0, 1.0]``. Empty input
    returns ``0.0`` — an order with no line items is not validate-able."""
    if not lines:
        return 0.0

    base = sum(ln.match_confidence for ln in lines) / len(lines)

    penalties = sum(
        CHECK_FAILURE_PENALTY
        for ln in lines
        if not ln.price_ok or not ln.qty_ok
    )
    # One line can contribute two penalties (price + qty both fail).
    penalties += sum(
        CHECK_FAILURE_PENALTY for ln in lines if not ln.price_ok and not ln.qty_ok
    )

    return max(0.0, min(1.0, base - penalties))


__all__ = ["aggregate", "CHECK_FAILURE_PENALTY"]
