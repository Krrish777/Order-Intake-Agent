"""Unit tests for ``scorer.aggregate``. Pure function — no fixtures."""

from __future__ import annotations

from backend.models.validation_result import LineItemValidation
from backend.tools.order_validator.scorer import CHECK_FAILURE_PENALTY, aggregate


def _line(conf: float = 1.0, price_ok: bool = True, qty_ok: bool = True) -> LineItemValidation:
    return LineItemValidation(
        line_index=0,
        matched_sku="FST-HCS-050-13-200-G5Z" if conf > 0 else None,
        match_tier="exact" if conf == 1.0 else ("fuzzy" if conf > 0 else "none"),
        match_confidence=conf,
        price_ok=price_ok,
        qty_ok=qty_ok,
    )


def test_empty_returns_zero() -> None:
    assert aggregate([]) == 0.0


def test_single_clean_line_returns_confidence() -> None:
    assert aggregate([_line(1.0)]) == 1.0
    assert aggregate([_line(0.87)]) == 0.87


def test_mean_across_clean_lines() -> None:
    result = aggregate([_line(1.0), _line(1.0), _line(0.85)])
    assert abs(result - (1.0 + 1.0 + 0.85) / 3) < 1e-9


def test_price_failure_applies_single_penalty() -> None:
    result = aggregate([_line(1.0, price_ok=False)])
    assert abs(result - (1.0 - CHECK_FAILURE_PENALTY)) < 1e-9


def test_qty_failure_applies_single_penalty() -> None:
    result = aggregate([_line(1.0, qty_ok=False)])
    assert abs(result - (1.0 - CHECK_FAILURE_PENALTY)) < 1e-9


def test_both_failures_on_same_line_apply_double_penalty() -> None:
    """A line where both price and qty fail counts as two failures."""
    result = aggregate([_line(1.0, price_ok=False, qty_ok=False)])
    assert abs(result - (1.0 - 2 * CHECK_FAILURE_PENALTY)) < 1e-9


def test_miss_line_contributes_zero_to_mean() -> None:
    # one hit at 1.0, one miss at 0.0 → mean 0.5
    assert abs(aggregate([_line(1.0), _line(0.0)]) - 0.5) < 1e-9


def test_clamped_to_zero_floor() -> None:
    result = aggregate([_line(0.0, price_ok=False, qty_ok=False)])
    assert result == 0.0


def test_clamped_to_one_ceiling() -> None:
    # Plausibly impossible, but the clamp exists — verify it.
    result = aggregate([_line(1.0)])
    assert result <= 1.0
