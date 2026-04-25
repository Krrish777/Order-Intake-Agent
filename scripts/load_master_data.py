"""Load data/masters/{products,customers}.json into Firestore.

Idempotent: re-running overwrites docs by ID. Selects emulator vs live via
FIRESTORE_EMULATOR_HOST (auto-detected by the client) and GOOGLE_CLOUD_PROJECT.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Optional

from google.cloud import firestore
from google.cloud.firestore_v1.vector import Vector
from google.genai import Client as GenAIClient
from google.genai.types import EmbedContentConfig

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "masters"
DEFAULT_PROJECT = "demo-order-intake-local"

EMBED_MODEL = "text-embedding-004"
EMBED_DIM = 768


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


def _embed_text(genai: GenAIClient, text: str) -> list[float]:
    """Compute one RETRIEVAL_DOCUMENT embedding for `text` via the
    google-genai sync client. Returns the 768-dim float vector.

    Used at seed time only. The repo's query-side hot path uses the
    async variant (MasterDataRepo._embed_query) with task_type
    RETRIEVAL_QUERY for the asymmetric-embedding pattern.
    """
    response = genai.models.embed_content(
        model=EMBED_MODEL,
        contents=[text],
        config=EmbedContentConfig(
            task_type="RETRIEVAL_DOCUMENT",
            output_dimensionality=EMBED_DIM,
        ),
    )
    return list(response.embeddings[0].values)


def _client() -> firestore.Client:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", DEFAULT_PROJECT)
    return firestore.Client(project=project)


def load_products(db: firestore.Client, *, with_embeddings: bool = True) -> int:
    """Seed the products collection from data/masters/products.json.

    Idempotent: re-running overwrites each product doc by sku. When
    `with_embeddings=True` (default), each doc gains a
    `description_embedding: Vector(768)` field computed via
    text-embedding-004. Pass `with_embeddings=False` for offline
    seed runs that don't have a GOOGLE_API_KEY available.
    """
    payload = json.loads((DATA_DIR / "products.json").read_text(encoding="utf-8"))
    products = payload["products"]

    genai: Optional[GenAIClient] = GenAIClient() if with_embeddings else None

    batch = db.batch()
    for product in products:
        doc = dict(product)
        if with_embeddings and genai is not None:
            text = _embed_text_for_product(product)
            doc["description_embedding"] = Vector(_embed_text(genai, text))
        batch.set(db.collection("products").document(product["sku"]), doc)
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
    parser = argparse.ArgumentParser(description="Seed master-data collections.")
    parser.add_argument(
        "--embeddings",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Compute + persist text-embedding-004 vectors on each product "
            "doc (default: --embeddings). Use --no-embeddings for offline "
            "seed runs without a GOOGLE_API_KEY."
        ),
    )
    args = parser.parse_args()

    target = os.environ.get("FIRESTORE_EMULATOR_HOST") or "live Firestore"
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", DEFAULT_PROJECT)
    print(f"Loading into: {target} (project={project})")

    db = _client()
    n_products = load_products(db, with_embeddings=args.embeddings)
    n_customers = load_customers(db)
    load_meta(db)

    emb_note = "with embeddings" if args.embeddings else "NO embeddings (offline mode)"
    print(f"Loaded {n_products} products ({emb_note}), {n_customers} customers, 1 meta doc.")


if __name__ == "__main__":
    main()
