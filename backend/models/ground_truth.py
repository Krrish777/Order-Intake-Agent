"""Ground-truth schema for the synthetic data corpus.

Each generated document in `data/` has a sibling `.expected.json` that conforms
to `GroundTruth`. This is NOT what the extraction agent produces (that's
`ExtractedOrder` in `parsed_document.py`); this is what the TEST HARNESS
knows ahead of time about each document — including which edge cases the
document exercises and how a reasonable pipeline should route it.

The distinction matters:
- `ExtractedOrder`: shape of data an agent pulls out of a document.
- `GroundTruth`: shape of the annotation that tells evaluators what the
  correct extraction looks like AND what edge-case routing the pipeline
  should take.

Ground truth is written once (here) and evaluated against many candidate
extractions from many prompt/model variants later.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


GroundTruthFormat = Literal["excel", "csv", "pdf", "edi", "email"]

EdgeCase = Literal[
    "clean",
    "typos_label_variations",
    "ambiguity_missing_fields",
    "quirky_encoding",
    "minimalist_envelope",
    "vague_references",
    "semi_formal_typos",
    "conflict_lead_time",
]

RoutingDecision = Literal[
    "auto_process",         # every field resolved, no ambiguity, no conflict
    "human_review",         # ambiguity requires human judgment before posting
    "conflict_resolution",  # structural conflict (lead-time impossible, etc.)
]


class GroundTruthLineItem(BaseModel):
    """One line of expected extraction output.

    `customer_ref` is what the source document actually wrote (alias, canonical
    SKU, or free-text description fragment). `canonical_sku` is the resolved
    Grafton-Reese SKU from `products.json`, or None if the reference cannot
    be uniquely resolved without human input.
    """

    line_number: int = Field(..., ge=1)
    customer_ref: str
    canonical_sku: Optional[str] = None
    description: str
    quantity: float = Field(..., gt=0)
    unit_of_measure: str
    unit_price: Optional[float] = Field(None, ge=0)
    notes: Optional[str] = None


class GroundTruth(BaseModel):
    """Ground-truth annotation for one document in the corpus."""

    source_doc: str = Field(..., description="Path relative to repo root.")
    customer_id: str = Field(..., description="Must match a row in customers.json.")
    format: GroundTruthFormat
    edge_case: EdgeCase

    po_number: Optional[str] = None
    po_date: Optional[str] = Field(None, description="ISO YYYY-MM-DD.")
    required_date: Optional[str] = Field(
        None, description="ISO YYYY-MM-DD, or null if unparseable/absent."
    )
    ship_to_code: Optional[str] = None
    payment_terms: Optional[str] = None

    line_items: list[GroundTruthLineItem]

    known_ambiguities: list[str] = Field(
        default_factory=list,
        description=(
            "Human-readable notes describing fields the extractor cannot "
            "resolve without context (e.g., 'No PO number; derive from filename')."
        ),
    )
    known_conflicts: list[str] = Field(
        default_factory=list,
        description=(
            "Human-readable notes about conflicts between the order and "
            "known master data (e.g., 'Delivery before lead time')."
        ),
    )
    expected_routing: RoutingDecision
