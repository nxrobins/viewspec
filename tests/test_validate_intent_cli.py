from __future__ import annotations

import json
import subprocess
import sys

import pytest

from viewspec import (
    STARTER_INTENT_KINDS,
    ViewSpecBuilder,
    diff_intent_text,
    init_intent_file,
    profile_style_facts,
    starter_intent_bundle,
    validate_intent_file,
    validate_intent_text,
)
from viewspec.cli import main as cli_main


def _valid_bundle_json() -> dict:
    builder = ViewSpecBuilder("validate_cli")
    dashboard = builder.add_dashboard("cards", region="main", group_id="cards")
    dashboard.add_card(label="Revenue", value="$12", id="revenue")
    return builder.build_bundle().to_json()


def _profile_workspace_bundle_json(profile: str) -> dict:
    builder = ViewSpecBuilder(
        "validate_cli_profile_workspace",
        root_attrs={"title": "Profile Workspace"},
        default_main_region=False,
        root_min_children=2,
    )
    builder.set_aesthetic_profile(profile)
    builder.add_region("north", parent_region="root", role="banner", layout="stack", min_children=1)
    builder.add_region("canvas", parent_region="root", role="application", layout="grid", min_children=2)
    builder.add_region("focus", parent_region="canvas", role="primary", layout="stack", min_children=1)
    builder.add_region("assist", parent_region="canvas", role="complementary", layout="stack", min_children=1)
    builder.add_hero(
        "intro",
        eyebrow="Operations",
        title="Profile workspace",
        description="Check output should expose manifest layout facts.",
        region="north",
        group_id="intro",
    )
    dashboard = builder.add_dashboard("numbers", region="focus", group_id="metrics")
    dashboard.add_card(label="Open", value="4", id="open")
    dashboard.add_card(label="Blocked", value="1", id="blocked")
    dashboard.add_card(label="Ready", value="9", id="ready")
    detail = builder.add_detail("identity", region="assist", group_id="details")
    detail.add_field(label="Manifest", value="checked", id="manifest")
    return builder.build_bundle().to_json()


def test_validate_intent_valid_bundle_exits_zero_and_returns_json(tmp_path, capsys):
    path = tmp_path / "viewspec.intent.json"
    path.write_text(json.dumps(_valid_bundle_json()), encoding="utf-8")

    assert cli_main(["validate-intent", str(path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload == {
        "schema_version": 1,
        "ok": True,
        "compile_check": "passed",
        "issues": [],
        "repair_checklist": [],
        "correction_prompt": None,
    }


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("", "INVALID_JSON"),
        ("not json", "INVALID_JSON"),
        ("```json\n{}\n```", "INVALID_JSON"),
        (json.dumps([]), "INVALID_PAYLOAD"),
        (json.dumps({"id": "root", "primitive": "stack", "children": []}), "COMPOSITION_IR_INPUT"),
    ],
)
def test_validate_intent_rejects_non_intent_payloads(tmp_path, capsys, text, code):
    path = tmp_path / "bad.json"
    path.write_text(text, encoding="utf-8")

    assert cli_main(["validate-intent", str(path), "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)

    assert payload["schema_version"] == 1
    assert payload["ok"] is False
    assert payload["compile_check"] == "failed"
    assert any(issue["code"] == code for issue in payload["issues"])
    assert all(issue["suggestion"] for issue in payload["issues"])
    assert payload["correction_prompt"]
    assert payload["repair_checklist"]
    assert any("Regenerate the full IntentBundle" in item for item in payload["repair_checklist"])
    assert "Output strict JSON only" in payload["correction_prompt"]
    assert "Do not patch fragments" in payload["correction_prompt"]


@pytest.mark.parametrize(
    ("text", "expected_message"),
    [
        ('{"substrate":{},"substrate":{},"view_spec":{}}', "duplicate object key 'substrate'"),
        (lambda: json.dumps(_valid_bundle_json()).replace('"$12"', "NaN", 1), "non-standard JSON constant 'NaN'"),
        (lambda: json.dumps(_valid_bundle_json()).replace('"$12"', "Infinity", 1), "non-standard JSON constant 'Infinity'"),
    ],
)
def test_validate_intent_rejects_non_strict_json(tmp_path, capsys, text, expected_message):
    payload_text = text() if callable(text) else text
    path = tmp_path / "bad.json"
    path.write_text(payload_text, encoding="utf-8")

    assert cli_main(["validate-intent", str(path), "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is False
    assert payload["issues"][0]["code"] == "INVALID_JSON"
    assert expected_message in payload["issues"][0]["message"]
    assert payload["repair_checklist"]
    assert payload["correction_prompt"]


def test_validate_intent_no_compile_check_returns_skipped(tmp_path, capsys):
    path = tmp_path / "viewspec.intent.json"
    path.write_text(json.dumps(_valid_bundle_json()), encoding="utf-8")

    assert cli_main(["validate-intent", str(path), "--no-compile-check", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert payload["compile_check"] == "skipped"
    assert payload["repair_checklist"] == []


def test_public_intent_sdk_helpers_are_root_exports(tmp_path):
    bundle = starter_intent_bundle("list")
    text = json.dumps(bundle.to_json())
    output_path = tmp_path / "starter.intent.json"

    validation = validate_intent_text(text)
    written = init_intent_file(output_path, kind="list")
    file_validation = validate_intent_file(written)
    diff = diff_intent_text(text, output_path.read_text(encoding="utf-8"), compile_check=False)

    assert output_path == written
    assert validation["ok"] is True
    assert file_validation["ok"] is True
    assert diff["ok"] is True
    assert diff["basis"] == "intent_bundle_v1"
    assert "list" in STARTER_INTENT_KINDS
    assert "form" in STARTER_INTENT_KINDS
    assert "detail" in STARTER_INTENT_KINDS
    assert "empty_state" in STARTER_INTENT_KINDS
    assert "loading_state" in STARTER_INTENT_KINDS
    assert "error_state" in STARTER_INTENT_KINDS
    assert "hero" in STARTER_INTENT_KINDS


def test_validate_intent_reports_hosted_only_fields_before_cascading_errors(tmp_path, capsys):
    hosted = _valid_bundle_json()
    hosted["view_spec"]["inputs"] = [{"id": "phase_filter"}]
    hosted["view_spec"]["actions"].append(
        {
            "id": "save",
            "kind": "submit",
            "label": "Save",
            "target_region": "main",
            "target_ref": None,
            "payload_bindings": ["phase_filter"],
        }
    )
    path = tmp_path / "hosted.intent.json"
    path.write_text(json.dumps(hosted), encoding="utf-8")

    assert cli_main(["validate-intent", str(path), "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is False
    assert any(issue["code"] == "HOSTED_ONLY_FIELD" and issue["path"] == "$.view_spec.inputs" for issue in payload["issues"])
    assert not any(issue["code"] == "UNKNOWN_ACTION_PAYLOAD_BINDING" for issue in payload["issues"])


def test_validate_intent_missing_file_returns_json_payload(tmp_path, capsys):
    path = tmp_path / "missing.intent.json"

    assert cli_main(["validate-intent", str(path), "--json"]) == 2
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert captured.err == ""
    assert payload["schema_version"] == 1
    assert payload["ok"] is False
    assert payload["compile_check"] == "failed"
    assert payload["issues"][0]["code"] == "INTENT_FILE_NOT_FOUND"
    assert payload["correction_prompt"]


def test_validate_intent_process_exit_codes_match_public_contract(tmp_path):
    valid = tmp_path / "valid.intent.json"
    valid.write_text(json.dumps(_valid_bundle_json()), encoding="utf-8")
    invalid = tmp_path / "invalid.intent.json"
    invalid.write_text(json.dumps([]), encoding="utf-8")
    missing = tmp_path / "missing.intent.json"

    valid_code, valid_stdout, valid_stderr = _run_validate_intent_process(tmp_path, "valid", valid)
    invalid_code, invalid_stdout, invalid_stderr = _run_validate_intent_process(tmp_path, "invalid", invalid)
    missing_code, missing_stdout, missing_stderr = _run_validate_intent_process(tmp_path, "missing", missing)

    assert valid_code == 0
    assert json.loads(valid_stdout)["ok"] is True
    assert valid_stderr == ""
    assert invalid_code == 2
    assert json.loads(invalid_stdout)["ok"] is False
    assert invalid_stderr == ""
    assert missing_code == 2
    assert json.loads(missing_stdout)["issues"][0]["code"] == "INTENT_FILE_NOT_FOUND"
    assert missing_stderr == ""


def _run_validate_intent_process(tmp_path, name: str, path) -> tuple[int, str, str]:
    stdout_path = tmp_path / f"{name}.stdout"
    stderr_path = tmp_path / f"{name}.stderr"
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        result = subprocess.run(
            [sys.executable, "-m", "viewspec.cli", "validate-intent", str(path), "--json"],
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            check=False,
        )
    return (
        result.returncode,
        stdout_path.read_text(encoding="utf-8"),
        stderr_path.read_text(encoding="utf-8"),
    )


def test_validate_intent_does_not_mutate_file(tmp_path):
    path = tmp_path / "viewspec.intent.json"
    original = json.dumps(_valid_bundle_json(), indent=2, sort_keys=True)
    path.write_text(original, encoding="utf-8")

    assert cli_main(["validate-intent", str(path), "--json"]) == 0

    assert path.read_text(encoding="utf-8") == original


def test_diff_intent_reports_semantic_intent_changes(tmp_path, capsys):
    left = tmp_path / "old.intent.json"
    right = tmp_path / "new.intent.json"
    left_payload = _valid_bundle_json()
    right_payload = _valid_bundle_json()
    right_payload["substrate"]["nodes"]["revenue"]["attrs"]["value"] = "$13"
    right_payload["view_spec"]["actions"].append(
        {
            "id": "refresh",
            "kind": "select",
            "label": "Refresh",
            "target_region": "main",
            "target_ref": "binding:revenue_value",
            "payload_bindings": ["revenue_value"],
        }
    )
    left.write_text(json.dumps(left_payload), encoding="utf-8")
    right.write_text(json.dumps(right_payload), encoding="utf-8")

    assert cli_main(["diff-intent", str(left), str(right), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["schema_version"] == 1
    assert payload["ok"] is True
    assert payload["diff_version"] == 1
    assert payload["basis"] == "intent_bundle_v1"
    assert payload["compile_check"] == "passed"
    assert payload["topology_similarity"] < 1
    assert payload["changes"]["substrate_nodes"]["changed"] == ["revenue"]
    assert payload["changes"]["actions"]["added"] == ["refresh"]
    assert {
        "id": "refresh",
        "change": "added",
        "kind": "select",
        "target_ref": "binding:revenue_value",
        "payload_bindings": ["revenue_value"],
    } in payload["semantic_changes"]["actions"]
    assert {
        "section": "substrate_nodes",
        "id": "revenue",
        "field": "attrs",
        "left": {"label": "Revenue", "value": "$12"},
        "right": {"label": "Revenue", "value": "$13"},
    } in payload["changed_fields"]


def test_diff_intent_reports_motif_and_binding_semantic_changes(tmp_path, capsys):
    left = tmp_path / "old.intent.json"
    right = tmp_path / "new.intent.json"
    left_payload = _valid_bundle_json()
    right_payload = _valid_bundle_json()
    right_payload["substrate"]["nodes"]["conversion"] = {
        "id": "conversion",
        "kind": "dashboard_card",
        "attrs": {"label": "Conversion", "value": "12%"},
        "slots": {},
        "edges": {},
    }
    right_payload["view_spec"]["bindings"].extend(
        [
            {
                "id": "conversion_label",
                "address": "node:conversion#attr:label",
                "target_region": "main",
                "present_as": "label",
                "cardinality": "exactly_once",
            },
            {
                "id": "conversion_value",
                "address": "node:conversion#attr:value",
                "target_region": "main",
                "present_as": "value",
                "cardinality": "exactly_once",
            },
        ]
    )
    right_payload["view_spec"]["groups"][0]["members"].extend(["conversion_label", "conversion_value"])
    right_payload["view_spec"]["motifs"][0]["members"].extend(["conversion_label", "conversion_value"])
    right_payload["view_spec"]["bindings"][1]["present_as"] = "badge"
    left.write_text(json.dumps(left_payload), encoding="utf-8")
    right.write_text(json.dumps(right_payload), encoding="utf-8")

    assert cli_main(["diff-intent", str(left), str(right), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert {
        "id": "cards",
        "change": "members_changed",
        "added": ["conversion_label", "conversion_value"],
        "removed": [],
        "order_changed": False,
        "left_order": ["revenue_label", "revenue_value"],
        "right_order": ["revenue_label", "revenue_value", "conversion_label", "conversion_value"],
    } in payload["semantic_changes"]["motifs"]
    assert {
        "id": "revenue_value",
        "change": "presentation_changed",
        "left": "value",
        "right": "badge",
    } in payload["semantic_changes"]["bindings"]
    assert any(change["id"] == "conversion_label" and change["change"] == "added" for change in payload["semantic_changes"]["bindings"])


def test_diff_intent_reports_region_group_and_style_semantic_changes(tmp_path, capsys):
    left = tmp_path / "old.intent.json"
    right = tmp_path / "new.intent.json"
    left_payload = _valid_bundle_json()
    right_payload = _valid_bundle_json()
    left_payload["view_spec"]["styles"].append(
        {"id": "value_tone", "target": "binding:revenue_value", "token": "tone.neutral"}
    )
    right_payload["view_spec"]["styles"].append(
        {"id": "value_tone", "target": "binding:revenue_value", "token": "tone.accent"}
    )
    right_payload["view_spec"]["regions"][1]["layout"] = "cluster"
    right_payload["view_spec"]["groups"][0]["members"] = ["revenue_value", "revenue_label"]
    left.write_text(json.dumps(left_payload), encoding="utf-8")
    right.write_text(json.dumps(right_payload), encoding="utf-8")

    assert cli_main(["diff-intent", str(left), str(right), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert {
        "id": "main",
        "change": "layout_changed",
        "left": "stack",
        "right": "cluster",
    } in payload["semantic_changes"]["regions"]
    assert {
        "id": "cards",
        "change": "members_changed",
        "added": [],
        "removed": [],
        "order_changed": True,
        "left_order": ["revenue_label", "revenue_value"],
        "right_order": ["revenue_value", "revenue_label"],
    } in payload["semantic_changes"]["groups"]
    assert {
        "id": "value_tone",
        "change": "token_changed",
        "left": "tone.neutral",
        "right": "tone.accent",
    } in payload["semantic_changes"]["styles"]


def test_diff_intent_reports_aesthetic_profile_semantic_changes(tmp_path, capsys):
    left = tmp_path / "old.intent.json"
    right = tmp_path / "new.intent.json"
    left.write_text(json.dumps(_profile_workspace_bundle_json("aesthetic.calm_ops")), encoding="utf-8")
    right.write_text(json.dumps(_profile_workspace_bundle_json("aesthetic.executive_review")), encoding="utf-8")

    assert cli_main(["diff-intent", str(left), str(right), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert payload["semantic_changes"]["aesthetic_profiles"] == [
        {
            "change": "profile_changed",
            "left": "aesthetic.calm_ops",
            "right": "aesthetic.executive_review",
            "left_style_id": "aesthetic_profile",
            "right_style_id": "aesthetic_profile",
            "left_target": "view:validate_cli_profile_workspace",
            "right_target": "view:validate_cli_profile_workspace",
        }
    ]
    assert {
        "id": "aesthetic_profile",
        "change": "token_changed",
        "left": "aesthetic.calm_ops",
        "right": "aesthetic.executive_review",
    } in payload["semantic_changes"]["styles"]


def test_diff_intent_reports_top_level_bundle_metadata_changes(tmp_path, capsys):
    left = tmp_path / "old.intent.json"
    right = tmp_path / "new.intent.json"
    left_payload = _valid_bundle_json()
    right_payload = _valid_bundle_json()
    right_payload["substrate"]["id"] = "validate_cli_substrate_v2"
    right_payload["view_spec"]["substrate_id"] = "validate_cli_substrate_v2"
    right_payload["view_spec"]["id"] = "validate_cli_v2"
    right_payload["view_spec"]["complexity_tier"] = 2
    left.write_text(json.dumps(left_payload), encoding="utf-8")
    right.write_text(json.dumps(right_payload), encoding="utf-8")

    assert cli_main(["diff-intent", str(left), str(right), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert payload["changes"]["bundle_metadata"]["changed"] == [
        "substrate.id",
        "view_spec.complexity_tier",
        "view_spec.id",
        "view_spec.substrate_id",
    ]
    assert payload["topology_similarity"] < 1
    assert {
        "section": "bundle_metadata",
        "id": "$",
        "field": "view_spec.id",
        "left": "validate_cli",
        "right": "validate_cli_v2",
    } in payload["changed_fields"]
    assert {
        "section": "bundle_metadata",
        "id": "$",
        "field": "substrate.id",
        "left": "validate_cli_substrate",
        "right": "validate_cli_substrate_v2",
    } in payload["changed_fields"]


def test_diff_intent_returns_validation_errors_for_invalid_input(tmp_path, capsys):
    left = tmp_path / "old.intent.json"
    right = tmp_path / "new.intent.json"
    left.write_text(json.dumps([]), encoding="utf-8")
    right.write_text(json.dumps(_valid_bundle_json()), encoding="utf-8")

    assert cli_main(["diff-intent", str(left), str(right), "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is False
    assert payload["basis"] == "intent_bundle_v1"
    assert payload["compile_check"] == "failed"
    assert payload["errors"][0]["side"] == "left"
    assert payload["errors"][0]["code"] == "INVALID_PAYLOAD"


def test_diff_intent_no_compile_check_returns_skipped(tmp_path, capsys):
    left = tmp_path / "old.intent.json"
    right = tmp_path / "new.intent.json"
    left.write_text(json.dumps(_valid_bundle_json()), encoding="utf-8")
    right.write_text(json.dumps(_valid_bundle_json()), encoding="utf-8")

    assert cli_main(["diff-intent", str(left), str(right), "--no-compile-check", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert payload["compile_check"] == "skipped"
    assert payload["topology_similarity"] == 1.0


def test_compile_invalid_intent_returns_correction_payload_without_writes(tmp_path, capsys):
    path = tmp_path / "viewspec.intent.json"
    path.write_text(json.dumps([]), encoding="utf-8")
    out_dir = tmp_path / "dist"

    assert cli_main(["compile", str(path), "--out", str(out_dir)]) == 2
    captured = capsys.readouterr()

    assert captured.out == ""
    assert "IntentBundle validation failed" in captured.err
    assert "INVALID_PAYLOAD" in captured.err
    assert "correction_prompt" in captured.err
    assert not out_dir.exists()


def test_compile_invalid_intent_reports_validation_before_missing_design(tmp_path, capsys):
    path = tmp_path / "viewspec.intent.json"
    path.write_text(json.dumps([]), encoding="utf-8")
    out_dir = tmp_path / "dist"
    missing_design = tmp_path / "missing-DESIGN.md"

    assert cli_main(["compile", str(path), "--design", str(missing_design), "--out", str(out_dir)]) == 2
    captured = capsys.readouterr()

    assert "IntentBundle validation failed" in captured.err
    assert "INVALID_PAYLOAD" in captured.err
    assert "missing-DESIGN.md" not in captured.err
    assert not out_dir.exists()


def test_check_human_output_prints_manifest_summary_for_aesthetic_artifact(tmp_path, capsys):
    style_facts = profile_style_facts("aesthetic.editorial_product")
    intent_path = tmp_path / "viewspec.intent.json"
    intent_path.write_text(
        json.dumps(_profile_workspace_bundle_json("aesthetic.editorial_product")),
        encoding="utf-8",
    )
    out_dir = tmp_path / "react-tailwind-dist"

    assert cli_main(
        ["compile", str(intent_path), "--target", "react-tailwind-tsx", "--out", str(out_dir)]
    ) == 0
    capsys.readouterr()

    assert cli_main(["check", str(out_dir)]) == 0
    output = capsys.readouterr().out

    assert output.startswith("ok\n")
    assert (
        "manifest: kind=intent_bundle_compile emitter=react_tailwind_tsx artifact=ViewSpecView.tsx nodes="
        in output
    )
    assert "aesthetic_profile: aesthetic.editorial_product" in output
    assert (
        "aesthetic_style: "
        f"profile=aesthetic.editorial_product "
        f"changed_tokens={style_facts['changed_token_count']} "
        f"categories={style_facts['category_count']} "
        f"declarations={style_facts['declaration_count']}"
    ) in output
    assert "aesthetic_layout:\n" in output
    assert "  content_grid: columns=2 nodes=1 profile=aesthetic.editorial_product" in output
    assert "  metric_grid: columns=1 nodes=1 profile=aesthetic.editorial_product" in output


def test_check_human_output_prints_aesthetic_span_layout_summary(tmp_path, capsys):
    style_facts = profile_style_facts("aesthetic.premium_saas")
    intent_path = tmp_path / "viewspec.intent.json"
    intent_path.write_text(
        json.dumps(_profile_workspace_bundle_json("aesthetic.premium_saas")),
        encoding="utf-8",
    )
    out_dir = tmp_path / "react-tailwind-dist"

    assert cli_main(
        ["compile", str(intent_path), "--target", "react-tailwind-tsx", "--out", str(out_dir)]
    ) == 0
    capsys.readouterr()

    assert cli_main(["check", str(out_dir)]) == 0
    output = capsys.readouterr().out

    assert "aesthetic_profile: aesthetic.premium_saas" in output
    assert (
        "aesthetic_style: "
        f"profile=aesthetic.premium_saas "
        f"changed_tokens={style_facts['changed_token_count']} "
        f"categories={style_facts['category_count']} "
        f"declarations={style_facts['declaration_count']}"
    ) in output
    assert "  content_grid: columns=2 nodes=1 profile=aesthetic.premium_saas" in output
    assert "  metric_card: span_columns=2 nodes=1 profile=aesthetic.premium_saas" in output
    assert "  metric_card: columns=unknown" not in output
    assert "  metric_grid: columns=2 nodes=1 profile=aesthetic.premium_saas" in output


@pytest.mark.parametrize("kind", STARTER_INTENT_KINDS)
def test_init_intent_writes_valid_starter_bundle(tmp_path, capsys, kind):
    path = tmp_path / f"{kind}.intent.json"

    assert cli_main(["init-intent", "--out", str(path), "--kind", kind]) == 0
    assert capsys.readouterr().out.strip() == str(path)

    validation = validate_intent_file(path)
    assert validation["ok"] is True
    assert validation["compile_check"] == "passed"

    html_out = tmp_path / f"{kind}-html"
    react_out = tmp_path / f"{kind}-react"
    assert cli_main(["compile", str(path), "--out", str(html_out)]) == 0
    capsys.readouterr()
    assert cli_main(["check", str(html_out), "--json"]) == 0
    capsys.readouterr()
    assert cli_main(["compile", str(path), "--target", "react-tsx", "--out", str(react_out)]) == 0
    capsys.readouterr()
    assert cli_main(["check", str(react_out), "--json"]) == 0
    capsys.readouterr()


def test_init_intent_refuses_overwrite_without_force(tmp_path):
    path = tmp_path / "viewspec.intent.json"
    path.write_text("{}", encoding="utf-8")

    assert cli_main(["init-intent", "--out", str(path)]) == 2
    assert path.read_text(encoding="utf-8") == "{}"

    assert cli_main(["init-intent", "--out", str(path), "--force"]) == 0
    assert validate_intent_file(path)["ok"] is True
