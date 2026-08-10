from __future__ import annotations

import json
from pathlib import Path

from viewspec.app_bundle import compile_app, starter_react_app_bundle, validate_app_text
from viewspec.app_resource_repeat import (
    materialize_resource_repeats,
    resource_binding_address,
    resource_repeat_binding_id,
    resource_repeat_node_id,
)


REACT_APP_TARGET = "react-tailwind-app"


def _write_app(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _repeat_app() -> dict:
    app = starter_react_app_bundle("internal_tool")
    queue = app["screens"][0]
    resource_view = queue["resource_views"][0]
    resource_view["repeat"] = {
        "group_id": "incident_rows",
        "field_presentations": [
            {"field": "id", "present_as": "label"},
            {"field": "severity", "present_as": "value"},
            {"field": "status", "present_as": "badge"},
        ],
    }

    intent = queue["intent_bundle"]
    intent["substrate"]["nodes"].pop("inc_1042")
    intent["substrate"]["nodes"].pop("inc_1043")
    intent["view_spec"]["bindings"] = []
    intent["view_spec"]["groups"][0]["members"] = []
    intent["view_spec"]["motifs"][0]["members"] = []

    generated_payload_binding = resource_repeat_binding_id("queue_incidents", "inc_1043", "id")
    intent["view_spec"]["actions"][0]["payload_bindings"] = [generated_payload_binding]
    app["mutations"][0]["ops"][0]["item_id"]["from_payload"] = generated_payload_binding
    app["mutations"][0]["ops"][1]["value"]["from_payload"] = generated_payload_binding
    app["state_replay_assertions"][0]["events"][0]["payload_values"] = {
        generated_payload_binding: "inc_1043"
    }
    app["visibility"][0]["target_ref"] = (
        f"binding:{resource_repeat_binding_id('queue_incidents', 'inc_1043', 'status')}"
    )

    # The two records deliberately share one visible scalar. Canonical identity, not text,
    # must distinguish the generated bindings.
    app["resources"][0]["records"][1]["severity"] = "high"
    expected = app["state_replay_assertions"][0]
    expected["expect_state"]["incidents_state"][1]["severity"] = "high"
    expected["expect_selectors"]["active_incidents"][1]["severity"] = "high"
    return app


def test_repeat_materializes_stable_record_field_bindings_without_authored_duplicates(tmp_path):
    payload = _repeat_app()
    validation = validate_app_text(json.dumps(payload))
    assert validation["ok"] is True, validation["issues"]

    materialized = materialize_resource_repeats(payload)
    queue = materialized["screens"][0]
    intent = queue["intent_bundle"]
    resource_view = queue["resource_views"][0]
    binding_ids = [item["id"] for item in intent["view_spec"]["bindings"]]
    assert len(binding_ids) == 6
    assert len(set(binding_ids)) == 6
    assert intent["view_spec"]["motifs"][0]["members"] == binding_ids
    assert intent["view_spec"]["groups"][0]["members"] == binding_ids
    for record_id in resource_view["record_ids"]:
        node_id = resource_repeat_node_id("queue_incidents", record_id)
        assert node_id in intent["substrate"]["nodes"]
        for field in resource_view["fields"]:
            binding_id = resource_repeat_binding_id("queue_incidents", record_id, field)
            binding = next(item for item in intent["view_spec"]["bindings"] if item["id"] == binding_id)
            assert binding["address"] == resource_binding_address(resource_view, record_id, field)

    app_path = tmp_path / "viewspec.app.json"
    static_dir = tmp_path / "static"
    react_dir = tmp_path / "react"
    _write_app(app_path, payload)
    static = compile_app(app_path, out_dir=static_dir, cwd=tmp_path)
    react = compile_app(app_path, out_dir=react_dir, target=REACT_APP_TARGET, cwd=tmp_path)

    for result in (static, react):
        assert result["ok"] is True, result.get("errors")
        proof = result["resource_binding_assertions"]
        assert proof["ok"] is True
        assert proof["assertion_count"] == 9
        assert proof["resource_repeat"]["view_count"] == 1
        assert proof["resource_repeat"]["generated_binding_count"] == 6
        queue_assertions = next(view for view in proof["views"] if view["id"] == "queue_incidents")[
            "assertions"
        ]
        severity = [item for item in queue_assertions if item["field"] == "severity"]
        assert {item["expected"] for item in severity} == {"high"}
        assert len({item["canonical_identity"] for item in severity}) == 2
        assert len({item["matched_dom_id"] for item in severity}) == 2

    react_source = (react_dir / "src" / "ViewSpecApp.tsx").read_text(encoding="utf-8")
    assert resource_repeat_binding_id("queue_incidents", "inc_1043", "id") in react_source

    plan = json.loads((react_dir / "presentation_plan.json").read_text(encoding="utf-8"))
    queue_rule = next(
        rule
        for rule in plan["screens"][0]["rules"]
        if rule["target_ref"] == "motif:incidents"
    )
    assert queue_rule["role"] == "job_row"
    assert queue_rule["items"]["variants"]["compact"]["columns"] == [
        "identity_sm",
        "fluid",
        "auto",
    ]
    assert queue_rule["items"]["variants"]["compact"]["areas"][1] == [
        "record_title",
        "record_title",
        "record_title",
    ]
    queue_tsx = (react_dir / "src" / "screens" / "queue" / "ViewSpecView.tsx").read_text(
        encoding="utf-8"
    )
    assert 'data-resource-id={"incidents"}' in queue_tsx
    assert 'data-record-id={"inc_1042"}' in queue_tsx
    assert 'data-resource-field={"severity"}' in queue_tsx


def test_repeat_rejects_missing_field_coverage_and_authored_identity_collision():
    payload = _repeat_app()
    payload["screens"][0]["resource_views"][0]["repeat"]["field_presentations"].pop()
    missing = validate_app_text(json.dumps(payload))
    assert missing["ok"] is False
    assert "APP_RESOURCE_REPEAT_FIELD_COVERAGE_INVALID" in {
        issue["code"] for issue in missing["issues"]
    }

    payload = _repeat_app()
    queue = payload["screens"][0]
    generated_id = resource_repeat_binding_id("queue_incidents", "inc_1042", "severity")
    queue["intent_bundle"]["view_spec"]["bindings"].append(
        {
            "id": generated_id,
            "address": "node:incident_queue#attr:title",
            "target_region": "main",
            "present_as": "text",
            "cardinality": "exactly_once",
        }
    )
    collision = validate_app_text(json.dumps(payload))
    assert collision["ok"] is False
    matching = [
        issue for issue in collision["issues"] if issue["code"] == "APP_RESOURCE_REPEAT_ID_COLLISION"
    ]
    assert matching
    assert "incidents/inc_1042/severity" in matching[0]["message"]
    assert generated_id in matching[0]["message"]


def test_repeat_rejects_hand_authored_prototype_fields_in_its_target_motif():
    payload = _repeat_app()
    queue = payload["screens"][0]
    intent = queue["intent_bundle"]
    intent["substrate"]["nodes"]["inc_1042"] = {
        "id": "inc_1042",
        "kind": "incident",
        "attrs": {"severity": "high"},
        "slots": {},
        "edges": {},
    }
    intent["view_spec"]["bindings"].append(
        {
            "id": "authored_incident_severity",
            "address": "node:inc_1042#attr:severity",
            "target_region": "main",
            "present_as": "badge",
            "cardinality": "exactly_once",
        }
    )
    intent["view_spec"]["motifs"][0]["members"].append("authored_incident_severity")

    invalid = validate_app_text(json.dumps(payload))
    issue = next(
        item
        for item in invalid["issues"]
        if item["code"] == "APP_RESOURCE_REPEAT_AUTHORED_DUPLICATE"
    )
    assert "incidents/inc_1042/severity" in issue["message"]
    assert "authored_incident_severity" in issue["message"]
    assert "Remove the hand-authored prototype binding" in issue["suggestion"]


def test_agent_schema_exposes_bounded_resource_repeat_contract():
    from viewspec.app_bundle import AGENT_APP_BUNDLE_SCHEMA

    resource_view = AGENT_APP_BUNDLE_SCHEMA["$defs"]["resource_view"]
    assert resource_view["properties"]["repeat"] == {"$ref": "#/$defs/resource_repeat"}
    repeat = AGENT_APP_BUNDLE_SCHEMA["$defs"]["resource_repeat"]
    assert repeat["required"] == ["field_presentations"]
    assert repeat["additionalProperties"] is False
