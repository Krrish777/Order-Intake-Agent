"""Pydantic models that carry validator output across the agent pipeline.

These contracts are imported by the validator (``backend.tools.order_validator``)
that produces them, the agent orchestration that routes on them, the
persistence layer that stores them with the order/exception, and the
clarify-email generator that renders them into prose.

Threshold constants live next to :class:`RoutingDecision` so the router
and tests share one source of truth — never hardcode 0.80 or 0.95
elsewhere.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from backend.models.master_records import CustomerRecord

AUTO_THRESHOLD: float = 0.95
"""Aggregate confidence at or above this auto-approves the order."""

CLARIFY_THRESHOLD: float = 0.80
"""Aggregate confidence at or above this (and below ``AUTO_THRESHOLD``)
routes to clarify; strictly below escalates to a human."""


MatchTier = Literal["exact", "fuzzy", "embedding", "none"]
"""Which tier of the SKU matcher ladder produced the match.

* ``exact``   — direct sku doc lookup, optionally via ``customer.sku_aliases``
* ``fuzzy``   — rapidfuzz ``token_set_ratio`` over short+long descriptions
* ``embedding`` — vector search (Layer 2; stub returns no matches today)
* ``none``    — full miss, no match at any tier
"""


class RoutingDecision(StrEnum):
    """Where the validator's output sends the order next.

    The value strings double as the ``status`` field on the persisted
    order/exception document, so they're stable identifiers — do not
    change without a Firestore migration.
    """

    AUTO_APPROVE = "auto_approve"
    CLARIFY = "clarify"
    ESCALATE = "escalate"


class LineItemValidation(BaseModel):
    """Per-line validator output. The order-level :class:`ValidationResult`
    holds one of these per line in ``line_items``.

    ``notes`` collects human-readable reasons for any ``False`` flag —
    the clarify email generator concatenates them per line, so phrase
    them as facts (``"unit_price 5.49 vs catalog 4.10 (+33.9%)"``), not
    instructions.
    """

    model_config = ConfigDict(extra="forbid")

    line_index: int = Field(..., ge=0, description="Position in the source order, 0-based.")
    matched_sku: Optional[str] = Field(
        None, description="Canonical sku from the catalog. None on full miss."
    )
    match_tier: MatchTier = Field("none", description="Which matcher tier produced the result.")
    match_confidence: float = Field(
        0.0, ge=0.0, le=1.0, description="0.0–1.0; 1.0 only for exact-id hits."
    )
    price_ok: bool = Field(
        True,
        description="True if quoted price is within tolerance of catalog "
        "price, OR if no quote was supplied (price_check is permissive on "
        "missing quotes).",
    )
    qty_ok: bool = Field(
        True,
        description="True if quantity passes positive-int and min_order "
        "checks against the matched product.",
    )
    notes: list[str] = Field(
        default_factory=list,
        description="Per-line reasons; empty when everything is clean.",
    )


class ValidationResult(BaseModel):
    """Order-level validator output. One per :class:`ExtractedOrder` from
    the parser; a multi-order :class:`ParsedDocument` produces a list."""

    model_config = ConfigDict(extra="forbid")

    customer: Optional[CustomerRecord] = Field(
        None,
        description="Resolved customer record, or None if the customer "
        "could not be matched above threshold.",
    )
    lines: list[LineItemValidation] = Field(default_factory=list)
    aggregate_confidence: float = Field(
        0.0,
        ge=0.0,
        le=1.0,
        description="Scorer output across all lines; the router compares "
        "this against ``AUTO_THRESHOLD`` and ``CLARIFY_THRESHOLD``.",
    )
    decision: RoutingDecision = Field(
        RoutingDecision.ESCALATE,
        description="Where the order goes next. Defaults to ESCALATE so a "
        "validator that errors mid-flight fails safe rather than auto-approving.",
    )
    rationale: str = Field(
        "",
        description="One-sentence explanation suitable for the dashboard "
        "exception card or the clarify email opener.",
    )


__all__ = [
    "AUTO_THRESHOLD",
    "CLARIFY_THRESHOLD",
    "MatchTier",
    "RoutingDecision",
    "LineItemValidation",
    "ValidationResult",
]
