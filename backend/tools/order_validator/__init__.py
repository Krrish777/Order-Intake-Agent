"""Order validator — Track V of the Order Intake Agent.

Public surface for the rest of the agent: construct ``MasterDataRepo``
with an ``AsyncClient`` (emulator or live), pass it to
``OrderValidator``, call ``validate(parsed_document)``, route on the
returned ``ValidationResult.decision``.

The validator is composed of tools rather than monolithic so each
concern (sku matching, price tolerance, qty sanity, customer resolution)
can be unit-tested in isolation. Tools live under ``.tools``; the
orchestrator + scorer + router sit at this level.
"""

from backend.models.master_records import (
    AddressRecord,
    ContactRecord,
    CustomerRecord,
    EmbeddingMatch,
    MetaRecord,
    ProductRecord,
    ShipToLocation,
)
from backend.models.validation_result import (
    AUTO_THRESHOLD,
    CLARIFY_THRESHOLD,
    LineItemValidation,
    RoutingDecision,
    ValidationResult,
)
from backend.tools.order_validator.tools import (
    CUSTOMERS_COLLECTION,
    DEFAULT_CUSTOMER_MATCH_THRESHOLD,
    DEFAULT_EMBEDDING_TOP_K,
    DEFAULT_PROJECT,
    META_COLLECTION,
    META_DOC_ID,
    PRODUCTS_COLLECTION,
    MasterDataRepo,
    get_async_client,
)
from backend.tools.order_validator.validator import OrderValidator

__all__ = [
    # orchestrator + contracts
    "OrderValidator",
    "ValidationResult",
    "LineItemValidation",
    "RoutingDecision",
    "AUTO_THRESHOLD",
    "CLARIFY_THRESHOLD",
    # data-access tool
    "MasterDataRepo",
    "DEFAULT_PROJECT",
    "get_async_client",
    # collection-name + threshold constants
    "PRODUCTS_COLLECTION",
    "CUSTOMERS_COLLECTION",
    "META_COLLECTION",
    "META_DOC_ID",
    "DEFAULT_CUSTOMER_MATCH_THRESHOLD",
    "DEFAULT_EMBEDDING_TOP_K",
    # typed records (re-exported for convenience)
    "AddressRecord",
    "ContactRecord",
    "CustomerRecord",
    "EmbeddingMatch",
    "MetaRecord",
    "ProductRecord",
    "ShipToLocation",
]
