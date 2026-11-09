"""AppBundle schema_version 5: bounded expressions on top of the V4 contract.

Covers the version gate (V1 to V4 bundles that use a V5 construct fail with one code), composed
visibility (all, any, not, depth and leaf caps), multi-value state text with closed formatters,
Python replay against generated-reducer conformance in Node, the static shell and React targets,
and the agent prompt section. The canonical fixture is tests/fixtures/app_bundle_v5_incident_console.json.
"""

from __future__ import annotations

import copy
import json
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_app_state_text import _state_text_app_bundle  # noqa: E402
from test_app_visibility import _visibility_app_bundle  # noqa: E402

from viewspec.app_agent_schema import APPBUNDLE_V5_PROMPT_SECTION  # noqa: E402
from viewspec.app_bundle import (  # noqa: E402
    compile_app,
    prove_app,
    starter_react_app_bundle,
    validate_app_text,
)
from viewspec.app_numeric import generate_numeric_typescript  # noqa: E402
from viewspec.app_shell import APP_SHELL_MAX_STATE_JS_BYTES, _app_shell_state_runtime_script  # noqa: E402
from viewspec.app_validation import APP_BUNDLE_SUPPORTED_SCHEMA_VERSIONS  # noqa: E402
from viewspec.app_versions import (  # noqa: E402
    app_has_interactive_state,
    app_has_state_text,
    app_has_visibility,
    app_is_v5,
)
from viewspec.state_ir import (  # noqa: E402
    APP_STATE_MAX_REDUCER_BYTES,
    check_reducer_conformance,
    evaluate_selectors,
    evaluate_state_text,
    evaluate_visibility,
    generate_browser_reducer_script,
    generate_javascript_reducer,
    generate_typescript_reducer,
    initial_state,
    initial_state_text,
    initial_visibility,
    normalize_state_ir,
    replay_state_assertions,
    validate_state_ir,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "app_bundle_v5_incident_console.json"
NODE = shutil.which("node")
NODE_REASON = "Node.js is not on PATH, so generated-reducer conformance cannot run."
TSC = ROOT / "src" / "viewspec" / "host_verify_template" / "node_modules" / ".bin" / "tsc"
V5_CODE = "APP_V5_FEATURE_REQUIRES_SCHEMA_VERSION_5"
_INLINE_HANDLER_RE = re.compile(r"\son[a-z]+\s*=", re.IGNORECASE)


def _v5() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _mutated(payload: dict[str, Any], mutate) -> dict[str, Any]:
    clone = copy.deepcopy(payload)
    mutate(clone)
    return clone


def _codes(payload: dict[str, Any]) -> set[str]:
    return {issue["code"] for issue in validate_app_text(json.dumps(payload))["issues"]}


def _rule(payload: dict[str, Any], section: str, rule_id: str) -> dict[str, Any]:
    return next(rule for rule in payload[section] if rule["id"] == rule_id)


# --- contract acceptance ------------------------------------------------------------------------------


def test_fixture_validates_as_schema_version_5():
    result = validate_app_text(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert result["ok"] is True, result["issues"]
    assert result["app_schema_version"] == 5
    assert result["resource_binding"] == "fixture_readonly_v0"
    assert result["state_ir"]["visibility_rule_count"] == 2
    assert result["state_ir"]["state_text_rule_count"] == 4
    assert 5 in APP_BUNDLE_SUPPORTED_SCHEMA_VERSIONS


def test_capability_predicates_name_the_versions_that_carry_each_feature():
    for version, state, visibility, v5 in ((1, False, False, False), (3, True, False, False), (4, True, True, False)):
        payload = {"schema_version": version}
        assert (app_has_interactive_state(payload), app_has_visibility(payload), app_is_v5(payload)) == (
            state,
            visibility,
            v5,
        )
    assert app_is_v5({"schema_version": 5}) and app_has_visibility({"schema_version": 5})
    assert app_has_state_text({"schema_version": 5, "state_text": []})
    assert not app_has_state_text({"schema_version": 3, "state_text": []})
    assert not app_has_visibility({"schema_version": [4]})  # unhashable values never match


def test_unhashable_schema_version_is_reported_instead_of_crashing():
    result = validate_app_text(json.dumps({"schema_version": [5]}))
    assert result["ok"] is False
    assert "APP_SCHEMA_VERSION_REQUIRED" in {issue["code"] for issue in result["issues"]}


@pytest.mark.parametrize(
    "factory", [_visibility_app_bundle, _state_text_app_bundle, lambda: starter_react_app_bundle("internal_tool")]
)
def test_v5_accepts_every_v4_construct_with_the_same_verdicts(factory):
    v4 = factory()
    v5 = _mutated(v4, lambda p: p.update(schema_version=5))
    assert validate_app_text(json.dumps(v5))["ok"] is True
    v4_ir, _ = validate_state_ir(v4)
    v5_ir, _ = validate_state_ir(v5)
    assert v5_ir is not None and v5_ir.bounded_expressions is True
    assert initial_visibility(v5, v5_ir) == initial_visibility(v4, v4_ir)
    if "state_text" in v4:
        assert initial_state_text(v5, v5_ir) == initial_state_text(v4, v4_ir)
    assert replay_state_assertions(v5)["ok"] is True


# --- the version gate ---------------------------------------------------------------------------------


@pytest.mark.parametrize("version", [1, 2, 3, 4])
def test_earlier_versions_name_the_v5_gate(version):
    codes = _codes(_mutated(_v5(), lambda p: p.update(schema_version=version)))
    assert V5_CODE in codes
    if version == 4:
        # V4 admits visibility and state_text, so the gate is the only failure: one code, one fix.
        assert codes == {V5_CODE}


@pytest.mark.parametrize(
    ("label", "mutate"),
    [
        (
            "composed_when",
            lambda p: p["visibility"][0].update(when={"not": {"state": "selected_incident", "is": "truthy"}}),
        ),
        (
            "values_form",
            lambda p: p.update(
                state_text=[
                    {
                        "id": "selected_text",
                        "screen_id": "queue",
                        "target_ref": "binding:inc_1043_status",
                        "template": "Selected {selected}",
                        "values": {"selected": {"state": "selected_incident"}},
                    }
                ]
            ),
        ),
        (
            "format_on_value_form",
            lambda p: p.update(
                state_text=[
                    {
                        "id": "selected_text",
                        "screen_id": "queue",
                        "target_ref": "binding:inc_1043_status",
                        "state": "selected_incident",
                        "template": "Selected {value}",
                        "format": {"format": "integer"},
                    }
                ]
            ),
        ),
    ],
)
def test_v4_bundle_with_one_v5_construct_fails_with_the_gate_code(label, mutate):
    payload = _mutated(_visibility_app_bundle(), mutate)
    result = validate_app_text(json.dumps(payload))
    assert result["ok"] is False
    issues = [issue for issue in result["issues"] if issue["code"] == V5_CODE]
    assert len(issues) == 1, (label, result["issues"])
    assert "schema_version to 5" in issues[0]["suggestion"]
    assert _codes(_mutated(payload, lambda p: p.update(schema_version=5))) == set()


# --- composed visibility --------------------------------------------------------------------------------

LEAF_ACKED = {"state": "acked", "is": "truthy"}
LEAF_ACTIVE = {"selector": "active", "is": "non_empty"}
LEAF_PICKED = {"state": "selected_incident", "equals": "inc_1043"}


def _bundle_with_conditions(conditions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    targets = iter(
        [
            "binding:inc_1042_id",
            "binding:inc_1042_severity",
            "binding:inc_1042_status",
            "binding:inc_1043_severity",
            "binding:summary_label",
            "motif:summary",
        ]
    )
    payload = _v5()
    payload["visibility"] += [
        {"id": rule_id, "screen_id": "queue", "target_ref": next(targets), "when": when}
        for rule_id, when in conditions.items()
    ]
    return payload


COMPOSED = {
    "rule_all": {"all": [LEAF_ACKED, LEAF_PICKED]},
    "rule_any": {"any": [LEAF_ACKED, {"selector": "active", "is": "empty"}]},
    "rule_not": {"not": LEAF_ACKED},
    "rule_depth_3": {"not": {"any": [LEAF_ACKED, {"state": "selected_incident", "equals": "zzz"}]}},
    "rule_mixed_depth_3": {"any": [{"all": [LEAF_ACKED, LEAF_ACTIVE]}, {"not": LEAF_PICKED}]},
}


@pytest.mark.parametrize(
    ("acked", "selected", "expected"),
    [
        (
            False,
            None,
            {"rule_all": False, "rule_any": False, "rule_not": True, "rule_depth_3": True, "rule_mixed_depth_3": True},
        ),
        (
            True,
            None,
            {"rule_all": False, "rule_any": True, "rule_not": False, "rule_depth_3": False, "rule_mixed_depth_3": True},
        ),
        (
            True,
            "inc_1043",
            {"rule_all": True, "rule_any": True, "rule_not": False, "rule_depth_3": False, "rule_mixed_depth_3": True},
        ),
        (
            False,
            "inc_1043",
            {"rule_all": False, "rule_any": False, "rule_not": True, "rule_depth_3": True, "rule_mixed_depth_3": False},
        ),
    ],
)
def test_all_any_not_and_depth_3_evaluate_as_boolean_logic(acked, selected, expected):
    payload = _bundle_with_conditions(COMPOSED)
    state_ir, issues = validate_state_ir(payload)
    assert not issues, issues
    state = initial_state(payload, state_ir)
    state["acked"] = acked
    state["selected_incident"] = selected
    verdicts = evaluate_visibility(state, evaluate_selectors(state, state_ir), state_ir)
    assert {rule_id: verdicts[rule_id] for rule_id in expected} == expected


@pytest.mark.skipif(NODE is None, reason=NODE_REASON)
def test_composed_visibility_replays_and_conforms_in_node():
    payload = _bundle_with_conditions(COMPOSED)
    payload["state_replay_assertions"].append(
        {
            "id": "acknowledge_only",
            "events": [{"mutation_id": "acknowledge_state"}],
            "expect_state": {"acked": True},
            "expect_selectors": {},
            "expect_visibility": {
                "rule_all": False,
                "rule_any": True,
                "rule_not": False,
                "rule_depth_3": False,
                "rule_mixed_depth_3": True,
                "attention": True,
                "escalation": False,
            },
        }
    )
    replay = replay_state_assertions(payload)
    assert replay["ok"] is True, replay["errors"]
    report = check_reducer_conformance(payload)
    assert report["ok"] is True, report["errors"]
    assert all(entry["visibility_matches"] for entry in report["replays"])


def _composed(when: Any) -> dict[str, Any]:
    return _mutated(_v5(), lambda p: _rule(p, "visibility", "escalation").update(when=when))


def test_depth_4_condition_is_rejected():
    when = {"not": {"all": [{"any": [LEAF_ACKED, LEAF_ACTIVE]}, LEAF_PICKED]}}
    assert "APP_VISIBILITY_CONDITION_TOO_DEEP" in _codes(_composed(when))


def test_depth_3_condition_is_accepted_and_one_more_level_is_not():
    depth_3 = {"all": [LEAF_ACKED, {"any": [LEAF_ACTIVE, LEAF_PICKED]}]}
    assert _codes(_composed(depth_3)) == set()
    assert "APP_VISIBILITY_CONDITION_TOO_DEEP" in _codes(_composed({"not": depth_3}))


def test_nine_leaves_are_rejected_and_eight_are_accepted():
    eight = {"any": [{"all": [LEAF_ACKED] * 4}, {"all": [LEAF_ACTIVE] * 4}]}
    nine = {"any": [{"all": [LEAF_ACKED] * 5}, {"all": [LEAF_ACTIVE] * 4}]}
    assert _codes(_composed(eight)) == set()
    assert "APP_VISIBILITY_CONDITION_TOO_MANY_LEAVES" in _codes(_composed(nine))


@pytest.mark.parametrize(
    ("when", "code"),
    [
        ({"all": [LEAF_ACKED]}, "APP_VISIBILITY_CONDITION_ARITY_INVALID"),
        ({"any": [LEAF_ACKED] * 9}, "APP_VISIBILITY_CONDITION_ARITY_INVALID"),
        ({"all": []}, "APP_VISIBILITY_CONDITION_ARITY_INVALID"),
        ({"all": LEAF_ACKED}, "APP_VISIBILITY_CONDITION_INVALID"),
        ({"not": [LEAF_ACKED]}, "APP_VISIBILITY_CONDITION_INVALID"),
        ({"all": [LEAF_ACKED, LEAF_ACTIVE], "any": [LEAF_ACKED, LEAF_ACTIVE]}, "APP_VISIBILITY_CONDITION_INVALID"),
        ({"not": LEAF_ACKED, "state": "acked"}, "APP_VISIBILITY_CONDITION_INVALID"),
        ({"all": [LEAF_ACKED, None]}, "APP_VISIBILITY_CONDITION_INVALID"),
        ({"all": [LEAF_ACKED, "acked"]}, "APP_VISIBILITY_CONDITION_INVALID"),
        (None, "APP_VISIBILITY_CONDITION_INVALID"),
        ({"all": [LEAF_ACKED, {"state": "ghost", "is": "truthy"}]}, "APP_VISIBILITY_STATE_MISSING"),
        ({"not": {"selector": "ghost", "is": "empty"}}, "APP_VISIBILITY_SELECTOR_MISSING"),
        ({"any": [LEAF_ACKED, {"state": "acked", "is": "maybe"}]}, "APP_VISIBILITY_CONDITION_INVALID"),
    ],
)
def test_invalid_composed_conditions_fail_closed(when, code):
    assert code in _codes(_composed(when))


def test_nested_leaf_issues_name_the_nested_path():
    payload = _composed({"all": [LEAF_ACKED, {"not": {"state": "ghost", "is": "truthy"}}]})
    issues = validate_app_text(json.dumps(payload))["issues"]
    assert [issue["path"] for issue in issues if issue["code"] == "APP_VISIBILITY_STATE_MISSING"] == [
        "$.visibility[0].when.all[1].not.state"
    ]


# --- multi-value state text -----------------------------------------------------------------------------


def _text_rule(update: dict[str, Any], rule_id: str = "open_summary") -> dict[str, Any]:
    return _mutated(_v5(), lambda p: _rule(p, "state_text", rule_id).update(update))


@pytest.mark.parametrize(
    ("update", "code"),
    [
        ({"values": []}, "APP_STATE_TEXT_VALUES_INVALID"),
        ({"values": {}}, "APP_STATE_TEXT_VALUES_INVALID"),
        ({"values": {"open": "active"}}, "APP_STATE_TEXT_VALUES_INVALID"),
        ({"values": {"open": {"selector": "active"}}}, "APP_STATE_TEXT_VALUES_INVALID"),
        ({"values": {"open": {"selector": "active", "measure": "sum"}}}, "APP_STATE_TEXT_VALUES_INVALID"),
        (
            {"values": {"open": {"state": "spend", "selector": "active", "measure": "count"}}},
            "APP_STATE_TEXT_VALUES_INVALID",
        ),
        ({"values": {"open": {"state": "spend", "path": "a.b"}}}, "APP_STATE_TEXT_VALUES_INVALID"),
        ({"template": "{open} open"}, "APP_STATE_TEXT_VALUES_MISMATCH"),
        ({"template": "{open} of {total}, {spend} and {extra}"}, "APP_STATE_TEXT_VALUES_MISMATCH"),
        ({"template": "{open} and {open}"}, "APP_STATE_TEXT_TEMPLATE_INVALID"),
        ({"template": "{open} {total} {spend} {{x}}"}, "APP_STATE_TEXT_TEMPLATE_INVALID"),
        ({"template": "{Open} {total} {spend}"}, "APP_STATE_TEXT_TEMPLATE_INVALID"),
        ({"template": "no placeholders"}, "APP_STATE_TEXT_TEMPLATE_INVALID"),
        ({"template": "{open}" + "x" * 512 + "{total}{spend}"}, "APP_STATE_TEXT_TEMPLATE_TOO_LARGE"),
        ({"values": {"open": {"selector": "ghost", "measure": "count"}}}, "APP_STATE_TEXT_SELECTOR_MISSING"),
        ({"values": {"open": {"state": "ghost"}}}, "APP_STATE_TEXT_STATE_MISSING"),
        ({"values": {"open": {"state": "incidents_state"}}}, "APP_STATE_TEXT_STATE_UNSUPPORTED"),
        (
            {"values": {"open": {"state": "spend", "format": {"format": "decimal", "digits": 7}}}},
            "APP_STATE_TEXT_FORMAT_INVALID",
        ),
        (
            {"values": {"open": {"state": "spend", "format": {"format": "currency", "currency": "BTC"}}}},
            "APP_STATE_TEXT_FORMAT_INVALID",
        ),
        ({"values": {"open": {"state": "spend", "format": None}}}, "APP_STATE_TEXT_FORMAT_INVALID"),
        ({"state": "spend"}, "APP_STATE_UNKNOWN_FIELD"),
        ({"target_ref": "binding:ghost"}, "APP_STATE_TEXT_TARGET_MISSING"),
    ],
)
def test_invalid_multi_value_rules_fail_closed(update, code):
    assert code in _codes(_text_rule(update))


def test_nine_placeholders_are_rejected():
    names = [f"p{index}" for index in range(9)]
    update = {
        "template": " ".join(f"{{{name}}}" for name in names),
        "values": {name: {"state": "spend"} for name in names},
    }
    codes = _codes(_text_rule(update))
    assert {"APP_STATE_TEXT_TEMPLATE_INVALID", "APP_STATE_TEXT_VALUES_INVALID"} <= codes


def test_count_sources_need_collection_selectors_on_the_rule_screen():
    def scalar_selector(p):
        p["selectors"].append({"id": "spend_view", "source_state": "spend", "ops": []})
        _rule(p, "state_text", "open_summary")["values"]["open"] = {"selector": "spend_view", "measure": "count"}

    assert "APP_STATE_TEXT_SELECTOR_SOURCE_UNSUPPORTED" in _codes(_mutated(_v5(), scalar_selector))

    def detail_scoped(p):
        p["state"].append(
            {
                "id": "detail_rows",
                "kind": "collection",
                "scope": "screen",
                "screen_id": "detail",
                "initial": {"value": []},
            }
        )
        p["selectors"].append({"id": "detail_view", "source_state": "detail_rows", "ops": []})
        _rule(p, "state_text", "open_summary")["values"]["open"] = {"selector": "detail_view", "measure": "count"}

    assert "APP_STATE_TEXT_STATE_SCOPE_MISMATCH" in _codes(_mutated(_v5(), detail_scoped))


def test_single_value_rules_take_an_optional_format_in_v5_only():
    payload = _v5()
    assert _rule(payload, "state_text", "spend_summary")["format"] == {"format": "decimal", "digits": 2}
    bad = _mutated(payload, lambda p: _rule(p, "state_text", "spend_summary").update(format={"format": "date"}))
    assert "APP_STATE_TEXT_FORMAT_INVALID" in _codes(bad)


def test_fixture_text_replays_and_renders_formatted_values():
    payload = _v5()
    state_ir, _ = validate_state_ir(payload)
    assert initial_state_text(payload, state_ir) == {
        "open_summary": "1 open of 2, $1,234.50 spent",
        "budget_summary": "Budget used: 12.5%",
        "spend_summary": "Spend: 1,234.50",
        "opened_summary": "Opened 2023-11-14",
    }
    replay = replay_state_assertions(payload)
    assert replay["ok"] is True, replay["errors"]
    assert all(entry["text_matches"] and entry["visibility_matches"] for entry in replay["assertions"])
    flips = {entry["id"]: entry for entry in payload["state_replay_assertions"]}
    assert flips["initial_view"]["expect_visibility"]["escalation"] is True
    assert flips["triage_then_acknowledge"]["expect_visibility"]["escalation"] is False
    assert flips["triage_then_acknowledge"]["expect_text"]["open_summary"] == "2 open of 2, $1,234.50 spent"


def test_text_replay_mismatch_fails_closed():
    payload = _mutated(
        _v5(), lambda p: p["state_replay_assertions"][2]["expect_text"].update(budget_summary="Budget used: 18.7%")
    )
    report = replay_state_assertions(payload)
    assert report["ok"] is False
    mismatch = next(error for error in report["errors"] if error["code"] == "APP_STATE_TEXT_REPLAY_MISMATCH")
    assert mismatch["expected"] == "Budget used: 18.7%"
    assert mismatch["actual"] == "Budget used: 18.8%"


@pytest.mark.skipif(NODE is None, reason=NODE_REASON)
def test_fixture_reducer_conforms_in_node():
    report = check_reducer_conformance(_v5())
    assert report["ok"] is True, report["errors"]
    assert [entry["status"] for entry in report["replays"]] == ["passed"] * 3
    assert all(entry["text_matches"] and entry["visibility_matches"] for entry in report["replays"])


def test_normalized_contract_keeps_both_rule_forms():
    contract = normalize_state_ir(_v5()).contract
    by_id = {rule["id"]: rule for rule in contract["state_text"]}
    assert set(by_id["open_summary"]) == {"id", "screen_id", "target_ref", "template", "values"}
    assert set(by_id["spend_summary"]) == {"id", "screen_id", "target_ref", "state", "template", "format"}
    assert contract["visibility"][1]["when"]["any"][0]["all"][0] == LEAF_ACKED


def test_v5_rejects_integers_beyond_two_pow_53_as_v4_does():
    def big(p):
        _rule(p, "state", "spend")["initial"] = {"value": 2**53 + 1}

    issues = validate_app_text(json.dumps(_mutated(_v5(), big)))["issues"]
    assert [(issue["code"], issue["path"]) for issue in issues] == [
        ("APP_STATE_INTEGER_OUT_OF_SAFE_RANGE", "$.state[3].initial.value")
    ]
    assert _codes(_mutated(_v5(), lambda p: _rule(p, "state", "spend").update(initial={"value": 2**53}))) == set()
    # Every schema version rejects them now: the V4 runtime reads the same doubles.
    v4 = _state_text_app_bundle()
    v4["state"][1]["initial"] = {"value": 2**53 + 1}
    assert [(issue["code"], issue["path"]) for issue in validate_app_text(json.dumps(v4))["issues"]] == [
        ("APP_STATE_INTEGER_OUT_OF_SAFE_RANGE", "$.state[1].initial.value")
    ]


# --- Python equals JavaScript on arbitrary state shapes ------------------------------------------------


_EVALUATE_RUNNER = """
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
const mod = await import(pathToFileURL(process.argv[2]).href);
const states = JSON.parse(readFileSync(process.argv[3], "utf8"));
console.log(JSON.stringify(states.map((state) => ({
  visibility: mod.evaluateViewSpecVisibility(state),
  text: mod.evaluateViewSpecText(state),
}))));
""".lstrip()


def _random_states(payload: dict[str, Any], seed: int, count: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    state_ir, _ = validate_state_ir(payload)
    base = initial_state(payload, state_ir)
    scalars: list[Any] = [
        None,
        True,
        False,
        0,
        -0.0,
        1,
        "",
        "inc_1043",
        "zzz",
        "$&",
        "2024-02-29T08:00:00Z",
        [],
        [0],
        {},
        {"k": 1},
        1e21,
        1e-7,
        1234.5,
        -0.005,
        0.1875,
        1700000000000,
        -62167219200001,
        2.675,
    ]
    rows = [
        [],
        base["incidents_state"],
        [{"id": "inc_1042", "status": "investigating"}, {"id": "x", "status": "investigating"}],
        "not a list",
        None,
        [1, "two", None],
    ]
    states = []
    for _ in range(count):
        state = copy.deepcopy(base)
        for key in ("selected_incident", "acked", "spend", "budget_used", "opened_at"):
            state[key] = rng.choice(scalars)
        state["incidents_state"] = copy.deepcopy(rng.choice(rows))
        states.append(state)
    return states


@pytest.mark.skipif(NODE is None, reason=NODE_REASON)
def test_generated_evaluators_match_python_on_arbitrary_states(tmp_path):
    payload = _bundle_with_conditions(COMPOSED)
    state_ir, issues = validate_state_ir(payload)
    assert not issues
    states = _random_states(payload, seed=5, count=300)
    module = tmp_path / "state_reducer.mjs"
    module.write_text(generate_javascript_reducer(payload), encoding="utf-8")
    runner = tmp_path / "runner.mjs"
    runner.write_text(_EVALUATE_RUNNER, encoding="utf-8")
    states_path = tmp_path / "states.json"
    states_path.write_text(json.dumps(states), encoding="utf-8")
    completed = subprocess.run(
        [str(NODE), str(runner), str(module), str(states_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    for state, js in zip(states, json.loads(completed.stdout), strict=True):
        selectors = evaluate_selectors(state, state_ir)
        assert js["visibility"] == evaluate_visibility(state, selectors, state_ir), state
        assert js["text"] == evaluate_state_text(state, state_ir), state


# --- generated artifacts --------------------------------------------------------------------------------


def test_v4_reducers_carry_no_v5_code():
    for payload in (_visibility_app_bundle(), _state_text_app_bundle(), starter_react_app_bundle("internal_tool")):
        source = generate_typescript_reducer(payload)
        # Escaping <, >, and & in reducer JSON is not V5 code: every schema version does it.
        for token in ("visibilityHolds", "formatStateValue", "vsNumberText", "segments"):
            assert token not in source


def test_v5_reducer_emits_the_formatter_kernel_only_when_a_format_is_used():
    with_formats = generate_typescript_reducer(_v5())
    assert "function vsNumberText" in with_formats and "function vsDateIso" in with_formats

    def drop_formats(p):
        for rule in p["state_text"]:
            rule.pop("format", None)
            for source in rule.get("values", {}).values():
                source.pop("format", None)

    without_formats = generate_typescript_reducer(_mutated(_v5(), drop_formats))
    assert "function formatStateValue" in without_formats
    assert "vsNumberText" not in without_formats and "viewspecCurrencies" not in without_formats


def test_v5_reducer_escapes_markup_and_renders_it_literally():
    def markup(p):
        _rule(p, "state_text", "budget_summary")["template"] = "</script><b>{used}</b> & more"
        p["state_replay_assertions"][0]["expect_text"]["budget_summary"] = "</script><b>12.5%</b> & more"
        p["state_replay_assertions"][2]["expect_text"]["budget_summary"] = "</script><b>18.8%</b> & more"

    payload = _mutated(_v5(), markup)
    assert replay_state_assertions(payload)["ok"] is True
    browser = generate_browser_reducer_script(payload)
    assert "</script" not in browser and "<b>" not in browser
    assert "\\u003c/script\\u003e\\u003cb\\u003e" in browser


def test_v5_runtime_sizes_stay_within_the_caps():
    payload = _v5()
    reducer = generate_typescript_reducer(payload)
    state_js = generate_browser_reducer_script(payload) + _app_shell_state_runtime_script()
    assert len(reducer.encode("utf-8")) <= APP_STATE_MAX_REDUCER_BYTES
    assert len(state_js.encode("utf-8")) <= APP_SHELL_MAX_STATE_JS_BYTES
    for script in (reducer, state_js):
        assert not _INLINE_HANDLER_RE.search(script)
        for token in ("http:", "https:", "url(", "Worker(", "importScripts"):
            assert token not in script


@pytest.mark.skipif(
    NODE is None or not TSC.is_file(), reason="The pinned TypeScript compiler seed or Node.js is not installed."
)
def test_v5_reducer_typechecks_strictly(tmp_path):
    payload = _bundle_with_conditions(COMPOSED)
    (tmp_path / "state_reducer.ts").write_text(generate_typescript_reducer(payload), encoding="utf-8")
    (tmp_path / "viewspec_numeric.ts").write_text(generate_numeric_typescript(payload), encoding="utf-8")
    completed = subprocess.run(
        [str(TSC), "--strict", "--noEmit", "--skipLibCheck", "--target", "ES2022", "state_reducer.ts"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.skipif(NODE is None, reason=NODE_REASON)
def test_static_shell_bakes_and_carries_the_v5_runtime(tmp_path):
    proof = prove_app(app_path=FIXTURE_PATH, out_dir=tmp_path / "proof", with_shell=True, cwd=tmp_path)
    assert proof["ok"] is True, proof.get("errors")
    html = (tmp_path / "proof" / "app-shell" / "index.html").read_text(encoding="utf-8")
    assert html.count("const ViewSpecStateRuntime = (() =>") == 1
    assert html.count("function visibilityHolds") == 1
    assert html.count("function vsNumberText") == 1
    assert 'data-visibility-rule="escalation" data-visibility-state="visible"' in html
    assert 'data-visibility-rule="attention" data-visibility-state="hidden"' in html
    assert "1 open of 2, $1,234.50 spent" in html
    assert "Budget used: 12.5%" in html
    manifest = json.loads((tmp_path / "proof" / "app-shell" / "shell_manifest.json").read_text(encoding="utf-8"))
    assert manifest["app_schema_version"] == 5
    assert manifest["state_runtime"]["state_js_bytes"] <= APP_SHELL_MAX_STATE_JS_BYTES
    assert manifest["state_ir"]["visibility_replay_ok"] is True
    assert manifest["state_ir"]["state_text_replay_ok"] is True
    # Every inline script still parses once the V5 kernel is inlined.
    scripts = [
        body
        for attrs, body in re.findall(r"<script\b([^>]*)>([\s\S]*?)</script>", html)
        if "application/json" not in attrs
    ]
    assert len(scripts) == 3
    for index, body in enumerate(scripts):
        script_path = tmp_path / f"inline-{index}.js"
        script_path.write_text(body, encoding="utf-8")
        checked = subprocess.run(
            [str(NODE), "--check", str(script_path)], capture_output=True, text=True, timeout=30, check=False
        )
        assert checked.returncode == 0, checked.stderr


@pytest.mark.skipif(NODE is None, reason=NODE_REASON)
def test_react_target_compiles_v5_deterministically(tmp_path):
    outputs = []
    for run in ("one", "two"):
        out_dir = tmp_path / run
        result = compile_app(FIXTURE_PATH, out_dir=out_dir, target="react-tailwind-app", cwd=tmp_path)
        assert result["ok"] is True, result.get("errors")
        outputs.append(
            {
                path.relative_to(out_dir).as_posix(): path.read_bytes()
                for path in sorted(out_dir.rglob("*"))
                if path.is_file()
            }
        )
    assert outputs[0] == outputs[1]
    app_source = outputs[0]["src/ViewSpecApp.tsx"].decode("utf-8")
    assert "evaluateViewSpecVisibility, evaluateViewSpecText" in app_source
    screen = outputs[0]["src/screens/queue/ViewSpecView.tsx"].decode("utf-8")
    assert 'renderValue(stateText["open_summary"], "1 open of 2, $1,234.50 spent")' in screen
    spec = outputs[0]["tests/viewspec-app.spec.ts"].decode("utf-8")
    assert '"text": "2 open of 2, $1,234.50 spent"' in spec
    assert '"ruleId": "attention", "screenId": "queue", "visible": true' in spec


# --- the agent prompt section ---------------------------------------------------------------------------


def test_prompt_section_is_bounded_and_its_examples_are_the_fixture_rules():
    assert len(APPBUNDLE_V5_PROMPT_SECTION.encode("utf-8")) <= 1200
    assert "schema_version 5" in APPBUNDLE_V5_PROMPT_SECTION
    examples = [json.loads(line) for line in APPBUNDLE_V5_PROMPT_SECTION.splitlines() if line.startswith("{")]
    assert len(examples) == 2
    payload = _v5()
    visibility, text = examples
    assert visibility == _rule(payload, "visibility", visibility["id"])
    assert set(visibility["when"]) == {"all"}
    assert text == _rule(payload, "state_text", text["id"])
    assert any("format" in source for source in text["values"].values())
    assert len(text["values"]) >= 2
    for forbidden in ("Intl", "locale-aware", "expression language"):
        assert forbidden not in APPBUNDLE_V5_PROMPT_SECTION
