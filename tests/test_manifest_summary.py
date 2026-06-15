from __future__ import annotations

from viewspec.manifest_summary import manifest_aesthetic_layout_summary, manifest_root_aesthetic_profile


def test_manifest_summary_helpers_ignore_boolean_layout_columns():
    nodes = {
        "dom-root": {
            "primitive": "root",
            "props": {"aesthetic_profile": "aesthetic.editorial_product"},
        },
        "dom-grid": {
            "primitive": "grid",
            "props": {
                "aesthetic_layout_profile": "aesthetic.editorial_product",
                "columns": True,
                "product_role": "metric_grid",
            },
        },
    }

    assert manifest_root_aesthetic_profile(nodes) == "aesthetic.editorial_product"
    assert manifest_aesthetic_layout_summary(nodes) == {
        "metric_grid": {
            "columns": None,
            "node_count": 1,
            "profile": "aesthetic.editorial_product",
        }
    }
