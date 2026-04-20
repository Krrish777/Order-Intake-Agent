"""Pydantic schema for parser output.

Also fed to LlamaExtract as ``data_schema`` via ``model_json_schema()`` —
field descriptions double as extraction hints, so keep the label-alias
lists accurate.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class OrderLineItem(BaseModel):
    sku: Optional[str] = Field(
        None,
        description=(
            "Product code. Map labels: Item #, Item No, Part Number, PN, "
            "Material, Material No, Product Code, Catalog #, UPC."
        ),
    )
    description: Optional[str] = Field(None, description="Product description / name.")
    quantity: Optional[float] = Field(
        None,
        description=(
            "Numeric count. Map labels: Qty, QTY, Qty Ordered, Order Qty, "
            "Units, Pcs, Pieces, Count, Amount, No. of Units, EA."
        ),
    )
    unit_of_measure: Optional[str] = Field(
        None, description="Unit of measure: EA, CS, PLT, KG, etc."
    )
    unit_price: Optional[float] = Field(
        None,
        description=(
            "Per-unit price. Map labels: Unit Price, Price/Unit, Rate, Cost."
        ),
    )
    requested_date: Optional[str] = Field(
        None,
        description=(
            "Requested delivery / ship date as ISO YYYY-MM-DD. Map labels: "
            "Ship Date, Required Date, Need By, Deliver By, ETA, Due Date, Req Date."
        ),
    )


class ExtractedOrder(BaseModel):
    """One purchase order; a document may carry multiple."""

    customer_name: Optional[str] = Field(None, description="Buyer / customer company name.")
    po_number: Optional[str] = Field(
        None,
        description=(
            "Customer's PO reference. Map labels: PO #, Purchase Order, "
            "Order #, Order Number, Reference, Ref #."
        ),
    )
    line_items: list[OrderLineItem] = Field(default_factory=list)
    ship_to_address: Optional[str] = Field(
        None, description="Full destination address as a single string."
    )
    requested_delivery_date: Optional[str] = Field(
        None,
        description=(
            "Order-level requested date if no per-line dates exist. "
            "ISO YYYY-MM-DD."
        ),
    )
    special_instructions: Optional[str] = Field(
        None,
        description=(
            "Free-text instructions: rush, hold, substitution rules, "
            "delivery windows, etc."
        ),
    )


DocumentClassification = Literal[
    "purchase_order",
    "po_confirmation",
    "shipping_notice",
    "invoice",
    "inquiry",
    "complaint",
    "spam",
    "other",
]


class ParsedDocument(BaseModel):
    """Top-level parser result; populated by a single LlamaExtract job."""

    classification: DocumentClassification = Field(
        ...,
        description=(
            "Classify the document's primary intent. Pick the single best match."
        ),
    )
    classification_rationale: str = Field(
        ...,
        description=(
            "One sentence explaining the classification, citing specific phrases "
            "from the document."
        ),
    )
    sub_documents: list[ExtractedOrder] = Field(
        default_factory=list,
        description=(
            "One entry per distinct order detected in the document. A single PO "
            "produces one entry. A bundle of three POs produces three entries. "
            "Empty list if the document is not a purchase order."
        ),
    )
    page_count: Optional[int] = Field(
        None, description="Number of pages in the source document."
    )
    detected_language: Optional[str] = Field(
        None, description="ISO 639-1 language code, e.g. 'en'."
    )
