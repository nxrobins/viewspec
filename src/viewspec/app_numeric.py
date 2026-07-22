"""Generated numeric kernel shared by React AppBundle runtime and Freerange proof.

The function inventory is derived only from declared state operations.  TypeScript and
JavaScript variants are rendered from the same bounded function specifications so the
analyzed React implementation and reducer-conformance implementation cannot drift.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Iterable, Mapping


NUMERIC_KERNEL_PROFILE = "viewspec_numeric_kernel_v1"
NUMERIC_KERNEL_PATH = "src/viewspec_numeric.ts"
NUMERIC_KERNEL_MODULE = "./viewspec_numeric"


@dataclass(frozen=True)
class _NumericFunction:
    name: str
    parameter_names: tuple[str, ...]
    body: tuple[str, ...]
    allowed_requires: tuple[str, ...]
    required_ensures: tuple[str, ...]


_FUNCTIONS: dict[str, _NumericFunction] = {
    "clampMoveIndex": _NumericFunction(
        name="clampMoveIndex",
        parameter_names=("rawIndex", "length"),
        body=(
            'if (!Number.isFinite(rawIndex)) throw new Error("APP_STATE_REDUCER_OP_FAILED");',
            'if (!Number.isInteger(length) || length < 0) throw new Error("APP_STATE_REDUCER_OP_FAILED");',
            "const index = Math.trunc(rawIndex);",
            "return Math.max(0, Math.min(index, length));",
        ),
        allowed_requires=("Number.isFinite(rawIndex)", "Number.isFinite(length)"),
        required_ensures=("return is a finite integer number at least 0",),
    ),
    "addFiniteNumbers": _NumericFunction(
        name="addFiniteNumbers",
        parameter_names=("current", "amount"),
        body=(
            'if (!Number.isFinite(current) || !Number.isFinite(amount)) throw new Error("APP_STATE_REDUCER_OP_FAILED");',
            "const result = current + amount;",
            'if (!Number.isFinite(result)) throw new Error("APP_STATE_REDUCER_OP_FAILED");',
            "return result;",
        ),
        allowed_requires=("Number.isFinite(current)", "Number.isFinite(amount)"),
        required_ensures=("return is a finite number",),
    ),
    "compareFiniteNumbers": _NumericFunction(
        name="compareFiniteNumbers",
        parameter_names=("left", "right"),
        body=(
            'if (!Number.isFinite(left) || !Number.isFinite(right)) throw new Error("APP_STATE_REDUCER_OP_FAILED");',
            "const result = left - right;",
            'if (!Number.isFinite(result)) throw new Error("APP_STATE_REDUCER_OP_FAILED");',
            "return result;",
        ),
        allowed_requires=("Number.isFinite(left)", "Number.isFinite(right)"),
        required_ensures=("return is a finite number",),
    ),
    "applySortDirection": _NumericFunction(
        name="applySortDirection",
        parameter_names=("comparison", "direction"),
        body=(
            'if (!Number.isFinite(comparison)) throw new Error("APP_STATE_REDUCER_OP_FAILED");',
            'if (direction !== -1 && direction !== 1) throw new Error("APP_STATE_REDUCER_OP_FAILED");',
            "const result = comparison * direction;",
            'if (!Number.isFinite(result)) throw new Error("APP_STATE_REDUCER_OP_FAILED");',
            "return result;",
        ),
        allowed_requires=("Number.isFinite(comparison)", "Number.isFinite(direction)"),
        required_ensures=("return is a finite number",),
    ),
    "stableSortIndexDelta": _NumericFunction(
        name="stableSortIndexDelta",
        parameter_names=("leftIndex", "rightIndex"),
        body=(
            'if (!Number.isInteger(leftIndex) || leftIndex < 0) throw new Error("APP_STATE_REDUCER_OP_FAILED");',
            'if (!Number.isInteger(rightIndex) || rightIndex < 0) throw new Error("APP_STATE_REDUCER_OP_FAILED");',
            "const result = leftIndex - rightIndex;",
            'if (!Number.isFinite(result)) throw new Error("APP_STATE_REDUCER_OP_FAILED");',
            "return result;",
        ),
        allowed_requires=("Number.isFinite(leftIndex)", "Number.isFinite(rightIndex)"),
        required_ensures=("return is a finite integer number",),
    ),
    "normalizeSliceIndex": _NumericFunction(
        name="normalizeSliceIndex",
        parameter_names=("index",),
        body=(
            'if (!Number.isInteger(index) || index < 0) throw new Error("APP_STATE_REDUCER_OP_FAILED");',
            "return index;",
        ),
        allowed_requires=("Number.isFinite(index)",),
        required_ensures=("return is a finite integer number at least 0",),
    ),
}

_FUNCTION_ORDER = tuple(_FUNCTIONS)


def numeric_scope_for_app(app_payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return the deterministic numeric proof scope implied by runtime operations."""
    mutation_ops = _operation_names(app_payload.get("mutations"))
    selector_ops = _operation_names(app_payload.get("selectors"))
    required: list[str] = []
    if "move" in mutation_ops:
        required.append("clampMoveIndex")
    if "increment" in mutation_ops:
        required.append("addFiniteNumbers")
    if "sort_by" in selector_ops:
        required.extend(("compareFiniteNumbers", "applySortDirection", "stableSortIndexDelta"))
    if "slice" in selector_ops:
        required.append("normalizeSliceIndex")
    ordered = tuple(name for name in _FUNCTION_ORDER if name in required)
    if not ordered:
        return {
            "schema_version": 1,
            "profile": NUMERIC_KERNEL_PROFILE,
            "status": "not_applicable",
            "files": [],
            "call_sites": [],
            "required_functions": [],
            "allowed_requires": {},
            "required_ensures": {},
        }
    return {
        "schema_version": 1,
        "profile": NUMERIC_KERNEL_PROFILE,
        "status": "applicable",
        "kernel_path": NUMERIC_KERNEL_PATH,
        "required_functions": list(ordered),
        "allowed_requires": {name: list(_FUNCTIONS[name].allowed_requires) for name in ordered},
        "required_ensures": {name: list(_FUNCTIONS[name].required_ensures) for name in ordered},
    }


def generate_numeric_typescript(scope_or_payload: Mapping[str, Any]) -> str:
    """Render the analyzable TypeScript module for an applicable scope."""
    return _render_numeric_source(_scope_names(scope_or_payload), typescript=True, exports=True)


def generate_numeric_javascript(
    scope_or_payload: Mapping[str, Any],
    *,
    exports: bool = True,
) -> str:
    """Render the executable JavaScript twin from the exact same function model."""
    return _render_numeric_source(_scope_names(scope_or_payload), typescript=False, exports=exports)


def numeric_import_names(app_payload: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(numeric_scope_for_app(app_payload)["required_functions"])


def numeric_function_hashes(scope_or_payload: Mapping[str, Any]) -> dict[str, str]:
    """Hash each exact TypeScript function declaration independently of file ordering."""
    return {
        name: hashlib.sha256(_render_numeric_function(_FUNCTIONS[name], typescript=True, exports=True).encode("utf-8")).hexdigest()
        for name in _scope_names(scope_or_payload)
    }


def _scope_names(scope_or_payload: Mapping[str, Any]) -> tuple[str, ...]:
    if "status" in scope_or_payload and "required_functions" in scope_or_payload:
        names_value = scope_or_payload.get("required_functions")
    else:
        names_value = numeric_scope_for_app(scope_or_payload)["required_functions"]
    if not isinstance(names_value, list) or any(not isinstance(name, str) for name in names_value):
        raise ValueError("APP_FREERANGE_SCOPE_INVALID: numeric required_functions must be a string array")
    names = tuple(names_value)
    if len(set(names)) != len(names) or any(name not in _FUNCTIONS for name in names):
        raise ValueError("APP_FREERANGE_SCOPE_INVALID: numeric required_functions are invalid")
    return tuple(name for name in _FUNCTION_ORDER if name in names)


def _operation_names(items: object) -> frozenset[str]:
    names: set[str] = set()
    if not isinstance(items, list):
        return frozenset()
    for item in items:
        if not isinstance(item, dict):
            continue
        ops = item.get("ops")
        if not isinstance(ops, list):
            continue
        for op in ops:
            if isinstance(op, dict) and isinstance(op.get("op"), str):
                names.add(op["op"])
    return frozenset(names)


def _render_numeric_source(names: Iterable[str], *, typescript: bool, exports: bool) -> str:
    selected = tuple(names)
    if not selected:
        return ""
    lines = [
        "// Generated by ViewSpec numeric kernel v1. Do not edit.",
        "// Runtime-connected helpers only; this module contains no certificate-only implementation.",
        "",
    ]
    for index, name in enumerate(selected):
        spec = _FUNCTIONS[name]
        lines.extend(_render_numeric_function(spec, typescript=typescript, exports=exports).splitlines())
        if index != len(selected) - 1:
            lines.append("")
    lines.append("")
    return "\n".join(lines)


def _render_numeric_function(spec: _NumericFunction, *, typescript: bool, exports: bool) -> str:
    parameters = ", ".join(
        f"{parameter}: number" if typescript else parameter
        for parameter in spec.parameter_names
    )
    prefix = "export " if exports else ""
    return_type = ": number" if typescript else ""
    lines = [f"{prefix}function {spec.name}({parameters}){return_type} {{"]
    lines.extend(f"  {body_line}" for body_line in spec.body)
    lines.append("}")
    return "\n".join(lines)


__all__ = [
    "NUMERIC_KERNEL_MODULE",
    "NUMERIC_KERNEL_PATH",
    "NUMERIC_KERNEL_PROFILE",
    "generate_numeric_javascript",
    "generate_numeric_typescript",
    "numeric_import_names",
    "numeric_function_hashes",
    "numeric_scope_for_app",
]
