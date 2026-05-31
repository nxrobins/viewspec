from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from viewspec import ViewSpecBuilder
from viewspec.agent import AGENT_INTENT_BUNDLE_SCHEMA, AGENT_SYSTEM_PROMPT
from viewspec.cli import main as cli_main
from viewspec.emitters.html_tailwind import ACTION_EVENT_SCRIPT
from viewspec.intent_tools import (
    agent_correction_prompt_file_tool,
    compile_intent_bundle_file_tool,
    diff_intent_bundle_files_tool,
    init_intent_tool,
    validate_intent_bundle_file_tool,
)
from viewspec.local_tools import (
    LocalToolError,
    MCP_RESULT_SCHEMA_VERSION,
    check_artifact_tool,
    compile_html_file_tool,
    diff_html_files_tool,
    export_agent_assets_tool,
    file_hash,
    init_design_tool,
    lift_html_file_tool,
    tool_response,
)
from viewspec.mcp_server import MCP_INSTALL_HINT, MissingMCPDependency


def _bundle_json() -> dict:
    builder = ViewSpecBuilder("mcp_intent")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Alpha", value="1", id="alpha")
    return builder.build_bundle().to_json()


def _bundle_with_action_json() -> dict:
    builder = ViewSpecBuilder("mcp_action")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Alpha", value="1", id="alpha")
    builder.add_action(
        "open_alpha",
        "select",
        "Open Alpha",
        target_region="main",
        target_ref="binding:alpha_label",
        payload_bindings=["alpha_label", "alpha_value"],
    )
    return builder.build_bundle().to_json()


def _bundle_with_input_action_json() -> dict:
    builder = ViewSpecBuilder("mcp_input")
    builder.add_node("draft", "field", attrs={"label": "Message", "value": "Hello"})
    builder.bind_attr("draft_label", "draft", "label", region="main", present_as="label")
    builder.bind_attr("draft_value", "draft", "value", region="main", present_as="input")
    builder.add_action("send", "submit", "Send", target_region="main", payload_bindings=["draft_value"])
    return builder.build_bundle().to_json()


def _bundle_with_image_slot_json() -> dict:
    builder = ViewSpecBuilder("mcp_image")
    builder.add_node("hero", "asset", attrs={"alt": "Hero Preview"})
    builder.bind_attr("hero_image", "hero", "alt", present_as="image_slot")
    return builder.build_bundle().to_json()


def _bundle_with_list_json() -> dict:
    builder = ViewSpecBuilder("mcp_list")
    items = builder.add_list("tasks", region="main", group_id="task_order")
    items.add_item(label="Plan", description="Define intent", id="plan")
    items.add_item(label="Build", description="Compile artifact", id="build")
    return builder.build_bundle().to_json()


def _bundle_with_form_json() -> dict:
    builder = ViewSpecBuilder("mcp_form")
    form = builder.add_form("contact", region="main", group_id="fields")
    form.add_field(label="Name", value="Ada", id="name")
    form.add_field(label="Email", value="ada@example.com", id="email")
    builder.add_action(
        "submit_contact",
        "submit",
        "Submit",
        target_region="main",
        target_ref="motif:contact",
        payload_bindings=["name_value", "email_value"],
    )
    return builder.build_bundle().to_json()


def _bundle_with_detail_json() -> dict:
    builder = ViewSpecBuilder("mcp_detail")
    detail = builder.add_detail("profile", region="main", group_id="fields")
    detail.add_field(label="Owner", value="Ada Lovelace", id="owner")
    detail.add_field(label="Status", value="Ready", id="status")
    return builder.build_bundle().to_json()


def _bundle_with_empty_state_json() -> dict:
    builder = ViewSpecBuilder("mcp_empty_state")
    builder.add_empty_state(
        "no_results",
        title="No results yet",
        description="Adjust filters or create the first item.",
        region="main",
        group_id="message",
    )
    return builder.build_bundle().to_json()


def _bundle_with_hero_json() -> dict:
    builder = ViewSpecBuilder("mcp_hero")
    builder.add_hero(
        "intro",
        eyebrow="Agent-native UI",
        title="Stop writing DOM",
        description="ViewSpec compiles intent into checked UI artifacts.",
        region="main",
        group_id="message",
    )
    return builder.build_bundle().to_json()


def assert_tool_schema(payload: dict) -> None:
    assert payload["schema_version"] == MCP_RESULT_SCHEMA_VERSION
    assert isinstance(payload["ok"], bool)
    assert isinstance(payload["summary"], str)
    assert isinstance(payload["diagnostics"], list)
    assert isinstance(payload["external_refs"], list)
    assert isinstance(payload["paths"], dict)
    assert isinstance(payload["next_actions"], list)
    assert isinstance(payload["errors"], list)


def test_tool_response_rejects_reserved_data_key_shadowing():
    with pytest.raises(LocalToolError, match="reserved MCP result keys"):
        tool_response(True, "bad extension", data={"ok": False})


def test_mcp_missing_dependency_cli_hint(monkeypatch, capsys):
    import viewspec.cli as cli

    def missing_mcp(**kwargs):
        raise MissingMCPDependency(MCP_INSTALL_HINT)

    monkeypatch.setattr(cli, "run_mcp_server", missing_mcp)

    assert cli_main(["mcp"]) == 2
    assert MCP_INSTALL_HINT in capsys.readouterr().err


def test_doctor_agents_reports_missing_optional_mcp(capsys):
    exit_code = cli_main(["doctor", "--agents"])
    output = capsys.readouterr().out
    payload = json.loads(output)
    checks = payload["checks"]

    assert '"mcp_dependency"' in output
    assert "viewspec[agents]" in output
    assert checks["intent_first_commands"]["validate_intent"] is True
    assert checks["intent_first_commands"]["diff_intent"] is True
    assert checks["intent_first_commands"]["export_agent_assets"] is True
    assert checks["intent_pipeline"]["ok"] is True
    assert checks["agent_contract_assets"]["ok"] is True
    assert checks["agent_contract_assets"]["system_prompt_file"] == "agent-system-prompt.txt"
    assert checks["agent_contract_assets"]["intent_schema_file"] == "agent-intent-bundle.schema.json"
    assert checks["agent_contract_assets"]["intent_schema_id"] == "https://viewspec.dev/agent-intent-bundle.schema.json"
    assert checks["agent_contract_assets"]["export_command"] == "viewspec export-agent-assets --out .viewspec"
    assert len(checks["agent_contract_assets"]["system_prompt_sha256"]) == 64
    assert len(checks["agent_contract_assets"]["intent_schema_sha256"]) == 64
    assert checks["path_policy"] == "cwd containment by default"
    assert "validate-intent" in checks["local_network_policy"]
    assert "diff-intent" in checks["local_network_policy"]
    assert "export-agent-assets" in checks["local_network_policy"]
    if exit_code == 2:
        assert '"mcp_dependency": false' in output.lower()


def test_local_tool_wrappers_compile_check_lift_and_diff(tmp_path):
    html = tmp_path / "report.html"
    newer = tmp_path / "report-new.html"
    html.write_text("<h1>Report</h1><p>$1</p>", encoding="utf-8")
    newer.write_text("<h1>Report Updated</h1><p>$2</p>", encoding="utf-8")

    design = init_design_tool("DESIGN.md", cwd=tmp_path)
    assert_tool_schema(design)
    assert design["ok"] is True

    compiled = compile_html_file_tool("report.html", "dist", design_path="DESIGN.md", include_lift=True, cwd=tmp_path)
    assert_tool_schema(compiled)
    assert compiled["ok"] is True
    assert (tmp_path / "dist/index.html").exists()
    assert (tmp_path / "dist/lift.json").exists()

    checked = check_artifact_tool("dist", cwd=tmp_path)
    assert_tool_schema(checked)
    assert checked["ok"] is True

    lifted = lift_html_file_tool("report.html", "lift.json", cwd=tmp_path)
    assert_tool_schema(lifted)
    assert lifted["ok"] is True
    assert (tmp_path / "lift.json").exists()

    diffed = diff_html_files_tool("report.html", "report-new.html", cwd=tmp_path)
    assert_tool_schema(diffed)
    assert diffed["ok"] is True
    assert diffed["diff"]["basis"] == "lift_v1"


def test_export_agent_assets_tool_writes_prompt_and_schema(tmp_path):
    exported = export_agent_assets_tool(".viewspec", cwd=tmp_path)

    assert_tool_schema(exported)
    assert exported["ok"] is True
    assert exported["metadata"]["network_calls"] == "none"
    assert exported["metadata"]["changes"] == 2
    assert exported["paths"]["prompt"].endswith("agent-system-prompt.txt")
    assert exported["paths"]["schema"].endswith("agent-intent-bundle.schema.json")
    assert (tmp_path / ".viewspec/agent-system-prompt.txt").read_text(encoding="utf-8") == AGENT_SYSTEM_PROMPT
    assert json.loads((tmp_path / ".viewspec/agent-intent-bundle.schema.json").read_text(encoding="utf-8")) == AGENT_INTENT_BUNDLE_SCHEMA
    assert {item["path"]: item["action"] for item in exported["assets"]["files"]} == {
        "agent-system-prompt.txt": "create",
        "agent-intent-bundle.schema.json": "create",
    }

    dry_run = export_agent_assets_tool(".viewspec-dry-run", dry_run=True, cwd=tmp_path)

    assert_tool_schema(dry_run)
    assert dry_run["ok"] is True
    assert dry_run["metadata"]["dry_run"] is True
    assert not (tmp_path / ".viewspec-dry-run").exists()


def test_export_agent_assets_tool_rejects_conflicts_and_path_escapes(tmp_path):
    asset_dir = tmp_path / ".viewspec"
    asset_dir.mkdir()
    (asset_dir / "agent-system-prompt.txt").write_text("custom prompt\n", encoding="utf-8")

    conflict = export_agent_assets_tool(".viewspec", cwd=tmp_path)

    assert_tool_schema(conflict)
    assert conflict["ok"] is False
    assert conflict["errors"][0]["code"] == "IO_ERROR"
    assert "already exists with different content" in conflict["errors"][0]["message"]
    assert not (asset_dir / "agent-intent-bundle.schema.json").exists()

    outside = export_agent_assets_tool("../outside-assets", cwd=asset_dir)

    assert_tool_schema(outside)
    assert outside["ok"] is False
    assert outside["errors"][0]["code"] == "PATH_OUTSIDE_CWD"


def test_intent_mcp_wrappers_validate_compile_and_prompt(tmp_path):
    intent_path = tmp_path / "viewspec.intent.json"
    intent_path.write_text(json.dumps(_bundle_json()), encoding="utf-8")

    validated = validate_intent_bundle_file_tool("viewspec.intent.json", cwd=tmp_path)
    assert_tool_schema(validated)
    assert validated["ok"] is True
    assert validated["validation"]["compile_check"] == "passed"

    compiled = compile_intent_bundle_file_tool("viewspec.intent.json", "dist", cwd=tmp_path)
    assert_tool_schema(compiled)
    assert compiled["ok"] is True
    assert (tmp_path / "dist/index.html").exists()
    assert ACTION_EVENT_SCRIPT not in (tmp_path / "dist/index.html").read_text(encoding="utf-8")
    assert compiled["metadata"]["target"] == "html-tailwind"
    assert compiled["metadata"]["artifact_check"] == "passed"
    assert check_artifact_tool("dist", cwd=tmp_path)["ok"] is True

    bad_path = tmp_path / "bad.json"
    bad_path.write_text(json.dumps({"id": "root", "primitive": "stack", "children": []}), encoding="utf-8")
    prompt = agent_correction_prompt_file_tool("bad.json", cwd=tmp_path)
    assert_tool_schema(prompt)
    assert prompt["ok"] is False
    assert "COMPOSITION_IR_INPUT" in prompt["correction_prompt"]


def test_intent_mcp_compile_can_emit_checked_react_tsx_artifact(tmp_path):
    intent_path = tmp_path / "viewspec.intent.json"
    intent_path.write_text(json.dumps(_bundle_with_input_action_json()), encoding="utf-8")

    compiled = compile_intent_bundle_file_tool("viewspec.intent.json", "react-dist", target="react-tsx", cwd=tmp_path)
    assert_tool_schema(compiled)
    assert compiled["ok"] is True
    assert compiled["metadata"]["target"] == "react-tsx"
    assert compiled["metadata"]["emitter"] == "react_tsx"
    assert compiled["metadata"]["artifact_check"] == "passed"
    assert compiled["paths"]["tsx"].endswith("ViewSpecView.tsx")
    assert (tmp_path / "react-dist/ViewSpecView.tsx").exists()
    assert (tmp_path / "react-dist/index.html").exists() is False
    assert check_artifact_tool("react-dist", cwd=tmp_path)["ok"] is True

    tsx = (tmp_path / "react-dist/ViewSpecView.tsx").read_text(encoding="utf-8")
    manifest = json.loads((tmp_path / "react-dist/provenance_manifest.json").read_text(encoding="utf-8"))

    assert 'source: "viewspec-react-tsx"' in tsx
    assert "payloadValues: collectPayloadValues" in tsx
    assert manifest["emitter"] == "react_tsx"
    assert manifest["artifact_file"] == "ViewSpecView.tsx"
    assert manifest["artifact_hash"] == file_hash(tmp_path / "react-dist/ViewSpecView.tsx")
    assert manifest["command_args"] == [
        "viewspec",
        "compile",
        "viewspec.intent.json",
        "--target",
        "react-tsx",
        "--out",
        "<out>",
    ]


def test_check_rejects_tampered_react_tsx_artifact_source(tmp_path):
    intent_path = tmp_path / "viewspec.intent.json"
    intent_path.write_text(json.dumps(_bundle_with_input_action_json()), encoding="utf-8")
    compiled = compile_intent_bundle_file_tool("viewspec.intent.json", "react-dist", target="react-tsx", cwd=tmp_path)
    assert compiled["ok"] is True
    tsx_path = tmp_path / "react-dist/ViewSpecView.tsx"
    manifest_path = tmp_path / "react-dist/provenance_manifest.json"

    tsx = tsx_path.read_text(encoding="utf-8")
    tsx_path.write_text(tsx.replace('source: "viewspec-react-tsx"', 'source: "tampered"'), encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_hash"] = file_hash(tsx_path)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    checked = check_artifact_tool("react-dist", cwd=tmp_path)

    assert_tool_schema(checked)
    assert checked["ok"] is False
    assert any(error["message"] == "ViewSpecView.tsx missing React action source marker" for error in checked["errors"])


def test_intent_mcp_compile_rejects_unknown_target(tmp_path):
    intent_path = tmp_path / "viewspec.intent.json"
    intent_path.write_text(json.dumps(_bundle_json()), encoding="utf-8")

    result = compile_intent_bundle_file_tool("viewspec.intent.json", "dist", target="swiftui", cwd=tmp_path)

    assert_tool_schema(result)
    assert result["ok"] is False
    assert result["errors"][0]["code"] == "COMPILE_FAILED"
    assert result["metadata"]["target"] == "swiftui"
    assert not (tmp_path / "dist").exists()


def test_intent_mcp_diff_reports_semantic_changes(tmp_path):
    left = tmp_path / "old.intent.json"
    right = tmp_path / "new.intent.json"
    left_payload = _bundle_json()
    right_payload = _bundle_json()
    right_payload["substrate"]["nodes"]["alpha"]["attrs"]["value"] = "2"
    left.write_text(json.dumps(left_payload), encoding="utf-8")
    right.write_text(json.dumps(right_payload), encoding="utf-8")

    result = diff_intent_bundle_files_tool("old.intent.json", "new.intent.json", cwd=tmp_path)

    assert_tool_schema(result)
    assert result["ok"] is True
    assert result["diff"]["basis"] == "intent_bundle_v1"
    assert result["diff"]["changes"]["substrate_nodes"]["changed"] == ["alpha"]
    assert result["diff"]["semantic_changes"] == {"motifs": [], "actions": [], "bindings": []}
    assert result["metadata"]["network_calls"] == "none"


def test_intent_mcp_init_intent_writes_valid_scaffold(tmp_path):
    result = init_intent_tool("viewspec.intent.json", kind="outline", cwd=tmp_path)

    assert_tool_schema(result)
    assert result["ok"] is True
    assert result["validation"]["ok"] is True
    assert result["metadata"]["kind"] == "outline"
    assert "viewspec init-design --out DESIGN.md" in " ".join(result["next_actions"])
    assert (tmp_path / "viewspec.intent.json").exists()


def test_intent_mcp_invalid_intent_returns_stable_error_and_correction_prompt(tmp_path):
    bad_path = tmp_path / "bad.json"
    bad_path.write_text(json.dumps([]), encoding="utf-8")

    result = compile_intent_bundle_file_tool("bad.json", "dist", cwd=tmp_path)

    assert_tool_schema(result)
    assert result["ok"] is False
    assert result["errors"][0]["code"] == "INVALID_PAYLOAD"
    assert "correction_prompt" in result
    assert "Output strict JSON only" in result["correction_prompt"]


def test_intent_mcp_refuses_input_overwrite_before_design_load(tmp_path):
    intent_path = tmp_path / "provenance_manifest.json"
    intent_path.write_text(json.dumps(_bundle_json()), encoding="utf-8")

    result = compile_intent_bundle_file_tool("provenance_manifest.json", ".", design_path="missing-DESIGN.md", cwd=tmp_path)

    assert_tool_schema(result)
    assert result["ok"] is False
    assert result["errors"][0]["code"] == "COMPILE_FAILED"
    assert "Refusing to overwrite input file" in result["errors"][0]["message"]
    assert "missing-DESIGN.md" not in result["errors"][0]["message"]


def test_check_rejects_unknown_script_in_intent_artifact(tmp_path):
    intent_path = tmp_path / "viewspec.intent.json"
    intent_path.write_text(json.dumps(_bundle_json()), encoding="utf-8")
    assert compile_intent_bundle_file_tool("viewspec.intent.json", "dist", cwd=tmp_path)["ok"] is True

    html_path = tmp_path / "dist/index.html"
    tampered_html = f"{html_path.read_text(encoding='utf-8')}<script>fetch('/leak')</script>"
    html_path.write_text(tampered_html, encoding="utf-8")
    manifest_path = tmp_path / "dist/provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_hash"] = file_hash(html_path)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    checked = check_artifact_tool("dist", cwd=tmp_path)

    assert_tool_schema(checked)
    assert checked["ok"] is False
    assert checked["errors"][0]["message"] == "index.html contains an unknown inline script"


def test_check_rejects_action_runtime_script_without_action_nodes(tmp_path):
    intent_path = tmp_path / "viewspec.intent.json"
    intent_path.write_text(json.dumps(_bundle_json()), encoding="utf-8")
    assert compile_intent_bundle_file_tool("viewspec.intent.json", "dist", cwd=tmp_path)["ok"] is True

    html_path = tmp_path / "dist/index.html"
    tampered_html = html_path.read_text(encoding="utf-8").replace("</body>", f"{ACTION_EVENT_SCRIPT}\n</body>")
    html_path.write_text(tampered_html, encoding="utf-8")
    manifest_path = tmp_path / "dist/provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_hash"] = file_hash(html_path)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    checked = check_artifact_tool("dist", cwd=tmp_path)

    assert_tool_schema(checked)
    assert checked["ok"] is False
    assert checked["errors"][0]["message"] == "index.html contains an action runtime script without action nodes"


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        ('<img srcset="https://example.com/a.png 1x, https://example.com/b.png 2x" alt="">', "index.html contains an auto-fetching remote URL attribute"),
        ('<source srcset="//example.com/a.webp 1x">', "index.html contains an auto-fetching remote URL attribute"),
        ('<meta http-equiv="refresh" content="0;url=https://example.com">', "index.html contains an active or auto-fetching surface"),
        ('<svg><image href="https://example.com/chart.svg"></image></svg>', "index.html contains an auto-fetching remote URL attribute"),
    ],
)
def test_check_rejects_tampered_autofetch_surfaces(tmp_path, snippet, expected):
    intent_path = tmp_path / "viewspec.intent.json"
    intent_path.write_text(json.dumps(_bundle_json()), encoding="utf-8")
    assert compile_intent_bundle_file_tool("viewspec.intent.json", "dist", cwd=tmp_path)["ok"] is True

    html_path = tmp_path / "dist/index.html"
    tampered_html = html_path.read_text(encoding="utf-8").replace("</body>", f"{snippet}</body>")
    html_path.write_text(tampered_html, encoding="utf-8")
    manifest_path = tmp_path / "dist/provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_hash"] = file_hash(html_path)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    checked = check_artifact_tool("dist", cwd=tmp_path)

    assert_tool_schema(checked)
    assert checked["ok"] is False
    assert any(error["message"] == expected for error in checked["errors"])


def test_check_rejects_unknown_manifest_kind(tmp_path):
    intent_path = tmp_path / "viewspec.intent.json"
    intent_path.write_text(json.dumps(_bundle_json()), encoding="utf-8")
    assert compile_intent_bundle_file_tool("viewspec.intent.json", "dist", cwd=tmp_path)["ok"] is True

    manifest_path = tmp_path / "dist/provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["kind"] = "legacy_compile"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    checked = check_artifact_tool("dist", cwd=tmp_path)

    assert_tool_schema(checked)
    assert checked["ok"] is False
    assert any(error["message"] == "manifest kind must be one of: intent_bundle_compile, raw_html_compile" for error in checked["errors"])


def test_check_rejects_intent_manifest_envelope_mismatch(tmp_path):
    intent_path = tmp_path / "viewspec.intent.json"
    intent_path.write_text(json.dumps(_bundle_json()), encoding="utf-8")
    assert compile_intent_bundle_file_tool("viewspec.intent.json", "dist", cwd=tmp_path)["ok"] is True

    manifest_path = tmp_path / "dist/provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["command"] = "compile_html"
    manifest["policy_version"] = "viewspec-raw-html-allowlist@1"
    manifest["guarantees"]["decompilation"] = "not_claimed"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    checked = check_artifact_tool("dist", cwd=tmp_path)
    messages = {error["message"] for error in checked["errors"]}

    assert_tool_schema(checked)
    assert checked["ok"] is False
    assert "manifest command must be compile for intent_bundle_compile" in messages
    assert "manifest policy_version must be viewspec-intent-bundle@1 for intent_bundle_compile" in messages
    assert "manifest guarantees.decompilation must be not_applicable for intent_bundle_compile" in messages


def test_check_rejects_invalid_manifest_diagnostic_shape(tmp_path):
    html = tmp_path / "report.html"
    html.write_text('<h1 onclick="bad()">Report</h1>', encoding="utf-8")
    assert compile_html_file_tool("report.html", "dist", cwd=tmp_path)["ok"] is True

    manifest_path = tmp_path / "dist/provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["diagnostics"][0]["severity"] = "critical"
    manifest["diagnostics"][0]["code"] = ""
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    checked = check_artifact_tool("dist", cwd=tmp_path)
    messages = {error["message"] for error in checked["errors"]}

    assert_tool_schema(checked)
    assert checked["ok"] is False
    assert "manifest diagnostics[0].severity must be one of: error, info, warning" in messages
    assert "manifest diagnostics[0].code must be a non-empty string" in messages


def test_check_rejects_invalid_external_ref_policy(tmp_path):
    html = tmp_path / "report.html"
    html.write_text('<img src="https://example.com/chart.png" alt="Chart">', encoding="utf-8")
    assert compile_html_file_tool("report.html", "dist", cwd=tmp_path)["ok"] is True

    manifest_path = tmp_path / "dist/provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["external_refs"][0]["behavior"] = "auto_fetch"
    manifest["external_refs"][0]["url"] = "javascript:alert(1)"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    checked = check_artifact_tool("dist", cwd=tmp_path)
    messages = {error["message"] for error in checked["errors"]}

    assert_tool_schema(checked)
    assert checked["ok"] is False
    assert "manifest external_refs[0] must use an allowed inert external-ref policy" in messages
    assert "manifest external_refs[0].url must be an http(s) URL" in messages


def test_check_rejects_invalid_design_metadata_shape(tmp_path):
    html = tmp_path / "report.html"
    html.write_text("<h1>Report</h1>", encoding="utf-8")
    assert init_design_tool("DESIGN.md", cwd=tmp_path)["ok"] is True
    assert compile_html_file_tool("report.html", "dist", design_path="DESIGN.md", cwd=tmp_path)["ok"] is True

    manifest_path = tmp_path / "dist/provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["design"]["design_hash"] = "bad"
    manifest["design"]["lint_summary"]["warnings"] = -1
    manifest["design"]["findings"] = [{"severity": "critical", "code": "", "path": "$", "message": ""}]
    manifest["design"]["applied_tokens"] = {"tone.neutral": "colors.primary"}
    manifest["design"]["ignored_tokens"] = [1]
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    checked = check_artifact_tool("dist", cwd=tmp_path)
    messages = {error["message"] for error in checked["errors"]}

    assert_tool_schema(checked)
    assert checked["ok"] is False
    assert "manifest design.design_hash must be a sha256 hex string" in messages
    assert "manifest design.lint_summary.warnings must be a non-negative integer" in messages
    assert "manifest design.findings[0].severity must be one of: error, info, warning" in messages
    assert "manifest design.findings[0].code must be a non-empty string" in messages
    assert "manifest design.findings[0].message must be a non-empty string" in messages
    assert "manifest design.applied_tokens must be an object of string arrays" in messages
    assert "manifest design.ignored_tokens must be a list of strings" in messages


def test_check_rejects_invalid_intent_manifest_node_shape(tmp_path):
    intent_path = tmp_path / "viewspec.intent.json"
    intent_path.write_text(json.dumps(_bundle_json()), encoding="utf-8")
    assert compile_intent_bundle_file_tool("viewspec.intent.json", "dist", cwd=tmp_path)["ok"] is True

    manifest_path = tmp_path / "dist/provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    dom_id = sorted(manifest["nodes"])[0]
    manifest["nodes"][dom_id]["ir_id"] = ""
    manifest["nodes"][dom_id]["style_tokens"] = "tone.neutral"
    manifest["nodes"][dom_id]["props"] = []
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    checked = check_artifact_tool("dist", cwd=tmp_path)
    messages = {error["message"] for error in checked["errors"]}

    assert_tool_schema(checked)
    assert checked["ok"] is False
    assert f"manifest nodes.{dom_id}.ir_id must be a non-empty string" in messages
    assert f"manifest nodes.{dom_id}.style_tokens must be a list of strings" in messages
    assert f"manifest nodes.{dom_id}.props must be an object" in messages


def test_check_rejects_invalid_raw_html_manifest_node_shape(tmp_path):
    html = tmp_path / "report.html"
    html.write_text("<h1>Report</h1>", encoding="utf-8")
    assert compile_html_file_tool("report.html", "dist", cwd=tmp_path)["ok"] is True

    manifest_path = tmp_path / "dist/provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    node_id = sorted(manifest["nodes"])[0]
    manifest["nodes"][node_id]["tag"] = ""
    manifest["nodes"][node_id]["attrs"] = {"class": ["bad"]}
    manifest["nodes"][node_id]["text"] = None
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    checked = check_artifact_tool("dist", cwd=tmp_path)
    messages = {error["message"] for error in checked["errors"]}

    assert_tool_schema(checked)
    assert checked["ok"] is False
    assert f"manifest nodes.{node_id}.tag must be a non-empty string" in messages
    assert f"manifest nodes.{node_id}.attrs must be an object of strings" in messages
    assert f"manifest nodes.{node_id}.text must be a string" in messages


def test_check_rejects_missing_intent_manifest_node(tmp_path):
    intent_path = tmp_path / "viewspec.intent.json"
    intent_path.write_text(json.dumps(_bundle_json()), encoding="utf-8")
    assert compile_intent_bundle_file_tool("viewspec.intent.json", "dist", cwd=tmp_path)["ok"] is True

    manifest_path = tmp_path / "dist/provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    removed_dom_id = sorted(manifest["nodes"])[0]
    del manifest["nodes"][removed_dom_id]
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    checked = check_artifact_tool("dist", cwd=tmp_path)

    assert_tool_schema(checked)
    assert checked["ok"] is False
    assert any(error["message"] == f"DOM element {removed_dom_id} with data-ir-id is missing from manifest nodes" for error in checked["errors"])


def test_check_rejects_intent_dom_ir_mismatch(tmp_path):
    intent_path = tmp_path / "viewspec.intent.json"
    intent_path.write_text(json.dumps(_bundle_json()), encoding="utf-8")
    assert compile_intent_bundle_file_tool("viewspec.intent.json", "dist", cwd=tmp_path)["ok"] is True

    html_path = tmp_path / "dist/index.html"
    manifest_path = tmp_path / "dist/provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    dom_id = sorted(manifest["nodes"])[0]
    ir_id = manifest["nodes"][dom_id]["ir_id"]
    html = html_path.read_text(encoding="utf-8")
    tampered_html = html.replace(f'data-ir-id="{ir_id}"', 'data-ir-id="tampered_ir"', 1)
    html_path.write_text(tampered_html, encoding="utf-8")
    manifest["artifact_hash"] = file_hash(html_path)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    checked = check_artifact_tool("dist", cwd=tmp_path)

    assert_tool_schema(checked)
    assert checked["ok"] is False
    assert any(error["message"] == f"manifest node {dom_id} ir_id does not match DOM data-ir-id" for error in checked["errors"])


def test_check_rejects_duplicate_binding_identity_in_dom(tmp_path):
    intent_path = tmp_path / "viewspec.intent.json"
    intent_path.write_text(json.dumps(_bundle_json()), encoding="utf-8")
    assert compile_intent_bundle_file_tool("viewspec.intent.json", "dist", cwd=tmp_path)["ok"] is True

    html_path = tmp_path / "dist/index.html"
    html = html_path.read_text(encoding="utf-8")
    tampered_html = html.replace('data-binding-id="alpha_value"', 'data-binding-id="alpha_label"', 1)
    html_path.write_text(tampered_html, encoding="utf-8")
    manifest_path = tmp_path / "dist/provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_hash"] = file_hash(html_path)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    checked = check_artifact_tool("dist", cwd=tmp_path)

    assert_tool_schema(checked)
    assert checked["ok"] is False
    assert any(error["message"] == "index.html contains duplicate data-binding-id alpha_label" for error in checked["errors"])


def test_check_rejects_binding_manifest_without_source_provenance(tmp_path):
    intent_path = tmp_path / "viewspec.intent.json"
    intent_path.write_text(json.dumps(_bundle_json()), encoding="utf-8")
    assert compile_intent_bundle_file_tool("viewspec.intent.json", "dist", cwd=tmp_path)["ok"] is True

    manifest_path = tmp_path / "dist/provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["nodes"]["dom-binding_alpha_label"]["content_refs"] = []
    manifest["nodes"]["dom-binding_alpha_label"]["intent_refs"] = []
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    checked = check_artifact_tool("dist", cwd=tmp_path)
    messages = {error["message"] for error in checked["errors"]}

    assert_tool_schema(checked)
    assert checked["ok"] is False
    assert "manifest nodes.dom-binding_alpha_label.intent_refs must not be empty" in messages
    assert "manifest nodes.dom-binding_alpha_label.intent_refs must include viewspec:binding:alpha_label" in messages
    assert "manifest nodes.dom-binding_alpha_label.content_refs must not be empty for binding_id alpha_label" in messages


def test_check_rejects_intent_style_token_mismatch(tmp_path):
    intent_path = tmp_path / "viewspec.intent.json"
    intent_path.write_text(json.dumps(_bundle_json()), encoding="utf-8")
    assert compile_intent_bundle_file_tool("viewspec.intent.json", "dist", cwd=tmp_path)["ok"] is True

    html_path = tmp_path / "dist/index.html"
    manifest_path = tmp_path / "dist/provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    dom_id = next(dom_id for dom_id, entry in manifest["nodes"].items() if entry["style_tokens"])
    html = html_path.read_text(encoding="utf-8")
    node_start = html.index(f'id="{dom_id}"')
    attr_start = html.index('data-style-tokens="', node_start)
    attr_value_start = attr_start + len('data-style-tokens="')
    attr_end = html.index('"', attr_value_start)
    tampered_html = f'{html[:attr_value_start]}[&quot;tampered&quot;]{html[attr_end:]}'
    html_path.write_text(tampered_html, encoding="utf-8")
    manifest["artifact_hash"] = file_hash(html_path)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    checked = check_artifact_tool("dist", cwd=tmp_path)

    assert_tool_schema(checked)
    assert checked["ok"] is False
    assert any(
        error["message"] == f"manifest node {dom_id} style_tokens does not match DOM data-style-tokens"
        for error in checked["errors"]
    )


def test_check_rejects_intent_visible_text_mismatch(tmp_path):
    intent_path = tmp_path / "viewspec.intent.json"
    intent_path.write_text(json.dumps(_bundle_json()), encoding="utf-8")
    assert compile_intent_bundle_file_tool("viewspec.intent.json", "dist", cwd=tmp_path)["ok"] is True

    html_path = tmp_path / "dist/index.html"
    manifest_path = tmp_path / "dist/provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    dom_id, entry = next(
        (dom_id, entry)
        for dom_id, entry in manifest["nodes"].items()
        if entry["primitive"] in {"label", "text", "value"} and entry["props"].get("text") == "Alpha"
    )
    html = html_path.read_text(encoding="utf-8")
    tampered_html = html.replace(">Alpha</", ">Tampered</", 1)
    html_path.write_text(tampered_html, encoding="utf-8")
    manifest["artifact_hash"] = file_hash(html_path)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    checked = check_artifact_tool("dist", cwd=tmp_path)

    assert_tool_schema(checked)
    assert checked["ok"] is False
    assert any(error["message"] == f"DOM element {dom_id} text does not match manifest props" for error in checked["errors"])


def test_check_rejects_tampered_action_button_semantics(tmp_path):
    intent_path = tmp_path / "viewspec.intent.json"
    intent_path.write_text(json.dumps(_bundle_with_action_json()), encoding="utf-8")
    assert compile_intent_bundle_file_tool("viewspec.intent.json", "dist", cwd=tmp_path)["ok"] is True

    html_path = tmp_path / "dist/index.html"
    tampered_html = html_path.read_text(encoding="utf-8").replace('type="button"', 'type="submit"', 1)
    tampered_html = tampered_html.replace('data-action-target-ref="binding:alpha_label"', 'data-action-target-ref="binding:alpha_value"', 1)
    tampered_html = tampered_html.replace(
        'data-payload-bindings="[&quot;alpha_label&quot;, &quot;alpha_value&quot;]"',
        'data-payload-bindings="not-json"',
        1,
    )
    html_path.write_text(tampered_html, encoding="utf-8")
    manifest_path = tmp_path / "dist/provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_hash"] = file_hash(html_path)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    checked = check_artifact_tool("dist", cwd=tmp_path)

    assert_tool_schema(checked)
    assert checked["ok"] is False
    messages = {error["message"] for error in checked["errors"]}
    assert 'DOM element dom-action_open_alpha button missing type="button"' in messages
    assert "DOM element dom-action_open_alpha data-action-target-ref does not match manifest props" in messages
    assert "DOM element dom-action_open_alpha has invalid data-payload-bindings JSON" in messages


def test_check_rejects_tampered_input_semantics(tmp_path):
    intent_path = tmp_path / "viewspec.intent.json"
    intent_path.write_text(json.dumps(_bundle_with_input_action_json()), encoding="utf-8")
    assert compile_intent_bundle_file_tool("viewspec.intent.json", "dist", cwd=tmp_path)["ok"] is True

    html_path = tmp_path / "dist/index.html"
    html = html_path.read_text(encoding="utf-8")
    tampered_html = html.replace('type="text"', 'type="password"', 1)
    tampered_html = tampered_html.replace('value="Hello"', 'value="Tampered"', 1)
    tampered_html = tampered_html.replace('aria-label="Message"', 'aria-label="Wrong"', 1)
    tampered_html = tampered_html.replace('data-binding-id="draft_value"', 'data-binding-id="wrong"', 1)
    html_path.write_text(tampered_html, encoding="utf-8")
    manifest_path = tmp_path / "dist/provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_hash"] = file_hash(html_path)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    checked = check_artifact_tool("dist", cwd=tmp_path)
    messages = {error["message"] for error in checked["errors"]}

    assert_tool_schema(checked)
    assert checked["ok"] is False
    assert 'DOM element dom-binding_draft_value input missing type="text"' in messages
    assert "DOM element dom-binding_draft_value input value does not match manifest props" in messages
    assert "DOM element dom-binding_draft_value input aria-label does not match manifest props" in messages
    assert "DOM element dom-binding_draft_value data-binding-id does not match manifest props" in messages


def test_check_rejects_tampered_image_slot_semantics(tmp_path):
    intent_path = tmp_path / "viewspec.intent.json"
    intent_path.write_text(json.dumps(_bundle_with_image_slot_json()), encoding="utf-8")
    assert compile_intent_bundle_file_tool("viewspec.intent.json", "dist", cwd=tmp_path)["ok"] is True

    html_path = tmp_path / "dist/index.html"
    tampered_html = html_path.read_text(encoding="utf-8").replace('aria-label="Hero Preview"', 'aria-label="Wrong"', 1)
    html_path.write_text(tampered_html, encoding="utf-8")
    manifest_path = tmp_path / "dist/provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_hash"] = file_hash(html_path)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    checked = check_artifact_tool("dist", cwd=tmp_path)

    assert_tool_schema(checked)
    assert checked["ok"] is False
    assert any(error["message"] == "DOM element dom-binding_hero_image image_slot aria-label does not match manifest props" for error in checked["errors"])


def test_check_rejects_tampered_list_semantic_tags(tmp_path):
    intent_path = tmp_path / "viewspec.intent.json"
    intent_path.write_text(json.dumps(_bundle_with_list_json()), encoding="utf-8")
    assert compile_intent_bundle_file_tool("viewspec.intent.json", "dist", cwd=tmp_path)["ok"] is True

    html_path = tmp_path / "dist/index.html"
    tampered_html = html_path.read_text(encoding="utf-8").replace("<ul ", "<div ", 1).replace("</ul>", "</div>", 1)
    html_path.write_text(tampered_html, encoding="utf-8")
    manifest_path = tmp_path / "dist/provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_hash"] = file_hash(html_path)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    checked = check_artifact_tool("dist", cwd=tmp_path)

    assert_tool_schema(checked)
    assert checked["ok"] is False
    assert any(error["message"] == "manifest node dom-motif_tasks list stack must render as <ul>" for error in checked["errors"])


def test_check_rejects_tampered_form_semantic_tags(tmp_path):
    intent_path = tmp_path / "viewspec.intent.json"
    intent_path.write_text(json.dumps(_bundle_with_form_json()), encoding="utf-8")
    assert compile_intent_bundle_file_tool("viewspec.intent.json", "dist", cwd=tmp_path)["ok"] is True

    html_path = tmp_path / "dist/index.html"
    tampered_html = html_path.read_text(encoding="utf-8")
    tampered_html = tampered_html.replace("<section ", "<form ", 1).replace("</section>", "</form>", 1)
    tampered_html = tampered_html.replace('role="form"', 'role="region"', 1).replace('role="group"', 'role="region"', 1)
    html_path.write_text(tampered_html, encoding="utf-8")
    manifest_path = tmp_path / "dist/provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_hash"] = file_hash(html_path)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    checked = check_artifact_tool("dist", cwd=tmp_path)
    messages = {error["message"] for error in checked["errors"]}

    assert_tool_schema(checked)
    assert checked["ok"] is False
    assert "index.html contains an active form surface" in messages
    assert "manifest node dom-motif_contact form stack must render as <section>" in messages
    assert 'DOM element dom-motif_contact form stack missing role="form"' in messages
    assert 'DOM element dom-motif_contact_name form field missing role="group"' in messages


def test_check_rejects_tampered_detail_semantic_tags(tmp_path):
    intent_path = tmp_path / "viewspec.intent.json"
    intent_path.write_text(json.dumps(_bundle_with_detail_json()), encoding="utf-8")
    assert compile_intent_bundle_file_tool("viewspec.intent.json", "dist", cwd=tmp_path)["ok"] is True

    html_path = tmp_path / "dist/index.html"
    tampered_html = html_path.read_text(encoding="utf-8")
    tampered_html = tampered_html.replace("<dl ", "<section ", 1).replace("</dl>", "</section>", 1)
    tampered_html = tampered_html.replace("<dt ", "<span ", 1).replace("</dt>", "</span>", 1)
    tampered_html = tampered_html.replace("<dd ", "<span ", 1).replace("</dd>", "</span>", 1)
    html_path.write_text(tampered_html, encoding="utf-8")
    manifest_path = tmp_path / "dist/provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_hash"] = file_hash(html_path)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    checked = check_artifact_tool("dist", cwd=tmp_path)
    messages = {error["message"] for error in checked["errors"]}

    assert_tool_schema(checked)
    assert checked["ok"] is False
    assert "manifest node dom-motif_profile detail stack must render as <dl>" in messages
    assert "manifest node dom-binding_owner_label detail term must render as <dt>" in messages
    assert "manifest node dom-binding_owner_value detail description must render as <dd>" in messages


def test_check_rejects_tampered_empty_state_semantic_tags(tmp_path):
    intent_path = tmp_path / "viewspec.intent.json"
    intent_path.write_text(json.dumps(_bundle_with_empty_state_json()), encoding="utf-8")
    assert compile_intent_bundle_file_tool("viewspec.intent.json", "dist", cwd=tmp_path)["ok"] is True

    html_path = tmp_path / "dist/index.html"
    tampered_html = html_path.read_text(encoding="utf-8")
    tampered_html = tampered_html.replace("<section ", "<div ", 1).replace("</section>", "</div>", 1)
    tampered_html = tampered_html.replace('aria-label="Empty state"', 'aria-label="Changed"', 1)
    tampered_html = tampered_html.replace("<h2 ", "<span ", 1).replace("</h2>", "</span>", 1)
    tampered_html = tampered_html.replace("<p ", "<span ", 1).replace("</p>", "</span>", 1)
    html_path.write_text(tampered_html, encoding="utf-8")
    manifest_path = tmp_path / "dist/provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_hash"] = file_hash(html_path)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    checked = check_artifact_tool("dist", cwd=tmp_path)
    messages = {error["message"] for error in checked["errors"]}

    assert_tool_schema(checked)
    assert checked["ok"] is False
    assert "manifest node dom-motif_no_results empty_state surface must render as <section>" in messages
    assert "DOM element dom-motif_no_results empty_state surface aria-label does not match manifest props" in messages
    assert "manifest node dom-binding_no_results_title empty_state title must render as <h2>" in messages
    assert "manifest node dom-binding_no_results_description empty_state description must render as <p>" in messages


def test_check_rejects_tampered_hero_semantic_tags(tmp_path):
    intent_path = tmp_path / "viewspec.intent.json"
    intent_path.write_text(json.dumps(_bundle_with_hero_json()), encoding="utf-8")
    assert compile_intent_bundle_file_tool("viewspec.intent.json", "dist", cwd=tmp_path)["ok"] is True

    html_path = tmp_path / "dist/index.html"
    tampered_html = html_path.read_text(encoding="utf-8")
    tampered_html = tampered_html.replace("<header ", "<section ", 1).replace("</header>", "</section>", 1)
    tampered_html = tampered_html.replace('aria-label="Hero"', 'aria-label="Changed"', 1)
    tampered_html = tampered_html.replace("<h1 ", "<span ", 1).replace("</h1>", "</span>", 1)
    html_path.write_text(tampered_html, encoding="utf-8")
    manifest_path = tmp_path / "dist/provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_hash"] = file_hash(html_path)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    checked = check_artifact_tool("dist", cwd=tmp_path)
    messages = {error["message"] for error in checked["errors"]}

    assert_tool_schema(checked)
    assert checked["ok"] is False
    assert "manifest node dom-motif_intro hero surface must render as <header>" in messages
    assert "DOM element dom-motif_intro hero surface aria-label does not match manifest props" in messages
    assert "manifest node dom-binding_intro_title hero title must render as <h1>" in messages


def test_check_rejects_tampered_table_semantic_tags(tmp_path):
    intent_path = tmp_path / "viewspec.intent.json"
    intent_path.write_text(json.dumps(_bundle_json()), encoding="utf-8")
    assert compile_intent_bundle_file_tool("viewspec.intent.json", "dist", cwd=tmp_path)["ok"] is True

    html_path = tmp_path / "dist/index.html"
    manifest_path = tmp_path / "dist/provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    table_dom_id = next(
        dom_id
        for dom_id, entry in manifest["nodes"].items()
        if entry["primitive"] == "stack" and entry["props"].get("motif_kind") == "table"
    )
    row_dom_id = next(
        dom_id
        for dom_id, entry in manifest["nodes"].items()
        if entry["primitive"] == "cluster" and entry["props"].get("motif_kind") == "table"
    )
    header_dom_id = next(
        dom_id
        for dom_id, entry in manifest["nodes"].items()
        if entry["props"].get("table_cell_role") == "row_header"
    )
    cell_dom_id = next(
        dom_id
        for dom_id, entry in manifest["nodes"].items()
        if entry["props"].get("table_cell_role") == "cell"
    )
    html = html_path.read_text(encoding="utf-8")
    tampered_html = html.replace("<table ", "<div ", 1).replace("</table>", "</div>", 1)
    tampered_html = tampered_html.replace("<tr ", "<section ", 1).replace("</tr>", "</section>", 1)
    tampered_html = tampered_html.replace('scope="row"', 'scope="col"', 1)
    tampered_html = tampered_html.replace(f'<td id="{cell_dom_id}"', f'<span id="{cell_dom_id}"', 1).replace("</td>", "</span>", 1)
    html_path.write_text(tampered_html, encoding="utf-8")
    manifest["artifact_hash"] = file_hash(html_path)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    checked = check_artifact_tool("dist", cwd=tmp_path)
    messages = {error["message"] for error in checked["errors"]}

    assert_tool_schema(checked)
    assert checked["ok"] is False
    assert f"manifest node {table_dom_id} table stack must render as <table>" in messages
    assert f"manifest node {row_dom_id} table row must render as <tr>" in messages
    assert f'DOM element {header_dom_id} table row_header missing scope="row"' in messages
    assert f"manifest node {cell_dom_id} table cell must render as <td>" in messages


def test_check_rejects_missing_raw_html_manifest_node(tmp_path):
    html = tmp_path / "report.html"
    html.write_text("<h1>Report</h1><p>Value</p>", encoding="utf-8")
    assert compile_html_file_tool("report.html", "dist", cwd=tmp_path)["ok"] is True

    manifest_path = tmp_path / "dist/provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    removed_node_id = sorted(manifest["nodes"])[0]
    del manifest["nodes"][removed_node_id]
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    checked = check_artifact_tool("dist", cwd=tmp_path)

    assert_tool_schema(checked)
    assert checked["ok"] is False
    assert any(
        error["message"] == f"DOM element {removed_node_id} with data-viewspec-node-id is missing from manifest nodes"
        for error in checked["errors"]
    )


@pytest.mark.parametrize(
    ("tamper_kind", "expected_suffix"),
    [
        ("tag", "tag does not match DOM tag"),
        ("attr", "attr title does not match DOM"),
        ("text", "text does not match DOM text"),
    ],
)
def test_check_rejects_raw_html_manifest_dom_content_drift(tmp_path, tamper_kind, expected_suffix):
    html = tmp_path / "report.html"
    html.write_text('<h1 title="Original">Report</h1>', encoding="utf-8")
    assert compile_html_file_tool("report.html", "dist", cwd=tmp_path)["ok"] is True

    manifest_path = tmp_path / "dist/provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    node_id = sorted(manifest["nodes"])[0]
    html_path = tmp_path / "dist/index.html"
    tampered_html = html_path.read_text(encoding="utf-8")
    if tamper_kind == "tag":
        tampered_html = tampered_html.replace("<h1 ", "<h2 ", 1).replace("</h1>", "</h2>", 1)
    elif tamper_kind == "attr":
        tampered_html = tampered_html.replace('title="Original"', 'title="Changed"', 1)
    else:
        tampered_html = tampered_html.replace(">Report</h1>", ">Tampered</h1>", 1)
    html_path.write_text(tampered_html, encoding="utf-8")
    manifest["artifact_hash"] = file_hash(html_path)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    checked = check_artifact_tool("dist", cwd=tmp_path)

    assert_tool_schema(checked)
    assert checked["ok"] is False
    assert any(error["message"] == f"manifest node {node_id} {expected_suffix}" for error in checked["errors"])


def test_mcp_raw_html_tool_descriptions_are_import_only():
    root = Path(__file__).resolve().parents[1]
    text = root.joinpath("src/viewspec/mcp_server.py").read_text(encoding="utf-8")

    assert text.count("Use only when importing existing HTML; do not use for new UI.") >= 3
    assert "Compile a ViewSpec IntentBundle JSON file into a local compiler artifact" in text
    assert "target='html-tailwind' for checked standalone HTML" in text
    assert "target='react-tsx' for checked React source" in text
    assert "Export the local ViewSpec agent system prompt and IntentBundle JSON schema without network calls." in text


def test_mcp_path_sandbox_rejects_urls_and_outside_paths(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.html"
    outside.write_text("<h1>Outside</h1>", encoding="utf-8")

    outside_result = compile_html_file_tool(outside, "dist", cwd=root)
    assert_tool_schema(outside_result)
    assert outside_result["ok"] is False
    assert outside_result["errors"][0]["code"] == "PATH_OUTSIDE_CWD"
    assert outside_result["metadata"]["cwd"] == str(root.resolve())
    assert outside_result["metadata"]["allow_outside_cwd"] is False
    assert outside_result["metadata"]["network_calls"] == "none"

    url_result = lift_html_file_tool("file:///tmp/report.html", cwd=root)
    assert_tool_schema(url_result)
    assert url_result["ok"] is False
    assert url_result["errors"][0]["code"] == "INVALID_PATH"
    assert url_result["metadata"]["cwd"] == str(root.resolve())
    assert url_result["metadata"]["allow_outside_cwd"] is False

    absolute = root.anchor + "viewspec-outside-test.html"
    absolute_result = lift_html_file_tool(absolute, cwd=root)
    assert_tool_schema(absolute_result)
    assert absolute_result["ok"] is False
    assert absolute_result["errors"][0]["code"] in {"PATH_OUTSIDE_CWD", "INVALID_PATH"}

    intent_url_result = validate_intent_bundle_file_tool("https://example.com/viewspec.intent.json", cwd=root)
    assert_tool_schema(intent_url_result)
    assert intent_url_result["ok"] is False
    assert intent_url_result["errors"][0]["code"] == "INVALID_PATH"
    assert intent_url_result["metadata"]["cwd"] == str(root.resolve())
    assert intent_url_result["metadata"]["allow_outside_cwd"] is False


def test_mcp_path_sandbox_rejects_windows_drive_relative_tricks(tmp_path):
    root = tmp_path / "root"
    root.mkdir()

    drive_relative = lift_html_file_tool("C:viewspec-trick.html", cwd=root, allow_outside_cwd=True)
    assert_tool_schema(drive_relative)
    assert drive_relative["ok"] is False
    assert drive_relative["errors"][0]["code"] == "INVALID_PATH"
    assert "drive-relative" in drive_relative["errors"][0]["message"] or drive_relative["errors"][0]["message"].startswith("Path does not exist")
    assert drive_relative["metadata"]["allow_outside_cwd"] is True

    rooted_current_drive = lift_html_file_tool("\\viewspec-rooted-trick.html", cwd=root, allow_outside_cwd=True)
    assert_tool_schema(rooted_current_drive)
    assert rooted_current_drive["ok"] is False
    assert rooted_current_drive["errors"][0]["code"] == "INVALID_PATH"
    assert "rooted paths without a drive" in rooted_current_drive["errors"][0]["message"] or rooted_current_drive["errors"][0]["message"].startswith("Path does not exist")
    assert rooted_current_drive["metadata"]["allow_outside_cwd"] is True


def test_mcp_allow_outside_cwd_is_reflected_in_metadata(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.html"
    outside.write_text("<h1>Outside</h1>", encoding="utf-8")

    result = lift_html_file_tool(outside, cwd=root, allow_outside_cwd=True)

    assert_tool_schema(result)
    assert result["ok"] is True
    assert result["metadata"]["allow_outside_cwd"] is True

    missing = lift_html_file_tool("missing.html", cwd=root, allow_outside_cwd=True)
    assert_tool_schema(missing)
    assert missing["ok"] is False
    assert missing["errors"][0]["code"] == "INVALID_PATH"
    assert missing["metadata"]["allow_outside_cwd"] is True
    assert missing["metadata"]["network_calls"] == "none"


def test_mcp_symlink_escape_is_rejected_when_supported(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.html"
    outside.write_text("<h1>Outside</h1>", encoding="utf-8")
    link = root / "link.html"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is not available in this environment")

    result = lift_html_file_tool("link.html", cwd=root)

    assert_tool_schema(result)
    assert result["ok"] is False
    assert result["errors"][0]["code"] == "PATH_OUTSIDE_CWD"


def test_mcp_tools_make_no_socket_calls(tmp_path, monkeypatch):
    def fail_socket(*args, **kwargs):
        raise AssertionError("network call attempted")

    monkeypatch.setattr(socket, "create_connection", fail_socket)
    monkeypatch.setattr(socket.socket, "connect", fail_socket)

    html = tmp_path / "report.html"
    html.write_text("<h1>Report</h1>", encoding="utf-8")
    intent = tmp_path / "viewspec.intent.json"
    intent.write_text(json.dumps(_bundle_json()), encoding="utf-8")

    assert validate_intent_bundle_file_tool("viewspec.intent.json", cwd=tmp_path)["ok"] is True
    assert compile_intent_bundle_file_tool("viewspec.intent.json", "intent-dist", cwd=tmp_path)["ok"] is True
    assert compile_html_file_tool("report.html", "dist", cwd=tmp_path)["ok"] is True
    assert check_artifact_tool("dist", cwd=tmp_path)["ok"] is True
    assert lift_html_file_tool("report.html", cwd=tmp_path)["ok"] is True
