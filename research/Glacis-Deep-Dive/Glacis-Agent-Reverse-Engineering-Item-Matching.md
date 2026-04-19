---
type: research-deep-dive
topic: "Glacis AI Agent Reverse-Engineering: Order Intake + PO Confirmation"
subtopic: "Embedding-Based Item Matching"
overview: "[[Glacis-Agent-Reverse-Engineering-Overview]]"
depth_level: 3
date: 2026-04-08
tags:
  - research
  - supply-chain
  - item-matching
  - embedding-search
  - entity-resolution
  - firestore-vector
---

# Embedding-Based Item Matching

> [!info] Context — Part of [[Glacis-Agent-Reverse-Engineering-Overview]] deep dive. Depth level: 3
> Siblings: [[Glacis-Agent-Reverse-Engineering-Validation-Pipeline]] | [[Glacis-Agent-Reverse-Engineering-Firestore-Schema]] | [[Glacis-Agent-Reverse-Engineering-Learning-Loop]] | [[Glacis-Agent-Reverse-Engineering-Order-Intake-Agent]]

## The Problem

A customer emails: "50 cases Dark Roast 5lb bags." The ERP knows this product as `SKU-7042-DR5`, described internally as "Arabica Blend Dark Roast Ground Coffee, 5 lb Retail Bag." These two strings share almost no tokens. A naive string match fails. A keyword search fails. Even a fuzzy string distance metric like Jaro-Winkler, which handles typos well, cannot bridge the semantic gap between a customer's shorthand and an ERP's canonical description.

This is not an edge case. It is the default state of B2B ordering. Every customer has their own vocabulary. One calls it "Dark Roast 5lb bag." Another calls it "DR Ground 5#." A third uses an internal code from their procurement system that has no textual relationship to the product at all. Glacis's whitepaper notes that the Order Intake agent "maps customer descriptions to internal item codes" — a single sentence that hides one of the hardest problems in supply chain automation.

The problem is formally called **entity resolution**: determining that two different representations refer to the same real-world entity. In e-commerce, Google Shopping solves this to compare prices of the same product across retailers. In supply chain, Pallet's "Atlas" engine solves it to match carriers, customers, and locations across systems that each invented their own naming conventions. The academic literature frames it using the Fellegi-Sunter probabilistic model — calculating match weights from attribute agreement probabilities. But for our implementation, we need something faster, more semantic, and capable of learning from corrections. That something is embedding similarity search.

## Core Idea

Take every product in the catalog. Concatenate its name, description, aliases, and category into a single text block. Run it through an embedding model that compresses the semantic meaning of that text into a 768-dimensional vector. Store that vector alongside the product document in Firestore. When a customer order arrives, embed the customer's description using the same model. Find the stored vectors closest to the query vector. The closest match is your candidate SKU.

This works because embedding models are trained on massive corpora to place semantically similar texts near each other in vector space. "Dark Roast 5lb bag" and "Arabica Blend Dark Roast Ground Coffee, 5 lb Retail Bag" share the concepts of dark roast, coffee, 5 pounds, and bag — the embedding model knows these are near-synonyms even though the surface forms differ. The cosine distance between their vectors will be small. Meanwhile, "Dark Roast 5lb bag" and "Stainless Steel Mixing Bowl 5qt" might share the number 5 and a container word, but their embeddings will be far apart because the models understand the categorical difference.

The key insight: embedding search is not magic string matching. It is compressed world knowledge applied to your specific matching problem. The model has already learned that "5lb" means "5 pound," that "DR" is a common abbreviation for "Dark Roast" in coffee contexts, and that "bag" and "retail bag" are the same packaging. You get this for free from the pretrained model without writing a single rule.

## How It Works

### The Three-Tier Lookup

Not every match needs a vector search. Running embeddings on "SKU-7042-DR5" when the customer literally typed "SKU-7042-DR5" wastes latency and money. The architecture uses a three-tier cascade, inspired by Pallet's Atlas engine pattern:

**Tier 1: Exact Match.** Check if the customer's text exactly matches a known identifier — a SKU, a UPC, or a stored alias. This is a Firestore equality query, sub-10ms. If the customer types "SKU-7042-DR5" or if they always order "Dark Roast 5lb" and that exact string is already stored as an alias from a previous confirmed match, you are done. No embedding needed. In production, 40-60% of line items from repeat customers hit this tier because their ordering patterns are habitual.

**Tier 2: Embedding Similarity.** When exact match fails, embed the customer's description and run a vector nearest-neighbor search against the catalog. Return the top-K candidates (K=5 is a reasonable starting point). Apply confidence scoring to the results. This is where the interesting engineering happens.

**Tier 3: Human Escalation.** When embedding similarity is too low to trust, flag the line item for human review. Present the top candidates with their scores so the human reviewer has context, not just an error message. When the human confirms or corrects the mapping, feed it back as a new alias for Tier 1 (see [[Glacis-Agent-Reverse-Engineering-Learning-Loop]]).

This cascade means the expensive operation (embedding + vector search) only runs on the items that actually need it. And over time, as the alias table grows from human corrections, more items graduate to Tier 1 and the system gets both faster and cheaper.

### Embedding the Product Catalog

The catalog embedding happens at ingest time — when products are added or updated in the system — not at query time. For each product in the catalog, construct an embedding input string:

```
{product_name}. {description}. Category: {category}. Also known as: {alias_1}, {alias_2}, ...
```

Concatenating multiple fields into a single string gives the embedding model richer context. Including aliases is critical: if a human previously confirmed that "DR Ground 5#" maps to this SKU, baking that alias into the embedding means future queries with similar phrasing will match more strongly.

**Model choice: Vertex AI `text-embedding-004`.** This is Google's current production embedding model. It outputs 768-dimensional vectors by default, supports configurable dimensionality (you can request 256 dimensions for faster search at the cost of some accuracy), and handles the semantic nuances of product descriptions well. It supports task-type hints — use `RETRIEVAL_DOCUMENT` when embedding catalog items and `RETRIEVAL_QUERY` when embedding the customer's search text. This asymmetric embedding improves retrieval quality because the model optimizes differently for short queries versus long documents.

```python
from vertexai.language_models import TextEmbeddingModel

model = TextEmbeddingModel.from_pretrained("text-embedding-004")

# Embedding a catalog product (document side)
def embed_product(product: dict) -> list[float]:
    text = f"{product['name']}. {product['description']}. "
    text += f"Category: {product['category']}. "
    if product.get('aliases'):
        text += f"Also known as: {', '.join(product['aliases'])}."
    
    embeddings = model.get_embeddings(
        [text],
        output_dimensionality=768,
        task_type="RETRIEVAL_DOCUMENT"
    )
    return embeddings[0].values

# Embedding a customer query (query side)
def embed_query(customer_description: str) -> list[float]:
    embeddings = model.get_embeddings(
        [customer_description],
        output_dimensionality=768,
        task_type="RETRIEVAL_QUERY"
    )
    return embeddings[0].values
```

### Storing Vectors in Firestore

Firestore supports a native `Vector` field type. You store the embedding directly on the product document — no separate vector database, no data synchronization headaches, no additional infrastructure. The product document in Firestore looks like this:

```python
from google.cloud import firestore
from google.cloud.firestore_v1.vector import Vector

db = firestore.Client()

def store_product(product: dict, embedding: list[float]):
    db.collection("products").document(product["sku"]).set({
        "sku": product["sku"],
        "name": product["name"],
        "description": product["description"],
        "category": product["category"],
        "aliases": product.get("aliases", []),
        "active": True,
        "unit_price": product["unit_price"],
        "unit_of_measure": product["uom"],
        "embedding": Vector(embedding),
    })
```

Before you can query vectors, Firestore needs a vector index on the embedding field. Create it with the CLI:

```bash
gcloud firestore indexes composite create \
  --collection-group=products \
  --query-scope=COLLECTION \
  --field-config="vector-config={dimension:768,flat},field-path=embedding"
```

The `flat` index type means exact K-nearest-neighbor search — Firestore compares against every vector in the collection. For catalogs under ~100K products this is perfectly fine. If you scale to millions, you would need approximate nearest neighbor (ANN) indexing, which Firestore does not yet support natively — at that scale, you would add Vertex AI Vector Search as a sidecar. But for the hackathon build and most mid-market manufacturers, flat KNN on Firestore handles it.

### The Query Path

When the Order Intake agent extracts a line item with `customer_description: "Dark Roast 5lb bag"`, the matching function runs:

```python
from google.cloud.firestore_v1.base_vector_query import DistanceMeasure

def match_product(customer_description: str, top_k: int = 5):
    # Tier 1: exact alias match
    alias_query = (
        db.collection("products")
        .where("aliases", "array_contains", customer_description.lower().strip())
        .where("active", "==", True)
        .limit(1)
    )
    alias_results = alias_query.get()
    if alias_results:
        doc = alias_results[0]
        return {"sku": doc.id, "confidence": 1.0, "method": "alias"}

    # Tier 2: embedding similarity
    query_embedding = embed_query(customer_description)
    
    vector_results = (
        db.collection("products")
        .where("active", "==", True)
        .find_nearest(
            vector_field="embedding",
            query_vector=Vector(query_embedding),
            distance_measure=DistanceMeasure.COSINE,
            limit=top_k,
            distance_result_field="distance",
        )
        .get()
    )

    candidates = []
    for doc in vector_results:
        data = doc.to_dict()
        # Cosine distance: 0 = identical, 2 = opposite
        # Convert to similarity: 1 - (distance / 2)
        similarity = 1.0 - (data["distance"] / 2.0)
        candidates.append({
            "sku": doc.id,
            "name": data["name"],
            "similarity": similarity,
        })

    if not candidates:
        return {"sku": None, "confidence": 0.0, "method": "no_match"}

    best = candidates[0]

    # Confidence thresholds
    if best["similarity"] >= 0.90:
        return {"sku": best["sku"], "confidence": best["similarity"], "method": "auto"}
    elif best["similarity"] >= 0.70:
        return {
            "sku": best["sku"],
            "confidence": best["similarity"],
            "method": "suggest",
            "candidates": candidates[:3],
        }
    else:
        return {
            "sku": None,
            "confidence": best["similarity"],
            "method": "escalate",
            "candidates": candidates[:3],
        }
```

**Why cosine distance, not dot product or Euclidean?** Cosine distance measures the angle between vectors, ignoring magnitude. This matters because a short customer description ("DR 5lb") produces a vector with different magnitude than a long catalog description, but their directional similarity is what captures semantic overlap. Firestore's cosine distance ranges from 0 (identical direction) to 2 (opposite direction). Google's own documentation notes that `DOT_PRODUCT` with unit-normalized vectors is mathematically equivalent and slightly faster — worth considering if latency matters at scale.

### Hybrid Search: Vectors + Metadata Filters

One of Firestore's advantages over standalone vector databases is that `find_nearest()` composes with standard `where()` filters. Firestore applies the filters first, then searches within the filtered set. This enables hybrid search:

```python
# Search only within a specific product category
db.collection("products") \
    .where("category", "==", "coffee") \
    .where("active", "==", True) \
    .find_nearest(
        vector_field="embedding",
        query_vector=Vector(query_embedding),
        distance_measure=DistanceMeasure.COSINE,
        limit=5,
    )
```

When the agent can infer the product category from context (the email subject says "Coffee order" or the customer's profile shows they only buy from the coffee division), adding a category filter dramatically improves precision. You are searching a smaller, more relevant subset. A "5lb bag" in the coffee category is unambiguous. Without the filter, "5lb bag" might also match dog food, flour, or fertilizer.

### Confidence Scoring and the Human Boundary

The threshold values — 0.90 for auto-match, 0.70 for suggestion, below 0.70 for escalation — are starting points, not dogma. The right thresholds depend on the cost asymmetry of your errors:

- **False positive (wrong auto-match)**: You ship the wrong product. Customer is angry, return is expensive, OTIF metric takes a hit. In industries with safety implications (pharmaceutical, aerospace parts), a false positive is catastrophic.
- **False negative (unnecessary escalation)**: A human reviews a match that the system could have handled. You pay $2-5 in labor cost and add a few minutes of latency. Annoying but not dangerous.

For most supply chain contexts, the cost of a false positive far exceeds the cost of a false negative. This means you set the auto-match threshold conservatively high (0.90+) and accept that more items go to human review. As the alias table grows from human corrections, the percentage hitting Tier 1 (exact match) increases and the volume of items needing human review naturally decreases.

A practical calibration approach: run the embedding search against your first 500 historical orders where you know the correct SKU mapping. Plot similarity scores against correctness. Find the threshold where precision hits 99%+ and set your auto-match there. Measure recall at that threshold — the gap between recall and 100% is your human review volume.

## Tradeoffs

**Embedding search vs. traditional fuzzy matching (Jaro-Winkler, Levenshtein, tf-idf).** Traditional string distance metrics handle typos and minor variations well: "AirPods" vs "Airpods" vs "Air Pods." They fail completely on semantic equivalence: "wireless earbuds" vs "AirPods." Embeddings handle both, but they are more expensive (API call + vector search vs. in-memory string comparison) and introduce a dependency on an external model. For catalogs where customers mostly use recognizable product names with occasional typos, fuzzy matching might be sufficient. For catalogs where customers use their own vocabulary, embeddings are non-negotiable.

**Firestore vector search vs. dedicated vector database (Pinecone, Weaviate, Qdrant).** Firestore's vector search is flat KNN — it scales linearly with collection size. Dedicated vector databases use approximate nearest neighbor (ANN) algorithms like HNSW that scale logarithmically. For a product catalog of 1,000-50,000 SKUs (typical for a mid-market manufacturer), Firestore is plenty fast and eliminates an entire infrastructure component. For a distributor with 500K+ SKUs, you would outgrow flat KNN and need either Vertex AI Vector Search or a dedicated vector DB. The architectural decision: start with Firestore for simplicity, measure latency as the catalog grows, and add a vector sidecar only when you need it.

**Generic embeddings vs. fine-tuned embeddings.** `text-embedding-004` is a general-purpose model. It knows that "Dark Roast" and "Arabica Blend Dark Roast" are related, but it does not know your specific catalog's jargon, internal codes, or industry abbreviations. Fine-tuning the embedding model on your product catalog and historical order data (positive pairs: customer description + correct SKU description) would improve precision significantly. Vertex AI supports embedding model fine-tuning. The tradeoff: fine-tuning requires labeled training data (at least a few hundred confirmed customer-description-to-SKU pairs) and adds operational complexity. For the hackathon, generic embeddings plus the alias learning loop are sufficient. For production, fine-tuning is the highest-leverage accuracy improvement available.

**768 dimensions vs. reduced dimensions.** `text-embedding-004` supports outputting 256 or 512 dimensions instead of the full 768. Fewer dimensions mean smaller vectors, faster search, less storage, and lower cost. The tradeoff is accuracy — you lose some semantic discrimination. Google's benchmarks show minimal degradation at 512 dimensions and modest degradation at 256. For a product catalog where most items are semantically distinct (coffee vs. tea vs. filters vs. mugs), 256 dimensions might work fine. For a catalog with many near-identical variants (Dark Roast 5lb vs. Dark Roast 2lb vs. Medium Roast 5lb), you want the full 768 to distinguish between them.

## Common Misconceptions

**"Embeddings solve the matching problem end-to-end."** They do not. Embeddings handle semantic similarity, but product matching also requires structural reasoning. A customer orders "50 cases of Dark Roast 5lb" — the embedding matches "Dark Roast 5lb bag" with high confidence, but is "cases" the right unit? Does the catalog sell by the case or by the bag? Does 5lb refer to the bag size or the case weight? The embedding has no concept of units, quantities, or packaging hierarchies. You still need a validation layer (see [[Glacis-Agent-Reverse-Engineering-Validation-Pipeline]]) that checks the structural coherence of the match.

**"Higher similarity always means a better match."** Not necessarily. If a customer orders "Premium Dark Roast 5lb" and your catalog has "Dark Roast 5lb Bag" (similarity 0.93) and "Premium Dark Roast 12oz Bag" (similarity 0.91), the similarity scores are close but the products are completely different. The embedding model correctly ranks the 5lb version higher, but a small threshold change could flip the result. When top candidates have close scores, presenting multiple options to a human is safer than auto-matching on the marginal winner.

**"You need a vector database."** For product catalogs, you almost certainly do not. Dedicated vector databases are built for millions-to-billions of vectors with sub-millisecond latency requirements — think semantic search over the entire internet, or recommendation systems for streaming platforms. A manufacturer's product catalog has hundreds to tens of thousands of SKUs. Firestore's flat KNN handles this trivially. Adding Pinecone or Weaviate to the stack adds operational complexity, data synchronization challenges, and cost — all for a scale problem you do not have.

**"The 0.90 threshold is standard."** There is no standard. The right threshold depends on your embedding model, your catalog's semantic density (how many similar products exist), and your error cost asymmetry. A catalog with 50 distinct product lines can use a lower threshold than one with 5,000 variants of essentially the same chemical compound. Always calibrate against labeled data from your own domain.

## Connections

- **Upstream**: The Order Intake agent ([[Glacis-Agent-Reverse-Engineering-Order-Intake-Agent]]) calls item matching as Step 3 after multi-format extraction. The input is `customer_description` strings from the extraction output.
- **Downstream**: Matched SKUs feed into the validation pipeline ([[Glacis-Agent-Reverse-Engineering-Validation-Pipeline]]) for price, quantity, inventory, and credit checks.
- **Schema**: Product documents with embedded vectors live in Firestore. The schema design — including how aliases accumulate and how embeddings are recomputed — is detailed in [[Glacis-Agent-Reverse-Engineering-Firestore-Schema]].
- **Learning**: Human corrections to matches feed back as aliases, closing the loop. Over time, the system bootstraps its own matching accuracy. See [[Glacis-Agent-Reverse-Engineering-Learning-Loop]].
- **Pallet parallel**: Pallet's "Atlas" entity resolution engine uses the same three-tier pattern (exact → fuzzy → unknown/flag-for-human) for carrier, customer, and location matching across supply chain systems. Their pattern validates ours.
- **Broader context**: This is one implementation of the entity resolution pattern from [[Glacis-Agent-Reverse-Engineering-Overview]]. The same embedding approach could apply to supplier matching in the PO Confirmation agent ([[Glacis-Agent-Reverse-Engineering-PO-Confirmation-Agent]]) — mapping supplier company names from email signatures to canonical vendor records.
- **Google ecosystem**: Vertex AI `text-embedding-004` + Firestore vector search + Cloud Run for the matching service. All Google-native, all within the Solution Challenge's expected stack. No third-party vector database needed.
