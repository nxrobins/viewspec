from __future__ import annotations

from viewspec.app_numeric import (
    generate_numeric_javascript,
    generate_numeric_typescript,
    numeric_import_names,
    numeric_scope_for_app,
)


def test_numeric_scope_is_not_applicable_without_runtime_numeric_operations() -> None:
    scope = numeric_scope_for_app({"mutations": [], "selectors": []})

    assert scope["status"] == "not_applicable"
    assert scope["required_functions"] == []
    assert generate_numeric_typescript(scope) == ""


def test_numeric_scope_is_derived_from_actual_mutation_and_selector_operations() -> None:
    payload = {
        "mutations": [
            {"ops": [{"op": "move"}, {"op": "increment"}, {"op": "patch"}]},
        ],
        "selectors": [
            {"ops": [{"op": "sort_by"}, {"op": "slice"}, {"op": "filter_eq"}]},
        ],
    }

    scope = numeric_scope_for_app(payload)

    assert scope["status"] == "applicable"
    assert scope["required_functions"] == [
        "clampMoveIndex",
        "addFiniteNumbers",
        "compareFiniteNumbers",
        "applySortDirection",
        "stableSortIndexDelta",
        "normalizeSliceIndex",
    ]
    assert numeric_import_names(payload) == tuple(scope["required_functions"])
    assert set(scope["allowed_requires"]) == set(scope["required_functions"])
    assert set(scope["required_ensures"]) == set(scope["required_functions"])


def test_typescript_and_javascript_kernels_share_one_function_model() -> None:
    payload = {
        "mutations": [{"ops": [{"op": "move"}, {"op": "increment"}]}],
        "selectors": [{"ops": [{"op": "sort_by"}, {"op": "slice"}]}],
    }
    typescript = generate_numeric_typescript(payload)
    javascript = generate_numeric_javascript(payload)

    for statement in (
        "const index = Math.trunc(rawIndex);",
        "const result = current + amount;",
        "const result = left - right;",
        "const result = comparison * direction;",
        "const result = leftIndex - rightIndex;",
        "return index;",
    ):
        assert typescript.count(statement) == javascript.count(statement) == 1
    assert "rawIndex: number" in typescript
    assert "rawIndex: number" not in javascript
    assert "export function clampMoveIndex" in typescript
    assert "export function clampMoveIndex" in javascript
