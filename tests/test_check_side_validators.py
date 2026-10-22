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
    check_issue_entry,
    css_has_unsafe_reference,
    find_tag_blocks,
    iter_tag_regions,
    normalize_css_text,
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
    assert (
        _validate_react_tailwind_semantic_markers(ok_tsx, {"d1": {"primitive": "text", "props": {"text": "Hello"}}})
        == []
    )


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


def test_find_tag_blocks_matches_regex_semantics_and_reports_unclosed_blocks():
    html = "<p>a</p><STYLE media=x>.a{}</style ><style>.b{}</Style><styles>no</styles><style>tail"
    scan = find_tag_blocks(html, "style")

    assert [html[cs:ce] for _s, cs, ce, _e in scan.blocks] == [".a{}", ".b{}"]
    assert [html[s:e] for s, _cs, _ce, e in scan.blocks] == ["<STYLE media=x>.a{}</style >", "<style>.b{}</Style>"]
    assert html[scan.unclosed_content_start :] == "tail"
    assert scan.truncated is False

    closed = find_tag_blocks("<style>a</style><style>b</style>", "style", limit=1)
    assert len(closed.blocks) == 1 and closed.truncated is True
    assert find_tag_blocks("<style x", "style").unclosed_content_start is not None  # opener never terminates
    assert find_tag_blocks("<style>a</style", "style").unclosed_content_start is not None  # closer never terminates
    assert find_tag_blocks("<p>no blocks</p>", "style") == ((), None, False)

    regions = list(iter_tag_regions("<meta charset=x><META http-equiv=refresh content=0><meta tail", "meta"))
    assert [attrs for _start, attrs in regions] == [" charset=x", " http-equiv=refresh content=0", " tail"]


def test_normalize_css_text_decodes_escapes_and_strips_comments_both_ways():
    with_comments, without_comments = normalize_css_text("background:\\75 r\\6C/**/(HTTPS://e/x) /* c */")
    assert without_comments == "background:url(https://e/x)"
    assert with_comments == "background:url/**/(https://e/x)/*c*/"
    # A comment opener inside a string must not hide a live url( that follows it (unterminated keeps text).
    assert css_has_unsafe_reference('content:"/*"; background:url(https://evil.example/x)') is True
    assert css_has_unsafe_reference("a:/* url( */ b") is True  # fail closed on the raw view too
    assert css_has_unsafe_reference("color:#fff; grid-area: 1 / 2 / 3 / 4; font: 12px/1.5 x") is False


def test_check_issue_entry_shapes_registered_prefixes_and_legacy_messages():
    coded = check_issue_entry("CHECK_INLINE_EVENT_HANDLER: index.html contains an inline event handler attribute")
    assert coded["code"] == "CHECK_INLINE_EVENT_HANDLER"
    assert coded["path"] == "index.html"
    assert coded["message"] == "index.html contains an inline event handler attribute"
    assert "on*" in coded["fix"]

    legacy = check_issue_entry("manifest missing nodes", default_fix="Fix it.")
    assert legacy == {
        "code": "CHECK_FAILED",
        "path": "provenance_manifest.json",
        "message": "manifest missing nodes",
        "fix": "Fix it.",
    }

    tailwind = check_issue_entry("TAILWIND_LIMIT_EXCEEDED_NODES: react-tailwind-tsx artifact exceeds 600 IR nodes")
    assert tailwind["code"] == "TAILWIND_LIMIT_EXCEEDED_NODES"
    assert tailwind["path"] == "provenance_manifest.json"  # no artifact file named: falls back to the manifest
    assert check_issue_entry("ViewSpecView.tsx contains fetch()")["path"] == "ViewSpecView.tsx"
    assert check_issue_entry("invalid diagnostics.json: boom")["path"] == "diagnostics.json"
    unknown_prefix = check_issue_entry("NOT_A_CODE: index.html something")
    assert unknown_prefix["code"] == "CHECK_FAILED"
    assert unknown_prefix["message"] == "NOT_A_CODE: index.html something"
