from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from viewspec import (
    AGENT_ASSET_CHECK_COMMAND,
    AGENT_ASSET_CONTRACT_PROFILE,
    AGENT_ASSET_EXPORT_COMMAND,
    AGENT_ASSET_MANIFEST_FILE,
    AGENT_ASSET_NETWORK_POLICY,
    AGENT_ASSET_SCHEMA_VERSION,
    AGENT_INTENT_BUNDLE_SCHEMA,
    AGENT_SYSTEM_PROMPT,
    AESTHETIC_PROFILE_TOKENS,
    SUPPORTED_AGENT_ACTION_KINDS,
    SUPPORTED_AGENT_CARDINALITIES,
    SUPPORTED_AGENT_GROUP_KINDS,
    SUPPORTED_AGENT_MOTIFS,
    SUPPORTED_AGENT_REGION_LAYOUTS,
    SUPPORTED_AGENT_STYLE_TOKENS,
    ViewSpecBuilder,
    agent_correction_prompt,
    agent_repair_checklist,
    export_agent_assets,
    starter_intent_bundle,
    validate_agent_intent_bundle,
)
from viewspec.agent import (
    MAX_AGENT_ACTION_PAYLOAD_BINDINGS,
    MAX_AGENT_CORRECTION_PROMPT_ISSUES,
    MAX_AGENT_INTENT_BYTES,
    MAX_AGENT_NODES,
    MAX_AGENT_RELATION_VALUES,
)
from viewspec.aesthetics import AESTHETIC_PROFILE_LAYOUT_PROPS, AESTHETIC_PROFILE_LAYOUT_ROLES, profile_style_facts
from viewspec.cli import main as cli_main


ROOT = Path(__file__).resolve().parents[1]


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
    elif motif == "list":
        items = builder.add_list("tasks", region="main", group_id="task_order")
        items.add_item(label="Plan", description="Define the UI intent", id="plan")
        items.add_item(label="Build", description="Compile a checked artifact", id="build")
    elif motif == "form":
        form = builder.add_form("contact", region="main", group_id="fields")
        form.add_field(label="Name", value="", id="name")
        form.add_field(label="Email", value="", id="email")
        builder.add_action(
            "submit_contact",
            "submit",
            "Submit",
            target_region="main",
            target_ref="motif:contact",
            payload_bindings=["name_value", "email_value"],
        )
    elif motif == "detail":
        detail = builder.add_detail("profile", region="main", group_id="fields")
        detail.add_field(label="Owner", value="Ada Lovelace", id="owner")
        detail.add_field(label="Status", value="Ready", id="status")
    elif motif == "empty_state":
        builder.add_empty_state(
            "no_results",
            title="No results yet",
            description="Adjust filters or create the first item.",
            region="main",
            group_id="message",
        )
    elif motif == "loading_state":
        builder.add_loading_state(
            "loading_results",
            title="Loading results",
            description="Fetching the current collection.",
            region="main",
            group_id="message",
        )
    elif motif == "error_state":
        builder.add_error_state(
            "error_results",
            title="Unable to load results",
            description="Retry after checking the source data.",
            region="main",
            group_id="message",
        )
    elif motif == "hero":
        builder.add_hero(
            "intro",
            eyebrow="Agent-native UI",
            title="Stop writing DOM",
            description="ViewSpec compiles intent into checked UI artifacts.",
            region="main",
            group_id="message",
        )
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
    assert "Use stable ids and object keys matching this pattern only" in AGENT_SYSTEM_PROMPT
    assert "substrate.nodes as an object keyed by node ID, never an array" in AGENT_SYSTEM_PROMPT
    assert "Stay inside the v1 local contract caps" in AGENT_SYSTEM_PROMPT
    assert "Use only these action kinds: select, submit, navigate, search, filter, sort, paginate, bulk_action." in AGENT_SYSTEM_PROMPT
    assert "input" in AGENT_SYSTEM_PROMPT
    assert "Use only this v1 binding cardinality: exactly_once." in AGENT_SYSTEM_PROMPT
    assert "Use only these v1 region layouts: stack, grid, cluster." in AGENT_SYSTEM_PROMPT
    assert "Use only this v1 group kind: ordered." in AGENT_SYSTEM_PROMPT
    assert "Do not include hosted-only fields in the local v1 contract" in AGENT_SYSTEM_PROMPT
    assert "Unknown fields are rejected instead of silently ignored" in AGENT_SYSTEM_PROMPT
    assert "Use style tokens only from this v1 set:" in AGENT_SYSTEM_PROMPT
    assert "Aesthetic profile tokens are deterministic art-direction handles, not CSS." in AGENT_SYSTEM_PROMPT
    for token in AESTHETIC_PROFILE_TOKENS:
        assert token in AGENT_SYSTEM_PROMPT
    assert "Semantic edges must reference declared substrate node IDs" in AGENT_SYSTEM_PROMPT
    assert "Action target_ref must be empty/null or use region:id" in AGENT_SYSTEM_PROMPT
    assert "Stateful collection actions are bounded." in AGENT_SYSTEM_PROMPT
    assert "Loading_state and error_state motifs need exactly one title" in AGENT_SYSTEM_PROMPT
    assert "Region parent links must form one acyclic tree" in AGENT_SYSTEM_PROMPT
    assert "Region min_children must be >= 0" in AGENT_SYSTEM_PROMPT
    assert "Generated JSON is not a finished ViewSpec proof" in AGENT_SYSTEM_PROMPT
    assert "viewspec prove --out .viewspec-proof" in AGENT_SYSTEM_PROMPT
    assert "viewspec validate-intent viewspec.intent.json --json" in AGENT_SYSTEM_PROMPT
    assert "viewspec check" in AGENT_SYSTEM_PROMPT
    assert ".viewspec-proof/PROOF.md" in AGENT_SYSTEM_PROMPT
    assert "proof_report.json" in AGENT_SYSTEM_PROMPT
    assert "support_bundle.json" in AGENT_SYSTEM_PROMPT
    assert "pixel-perfect visual regression" in AGENT_SYSTEM_PROMPT
    assert "viewspec diff-intent old.intent.json new.intent.json --json" in AGENT_SYSTEM_PROMPT
    assert "Review semantic_changes first" in AGENT_SYSTEM_PROMPT
    assert "compact aesthetic profile style impact counts and bounded layout deltas" in AGENT_SYSTEM_PROMPT
    assert "MCP semantic_summary" in AGENT_SYSTEM_PROMPT
    assert 'intent_semantic_change_lines(diff["semantic_changes"])' in AGENT_SYSTEM_PROMPT
    assert "Do not call remote reference libraries by default" in AGENT_SYSTEM_PROMPT
    assert "query an MCP-accessible UI reference library" not in AGENT_SYSTEM_PROMPT
    assert AGENT_INTENT_BUNDLE_SCHEMA["$id"] == "https://viewspec.dev/agent-intent-bundle.schema.json"
    assert AGENT_INTENT_BUNDLE_SCHEMA["not"] == {
        "anyOf": [{"required": ["design"]}, {"required": ["motif_library"]}]
    }
    assert AGENT_INTENT_BUNDLE_SCHEMA["$defs"]["motif"]["properties"]["kind"]["enum"] == list(SUPPORTED_AGENT_MOTIFS)
    assert AGENT_INTENT_BUNDLE_SCHEMA["$defs"]["action"]["properties"]["kind"]["enum"] == list(SUPPORTED_AGENT_ACTION_KINDS)
    assert AGENT_INTENT_BUNDLE_SCHEMA["$defs"]["binding"]["properties"]["cardinality"]["enum"] == list(SUPPORTED_AGENT_CARDINALITIES)
    assert "input" in AGENT_INTENT_BUNDLE_SCHEMA["$defs"]["binding"]["properties"]["present_as"]["enum"]
    assert AGENT_INTENT_BUNDLE_SCHEMA["$defs"]["group"]["properties"]["kind"]["enum"] == list(SUPPORTED_AGENT_GROUP_KINDS)
    assert AGENT_INTENT_BUNDLE_SCHEMA["$defs"]["region"]["properties"]["layout"]["enum"] == list(SUPPORTED_AGENT_REGION_LAYOUTS)
    assert AGENT_INTENT_BUNDLE_SCHEMA["$defs"]["region"]["properties"]["role"]["minLength"] == 1
    assert AGENT_INTENT_BUNDLE_SCHEMA["$defs"]["region"]["properties"]["min_children"]["minimum"] == 0
    assert AGENT_INTENT_BUNDLE_SCHEMA["$defs"]["region"]["properties"]["max_children"]["anyOf"][0]["minimum"] == 0
    assert AGENT_INTENT_BUNDLE_SCHEMA["$defs"]["substrate_node"]["properties"]["attrs"]["propertyNames"]["pattern"] == "^[A-Za-z0-9_.-]+$"
    assert AGENT_INTENT_BUNDLE_SCHEMA["$defs"]["substrate_node"]["properties"]["edges"]["additionalProperties"]["$ref"] == "#/$defs/edge_values"
    assert AGENT_INTENT_BUNDLE_SCHEMA["$defs"]["edge_values"]["properties"]["values"]["items"]["pattern"] == "^[A-Za-z0-9_.-]+$"
    for definition in ("substrate", "substrate_node", "region", "binding", "group", "motif", "style", "action", "view_spec"):
        assert AGENT_INTENT_BUNDLE_SCHEMA["$defs"][definition]["additionalProperties"] is False
    assert AGENT_INTENT_BUNDLE_SCHEMA["$defs"]["view_spec"]["not"] == {
        "anyOf": [
            {"required": ["inputs"]},
            {"required": ["projections"]},
            {"required": ["rules"]},
        ]
    }
    target_ref_schema = AGENT_INTENT_BUNDLE_SCHEMA["$defs"]["action"]["properties"]["target_ref"]["anyOf"]
    assert {"const": ""} in target_ref_schema
    assert {"type": "null"} in target_ref_schema
    assert target_ref_schema[0]["pattern"] == "^(region|binding|motif|view):[A-Za-z0-9_.-]+$"
    assert AGENT_INTENT_BUNDLE_SCHEMA["$defs"]["action"]["properties"]["label"]["minLength"] == 1
    assert AGENT_INTENT_BUNDLE_SCHEMA["$defs"]["style"]["properties"]["target"]["pattern"] == "^(?:(?:region|binding|motif|view):)?[A-Za-z0-9_.-]+$"
    assert AGENT_INTENT_BUNDLE_SCHEMA["$defs"]["style"]["properties"]["token"]["enum"] == list(SUPPORTED_AGENT_STYLE_TOKENS)
    profile_contract = AGENT_INTENT_BUNDLE_SCHEMA["x-viewspec-aesthetic-profiles"]
    assert profile_contract["tokens"] == list(AESTHETIC_PROFILE_TOKENS)
    assert profile_contract["style_token_prefix"] == "aesthetic."
    assert profile_contract["max_declarations"] == 1
    assert profile_contract["target"] == "view:{view_spec.id}"
    assert profile_contract["layout_roles"] == sorted(AESTHETIC_PROFILE_LAYOUT_ROLES)
    assert profile_contract["layout_props"] == {
        profile: {role: dict(props) for role, props in role_props.items()}
        for profile, role_props in AESTHETIC_PROFILE_LAYOUT_PROPS.items()
    }
    assert profile_contract["style_facts"] == {profile: profile_style_facts(profile) for profile in AESTHETIC_PROFILE_TOKENS}
    assert "not_css" in profile_contract["non_claims"]
    assert AGENT_INTENT_BUNDLE_SCHEMA["$defs"]["substrate"]["properties"]["nodes"]["maxProperties"] == MAX_AGENT_NODES
    assert AGENT_INTENT_BUNDLE_SCHEMA["$defs"]["substrate_node"]["properties"]["kind"] == {"type": "string", "minLength": 1}
    invariants = AGENT_INTENT_BUNDLE_SCHEMA["x-viewspec-invariants"]
    assert "view_spec.substrate_id must equal substrate.id." in invariants
    assert "Each substrate.nodes object key must equal that node object's id." in invariants
    assert "Semantic node edges must reference declared substrate node ids." in invariants
    assert any(
        "Hosted-only fields design, motif_library, view_spec.inputs, view_spec.projections, and view_spec.rules are rejected by the local schema" in item
        for item in invariants
    )
    assert "IntentBundle may declare at most one aesthetic.* style token." in invariants
    assert "Aesthetic profile style token must target exactly view:{view_spec.id}." in invariants
    assert any("bounded layout metadata for content_grid, metric_grid, and featured metric_card roles" in item for item in invariants)
    assert "Unknown extension fields are rejected instead of silently ignored." in invariants
    assert "viewspec validate-intent enforces cross-reference invariants." in invariants[-1]


def test_published_agent_schema_matches_runtime_contract():
    published = json.loads(ROOT.joinpath("demos/agent-intent-bundle.schema.json").read_text(encoding="utf-8"))

    assert published == AGENT_INTENT_BUNDLE_SCHEMA


def test_published_agent_prompt_matches_runtime_contract():
    published = ROOT.joinpath("demos/agent-system-prompt.txt").read_text(encoding="utf-8")

    assert published == AGENT_SYSTEM_PROMPT


def test_published_agent_example_matches_runtime_starter(tmp_path, capsys):
    example_path = ROOT.joinpath("demos/agent-intent-example.dashboard.json")
    published = json.loads(example_path.read_text(encoding="utf-8"))

    assert published == starter_intent_bundle("dashboard").to_json()
    assert validate_agent_intent_bundle(published).valid
    assert cli_main(["validate-intent", str(example_path), "--json"]) == 0
    capsys.readouterr()

    html_out = tmp_path / "published-html"
    react_out = tmp_path / "published-react"
    assert cli_main(["compile", str(example_path), "--out", str(html_out)]) == 0
    capsys.readouterr()
    assert cli_main(["check", str(html_out), "--json"]) == 0
    capsys.readouterr()
    assert cli_main(["compile", str(example_path), "--target", "react-tsx", "--out", str(react_out)]) == 0
    capsys.readouterr()
    assert cli_main(["check", str(react_out), "--json"]) == 0
    capsys.readouterr()


def test_published_agent_asset_manifest_matches_runtime_export(tmp_path):
    export_agent_assets(tmp_path)
    published = json.loads(ROOT.joinpath("demos/agent-assets.json").read_text(encoding="utf-8"))
    exported = json.loads(tmp_path.joinpath(AGENT_ASSET_MANIFEST_FILE).read_text(encoding="utf-8"))

    assert published == exported
    assert published["schema_version"] == AGENT_ASSET_SCHEMA_VERSION
    assert published["contract"] == {
        "profile": AGENT_ASSET_CONTRACT_PROFILE,
        "intent_schema_id": AGENT_INTENT_BUNDLE_SCHEMA["$id"],
        "export_command": AGENT_ASSET_EXPORT_COMMAND,
        "check_command": AGENT_ASSET_CHECK_COMMAND,
        "network_policy": AGENT_ASSET_NETWORK_POLICY,
        "files": {
            "manifest": "agent-assets.json",
            "system_prompt": "agent-system-prompt.txt",
            "intent_schema": "agent-intent-bundle.schema.json",
            "intent_example": "agent-intent-example.dashboard.json",
        },
    }


def test_published_openapi_agent_artifacts_match_runtime_contract():
    openapi = json.loads(ROOT.joinpath("demos/openapi.json").read_text(encoding="utf-8"))
    artifacts = openapi["x-viewspec-agent-artifacts"]
    schemas = openapi["components"]["schemas"]
    compile_request_ref = openapi["paths"]["/v1/compile"]["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"]

    assert artifacts["assetSchemaVersion"] == AGENT_ASSET_SCHEMA_VERSION
    assert artifacts["assetManifest"] == "https://viewspec.dev/agent-assets.json"
    assert artifacts["systemPrompt"] == "https://viewspec.dev/agent-system-prompt.txt"
    assert artifacts["intentBundleSchema"] == AGENT_INTENT_BUNDLE_SCHEMA["$id"]
    assert artifacts["intentBundleExample"] == "https://viewspec.dev/agent-intent-example.dashboard.json"
    assert artifacts["contractProfile"] == AGENT_ASSET_CONTRACT_PROFILE
    assert artifacts["exportCommand"] == AGENT_ASSET_EXPORT_COMMAND
    assert artifacts["checkCommand"] == AGENT_ASSET_CHECK_COMMAND
    assert artifacts["networkPolicy"] == AGENT_ASSET_NETWORK_POLICY
    assert compile_request_ref == "#/components/schemas/CompileRequestPayload"
    assert schemas["CompileRequestPayload"]["properties"]["design"]["$ref"] == "#/components/schemas/DesignRequest"
    assert "hosted-only" in schemas["CompileRequestPayload"]["description"]
    assert "design" not in schemas["IntentBundle"]["properties"]
    assert "motif_library" not in schemas["IntentBundle"]["properties"]


def test_rejects_substrate_nodes_array():
    payload = _bundle_for_motif("dashboard")
    payload["substrate"]["nodes"] = list(payload["substrate"]["nodes"].values())

    assert "NODES_MUST_BE_OBJECT" in _issue_codes(payload)


def test_rejects_unsafe_ids_and_object_keys():
    payload = _bundle_for_motif("dashboard")
    payload["view_spec"]["id"] = "bad/view"
    first_node = next(iter(payload["substrate"]["nodes"].values()))
    first_node["attrs"]["bad key"] = "unsafe"
    first_node["slots"]["bad:slot"] = {"values": []}

    assert "INVALID_ID" in _issue_codes(payload)


@pytest.mark.parametrize("motif", ["chat", "feed"])
def test_rejects_unsupported_spec_motifs(motif):
    payload = _bundle_for_motif("dashboard")
    payload["view_spec"]["motifs"][0]["kind"] = motif

    assert "UNSUPPORTED_MOTIF" in _issue_codes(payload)


def test_rejects_unsupported_action_kind():
    payload = _bundle_for_motif("dashboard")
    payload["view_spec"]["actions"].append(
        {
            "id": "bad_action",
            "kind": "delete_everything",
            "label": "Bad action",
            "target_region": "main",
            "target_ref": None,
            "payload_bindings": [],
        }
    )

    assert "UNSUPPORTED_ACTION_KIND" in _issue_codes(payload)


def test_rejects_action_missing_required_target_ref_key():
    payload = _bundle_for_motif("dashboard")
    payload["view_spec"]["actions"].append(
        {
            "id": "missing_target_ref",
            "kind": "select",
            "label": "Select",
            "target_region": "main",
            "payload_bindings": [],
        }
    )

    result = validate_agent_intent_bundle(payload, require_reference_compiler_support=False)

    assert any(
        issue.code == "MISSING_FIELD" and issue.path == "$.view_spec.actions[0].target_ref"
        for issue in result.issues
    )


def test_rejects_action_target_ref_with_invalid_type():
    payload = _bundle_for_motif("dashboard")
    payload["view_spec"]["actions"].append(
        {
            "id": "invalid_target_ref",
            "kind": "select",
            "label": "Select",
            "target_region": "main",
            "target_ref": ["binding:revenue_value"],
            "payload_bindings": [],
        }
    )

    result = validate_agent_intent_bundle(payload, require_reference_compiler_support=False)

    assert any(
        issue.code == "MISSING_FIELD" and issue.path == "$.view_spec.actions[0].target_ref"
        for issue in result.issues
    )


def test_rejects_action_target_ref_without_explicit_kind():
    payload = _bundle_for_motif("dashboard")
    payload["view_spec"]["actions"].append(
        {
            "id": "bare_target",
            "kind": "select",
            "label": "Select",
            "target_region": "main",
            "target_ref": "revenue_value",
            "payload_bindings": [],
        }
    )

    result = validate_agent_intent_bundle(payload, require_reference_compiler_support=False)

    assert any(
        issue.code == "INVALID_ACTION_TARGET_REF" and issue.path == "$.view_spec.actions[0].target_ref"
        for issue in result.issues
    )


def test_rejects_missing_root_region():
    payload = _bundle_for_motif("dashboard")
    payload["view_spec"]["root_region"] = "missing"

    assert "MISSING_ROOT_REGION" in _issue_codes(payload)


def test_rejects_unsupported_region_layout():
    payload = _bundle_for_motif("dashboard")
    payload["view_spec"]["regions"][1]["layout"] = "masonry"

    assert "UNSUPPORTED_REGION_LAYOUT" in _issue_codes(payload)


def test_rejects_invalid_complexity_tier_and_region_child_bounds():
    payload = _bundle_for_motif("dashboard")
    payload["view_spec"]["complexity_tier"] = 0
    payload["view_spec"]["regions"][0]["min_children"] = -1
    payload["view_spec"]["regions"][1]["min_children"] = 3
    payload["view_spec"]["regions"][1]["max_children"] = 2

    codes = _issue_codes(payload)

    assert "INVALID_COMPLEXITY_TIER" in codes
    assert "INVALID_REGION_CHILD_BOUNDS" in codes


def test_rejects_non_integer_region_child_bounds_before_proto_parse():
    payload = _bundle_for_motif("dashboard")
    payload["view_spec"]["complexity_tier"] = True
    payload["view_spec"]["regions"][0]["max_children"] = "many"

    result = validate_agent_intent_bundle(payload, require_reference_compiler_support=False)

    assert any(issue.code == "MISSING_FIELD" and issue.path == "$.view_spec.complexity_tier" for issue in result.issues)
    assert any(issue.code == "MISSING_FIELD" and issue.path == "$.view_spec.regions[0].max_children" for issue in result.issues)
    assert not any(issue.code == "INTENT_BUNDLE_PARSE_ERROR" for issue in result.issues)


def test_rejects_unsupported_group_kind():
    payload = _bundle_for_motif("dashboard")
    payload["view_spec"]["groups"][0]["kind"] = "unordered"

    assert "UNSUPPORTED_GROUP_KIND" in _issue_codes(payload)


def test_rejects_root_region_with_parent():
    payload = _bundle_for_motif("dashboard")
    payload["view_spec"]["regions"][0]["parent_region"] = "main"

    assert "ROOT_REGION_HAS_PARENT" in _issue_codes(payload)


def test_rejects_detached_non_root_region():
    payload = _bundle_for_motif("dashboard")
    payload["view_spec"]["regions"].append(
        {
            "id": "aside",
            "parent_region": "",
            "role": "complementary",
            "layout": "stack",
            "min_children": 0,
            "max_children": None,
        }
    )

    assert "DETACHED_REGION" in _issue_codes(payload)


def test_rejects_region_parent_cycle():
    payload = _bundle_for_motif("dashboard")
    payload["view_spec"]["regions"].extend(
        [
            {
                "id": "left",
                "parent_region": "right",
                "role": "section",
                "layout": "stack",
                "min_children": 0,
                "max_children": None,
            },
            {
                "id": "right",
                "parent_region": "left",
                "role": "section",
                "layout": "stack",
                "min_children": 0,
                "max_children": None,
            },
        ]
    )

    assert "REGION_PARENT_CYCLE" in _issue_codes(payload)


def test_rejects_outline_semantic_cycle_from_reference_compiler():
    payload = _bundle_for_motif("outline")
    root_id = payload["substrate"]["root_id"]
    nodes = payload["substrate"]["nodes"]
    nodes[root_id]["slots"] = {"items": {"values": ["plan"]}}
    nodes["plan"]["slots"] = {"items": {"values": ["build"]}}
    nodes["build"]["slots"] = {"items": {"values": ["plan"]}}

    result = validate_agent_intent_bundle(payload)

    assert not result.valid
    assert any(issue.code == "SEMANTIC_GRAPH_CYCLE" for issue in result.issues)


def test_rejects_missing_semantic_edge_target():
    payload = _bundle_for_motif("dashboard")
    root_id = payload["substrate"]["root_id"]
    payload["substrate"]["nodes"][root_id]["edges"] = {"next": {"values": ["missing_node"]}}

    result = validate_agent_intent_bundle(payload, require_reference_compiler_support=False)

    assert any(
        issue.code == "UNKNOWN_EDGE_TARGET" and issue.path == f"$.substrate.nodes.{root_id}.edges.next.values[0]"
        for issue in result.issues
    )


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


def test_rejects_unsupported_cardinality():
    payload = _bundle_for_motif("dashboard")
    payload["view_spec"]["bindings"][0]["cardinality"] = "optional"

    assert "UNSUPPORTED_CARDINALITY" in _issue_codes(payload)


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


def test_rejects_empty_motif_before_empty_artifact_compile():
    payload = _bundle_for_motif("dashboard")
    payload["view_spec"]["motifs"][0]["members"] = []

    result = validate_agent_intent_bundle(payload)

    assert not result.valid
    assert any(issue.code == "EMPTY_MOTIF" and issue.path == "$.view_spec.motifs[0].members" for issue in result.issues)


@pytest.mark.parametrize("motif", ["hero", "empty_state"])
def test_rejects_title_required_motifs_without_title_binding(motif):
    payload = _bundle_for_motif(motif)
    payload["view_spec"]["motifs"][0]["members"] = [
        member
        for member in payload["view_spec"]["motifs"][0]["members"]
        if not member.endswith("_title")
    ]

    result = validate_agent_intent_bundle(payload)

    assert not result.valid
    assert any(issue.code == "MOTIF_MISSING_TITLE" for issue in result.issues)


@pytest.mark.parametrize("motif", ["loading_state", "error_state"])
def test_rejects_state_motifs_without_exactly_one_title_binding(motif):
    payload = _bundle_for_motif(motif)
    payload["view_spec"]["motifs"][0]["members"] = [
        member
        for member in payload["view_spec"]["motifs"][0]["members"]
        if not member.endswith("_title")
    ]

    result = validate_agent_intent_bundle(payload)

    assert not result.valid
    assert any(issue.code == "STATE_MOTIF_TITLE_REQUIRED" for issue in result.issues)


def test_rejects_state_motif_with_too_many_descriptions():
    payload = _bundle_for_motif("loading_state")
    payload["substrate"]["nodes"]["loading_results"]["attrs"]["message"] = "Second description"
    payload["view_spec"]["bindings"].append(
        {
            "id": "loading_results_message",
            "address": "node:loading_results#attr:message",
            "target_region": "main",
            "present_as": "text",
            "cardinality": "exactly_once",
        }
    )
    payload["view_spec"]["motifs"][0]["members"].append("loading_results_message")

    result = validate_agent_intent_bundle(payload)

    assert not result.valid
    assert any(issue.code == "STATE_MOTIF_TOO_MANY_DESCRIPTIONS" for issue in result.issues)


def test_rejects_collection_action_contract_violations():
    payload = _bundle_for_motif("table")
    payload["view_spec"]["actions"].extend(
        [
            {
                "id": "search_missing_payload",
                "kind": "search",
                "label": "Search",
                "target_region": "main",
                "target_ref": "motif:items",
                "payload_bindings": [],
            },
            {
                "id": "filter_wrong_target",
                "kind": "filter",
                "label": "Filter",
                "target_region": "main",
                "target_ref": "motif:missing",
                "payload_bindings": ["alpha_label"],
            },
            {
                "id": "bulk_missing_selection",
                "kind": "bulk_action",
                "label": "Bulk",
                "target_region": "main",
                "target_ref": "motif:items",
                "payload_bindings": ["alpha_label"],
            },
        ]
    )

    codes = _issue_codes(payload)

    assert "COLLECTION_ACTION_PAYLOAD_REQUIRED" in codes
    assert "COLLECTION_ACTION_TARGET_INVALID" in codes
    assert "COLLECTION_BULK_SELECTION_REQUIRED" in codes


def test_rejects_too_many_collection_actions_and_state_conflicts():
    payload = _bundle_for_motif("table")
    for index in range(9):
        payload["view_spec"]["actions"].append(
            {
                "id": f"search_items_{index}",
                "kind": "search",
                "label": f"Search {index}",
                "target_region": "main",
                "target_ref": "motif:items",
                "payload_bindings": ["alpha_label"],
            }
        )

    assert "TOO_MANY_COLLECTION_ACTIONS" in _issue_codes(payload)

    payload = _bundle_for_motif("table")
    state_payload = _bundle_for_motif("loading_state")
    payload["substrate"]["nodes"].update(state_payload["substrate"]["nodes"])
    payload["view_spec"]["bindings"].extend(state_payload["view_spec"]["bindings"])
    payload["view_spec"]["groups"].extend(state_payload["view_spec"]["groups"])
    payload["view_spec"]["motifs"].extend(state_payload["view_spec"]["motifs"])

    assert "COLLECTION_STATE_CONFLICT" in _issue_codes(payload)


def test_rejects_form_motif_without_input_binding():
    payload = _bundle_for_motif("form")
    payload["view_spec"]["motifs"][0]["members"] = [
        member
        for member in payload["view_spec"]["motifs"][0]["members"]
        if not member.endswith("_value")
    ]

    result = validate_agent_intent_bundle(payload)

    assert not result.valid
    assert any(issue.code == "MOTIF_MISSING_INPUT" for issue in result.issues)


@pytest.mark.parametrize("motif", ["table", "dashboard", "detail"])
def test_rejects_label_value_motifs_without_value_binding(motif):
    payload = _bundle_for_motif(motif)
    payload["view_spec"]["motifs"][0]["members"] = [
        member
        for member in payload["view_spec"]["motifs"][0]["members"]
        if member.endswith("_label")
    ]

    result = validate_agent_intent_bundle(payload)

    assert not result.valid
    assert any(issue.code == "MOTIF_MISSING_VALUE" for issue in result.issues)


def test_rejects_comparison_with_one_semantic_item():
    payload = _bundle_for_motif("comparison")
    payload["view_spec"]["motifs"][0]["members"] = [
        member
        for member in payload["view_spec"]["motifs"][0]["members"]
        if member.startswith("free_")
    ]

    result = validate_agent_intent_bundle(payload)

    assert not result.valid
    assert any(issue.code == "MOTIF_TOO_FEW_ITEMS" for issue in result.issues)


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


def test_rejects_ambiguous_bare_style_target():
    payload = _bundle_for_motif("dashboard")
    payload["substrate"]["nodes"]["style_source"] = {
        "id": "style_source",
        "kind": "metric",
        "attrs": {"label": "Style source"},
        "slots": {},
        "edges": {},
    }
    payload["view_spec"]["bindings"].append(
        {
            "id": "main",
            "address": "node:style_source#attr:label",
            "target_region": "main",
            "present_as": "label",
            "cardinality": "exactly_once",
        }
    )
    payload["view_spec"]["styles"].append({"id": "ambiguous_style", "target": "main", "token": "tone.muted"})

    assert "AMBIGUOUS_STYLE_TARGET" in _issue_codes(payload)


def test_rejects_unsupported_style_token():
    payload = _bundle_for_motif("dashboard")
    payload["view_spec"]["styles"].append({"id": "bad_style", "target": "binding:revenue_value", "token": "css.position.fixed"})

    assert "UNSUPPORTED_STYLE_TOKEN" in _issue_codes(payload)


def test_accepts_view_scoped_aesthetic_profile_token():
    payload = _bundle_for_motif("dashboard")
    payload["view_spec"]["styles"].append(
        {"id": "aesthetic_profile", "target": "view:agent_dashboard", "token": "aesthetic.calm_ops"}
    )

    result = validate_agent_intent_bundle(payload)

    assert result.valid
    assert not any(issue.code.startswith("AESTHETIC_PROFILE_") for issue in result.issues)


@pytest.mark.parametrize(
    ("styles", "expected_code"),
    [
        (
            [{"id": "aesthetic_profile", "target": "view:agent_dashboard", "token": "aesthetic.noir"}],
            "AESTHETIC_PROFILE_UNKNOWN",
        ),
        (
            [{"id": "aesthetic_profile", "target": "motif:kpis", "token": "aesthetic.calm_ops"}],
            "AESTHETIC_PROFILE_TARGET_INVALID",
        ),
        (
            [
                {"id": "profile_a", "target": "view:agent_dashboard", "token": "aesthetic.calm_ops"},
                {"id": "profile_b", "target": "view:agent_dashboard", "token": "aesthetic.data_dense"},
            ],
            "AESTHETIC_PROFILE_MULTIPLE",
        ),
    ],
)
def test_rejects_invalid_aesthetic_profile_tokens(styles, expected_code):
    payload = _bundle_for_motif("dashboard")
    payload["view_spec"]["styles"].extend(styles)

    assert expected_code in _issue_codes(payload)


def test_rejects_composition_ir_input():
    payload = {
        "id": "region_root",
        "primitive": "root",
        "children": [],
    }

    codes = _issue_codes(payload)

    assert "COMPOSITION_IR_INPUT" in codes


def test_rejects_hosted_only_fields_without_cascading_payload_binding_noise():
    payload = _bundle_for_motif("dashboard")
    payload["design"] = {"format": "design.md", "content": "name: Acme\n"}
    payload["motif_library"] = {"cards": {}}
    payload["view_spec"]["inputs"] = [{"id": "phase_filter"}]
    payload["view_spec"]["projections"] = []
    payload["view_spec"]["rules"] = [{"id": "show_mobile_note"}]
    payload["view_spec"]["actions"].append(
        {
            "id": "save",
            "kind": "submit",
            "label": "Save",
            "target_region": "main",
            "target_ref": None,
            "payload_bindings": ["phase_filter"],
        }
    )

    result = validate_agent_intent_bundle(payload)
    paths = {issue.path for issue in result.issues if issue.code == "HOSTED_ONLY_FIELD"}

    assert not result.valid
    assert paths == {
        "$.design",
        "$.motif_library",
        "$.view_spec.inputs",
        "$.view_spec.projections",
        "$.view_spec.rules",
    }
    assert not any(issue.code == "UNKNOWN_ACTION_PAYLOAD_BINDING" for issue in result.issues)


def test_rejects_unknown_extension_fields_before_proto_parse():
    payload = _bundle_for_motif("dashboard")
    root_id = payload["substrate"]["root_id"]
    payload["unexpected"] = True
    payload["substrate"]["x_extra"] = True
    payload["substrate"]["nodes"][root_id]["css"] = {"position": "fixed"}
    payload["view_spec"]["x_extra"] = True
    payload["view_spec"]["regions"][0]["css"] = "display: contents"
    payload["view_spec"]["bindings"][0]["formatter"] = "currency"
    payload["view_spec"]["groups"][0]["layout_hint"] = "masonry"
    payload["view_spec"]["motifs"][0]["renderer"] = "custom"
    payload["view_spec"]["styles"].append({"id": "style", "target": "binding:revenue_value", "token": "tone.muted", "css": "color:red"})
    payload["view_spec"]["actions"].append(
        {
            "id": "save",
            "kind": "submit",
            "label": "Save",
            "target_region": "main",
            "target_ref": None,
            "payload_bindings": [],
            "fetch": "https://example.com",
        }
    )

    result = validate_agent_intent_bundle(payload)
    paths = {issue.path for issue in result.issues if issue.code == "UNKNOWN_FIELD"}

    assert not result.valid
    assert {
        "$.unexpected",
        "$.substrate.x_extra",
        f"$.substrate.nodes.{root_id}.css",
        "$.view_spec.x_extra",
        "$.view_spec.regions[0].css",
        "$.view_spec.bindings[0].formatter",
        "$.view_spec.groups[0].layout_hint",
        "$.view_spec.motifs[0].renderer",
        "$.view_spec.styles[0].css",
        "$.view_spec.actions[0].fetch",
    }.issubset(paths)
    assert not any(issue.code == "INTENT_BUNDLE_PARSE_ERROR" for issue in result.issues)


def test_rejects_oversized_intent_text_before_json_parse():
    payload = " " * (MAX_AGENT_INTENT_BYTES + 1)

    result = validate_agent_intent_bundle(payload)

    assert not result.valid
    assert result.issues[0].code == "INTENT_TOO_LARGE"


def test_rejects_oversized_intent_dict_before_shape_validation():
    payload = _bundle_for_motif("dashboard")
    root_id = payload["substrate"]["root_id"]
    payload["substrate"]["nodes"][root_id]["attrs"]["blob"] = "x" * MAX_AGENT_INTENT_BYTES

    result = validate_agent_intent_bundle(payload)

    assert not result.valid
    assert result.issues[0].code == "INTENT_TOO_LARGE"


def test_rejects_non_json_serializable_intent_dict_before_proto_parse():
    payload = _bundle_for_motif("dashboard")
    root_id = payload["substrate"]["root_id"]
    payload["substrate"]["nodes"][root_id]["attrs"]["bad"] = object()

    result = validate_agent_intent_bundle(payload)

    assert not result.valid
    assert result.issues[0].code == "INVALID_JSON_VALUE"


def test_rejects_non_finite_intent_dict_before_proto_parse():
    payload = _bundle_for_motif("dashboard")
    root_id = payload["substrate"]["root_id"]
    payload["substrate"]["nodes"][root_id]["attrs"]["bad"] = float("inf")

    result = validate_agent_intent_bundle(payload)

    assert not result.valid
    assert result.issues[0].code == "INVALID_JSON_VALUE"
    assert "Out of range float values" in result.issues[0].message


def test_rejects_too_many_substrate_nodes():
    payload = _bundle_for_motif("dashboard")
    payload["substrate"]["root_id"] = "node_0"
    payload["substrate"]["nodes"] = {
        f"node_{index}": {
            "id": f"node_{index}",
            "kind": "item",
            "attrs": {},
            "slots": {},
            "edges": {},
        }
        for index in range(MAX_AGENT_NODES + 1)
    }

    assert "TOO_MANY_NODES" in _issue_codes(payload)


def test_rejects_too_many_slot_values():
    payload = _bundle_for_motif("dashboard")
    first_node = next(iter(payload["substrate"]["nodes"].values()))
    first_node["slots"]["items"] = {"values": [f"node_{index}" for index in range(MAX_AGENT_RELATION_VALUES + 1)]}

    assert "TOO_MANY_RELATION_VALUES" in _issue_codes(payload)


def test_rejects_too_many_action_payload_bindings():
    payload = _bundle_for_motif("dashboard")
    payload["view_spec"]["actions"].append(
        {
            "id": "bulk_action",
            "kind": "select",
            "label": "Bulk action",
            "target_region": "main",
            "target_ref": None,
            "payload_bindings": [f"binding_{index}" for index in range(MAX_AGENT_ACTION_PAYLOAD_BINDINGS + 1)],
        }
    )

    assert "TOO_MANY_ACTION_PAYLOAD_BINDINGS" in _issue_codes(payload)


def test_correction_prompt_is_actionable_issue_json():
    payload = _bundle_for_motif("dashboard")
    payload["substrate"]["nodes"] = []
    result = validate_agent_intent_bundle(payload)
    prompt = agent_correction_prompt(result)
    report = json.loads(prompt.splitlines()[-1])

    assert "Output strict JSON only" in prompt
    assert "Do not patch fragments" in prompt
    assert "NODES_MUST_BE_OBJECT" in prompt
    assert "suggestion" in prompt
    assert report["repair_mode"] == "regenerate_full_intent_bundle"
    assert report["retry_command"] == "viewspec validate-intent viewspec.intent.json --json"
    assert report["issue_codes"] == ["NODES_MUST_BE_OBJECT"]
    assert report["affected_paths"] == ["$.substrate.nodes"]
    assert any("required local V1" in item for item in report["repair_checklist"])
    assert "```" not in prompt
    assert len(prompt.splitlines()) < 30


def test_correction_prompt_is_bounded_and_all_issue_json_has_suggestions():
    payload = _bundle_for_motif("dashboard")
    for index in range(MAX_AGENT_CORRECTION_PROMPT_ISSUES + 5):
        payload[f"extra_{index}"] = True

    result = validate_agent_intent_bundle(payload)
    prompt = agent_correction_prompt(result)
    report = json.loads(prompt.splitlines()[-1])

    assert not result.valid
    assert len(result.issues) > MAX_AGENT_CORRECTION_PROMPT_ISSUES
    assert report["issue_count"] == len(result.issues)
    assert report["shown_issue_count"] == MAX_AGENT_CORRECTION_PROMPT_ISSUES
    assert report["truncated"] is True
    assert report["issue_codes"] == ["UNKNOWN_FIELD"]
    assert report["affected_paths"] == [f"$.extra_{index}" for index in range(MAX_AGENT_CORRECTION_PROMPT_ISSUES)]
    assert len(report["issues"]) == MAX_AGENT_CORRECTION_PROMPT_ISSUES
    assert 1 <= len(report["repair_checklist"]) <= 8
    assert all(issue["suggestion"] for issue in report["issues"])
    assert all(issue.to_json()["suggestion"] for issue in result.issues)


def test_agent_repair_checklist_maps_issue_families():
    payload = _bundle_for_motif("dashboard")
    payload["view_spec"]["motifs"][0]["members"] = []
    payload["view_spec"]["bindings"][0]["address"] = "bad/address"

    result = validate_agent_intent_bundle(payload)
    checklist = agent_repair_checklist(result)

    assert not result.valid
    assert any("canonical node addresses" in item for item in checklist)
    assert any("semantically complete" in item for item in checklist)
    assert checklist[-1] == "Regenerate the full IntentBundle, then rerun viewspec validate-intent before compiling."
    assert len(checklist) <= 8
