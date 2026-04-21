"""Validator's tool collection.

The orchestrator (:class:`backend.tools.order_validator.OrderValidator`)
composes these tools per line item. Each tool is independently testable:
the data-access tool against the in-memory ``FakeAsyncClient`` fixture,
the pure-function tools against a ``ProductRecord`` literal.
"""

from backend.tools.order_validator.tools.firestore_client import (
    DEFAULT_PROJECT,
    get_async_client,
)
from backend.tools.order_validator.tools.master_data_repo import (
    CUSTOMERS_COLLECTION,
    DEFAULT_CUSTOMER_MATCH_THRESHOLD,
    DEFAULT_EMBEDDING_TOP_K,
    META_COLLECTION,
    META_DOC_ID,
    PRODUCTS_COLLECTION,
    MasterDataRepo,
)

__all__ = [
    "MasterDataRepo",
    "DEFAULT_PROJECT",
    "get_async_client",
    "PRODUCTS_COLLECTION",
    "CUSTOMERS_COLLECTION",
    "META_COLLECTION",
    "META_DOC_ID",
    "DEFAULT_CUSTOMER_MATCH_THRESHOLD",
    "DEFAULT_EMBEDDING_TOP_K",
]
