from __future__ import annotations

import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from viewspec.app_bundle import compile_app, prove_app, starter_react_app_bundle, validate_app_text
from viewspec.app_format import render_state_scalar
from viewspec.app_numeric import generate_numeric_typescript
from viewspec.state_ir import (
    APP_STATE_TEXT_MAX_RULES,
    check_reducer_conformance,
    evaluate_state_text,
    generate_javascript_reducer,
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
    payload["state_replay_assertions"][0]["expect_text"] = {"selected_incident_text": "Selected: inc_1043"}
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
    assert evaluate_state_text(initial_state(payload, state_ir), state_ir) == {"selected_incident_text": "Selected: "}
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
        (
            "APP_STATE_TEXT_DUPLICATE_TARGET",
            lambda p: p["state_text"].append({**p["state_text"][0], "id": "duplicate"}),
        ),
        (
            "APP_STATE_TEXT_DUPLICATE_ID",
            lambda p: p["state_text"].append({**p["state_text"][0], "target_ref": "binding:inc_1042_status"}),
        ),
        ("APP_STATE_TEXT_STATE_MISSING", lambda p: p["state_text"][0].update(state="ghost")),
        ("APP_STATE_TEXT_STATE_UNSUPPORTED", lambda p: p["state_text"][0].update(state="incidents_state")),
        (
            "APP_STATE_TEXT_STATE_SCOPE_MISMATCH",
            lambda p: p["state_text"][0].update(screen_id="detail", target_ref="binding:inc_1042_status"),
        ),
        ("APP_STATE_TEXT_TEMPLATE_INVALID", lambda p: p["state_text"][0].update(template="Selected")),
        ("APP_STATE_TEXT_TEMPLATE_INVALID", lambda p: p["state_text"][0].update(template="{value} / {value}")),
        (
            "APP_STATE_TEXT_REPLAY_RULE_MISSING",
            lambda p: p["state_replay_assertions"][0]["expect_text"].update(ghost="x"),
        ),
        (
            "APP_STATE_TEXT_REPLAY_EXPECT_INVALID",
            lambda p: p["state_replay_assertions"][0]["expect_text"].update(selected_incident_text=1),
        ),
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


# --- Python and JavaScript render the same text --------------------------------------------------

# Values on which the V4 bake and replay (Python) and the V4 runtime (the generated reducer) used to
# render different text: replacement patterns that String.prototype.replace expands, and numbers
# whose Python repr differs from JavaScript String().
_DIVERGENT_VALUES = [
    "$&",
    "a$'b",
    "a$`b",
    "$$",
    "$1",
    "$<name>",
    "total: $5 & $& more",
    1e-05,
    1.5e-07,
    0.000123,
    1e21,
    1.5e300,
    10**21,
    9007199254740993,
    2**60,
    10**400,
    -0.0,
    0.1 + 0.2,
    1e16,
    100,
    -42,
    True,
    False,
    None,
    "",
    "plain",
]

_EVALUATE_TEXT_RUNNER = """
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
const mod = await import(pathToFileURL(process.argv[2]).href);
const states = JSON.parse(readFileSync(process.argv[3], "utf8"));
console.log(JSON.stringify(states.map((state) => mod.evaluateViewSpecText(state))));
""".lstrip()


def _texts_for(values: list) -> tuple[dict, object, list[dict]]:
    payload = _state_text_app_bundle()
    state_ir, issues = validate_state_ir(payload)
    assert state_ir is not None and not issues
    states = [{**initial_state(payload, state_ir), "selected_incident": value} for value in values]
    return payload, state_ir, states


def test_v4_text_inserts_the_value_literally_and_renders_numbers_as_javascript_does() -> None:
    _, state_ir, states = _texts_for(_DIVERGENT_VALUES)
    for value, state in zip(_DIVERGENT_VALUES, states, strict=True):
        text = evaluate_state_text(state, state_ir)["selected_incident_text"]
        assert text == "Selected: " + render_state_scalar(value), value
    # The documented JavaScript String() forms, so the bake matches what the runtime shows later.
    _, state_ir, states = _texts_for([1e-05, 1.5e-07, 1e21, "$&"])
    assert [evaluate_state_text(state, state_ir)["selected_incident_text"] for state in states] == [
        "Selected: 0.00001",
        "Selected: 1.5e-7",
        "Selected: 1e+21",
        "Selected: $&",
    ]


@pytest.mark.skipif(not _NODE_AVAILABLE, reason="Node.js is not on PATH, so the generated reducer cannot run.")
def test_v4_generated_reducer_renders_the_same_text_as_python(tmp_path) -> None:
    payload, state_ir, states = _texts_for(_DIVERGENT_VALUES)
    module = tmp_path / "state_reducer.mjs"
    module.write_text(generate_javascript_reducer(payload), encoding="utf-8")
    runner = tmp_path / "runner.mjs"
    runner.write_text(_EVALUATE_TEXT_RUNNER, encoding="utf-8")
    states_path = tmp_path / "states.json"
    states_path.write_text(json.dumps(states), encoding="utf-8")
    completed = subprocess.run(
        [str(shutil.which("node")), str(runner), str(module), str(states_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    for value, state, js_text in zip(_DIVERGENT_VALUES, states, json.loads(completed.stdout), strict=True):
        assert js_text == evaluate_state_text(state, state_ir), value


_TSC = Path("src/viewspec/host_verify_template/node_modules/.bin/tsc").resolve()


@pytest.mark.skipif(
    not _TSC.is_file() or shutil.which("node") is None,
    reason="The pinned TypeScript compiler and Node.js are required: the tsc shim runs on Node.",
)
def test_v4_state_text_reducer_typechecks(tmp_path) -> None:
    payload = _state_text_app_bundle()
    source = tmp_path / "state_reducer.ts"
    source.write_text(generate_typescript_reducer(payload), encoding="utf-8")
    tmp_path.joinpath("viewspec_numeric.ts").write_text(generate_numeric_typescript(payload), encoding="utf-8")

    result = subprocess.run(
        [str(_TSC), "--strict", "--noEmit", "--skipLibCheck", "--target", "ES2022", str(source)],
        cwd=tmp_path,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )

    assert result.returncode == 0, result.stdout
