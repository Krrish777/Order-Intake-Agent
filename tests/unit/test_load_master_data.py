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
