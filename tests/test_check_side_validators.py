"""Direct unit coverage for check-side validators that were reachable only via check_artifact_dir
and never fed a tampered artifact, plus the reducer-conformance comparator (dev-only).

Each test pins BOTH a tampered/unequal case (fires) and a valid/equal case (does not), so a
no-oped validator or a degenerate comparator (`lambda a, b: True` / `False`) fails loudly.
"""

from __future__ import annotations

from viewspec.local_tools_validators import (
    _tsx_text_marker,
    _validate_react_tailwind_limits,
    _validate_react_tailwind_semantic_markers,
)
from viewspec.state_ir import _json_values_equal


def _text_nodes(count: int) -> dict[str, dict]:
    return {f"n{i}": {"primitive": "text", "props": {"text": "x"}} for i in range(count)}


def _button_nodes(count: int) -> dict[str, dict]:
    return {f"b{i}": {"primitive": "button", "props": {"action_id": f"a{i}", "label": "L"}} for i in range(count)}


def test_react_tailwind_limits_fire_on_tampered_artifact():
    # Over-limit SHIPPED artifacts must be rejected by the check-side re-derivation, each with its
    # distinct code -- and a within-limits artifact must fire nothing (non-vacuity).
    assert any("TAILWIND_LIMIT_EXCEEDED_NODES" in e for e in _validate_react_tailwind_limits("", _text_nodes(601)))
    assert any("TAILWIND_LIMIT_EXCEEDED_ACTIONS" in e for e in _validate_react_tailwind_limits("", _button_nodes(129)))
    assert any(
        "TAILWIND_LIMIT_EXCEEDED_ARTIFACT_BYTES" in e
        for e in _validate_react_tailwind_limits("z" * (256 * 1024 + 1), {})
    )
    assert _validate_react_tailwind_limits("small", _text_nodes(3)) == []


def test_react_tailwind_semantic_drift_fires_on_tamper():
    # Manifest text absent from the TSX, and an action-ID mismatch, each raise TAILWIND_SEMANTIC_DRIFT.
    text_drift = _validate_react_tailwind_semantic_markers(
        "const x = 1", {"d1": {"primitive": "text", "props": {"text": "MissingText"}}}
    )
    assert any("TAILWIND_SEMANTIC_DRIFT" in e and "d1" in e for e in text_drift)

    label_marker = _tsx_text_marker("Go")  # label present, but no data-action-id for a1 in the TSX
    action_mismatch = _validate_react_tailwind_semantic_markers(
        f"render {label_marker}", {"b1": {"primitive": "button", "props": {"action_id": "a1", "label": "Go"}}}
    )
    assert any("source action IDs do not match manifest action IDs" in e for e in action_mismatch)

    # A matching artifact (text marker present, no actions on either side) fires nothing.
    ok_tsx = f"return <p>{_tsx_text_marker('Hello')}</p>"
    assert _validate_react_tailwind_semantic_markers(ok_tsx, {"d1": {"primitive": "text", "props": {"text": "Hello"}}}) == []


def test_json_values_equal_pins_comparator_both_directions():
    # The comparator every reducer-conformance test leans on: pin equal AND unequal cases so a
    # degenerate `lambda a, b: True` / `False` replacement fails one asserted direction.
    assert _json_values_equal(1, 1.0) is True  # numbers compare by value (int/float)
    assert _json_values_equal({"a": 1, "b": [1, 2]}, {"a": 1.0, "b": [1, 2]}) is True  # nested
    assert _json_values_equal(None, None) is True
    assert _json_values_equal("x", "x") is True

    assert _json_values_equal(1, 2) is False
    assert _json_values_equal([1, 2], [2, 1]) is False  # order-sensitive
    assert _json_values_equal({"a": 1}, {"a": 1, "b": 2}) is False  # key-set-sensitive
    assert _json_values_equal({"a": 1}, {"a": 2}) is False  # differing leaf
