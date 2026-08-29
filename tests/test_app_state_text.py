from __future__ import annotations

import copy
import json
import shutil

import pytest

from viewspec.app_bundle import compile_app, prove_app, starter_react_app_bundle, validate_app_text
from viewspec.state_ir import (
    APP_STATE_TEXT_MAX_RULES,
    check_reducer_conformance,
    evaluate_state_text,
    generate_typescript_reducer,
    initial_state,
    replay_state_assertions,
    state_reducer_exports,
    validate_state_ir,
)


_NODE_AVAILABLE = shutil.which("node") is not None


def _state_text_app_bundle() -> dict:
    payload = starter_react_app_bundle("internal_tool")
    payload["state_text"] = [
        {
            "id": "selected_incident_text",
            "screen_id": "queue",
            "target_ref": "binding:inc_1043_status",
            "state": "selected_incident",
            "template": "Selected: {value}",
        }
    ]
    payload["state_replay_assertions"][0]["expect_text"] = {
        "selected_incident_text": "Selected: inc_1043"
    }
    return payload


def _codes(payload: dict) -> set[str]:
    result = validate_app_text(json.dumps(payload))
    return {str(issue["code"]) for issue in result.get("issues", [])}


def test_state_text_contract_validates_evaluates_and_replays() -> None:
    payload = _state_text_app_bundle()
    validation = validate_app_text(json.dumps(payload))
    assert validation["ok"] is True
    assert validation["state_ir"]["state_text_rule_count"] == 1

    state_ir, issues = validate_state_ir(payload)
    assert state_ir is not None
    assert not issues
    assert evaluate_state_text(initial_state(payload, state_ir), state_ir) == {
        "selected_incident_text": "Selected: "
    }
    replay = replay_state_assertions(payload)
    assert replay["ok"] is True
    assert replay["assertions"][0]["text_matches"] is True
    assert "evaluateViewSpecText" in state_reducer_exports(payload)
    assert "export function evaluateViewSpecText" in generate_typescript_reducer(payload)


@pytest.mark.parametrize(
    ("expected", "mutate"),
    [
        ("APP_STATE_TEXT_NOT_ARRAY", lambda p: p.update(state_text={})),
        ("APP_STATE_TEXT_RULE_NOT_OBJECT", lambda p: p["state_text"].append("bad")),
        ("APP_STATE_TEXT_TARGET_REF_INVALID", lambda p: p["state_text"][0].update(target_ref="motif:incidents")),
        ("APP_STATE_TEXT_TARGET_MISSING", lambda p: p["state_text"][0].update(target_ref="binding:ghost")),
        ("APP_STATE_TEXT_DUPLICATE_TARGET", lambda p: p["state_text"].append({**p["state_text"][0], "id": "duplicate"})),
        ("APP_STATE_TEXT_DUPLICATE_ID", lambda p: p["state_text"].append({**p["state_text"][0], "target_ref": "binding:inc_1042_status"})),
        ("APP_STATE_TEXT_STATE_MISSING", lambda p: p["state_text"][0].update(state="ghost")),
        ("APP_STATE_TEXT_STATE_UNSUPPORTED", lambda p: p["state_text"][0].update(state="incidents_state")),
        ("APP_STATE_TEXT_STATE_SCOPE_MISMATCH", lambda p: p["state_text"][0].update(screen_id="detail", target_ref="binding:inc_1042_status")),
        ("APP_STATE_TEXT_TEMPLATE_INVALID", lambda p: p["state_text"][0].update(template="Selected")),
        ("APP_STATE_TEXT_TEMPLATE_INVALID", lambda p: p["state_text"][0].update(template="{value} / {value}")),
        ("APP_STATE_TEXT_REPLAY_RULE_MISSING", lambda p: p["state_replay_assertions"][0]["expect_text"].update(ghost="x")),
        ("APP_STATE_TEXT_REPLAY_EXPECT_INVALID", lambda p: p["state_replay_assertions"][0]["expect_text"].update(selected_incident_text=1)),
        (
            "APP_STATE_TEXT_LIMIT_EXCEEDED",
            lambda p: p.update(
                state_text=[
                    {
                        "id": f"text_{index}",
                        "screen_id": "queue",
                        "target_ref": "binding:inc_1043_status",
                        "state": "selected_incident",
                        "template": "Selected: {value}",
                    }
                    for index in range(APP_STATE_TEXT_MAX_RULES + 1)
                ]
            ),
        ),
    ],
)
def test_invalid_state_text_contract_fails_closed(expected, mutate) -> None:
    payload = copy.deepcopy(_state_text_app_bundle())
    mutate(payload)
    assert expected in _codes(payload)


def test_v3_rejects_state_text_and_expect_text() -> None:
    payload = _state_text_app_bundle()
    payload["schema_version"] = 3
    payload.pop("visibility")
    payload["state_replay_assertions"][0].pop("expect_visibility")
    codes = _codes(payload)
    assert "APP_UNKNOWN_FIELD" in codes
    assert "APP_STATE_UNKNOWN_FIELD" in codes


@pytest.mark.skipif(not _NODE_AVAILABLE, reason="Node.js is required for reducer conformance")
def test_state_text_reducer_conformance_is_exact() -> None:
    payload = _state_text_app_bundle()
    report = check_reducer_conformance(payload)
    assert report["ok"] is True
    assert report["replays"][0]["text_matches"] is True


@pytest.mark.skipif(not _NODE_AVAILABLE, reason="AppBundle proof requires Node.js")
def test_static_and_react_targets_share_baked_and_live_state_text(tmp_path) -> None:
    payload = _state_text_app_bundle()
    app_path = tmp_path / "viewspec.app.json"
    app_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    proof_dir = tmp_path / "proof"
    proof = prove_app(app_path=app_path, out_dir=proof_dir, with_shell=True, cwd=tmp_path)
    assert proof["ok"] is True, json.dumps(proof, indent=2)
    static_html = (proof_dir / "app-shell" / "index.html").read_text(encoding="utf-8")
    assert 'data-state-text-id="selected_incident_text"' in static_html
    assert "Selected: " in static_html
    assert "evaluateViewSpecText(state)" in static_html
    assert "el.textContent = values[ruleId]" in static_html

    react_dir = tmp_path / "react"
    compiled = compile_app(app_path, out_dir=react_dir, target="react-tailwind-app", cwd=tmp_path)
    assert compiled["ok"] is True
    screen_source = (react_dir / "src" / "screens" / "queue" / "ViewSpecView.tsx").read_text(encoding="utf-8")
    app_source = (react_dir / "src" / "ViewSpecApp.tsx").read_text(encoding="utf-8")
    assert 'data-state-text-id={"selected_incident_text"}' in screen_source
    assert 'renderValue(stateText["selected_incident_text"], "Selected: ")' in screen_source
    assert "const stateText = React.useMemo(() => evaluateViewSpecText(state), [state]);" in app_source
    assert "stateText={stateText}" in app_source
