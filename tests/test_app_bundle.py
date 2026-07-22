from __future__ import annotations

import json
from copy import deepcopy

import pytest
from hypothesis import given, settings, strategies as st

import viewspec.app_bundle as app_bundle_module
from viewspec.app_bundle import (
    AGENT_APP_BUNDLE_SCHEMA,
    APP_BUNDLE_STATE_SCHEMA_VERSION,
    APP_BUNDLE_MAX_BYTES,
    APP_BUNDLE_MAX_EMBEDDED_INTENT_BYTES,
    APP_BUNDLE_RESOURCE_BINDING,
    APP_BUNDLE_RESOURCE_BINDING_READONLY,
    APP_BUNDLE_BINDING_SCOPE,
    APP_STATE_MANIFEST,
    APP_STATE_REDUCER,
    APP_SHELL_ROUTE_NAVIGATION,
    APP_SHELL_TARGET,
    app_semantic_change_lines,
    compile_app,
    compile_app_tool,
    diff_app_text,
    init_app_tool,
    prove_app,
    prove_app_tool,
    starter_app_bundle,
    validate_app_text,
)
from viewspec.agent import AGENT_INTENT_BUNDLE_SCHEMA
from viewspec.cli import _doctor_app_bundle_pipeline, _doctor_checks_ok, main as cli_main
from viewspec.intent_tools import starter_intent_bundle
from viewspec.local_tools import check_artifact_dir, file_hash
from viewspec.state_ir import (
    STATE_MANIFEST_SCHEMA_VERSION,
    STATE_REDUCER_EXPORTS,
    apply_event,
    check_reducer_conformance,
    evaluate_selectors,
    generate_javascript_reducer,
    generate_typescript_reducer,
    initial_state,
    normalize_state_ir,
    replay_state_assertions,
    state_contract_hash,
    state_event_schemas,
    validate_state_ir,
)


def _app_text(payload: dict) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


def _issue_codes(payload: dict) -> set[str]:
    return {issue["code"] for issue in payload["issues"]}


def _stateful_app_bundle() -> dict:
    app = starter_app_bundle("internal_tool", resource_binding="fixture_readonly_v0")
    app["schema_version"] = APP_BUNDLE_STATE_SCHEMA_VERSION
    queue_intent = app["screens"][0]["intent_bundle"]
    queue_intent["view_spec"]["actions"].append(
        {
            "id": "triage_incident",
            "kind": "submit",
            "label": "Triage",
            "target_region": "main",
            "target_ref": None,
            "payload_bindings": ["inc_1043_id"],
        }
    )
    app["interactive_state"] = "interactive_state_v0"
    app["state"] = [
        {
            "id": "incidents_state",
            "kind": "collection",
            "scope": "app",
            "initial": {"from_resource_view": {"screen_id": "queue", "view_id": "queue_incidents"}},
        },
        {"id": "selected_incident", "kind": "scalar", "scope": "screen", "screen_id": "queue", "initial": {"value": None}},
        {"id": "queue_flags", "kind": "record", "scope": "app", "initial": {"value": {"urgent": False, "count": 0}}},
        {"id": "selected_ids", "kind": "selection", "scope": "app", "initial": {"value": []}},
    ]
    app["mutations"] = [
        {
            "id": "triage_incident_state",
            "trigger": {"screen_id": "queue", "action_id": "triage_incident"},
            "ops": [
                {
                    "op": "patch",
                    "state": "incidents_state",
                    "item_id": {"from_payload": "inc_1043_id"},
                    "value": {"status": "investigating"},
                },
                {
                    "op": "move",
                    "state": "incidents_state",
                    "item_id": {"from_payload": "inc_1043_id"},
                    "to_index": 0,
                },
                {"op": "increment", "state": "queue_flags", "field": "count", "amount": 1},
                {"op": "toggle", "state": "queue_flags", "field": "urgent"},
                {"op": "set", "state": "selected_incident", "value": {"from_payload": "inc_1043_id"}},
                {"op": "append", "state": "selected_ids", "value": {"from_payload": "inc_1043_id"}},
            ],
        },
        {
            "id": "remove_incident_state",
            "trigger": {"screen_id": "queue", "action_id": "triage_incident"},
            "ops": [{"op": "remove", "state": "incidents_state", "item_id": {"from_payload": "inc_1043_id"}}],
        },
    ]
    app["selectors"] = [
        {
            "id": "active_incidents",
            "source_state": "incidents_state",
            "ops": [
                {"op": "filter_eq", "field": "status", "value": "investigating"},
                {"op": "sort_by", "field": "severity", "direction": "desc"},
                {"op": "slice", "start": 0, "end": 1},
            ],
        }
    ]
    app["state_replay_assertions"] = [
        {
            "id": "triage_replay",
            "events": [{"mutation_id": "triage_incident_state", "payload_values": {"inc_1043_id": "inc_1043"}}],
            "expect_state": {
                "incidents_state": [
                    {"id": "inc_1043", "severity": "medium", "status": "investigating"},
                    {"id": "inc_1042", "severity": "high", "status": "investigating"},
                ],
                "queue_flags": {"urgent": True, "count": 1.0},
                "selected_incident": "inc_1043",
                "selected_ids": ["inc_1043"],
            },
            "expect_selectors": {
                "active_incidents": [{"id": "inc_1043", "severity": "medium", "status": "investigating"}]
            },
        }
    ]
    return app


def _stateful_app_bundle_with_remove_replay() -> dict:
    app = _stateful_app_bundle()
    app["state_replay_assertions"].append(
        {
            "id": "remove_replay",
            "events": [{"mutation_id": "remove_incident_state", "payload_values": {"inc_1043_id": "inc_1043"}}],
            "expect_state": {
                "incidents_state": [{"id": "inc_1042", "severity": "high", "status": "investigating"}],
            },
            "expect_selectors": {
                "active_incidents": [{"id": "inc_1042", "severity": "high", "status": "investigating"}],
            },
        }
    )
    return app


def test_starter_app_bundle_validates_and_cli_writes(tmp_path, capsys):
    app = starter_app_bundle("internal_tool")
    validation = validate_app_text(_app_text(app))

    assert validation["ok"] is True
    assert validation["compile_check"] == "passed"
    assert validation["resource_binding"] == APP_BUNDLE_RESOURCE_BINDING
    assert validation["summary"]["screen_count"] == 2
    assert validation["route_assertions"] == {
        "all_routes_resolve": True,
        "all_screens_reachable": True,
        "root_route_resolves": True,
    }
    assert AGENT_APP_BUNDLE_SCHEMA["$id"] == "https://viewspec.dev/agent-app-bundle.schema.json"
    assert AGENT_APP_BUNDLE_SCHEMA["x-viewspec-resource-binding"] == "unbound_v0"
    assert AGENT_APP_BUNDLE_SCHEMA["x-viewspec-resource-bindings"] == [
        APP_BUNDLE_RESOURCE_BINDING,
        APP_BUNDLE_RESOURCE_BINDING_READONLY,
    ]

    out = tmp_path / "viewspec.app.json"
    assert cli_main(["init-app", "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8")) == app
    capsys.readouterr()

    assert cli_main(["validate-app", str(out), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["summary"]["id"] == "incident_console"


def test_bound_starter_app_bundle_validates_and_proves_fixture_readonly(tmp_path, capsys):
    app = starter_app_bundle("internal_tool", resource_binding="fixture_readonly_v0")
    validation = validate_app_text(_app_text(app))

    assert validation["ok"] is True
    assert validation["app_schema_version"] == 2
    assert validation["summary"]["schema_version"] == 2
    assert validation["resource_binding"] == APP_BUNDLE_RESOURCE_BINDING_READONLY
    assert validation["binding_scope"] == APP_BUNDLE_BINDING_SCOPE
    assert validation["resource_binding_validation"]["resource_view_count"] == 2
    assert validation["resource_binding_validation"]["assertion_count"] == 9

    app_path = tmp_path / "viewspec.app.json"
    app_path.write_text(_app_text(app), encoding="utf-8")
    compiled = compile_app(app_path, out_dir=tmp_path / "app-dist", cwd=tmp_path)
    proof = prove_app(app_path=app_path, out_dir=tmp_path / "app-proof", with_shell=True, cwd=tmp_path)

    for report in (compiled, proof):
        assert report["ok"] is True
        assert report["app_schema_version"] == 2
        assert report["validation"]["app_schema_version"] == 2
        assert report["resource_binding"] == APP_BUNDLE_RESOURCE_BINDING_READONLY
        assert report["binding_scope"] == APP_BUNDLE_BINDING_SCOPE
        assert report["resource_binding_assertions"]["ok"] is True
        assert report["resource_binding_assertions"]["assertion_count"] == 9
        assert report["resource_binding_assertions"]["binding_digest"]
        assert report["resource_binding_assertions"]["views"][0]["status"] == "passed"

    assert compiled["resource_binding_assertions"]["binding_digest"] == proof["resource_binding_assertions"]["binding_digest"]
    assert proof["shell"]["resource_binding"] == APP_BUNDLE_RESOURCE_BINDING_READONLY
    assert proof["shell"]["app_schema_version"] == 2
    assert proof["shell"]["binding_scope"] == APP_BUNDLE_BINDING_SCOPE

    cli_out = tmp_path / "viewspec.bound.app.json"
    assert cli_main(["init-app", "--resource-binding", "fixture-readonly-v0", "--out", str(cli_out)]) == 0
    assert json.loads(cli_out.read_text(encoding="utf-8")) == app
    capsys.readouterr()


def test_stateful_app_bundle_v3_validates_and_keeps_intent_v1_closed():
    app = _stateful_app_bundle()
    validation = validate_app_text(_app_text(app))

    assert validation["ok"] is True
    assert validation["app_schema_version"] == 3
    assert validation["resource_binding"] == APP_BUNDLE_RESOURCE_BINDING_READONLY
    assert validation["interactive_state"] == "interactive_state_v0"
    assert validation["state_ir"] == {
        "profile": "interactive_state_v0",
        "state_count": 4,
        "mutation_count": 2,
        "selector_count": 1,
        "replay_assertion_count": 1,
    }
    assert validation["summary"]["state_ir"] == validation["state_ir"]
    assert AGENT_APP_BUNDLE_SCHEMA["x-viewspec-app-schema-versions"] == [1, 2, 3, 4]
    assert AGENT_APP_BUNDLE_SCHEMA["x-viewspec-interactive-state"] == "interactive_state_v0"
    assert {"$ref": "#/$defs/app_bundle_v3"} in AGENT_APP_BUNDLE_SCHEMA["oneOf"]
    assert {"$ref": "#/$defs/app_bundle_v4"} in AGENT_APP_BUNDLE_SCHEMA["oneOf"]
    assert "interactive_state" not in AGENT_INTENT_BUNDLE_SCHEMA["properties"]
    assert "state" not in AGENT_INTENT_BUNDLE_SCHEMA["properties"]
    assert "mutations" not in AGENT_INTENT_BUNDLE_SCHEMA["properties"]


def test_state_ir_normalization_contract_hash_and_event_schemas_are_deterministic():
    app = _stateful_app_bundle()
    state_ir, issues = validate_state_ir(app)

    assert issues == []
    assert state_ir is not None

    normalized = normalize_state_ir(app, state_ir)
    normalized_again = normalize_state_ir(deepcopy(app)).to_json()
    event_schemas = state_event_schemas(state_ir)

    assert normalized.to_json() == normalized_again
    assert normalized.contract_hash == state_contract_hash(app)
    assert normalized.contract["state"][0]["id"] == "incidents_state"
    assert normalized.contract["mutations"][0]["id"] == "triage_incident_state"
    assert normalized.contract["mutations"][0]["ops"][0]["op"] == "patch"
    assert normalized.contract["selectors"][0]["ops"][0]["op"] == "filter_eq"
    assert event_schemas == normalized.contract["state_event_schemas"]
    assert event_schemas[0] == {
        "mutation_id": "triage_incident_state",
        "trigger": {"screen_id": "queue", "action_id": "triage_incident"},
        "allowed_payload_bindings": ["inc_1043_id"],
        "required_payload_bindings": ["inc_1043_id"],
        "value_type": "json_value",
    }


def test_state_ir_interpreter_replays_ops_selectors_and_rejects_malformed_payloads():
    app = _stateful_app_bundle()
    state_ir, issues = validate_state_ir(app)

    assert issues == []
    assert state_ir is not None
    current = initial_state(app, state_ir)
    result = apply_event(
        current,
        state_ir,
        {"mutation_id": "triage_incident_state", "payload_values": {"inc_1043_id": "inc_1043"}},
    )

    assert result["ok"] is True
    assert current["incidents_state"] == [
        {"id": "inc_1043", "severity": "medium", "status": "investigating"},
        {"id": "inc_1042", "severity": "high", "status": "investigating"},
    ]
    assert current["queue_flags"] == {"urgent": True, "count": 1.0}
    assert current["selected_incident"] == "inc_1043"
    assert current["selected_ids"] == ["inc_1043"]
    assert evaluate_selectors(current, state_ir) == {
        "active_incidents": [{"id": "inc_1043", "severity": "medium", "status": "investigating"}]
    }

    removed = initial_state(app, state_ir)
    remove_result = apply_event(
        removed,
        state_ir,
        {"mutation_id": "remove_incident_state", "payload_values": {"inc_1043_id": "inc_1043"}},
    )

    assert remove_result["ok"] is True
    assert removed["incidents_state"] == [{"id": "inc_1042", "severity": "high", "status": "investigating"}]

    malformed = apply_event(
        removed,
        state_ir,
        {"mutation_id": "triage_incident_state", "payload_values": []},
    )

    assert malformed["ok"] is False
    assert malformed["errors"][0]["code"] == "APP_STATE_EVENT_PAYLOAD_INVALID"

    missing = apply_event(
        initial_state(app, state_ir),
        state_ir,
        {"mutation_id": "triage_incident_state", "payload_values": {}},
    )
    unknown = apply_event(
        initial_state(app, state_ir),
        state_ir,
        {"mutation_id": "triage_incident_state", "payload_values": {"inc_1043_id": "inc_1043", "extra": True}},
    )

    assert missing["ok"] is False
    assert missing["errors"][0]["code"] == "APP_STATE_EVENT_PAYLOAD_MISSING"
    assert unknown["ok"] is False
    assert unknown["errors"][0]["code"] == "APP_STATE_EVENT_PAYLOAD_UNKNOWN"
    assert replay_state_assertions(app)["ok"] is True


def test_state_ir_replay_contract_rejects_missing_and_unknown_payload_values():
    missing = _stateful_app_bundle()
    missing["state_replay_assertions"][0]["events"][0]["payload_values"] = {}

    unknown = _stateful_app_bundle()
    unknown["state_replay_assertions"][0]["events"][0]["payload_values"]["extra"] = True

    missing_validation = validate_app_text(_app_text(missing))
    unknown_validation = validate_app_text(_app_text(unknown))

    assert missing_validation["ok"] is False
    assert "APP_STATE_EVENT_PAYLOAD_MISSING" in _issue_codes(missing_validation)
    assert unknown_validation["ok"] is False
    assert "APP_STATE_EVENT_PAYLOAD_UNKNOWN" in _issue_codes(unknown_validation)


def test_state_reducer_conformance_executes_generated_es_module_for_all_v0_ops():
    app = _stateful_app_bundle_with_remove_replay()
    reducer_source = generate_typescript_reducer(app)

    assert "export type ViewSpecState = Record<string, unknown>;" in reducer_source
    assert "state: ViewSpecState, event: ViewSpecStateEvent" in reducer_source
    assert "from \"./viewspec_numeric\"" in reducer_source
    assert "export function reduceViewSpecState" in reducer_source
    assert "export function selectViewSpecState" in reducer_source
    assert "export type" not in generate_javascript_reducer(app)

    report = check_reducer_conformance(app, reducer_source=reducer_source)

    assert report["ok"] is True
    assert report["runtime"] == "node"
    assert report["assertion_count"] == 2
    assert report["passed_count"] == 2
    assert set(STATE_REDUCER_EXPORTS).issubset(set(report["export_names"]))

    divergent_source = reducer_source.replace('"status": "investigating"', '"status": "closed"', 1)
    divergent = check_reducer_conformance(app, reducer_source=divergent_source)

    assert divergent["ok"] is False
    assert divergent["errors"][0]["code"] == "APP_STATE_REDUCER_CONFORMANCE_FAILED"


def _sort_by_app_bundle() -> dict:
    app = _stateful_app_bundle()
    app["state"] = [
        {
            "id": "rows",
            "kind": "collection",
            "scope": "app",
            "initial": {
                "value": [
                    {"id": "r1", "name": "apple"},
                    {"id": "r2", "name": "Banana"},
                    {"id": "r3", "name": "cherry"},
                ]
            },
        },
        {"id": "flags", "kind": "record", "scope": "app", "initial": {"value": {"count": 0}}},
    ]
    app["mutations"] = [
        {
            "id": "bump",
            "trigger": {"screen_id": "queue", "action_id": "triage_incident"},
            "ops": [{"op": "increment", "state": "flags", "field": "count", "amount": 1}],
        }
    ]
    app["selectors"] = [
        {"id": "sorted_rows", "source_state": "rows", "ops": [{"op": "sort_by", "field": "name", "direction": "asc"}]}
    ]
    # Code-point order puts uppercase "Banana" (B=0x42) before lowercase
    # "apple" (a=0x61); locale-aware collation would flip them. The generated
    # reducer must match the Python reference regardless of host locale.
    app["state_replay_assertions"] = [
        {
            "id": "sort_replay",
            "events": [{"mutation_id": "bump", "payload_values": {"inc_1043_id": "r1"}}],
            "expect_state": {
                "rows": [
                    {"id": "r1", "name": "apple"},
                    {"id": "r2", "name": "Banana"},
                    {"id": "r3", "name": "cherry"},
                ],
                "flags": {"count": 1.0},
            },
            "expect_selectors": {
                "sorted_rows": [
                    {"id": "r2", "name": "Banana"},
                    {"id": "r1", "name": "apple"},
                    {"id": "r3", "name": "cherry"},
                ]
            },
        }
    ]
    return app


def test_sort_by_selector_is_locale_independent_and_matches_reference():
    app = _sort_by_app_bundle()

    state_ir, issues = validate_state_ir(app)
    assert issues == []
    assert state_ir is not None

    current = initial_state(app, state_ir)
    order = [row["name"] for row in evaluate_selectors(current, state_ir)["sorted_rows"]]
    assert order == ["Banana", "apple", "cherry"]

    reducer_source = generate_typescript_reducer(app)
    assert "localeCompare" not in reducer_source

    # The generated ES module, executed under node, must agree with the Python
    # reference; a locale-dependent sort would diverge here.
    report = check_reducer_conformance(app, reducer_source=reducer_source)
    assert report["ok"] is True
    assert report["passed_count"] == 1


def _typed_sort_app_bundle() -> dict:
    app = _sort_by_app_bundle()  # reuse the working flags/bump/replay-event wiring
    rows = [
        {"id": "r1", "score": 10, "active": True, "label": "beta"},
        {"id": "r2", "score": 2, "active": False, "label": None},
        {"id": "r3", "score": 9, "active": True, "label": "alpha"},
    ]
    app["state"][0] = {"id": "rows", "kind": "collection", "scope": "app", "initial": {"value": rows}}
    app["selectors"] = [
        {"id": "by_score", "source_state": "rows", "ops": [{"op": "sort_by", "field": "score", "direction": "asc"}]},
        {"id": "by_active", "source_state": "rows", "ops": [{"op": "sort_by", "field": "active", "direction": "asc"}]},
        {"id": "by_label", "source_state": "rows", "ops": [{"op": "sort_by", "field": "label", "direction": "asc"}]},
    ]
    r1, r2, r3 = rows
    app["state_replay_assertions"] = [
        {
            "id": "typed_sort_replay",
            "events": app["state_replay_assertions"][0]["events"],
            "expect_state": {"rows": rows, "flags": {"count": 1.0}},
            "expect_selectors": {
                "by_score": [r2, r3, r1],
                "by_active": [r2, r1, r3],
                "by_label": [r2, r3, r1],
            },
        }
    ]
    return app


def test_sort_by_typed_keys_match_reference_across_node():
    # bool/null/number sort keys used to drift: Python str() vs JS String(x ?? "")
    # produced "True"/"true", "None"/"", "5.0"/"5". The typed comparator compares each
    # JSON type in its own bucket so Node and the Python reference agree.
    app = _typed_sort_app_bundle()

    state_ir, issues = validate_state_ir(app)
    assert issues == []
    assert state_ir is not None

    current = initial_state(app, state_ir)
    selectors = evaluate_selectors(current, state_ir)
    # Non-vacuity: a numeric field sorts numerically (2 < 9 < 10), NOT lexicographically
    # (which would give 10, 2, 9). A degenerate/constant comparator fails this line.
    assert [row["score"] for row in selectors["by_score"]] == [2, 9, 10]
    # Bool: false < true, input order stable within the true bucket.
    assert [row["active"] for row in selectors["by_active"]] == [False, True, True]
    # Null bucket sorts before strings; strings in code-point order.
    assert [row["label"] for row in selectors["by_label"]] == [None, "alpha", "beta"]

    # The generated ES module under node must agree with the Python reference for every
    # non-string key type -- the drift the old str()/String(x ?? "") derivation hid.
    report = check_reducer_conformance(app)
    assert report["ok"] is True
    assert report["passed_count"] == 1


def test_toggle_on_empty_container_matches_python_reference():
    app = _stateful_app_bundle()
    app["state"] = [{"id": "flag_list", "kind": "collection", "scope": "app", "initial": {"value": []}}]
    app["mutations"] = [
        {
            "id": "flip",
            "trigger": {"screen_id": "queue", "action_id": "triage_incident"},
            "ops": [{"op": "toggle", "state": "flag_list"}],
        }
    ]
    app["selectors"] = []
    app["state_replay_assertions"] = [
        {
            "id": "flip_replay",
            "events": [{"mutation_id": "flip", "payload_values": {"inc_1043_id": "x"}}],
            "expect_state": {"flag_list": True},
            "expect_selectors": {},
        }
    ]

    state_ir, issues = validate_state_ir(app)
    assert issues == []
    current = initial_state(app, state_ir)
    result = apply_event(current, state_ir, {"mutation_id": "flip", "payload_values": {"inc_1043_id": "x"}})
    assert result["ok"] is True
    assert current["flag_list"] is True  # Python not bool([]) == True

    # Generated JS must agree: !pyTruthy([]) === true (pre-fix it returned false).
    report = check_reducer_conformance(app)
    assert report["ok"] is True


def test_increment_on_non_numeric_fails_like_python_reference():
    app = _stateful_app_bundle()
    app["state"] = [{"id": "rec", "kind": "record", "scope": "app", "initial": {"value": {"count": "abc"}}}]
    app["mutations"] = [
        {
            "id": "bump",
            "trigger": {"screen_id": "queue", "action_id": "triage_incident"},
            "ops": [{"op": "increment", "state": "rec", "field": "count", "amount": 1}],
        }
    ]
    app["selectors"] = []
    app["state_replay_assertions"] = [
        {
            "id": "bump_replay",
            "events": [{"mutation_id": "bump", "payload_values": {"inc_1043_id": "x"}}],
            "expect_state": {},
            "expect_selectors": {},
        }
    ]

    state_ir, issues = validate_state_ir(app)
    assert issues == []
    result = apply_event(initial_state(app, state_ir), state_ir, {"mutation_id": "bump", "payload_values": {"inc_1043_id": "x"}})
    assert result["ok"] is False
    assert result["errors"][0]["code"] == "APP_STATE_REDUCER_OP_FAILED"

    # Generated JS must throw the same failure, not silently produce NaN.
    report = check_reducer_conformance(app)
    assert report["ok"] is True


def _increment_amount_app(amount: object) -> dict:
    app = _stateful_app_bundle()
    app["state"] = [{"id": "rec", "kind": "record", "scope": "app", "initial": {"value": {"count": 0}}}]
    app["mutations"] = [
        {
            "id": "bump",
            "trigger": {"screen_id": "queue", "action_id": "triage_incident"},
            "ops": [{"op": "increment", "state": "rec", "field": "count", "amount": amount}],
        }
    ]
    app["selectors"] = []
    app["state_replay_assertions"] = [
        {
            "id": "bump_replay",
            "events": [{"mutation_id": "bump", "payload_values": {"inc_1043_id": "x"}}],
            "expect_state": {},
            "expect_selectors": {},
        }
    ]
    return app


def test_increment_numeric_string_amount_fails_like_python_reference():
    # A numeric-string amount incremented fine under JS (Number("5") -> 5) but failed under
    # the Python reference (isinstance gate). Both sides now reject it identically instead of
    # silently coercing -- which also avoids the float()/Number() edge drift a coercion fix
    # would reintroduce.
    app = _increment_amount_app("5")
    state_ir, issues = validate_state_ir(app)
    assert issues == []
    current = initial_state(app, state_ir)
    result = apply_event(current, state_ir, {"mutation_id": "bump", "payload_values": {"inc_1043_id": "x"}})
    assert result["ok"] is False
    assert result["errors"][0]["code"] == "APP_STATE_REDUCER_OP_FAILED"
    assert check_reducer_conformance(app)["ok"] is True


def test_increment_numeric_amount_succeeds_across_node():
    # Non-vacuity: a real numeric amount still increments, identically on Node and Python.
    app = _increment_amount_app(5)
    state_ir, issues = validate_state_ir(app)
    assert issues == []
    current = initial_state(app, state_ir)
    result = apply_event(current, state_ir, {"mutation_id": "bump", "payload_values": {"inc_1043_id": "x"}})
    assert result["ok"] is True
    assert current["rec"] == {"count": 5}
    assert check_reducer_conformance(app)["ok"] is True


def test_prove_app_missing_input_is_user_error_not_internal(tmp_path):
    # A missing/unreadable AppBundle path is USER error -> a coded APP_PROOF_INPUT_READ_ERROR and
    # CLI exit 2, not an internal crash (APP_PROOF_INTERNAL_ERROR / exit 1). The catch is scoped to
    # the input read so a genuine internal failure downstream still surfaces as exit 1.
    missing = tmp_path / "no_such_app.json"

    report = prove_app(app_path=str(missing), out_dir=tmp_path / "proof", cwd=tmp_path)
    assert report["ok"] is False
    codes = {error["code"] for error in report["errors"]}
    assert codes == {"APP_PROOF_INPUT_READ_ERROR"}
    assert "APP_PROOF_INTERNAL_ERROR" not in codes

    assert cli_main(["prove-app", "--app", str(missing), "--out", str(tmp_path / "proof_cli")]) == 2


def _rec_state(value):
    return [{"id": "rec", "kind": "record", "scope": "app", "initial": {"value": value}}]


def _coll_state(value):
    return [{"id": "rows", "kind": "collection", "scope": "app", "initial": {"value": value}}]


def _reducer_scenario_app(state, ops, idval="x"):
    app = _stateful_app_bundle()
    app["state"] = state
    app["mutations"] = [
        {"id": "m", "trigger": {"screen_id": "queue", "action_id": "triage_incident"}, "ops": ops}
    ]
    app["selectors"] = []
    app["state_replay_assertions"] = [
        {
            "id": "r",
            "events": [{"mutation_id": "m", "payload_values": {"inc_1043_id": idval}}],
            "expect_state": {},
            "expect_selectors": {},
        }
    ]
    return app


_FROM_PAYLOAD = {"from_payload": "inc_1043_id"}


@pytest.mark.parametrize(
    "state,ops,idval",
    [
        # Type mismatch: Python raises; the generated JS used to no-op (append/remove/move) or
        # corrupt-and-report-success (patch/increment). Both must now fail identically.
        (_rec_state({"a": 1}), [{"op": "append", "state": "rec", "value": 1}], "x"),
        (_rec_state({"a": 1}), [{"op": "remove", "state": "rec", "item_id": _FROM_PAYLOAD}], "x"),
        (_rec_state({"a": 1}), [{"op": "move", "state": "rec", "item_id": _FROM_PAYLOAD, "to_index": 0}], "x"),
        (
            _rec_state({"a": 1}),
            [{"op": "set", "state": "rec", "value": [1, 2]}, {"op": "patch", "state": "rec", "value": {"x": 1}}],
            "x",
        ),
        (
            _rec_state({"a": 1}),
            [{"op": "set", "state": "rec", "value": [1, 2]}, {"op": "increment", "state": "rec", "field": "n", "amount": 1}],
            "x",
        ),
        # Integer-valued float id no longer drifts (String(1.0)="1" vs str(1.0)="1.0").
        (_coll_state([{"id": 1.0}, {"id": 2}]), [{"op": "remove", "state": "rows", "item_id": _FROM_PAYLOAD}], 1),
        # Well-formed multi-op still succeeds identically.
        (
            _coll_state([{"id": "a"}]),
            [{"op": "append", "state": "rows", "value": {"id": "b"}}, {"op": "patch", "state": "rows", "item_id": _FROM_PAYLOAD, "value": {"v": 1}}],
            "b",
        ),
    ],
)
def test_reducer_ops_conform_across_node_on_edge_inputs(state, ops, idval):
    # The generated reducer (Node) and the Python reference must agree on EVERY op for edge
    # inputs -- type mismatches, integer-valued float ids -- not just the well-formed path.
    app = _reducer_scenario_app(state, ops, idval)
    assert check_reducer_conformance(app)["ok"] is True


def test_reducer_type_mismatch_fails_atomically_like_reference():
    # A multi-op event whose 2nd op is a type mismatch must FAIL and leave state UNCHANGED
    # (atomic), matching the generated reducer's clone-and-return -- not partially commit op 1.
    app = _reducer_scenario_app(
        _rec_state({"a": 1}),
        [{"op": "set", "state": "rec", "value": [1, 2]}, {"op": "patch", "state": "rec", "value": {"x": 1}}],
    )
    state_ir, issues = validate_state_ir(app)
    assert issues == []
    current = initial_state(app, state_ir)
    result = apply_event(current, state_ir, {"mutation_id": "m", "payload_values": {"inc_1043_id": "x"}})
    assert result["ok"] is False
    assert result["errors"][0]["code"] == "APP_STATE_REDUCER_OP_FAILED"
    assert current == {"rec": {"a": 1}}  # op 1's set was rolled back


@settings(max_examples=24, deadline=None)
@given(
    original=st.dictionaries(
        st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=8),
        st.integers(min_value=-100, max_value=100),
        max_size=6,
    ),
    replacement=st.lists(st.integers(min_value=-10, max_value=10), max_size=6),
    invalid_op=st.sampled_from(("patch", "increment")),
)
def test_reducer_property_conformance_and_failure_atomicity(original, replacement, invalid_op):
    second = (
        {"op": "patch", "state": "rec", "value": {"x": 1}}
        if invalid_op == "patch"
        else {"op": "increment", "state": "rec", "field": "n", "amount": 1}
    )
    app = _reducer_scenario_app(
        _rec_state(original),
        [{"op": "set", "state": "rec", "value": replacement}, second],
    )

    assert check_reducer_conformance(app)["ok"] is True
    state_ir, issues = validate_state_ir(app)
    assert issues == []
    current = initial_state(app, state_ir)
    before = deepcopy(current)
    result = apply_event(current, state_ir, {"mutation_id": "m", "payload_values": {"inc_1043_id": "x"}})

    assert result["ok"] is False
    assert current == before


def test_filter_eq_distinguishes_bool_from_number_like_reducer():
    # Python filter_eq now mirrors JS === : true != 1, so only the numeric row matches.
    app = _stateful_app_bundle()
    app["state"] = [
        {"id": "rows", "kind": "collection", "scope": "app", "initial": {"value": [{"id": "t", "k": True}, {"id": "n", "k": 1}]}},
        {"id": "flags", "kind": "record", "scope": "app", "initial": {"value": {"count": 0}}},
    ]
    app["mutations"] = [
        {"id": "bump", "trigger": {"screen_id": "queue", "action_id": "triage_incident"}, "ops": [{"op": "increment", "state": "flags", "field": "count", "amount": 1}]}
    ]
    app["selectors"] = [{"id": "ones", "source_state": "rows", "ops": [{"op": "filter_eq", "field": "k", "value": 1}]}]
    app["state_replay_assertions"] = [
        {"id": "r", "events": [{"mutation_id": "bump", "payload_values": {"inc_1043_id": "x"}}], "expect_state": {}, "expect_selectors": {}}
    ]

    state_ir, issues = validate_state_ir(app)
    assert issues == []
    current = initial_state(app, state_ir)
    assert [row["id"] for row in evaluate_selectors(current, state_ir)["ones"]] == ["n"]
    assert check_reducer_conformance(app)["ok"] is True


def test_missing_node_yields_actionable_node_unavailable_code():
    # V3 reducer conformance shells out to Node; a missing Node must report a distinct, actionable
    # code -- not the generic "fix your state contract" conformance failure.
    report = check_reducer_conformance(_stateful_app_bundle(), node_command="node_definitely_missing_xyz")
    assert report["ok"] is False
    first = report["errors"][0]
    assert first["code"] == "APP_STATE_REDUCER_NODE_UNAVAILABLE"
    assert first.get("fix")  # actionable install / escape-hatch hint


def test_doctor_reports_node_availability_without_hard_failing():
    # doctor surfaces Node availability, but node absence must NOT hard-fail doctor -- V1/V2 and
    # IntentBundle flows are Python-only. Node status is a string so _doctor_checks_ok ignores it.
    pipeline = _doctor_app_bundle_pipeline()
    assert pipeline["node_available"] in {"yes", "no"}
    assert _doctor_checks_ok(pipeline) is True


def test_selector_slice_bounds_must_be_non_negative_integers():
    invalid = _stateful_app_bundle()
    invalid["selectors"][0]["ops"] = [{"op": "slice", "start": "x", "end": 1}]
    _, issues = validate_state_ir(invalid)
    assert "APP_STATE_SELECTOR_SLICE_INVALID" in {issue.code for issue in issues}

    valid = _stateful_app_bundle()
    valid["selectors"][0]["ops"] = [{"op": "slice", "start": 0, "end": 2}]
    state_ir, ok_issues = validate_state_ir(valid)
    assert state_ir is not None
    assert "APP_STATE_SELECTOR_SLICE_INVALID" not in {issue.code for issue in ok_issues}


def test_app_topology_similarity_is_order_independent():
    base = starter_app_bundle("internal_tool")

    identical = diff_app_text(_app_text(base), _app_text(base), compile_check=False)
    assert identical["topology_similarity"] == 1.0

    route_change = deepcopy(base)
    route_change["routes"][1]["label"] = "Incident Detail X"
    screen_change = deepcopy(base)
    screen_change["screens"][1]["title"] = "Detail View X"

    da = diff_app_text(_app_text(base), _app_text(route_change), compile_check=False)
    db = diff_app_text(_app_text(base), _app_text(screen_change), compile_check=False)
    assert da["ok"] and db["ok"]
    assert 0.0 < da["topology_similarity"] < 1.0
    # Equal-magnitude changes to equal-size sections must score identically,
    # regardless of section order. The pre-fix cumulative denominator did not.
    assert da["topology_similarity"] == db["topology_similarity"]


def test_validate_app_rejects_v0_constraints():
    cases: list[tuple[str, dict, str]] = []
    base = starter_app_bundle()

    duplicate_screens = deepcopy(base)
    duplicate_screens["screens"][1]["id"] = duplicate_screens["screens"][0]["id"]
    cases.append(("duplicate screens", duplicate_screens, "APP_DUPLICATE_SCREEN_ID"))

    duplicate_route_paths = deepcopy(base)
    duplicate_route_paths["routes"][1]["path"] = "/"
    cases.append(("duplicate route paths", duplicate_route_paths, "APP_DUPLICATE_ROUTE_PATH"))

    missing_root = deepcopy(base)
    missing_root["app"]["root_route"] = "/missing"
    cases.append(("missing root route", missing_root, "APP_ROOT_ROUTE_MISSING"))

    missing_screen = deepcopy(base)
    missing_screen["routes"][0]["screen_id"] = "missing"
    cases.append(("missing route screen", missing_screen, "APP_ROUTE_SCREEN_MISSING"))

    invalid_route = deepcopy(base)
    invalid_route["routes"][1]["path"] = "/incident?id=1042"
    cases.append(("invalid route", invalid_route, "APP_ROUTE_PATH_INVALID"))

    unsafe_id = deepcopy(base)
    unsafe_id["screens"][0]["id"] = "../queue"
    cases.append(("unsafe id", unsafe_id, "APP_INVALID_ID"))

    bad_resource = deepcopy(base)
    bad_resource["resources"][0]["kind"] = "fetch"
    cases.append(("bad resource kind", bad_resource, "APP_RESOURCE_KIND_UNSUPPORTED"))

    too_many_records = deepcopy(base)
    too_many_records["resources"][0]["records"] = [{"id": f"inc_{index}"} for index in range(101)]
    cases.append(("too many records", too_many_records, "APP_RESOURCE_TOO_MANY_RECORDS"))

    forbidden_surface = deepcopy(base)
    forbidden_surface["resources"][0]["records"][0]["token"] = "secret"
    cases.append(("forbidden surface", forbidden_surface, "APP_FORBIDDEN_SURFACE"))

    unknown_field = deepcopy(base)
    unknown_field["routes"][0]["guard"] = "auth"
    cases.append(("unknown field", unknown_field, "APP_UNKNOWN_FIELD"))

    invalid_intent = deepcopy(base)
    invalid_intent["screens"][0]["intent_bundle"] = {"id": "dom", "primitive": "stack", "children": []}
    cases.append(("invalid embedded intent", invalid_intent, "APP_SCREEN_INTENT_INVALID"))

    oversized_intent = deepcopy(base)
    root_id = oversized_intent["screens"][0]["intent_bundle"]["substrate"]["root_id"]
    oversized_intent["screens"][0]["intent_bundle"]["substrate"]["nodes"][root_id]["attrs"]["blob"] = "x" * (
        APP_BUNDLE_MAX_EMBEDDED_INTENT_BYTES + 1
    )
    cases.append(("oversized embedded intent", oversized_intent, "APP_SCREEN_INTENT_TOO_LARGE"))

    for _name, payload, code in cases:
        validation = validate_app_text(_app_text(payload), compile_check=False)
        assert validation["ok"] is False
        assert code in _issue_codes(validation)

    oversized = validate_app_text(" " * (APP_BUNDLE_MAX_BYTES + 1))
    assert oversized["ok"] is False
    assert oversized["issues"][0]["code"] == "APP_BUNDLE_TOO_LARGE"


def test_validate_app_rejects_v2_resource_binding_constraints():
    base = starter_app_bundle("internal_tool", resource_binding="fixture_readonly_v0")
    cases: list[tuple[str, dict, str]] = []

    v1_root_binding = starter_app_bundle()
    v1_root_binding["resource_binding"] = "fixture_readonly_v0"
    cases.append(("v1 root binding", v1_root_binding, "APP_SCHEMA_VERSION_RESOURCE_BINDING_MISMATCH"))

    v1_screen_views = starter_app_bundle()
    v1_screen_views["screens"][0]["resource_views"] = []
    cases.append(("v1 screen views", v1_screen_views, "APP_SCHEMA_VERSION_RESOURCE_BINDING_MISMATCH"))

    v2_missing_root_binding = deepcopy(base)
    del v2_missing_root_binding["resource_binding"]
    cases.append(("v2 missing binding", v2_missing_root_binding, "APP_SCHEMA_VERSION_RESOURCE_BINDING_MISMATCH"))

    unknown_mode = deepcopy(base)
    unknown_mode["screens"][0]["resource_views"][0]["mode"] = "detail"
    cases.append(("unknown mode", unknown_mode, "APP_RESOURCE_BINDING_MODE_UNSUPPORTED"))

    missing_resource = deepcopy(base)
    missing_resource["screens"][0]["resource_views"][0]["resource_id"] = "missing"
    cases.append(("missing resource", missing_resource, "APP_RESOURCE_BINDING_RESOURCE_MISSING"))

    duplicate_record = deepcopy(base)
    duplicate_record["resources"][0]["records"].append({"id": "inc_1042", "severity": "low", "status": "queued"})
    cases.append(("duplicate record", duplicate_record, "APP_RESOURCE_BINDING_DUPLICATE_RECORD_ID"))

    missing_record = deepcopy(base)
    missing_record["screens"][0]["resource_views"][0]["record_ids"] = ["missing"]
    cases.append(("missing record", missing_record, "APP_RESOURCE_BINDING_RECORD_MISSING"))

    missing_field = deepcopy(base)
    missing_field["screens"][0]["resource_views"][0]["fields"] = ["missing"]
    cases.append(("missing field", missing_field, "APP_RESOURCE_BINDING_FIELD_MISSING"))

    missing_motif = deepcopy(base)
    missing_motif["screens"][0]["resource_views"][0]["target_motif_id"] = "missing"
    cases.append(("missing motif", missing_motif, "APP_RESOURCE_BINDING_MOTIF_MISSING"))

    unsupported_query = deepcopy(base)
    unsupported_query["screens"][0]["resource_views"][0]["filter"] = {"status": "queued"}
    cases.append(("unsupported query", unsupported_query, "APP_RESOURCE_BINDING_QUERY_UNSUPPORTED"))

    empty_assertions = deepcopy(base)
    empty_assertions["screens"][0]["resource_views"] = []
    empty_assertions["screens"][1]["resource_views"] = []
    cases.append(("empty assertions", empty_assertions, "APP_RESOURCE_BINDING_EMPTY_ASSERTIONS"))

    for _name, payload, code in cases:
        validation = validate_app_text(_app_text(payload), compile_check=False)
        assert validation["ok"] is False
        assert code in _issue_codes(validation)


def test_validate_app_rejects_v3_state_constraints_and_v1_v2_mutation_fields():
    cases: list[tuple[str, dict, str]] = []

    v1_mutations = starter_app_bundle()
    v1_mutations["mutations"] = []
    cases.append(("v1 mutation root", v1_mutations, "APP_FORBIDDEN_SURFACE"))

    v2_mutations = starter_app_bundle("internal_tool", resource_binding="fixture_readonly_v0")
    v2_mutations["mutations"] = []
    cases.append(("v2 mutation root", v2_mutations, "APP_FORBIDDEN_SURFACE"))

    missing_resource_view = _stateful_app_bundle()
    missing_resource_view["state"][0]["initial"]["from_resource_view"]["view_id"] = "missing"
    cases.append(("missing resource view", missing_resource_view, "APP_STATE_RESOURCE_VIEW_MISSING"))

    unsafe_state_id = _stateful_app_bundle()
    unsafe_state_id["state"][0]["id"] = "../escape"
    cases.append(("unsafe state id", unsafe_state_id, "APP_STATE_INVALID_ID"))

    missing_action = _stateful_app_bundle()
    missing_action["mutations"][0]["trigger"]["action_id"] = "missing"
    cases.append(("missing action", missing_action, "APP_STATE_TRIGGER_ACTION_MISSING"))

    missing_payload = _stateful_app_bundle()
    missing_payload["mutations"][0]["ops"][0]["item_id"] = {"from_payload": "missing_binding"}
    cases.append(("missing payload binding", missing_payload, "APP_STATE_PAYLOAD_BINDING_MISSING"))

    unsupported_op = _stateful_app_bundle()
    unsupported_op["mutations"][0]["ops"][0] = {"op": "fetch", "state": "incidents_state"}
    cases.append(("unsupported op", unsupported_op, "APP_STATE_OP_UNSUPPORTED"))

    unknown_state_field = _stateful_app_bundle()
    unknown_state_field["state"][0]["adapter"] = "zustand"
    cases.append(("unknown state field", unknown_state_field, "APP_STATE_UNKNOWN_FIELD"))

    too_many_states = _stateful_app_bundle()
    too_many_states["state"] = [
        {"id": f"state_{index}", "kind": "scalar", "scope": "app", "initial": {"value": index}}
        for index in range(33)
    ]
    cases.append(("too many states", too_many_states, "APP_STATE_LIMIT_EXCEEDED"))

    for _name, payload, code in cases:
        validation = validate_app_text(_app_text(payload), compile_check=False)
        assert validation["ok"] is False
        assert code in _issue_codes(validation)


def test_bound_proof_fails_for_values_outside_target_motif_and_ambiguous_values(tmp_path):
    app = starter_app_bundle("internal_tool", resource_binding="fixture_readonly_v0")
    app["screens"][0]["intent_bundle"]["view_spec"]["motifs"][0]["members"].remove("inc_1042_status")
    app_path = tmp_path / "viewspec.app.json"
    app_path.write_text(_app_text(app), encoding="utf-8")

    missing = compile_app(app_path, out_dir=tmp_path / "missing", cwd=tmp_path)

    assert missing["ok"] is False
    assert missing["errors"][0]["code"] == "APP_RESOURCE_BINDING_ASSERTION_FAILED"

    ambiguous = starter_app_bundle("internal_tool", resource_binding="fixture_readonly_v0")
    ambiguous["resources"][0]["records"][1]["status"] = "investigating"
    ambiguous["screens"][0]["intent_bundle"]["substrate"]["nodes"]["inc_1043"]["attrs"]["status"] = "investigating"
    ambiguous_path = tmp_path / "ambiguous.app.json"
    ambiguous_path.write_text(_app_text(ambiguous), encoding="utf-8")

    ambiguous_report = compile_app(ambiguous_path, out_dir=tmp_path / "ambiguous", cwd=tmp_path)

    assert ambiguous_report["ok"] is False
    assert ambiguous_report["errors"][0]["code"] == "APP_RESOURCE_BINDING_AMBIGUOUS_VALUE"


def test_diff_app_reports_app_route_resource_screen_and_intent_changes():
    left = starter_app_bundle()
    right = deepcopy(left)
    right["app"]["title"] = "Incident Console Updated"
    right["routes"].append({"id": "reports", "path": "/reports", "label": "Reports", "screen_id": "reports"})
    right["resources"][0]["records"].append({"id": "inc_1044", "severity": "low", "status": "closed"})
    right["screens"].append(
        {
            "id": "reports",
            "title": "Incident Reports",
            "intent_bundle": starter_intent_bundle("dashboard").to_json(),
        }
    )
    right["screens"][0]["title"] = "Incident Queue Updated"
    right["screens"][0]["intent_bundle"] = starter_intent_bundle("list").to_json()

    diff = diff_app_text(_app_text(left), _app_text(right), compile_check=False)

    assert diff["ok"] is True
    assert diff["changes"]["app"]["changed"] == ["app"]
    assert diff["changes"]["routes"]["added"] == ["reports"]
    assert diff["changes"]["resources"]["changed"] == ["incidents"]
    assert diff["changes"]["screens"]["added"] == ["reports"]
    assert "queue" in diff["screen_intent_diffs"]
    assert diff["screen_intent_diffs"]["queue"]["semantic_summary"]
    lines = app_semantic_change_lines(diff["semantic_changes"])
    assert "app_metadata: title Incident Console -> Incident Console Updated" in lines
    assert "routes.reports: added" in lines
    assert "resources.incidents: records_changed" in lines
    assert any(line.startswith("screen_intents.queue:") for line in lines)


def test_diff_app_reports_v2_resource_binding_and_resource_view_changes():
    left = starter_app_bundle("internal_tool", resource_binding="fixture_readonly_v0")
    right = deepcopy(left)
    right["screens"][0]["resource_views"][0]["fields"] = ["id", "severity"]

    diff = diff_app_text(_app_text(left), _app_text(right), compile_check=False)

    assert diff["ok"] is True
    assert diff["changes"]["screens"]["changed"] == ["queue"]
    lines = app_semantic_change_lines(diff["semantic_changes"])
    assert "screens.queue: resource_views_changed" in lines
    assert diff["counts"]["resource_views"] == {"left": 2, "right": 2}


def test_diff_app_reports_resource_binding_mode_changes():
    left = starter_app_bundle()
    right = starter_app_bundle("internal_tool", resource_binding="fixture_readonly_v0")

    diff = diff_app_text(_app_text(left), _app_text(right), compile_check=False)

    assert diff["ok"] is True
    lines = app_semantic_change_lines(diff["semantic_changes"])
    assert "app_metadata: schema_version 1 -> 2" in lines
    assert "app_metadata: resource_binding null -> fixture_readonly_v0" in lines


def test_diff_app_reports_v3_state_mutation_selector_and_replay_changes():
    left = _stateful_app_bundle()
    right = deepcopy(left)
    right["state"][2]["initial"]["value"]["count"] = 2
    right["mutations"][0]["ops"][2]["amount"] = 2
    right["selectors"][0]["ops"][2]["end"] = 2
    right["state_replay_assertions"][0]["expect_state"]["queue_flags"]["count"] = 2.0

    diff = diff_app_text(_app_text(left), _app_text(right), compile_check=False)

    assert diff["ok"] is True
    assert diff["changes"]["state"]["changed"] == ["queue_flags"]
    assert diff["changes"]["mutations"]["changed"] == ["triage_incident_state"]
    assert diff["changes"]["selectors"]["changed"] == ["active_incidents"]
    assert diff["changes"]["state_replay_assertions"]["changed"] == ["triage_replay"]
    assert diff["counts"]["state"] == {"left": 4, "right": 4}
    lines = app_semantic_change_lines(diff["semantic_changes"])
    assert "state.queue_flags: definition_changed" in lines
    assert "mutations.triage_incident_state: definition_changed" in lines
    assert "selectors.active_incidents: definition_changed" in lines
    assert "state_replay_assertions.triage_replay: definition_changed" in lines


def test_diff_app_fails_changed_invalid_embedded_intent_with_stable_code():
    left = starter_app_bundle()
    right = deepcopy(left)
    right["screens"][0]["intent_bundle"] = {"id": "dom", "primitive": "stack", "children": []}

    diff = diff_app_text(_app_text(left), _app_text(right), compile_check=False)

    assert diff["ok"] is False
    assert diff["errors"][0]["code"] == "APP_DIFF_SCREEN_INTENT_INVALID"


def test_prove_app_writes_checked_screen_artifacts_and_redacted_support(tmp_path):
    app_path = tmp_path / "viewspec.app.json"
    app_path.write_text(_app_text(starter_app_bundle()), encoding="utf-8")
    out_dir = tmp_path / "app-proof"

    report = prove_app(app_path=app_path, out_dir=out_dir, cwd=tmp_path)

    assert report["ok"] is True
    assert report["proof_level"] == "app_contract_source_artifacts"
    assert report["target"] == "html-tailwind"
    assert report["resource_binding"] == "unbound_v0"
    assert report["policy"]["network_calls"] == "none"
    assert out_dir.joinpath("APP_PROOF.md").exists()
    assert out_dir.joinpath("app_proof_report.json").exists()
    assert out_dir.joinpath("app_support_bundle.json").exists()
    assert json.loads(out_dir.joinpath("app_proof_report.json").read_text(encoding="utf-8")) == report
    assert len(report["screens"]) == 2
    for screen in report["screens"]:
        screen_root = out_dir / "screens" / screen["id"]
        assert screen_root.joinpath("viewspec.intent.json").exists()
        assert screen_root.joinpath("artifact/index.html").exists()
        assert screen_root.joinpath("artifact/provenance_manifest.json").exists()
        assert screen_root.joinpath("artifact/diagnostics.json").exists()
        assert screen["artifact_hash"] == file_hash(screen_root / "artifact/index.html")
        assert screen["manifest_hash"] == file_hash(screen_root / "artifact/provenance_manifest.json")
        assert screen["manifest_summary"]["available"] is True
        assert check_artifact_dir(screen_root / "artifact")["ok"] is True
    support_text = out_dir.joinpath("app_support_bundle.json").read_text(encoding="utf-8")
    support = json.loads(support_text)
    assert support["kind"] == "viewspec_app_proof_support_bundle"
    assert support["privacy"]["contains_raw_app"] is False
    assert support["privacy"]["contains_absolute_paths"] is False
    assert str(tmp_path) not in support_text


def test_compile_app_writes_static_shell_artifact_and_cli_report(tmp_path, capsys):
    app_path = tmp_path / "viewspec.app.json"
    app_path.write_text(_app_text(starter_app_bundle()), encoding="utf-8")
    out_dir = tmp_path / "app-dist"

    report = compile_app(app_path, out_dir=out_dir, cwd=tmp_path)

    assert report["ok"] is True
    assert report["app_schema_version"] == 1
    assert report["target"] == APP_SHELL_TARGET
    assert report["route_navigation"] == APP_SHELL_ROUTE_NAVIGATION
    assert report["resource_binding"] == "unbound_v0"
    assert report["policy"]["network_calls"] == "none"
    assert report["shell_artifact_hash"] == file_hash(out_dir / "index.html")
    assert report["shell_manifest_hash"] == file_hash(out_dir / "shell_manifest.json")
    assert out_dir.joinpath("diagnostics.json").exists()
    assert out_dir.joinpath("screens/queue/artifact/index.html").exists()
    manifest = json.loads(out_dir.joinpath("shell_manifest.json").read_text(encoding="utf-8"))
    diagnostics = json.loads(out_dir.joinpath("diagnostics.json").read_text(encoding="utf-8"))
    assert manifest["app_schema_version"] == 1
    assert diagnostics["app_schema_version"] == 1
    html = out_dir.joinpath("index.html").read_text(encoding="utf-8")
    assert html.count('<section class="vs-app-404"') == 1
    assert html.count('data-selected="true"') == 1
    assert "http:" not in html.lower()
    assert "https:" not in html.lower()
    assert "<iframe" not in html.lower()
    assert report["route_assertions"] == {
        "every_route_maps_exactly_one_screen": True,
        "every_screen_has_route": True,
        "root_route_selects_exactly_one_screen": True,
        "unknown_route_selects_no_screen_and_one_404": True,
    }

    cli_out = tmp_path / "cli-app-dist"
    assert cli_main(["compile-app", str(app_path), "--out", str(cli_out), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["target"] == APP_SHELL_TARGET
    assert payload["shell_artifact_hash"] == file_hash(cli_out / "index.html")


def test_prove_app_with_shell_writes_shell_proof_and_matches_compile_app_hash(tmp_path):
    app_path = tmp_path / "viewspec.app.json"
    app_path.write_text(_app_text(starter_app_bundle()), encoding="utf-8")
    compiled = compile_app(app_path, out_dir=tmp_path / "app-dist", cwd=tmp_path)

    proof = prove_app(app_path=app_path, out_dir=tmp_path / "app-proof", with_shell=True, cwd=tmp_path)

    assert compiled["ok"] is True
    assert proof["ok"] is True
    assert proof["target"] == APP_SHELL_TARGET
    assert proof["route_navigation"] == APP_SHELL_ROUTE_NAVIGATION
    assert proof["shell_artifact_hash"] == compiled["shell_artifact_hash"]
    assert proof["paths"]["app_shell_index"].endswith("app-shell\\index.html") or proof["paths"]["app_shell_index"].endswith("app-shell/index.html")
    assert (tmp_path / "app-proof/app-shell/index.html").exists()
    assert proof["shell"]["route_assertions"]["unknown_route_selects_no_screen_and_one_404"] is True


def test_compile_and_prove_app_v3_emit_deterministic_state_artifacts(tmp_path):
    app_path = tmp_path / "viewspec.stateful.app.json"
    app_path.write_text(_app_text(_stateful_app_bundle()), encoding="utf-8")

    compiled = compile_app(app_path, out_dir=tmp_path / "app-dist", cwd=tmp_path)
    proof = prove_app(app_path=app_path, out_dir=tmp_path / "app-proof", with_shell=True, cwd=tmp_path)

    assert compiled["ok"] is True
    assert proof["ok"] is True
    assert compiled["app_schema_version"] == 3
    assert proof["app_schema_version"] == 3
    reducer_path = tmp_path / "app-dist" / APP_STATE_REDUCER
    manifest_path = tmp_path / "app-dist" / APP_STATE_MANIFEST
    proof_reducer_path = tmp_path / "app-proof" / "app-shell" / APP_STATE_REDUCER
    proof_manifest_path = tmp_path / "app-proof" / "app-shell" / APP_STATE_MANIFEST
    assert reducer_path.exists()
    assert manifest_path.exists()
    assert proof_reducer_path.exists()
    assert proof_manifest_path.exists()
    assert compiled["paths"]["state_reducer"].endswith(APP_STATE_REDUCER)
    assert compiled["paths"]["state_manifest"].endswith(APP_STATE_MANIFEST)
    assert proof["paths"]["app_state_reducer"].endswith(APP_STATE_REDUCER)
    assert proof["paths"]["app_state_manifest"].endswith(APP_STATE_MANIFEST)
    assert compiled["state_reducer_hash"] == file_hash(reducer_path)
    assert compiled["state_manifest_hash"] == file_hash(manifest_path)
    assert proof["state_reducer_hash"] == file_hash(proof_reducer_path)
    assert proof["state_manifest_hash"] == file_hash(proof_manifest_path)
    assert compiled["state_reducer_hash"] == proof["state_reducer_hash"]
    assert compiled["state_manifest_hash"] == proof["state_manifest_hash"]
    assert compiled["state_contract_hash"] == proof["state_contract_hash"]
    assert compiled["state_reducer_conformance"]["ok"] is True
    assert proof["state_reducer_conformance"]["ok"] is True
    assert proof["shell"]["state_contract_hash"] == proof["state_contract_hash"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == STATE_MANIFEST_SCHEMA_VERSION
    assert manifest["profile"] == "interactive_state_v0"
    assert manifest["reducer_hash"] == compiled["state_reducer_hash"]
    assert manifest["contract_hash"] == compiled["state_contract_hash"]
    assert manifest["normalized_contract"]["contract_hash"] == compiled["state_contract_hash"]
    assert manifest["state_event_schemas"][0]["required_payload_bindings"] == ["inc_1043_id"]
    assert manifest["reducer_exports"] == list(STATE_REDUCER_EXPORTS)
    assert manifest["replay"]["ok"] is True
    assert manifest["replay"]["passed_count"] == 1
    assert manifest["reducer_conformance"]["ok"] is True
    assert manifest["reducer_conformance"]["passed_count"] == 1
    diagnostics = json.loads((tmp_path / "app-dist" / "diagnostics.json").read_text(encoding="utf-8"))
    assert diagnostics["state_contract_hash"] == compiled["state_contract_hash"]
    assert diagnostics["state_reducer_conformance"]["ok"] is True
    reducer = reducer_path.read_text(encoding="utf-8")
    assert "export type ViewSpecState = Record<string, unknown>;" in reducer
    assert "state: ViewSpecState, event: ViewSpecStateEvent" in reducer
    assert "export function reduceViewSpecState" in reducer
    assert "export function selectViewSpecState" in reducer

    tool_proof = prove_app_tool(app_path=app_path, out_dir=tmp_path / "tool-proof", with_shell=True, cwd=tmp_path)
    assert tool_proof["ok"] is True
    assert tool_proof["metadata"]["state_contract_hash"] == compiled["state_contract_hash"]
    assert tool_proof["metadata"]["state_reducer_conformance"] == "passed"
    assert tool_proof["metadata"]["proof_identity"]["state_contract_hash"] == compiled["state_contract_hash"]
    assert tool_proof["metadata"]["proof_identity"]["state_reducer_conformance"] == "passed"


def test_compile_app_fails_when_state_reducer_conformance_fails(tmp_path, monkeypatch):
    app_path = tmp_path / "viewspec.stateful.app.json"
    app_path.write_text(_app_text(_stateful_app_bundle()), encoding="utf-8")

    def fail_conformance(*_args, **_kwargs):
        return {
            "ok": False,
            "errors": [
                {
                    "code": "APP_STATE_REDUCER_CONFORMANCE_FAILED",
                    "path": "$.interactive_state",
                    "message": "forced reducer divergence",
                }
            ],
        }

    monkeypatch.setattr(app_bundle_module, "check_reducer_conformance", fail_conformance)

    compiled = compile_app(app_path, out_dir=tmp_path / "app-dist", cwd=tmp_path)

    assert compiled["ok"] is False
    assert compiled["errors"][0]["code"] == "APP_STATE_REDUCER_CONFORMANCE_FAILED"
    assert "forced reducer divergence" in compiled["errors"][0]["message"]


def test_compile_app_rejects_static_shell_constraints(tmp_path):
    app_path = tmp_path / "viewspec.app.json"
    app_path.write_text(_app_text(starter_app_bundle()), encoding="utf-8")
    existing = tmp_path / "existing"
    existing.mkdir()

    output_exists = compile_app(app_path, out_dir=existing, cwd=tmp_path)
    assert output_exists["ok"] is False
    assert output_exists["errors"][0]["code"] == "APP_SHELL_OUTPUT_EXISTS"

    unsafe_output = compile_app(app_path, out_dir=tmp_path, cwd=tmp_path)
    assert unsafe_output["ok"] is False
    assert unsafe_output["errors"][0]["code"] == "APP_SHELL_OUTPUT_PATH_UNSAFE"

    unsupported_target = compile_app(app_path, out_dir=tmp_path / "bad-target", target="html-tailwind", cwd=tmp_path)
    assert unsupported_target["ok"] is False
    assert unsupported_target["errors"][0]["code"] == "APP_SHELL_TARGET_UNSUPPORTED"


def test_compile_app_rejects_network_surface_in_checked_screen_artifact(tmp_path):
    app = starter_app_bundle()
    app["screens"][0]["intent_bundle"]["substrate"]["nodes"]["inc_1042"]["attrs"]["value"] = "https://example.invalid"
    app_path = tmp_path / "viewspec.app.json"
    app_path.write_text(_app_text(app), encoding="utf-8")

    report = compile_app(app_path, out_dir=tmp_path / "app-dist", cwd=tmp_path)

    assert report["ok"] is False
    assert report["errors"][0]["code"] == "APP_SHELL_NETWORK_SURFACE_REJECTED"
    assert report["shell_artifact_hash"] is None


def test_compile_app_rejects_oversized_route_table(tmp_path):
    app = starter_app_bundle()
    app["routes"] = [
        {
            "id": f"route_{index}",
            "path": "/" if index == 0 else f"/route_{index}",
            "label": "x" * 2048,
            "screen_id": "detail" if index == 1 else "queue",
        }
        for index in range(32)
    ]
    app_path = tmp_path / "viewspec.app.json"
    app_path.write_text(_app_text(app), encoding="utf-8")

    report = compile_app(app_path, out_dir=tmp_path / "app-dist", cwd=tmp_path)

    assert report["ok"] is False
    assert report["errors"][0]["code"] == "APP_SHELL_SIZE_LIMIT_EXCEEDED"


def test_prove_app_fails_before_writing_artifacts_for_invalid_app(tmp_path):
    app = starter_app_bundle()
    app["routes"][0]["path"] = "/bad?query=1"
    app_path = tmp_path / "viewspec.app.json"
    app_path.write_text(_app_text(app), encoding="utf-8")
    out_dir = tmp_path / "bad-proof"

    report = prove_app(app_path=app_path, out_dir=out_dir, cwd=tmp_path)

    assert report["ok"] is False
    assert report["errors"][0]["code"] == "APP_ROUTE_PATH_INVALID"
    assert not out_dir.exists()


def test_prove_app_rejects_report_path_outside_proof_root(tmp_path):
    app_path = tmp_path / "viewspec.app.json"
    app_path.write_text(_app_text(starter_app_bundle()), encoding="utf-8")
    out_dir = tmp_path / "app-proof"
    escaped_report = tmp_path / "escaped-report.json"

    report = prove_app(app_path=app_path, out_dir=out_dir, report_out=escaped_report, cwd=tmp_path)

    assert report["ok"] is False
    assert report["errors"][0]["code"] == "APP_PROOF_REPORT_PATH_UNSAFE"
    assert not out_dir.exists()
    assert not escaped_report.exists()


def test_app_mcp_tools_respect_cwd_and_return_standard_envelopes(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside.app.json"
    outside.write_text(_app_text(starter_app_bundle()), encoding="utf-8")

    blocked = init_app_tool("../outside.app.json", cwd=tmp_path)
    assert blocked["ok"] is False
    assert blocked["errors"][0]["code"] == "PATH_OUTSIDE_CWD"

    init = init_app_tool("viewspec.app.json", cwd=tmp_path)
    assert init["schema_version"] == 1
    assert init["ok"] is True
    assert init["validation"]["ok"] is True

    escaped = prove_app_tool(app_path=outside, out_dir="proof", cwd=tmp_path)
    assert escaped["ok"] is False
    assert escaped["errors"][0]["code"] == "PATH_OUTSIDE_CWD"

    proved = prove_app_tool(app_path="viewspec.app.json", out_dir="proof", cwd=tmp_path)
    assert proved["ok"] is True
    assert proved["proof_report"]["ok"] is True
    assert proved["metadata"]["proof_level"] == "app_contract_source_artifacts"
    assert proved["metadata"]["resource_binding"] == "unbound_v0"

    compiled = compile_app_tool("viewspec.app.json", "app-dist", cwd=tmp_path)
    assert compiled["ok"] is True
    assert compiled["compile_report"]["target"] == APP_SHELL_TARGET
