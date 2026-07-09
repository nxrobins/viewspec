"""IntentBundle root schema_version: optional, exactly 1, and semantically invisible.

The field exists so documents self-describe their contract revision. Absence means
version 1 forever; any future revision must REQUIRE the field, so old and new documents
stay distinguishable. Presence of the field must not change compiled artifacts.
"""

import json
from pathlib import Path

import pytest

pytest.importorskip("jsonschema")
from jsonschema import Draft202012Validator

from viewspec import INTENT_BUNDLE_SCHEMA_VERSION, starter_intent_payload
from viewspec.agent import AGENT_INTENT_BUNDLE_SCHEMA, validate_agent_intent_bundle
from viewspec.agent_assets import check_agent_assets, export_agent_assets
from viewspec.app_bundle import starter_app_bundle
from viewspec.app_validation import validate_app_text
from viewspec.intent_tools import compile_intent_bundle_file_tool, init_intent_file
from viewspec.types import IntentBundle


def _starter() -> dict:
    return starter_intent_payload("dashboard")


def _versionless(payload: dict) -> dict:
    return {key: value for key, value in payload.items() if key != "schema_version"}


def _issue_codes(result) -> set[str]:
    return {issue.code for issue in result.issues}


def test_constant_is_one():
    assert INTENT_BUNDLE_SCHEMA_VERSION == 1


def test_starter_payload_carries_schema_version():
    payload = _starter()
    assert payload["schema_version"] == 1
    assert validate_agent_intent_bundle(json.dumps(payload)).valid


def test_versionless_document_is_version_one():
    assert validate_agent_intent_bundle(json.dumps(_versionless(_starter()))).valid


@pytest.mark.parametrize("bad", [2, 0, -1, "1", 1.5, True, False, None, [1], {"value": 1}])
def test_wrong_schema_version_fails_closed(bad):
    payload = dict(_starter(), schema_version=bad)
    result = validate_agent_intent_bundle(json.dumps(payload))
    assert not result.valid
    assert "UNSUPPORTED_SCHEMA_VERSION" in _issue_codes(result)


def test_integer_valued_float_matches_json_schema_semantics():
    # JSON has one number type: 1.0 is the integer 1. The runtime validator must agree
    # with jsonschema's draft 2020-12 reading of {"type": "integer", "const": 1}.
    payload = dict(_starter(), schema_version=1.0)
    assert validate_agent_intent_bundle(json.dumps(payload)).valid


@pytest.mark.parametrize("version", [1, 1.0, 2, "1", True, 1.5])
def test_published_schema_agrees_with_runtime_validator(version):
    payload = dict(_starter(), schema_version=version)
    validator = Draft202012Validator(AGENT_INTENT_BUNDLE_SCHEMA)
    schema_valid = not list(validator.iter_errors(json.loads(json.dumps(payload))))
    runtime_valid = validate_agent_intent_bundle(json.dumps(payload)).valid
    assert schema_valid == runtime_valid, f"schema and validator disagree for schema_version={version!r}"


def test_from_json_round_trip_is_version_blind():
    payload = _starter()
    with_version = IntentBundle.from_json(payload)
    without_version = IntentBundle.from_json(_versionless(payload))
    assert with_version.to_json() == without_version.to_json()
    # to_json never emits the metadata field; absence means version 1.
    assert "schema_version" not in with_version.to_json()


def test_from_json_rejects_other_versions():
    with pytest.raises(ValueError, match="schema_version must be 1"):
        IntentBundle.from_json(dict(_starter(), schema_version=2))


def test_from_json_does_not_mutate_caller_payload():
    payload = _starter()
    IntentBundle.from_json(payload)
    assert payload["schema_version"] == 1


def test_compiled_artifacts_are_byte_identical_with_and_without_version(tmp_path: Path):
    versioned = tmp_path / "versioned.intent.json"
    legacy = tmp_path / "legacy.intent.json"
    payload = _starter()
    versioned.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    legacy.write_text(json.dumps(_versionless(payload), indent=2, sort_keys=True), encoding="utf-8")

    out_a = tmp_path / "out-versioned"
    out_b = tmp_path / "out-legacy"
    result_a = compile_intent_bundle_file_tool(str(versioned), str(out_a), cwd=str(tmp_path))
    result_b = compile_intent_bundle_file_tool(str(legacy), str(out_b), cwd=str(tmp_path))
    assert result_a["ok"] and result_b["ok"]

    html_a = (out_a / "index.html").read_text(encoding="utf-8")
    html_b = (out_b / "index.html").read_text(encoding="utf-8")
    assert html_a == html_b

    digest_a = json.loads((out_a / "provenance_manifest.json").read_text(encoding="utf-8"))["semantic_digest"]["digest"]
    digest_b = json.loads((out_b / "provenance_manifest.json").read_text(encoding="utf-8"))["semantic_digest"]["digest"]
    assert digest_a == digest_b


def test_init_intent_file_writes_self_describing_document(tmp_path: Path):
    path = init_intent_file(tmp_path / "viewspec.intent.json", kind="form")
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert validate_agent_intent_bundle(json.dumps(payload)).valid


def test_embedded_screen_intent_accepts_schema_version():
    app = starter_app_bundle("internal_tool")
    screen = app["screens"][0]
    screen["intent_bundle"] = dict(screen["intent_bundle"], schema_version=1)
    result = validate_app_text(json.dumps(app))
    assert result["ok"], result["issues"]


def test_embedded_screen_intent_rejects_wrong_schema_version():
    app = starter_app_bundle("internal_tool")
    screen = app["screens"][0]
    screen["intent_bundle"] = dict(screen["intent_bundle"], schema_version=3)
    result = validate_app_text(json.dumps(app))
    assert not result["ok"]
    assert "UNSUPPORTED_SCHEMA_VERSION" in json.dumps(result["issues"])


def test_exported_intent_example_is_versioned_and_schema_valid(tmp_path: Path):
    export = export_agent_assets(tmp_path, force=True)
    assert export["ok"]
    example = json.loads((tmp_path / "agent-intent-example.dashboard.json").read_text(encoding="utf-8"))
    assert example["schema_version"] == 1
    validator = Draft202012Validator(AGENT_INTENT_BUNDLE_SCHEMA)
    assert not list(validator.iter_errors(example))
    check = check_agent_assets(tmp_path)
    assert check["ok"], check["errors"]
