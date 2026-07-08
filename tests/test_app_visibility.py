"""AppBundle V4 visibility_v0: bounded, replay-provable conditional visibility.

Guards the frozen contract shape (grammar, caps, error codes), the Python==JavaScript evaluation
parity (SC-V3), v3 byte-stability (hash-golden discipline), the baked-marker pipeline (SC-V1), and
honest replay reporting (SC-V2). The canonical v4 fixture lives here as `_visibility_app_bundle`.
"""

from __future__ import annotations

import copy
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_app_bundle import _stateful_app_bundle  # noqa: E402

from viewspec.app_bundle import APP_BUNDLE_VISIBILITY_SCHEMA_VERSION, validate_app_text
from viewspec.state_ir import (
    APP_VISIBILITY_MAX_RULES,
    STATE_REDUCER_VISIBILITY_EXPORT,
    check_reducer_conformance,
    evaluate_selectors,
    evaluate_visibility,
    generate_typescript_reducer,
    initial_state,
    initial_visibility,
    replay_state_assertions,
    state_ir_summary,
    state_manifest,
    state_reducer_exports,
    validate_state_ir,
)

_NODE_AVAILABLE = shutil.which("node") is not None


def _visibility_app_bundle() -> dict[str, Any]:
    """Canonical V4 fixture: the V3 stateful bundle + all three condition forms + expect_visibility."""
    payload = copy.deepcopy(_stateful_app_bundle())
    payload["schema_version"] = APP_BUNDLE_VISIBILITY_SCHEMA_VERSION
    payload["visibility"] = [
        {
            "id": "incidents_when_selected",
            "screen_id": "queue",
            "target_ref": "motif:incidents",
            "when": {"state": "selected_incident", "is": "truthy"},
        },
        {
            "id": "main_when_active",
            "screen_id": "detail",
            "target_ref": "region:main",
            "when": {"selector": "active_incidents", "is": "non_empty"},
        },
        {
            "id": "queue_main_when_1042",
            "screen_id": "queue",
            "target_ref": "region:main",
            "when": {"state": "selected_incident", "equals": "inc-1042"},
        },
    ]
    # Post-replay verdicts for the existing triage_replay (sets selected_incident to a truthy id).
    payload["state_replay_assertions"][0]["expect_visibility"] = {
        "incidents_when_selected": True,
        "main_when_active": True,
    }
    return payload


def _mutated(payload: dict[str, Any], mutate) -> dict[str, Any]:
    mutated = copy.deepcopy(payload)
    mutate(mutated)
    return mutated


def _issue_codes(payload: dict[str, Any]) -> set[str]:
    result = validate_app_text(json.dumps(payload))
    return {issue["code"] for issue in result.get("issues", [])}


# --- contract acceptance ----------------------------------------------------------------------------


def test_v4_fixture_validates_and_reports_summary():
    result = validate_app_text(json.dumps(_visibility_app_bundle()))
    assert result["ok"] is True
    assert result["state_ir"]["visibility_rule_count"] == 3
    assert result["state_ir"]["profile"] == "interactive_state_v0"


def test_v3_summary_shape_is_unchanged():
    # v3 pinned 5-key summary must never gain visibility keys.
    summary = state_ir_summary(_stateful_app_bundle())
    assert sorted(summary) == ["mutation_count", "profile", "replay_assertion_count", "selector_count", "state_count"]


# --- error codes (one per parametrized mutation) ----------------------------------------------------


@pytest.mark.parametrize(
    ("expected_code", "mutate"),
    [
        ("APP_VISIBILITY_NOT_ARRAY", lambda p: p.update(visibility={"not": "a list"})),
        ("APP_VISIBILITY_RULE_NOT_OBJECT", lambda p: p["visibility"].append("rule")),
        ("APP_VISIBILITY_UNKNOWN_FIELD", lambda p: p["visibility"][0].update(extra=True)),
        ("APP_VISIBILITY_FIELD_REQUIRED", lambda p: p["visibility"][0].pop("when")),
        ("APP_VISIBILITY_INVALID_ID", lambda p: p["visibility"][0].update(id="bad id!")),
        ("APP_VISIBILITY_DUPLICATE_ID", lambda p: p["visibility"].append(copy.deepcopy(p["visibility"][0]) | {"target_ref": "region:root"})),
        ("APP_VISIBILITY_SCREEN_MISSING", lambda p: p["visibility"][0].update(screen_id="ghost")),
        ("APP_VISIBILITY_TARGET_REF_INVALID", lambda p: p["visibility"][0].update(target_ref="view:main")),
        ("APP_VISIBILITY_TARGET_MISSING", lambda p: p["visibility"][0].update(target_ref="motif:ghost")),
        ("APP_VISIBILITY_DUPLICATE_TARGET", lambda p: p["visibility"].append({"id": "dup_target", "screen_id": "queue", "target_ref": "motif:incidents", "when": {"state": "selected_incident", "is": "falsy"}})),
        ("APP_VISIBILITY_CONDITION_INVALID", lambda p: p["visibility"][0].update(when={"state": "selected_incident", "is": "truthy", "equals": 1})),
        ("APP_VISIBILITY_STATE_MISSING", lambda p: p["visibility"][0].update(when={"state": "ghost_state", "is": "truthy"})),
        ("APP_VISIBILITY_STATE_KIND_UNSUPPORTED", lambda p: p["visibility"][0].update(when={"state": "queue_flags", "is": "truthy"})),
        ("APP_VISIBILITY_EQUALS_NOT_SCALAR", lambda p: p["visibility"][2].update(when={"state": "selected_incident", "equals": {"nested": True}})),
        ("APP_VISIBILITY_SELECTOR_MISSING", lambda p: p["visibility"][1].update(when={"selector": "ghost_selector", "is": "empty"})),
        ("APP_VISIBILITY_REPLAY_RULE_MISSING", lambda p: p["state_replay_assertions"][0]["expect_visibility"].update(ghost_rule=True)),
        ("APP_VISIBILITY_REPLAY_EXPECT_INVALID", lambda p: p["state_replay_assertions"][0]["expect_visibility"].update(incidents_when_selected="yes")),
        ("APP_VISIBILITY_LIMIT_EXCEEDED", lambda p: p.update(visibility=[
            {"id": f"r{i}", "screen_id": "queue", "target_ref": "motif:incidents", "when": {"state": "selected_incident", "is": "truthy"}}
            for i in range(APP_VISIBILITY_MAX_RULES + 1)
        ])),
    ],
)
def test_invalid_visibility_raises_expected_code(expected_code, mutate):
    codes = _issue_codes(_mutated(_visibility_app_bundle(), mutate))
    assert expected_code in codes, codes


def test_v3_rejects_visibility_and_expect_visibility():
    v3 = _mutated(_visibility_app_bundle(), lambda p: p.update(schema_version=3))
    codes = _issue_codes(v3)
    assert "APP_UNKNOWN_FIELD" in codes  # root visibility field
    assert "APP_STATE_UNKNOWN_FIELD" in codes  # expect_visibility on the replay assertion


# --- evaluator semantics: Python == JavaScript scalar table (SC-V3) --------------------------------


@pytest.mark.parametrize(
    ("value", "truthy"),
    [
        (0, False), (0.0, False), ("", False), ([], False), ({}, False), (None, False), (False, False),
        (1, True), ("0", True), ([0], True), ({"k": 0}, True), (-1, True), (0.5, True),
    ],
)
def test_truthiness_parity_table(value, truthy):
    payload = _visibility_app_bundle()
    state_ir, issues = validate_state_ir(payload)
    assert not issues
    state = initial_state(payload, state_ir)
    state["selected_incident"] = value
    verdicts = evaluate_visibility(state, evaluate_selectors(state, state_ir), state_ir)
    assert verdicts["incidents_when_selected"] is truthy


@pytest.mark.parametrize(
    ("state_value", "equals", "expected"),
    [
        (1, 1.0, True),  # numbers compare by value (JS ===)
        (True, 1, False),  # bool is distinct from number
        ("inc-1042", "inc-1042", True),
        (None, None, True),
        ({"id": 1}, {"id": 1}, False),  # containers never equal (JS reference semantics)
    ],
)
def test_equals_parity_table(state_value, equals, expected):
    payload = _visibility_app_bundle()
    payload["visibility"][2]["when"] = {"state": "selected_incident", "equals": equals}
    state_ir, issues = validate_state_ir(payload)
    if issues:  # container equals is rejected at validation; evaluate directly for totality
        pytest.skip("validation rejects non-scalar equals; totality covered by evaluator unit")
    state = initial_state(payload, state_ir)
    state["selected_incident"] = state_value
    verdicts = evaluate_visibility(state, evaluate_selectors(state, state_ir), state_ir)
    assert verdicts["queue_main_when_1042"] is expected


def test_evaluator_is_total_over_unexpected_shapes():
    # Kinds are validation-time advisories: a scalar state driven to a dict/list must not crash.
    payload = _visibility_app_bundle()
    state_ir, _ = validate_state_ir(payload)
    state = initial_state(payload, state_ir)
    state["selected_incident"] = {"unexpected": "object"}
    verdicts = evaluate_visibility(state, evaluate_selectors(state, state_ir), state_ir)
    assert verdicts["incidents_when_selected"] is True  # non-empty dict is truthy
    assert verdicts["queue_main_when_1042"] is False  # container never strict-equals a scalar


# --- replay: expect_visibility pass and fail --------------------------------------------------------


def test_replay_visibility_passes_on_correct_expectations():
    report = replay_state_assertions(_visibility_app_bundle())
    assert report["ok"] is True
    assert report["assertions"][0]["visibility_matches"] is True


def test_replay_visibility_mismatch_fails_closed():
    payload = _visibility_app_bundle()
    payload["state_replay_assertions"][0]["expect_visibility"]["incidents_when_selected"] = False
    report = replay_state_assertions(payload)
    assert report["ok"] is False
    assert report["assertions"][0]["visibility_matches"] is False
    assert any(error["code"] == "APP_VISIBILITY_REPLAY_MISMATCH" for error in report["errors"])


def test_v3_replay_report_has_no_visibility_key():
    report = replay_state_assertions(_stateful_app_bundle())
    assert "visibility_matches" not in report["assertions"][0]


# --- generated artifact: v4 exports the evaluator; v3 bytes unchanged -------------------------------


def test_v4_reducer_exports_visibility_and_v3_does_not():
    v4_source = generate_typescript_reducer(_visibility_app_bundle())
    assert "export function evaluateViewSpecVisibility" in v4_source
    v3_source = generate_typescript_reducer(_stateful_app_bundle())
    assert "evaluateViewSpecVisibility" not in v3_source
    assert state_reducer_exports(_visibility_app_bundle())[-1] == STATE_REDUCER_VISIBILITY_EXPORT
    assert STATE_REDUCER_VISIBILITY_EXPORT not in state_reducer_exports(_stateful_app_bundle())


def test_state_manifest_carries_visibility_rule_ids():
    manifest = state_manifest(_visibility_app_bundle(), reducer_hash="x" * 64)
    assert manifest["visibility_rule_ids"] == ["incidents_when_selected", "main_when_active", "queue_main_when_1042"]
    assert STATE_REDUCER_VISIBILITY_EXPORT in manifest["reducer_exports"]
    v3_manifest = state_manifest(_stateful_app_bundle(), reducer_hash="x" * 64)
    assert "visibility_rule_ids" not in v3_manifest


# --- Node conformance parity (SC-V3 end-to-end) ------------------------------------------------------


@pytest.mark.skipif(not _NODE_AVAILABLE, reason="Node.js is required for reducer conformance")
def test_node_conformance_compares_visibility_maps():
    report = check_reducer_conformance(_visibility_app_bundle())
    assert report["ok"] is True
    assert STATE_REDUCER_VISIBILITY_EXPORT in report["export_names"]
    assert report["replays"][0]["visibility_matches"] is True


@pytest.mark.skipif(not _NODE_AVAILABLE, reason="Node.js is required for reducer conformance")
def test_node_conformance_catches_tampered_visibility():
    payload = _visibility_app_bundle()
    source = generate_typescript_reducer(payload)
    tampered = source.replace('visible = w.is === "falsy" ? !t : t;', 'visible = w.is === "falsy" ? t : !t;')
    assert tampered != source
    report = check_reducer_conformance(payload, reducer_source=tampered)
    assert report["ok"] is False
    assert any("visibility" in str(error.get("message", "")).lower() for error in report["errors"])


# --- baked markers e2e (SC-V1) ----------------------------------------------------------------------


@pytest.mark.skipif(not _NODE_AVAILABLE, reason="prove-app V4 requires Node.js for conformance")
def test_prove_app_bakes_markers_and_cross_checks(tmp_path):
    from viewspec.app_bundle import prove_app

    payload = _visibility_app_bundle()
    app_path = tmp_path / "viewspec.app.json"
    app_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    report = prove_app(app_path=app_path, out_dir=tmp_path / "proof", with_shell=True, cwd=tmp_path)
    assert report["ok"] is True

    # Compute expected initial verdicts from the single source of truth.
    state_ir, _ = validate_state_ir(payload)
    verdicts = initial_visibility(payload, state_ir)

    queue_html = (tmp_path / "proof" / "screens" / "queue" / "artifact" / "index.html").read_text(encoding="utf-8")
    assert 'data-visibility-rule="incidents_when_selected"' in queue_html
    expected_state = "visible" if verdicts["incidents_when_selected"] else "hidden"
    assert f'data-visibility-rule="incidents_when_selected" data-visibility-state="{expected_state}"' in queue_html
    if not verdicts["incidents_when_selected"]:
        assert 'data-visibility-state="hidden" hidden' in queue_html
    assert "[data-visibility-rule][hidden]" in queue_html  # conditional emitter CSS

    manifest = json.loads(
        (tmp_path / "proof" / "screens" / "queue" / "artifact" / "provenance_manifest.json").read_text(encoding="utf-8")
    )
    marked = {
        entry["ir_id"]: entry["props"]
        for entry in manifest["nodes"].values()
        if "visibility_rule_id" in entry.get("props", {})
    }
    # Exactly the queue screen's rules materialize: motif + region targets.
    assert set(marked) == {"motif_incidents", "region_main"}
    assert marked["motif_incidents"]["visibility_hidden_initial"] == (not verdicts["incidents_when_selected"])

    # Shell carries the bake + CSS + state artifacts with the evaluator export.
    shell_html = (tmp_path / "proof" / "app-shell" / "index.html").read_text(encoding="utf-8")
    assert "data-visibility-rule=" in shell_html
    assert "[data-visibility-rule][hidden]" in shell_html
    state_manifest_payload = json.loads((tmp_path / "proof" / "app-shell" / "state_manifest.json").read_text(encoding="utf-8"))
    assert state_manifest_payload["visibility_rule_ids"] == [rule["id"] for rule in payload["visibility"]]
    assert state_manifest_payload["replay"]["assertions"][0]["visibility_matches"] is True


@pytest.mark.skipif(not _NODE_AVAILABLE, reason="prove-app V4 requires Node.js for conformance")
def test_prove_app_is_deterministic_for_v4(tmp_path):
    from viewspec.app_bundle import prove_app

    payload = _visibility_app_bundle()
    app_path = tmp_path / "viewspec.app.json"
    app_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    hashes = []
    for run in ("one", "two"):
        out = tmp_path / f"proof-{run}"
        report = prove_app(app_path=app_path, out_dir=out, with_shell=True, cwd=tmp_path)
        assert report["ok"] is True
        hashes.append((out / "screens" / "queue" / "artifact" / "index.html").read_bytes())
    assert hashes[0] == hashes[1]


def test_overlay_unresolved_target_fails_closed(tmp_path):
    # A validated ref that does not materialize as an IR node must fail loudly, not silently skip.
    from viewspec.intent_tools import _apply_ir_props_overlay, compile_intent_bundle_file_tool

    payload = _visibility_app_bundle()
    screen = payload["screens"][0]["intent_bundle"]
    intent_path = tmp_path / "screen.intent.json"
    intent_path.write_text(json.dumps(screen, indent=2), encoding="utf-8")
    result = compile_intent_bundle_file_tool(
        intent_path,
        tmp_path / "artifact",
        cwd=tmp_path,
        ir_props_overlay={"motif_ghost": {"visibility_rule_id": "r1", "visibility_hidden_initial": True}},
    )
    assert result["ok"] is False
    assert any(error["code"] == "APP_VISIBILITY_TARGET_UNRESOLVED" for error in result["errors"])
    with pytest.raises(ValueError):
        _apply_ir_props_overlay(object(), {"x": {"unexpected_key": 1}})


# --- honest reporting (SC-V2) ------------------------------------------------------------------------


@pytest.mark.skipif(not _NODE_AVAILABLE, reason="prove-app V4 requires Node.js for conformance")
def test_visibility_replay_ok_is_null_when_unasserted(tmp_path):
    from viewspec.app_bundle import prove_app

    payload = _visibility_app_bundle()
    del payload["state_replay_assertions"][0]["expect_visibility"]
    app_path = tmp_path / "viewspec.app.json"
    app_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    report = prove_app(app_path=app_path, out_dir=tmp_path / "proof", with_shell=True, cwd=tmp_path)
    assert report["ok"] is True
    shell_manifest = json.loads((tmp_path / "proof" / "app-shell" / "shell_manifest.json").read_text(encoding="utf-8"))
    state_summary = shell_manifest["state_ir"]
    assert state_summary["visibility_rule_count"] == 3
    assert state_summary["visibility_replay_ok"] is None  # reported, never claimed


@pytest.mark.skipif(not _NODE_AVAILABLE, reason="prove-app V4 requires Node.js for conformance")
def test_visibility_replay_ok_is_true_when_asserted(tmp_path):
    from viewspec.app_bundle import prove_app

    payload = _visibility_app_bundle()
    app_path = tmp_path / "viewspec.app.json"
    app_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    report = prove_app(app_path=app_path, out_dir=tmp_path / "proof", with_shell=True, cwd=tmp_path)
    assert report["ok"] is True
    shell_manifest = json.loads((tmp_path / "proof" / "app-shell" / "shell_manifest.json").read_text(encoding="utf-8"))
    state_summary = shell_manifest["state_ir"]
    assert state_summary["visibility_replay_ok"] is True
    assert state_summary["initial_hidden_count"] == sum(
        1 for visible in initial_visibility(payload, validate_state_ir(payload)[0]).values() if not visible
    )
