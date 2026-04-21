"""Master-data access layer.

Exposes :class:`FirestoreRepo` and the record types it returns. The
downstream validator imports from here; callers should not reach into
submodules unless they need the collection-name constants.
"""

from backend.data.firestore_client import DEFAULT_PROJECT, get_async_client
from backend.data.firestore_repo import (
    CUSTOMERS_COLLECTION,
    DEFAULT_CUSTOMER_MATCH_THRESHOLD,
    DEFAULT_EMBEDDING_TOP_K,
    META_COLLECTION,
    META_DOC_ID,
    PRODUCTS_COLLECTION,
    FirestoreRepo,
)
from backend.models.master_records import (
    AddressRecord,
    ContactRecord,
    CustomerRecord,
    EmbeddingMatch,
    MetaRecord,
    ProductRecord,
    ShipToLocation,
)

__all__ = [
    "FirestoreRepo",
    "DEFAULT_PROJECT",
    "get_async_client",
    "AddressRecord",
    "ContactRecord",
    "CustomerRecord",
    "EmbeddingMatch",
    "MetaRecord",
    "ProductRecord",
    "ShipToLocation",
    "PRODUCTS_COLLECTION",
    "CUSTOMERS_COLLECTION",
    "META_COLLECTION",
    "META_DOC_ID",
    "DEFAULT_CUSTOMER_MATCH_THRESHOLD",
    "DEFAULT_EMBEDDING_TOP_K",
]
