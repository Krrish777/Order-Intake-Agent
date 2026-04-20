"""Corpus-level integrity check for the synthetic data under `data/`.

Run after emit_ground_truth has produced all `.expected.json` siblings.
Checks performed:

1.  **Pair integrity.** Every ground-truth file has its source document on
    disk (and vice versa — no orphan source without a ground truth).
2.  **Schema validity.** Every `.expected.json` parses back through the
    Pydantic schema without loss.
3.  **Customer reference integrity.** `customer_id` on every ground truth
    exists in `data/masters/customers.json`.
4.  **SKU reference integrity.** Every non-null `canonical_sku` exists in
    `data/masters/products.json`.
5.  **Ship-to reference integrity.** Every non-null `ship_to_code` matches
    one of the declared ship-to locations for the referenced customer,
    OR is noted in line_items when the document uses per-line ship-to.
6.  **Alias resolution consistency.** When a customer has `sku_aliases`
    and the ground-truth `customer_ref` matches an alias key, the
    `canonical_sku` must equal the alias target.

The script exits non-zero and prints a specific error list on any failure.

Run with: ``uv run python -m scripts.verify_corpus``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from backend.models.ground_truth import GroundTruth


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
MASTERS = DATA / "masters"


def _load_masters() -> tuple[dict, dict]:
    customers_json = json.loads((MASTERS / "customers.json").read_text(encoding="utf-8"))
    products_json = json.loads((MASTERS / "products.json").read_text(encoding="utf-8"))
    customers = {c["customer_id"]: c for c in customers_json["customers"]}
    products = {p["sku"]: p for p in products_json["products"]}
    return customers, products


def _all_expected_paths() -> list[Path]:
    return sorted(DATA.rglob("*.expected.json"))


def _source_for(expected: Path) -> Path | None:
    stem = expected.name[: -len(".expected.json")]
    for ext in (".xlsx", ".csv", ".pdf", ".edi", ".eml"):
        candidate = expected.parent / f"{stem}{ext}"
        if candidate.exists():
            return candidate
    return None


def _all_source_paths() -> list[Path]:
    patterns = ("excel/*.xlsx", "csv/*.csv", "pdf/*.pdf", "edi/*.edi", "email/*.eml")
    out: list[Path] = []
    for pat in patterns:
        out.extend(DATA.glob(pat))
    return sorted(out)


def verify() -> list[str]:
    errors: list[str] = []
    customers, products = _load_masters()

    expected_paths = _all_expected_paths()
    source_paths = _all_source_paths()

    # Orphan check — every source has expected, every expected has source
    expected_stems = {p.name[: -len(".expected.json")] for p in expected_paths}
    source_stems = {p.stem for p in source_paths}

    for missing in sorted(expected_stems - source_stems):
        errors.append(f"orphan expected.json (no source): {missing}")
    for missing in sorted(source_stems - expected_stems):
        errors.append(f"orphan source (no expected.json): {missing}")

    # Per-file schema + reference integrity
    for ep in expected_paths:
        try:
            gt = GroundTruth.model_validate_json(ep.read_text(encoding="utf-8"))
        except Exception as e:
            errors.append(f"{ep.name}: schema validation failed: {e}")
            continue

        # Customer integrity
        if gt.customer_id not in customers:
            errors.append(f"{ep.name}: customer_id {gt.customer_id} not in customers.json")
            continue
        cust = customers[gt.customer_id]

        # Ship-to integrity (header-level, if present)
        if gt.ship_to_code is not None:
            valid_codes = {s["location_code"] for s in cust["ship_to"]}
            if gt.ship_to_code not in valid_codes:
                errors.append(
                    f"{ep.name}: ship_to_code {gt.ship_to_code} not among "
                    f"{sorted(valid_codes)} for {gt.customer_id}"
                )

        # Line-item integrity
        aliases = cust.get("sku_aliases", {}) or {}
        for li in gt.line_items:
            if li.canonical_sku is not None and li.canonical_sku not in products:
                errors.append(
                    f"{ep.name} line {li.line_number}: canonical_sku "
                    f"{li.canonical_sku} not in products.json"
                )
            # Alias resolution consistency
            if li.customer_ref in aliases:
                target = aliases[li.customer_ref]
                if li.canonical_sku != target:
                    errors.append(
                        f"{ep.name} line {li.line_number}: customer_ref "
                        f"{li.customer_ref!r} maps to {target} via aliases "
                        f"but ground truth says {li.canonical_sku}"
                    )

        # Source file existence
        src = _source_for(ep)
        if src is None:
            errors.append(f"{ep.name}: cannot find sibling source file")

    return errors


def main() -> int:
    errors = verify()
    expected_count = len(_all_expected_paths())
    source_count = len(_all_source_paths())
    print(f"sources: {source_count}  ground-truth: {expected_count}")
    if errors:
        print("FAILED:")
        for e in errors:
            print(f"  {e}")
        return 1
    print("all integrity checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
