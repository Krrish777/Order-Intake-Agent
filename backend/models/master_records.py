"""Pydantic records mirroring Firestore master-data documents.

These models deserialize snapshots from the ``products``, ``customers``, and
``meta`` collections that ``scripts/load_master_data.py`` writes from
``data/masters/*.json``. They form the typed surface that
:class:`backend.data.firestore_repo.FirestoreRepo` returns.

The product catalog is heterogeneous (fasteners, hoses, valves, regulators,
filters) and each subcategory carries its own attribute set (``thread``,
``bore_in``, ``working_pressure_psi``, ...). ``ProductRecord`` therefore
pins only the fields the validation pipeline uses and allows extras — the
domain-specific attributes pass through untouched and stay accessible via
attribute access.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class AddressRecord(BaseModel):
    """Postal address shape used by both ``bill_to`` and ``ship_to`` entries."""

    model_config = ConfigDict(extra="allow")

    street1: str
    street2: Optional[str] = None
    city: str
    state: str
    zip: str
    country: str


class ShipToLocation(AddressRecord):
    """One ship-to location on a customer. Extends ``AddressRecord`` with the
    distribution-center identifier and receiving window."""

    location_code: str
    label: str
    receiving_hours: Optional[str] = None


class ContactRecord(BaseModel):
    """Named contact on a customer (procurement, AP, warehouse, ...)."""

    model_config = ConfigDict(extra="allow")

    name: str
    role: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None


class ProductRecord(BaseModel):
    """One item from the product master (collection ``products``, doc id = sku).

    The validation pipeline reads ``unit_price_usd`` (price tolerance),
    ``min_order_qty`` / ``pack_size`` / ``uom`` / ``alt_uoms`` (quantity
    sanity), and ``short_description`` / ``long_description`` (rapidfuzz
    pool for SKU matching). All other attributes pass through via
    ``extra="allow"`` so the dashboard and future enrichment step can read
    them without schema changes here.
    """

    model_config = ConfigDict(extra="allow")

    sku: str
    short_description: str
    long_description: str
    category: str
    subcategory: Optional[str] = None
    uom: str
    pack_uom: Optional[str] = None
    pack_size: Optional[int] = None
    alt_uoms: list[str] = Field(default_factory=list)
    unit_price_usd: float
    standards: list[str] = Field(default_factory=list)
    lead_time_days: Optional[int] = None
    min_order_qty: Optional[int] = None
    country_of_origin: Optional[str] = None

    # Layer-2 seam. Populated by a future embedding-seed script; consumed by
    # ``FirestoreRepo.find_product_by_embedding``. Commented out until the
    # seed lands so the field does not appear on freshly-loaded docs and
    # confuse dashboard consumers.
    #
    # description_embedding: Optional[list[float]] = None


class CustomerRecord(BaseModel):
    """One trading partner from the customer master (collection ``customers``,
    doc id = customer_id).

    Carries the per-customer ``sku_aliases`` map (alias → canonical sku)
    that Layer-1 SKU matching consults before rapidfuzz.
    """

    model_config = ConfigDict(extra="allow")

    customer_id: str
    name: str
    dba: Optional[str] = None
    segment: str
    tax_id: Optional[str] = None
    duns: Optional[str] = None
    bill_to: AddressRecord
    ship_to: list[ShipToLocation] = Field(default_factory=list)
    payment_terms: str
    credit_limit_usd: float
    currency: str
    contacts: list[ContactRecord] = Field(default_factory=list)
    sku_aliases: dict[str, str] = Field(default_factory=dict)


class MetaRecord(BaseModel):
    """Catalog + customer-master version stamps (doc ``meta/master_data``).

    Used for audit-trail version stamping on validated orders and for the
    dashboard footer. ``seller_of_record`` is kept as a plain dict since
    the validator does not read into it.
    """

    model_config = ConfigDict(extra="allow")

    catalog_version: str
    catalog_effective_date: str
    currency: str
    master_version: str
    master_effective_date: str
    seller_of_record: dict[str, Any] = Field(default_factory=dict)


class EmbeddingMatch(BaseModel):
    """Return shape of :meth:`FirestoreRepo.find_product_by_embedding`.

    Stable now (Layer-2 stub returns an empty list of this type); populated
    by the real implementation once Gemini text-embedding-004 seeds the
    ``description_embedding`` field on each product and a vector index is
    declared.
    """

    sku: str
    score: float
    source: Literal["firestore_findnearest", "memory_cosine"] = "firestore_findnearest"


__all__ = [
    "AddressRecord",
    "ShipToLocation",
    "ContactRecord",
    "ProductRecord",
    "CustomerRecord",
    "MetaRecord",
    "EmbeddingMatch",
]
