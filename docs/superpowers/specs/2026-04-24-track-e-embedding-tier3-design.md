---
type: design-spec
topic: "Track E — Embedding Tier 3 (text-embedding-004 + Firestore find_nearest)"
track: E
date: 2026-04-24
parent: "research/Order-Intake-Sprint-Status.md"
source_spec: "research/Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Item-Matching.md + Glacis-Order-Intake.md §5 'Tier 3 embedding search'"
status: approved-for-implementation
depends_on: []
blocks: []
tags:
  - design-spec
  - track-e
  - embedding
  - text-embedding-004
  - firestore-vector
  - find-nearest
  - item-matching
---

# Track E — Embedding Tier 3 — Design

## Summary

Replace the stub at `MasterDataRepo.find_product_by_embedding(query, k=5)` with a real `text-embedding-004` + Firestore `find_nearest` implementation, and extend `scripts/load_master_data.py` to batch-compute and persist per-product embeddings at seed time. Closes the 3-tier SKU ladder: tier 1 (exact) + tier 2 (fuzzy) already work on master; tier 3 is currently a cleanly-falling-through stub. After Track E, tier 3 returns real `EmbeddingMatch` candidates from a flat-KNN cosine-distance search over the `products` collection.

**Scope boundary.** Only the stub gets replaced + the seed script gets extended + a Firestore vector index lands. The `EmbeddingMatch` Pydantic schema (`sku`, `score`, `source`) is already stable. The `sku_matcher.match_sku` call site stays byte-identical. `EMBEDDING_THRESHOLD = 0.70` stays. No new top-level `pyproject.toml` dependencies — `google-genai` is already a transitive dep of `google-adk`.

This closes Glacis `Item-Matching.md` + `Glacis-Order-Intake.md` §5 "Tier 3 embedding search with text-embedding-004", flipping `[Post-MVP]` → `[MVP ✓]`. The alias-learning portion of §5 stays `[Post-MVP]` (separate Learning Loop track).

## Context

- Stub at `backend/tools/order_validator/tools/master_data_repo.py:206-224` always returns `[]`. Docstring explicitly calls out the shape it will take: *"embed `query` via Gemini `text-embedding-004` and call Firestore `AsyncVectorQuery.find_nearest` against the `description_embedding` field on `products`"*. Track E executes that design.
- `sku_matcher.match_sku` at `backend/tools/order_validator/tools/sku_matcher.py:102-116` already calls `find_product_by_embedding` unconditionally with `EMBEDDING_THRESHOLD = 0.70`. Zero change needed to the matcher — swapping the stub in-place is a one-method surface area change.
- `EmbeddingMatch` Pydantic model at `backend/models/master_records.py:135-146` is stable: `sku: str`, `score: float`, `source: Literal["firestore_findnearest", "memory_cosine"] = "firestore_findnearest"`.
- `ProductRecord` uses `extra="allow"` (backend/models/master_records.py:56-), so a new Firestore-side `description_embedding: Vector(768)` field deserializes without any Pydantic change.
- `scripts/load_master_data.py` is sync (`firestore.Client` + `.batch()`) over 35 products from `data/masters/products.json`. 35 × one embedding call per product is trivially fast on a seed run.
- `google-genai` SDK is already installed as a transitive dep of `google-adk>=1.31.0`. It exposes `google.genai.Client().models.embed_content(...)` (sync) and `.aio.models.embed_content(...)` (async). Read-path (repo) uses async; seed script uses sync.
- Firestore emulator supports `find_nearest` as of early 2025 per Google release notes. Live Firestore requires an explicit composite vector index declared via `gcloud firestore indexes composite create`. `firebase/firestore.indexes.json` does **not** support the `vectorConfig` syntax as of 2026-04; index creation is CLI-only.
- The Glacis note recommends: 768 dims, `COSINE` distance, flat KNN index, asymmetric task types (`RETRIEVAL_DOCUMENT` for catalog, `RETRIEVAL_QUERY` for customer text), threshold 0.90 auto / 0.70 suggest. This spec picks the single-threshold (0.70) variant matching the existing `sku_matcher` constant — Decision 5.

## Decisions

### Decision 1 — Embedding model + SDK: `text-embedding-004` via `google-genai`

Use `google.genai.Client().aio.models.embed_content(model="text-embedding-004", contents=[text], config=EmbedContentConfig(task_type=..., output_dimensionality=768))` for the repo's read path. Sync variant `client.models.embed_content(...)` for the seed script.

`google-genai` is already a transitive dep of `google-adk>=1.31.0`. No new top-level pyproject entry required.

**Rejected:**
- `gemini-embedding-001` — newer model; no corpus evidence it helps this catalog; Glacis spec explicitly names `text-embedding-004`; the older model is better-tested and has stable task-type semantics.
- `google-cloud-aiplatform` / `vertexai` SDK — heavier dependency, redundant with the already-transitive `google-genai`, adds a second auth path.

### Decision 2 — Dimensionality: 768 (default, full)

Keep `output_dimensionality=768` on both catalog-side and query-side calls.

**Rationale.** The catalog has many near-identical fastener variants (`HCS 1/2-13 x 2 GR5 ZP` vs `HCS 1/2-13 x 1-1/2 SS18-8` vs `HCS 3/8-16 x 1 GR8 YZ`) where small semantic distinctions (grade, finish, length, thread) need discrimination. 768-dim preserves the most signal. At 35 products, storage + latency cost is irrelevant.

**Rejected:** 256 or 512 — premature optimization; Google benchmarks show measurable accuracy loss at 256; this catalog is exactly the case where full-dim retention matters.

### Decision 3 — Asymmetric task types

Catalog-side (seed script): `task_type="RETRIEVAL_DOCUMENT"`.
Query-side (`find_product_by_embedding`): `task_type="RETRIEVAL_QUERY"`.

The asymmetry is documented by Google as the retrieval-quality optimization for embedding models. Short queries and long documents have different optimal vector distributions; the task-type hint lets the model shape the vector differently for each role.

**Rejected:** Symmetric (`SEMANTIC_SIMILARITY` on both sides) — loses the retrieval-optimized shape; Glacis spec explicitly calls out the asymmetric pattern.

### Decision 4 — Distance measure: COSINE + flat KNN

Use `DistanceMeasure.COSINE` on the `description_embedding` field. Flat KNN index type (Firestore's only option as of 2026-04). The query surfaces the distance as a `distance_result_field` and the repo converts cosine distance `d ∈ [0, 2]` to similarity `s = 1.0 - d / 2.0`, clamped to `[0, 1]`. The resulting `score: float` on `EmbeddingMatch` is a monotonic 0-to-1 similarity matching the existing `EMBEDDING_THRESHOLD = 0.70` check in `sku_matcher`.

**Rejected:**
- `DOT_PRODUCT` — mathematically equivalent to cosine on unit vectors + slightly faster, but `text-embedding-004` does not emit unit-normalized vectors by default. Normalizing would require an extra step; the speedup is meaningless at 35 products.
- `EUCLIDEAN_L2` — magnitude-sensitive. Bad fit for variable-length text embeddings where the customer's short query has different magnitude than the catalog's long description.

### Decision 5 — Threshold: flat 0.70 (existing constant)

Keep `EMBEDDING_THRESHOLD = 0.70` in `sku_matcher.py`. `find_product_by_embedding` returns candidates ordered by descending score. The existing matcher check `if top.score >= EMBEDDING_THRESHOLD: return (product, "embedding", top.score)` decides hit vs miss.

Low-confidence hits (score < 0.70) fall through to the existing tier-3 miss path; the validator's aggregate scoring + routing layer handles the CLARIFY/ESCALATE decision based on overall confidence, not a separate per-tier gate.

**Rejected:** Tiered 0.90/0.70 (Glacis spec's two-threshold ladder with `suggest` middle tier) — introduces a second constant + a `suggest` return path that doesn't match the existing `(Product, MatchTier, float)` tuple shape; the validator's routing aggregate handles confidence-based triage already. Adding a `suggest` tier would churn the MatchTier enum + downstream scoring code for no behavior change from the perspective of the CLARIFY/ESCALATE outcome.

### Decision 6 — Seeding strategy: extend `scripts/load_master_data.py`, idempotent re-run

Single script, idempotent. Computes 35 embeddings once via `google.genai.Client().models.embed_content(...)` (sync), attaches `description_embedding: Vector(embedding_list)` to each product doc, writes the full batch. Idempotency: re-running the script re-computes + overwrites, matching today's non-embedding-field behavior.

CLI flag `--no-embeddings` skips the genai call for environments without an API key (useful for first-pass emulator runs and unit-test fixtures). Default is `True` so production seed runs always compute embeddings.

**Rejected:**
- Lazy / on-demand embedding inside `find_product_by_embedding` on first miss — introduces a first-request cold path, partial-catalog-embedded race states, and latency spikes. No benefit for 35 products seeded once.
- Separate `scripts/embed_products.py` — two scripts for one concept; keeps state fragmented; `load_master_data.py` is the canonical seed path and should own the full write.

### Decision 7 — Full-catalog search, no category pre-filter

`find_product_by_embedding` runs `products_collection.find_nearest(...)` without any `.where()` clauses beyond defaults. The method signature stays `(query: str, k: int = 5) -> list[EmbeddingMatch]` — no new kwargs. Works with the existing `sku_matcher` call site (passes only `line.description`); no category inference required from the caller.

**Rationale.** At 35 products in 3-4 categories, category pre-filtering adds scope (requires inference) for <1% precision lift. The embedding model's learned category priors handle fastener-vs-non-fastener discrimination natively.

**Rejected:** Category-filtered hybrid search — requires the caller (or `sku_matcher`) to infer category first; meaningful win only at 100K+ SKU scale. Listed as Post-MVP extension.

### Decision 8 — Embedding input text composition

Per product at seed time:

```
{short_description}. {long_description}. Category: {category}/{subcategory}.
```

Example for SKU `FST-HCS-050-13-200-G5Z`:

```
HCS 1/2-13 x 2 GR5 ZP. Hex Head Cap Screw, 1/2"-13 UNC x 2" OAL, Steel Grade 5, Zinc Plated (Clear), Plain Washer Face. Category: fasteners/hex_cap_screws.
```

Trailing slash-subcategory omitted if `subcategory` is empty or missing: `Category: {category}.`

Query text: the caller passes `line.description` (customer free-text) unchanged. The embedding model handles the semantic gap between customer shorthand ("dark roast 5lb bag") and catalog long-form.

**Rejected:**
- `long_description` only — loses the short-form shorthand that customers often type; the embedding model handles both together better than either alone.
- `+ aliases` — product-level aliases don't exist in the current schema (`sku_aliases` lives on `CustomerRecord`, not `ProductRecord`). Adding them requires the Learning Loop track.

### Decision 9 — Fail-open on embedding API errors

Any exception during `embed_content` (API timeout, 5xx, quota exhaustion, malformed response, Pydantic parse error) → log a `warning` via the existing `backend.utils.logging` helper and return `[]` from `find_product_by_embedding`. The caller (`sku_matcher`) treats `[]` as a tier-3 miss and falls through to `(None, "none", 0.0)`, exactly matching today's stub behavior. The validator's aggregate scoring then routes to CLARIFY/ESCALATE.

**Rationale.** Tier 3 is on the *inbound* read path. Fail-closed here would block the entire validation pipeline for orders where a real SKU is also missing — a much worse failure mode than a false escalation. The `sku_matcher` ladder already short-circuits on tier-1 hits, so fail-open on tier-3 preserves the property that Gemini outages don't harm orders that would match on earlier tiers anyway.

This is the opposite of Track B's judge (fail-closed on the egress side), for good reason: Track B protects the outbound communication; Track E enriches the inbound extraction.

**Rejected:** Fail-closed — any transient Gemini outage would prevent even tier-1/2 matches from resolving downstream orders; the judge gate in Track B already protects outbound communication.

## Data model

No Pydantic schema changes. `EmbeddingMatch` is already the stable return type (`sku`, `score`, `source: Literal["firestore_findnearest", "memory_cosine"]`). `ProductRecord` uses `extra="allow"`, so the new Firestore-side `description_embedding` field (Vector type, 768 floats) passes through untouched on reads.

Firestore document shape after Track E lands:

```json
{
  "sku": "FST-HCS-050-13-200-G5Z",
  "short_description": "HCS 1/2-13 x 2 GR5 ZP",
  "long_description": "Hex Head Cap Screw, 1/2\"-13 UNC x 2\" OAL, Steel Grade 5, Zinc Plated (Clear), Plain Washer Face",
  "category": "fasteners",
  "subcategory": "hex_cap_screws",
  "unit_price_usd": 0.34,
  "…other fields…": "…",
  "description_embedding": <Vector of 768 float32>
}
```

## Architecture

### MasterDataRepo — embedding + query

```python
# backend/tools/order_validator/tools/master_data_repo.py

from typing import Optional
from google.genai import Client as GenAIClient
from google.genai.types import EmbedContentConfig
from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
from google.cloud.firestore_v1.vector import Vector

EMBED_MODEL = "text-embedding-004"
EMBED_DIM = 768

class MasterDataRepo:
    def __init__(
        self,
        client: AsyncClient,
        *,
        genai_client: Optional[GenAIClient] = None,   # NEW — optional DI
    ) -> None:
        self._client = client
        self._products_cache = None
        self._customers_cache = None
        self._genai_client = genai_client      # lazily constructed if None

    def _ensure_genai_client(self) -> GenAIClient:
        if self._genai_client is None:
            self._genai_client = GenAIClient()    # reads GOOGLE_API_KEY / ADC
        return self._genai_client

    async def _embed_query(self, text: str) -> Optional[list[float]]:
        try:
            response = await self._ensure_genai_client().aio.models.embed_content(
                model=EMBED_MODEL,
                contents=[text],
                config=EmbedContentConfig(
                    task_type="RETRIEVAL_QUERY",
                    output_dimensionality=EMBED_DIM,
                ),
            )
            return list(response.embeddings[0].values)
        except Exception as exc:    # noqa: BLE001 — fail-open
            _log.warning("embedding_query_failed", error=str(exc), text=text[:80])
            return None

    async def find_product_by_embedding(
        self,
        query: str,
        k: int = DEFAULT_EMBEDDING_TOP_K,
    ) -> list[EmbeddingMatch]:
        if not query or not query.strip() or k < 1:
            return []

        query_vec = await self._embed_query(query)
        if query_vec is None:
            return []      # fail-open

        vector_query = (
            self._client
                .collection(PRODUCTS_COLLECTION)
                .find_nearest(
                    vector_field="description_embedding",
                    query_vector=Vector(query_vec),
                    distance_measure=DistanceMeasure.COSINE,
                    limit=k,
                    distance_result_field="__distance",
                )
        )

        matches: list[EmbeddingMatch] = []
        async for snap in vector_query.stream():
            data = snap.to_dict() or {}
            distance = float(data.get("__distance", 2.0))
            similarity = max(0.0, min(1.0, 1.0 - distance / 2.0))
            matches.append(EmbeddingMatch(
                sku=snap.id,
                score=similarity,
                source="firestore_findnearest",
            ))
        return matches
```

### load_master_data.py — seed with embeddings

```python
# scripts/load_master_data.py  — additions to existing sync flow

from google.cloud.firestore_v1.vector import Vector
from google.genai import Client as GenAIClient
from google.genai.types import EmbedContentConfig

EMBED_MODEL = "text-embedding-004"
EMBED_DIM = 768


def _embed_text_for_product(p: dict) -> str:
    short = p["short_description"]
    long_ = p["long_description"]
    cat   = p.get("category", "")
    sub   = p.get("subcategory", "")
    suffix = f"{cat}/{sub}" if sub else cat
    return f"{short}. {long_}. Category: {suffix}."


def _embed_text(genai: GenAIClient, text: str) -> list[float]:
    response = genai.models.embed_content(
        model=EMBED_MODEL,
        contents=[text],
        config=EmbedContentConfig(
            task_type="RETRIEVAL_DOCUMENT",
            output_dimensionality=EMBED_DIM,
        ),
    )
    return list(response.embeddings[0].values)


def load_products(db: firestore.Client, *, with_embeddings: bool = True) -> int:
    payload  = json.loads((DATA_DIR / "products.json").read_text(encoding="utf-8"))
    products = payload["products"]

    genai = GenAIClient() if with_embeddings else None

    batch = db.batch()
    for product in products:
        doc = dict(product)
        if with_embeddings:
            text = _embed_text_for_product(product)
            doc["description_embedding"] = Vector(_embed_text(genai, text))
        batch.set(db.collection("products").document(product["sku"]), doc)
    batch.commit()
    return len(products)
```

CLI: `python scripts/load_master_data.py [--no-embeddings]`. `argparse.BooleanOptionalAction` exposes the flag. Default `True`.

### Wiring the genai client

`_build_default_root_agent` in `backend/my_agent/agent.py` constructs one shared `GenAIClient()` and threads it into `MasterDataRepo`. Test fixtures inject a mock client. `MasterDataRepo.__init__`'s `genai_client: Optional[GenAIClient] = None` kwarg means existing `MasterDataRepo(client)` call sites continue working (lazy init on first embedding call).

### Vector index

`firebase/firestore.indexes.json` does **not** yet support `vectorConfig` composite indexes via the file-based declaration. Index creation is CLI-only:

```bash
gcloud firestore indexes composite create \
  --collection-group=products \
  --query-scope=COLLECTION \
  --field-config='vector-config={"dimension":768,"flat":{}},field-path=description_embedding'
```

Documented in `backend/my_agent/README.md` under a new "Vector index setup" subsection. For the emulator, `find_nearest` works on the in-memory dataset without an explicit index declaration.

## Failure matrix

| Scenario | `find_product_by_embedding` behavior | Downstream effect |
|---|---|---|
| Happy path | Returns `list[EmbeddingMatch]` ordered by descending score | `sku_matcher` returns `(product, "embedding", score)` if `top.score >= 0.70` |
| Query empty / whitespace / `k < 1` | Returns `[]` immediately (no API call) | `sku_matcher` falls through to tier-3 miss |
| Embedding API timeout / 5xx / quota | Caught; logs `embedding_query_failed`; returns `[]` | Same as above — tier-3 miss; aggregate routing decides CLARIFY/ESCALATE |
| Malformed embedding response (no `.embeddings` or empty) | Caught; logs + returns `[]` | Same tier-3 miss path |
| Firestore `find_nearest` raises (emulator lacks support, network, etc.) | Exception bubbles from `async for snap in …stream()` | Falls out to pipeline's existing error handling; logged via existing validator path |
| Vector index missing in prod | `find_nearest` raises `FailedPrecondition` | Operator runs the `gcloud` index-create command from the README; one-time setup per project |
| Product has no `description_embedding` field (seed-script skipped for some SKUs) | `find_nearest` omits that product per Firestore semantics | Benign: product unreachable by tier 3; still matchable by tier 1 / 2 |
| Cosine distance > 2.0 (malformed / unnormalized) | Clamped to similarity = 0.0 | Worst case: candidate returned with score 0.0; 0.70 threshold blocks it |
| Catalog empty (0 products) | `find_nearest` returns no docs | `[]` returned; tier-3 miss |

## New / modified files

### New

| Path | Purpose |
|---|---|
| `tests/unit/test_embedding_matcher.py` | Unit tests for `MasterDataRepo._embed_query` + `find_product_by_embedding` — ~6 tests covering happy path, empty query, API error → `[]`, distance→similarity conversion, `k` param forwarding, malformed response. Mocks `google.genai.Client.aio.models.embed_content` + the Firestore async vector query. |
| `tests/integration/test_find_nearest_emulator.py` | Gated emulator test — seed 3 products with precomputed embedding fixtures (checked-in JSON), call `find_product_by_embedding("widget red")`, assert top candidate is the red widget with `score ≥ 0.70`. Gated `@pytest.mark.firestore_emulator`. |
| `tests/fixtures/embedding_fixtures.py` or similar | Precomputed embedding vectors for the 3 seeded test products, so the integration test doesn't depend on live Gemini. |

### Modified

| Path | Change |
|---|---|
| `backend/tools/order_validator/tools/master_data_repo.py` | Replace stub `find_product_by_embedding` body with real impl; add `_embed_query` helper; add optional `genai_client` kwarg to `__init__`; add lazy `_ensure_genai_client`; import `Vector`, `DistanceMeasure`, `google.genai.Client`, `EmbedContentConfig`. |
| `scripts/load_master_data.py` | Add `_embed_text_for_product` + `_embed_text` helpers; `load_products` gains `with_embeddings: bool = True` kwarg + CLI `--no-embeddings` flag (via `argparse.BooleanOptionalAction`); attach `description_embedding` Vector to each product doc. |
| `backend/my_agent/agent.py` | `_build_default_root_agent` constructs a shared `google.genai.Client()` + threads it into `MasterDataRepo(client, genai_client=genai)`. `build_root_agent` signature unchanged (repo's genai client kwarg is optional, not a new top-level dep). |
| `tests/unit/test_master_data_repo.py` | Replace the *"stub returns empty list"* test with: `genai_client` kwarg injected + used; lazy construction when not provided; empty-query early return. |
| `tests/unit/test_sku_matcher.py` | Extend tier-3 test to exercise a stubbed `find_product_by_embedding` that returns a real `EmbeddingMatch` with score 0.85 — assert `match_sku` returns `("embedding", 0.85)`. |
| `tests/unit/test_load_master_data.py` (new or extending existing tests for the script) | Two tests: `_embed_text_for_product` composes the expected string for a sample product; `load_products(with_embeddings=False)` skips the genai call entirely. |
| `backend/my_agent/README.md` | Add "Vector index setup" subsection with the `gcloud firestore indexes composite create ...` command + a note that the emulator auto-handles `find_nearest`. |
| `research/Order-Intake-Sprint-Status.md` | Flip row "2d. Enrichment (item matching)" tier 3 from "stub falls through cleanly" to "text-embedding-004 + find_nearest live"; bump test count; append to Built inventory; append session note to last_updated frontmatter. |
| `Glacis-Order-Intake.md` | §5 "Tier 3 embedding search with text-embedding-004 + alias learning" — split bullet: flip embedding portion `[Post-MVP]` → `[MVP ✓]`; keep alias-learning portion `[Post-MVP]` with explicit *"out of scope per Track E"* note. Phase 3 roadmap removes the embedding portion. last_updated frontmatter bumped. |

## Test plan

**Unit (+~10 tests):**

| File | Count | What |
|---|---|---|
| `test_embedding_matcher.py` | 6 | happy path (returns sorted matches); empty/whitespace query → `[]` no API call; `k < 1` → `[]`; genai exception → `[]` + warning logged; distance→similarity conversion (d=0→s=1.0, d=1.0→s=0.5, d=2.0→s=0.0); `k` param forwarded to `find_nearest.limit`. |
| `test_master_data_repo.py` (update) | 1 | `genai_client` kwarg injected + used; lazy construction when omitted. |
| `test_sku_matcher.py` (update) | 1 | tier-3 hit with stubbed `find_product_by_embedding` returning score 0.85 → `match_sku` returns `(product, "embedding", 0.85)`. |
| `test_load_master_data.py` (new or extend) | 2 | `_embed_text_for_product` composition matches expected string for a sample product; `load_products(with_embeddings=False)` skips genai entirely (asserted via mock not-called). |

**Integration (+1 gated):** `test_find_nearest_emulator.py` — real Firestore emulator, 3 products seeded with checked-in precomputed embedding fixtures, asserts `find_nearest("widget red")` returns the correct top-1 with `score ≥ 0.70`. Gated `@pytest.mark.firestore_emulator`.

**Evalset:** no changes. Existing smoke cases already succeed via tier 1 / tier 2; embedding upgrade doesn't alter their outcomes (it only fills in where earlier tiers miss).

**Live-smoke:** `scripts/smoke_run.py` gains an optional `--verbose-embedding` flag that prints tier-3 candidates + scores when tier 1 / 2 miss. Purely observational.

**Total test delta:** +~10 unit, +1 integration. Pipeline test count moves ~323 → ~333 unit + 1 new integration test.

## Success criteria

1. Re-running `scripts/load_master_data.py` populates `description_embedding: Vector(768)` on all 35 product docs in the Firestore emulator. Re-run is idempotent (re-embeds + overwrites; no duplication).
2. `MasterDataRepo.find_product_by_embedding("dark roast coffee 5 lb bag")` (or any semantically clear query that tier 1/2 misses) returns a non-empty `list[EmbeddingMatch]` with descending `score`; the top candidate's `sku` is the expected product.
3. `sku_matcher.match_sku` on a line where tier 1 + tier 2 miss but the description is semantically clear returns `("embedding", score)` with `score ≥ 0.70`.
4. Simulated embedding API outage (raised exception in a fake genai client) causes `find_product_by_embedding` to return `[]` and the pipeline continues without raising.
5. CLI command for vector-index creation is documented in `backend/my_agent/README.md`; a manual run against live Firestore creates the index successfully (demo-time verification).
6. All existing pipeline tests green; no `sku_matcher` or validator test regresses.
7. Smoke evalset continues to pass — embeddings fill in on paths where tier 1 + 2 already succeeded (no-op change).
8. CLI `--no-embeddings` flag lets an offline seed run work without `GOOGLE_API_KEY` (tests + first-pass emulator).

## Out of scope (explicit)

- Per-product alias learning / `aliases` field on `ProductRecord` / re-embedding on human corrections — Glacis Learning Loop, separate track.
- Category-filtered hybrid search — not useful at 35 products / 3-4 categories; would require either a classifier call or heuristic inference in the caller.
- Tiered thresholds (0.90 auto / 0.70 suggest) — flat 0.70 kept per Decision 5.
- Fine-tuned embeddings on historical customer↔SKU pairs — no training corpus; Phase 3 work.
- Vertex AI Vector Search sidecar — only meaningful at 100K+ SKU scale.
- Multi-language embeddings / non-English catalog.
- Query embedding caching — 35 products + rare tier-3 misses don't justify it; cache invalidation is its own problem.
- Per-customer embedding personalization (different vectors per customer based on their ordering patterns) — interwoven with alias learning; separate track.
- Re-embedding on catalog updates outside of `load_master_data.py` — the seed script is idempotent; a full catalog refresh re-embeds everything.
- Dedicated vector DB (Pinecone, Weaviate, Qdrant) — unnecessary at this scale; Firestore flat KNN handles the catalog with room to spare.

## Connections

**Depends on.** Nothing in the current planned track set. Track E is orthogonal to A1/A2/A3/B/C/D. Can execute before or after any of them.

**Blocks.** Nothing. Leaf node.

**Parallel-compatible.** All other tracks. No pipeline topology changes; no stage additions; no schema bumps on `OrderRecord` or `ExceptionRecord`.

**Doc-flip targets.**
- `Glacis-Order-Intake.md` §5 "Tier 3 embedding search with text-embedding-004 + alias learning" — split bullet. Embedding portion flips `[Post-MVP]` → `[MVP ✓]` with full citation chain (commits, files). Alias-learning portion stays `[Post-MVP]` with note *"out of scope per Track E; part of future Learning Loop track"*. Phase 3 roadmap removes the embedding line.
- `research/Order-Intake-Sprint-Status.md` — flip row "2d. Enrichment (item matching)" tier-3 cell from "embedding stub falls through cleanly" to "text-embedding-004 + Firestore find_nearest live"; bump test count + append to Built inventory.

End of design.
