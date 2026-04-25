"""Load data/masters/{products,customers}.json into Firestore.

Idempotent: re-running overwrites docs by ID. Selects emulator vs live via
FIRESTORE_EMULATOR_HOST (auto-detected by the client) and GOOGLE_CLOUD_PROJECT.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from google.cloud import firestore

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "masters"
DEFAULT_PROJECT = "demo-order-intake-local"


def _embed_text_for_product(p: dict) -> str:
    """Compose the text string fed to text-embedding-004 for one catalog item.

    Includes short_description (customer-shorthand form), long_description
    (canonical/detailed form), and category (+ subcategory if present).
    The embedding model uses the combined context to map customer
    shorthand onto canonical SKUs.
    """
    short = p["short_description"].rstrip(".")
    long_ = p["long_description"].rstrip(".")
    cat = p.get("category", "")
    sub = p.get("subcategory", "")
    suffix = f"{cat}/{sub}" if sub else cat
    return f"{short}. {long_}. Category: {suffix}."


def _client() -> firestore.Client:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", DEFAULT_PROJECT)
    return firestore.Client(project=project)


def load_products(db: firestore.Client) -> int:
    payload = json.loads((DATA_DIR / "products.json").read_text(encoding="utf-8"))
    products = payload["products"]
    batch = db.batch()
    for product in products:
        batch.set(db.collection("products").document(product["sku"]), product)
    batch.commit()
    return len(products)


def load_customers(db: firestore.Client) -> int:
    payload = json.loads((DATA_DIR / "customers.json").read_text(encoding="utf-8"))
    customers = payload["customers"]
    batch = db.batch()
    for customer in customers:
        batch.set(db.collection("customers").document(customer["customer_id"]), customer)
    batch.commit()
    return len(customers)


def load_meta(db: firestore.Client) -> None:
    products_payload = json.loads((DATA_DIR / "products.json").read_text(encoding="utf-8"))
    customers_payload = json.loads((DATA_DIR / "customers.json").read_text(encoding="utf-8"))
    db.collection("meta").document("master_data").set(
        {
            "catalog_version": products_payload["catalog_version"],
            "catalog_effective_date": products_payload["effective_date"],
            "currency": products_payload["currency"],
            "master_version": customers_payload["master_version"],
            "master_effective_date": customers_payload["effective_date"],
            "seller_of_record": customers_payload["seller_of_record"],
        }
    )


def main() -> None:
    target = os.environ.get("FIRESTORE_EMULATOR_HOST") or "live Firestore"
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", DEFAULT_PROJECT)
    print(f"Loading into: {target} (project={project})")

    db = _client()
    n_products = load_products(db)
    n_customers = load_customers(db)
    load_meta(db)

    print(f"Loaded {n_products} products, {n_customers} customers, 1 meta doc.")


if __name__ == "__main__":
    main()
