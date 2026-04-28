from __future__ import annotations

import json
from copy import deepcopy

import pytest

from viewspec import (
    AGENT_INTENT_BUNDLE_SCHEMA,
    AGENT_SYSTEM_PROMPT,
    SUPPORTED_AGENT_MOTIFS,
    ViewSpecBuilder,
    agent_correction_prompt,
    validate_agent_intent_bundle,
)


def _bundle_for_motif(motif: str) -> dict:
    builder = ViewSpecBuilder(f"agent_{motif}")
    if motif == "table":
        table = builder.add_table("items", region="main", group_id="rows")
        table.add_row(label="Alpha", value="1", id="alpha")
        table.add_row(label="Beta", value="2", id="beta")
    elif motif == "dashboard":
        dashboard = builder.add_dashboard("kpis", region="main", group_id="cards")
        dashboard.add_card(label="Revenue", value="$2.4M", id="revenue")
        dashboard.add_card(label="Users", value="18,472", id="users")
    elif motif == "outline":
        outline = builder.add_outline("tree", region="main", group_id="branches")
        outline.add_branch(label="Plan", id="plan")
        outline.add_branch(label="Build", id="build")
    elif motif == "comparison":
        comparison = builder.add_comparison("plans", region="main", group_id="tiers")
        comparison.add_item(label="Free", value="$0", id="free")
        comparison.add_item(label="Pro", value="$39", id="pro")
    else:
        raise ValueError(motif)
    return builder.build_bundle().to_json()


def _issue_codes(payload: dict) -> set[str]:
    return {issue.code for issue in validate_agent_intent_bundle(payload).issues}


@pytest.mark.parametrize("motif", SUPPORTED_AGENT_MOTIFS)
def test_valid_agent_bundles_compile_for_supported_motifs(motif):
    result = validate_agent_intent_bundle(json.dumps(_bundle_for_motif(motif)))

    assert result.valid
    assert result.bundle is not None
    assert result.issues == []


def test_agent_prompt_and_schema_preserve_intent_bundle_contract():
    assert "IntentBundle" in AGENT_SYSTEM_PROMPT
    assert "CompositionIR is compiler output only" in AGENT_SYSTEM_PROMPT
    assert "substrate.nodes as an object keyed by node ID, never an array" in AGENT_SYSTEM_PROMPT
    assert AGENT_INTENT_BUNDLE_SCHEMA["$id"] == "https://viewspec.dev/agent-intent-bundle.schema.json"
    assert AGENT_INTENT_BUNDLE_SCHEMA["$defs"]["motif"]["properties"]["kind"]["enum"] == list(SUPPORTED_AGENT_MOTIFS)


def test_rejects_substrate_nodes_array():
    payload = _bundle_for_motif("dashboard")
    payload["substrate"]["nodes"] = list(payload["substrate"]["nodes"].values())

    assert "NODES_MUST_BE_OBJECT" in _issue_codes(payload)


@pytest.mark.parametrize("motif", ["form", "list", "detail", "chat"])
def test_rejects_unsupported_spec_motifs(motif):
    payload = _bundle_for_motif("dashboard")
    payload["view_spec"]["motifs"][0]["kind"] = motif

    assert "UNSUPPORTED_MOTIF" in _issue_codes(payload)


def test_rejects_missing_root_region():
    payload = _bundle_for_motif("dashboard")
    payload["view_spec"]["root_region"] = "missing"

    assert "MISSING_ROOT_REGION" in _issue_codes(payload)


def test_rejects_missing_substrate_root_node():
    payload = _bundle_for_motif("dashboard")
    payload["substrate"]["root_id"] = "missing"

    assert "MISSING_SUBSTRATE_ROOT" in _issue_codes(payload)


def test_rejects_invalid_binding_address():
    payload = _bundle_for_motif("dashboard")
    payload["view_spec"]["bindings"][0]["address"] = "node:missing#attr:value"

    assert "INVALID_ADDRESS" in _issue_codes(payload)


def test_rejects_unknown_region():
    payload = _bundle_for_motif("dashboard")
    payload["view_spec"]["bindings"][0]["target_region"] = "missing"
    payload["view_spec"]["groups"][0]["target_region"] = "missing"

    result = validate_agent_intent_bundle(payload, require_reference_compiler_support=False)
    paths = {issue.path for issue in result.issues if issue.code == "UNKNOWN_REGION"}

    assert "$.view_spec.bindings[0].target_region" in paths
    assert "$.view_spec.groups[0].target_region" in paths


def test_rejects_unknown_present_as():
    payload = _bundle_for_motif("dashboard")
    payload["view_spec"]["bindings"][0]["present_as"] = "chart"

    assert "UNKNOWN_PRESENT_AS" in _issue_codes(payload)


def test_rejects_duplicate_binding_id_and_exactly_once_address():
    payload = _bundle_for_motif("dashboard")
    duplicate = deepcopy(payload["view_spec"]["bindings"][0])
    payload["view_spec"]["bindings"].append(duplicate)

    codes = _issue_codes(payload)

    assert "DUPLICATE_BINDING_ID" in codes
    assert "DUPLICATE_EXACTLY_ONCE_ADDRESS" in codes


def test_rejects_missing_group_and_motif_members():
    payload = _bundle_for_motif("dashboard")
    payload["view_spec"]["groups"][0]["members"].append("missing_binding")
    payload["view_spec"]["motifs"][0]["members"].append("missing_binding")

    codes = _issue_codes(payload)

    assert "MISSING_GROUP_MEMBER" in codes
    assert "MISSING_MOTIF_MEMBER" in codes


def test_rejects_unknown_style_and_action_targets():
    payload = _bundle_for_motif("dashboard")
    payload["view_spec"]["styles"].append({"id": "bad_style", "target": "binding:missing", "token": "tone.muted"})
    payload["view_spec"]["actions"].append(
        {
            "id": "bad_action",
            "kind": "select",
            "label": "Select",
            "target_region": "missing",
            "target_ref": "binding:missing",
            "payload_bindings": ["missing"],
        }
    )

    codes = _issue_codes(payload)

    assert "UNKNOWN_STYLE_TARGET" in codes
    assert "UNKNOWN_ACTION_TARGET" in codes
    assert "UNKNOWN_ACTION_PAYLOAD_BINDING" in codes


def test_rejects_composition_ir_input():
    payload = {
        "id": "region_root",
        "primitive": "root",
        "children": [],
    }

    codes = _issue_codes(payload)

    assert "COMPOSITION_IR_INPUT" in codes


def test_correction_prompt_is_actionable_issue_json():
    payload = _bundle_for_motif("dashboard")
    payload["substrate"]["nodes"] = []
    result = validate_agent_intent_bundle(payload)
    prompt = agent_correction_prompt(result)

    assert "Output strict JSON only" in prompt
    assert "NODES_MUST_BE_OBJECT" in prompt
    assert "```" not in prompt
    assert len(prompt.splitlines()) < 30
