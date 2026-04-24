# Track E — Embedding Tier 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `MasterDataRepo.find_product_by_embedding` stub with a real `text-embedding-004` + Firestore `find_nearest` implementation, and extend `scripts/load_master_data.py` to batch-compute per-product embeddings at seed time. Closes the 3-tier SKU ladder: tier 1 (exact) + tier 2 (fuzzy) already work; tier 3 becomes a real semantic fallback instead of a `[]`-returning stub.

**Architecture:** The `EmbeddingMatch` Pydantic schema at `backend/models/master_records.py:135-146` is already stable. `sku_matcher.match_sku` already calls `find_product_by_embedding` at tier 3 with `EMBEDDING_THRESHOLD=0.70`. Swapping the stub in-place is a one-method surface-area change. The seed script adds `description_embedding: Vector(768)` to each product doc via `google.genai.Client().models.embed_content(task_type="RETRIEVAL_DOCUMENT")`. The repo wraps `google.genai.Client().aio.models.embed_content(task_type="RETRIEVAL_QUERY")` + `products_collection.find_nearest(distance_measure=COSINE, distance_result_field="__distance")` with fail-open error handling (log + return `[]`). Vector index creation is a documented `gcloud firestore indexes composite create` command; the Firestore emulator handles `find_nearest` natively without an explicit index. No Pydantic schema changes — `ProductRecord` uses `extra="allow"`.

**Tech Stack:** Python 3.13, `google-genai` (already transitive via `google-adk>=1.31.0` — no pyproject entry needed), `google-cloud-firestore>=2.27.0` (existing) for `Vector` + `DistanceMeasure` + `find_nearest`, pytest + pytest-asyncio, `AsyncMock` / `MagicMock` for genai + Firestore client mocking.

**Source spec:** `docs/superpowers/specs/2026-04-24-track-e-embedding-tier3-design.md` (rev `544c1d9`).

**Prerequisites:** None. Track E is orthogonal to A1/A2/A3/B/C/D — no pipeline topology changes, no schema bumps, no new top-level pyproject deps. Can execute in any order relative to other tracks.

**Environment note for Task 3 / Task 5 / Task 6:** The genai client constructed by `google.genai.Client()` reads `GOOGLE_API_KEY` or Application Default Credentials at construction time. Tests inject mock clients; only smoke runs + the seed script against real Gemini require the env var. The `--no-embeddings` CLI flag lets offline seed runs work without the key.

---

## File structure

| Path | Responsibility |
|---|---|
| **Modified** `backend/tools/order_validator/tools/master_data_repo.py` | Add `genai_client: Optional[GenAIClient] = None` kwarg to `__init__`; add `_ensure_genai_client` lazy ctor; add `_embed_query` async helper; replace `find_product_by_embedding` stub body with real impl (query→embed→`find_nearest`→similarity-clamped `EmbeddingMatch` list). |
| **Modified** `scripts/load_master_data.py` | Add `_embed_text_for_product` + `_embed_text` helpers; `load_products` gains `with_embeddings: bool = True` kwarg; `main()` gains `--no-embeddings` CLI flag (via `argparse.BooleanOptionalAction`); each product doc gains `description_embedding: Vector(embedding_list)` before the batch write when embeddings are enabled. |
| **Modified** `backend/my_agent/agent.py` | `_build_default_root_agent` constructs a shared `google.genai.Client()` + threads into `MasterDataRepo(client, genai_client=genai)`. `build_root_agent` signature unchanged. |
| **Modified** `backend/my_agent/README.md` | Add "Vector index setup" subsection with the `gcloud firestore indexes composite create` command + a note that the emulator auto-handles `find_nearest`. |
| **New** `tests/unit/test_embedding_matcher.py` | ~6 tests covering `_embed_query` + `find_product_by_embedding` with mocked genai + Firestore. Distance-to-similarity conversion, empty query, k<1, API error fail-open, `k` forwarding, malformed response. |
| **Modified** `tests/unit/test_master_data_repo.py` | Replace *"stub returns empty list"* test with: `genai_client` kwarg DI test + lazy construction test. |
| **Modified** `tests/unit/test_load_master_data.py` (create if missing) | +2 tests: `_embed_text_for_product` composes expected string; `load_products(with_embeddings=False)` skips genai call entirely. |
| **Modified** `tests/unit/test_sku_matcher.py` | Update tier-3 test to exercise a stubbed `find_product_by_embedding` returning a real `EmbeddingMatch` with score 0.85 — assert `match_sku` returns `(product, "embedding", 0.85)`. |
| **New** `tests/integration/test_find_nearest_emulator.py` | Gated emulator test: seed 3 products with precomputed embedding vectors (not real Gemini), mock `_embed_query` to return a fixed vector, assert `find_product_by_embedding` returns the expected top-1 with score ≥ 0.70. Gated `@pytest.mark.firestore_emulator`. |
| **Modified** `research/Order-Intake-Sprint-Status.md` | Flip row "2d. Enrichment (item matching)" tier 3 from "stub falls through cleanly" to "text-embedding-004 + find_nearest live"; bump test count; append to Built inventory; add session note to `last_updated` frontmatter. |
| **Modified** `Glacis-Order-Intake.md` | §5 "Tier 3 embedding search with text-embedding-004 + alias learning" — split bullet: flip embedding portion `[Post-MVP]` → `[MVP ✓]` with full citation chain; keep alias-learning portion `[Post-MVP]` with explicit *"out of scope per Track E"* note. Phase 3 roadmap removes the embedding line. `last_updated` frontmatter bumped. |

---

## Task 1: `_embed_text_for_product` helper in `scripts/load_master_data.py`

The simplest building block — a pure function that takes a product dict and returns the string to embed. Test-first, no I/O.

**Files:**
- Modify: `scripts/load_master_data.py`
- Create (or extend if exists): `tests/unit/test_load_master_data.py`

- [ ] **Step 1.1: Write the failing test**

Create `tests/unit/test_load_master_data.py` (or append if it already exists):

```python
"""Unit tests for scripts/load_master_data.py — Track E extension.

Focus: the pure helper functions. The full seed-script integration
(Firestore batch write + genai call) is covered by the emulator test
in tests/integration/.
"""

from __future__ import annotations

import pytest


def test_embed_text_for_product_composes_short_long_and_category_slash_subcategory():
    from scripts.load_master_data import _embed_text_for_product

    product = {
        "sku": "FST-HCS-050-13-200-G5Z",
        "short_description": "HCS 1/2-13 x 2 GR5 ZP",
        "long_description": (
            'Hex Head Cap Screw, 1/2"-13 UNC x 2" OAL, Steel Grade 5, '
            "Zinc Plated (Clear), Plain Washer Face"
        ),
        "category": "fasteners",
        "subcategory": "hex_cap_screws",
    }

    text = _embed_text_for_product(product)

    assert text == (
        "HCS 1/2-13 x 2 GR5 ZP. "
        'Hex Head Cap Screw, 1/2"-13 UNC x 2" OAL, Steel Grade 5, '
        "Zinc Plated (Clear), Plain Washer Face. "
        "Category: fasteners/hex_cap_screws."
    )


def test_embed_text_for_product_handles_missing_subcategory():
    from scripts.load_master_data import _embed_text_for_product

    product = {
        "sku": "SKU-1",
        "short_description": "Widget A",
        "long_description": "A generic widget.",
        "category": "widgets",
        # no subcategory
    }

    text = _embed_text_for_product(product)

    assert text == "Widget A. A generic widget. Category: widgets."


def test_embed_text_for_product_handles_empty_subcategory():
    from scripts.load_master_data import _embed_text_for_product

    product = {
        "sku": "SKU-1",
        "short_description": "Widget A",
        "long_description": "A generic widget.",
        "category": "widgets",
        "subcategory": "",
    }

    text = _embed_text_for_product(product)

    assert text == "Widget A. A generic widget. Category: widgets."
```

- [ ] **Step 1.2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_load_master_data.py -v`

Expected: all three tests fail with `ImportError: cannot import name '_embed_text_for_product' from 'scripts.load_master_data'`.

- [ ] **Step 1.3: Add the helper**

In `scripts/load_master_data.py`, append (near the top, after existing module constants and before `def _client()`):

```python
def _embed_text_for_product(p: dict) -> str:
    """Compose the text string fed to text-embedding-004 for one catalog item.

    Includes short_description (customer-shorthand form), long_description
    (canonical/detailed form), and category (+ subcategory if present).
    The embedding model uses the combined context to map customer
    shorthand onto canonical SKUs.
    """
    short = p["short_description"]
    long_ = p["long_description"]
    cat = p.get("category", "")
    sub = p.get("subcategory", "")
    suffix = f"{cat}/{sub}" if sub else cat
    return f"{short}. {long_}. Category: {suffix}."
```

- [ ] **Step 1.4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_load_master_data.py -v`

Expected: 3 passed.

- [ ] **Step 1.5: Commit**

```bash
git add scripts/load_master_data.py tests/unit/test_load_master_data.py
git commit -m "feat(seed): add _embed_text_for_product helper

Pure string-composition helper used at seed time to produce the text
fed to text-embedding-004 for each catalog item. Shape:

  {short_description}. {long_description}. Category: {cat}/{sub}.

Subcategory is optional — omitted if missing or empty, collapses to
'Category: {cat}.' alone. Three unit tests pin the formatting.

Track E plan Task 1."
```

---

## Task 2: `_embed_text` sync wrapper + `--no-embeddings` CLI flag

Add the actual embedding call (sync path, used only at seed time) and the `with_embeddings` switch on `load_products`. Mock the genai client in tests.

**Files:**
- Modify: `scripts/load_master_data.py`
- Modify: `tests/unit/test_load_master_data.py`

- [ ] **Step 2.1: Write the failing tests**

Append to `tests/unit/test_load_master_data.py`:

```python
# ---------- Track E: genai embedding call + --no-embeddings flag ----------

from unittest.mock import MagicMock, patch


def _fake_genai_with_fixed_vector(vector: list[float]) -> MagicMock:
    """Build a MagicMock that impersonates google.genai.Client well enough
    for embed_content(...).embeddings[0].values to return `vector`."""
    client = MagicMock()
    response = MagicMock()
    embedding_obj = MagicMock()
    embedding_obj.values = vector
    response.embeddings = [embedding_obj]
    client.models.embed_content = MagicMock(return_value=response)
    return client


def test_embed_text_runs_embed_content_with_retrieval_document_task_type():
    from scripts.load_master_data import EMBED_DIM, EMBED_MODEL, _embed_text

    fake = _fake_genai_with_fixed_vector([0.1] * EMBED_DIM)

    result = _embed_text(fake, "hello world")

    assert result == [0.1] * EMBED_DIM

    # Exactly one call with the right model + task type + dimensionality.
    assert fake.models.embed_content.call_count == 1
    kwargs = fake.models.embed_content.call_args.kwargs
    assert kwargs["model"] == EMBED_MODEL == "text-embedding-004"
    assert kwargs["contents"] == ["hello world"]
    config = kwargs["config"]
    assert config.task_type == "RETRIEVAL_DOCUMENT"
    assert config.output_dimensionality == EMBED_DIM == 768


def test_load_products_with_embeddings_false_skips_genai_entirely(tmp_path):
    """Seed with --no-embeddings should NOT construct a genai.Client nor
    call embed_content, so the script works offline / without GOOGLE_API_KEY."""
    from scripts import load_master_data

    # Mock db.batch()
    fake_db = MagicMock()
    fake_batch = MagicMock()
    fake_db.batch.return_value = fake_batch

    # Patch genai.Client at the module level. It should never be called.
    with patch.object(load_master_data, "GenAIClient") as genai_ctor:
        count = load_master_data.load_products(fake_db, with_embeddings=False)

    genai_ctor.assert_not_called()
    # batch.set was called once per product from the real products.json.
    assert fake_batch.set.call_count == count
    # No description_embedding key in any of the written docs.
    for call in fake_batch.set.call_args_list:
        _ref, doc = call.args
        assert "description_embedding" not in doc


def test_load_products_with_embeddings_true_calls_embed_once_per_product(tmp_path):
    """Each product triggers one embed_content call + the resulting
    vector is wrapped in Vector() on the written doc."""
    from google.cloud.firestore_v1.vector import Vector

    from scripts import load_master_data

    fake_db = MagicMock()
    fake_batch = MagicMock()
    fake_db.batch.return_value = fake_batch

    fake_vector = [0.25] * load_master_data.EMBED_DIM
    fake_genai = _fake_genai_with_fixed_vector(fake_vector)

    with patch.object(load_master_data, "GenAIClient", return_value=fake_genai):
        count = load_master_data.load_products(fake_db, with_embeddings=True)

    assert fake_genai.models.embed_content.call_count == count
    for call in fake_batch.set.call_args_list:
        _ref, doc = call.args
        assert "description_embedding" in doc
        assert isinstance(doc["description_embedding"], Vector)
```

- [ ] **Step 2.2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_load_master_data.py -v`

Expected: three new tests fail with `ImportError` on `_embed_text` / `EMBED_DIM` / `EMBED_MODEL` / `GenAIClient`.

- [ ] **Step 2.3: Add imports + constants + `_embed_text` + `with_embeddings` kwarg**

In `scripts/load_master_data.py`, add near the top of the file (after existing imports):

```python
import argparse
from typing import Optional

from google.cloud.firestore_v1.vector import Vector
from google.genai import Client as GenAIClient
from google.genai.types import EmbedContentConfig

EMBED_MODEL = "text-embedding-004"
EMBED_DIM = 768
```

Then add the sync embedding helper (below `_embed_text_for_product`):

```python
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
```

Replace the existing `load_products` function:

```python
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
```

- [ ] **Step 2.4: Update `main()` to accept the CLI flag**

Replace the existing `main()` at the bottom of `scripts/load_master_data.py`:

```python
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
    print(f"Seeding master data to: {target}")
    db = _client()
    n_products  = load_products(db, with_embeddings=args.embeddings)
    n_customers = load_customers(db)
    load_meta(db)
    emb_note = "with embeddings" if args.embeddings else "NO embeddings (offline mode)"
    print(f"  products:  {n_products} ({emb_note})")
    print(f"  customers: {n_customers}")
    print("  meta:      master_data")
```

*(Adjust minor details — existing imports of `os`, the `target` / `_client()` print shape — to match whatever the current file has. The above assumes the existing structure from this spec's Context read.)*

- [ ] **Step 2.5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_load_master_data.py -v`

Expected: 6 passed (3 from Task 1 + 3 new from Task 2).

- [ ] **Step 2.6: Commit**

```bash
git add scripts/load_master_data.py tests/unit/test_load_master_data.py
git commit -m "feat(seed): add embedding computation + --no-embeddings CLI flag

Per-product text-embedding-004 call via google.genai.Client
(RETRIEVAL_DOCUMENT task_type, 768 dim). Each product doc gains
description_embedding: Vector(768) before the batch write.

CLI: --embeddings (default) / --no-embeddings. Offline seed runs
(e.g. against the Firestore emulator without GOOGLE_API_KEY) can
use --no-embeddings to skip the genai call entirely. Three unit
tests cover: embed_content config shape (RETRIEVAL_DOCUMENT + 768);
--no-embeddings skips GenAIClient construction + never writes
description_embedding; with_embeddings=True calls embed_content
once per product + wraps the result in Vector().

Track E plan Task 2."
```

---

## Task 3: Live seed-run smoke check (manual)

One-shot manual verification that the seed script works end-to-end against the Firestore emulator. Not a pytest — a sanity gate before wiring the read path.

**Files:** none modified in this task — just running the script + visually inspecting output.

- [ ] **Step 3.1: Start the Firestore emulator (if not already running)**

Run in a separate terminal (or verify it's already up):

```bash
firebase emulators:start --only firestore
```

Leave it running. The script auto-detects `FIRESTORE_EMULATOR_HOST` if the emulator is listening on the default port.

- [ ] **Step 3.2: Run the seed script with embeddings enabled**

Run from the repo root (requires `GOOGLE_API_KEY` set):

```bash
uv run python scripts/load_master_data.py
```

Expected stdout:
```
Seeding master data to: <emulator host>
  products:  35 (with embeddings)
  customers: 10
  meta:      master_data
```

If you see an error like `google.genai.errors.ClientError: 401 API_KEY_INVALID`, the script correctly attempted embedding — export a valid `GOOGLE_API_KEY` and retry.

- [ ] **Step 3.3: Verify embeddings landed on a product doc**

Run (in a Python REPL or a quick one-off script):

```bash
uv run python -c "
import asyncio, os
from google.cloud.firestore import AsyncClient
os.environ.setdefault('FIRESTORE_EMULATOR_HOST', 'localhost:8080')
os.environ.setdefault('GOOGLE_CLOUD_PROJECT', 'demo-order-intake-local')

async def main():
    c = AsyncClient()
    snap = await c.collection('products').document('FST-HCS-050-13-200-G5Z').get()
    data = snap.to_dict()
    emb = data.get('description_embedding')
    print('has embedding:', emb is not None)
    print('embedding type:', type(emb).__name__)
    print('first 3 dims:', list(emb)[:3] if emb else '—')
    await c.close()

asyncio.run(main())
"
```

Expected:
```
has embedding: True
embedding type: Vector
first 3 dims: [<three floats>]
```

- [ ] **Step 3.4: Verify `--no-embeddings` still works**

Run:

```bash
uv run python scripts/load_master_data.py --no-embeddings
```

Expected stdout:
```
Seeding master data to: <emulator host>
  products:  35 (NO embeddings (offline mode))
  customers: 10
  meta:      master_data
```

Re-run the verification from Step 3.3 — `has embedding` should now be `False` (or the key absent) because `--no-embeddings` overwrote the docs without the vector field.

*(After this task, re-run the seed WITH embeddings enabled to restore state for downstream tasks: `uv run python scripts/load_master_data.py`.)*

- [ ] **Step 3.5: No commit for this task**

This task is a manual verification. Nothing to add to git. Mark complete once the REPL output confirms embeddings round-trip through the emulator.

---

## Task 4: `MasterDataRepo._embed_query` async helper + `genai_client` kwarg

Now the query side. Add the optional dep-injected genai client + lazy constructor + the private `_embed_query` coroutine. Fail-open: any exception → `None` return + warning log (caller treats as tier-3 miss).

**Files:**
- Modify: `backend/tools/order_validator/tools/master_data_repo.py`
- Create: `tests/unit/test_embedding_matcher.py`

- [ ] **Step 4.1: Write the failing tests**

Create `tests/unit/test_embedding_matcher.py`:

```python
"""Unit tests for MasterDataRepo's embedding + find_nearest surface
(Track E). Covers _embed_query (async) + find_product_by_embedding
(the full query path, Firestore mocked).

All tests mock the google.genai client and the AsyncClient.collection
chain. The emulator round-trip lives in tests/integration/.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def _fake_async_genai_with_fixed_vector(vector: list[float]) -> MagicMock:
    """Impersonate google.genai.Client well enough for
    client.aio.models.embed_content(...) to await into a response whose
    .embeddings[0].values is `vector`."""
    client = MagicMock()
    response = MagicMock()
    embedding_obj = MagicMock()
    embedding_obj.values = vector
    response.embeddings = [embedding_obj]
    client.aio.models.embed_content = AsyncMock(return_value=response)
    return client


def _fake_async_genai_that_raises(exc: Exception) -> MagicMock:
    client = MagicMock()
    client.aio.models.embed_content = AsyncMock(side_effect=exc)
    return client


@pytest.mark.asyncio
async def test_embed_query_returns_768_dim_vector_on_happy_path():
    from backend.tools.order_validator.tools.master_data_repo import (
        EMBED_DIM,
        EMBED_MODEL,
        MasterDataRepo,
    )

    fake_genai = _fake_async_genai_with_fixed_vector([0.1] * EMBED_DIM)
    repo = MasterDataRepo(client=MagicMock(), genai_client=fake_genai)

    result = await repo._embed_query("dark roast 5 lb bag")

    assert result == [0.1] * EMBED_DIM

    kwargs = fake_genai.aio.models.embed_content.call_args.kwargs
    assert kwargs["model"] == EMBED_MODEL == "text-embedding-004"
    assert kwargs["contents"] == ["dark roast 5 lb bag"]
    config = kwargs["config"]
    assert config.task_type == "RETRIEVAL_QUERY"
    assert config.output_dimensionality == EMBED_DIM == 768


@pytest.mark.asyncio
async def test_embed_query_returns_none_on_api_exception():
    """Fail-open: exception from embed_content -> logs a warning and
    returns None. The caller treats that as a tier-3 miss."""
    from backend.tools.order_validator.tools.master_data_repo import MasterDataRepo

    fake_genai = _fake_async_genai_that_raises(RuntimeError("simulated outage"))
    repo = MasterDataRepo(client=MagicMock(), genai_client=fake_genai)

    result = await repo._embed_query("widget red")

    assert result is None


@pytest.mark.asyncio
async def test_embed_query_returns_none_on_malformed_response():
    """If the response object lacks .embeddings or the list is empty,
    we don't crash — we fail-open."""
    from backend.tools.order_validator.tools.master_data_repo import MasterDataRepo

    client = MagicMock()
    bad_response = MagicMock()
    bad_response.embeddings = []   # empty -> IndexError when we access [0]
    client.aio.models.embed_content = AsyncMock(return_value=bad_response)

    repo = MasterDataRepo(client=MagicMock(), genai_client=client)

    result = await repo._embed_query("anything")

    assert result is None


def test_genai_client_is_lazily_constructed_when_not_injected():
    """Constructing MasterDataRepo without genai_client should NOT call
    google.genai.Client() eagerly — only on first embedding call."""
    from unittest.mock import patch

    from backend.tools.order_validator.tools import master_data_repo

    with patch.object(master_data_repo, "GenAIClient") as ctor:
        repo = master_data_repo.MasterDataRepo(client=MagicMock())
        ctor.assert_not_called()

        # First embedding call triggers construction:
        client = repo._ensure_genai_client()
        ctor.assert_called_once()

        # Second call reuses the same instance (no re-construction):
        again = repo._ensure_genai_client()
        assert again is client
        ctor.assert_called_once()
```

- [ ] **Step 4.2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_embedding_matcher.py -v`

Expected: 4 failures with `ImportError` on `EMBED_DIM` / `EMBED_MODEL` / `GenAIClient` / `genai_client` kwarg / `_embed_query` / `_ensure_genai_client`.

- [ ] **Step 4.3: Add imports + constants + `genai_client` kwarg + `_ensure_genai_client` + `_embed_query`**

Edit `backend/tools/order_validator/tools/master_data_repo.py`:

Add to the imports block at the top:

```python
from typing import Optional    # already present; verify

from google.genai import Client as GenAIClient
from google.genai.types import EmbedContentConfig
```

Add to the module-level constants (near `DEFAULT_EMBEDDING_TOP_K = 5`):

```python
EMBED_MODEL = "text-embedding-004"
EMBED_DIM = 768
```

Replace `MasterDataRepo.__init__` — change the signature to take an optional `genai_client` kwarg:

```python
def __init__(
    self,
    client: AsyncClient,
    *,
    genai_client: Optional[GenAIClient] = None,
) -> None:
    self._client = client
    self._products_cache: Optional[list[ProductRecord]] = None
    self._customers_cache: Optional[list[CustomerRecord]] = None
    self._genai_client = genai_client
```

Add two new private methods (after the existing `_list_all_customers` and before `find_product_by_embedding`):

```python
def _ensure_genai_client(self) -> GenAIClient:
    """Lazy-construct a google-genai client the first time it's needed.

    The client reads GOOGLE_API_KEY / ADC at construction time, so we
    defer it: a MasterDataRepo used only for tier-1/2 lookups never
    triggers the credential read at all.
    """
    if self._genai_client is None:
        self._genai_client = GenAIClient()
    return self._genai_client


async def _embed_query(self, text: str) -> Optional[list[float]]:
    """Embed a customer-side query string via text-embedding-004.

    Returns the 768-dim float vector on success, ``None`` on any
    exception (fail-open; the caller treats ``None`` as a tier-3 miss
    and the validator's aggregate scoring handles the routing).
    """
    try:
        client = self._ensure_genai_client()
        response = await client.aio.models.embed_content(
            model=EMBED_MODEL,
            contents=[text],
            config=EmbedContentConfig(
                task_type="RETRIEVAL_QUERY",
                output_dimensionality=EMBED_DIM,
            ),
        )
        return list(response.embeddings[0].values)
    except Exception as exc:    # noqa: BLE001 — fail-open by design
        _log.warning("embedding_query_failed", error=str(exc), text=text[:80])
        return None
```

- [ ] **Step 4.4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_embedding_matcher.py -v`

Expected: 4 passed.

- [ ] **Step 4.5: Run the full master_data_repo test suite to check for regressions**

Run: `uv run pytest tests/unit/test_master_data_repo.py -v`

Expected: all existing tests green. If any test constructed `MasterDataRepo(client)` as a positional arg expecting a trailing positional dep, review it — our new kwarg is keyword-only and should not affect positional calls.

- [ ] **Step 4.6: Commit**

```bash
git add backend/tools/order_validator/tools/master_data_repo.py tests/unit/test_embedding_matcher.py
git commit -m "feat(repo): add _embed_query + genai_client DI to MasterDataRepo

Adds optional genai_client kwarg to MasterDataRepo.__init__ with
lazy constructor (_ensure_genai_client). First use triggers
google.genai.Client() construction; subsequent calls reuse.

_embed_query is the async query-side embedding helper: calls
client.aio.models.embed_content with text-embedding-004 +
RETRIEVAL_QUERY task_type + 768 dimensionality. Fail-open: any
exception logs 'embedding_query_failed' warning and returns None
so find_product_by_embedding (Task 5) can cleanly short-circuit
to [] on transient Gemini outages.

Four new unit tests: happy-path vector + config shape;
exception -> None; malformed response (empty embeddings list)
-> None; lazy genai ctor.

Track E plan Task 4."
```

---

## Task 5: Replace `find_product_by_embedding` stub with real impl

The main event. Stub → real implementation. Wires `_embed_query` through to `Firestore.find_nearest(distance_measure=COSINE)`, converts distance → similarity, returns sorted `EmbeddingMatch` list.

**Files:**
- Modify: `backend/tools/order_validator/tools/master_data_repo.py`
- Modify: `tests/unit/test_embedding_matcher.py`

- [ ] **Step 5.1: Write the failing tests**

Append to `tests/unit/test_embedding_matcher.py`:

```python
# ---------- find_product_by_embedding (real impl) ----------


def _fake_vector_query_with_docs(docs: list[tuple[str, float]]) -> MagicMock:
    """Make an async stream() that yields (sku, distance) tuples as
    Firestore-style snapshots. `distance` is the cosine distance
    (0..2); the repo converts to similarity = 1 - d/2.
    """
    class _FakeSnap:
        def __init__(self, sku: str, distance: float) -> None:
            self.id = sku
            self._data = {"__distance": distance}

        def to_dict(self) -> dict:
            return dict(self._data)

    class _AsyncStream:
        def __init__(self, snaps):
            self._snaps = list(snaps)

        def __aiter__(self):
            self._i = 0
            return self

        async def __anext__(self):
            if self._i >= len(self._snaps):
                raise StopAsyncIteration
            snap = self._snaps[self._i]
            self._i += 1
            return snap

    snaps = [_FakeSnap(sku, d) for sku, d in docs]

    vector_query = MagicMock()
    vector_query.stream = MagicMock(return_value=_AsyncStream(snaps))
    return vector_query


def _fake_firestore_client_returning(vector_query: MagicMock) -> MagicMock:
    """Build a MagicMock AsyncClient chain:
      client.collection(PRODUCTS_COLLECTION).find_nearest(...) -> vector_query
    """
    client = MagicMock()
    collection = MagicMock()
    client.collection = MagicMock(return_value=collection)
    collection.find_nearest = MagicMock(return_value=vector_query)
    return client


@pytest.mark.asyncio
async def test_find_product_by_embedding_returns_sorted_matches_with_similarity_conversion():
    """Distance 0 -> similarity 1.0; distance 1 -> 0.5; distance 2 -> 0.0.
    The returned EmbeddingMatch list preserves the order from Firestore
    (which is ascending distance == descending similarity)."""
    from backend.tools.order_validator.tools.master_data_repo import (
        EMBED_DIM,
        MasterDataRepo,
    )

    fake_genai = _fake_async_genai_with_fixed_vector([0.1] * EMBED_DIM)
    vector_query = _fake_vector_query_with_docs([
        ("SKU-A", 0.2),    # distance 0.2 -> similarity 0.9
        ("SKU-B", 1.0),    # distance 1.0 -> similarity 0.5
        ("SKU-C", 1.8),    # distance 1.8 -> similarity 0.1
    ])
    fake_client = _fake_firestore_client_returning(vector_query)

    repo = MasterDataRepo(client=fake_client, genai_client=fake_genai)
    matches = await repo.find_product_by_embedding("dark roast 5 lb bag")

    assert [m.sku for m in matches] == ["SKU-A", "SKU-B", "SKU-C"]
    assert matches[0].score == pytest.approx(0.9)
    assert matches[1].score == pytest.approx(0.5)
    assert matches[2].score == pytest.approx(0.1)
    assert all(m.source == "firestore_findnearest" for m in matches)


@pytest.mark.asyncio
async def test_find_product_by_embedding_forwards_k_to_find_nearest_limit():
    from backend.tools.order_validator.tools.master_data_repo import (
        EMBED_DIM,
        MasterDataRepo,
    )

    fake_genai = _fake_async_genai_with_fixed_vector([0.1] * EMBED_DIM)
    vector_query = _fake_vector_query_with_docs([])
    fake_client = _fake_firestore_client_returning(vector_query)

    repo = MasterDataRepo(client=fake_client, genai_client=fake_genai)
    await repo.find_product_by_embedding("anything", k=3)

    collection = fake_client.collection.return_value
    kwargs = collection.find_nearest.call_args.kwargs
    assert kwargs["limit"] == 3
    # Distance field must match what to_dict() reads back.
    assert kwargs["distance_result_field"] == "__distance"


@pytest.mark.asyncio
async def test_find_product_by_embedding_returns_empty_for_empty_query():
    from backend.tools.order_validator.tools.master_data_repo import MasterDataRepo

    fake_genai = MagicMock()  # never called
    repo = MasterDataRepo(client=MagicMock(), genai_client=fake_genai)

    assert await repo.find_product_by_embedding("")        == []
    assert await repo.find_product_by_embedding("   ")     == []
    assert await repo.find_product_by_embedding("foo", k=0) == []

    # No genai call for degenerate inputs.
    fake_genai.aio.models.embed_content.assert_not_called() \
        if hasattr(fake_genai.aio.models.embed_content, 'assert_not_called') else None


@pytest.mark.asyncio
async def test_find_product_by_embedding_returns_empty_when_embed_query_fails():
    """_embed_query returning None (fail-open) must short-circuit —
    no Firestore call, empty list."""
    from backend.tools.order_validator.tools.master_data_repo import MasterDataRepo

    fake_genai = _fake_async_genai_that_raises(RuntimeError("outage"))
    fake_client = MagicMock()
    repo = MasterDataRepo(client=fake_client, genai_client=fake_genai)

    matches = await repo.find_product_by_embedding("widget red")

    assert matches == []
    fake_client.collection.assert_not_called()


@pytest.mark.asyncio
async def test_find_product_by_embedding_clamps_out_of_range_distances():
    """If Firestore returns a weirdly-large distance (>2.0 — shouldn't
    happen for cosine but the SDK doesn't guarantee it), similarity
    clamps to 0.0 rather than going negative."""
    from backend.tools.order_validator.tools.master_data_repo import (
        EMBED_DIM,
        MasterDataRepo,
    )

    fake_genai = _fake_async_genai_with_fixed_vector([0.1] * EMBED_DIM)
    vector_query = _fake_vector_query_with_docs([
        ("SKU-X", 3.5),    # out of range
    ])
    fake_client = _fake_firestore_client_returning(vector_query)

    repo = MasterDataRepo(client=fake_client, genai_client=fake_genai)
    matches = await repo.find_product_by_embedding("anything")

    assert len(matches) == 1
    assert matches[0].score == 0.0
```

- [ ] **Step 5.2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_embedding_matcher.py -v`

Expected: the 5 new tests fail because the stub still returns `[]`.

- [ ] **Step 5.3: Replace the stub body**

In `backend/tools/order_validator/tools/master_data_repo.py`, add to the imports:

```python
from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
from google.cloud.firestore_v1.vector import Vector
```

Replace the entire `find_product_by_embedding` method body:

```python
async def find_product_by_embedding(
    self,
    query: str,
    k: int = DEFAULT_EMBEDDING_TOP_K,
) -> list[EmbeddingMatch]:
    """Layer-2 semantic SKU match: embed the customer query, run
    ``find_nearest`` against the ``description_embedding`` field, and
    return similarity-scored ``EmbeddingMatch`` candidates in
    descending-score order.

    Fail-open contract:

    * Degenerate input (empty query, whitespace only, ``k < 1``) →
      ``[]`` with no API call.
    * Embedding API exception → ``[]`` (logged via ``_embed_query``).
    * Firestore ``find_nearest`` exception → bubbles (caller is
      responsible; this is an infrastructure failure worth surfacing).

    Scores are in ``[0.0, 1.0]`` via ``similarity = 1 - cosine_distance / 2``,
    clamped. Caller (``sku_matcher``) compares ``matches[0].score``
    against ``EMBEDDING_THRESHOLD``.
    """
    if not query or not query.strip() or k < 1:
        return []

    query_vec = await self._embed_query(query)
    if query_vec is None:
        return []

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

Remove the stub's old body (the `_log.debug("layer2_stub_called", ...)` + `return []`) — fully replaced by the code above.

- [ ] **Step 5.4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_embedding_matcher.py -v`

Expected: all 9 tests green (4 from Task 4 + 5 from Task 5).

- [ ] **Step 5.5: Run adjacent suites to catch regressions**

Run: `uv run pytest tests/unit/test_master_data_repo.py tests/unit/test_sku_matcher.py -v`

Expected: all existing tests still pass. If `test_master_data_repo.py` has a *"stub returns empty list"* test, it may now be redundant — flag for Task 7's cleanup. If `test_sku_matcher.py` has tests that stub `find_product_by_embedding` returning `[]` (normal), they should still pass.

- [ ] **Step 5.6: Commit**

```bash
git add backend/tools/order_validator/tools/master_data_repo.py tests/unit/test_embedding_matcher.py
git commit -m "feat(repo): replace find_product_by_embedding stub with real impl

Real tier-3 implementation: await _embed_query for the customer text,
Firestore AsyncClient.collection(products).find_nearest(...) with
COSINE distance + 'description_embedding' vector field + explicit
distance_result_field='__distance'. Converts cosine distance
d in [0, 2] to similarity 1 - d/2, clamped to [0.0, 1.0].

Short-circuits to [] for: empty/whitespace query, k < 1,
_embed_query returning None (fail-open on embedding errors).
Firestore exceptions bubble — infrastructure failure worth
surfacing.

Five new unit tests cover distance-similarity conversion (d=0.2 ->
0.9, d=1.0 -> 0.5, d=1.8 -> 0.1); k kwarg forwarding to
find_nearest.limit + distance_result_field contract; degenerate
inputs return [] with no API call; embed failure -> [] without
Firestore call; out-of-range distance clamp to 0.0.

Track E plan Task 5."
```

---

## Task 6: Update `test_sku_matcher` tier-3 test

The matcher's tier-3 code path was already written against the stub. Now it has a real backing — extend the test to exercise a non-empty `EmbeddingMatch` list.

**Files:**
- Modify: `tests/unit/test_sku_matcher.py`

- [ ] **Step 6.1: Inspect the current tier-3 test**

Run: `uv run pytest tests/unit/test_sku_matcher.py -v` and find the test that exercises tier 3 (likely named `test_match_sku_tier_3_*` or `test_match_sku_falls_through_on_embedding_miss`).

The current test almost certainly stubs `MasterDataRepo.find_product_by_embedding` to return `[]` and asserts `match_sku` returns `(None, "none", 0.0)`. Keep that test as-is.

- [ ] **Step 6.2: Add a new test for the tier-3 hit path**

Append to `tests/unit/test_sku_matcher.py`:

```python
# ---------- Track E: tier-3 real match ----------

@pytest.mark.asyncio
async def test_match_sku_tier_3_hit_returns_embedding_tier_with_score():
    """When tier 1 (exact) and tier 2 (fuzzy) miss, but tier 3 returns
    an EmbeddingMatch with score >= EMBEDDING_THRESHOLD, match_sku
    returns (product, 'embedding', score)."""
    from backend.models.master_records import EmbeddingMatch, ProductRecord
    from backend.models.parsed_document import OrderLineItem
    from backend.tools.order_validator.tools.sku_matcher import (
        EMBEDDING_THRESHOLD,
        match_sku,
    )

    matched_product = ProductRecord(
        sku="WID-RED-100",
        short_description="Widget Red 100ct",
        long_description="Red widgets, pack of 100.",
        category="widgets",
        subcategory="colored",
        uom="EA",
        pack_uom="BX",
        pack_size=100,
        alt_uoms=["BX"],
        unit_price_usd=4.20,
        standards=[],
        lead_time_days=1,
        min_order_qty=1,
        country_of_origin="US",
    )

    # Tier 1 misses (line.sku is None) + tier 2 misses (description
    # doesn't fuzzy-match any short_description). Tier 3 returns a
    # match with score 0.85 > EMBEDDING_THRESHOLD (0.70).
    repo = MagicMock()
    repo.get_product = AsyncMock(return_value=None)       # tier 1 miss on line.sku=None path
    repo.list_all_products = AsyncMock(return_value=[])   # tier 2 empty pool -> miss

    async def fake_find(query: str, k: int = 5):
        return [EmbeddingMatch(
            sku="WID-RED-100",
            score=0.85,
            source="firestore_findnearest",
        )]
    repo.find_product_by_embedding = fake_find

    # get_product is called AGAIN by tier 3 to hydrate the top match
    # into a ProductRecord. Reconfigure the mock to return the product
    # on the second call.
    repo.get_product = AsyncMock(side_effect=[None, matched_product])

    line = OrderLineItem(
        sku=None,
        description="widget red, case of 100",
        quantity=5,
        unit_of_measure="EA",
    )

    product, tier, score = await match_sku(line, repo, customer=None)

    assert product == matched_product
    assert tier == "embedding"
    assert score == pytest.approx(0.85)
    assert score >= EMBEDDING_THRESHOLD


@pytest.mark.asyncio
async def test_match_sku_tier_3_below_threshold_misses():
    """Score < EMBEDDING_THRESHOLD falls through to the overall miss
    branch, not an 'embedding' hit."""
    from backend.models.master_records import EmbeddingMatch
    from backend.models.parsed_document import OrderLineItem
    from backend.tools.order_validator.tools.sku_matcher import match_sku

    repo = MagicMock()
    repo.get_product = AsyncMock(return_value=None)
    repo.list_all_products = AsyncMock(return_value=[])

    async def fake_find(query: str, k: int = 5):
        return [EmbeddingMatch(
            sku="SKU-MAYBE",
            score=0.65,    # below 0.70
            source="firestore_findnearest",
        )]
    repo.find_product_by_embedding = fake_find

    line = OrderLineItem(
        sku=None,
        description="some unclear description",
        quantity=1,
        unit_of_measure="EA",
    )

    product, tier, score = await match_sku(line, repo, customer=None)

    assert product is None
    assert tier == "none"
    assert score == 0.0
```

Add the imports at the top of the test file if not already present:

```python
from unittest.mock import AsyncMock, MagicMock

import pytest
```

- [ ] **Step 6.3: Run tests to verify new tier-3 tests pass + regression tests still green**

Run: `uv run pytest tests/unit/test_sku_matcher.py -v`

Expected: pre-existing tier-3-miss tests still pass; both new tests green.

- [ ] **Step 6.4: Commit**

```bash
git add tests/unit/test_sku_matcher.py
git commit -m "test(sku_matcher): exercise tier-3 real EmbeddingMatch path

Adds two tests to complement the existing tier-3-miss test:
- tier-3 hit with score 0.85 (>= EMBEDDING_THRESHOLD 0.70) returns
  (product, 'embedding', 0.85). Verifies the matcher calls
  repo.get_product again to hydrate the top sku into a ProductRecord.
- tier-3 score 0.65 (below threshold) falls through to (None, 'none',
  0.0), matching the overall-miss path.

Tests stub MasterDataRepo.find_product_by_embedding with AsyncMock so
they don't depend on live Gemini or Firestore.

Track E plan Task 6."
```

---

## Task 7: Update `test_master_data_repo` — replace stub-era test

The old *"stub returns empty list"* test in `test_master_data_repo.py` is now stale — the method no longer unconditionally returns `[]`. Replace with two new tests that pin the DI contract.

**Files:**
- Modify: `tests/unit/test_master_data_repo.py`

- [ ] **Step 7.1: Find the old stub test**

Search the file for the pre-existing test:

Run: `uv run pytest tests/unit/test_master_data_repo.py -v -k embedding`

Expected to find: a test named something like `test_find_product_by_embedding_returns_empty_stub` or `test_layer2_stub_returns_empty_list`. Delete it — `tests/unit/test_embedding_matcher.py` (Task 5) covers the new behavior comprehensively.

- [ ] **Step 7.2: Replace with DI-contract tests**

In place of the deleted stub test, append:

```python
# ---------- Track E: genai_client DI + construction contract ----------


def test_master_data_repo_accepts_optional_genai_client_kwarg():
    from backend.tools.order_validator.tools.master_data_repo import MasterDataRepo

    stub_genai = MagicMock()
    repo = MasterDataRepo(client=MagicMock(), genai_client=stub_genai)
    assert repo._genai_client is stub_genai


def test_master_data_repo_constructs_without_genai_client():
    """Backward compatibility: pre-Track-E call sites that do not
    pass genai_client must continue to work. The client is lazily
    created on first embedding call, never during __init__."""
    from unittest.mock import patch

    from backend.tools.order_validator.tools import master_data_repo

    with patch.object(master_data_repo, "GenAIClient") as ctor:
        repo = master_data_repo.MasterDataRepo(client=MagicMock())
        ctor.assert_not_called()
        assert repo._genai_client is None
```

Add the imports at the top if missing:

```python
from unittest.mock import MagicMock
```

- [ ] **Step 7.3: Run the suite to verify the stub test is gone + new tests pass**

Run: `uv run pytest tests/unit/test_master_data_repo.py -v`

Expected: all tests green; no reference to a deleted stub test name remains.

- [ ] **Step 7.4: Commit**

```bash
git add tests/unit/test_master_data_repo.py
git commit -m "test(repo): replace stub-era embedding test with DI contract tests

Drops the old 'find_product_by_embedding stub returns []' test
(covered comprehensively by tests/unit/test_embedding_matcher.py as
of Task 5). Replaces with two small tests that pin the DI contract:
- Passing genai_client= stores the instance on _genai_client.
- Omitting genai_client does NOT trigger google.genai.Client()
  construction during __init__ (lazy only on first embedding call).

Guards against regressions where a future refactor might
inadvertently eager-construct the genai client and break
MasterDataRepo instances used only for tier-1/2 lookups.

Track E plan Task 7."
```

---

## Task 8: Wire `genai_client` through `_build_default_root_agent`

Thread a single `google.genai.Client()` into the `MasterDataRepo` constructed by the default root-agent factory so the pipeline-wide instance is shared.

**Files:**
- Modify: `backend/my_agent/agent.py`
- Modify: `tests/unit/test_orchestrator_build.py` (minor — ensure no test fails on the new kwarg path)

- [ ] **Step 8.1: Inspect the current `_build_default_root_agent`**

Run: `uv run python -c "import inspect; from backend.my_agent import agent; print(inspect.getsource(agent._build_default_root_agent))" | head -30`

Find the line that constructs `MasterDataRepo(client)`. It will look something like:

```python
master_data_repo = MasterDataRepo(firestore_client)
```

- [ ] **Step 8.2: Write a test (optional — skip if test_orchestrator_build already covers the build path comprehensively)**

In `tests/unit/test_orchestrator_build.py`, add a light-touch test that `_build_default_root_agent` constructs without exploding. If the existing suite already verifies this via `build_root_agent(**_make_deps())`, you can skip this step — the real check is Step 8.3 + Step 8.4 manual smoke.

- [ ] **Step 8.3: Thread a shared GenAIClient through**

In `backend/my_agent/agent.py`:

1. Add to imports near the top:

```python
from google.genai import Client as GenAIClient
```

2. In `_build_default_root_agent`, construct a single GenAIClient and pass it:

```python
def _build_default_root_agent() -> SequentialAgent:
    firestore_client = AsyncClient()   # or whatever existing factory call
    genai_client = GenAIClient()       # NEW — shared embedding client

    master_data_repo = MasterDataRepo(
        firestore_client,
        genai_client=genai_client,     # NEW — was: MasterDataRepo(firestore_client)
    )
    # ... rest of the function unchanged ...
```

*(Find the exact existing MasterDataRepo construction call and add the kwarg. Do not change `build_root_agent` — only the default-wiring helper.)*

- [ ] **Step 8.4: Run the orchestrator suite**

Run: `uv run pytest tests/unit/test_orchestrator_build.py -v`

Expected: all tests green.

Smoke test the default build:

```bash
uv run python -c "from backend.my_agent.agent import _build_default_root_agent; root = _build_default_root_agent(); print(root.name, len(root.sub_agents))"
```

Expected: the pipeline's current stage count (9 pre-Track-A2-land, 10 or 11 if other tracks have landed) prints cleanly without a `GOOGLE_API_KEY` error — because `GenAIClient()` constructs lazily (reads env at first API call) and `MasterDataRepo._ensure_genai_client` further defers the check.

*(If `GenAIClient()` does validate the key at construction time in a future SDK version, adapt: wrap in a try/except log-and-continue, or defer GenAIClient construction into a factory function passed as a kwarg. As of google-genai 0.3.x, construction is lazy.)*

- [ ] **Step 8.5: Commit**

```bash
git add backend/my_agent/agent.py tests/unit/test_orchestrator_build.py
git commit -m "feat(orchestrator): thread shared GenAIClient into MasterDataRepo

_build_default_root_agent now constructs a single google.genai.Client
and passes it to MasterDataRepo's new genai_client kwarg. Pipeline-wide
sharing — all sku_matcher tier-3 calls reuse one client (connection
pooling, auth caching).

build_root_agent signature is unchanged; genai_client stays internal
to the default-wiring helper.

Track E plan Task 8."
```

---

## Task 9: Emulator integration test

Real Firestore emulator, 3 products seeded with known vectors (not from Gemini — precomputed deterministic vectors), `_embed_query` mocked to return a fixed vector. Asserts `find_nearest` over the emulator returns the expected top-1 with similarity ≥ 0.70.

**Files:**
- Create: `tests/integration/test_find_nearest_emulator.py`

- [ ] **Step 9.1: Write the test**

Create `tests/integration/test_find_nearest_emulator.py`:

```python
"""Emulator round-trip test for Track E's tier-3 vector search.

Strategy: seed 3 products with deterministic hand-crafted embeddings
so the test doesn't depend on a live Gemini call. We then call
find_product_by_embedding with _embed_query mocked to return a
specific query vector, and assert the Firestore emulator's find_nearest
returns the expected ranking.

The hand-crafted vectors work in 3-dim space (padded out to 768 with
zeros) with cosine distance:
  product A: [1, 0, 0, 0, ...] - maximally similar to query A
  product B: [0, 1, 0, 0, ...]
  product C: [0, 0, 1, 0, ...]
  query    : [1, 0, 0, 0, ...]  <-- matches A exactly

We expect A as top-1 with similarity 1.0, B and C tied at 0.5
(orthogonal cosine distance 1.0).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from google.cloud.firestore import AsyncClient
from google.cloud.firestore_v1.vector import Vector


EMBED_DIM = 768


def _one_hot(index: int) -> list[float]:
    """Return a 768-dim unit vector with 1.0 at `index` and 0.0 elsewhere."""
    v = [0.0] * EMBED_DIM
    v[index] = 1.0
    return v


@pytest.mark.asyncio
@pytest.mark.firestore_emulator
async def test_find_nearest_returns_expected_top1_over_emulator(
    firestore_emulator_project,   # existing fixture — sets project + emulator env
):
    from backend.tools.order_validator.tools.master_data_repo import (
        PRODUCTS_COLLECTION,
        MasterDataRepo,
    )

    # Seed 3 deterministic products via AsyncClient direct write.
    client = AsyncClient()
    collection = client.collection(PRODUCTS_COLLECTION)

    test_products = [
        ("TEST-A", _one_hot(0)),
        ("TEST-B", _one_hot(1)),
        ("TEST-C", _one_hot(2)),
    ]
    for sku, vec in test_products:
        await collection.document(sku).set({
            "sku": sku,
            "short_description": f"Product {sku}",
            "long_description": f"Full description of product {sku}.",
            "category": "test",
            "subcategory": "track-e",
            "uom": "EA",
            "pack_uom": "EA",
            "pack_size": 1,
            "alt_uoms": ["EA"],
            "unit_price_usd": 1.0,
            "standards": [],
            "lead_time_days": 1,
            "min_order_qty": 1,
            "country_of_origin": "US",
            "description_embedding": Vector(vec),
        })

    repo = MasterDataRepo(client)

    # Mock _embed_query to return the query vector, bypassing real Gemini.
    query_vec = _one_hot(0)
    with patch.object(repo, "_embed_query", AsyncMock(return_value=query_vec)):
        matches = await repo.find_product_by_embedding("product A", k=3)

    skus_ordered = [m.sku for m in matches]
    assert skus_ordered[0] == "TEST-A", (
        f"expected TEST-A as top-1; got order {skus_ordered}"
    )
    assert matches[0].score >= 0.99, (
        f"expected near-1.0 similarity for identical vectors; got {matches[0].score}"
    )
    assert matches[0].score <= 1.0
    assert matches[0].source == "firestore_findnearest"

    # Cleanup
    for sku, _ in test_products:
        await collection.document(sku).delete()
    await client.close()
```

**Fixture note:** The plan assumes `firestore_emulator_project` (or equivalent) fixture exists from earlier tracks in `tests/integration/conftest.py`. If the existing conftest uses a different fixture name (e.g. `emulator_firestore_client`), replace the fixture dependency accordingly. The test itself constructs its own `AsyncClient()` so fixture needs are minimal — just ensure `FIRESTORE_EMULATOR_HOST` + `GOOGLE_CLOUD_PROJECT` env vars are set before the test runs.

- [ ] **Step 9.2: Run the test**

Start the emulator if not running:

```bash
firebase emulators:start --only firestore
```

Then run:

```bash
FIRESTORE_EMULATOR_HOST=localhost:8080 GOOGLE_CLOUD_PROJECT=demo-order-intake-local \
  uv run pytest tests/integration/test_find_nearest_emulator.py -v -m firestore_emulator
```

Expected: 1 passed. If `find_nearest` is unsupported on the currently installed emulator version, the test errors with `NotImplementedError` or a gRPC `UNIMPLEMENTED` error — in that case, update the emulator (Firebase CLI ≥ 13.x / Firestore emulator ≥ 1.19 supports find_nearest).

- [ ] **Step 9.3: Commit**

```bash
git add tests/integration/test_find_nearest_emulator.py
git commit -m "test(integration): tier-3 find_nearest emulator round-trip

Seeds 3 products with deterministic one-hot embedding vectors (no
Gemini dependency) and asserts find_product_by_embedding returns the
expected top-1 with similarity ~1.0 when the query vector matches
a seeded product exactly.

_embed_query is mocked to return the query vector, bypassing live
Gemini — the test verifies the Firestore integration (Vector storage
+ find_nearest COSINE + distance-to-similarity conversion + response
mapping to EmbeddingMatch).

Gated by @pytest.mark.firestore_emulator so CI skips it when no
emulator is running.

Track E plan Task 9."
```

---

## Task 10: Vector index setup docs in README

Document the one-shot `gcloud` command operators need to run against live Firestore. The emulator handles find_nearest without any explicit index, so this is live-only setup.

**Files:**
- Modify: `backend/my_agent/README.md`

- [ ] **Step 10.1: Inspect the current README structure**

Run: `grep -n '^##' backend/my_agent/README.md` to see the existing section headers. Decide on a good insertion point — typically after the "Running locally" / "Emulator setup" section and before "Deployment".

- [ ] **Step 10.2: Append the vector-index subsection**

Add the following section to `backend/my_agent/README.md` (adjust the heading level `##` / `###` to fit the surrounding hierarchy):

```markdown
## Vector index setup (Track E — tier-3 embedding search)

The SKU matcher's tier-3 semantic search uses Firestore's
`find_nearest` over the `description_embedding` field on each
`products` doc. Before tier 3 can return results against **live**
Firestore, create a composite vector index once per project:

\`\`\`bash
gcloud firestore indexes composite create \\
  --collection-group=products \\
  --query-scope=COLLECTION \\
  --field-config='vector-config={"dimension":768,"flat":{}},field-path=description_embedding'
\`\`\`

Index creation takes ~1-3 minutes. Check status:

\`\`\`bash
gcloud firestore indexes composite list --filter="collection_group=products"
\`\`\`

The Firestore **emulator** handles `find_nearest` without any
explicit index declaration — nothing to run locally.

### Seeding embeddings

Embeddings are computed at seed time, not on read. Run:

\`\`\`bash
# Live Firestore (needs GOOGLE_API_KEY for text-embedding-004):
uv run python scripts/load_master_data.py

# Offline / emulator-only (no GOOGLE_API_KEY required):
uv run python scripts/load_master_data.py --no-embeddings
\`\`\`

`--no-embeddings` is useful when seeding the emulator for pipeline
tests that don't exercise tier 3. In that mode, tier-3 searches
return `[]` (no products have an embedding field), matching the
pre-Track-E stub behavior — tier 1 + tier 2 continue to work.
```

*(Note the escaped triple-backticks in the example above are for THIS plan file; when editing the README, use unescaped triple-backticks.)*

- [ ] **Step 10.3: Verify the README still renders**

Quickly scan the edited README to make sure the section lands in a sensible place and doesn't break surrounding prose.

- [ ] **Step 10.4: Commit**

```bash
git add backend/my_agent/README.md
git commit -m "docs(readme): vector index setup + seed-embeddings instructions

New 'Vector index setup (Track E)' section documents the one-shot
gcloud firestore indexes composite create command needed for live
Firestore tier-3 vector search. Emulator handles find_nearest
natively, so no local setup required.

Also adds a Seeding embeddings subsection: the default
load_master_data.py run computes text-embedding-004 vectors and
writes them to each product doc; --no-embeddings lets offline seed
runs skip the genai call (useful when GOOGLE_API_KEY isn't set or
when seeding the emulator for tier-1/2-only tests).

Track E plan Task 10."
```

---

## Task 11: Doc flips — Sprint status + Glacis roadmap

Close the loop. Track E flips its status-table row + one-line summary + Built inventory; Glacis §5 embedding bullet flips `[Post-MVP]` → `[MVP ✓]`; Phase 3 roadmap drops the line.

**Files:**
- Modify: `research/Order-Intake-Sprint-Status.md`
- Modify: `Glacis-Order-Intake.md`

- [ ] **Step 11.1: Update the `last_updated` frontmatter on the sprint status**

In `research/Order-Intake-Sprint-Status.md` line 7, append to the existing `last_updated` parenthesized block:

```
... existing text ... **Track E complete 2026-04-24:** tier-3 embedding search live — MasterDataRepo.find_product_by_embedding replaces its stub with a real text-embedding-004 + Firestore find_nearest impl; scripts/load_master_data.py extended to batch-compute description_embedding: Vector(768) on all 35 products at seed time via google.genai.Client (already transitive dep of google-adk, zero new pyproject entries); --no-embeddings CLI flag lets offline seed runs skip the genai call. Fail-open: embedding API exception or empty query returns [] + logs warning so tier-1/2 continue to work during Gemini outages. Flat EMBEDDING_THRESHOLD=0.70 preserved. No Pydantic schema changes (EmbeddingMatch already stable; ProductRecord uses extra='allow'). No pipeline topology changes. +~12 unit tests (test_embedding_matcher.py x9 + test_load_master_data.py x3) + 1 gated emulator integration + DI contract tests on test_master_data_repo.py.
```

- [ ] **Step 11.2: Flip the status-table row 2d (embeddings tier)**

Find the row "2d. Enrichment (item matching)" in the markdown table around line 26. Update the "What we have" cell to reflect tier 3 now live:

```markdown
| **2d. Enrichment (item matching)** | Exact → fuzzy → embedding | 3-tier ladder in `sku_matcher.py` ✓ — Tier 1 exact + alias, Tier 2 rapidfuzz `token_set_ratio` over `short_description`, **Tier 3 `text-embedding-004` + Firestore `find_nearest` live (Track E landed 2026-04-24)** — per-product `description_embedding: Vector(768)` seeded at load time; COSINE distance clamped to 0-1 similarity; `EMBEDDING_THRESHOLD=0.70` gate in `sku_matcher`; fail-open on embedding API errors (returns `[]`, tier-3 miss, aggregate routing handles CLARIFY/ESCALATE). | Nothing. Done. |
```

- [ ] **Step 11.3: Update the one-line summary**

In the "One-line summary" section around line 40, change any mention of "Tier 3 embedding search is stubbed" to "Tier 3 embedding search live". Example replacement:

Before:
```
(currently tier-1 exact + tier-2 fuzzy work; tier-3 falls through cleanly)
```

After:
```
(all three tiers live: tier-1 exact + tier-2 fuzzy + tier-3 text-embedding-004 semantic)
```

- [ ] **Step 11.4: Update completion metrics**

In the "Completion metrics" line around line 44, bump the unit-test count by ~12 and add the integration test.

- [ ] **Step 11.5: Append to the Built inventory**

In the "### Built (do not rebuild)" block (around line 50+), append:

```
scripts/load_master_data.py (embeddings)                                ✓ --embeddings / --no-embeddings CLI flag; _embed_text_for_product composes '{short}. {long}. Category: {cat}/{sub}.'; _embed_text calls google-genai text-embedding-004 RETRIEVAL_DOCUMENT task_type + 768 dim; each product doc gains description_embedding: Vector(768) before batch.commit() (<SHA Task 2>, 2026-04-24, Track E plan Task 2)
backend/tools/order_validator/tools/master_data_repo.py (Track E)       ✓ genai_client kwarg + _ensure_genai_client lazy ctor + _embed_query async helper (RETRIEVAL_QUERY task_type) with fail-open None return on exception; find_product_by_embedding real impl: query -> _embed_query -> Firestore find_nearest(COSINE, distance_result_field='__distance', limit=k) -> sorted EmbeddingMatch list with similarity = 1 - d/2 clamped to [0,1] (<SHA Task 4+5>, 2026-04-24, Track E plan Tasks 4 & 5)
backend/my_agent/agent.py (genai wiring)                                ✓ _build_default_root_agent constructs a shared google.genai.Client and passes it to MasterDataRepo via the new genai_client kwarg; pipeline-wide sharing (<SHA Task 8>, 2026-04-24, Track E plan Task 8)
tests/unit/test_embedding_matcher.py                                    ✓ 9 unit tests: _embed_query happy path + exception fail-open + malformed response; lazy genai ctor; find_product_by_embedding distance-similarity conversion across d=0/0.2/1.0/1.8/2.0/3.5 (clamp); k forwarding to find_nearest.limit; degenerate inputs (empty query, whitespace, k<1); _embed_query failure short-circuits (<SHA Task 4+5>)
tests/unit/test_load_master_data.py                                     ✓ 6 tests: _embed_text_for_product composition with/without subcategory; _embed_text config shape (RETRIEVAL_DOCUMENT + 768); --no-embeddings skips GenAIClient ctor entirely; with_embeddings=True calls embed_content per product + wraps result in Vector() (<SHA Task 1+2>)
tests/unit/test_sku_matcher.py (tier-3 real)                            ✓ two new tests: tier-3 hit with score 0.85 returns ('embedding', 0.85); tier-3 score 0.65 below threshold falls through to miss (<SHA Task 6>)
tests/integration/test_find_nearest_emulator.py                         ✓ emulator round-trip: seeds 3 products with one-hot 768-dim vectors; mocks _embed_query to return the query vector; asserts find_product_by_embedding top-1 with similarity ~1.0; gated @pytest.mark.firestore_emulator (<SHA Task 9>)
backend/my_agent/README.md (Vector index setup)                         ✓ gcloud firestore indexes composite create command + emulator-skip note + load_master_data --embeddings / --no-embeddings docs (<SHA Task 10>)
```

Fill in the commit SHAs as the tasks land.

- [ ] **Step 11.6: Flip the remaining-tracks Track E bullet**

In the "Remaining tracks" section around line 200, flip the Track E line from "spec + plan" to "landed":

```markdown
- **Track E — Embedding Tier 3** ✓ landed 2026-04-24 — text-embedding-004 + Firestore find_nearest live in `MasterDataRepo.find_product_by_embedding`; `scripts/load_master_data.py` batch-seeds `description_embedding: Vector(768)` on all 35 products; CLI `--no-embeddings` for offline runs. Flat `EMBEDDING_THRESHOLD=0.70` preserved; fail-open on embedding errors (tier-3 miss, aggregate routing handles CLARIFY/ESCALATE). Depends on nothing; blocks nothing. Design spec `docs/superpowers/specs/2026-04-24-track-e-embedding-tier3-design.md` (544c1d9); plan `docs/superpowers/plans/2026-04-24-track-e-embedding-tier3.md` (<plan SHA>); implementation landed across ~11 commits.
```

- [ ] **Step 11.7: Update the Glacis roadmap**

In `Glacis-Order-Intake.md`:

1. **Frontmatter `last_updated`** (line 5): append a Track E note matching the existing prose style.

2. **§5 "Tier 3 embedding search"** — find the bullet that currently reads something like:

```markdown
- `[Post-MVP]` **Tier 3 embedding search with `text-embedding-004` + alias learning from corrections** — ... Source: `Item-Matching.md`.
```

Split into two bullets (embedding portion flips; alias-learning stays Post-MVP):

```markdown
- `[MVP ✓]` **Tier 3 embedding search with `text-embedding-004`** — real `MasterDataRepo.find_product_by_embedding` implementation: async call to `google.genai.Client().aio.models.embed_content(model="text-embedding-004", task_type="RETRIEVAL_QUERY", output_dimensionality=768)` → `AsyncClient.collection("products").find_nearest(vector_field="description_embedding", query_vector=Vector(query_vec), distance_measure=COSINE, limit=k, distance_result_field="__distance")` → cosine-distance-to-similarity conversion (`similarity = 1 - d/2`, clamped) → sorted `EmbeddingMatch` list. `scripts/load_master_data.py` extended to batch-compute + persist `description_embedding: Vector(768)` on each of the 35 products at seed time (RETRIEVAL_DOCUMENT task_type); `--no-embeddings` CLI flag for offline seed runs. `google-genai` already a transitive dep of `google-adk`; zero new pyproject entries. Fail-open on embedding API errors (log + `[]`). Flat `EMBEDDING_THRESHOLD=0.70` in `sku_matcher` kept (rejecting Glacis's tiered 0.90/0.70 — validator aggregate handles confidence triage already). `backend/my_agent/README.md` documents the `gcloud firestore indexes composite create` CLI command for live-Firestore vector index setup; emulator auto-handles find_nearest. MVP: `scripts/load_master_data.py` (<SHAs>) + `backend/tools/order_validator/tools/master_data_repo.py` (<SHAs>) + test coverage (~12 unit + 1 emulator integration). Source: `Item-Matching.md`.
- `[Post-MVP]` **Alias learning from human corrections** — bake confirmed customer-description-to-SKU mappings back into per-product `aliases` → re-embed → customer vocabulary gradually enters the catalog's semantic space. MVP: —. Post-hackathon: couples with the Learning Loop track; requires a corrections-capture surface (dashboard or inline feedback). Source: `Item-Matching.md`, `Learning-Loop.md`.
```

3. **Phase 3 roadmap** — find the bullet near line 307:

```markdown
- Tier 3 embedding search with `text-embedding-004` + alias learning from corrections (§5, §11)
```

Replace with (only alias-learning remains Post-MVP):

```markdown
- Alias learning from corrections (§5 — embedding portion landed MVP via Track E; alias-feedback loop remains) + Learning Loop captures (§11)
```

- [ ] **Step 11.8: Sanity check — no existing tests broken**

Run: `uv run pytest -x`

Expected: all tests pass. Doc-only commits shouldn't affect tests.

- [ ] **Step 11.9: Commit**

```bash
git add research/Order-Intake-Sprint-Status.md Glacis-Order-Intake.md
git commit -m "docs: flip Track E + §5 tier-3 embedding to MVP complete

Sprint status:
- last_updated frontmatter appends Track E completion note.
- Flips 'Enrichment (item matching)' row: tier 3 'stub falls
  through' -> 'text-embedding-004 + find_nearest live'.
- One-line summary updated: 'all three tiers live'.
- Completion metrics bumped (+12 unit, +1 integration).
- Built inventory appends 8 new/modified file entries with per-
  task commit SHAs.
- Remaining-tracks bullet for Track E flipped 'spec+plan' -> '✓ landed'.

Glacis-Order-Intake.md:
- §5 bullet split: embedding portion [Post-MVP] -> [MVP checkmark]
  with full citation chain (files, commits, decisions); alias-
  learning portion stays [Post-MVP] (Learning Loop track).
- Phase 3 roadmap bullet trimmed — only alias learning remains.
- last_updated bumped with Track E narrative.

Track E plan Task 11."
```

---

## Post-implementation verification

After all 11 tasks land:

- [ ] **Run the full unit suite:** `uv run pytest tests/unit -v`

Expected: ~333 tests green (323 baseline + ~10 new from Track E; if Track B has also landed, add its ~20).

- [ ] **Run the integration suite:** `uv run pytest tests/integration -v`

Expected: baseline integration tests + 1 new `test_find_nearest_emulator.py` green (requires `firebase emulators:start --only firestore`).

- [ ] **Seed the emulator with embeddings:**

```bash
firebase emulators:start --only firestore &
FIRESTORE_EMULATOR_HOST=localhost:8080 \
  GOOGLE_CLOUD_PROJECT=demo-order-intake-local \
  uv run python scripts/load_master_data.py
```

Expected: `products: 35 (with embeddings)` printed to stdout.

- [ ] **Live-smoke tier-3 path:**

```bash
FIRESTORE_EMULATOR_HOST=localhost:8080 \
  GOOGLE_CLOUD_PROJECT=demo-order-intake-local \
  uv run python scripts/smoke_run.py data/email/<a fixture that has a tier-3-only line>
```

If the fixture produces a line description that tier 1 + tier 2 miss but is semantically clear, the pipeline log should show `sku_matched_embedding` with a score ≥ 0.70 on that line. If all fixtures hit tier 1 / 2 (likely with the fastener catalog), Track E is still wired correctly — tier 3 stays dormant on these paths.

- [ ] **Live Firestore index creation (for a deployed environment only):**

```bash
gcloud firestore indexes composite create \
  --collection-group=products \
  --query-scope=COLLECTION \
  --field-config='vector-config={"dimension":768,"flat":{}},field-path=description_embedding'
```

Verify index built:

```bash
gcloud firestore indexes composite list --filter="collection_group=products"
```

- [ ] **`adk web` discovery check:**

```bash
uv run adk web adk_apps
```

Expected: `order_intake_pipeline` listed; running a fixture shows the pipeline firing; no `GOOGLE_API_KEY` error when tier 3 is not invoked.

---

## Execution notes

- **Total tasks:** 11.
- **Estimated execution time:** ~4-5h (closer to A1 scale than A2/B/D — smaller touch surface).
- **Per-task commit SHAs:** fill in the Built-inventory entries in Task 11 by running `git log --oneline` at land-time.
- **Test count delta:** +~12 unit + 1 integration. Pipeline total ends around ~333 unit + existing integration + 1.
- **Dependency profile:** NONE. Track E is a leaf in the sprint graph — no preflight checks, no other-track guards. Executes in any session order.
- **Environment variables:** Task 3 (seed-run smoke) + Task 9 (emulator integration) + any live-Gemini smoke runs need `GOOGLE_API_KEY` set. Unit tests mock genai + never hit the network.
- **SDK version sensitivity:** The `google.genai.Client().aio.models.embed_content(...)` API shape reflects `google-genai` ~0.3.x through ~1.x (stable across these). If a future major-version bump changes the signature, the `_embed_query` method + its tests are the only surface to update.
- **Emulator support:** `find_nearest` requires Firestore emulator ≥ ~1.19 (Firebase CLI ≥ ~13.x). If your emulator version pre-dates that, Task 9's test errors with `UNIMPLEMENTED`; update the emulator.

**Last step:** `research/Order-Intake-Sprint-Status.md` auto-updates with Track E completion via Task 11; the stop hook's staleness guard will pass cleanly post-commit.

End of plan.
