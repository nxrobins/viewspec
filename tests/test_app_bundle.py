from __future__ import annotations

import json
from copy import deepcopy

from viewspec.app_bundle import (
    AGENT_APP_BUNDLE_SCHEMA,
    APP_BUNDLE_MAX_BYTES,
    APP_BUNDLE_MAX_EMBEDDED_INTENT_BYTES,
    APP_BUNDLE_RESOURCE_BINDING,
    APP_BUNDLE_RESOURCE_BINDING_READONLY,
    APP_BUNDLE_BINDING_SCOPE,
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
from viewspec.cli import main as cli_main
from viewspec.intent_tools import starter_intent_bundle
from viewspec.local_tools import check_artifact_dir, file_hash


def _app_text(payload: dict) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


def _issue_codes(payload: dict) -> set[str]:
    return {issue["code"] for issue in payload["issues"]}


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
