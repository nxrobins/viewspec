from __future__ import annotations

import json
import time
from pathlib import Path

from viewspec.cli import main as cli_main
from viewspec.host_verify import (
    CommandResult,
    summarize_host_verification_report,
    verify_host_artifact_dir,
    verify_host_tool,
)
from viewspec.local_tools import file_hash
from viewspec.sdk.builder import ViewSpecBuilder


def _write_tailwind_artifact(tmp_path: Path) -> Path:
    builder = ViewSpecBuilder(
        "host_verify",
        root_attrs={"title": "Host Verification"},
        default_main_region=False,
        root_min_children=2,
    )
    builder.set_aesthetic_profile("aesthetic.data_dense")
    builder.add_region("north", parent_region="root", role="banner", layout="stack", min_children=1)
    builder.add_region("canvas", parent_region="root", role="application", layout="grid", min_children=2)
    builder.add_region("focus", parent_region="canvas", role="primary", layout="stack", min_children=1)
    builder.add_region("assist", parent_region="canvas", role="complementary", layout="stack", min_children=1)
    builder.add_hero(
        "intro",
        eyebrow="Proof",
        title="Host verification",
        description="Runtime proof reports should retain checked manifest facts.",
        region="north",
        group_id="intro",
    )
    dashboard = builder.add_dashboard("metrics", region="focus", group_id="cards")
    dashboard.add_card(label="Open", value="18", id="open")
    dashboard.add_card(label="Ready", value="7", id="ready")
    field = builder.add_text_input("query", label="Query", value="old", region="assist", group_id="filters")
    detail = builder.add_detail("identity", region="assist", group_id="details")
    detail.add_field(label="Manifest", value="checked", id="manifest")
    builder.add_action("apply", "submit", "Apply", target_region="assist", payload_bindings=[field])
    bundle_path = tmp_path / "viewspec.intent.json"
    bundle_path.write_text(json.dumps(builder.build_bundle().to_json()), encoding="utf-8")
    out_dir = tmp_path / "react-tailwind-output"
    assert cli_main(["compile", str(bundle_path), "--target", "react-tailwind-tsx", "--out", str(out_dir)]) == 0
    assert cli_main(["check", str(out_dir), "--json"]) == 0
    return out_dir


def _fake_runtime(host_dir, *, install, started, timings):
    generated = host_dir / "src" / "generated"
    assert sorted(path.name for path in generated.iterdir()) == [
        "ViewSpecView.tsx",
        "diagnostics.json",
        "provenance_manifest.json",
    ]
    timings["build"] = 1
    timings["browser"] = 1
    return {
        "assertions": {
            "action_count": 1,
            "aesthetic_layout_assertion_count": 2,
            "aesthetic_profile_assertion_count": 1,
            "dom_count": 4,
            "grid_column_assertion_count": 2,
            "grid_span_assertion_count": 0,
            "payload_binding_count": 1,
            "style_assertion_count": 7,
        },
        "node_version": "v22.0.0",
        "npm_version": "10.0.0",
    }


def test_summarize_host_verification_report_filters_to_bounded_metadata():
    summary = summarize_host_verification_report(
        {
            "ok": True,
            "assertions": {
                "dom_count": 4,
                "style_assertion_count": 7,
                "unsafe_bool": True,
                "unsafe_string": "9",
            },
            "errors": [
                {"code": "HOST_VERIFY_AESTHETIC_LAYOUT_ASSERTION_MISSING", "message": "Missing layout proof."},
                {"message": "No code"},
                "not an error object",
            ],
        }
    )

    assert summary == {
        "ok": True,
        "assertions": {
            "dom_count": 4,
            "style_assertion_count": 7,
        },
        "error_codes": ["HOST_VERIFY_AESTHETIC_LAYOUT_ASSERTION_MISSING"],
    }
    assert summarize_host_verification_report(None) is None


def test_verify_host_artifact_mode_writes_stable_report(tmp_path, monkeypatch):
    out_dir = _write_tailwind_artifact(tmp_path)
    report_path = tmp_path / "host-proof.json"
    monkeypatch.setattr("viewspec.host_verify._run_host_browser_phases", _fake_runtime)

    report = verify_host_artifact_dir(out_dir, install=False, report_out=report_path)

    assert report["ok"] is True
    assert report["target"] == "react-tailwind-tsx"
    assert report["artifact_hash"] == file_hash(out_dir / "ViewSpecView.tsx")
    assert report["manifest_hash"] == file_hash(out_dir / "provenance_manifest.json")
    assert report["diagnostics_hash"] == file_hash(out_dir / "diagnostics.json")
    assert report["assertions"]["aesthetic_layout_assertion_count"] == 2
    assert report["assertions"]["aesthetic_profile_assertion_count"] == 1
    assert report["assertions"]["grid_column_assertion_count"] == 2
    assert report["assertions"]["grid_span_assertion_count"] == 0
    assert report["assertions"]["style_assertion_count"] == 7
    assert report["manifest_summary"]["available"] is True
    assert report["manifest_summary"]["emitter"] == "react_tailwind_tsx"
    assert report["manifest_summary"]["aesthetic_profile"] == "aesthetic.data_dense"
    assert report["manifest_summary"]["aesthetic_layout"]["metric_grid"]["columns"] == 3
    assert json.loads(report_path.read_text(encoding="utf-8")) == report


def test_verify_host_preflight_failure_preserves_manifest_summary(tmp_path):
    out_dir = _write_tailwind_artifact(tmp_path)
    tsx_path = out_dir / "ViewSpecView.tsx"
    tsx_path.write_text(tsx_path.read_text(encoding="utf-8") + "\n// tampered\n", encoding="utf-8")

    report = verify_host_artifact_dir(out_dir)

    assert report["ok"] is False
    assert report["errors"][0]["code"] == "HOST_VERIFY_ARTIFACT_CHECK_FAILED"
    assert any("artifact_hash does not match ViewSpecView.tsx" in error["message"] for error in report["errors"])
    assert report["manifest_summary"]["available"] is True
    assert report["manifest_summary"]["aesthetic_profile"] == "aesthetic.data_dense"
    assert report["manifest_summary"]["aesthetic_layout"]["metric_grid"]["columns"] == 3


def test_verify_host_compile_mode_uses_public_cli_contract(tmp_path, monkeypatch, capsys):
    builder = ViewSpecBuilder("host_verify_compile")
    dashboard = builder.add_dashboard("metrics", region="main", group_id="cards")
    dashboard.add_card(label="Open", value="18", id="open")
    bundle_path = tmp_path / "viewspec.intent.json"
    bundle_path.write_text(json.dumps(builder.build_bundle().to_json()), encoding="utf-8")
    out_dir = tmp_path / "compiled"
    monkeypatch.setattr("viewspec.host_verify._run_host_browser_phases", _fake_runtime)

    assert cli_main(["verify-host", "--intent", str(bundle_path), "--out", str(out_dir), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert out_dir.joinpath("ViewSpecView.tsx").exists()
    assert payload["artifact_hash"] == file_hash(out_dir / "ViewSpecView.tsx")


def test_verify_host_human_output_prints_manifest_and_assertions(tmp_path, monkeypatch, capsys):
    out_dir = _write_tailwind_artifact(tmp_path)
    capsys.readouterr()
    monkeypatch.setattr("viewspec.host_verify._run_host_browser_phases", _fake_runtime)

    assert cli_main(["verify-host", str(out_dir)]) == 0
    output = capsys.readouterr().out

    assert output.startswith("ok\n")
    assert "manifest: kind=intent_bundle_compile emitter=react_tailwind_tsx artifact=ViewSpecView.tsx nodes=" in output
    assert "aesthetic_profile: aesthetic.data_dense" in output
    assert "  content_grid: columns=3 nodes=1 profile=aesthetic.data_dense" in output
    assert "  metric_grid: columns=3 nodes=1 profile=aesthetic.data_dense" in output
    assert "host_assertions:\n" in output
    assert "  action_count: 1" in output
    assert "  aesthetic_layout_assertion_count: 2" in output
    assert "  aesthetic_profile_assertion_count: 1" in output
    assert "  dom_count: 4" in output
    assert "  grid_column_assertion_count: 2" in output
    assert "  grid_span_assertion_count: 0" in output
    assert "  payload_binding_count: 1" in output
    assert "  style_assertion_count: 7" in output


def test_verify_host_rejects_profiled_artifact_without_runtime_aesthetic_proof(tmp_path, monkeypatch):
    out_dir = _write_tailwind_artifact(tmp_path)

    def weak_runtime(host_dir, *, install, started, timings):
        runtime = _fake_runtime(host_dir, install=install, started=started, timings=timings)
        runtime["assertions"].pop("aesthetic_profile_assertion_count")
        return runtime

    monkeypatch.setattr("viewspec.host_verify._run_host_browser_phases", weak_runtime)

    report = verify_host_artifact_dir(out_dir)

    assert report["ok"] is False
    assert report["errors"][0]["code"] == "HOST_VERIFY_AESTHETIC_PROFILE_ASSERTION_MISSING"


def test_verify_host_rejects_profiled_artifact_without_runtime_aesthetic_layout_proof(tmp_path, monkeypatch):
    out_dir = _write_tailwind_artifact(tmp_path)

    def weak_runtime(host_dir, *, install, started, timings):
        runtime = _fake_runtime(host_dir, install=install, started=started, timings=timings)
        runtime["assertions"]["aesthetic_layout_assertion_count"] = 1
        return runtime

    monkeypatch.setattr("viewspec.host_verify._run_host_browser_phases", weak_runtime)

    report = verify_host_artifact_dir(out_dir)

    assert report["ok"] is False
    assert report["errors"][0]["code"] == "HOST_VERIFY_AESTHETIC_LAYOUT_ASSERTION_MISSING"


def test_verify_host_rejects_non_react_tailwind_artifact(tmp_path):
    builder = ViewSpecBuilder("host_verify_html")
    dashboard = builder.add_dashboard("metrics", region="main", group_id="cards")
    dashboard.add_card(label="Open", value="18", id="open")
    bundle_path = tmp_path / "viewspec.intent.json"
    bundle_path.write_text(json.dumps(builder.build_bundle().to_json()), encoding="utf-8")
    out_dir = tmp_path / "html-output"
    assert cli_main(["compile", str(bundle_path), "--out", str(out_dir)]) == 0

    report = verify_host_artifact_dir(out_dir)

    assert report["ok"] is False
    assert report["errors"][0]["code"] == "HOST_VERIFY_UNSUPPORTED_TARGET"


def test_verify_host_missing_node_and_node_modules_are_exact_codes(tmp_path, monkeypatch):
    out_dir = _write_tailwind_artifact(tmp_path)
    monkeypatch.setattr("viewspec.host_verify.shutil.which", lambda name: None if name == "node" else name)

    missing_node = verify_host_artifact_dir(out_dir)

    assert missing_node["ok"] is False
    assert missing_node["errors"][0]["code"] == "HOST_VERIFY_NODE_MISSING"

    monkeypatch.setattr("viewspec.host_verify.shutil.which", lambda name: name)
    monkeypatch.setattr("viewspec.host_verify._run_process", lambda *args, **kwargs: CommandResult("v-test", "", 0))
    missing_modules = verify_host_artifact_dir(out_dir)

    assert missing_modules["ok"] is False
    assert missing_modules["errors"][0]["code"] == "HOST_VERIFY_NODE_MODULES_MISSING"


def test_verify_host_install_runs_npm_ci_ignore_scripts(tmp_path, monkeypatch):
    host_dir = tmp_path / "host"
    host_dir.mkdir()
    commands: list[list[str]] = []

    def fake_run(command, *, cwd, timeout_ms, code, env=None):
        commands.append(command)
        if command[1:] == ["ci", "--ignore-scripts"]:
            bin_dir = cwd / "node_modules" / ".bin"
            bin_dir.mkdir(parents=True)
            for name in ("vite", "playwright"):
                bin_dir.joinpath(name).write_text("", encoding="utf-8")
        if command[1:3] == ["run", "test"] and env:
            Path(env["VIEWSPEC_HOST_VERIFY_BROWSER_REPORT"]).parent.mkdir(parents=True)
            Path(env["VIEWSPEC_HOST_VERIFY_BROWSER_REPORT"]).write_text(
                json.dumps(
                    {
                        "assertions": {
                            "action_count": 0,
                            "aesthetic_layout_assertion_count": 0,
                            "aesthetic_profile_assertion_count": 0,
                            "dom_count": 1,
                            "grid_column_assertion_count": 1,
                            "grid_span_assertion_count": 0,
                            "payload_binding_count": 0,
                            "style_assertion_count": 4,
                        }
                    }
                ),
                encoding="utf-8",
            )
        return CommandResult("v-test", "", 0)

    monkeypatch.setattr("viewspec.host_verify._require_executable", lambda name, code: name)
    monkeypatch.setattr("viewspec.host_verify._run_process", fake_run)
    monkeypatch.setattr("viewspec.host_verify._start_preview", lambda host, npm, port: None)
    monkeypatch.setattr("viewspec.host_verify._wait_for_preview", lambda port, started: None)
    monkeypatch.setattr("viewspec.host_verify._kill_process_tree", lambda proc: None)

    from viewspec.host_verify import _run_host_browser_phases

    runtime = _run_host_browser_phases(host_dir, install=True, started=time.perf_counter(), timings={})

    assert ["npm", "ci", "--ignore-scripts"] in commands
    assert runtime["assertions"]["grid_column_assertion_count"] == 1
    assert runtime["assertions"]["style_assertion_count"] == 4


def test_verify_host_mcp_tool_respects_cwd_containment(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    result = verify_host_tool(str(outside), cwd=root)

    assert result["ok"] is False
    assert result["errors"][0]["code"] == "PATH_OUTSIDE_CWD"


def test_verify_host_mcp_tool_metadata_exposes_bounded_proof_summary(tmp_path, monkeypatch):
    _write_tailwind_artifact(tmp_path)
    monkeypatch.setattr("viewspec.host_verify._run_host_browser_phases", _fake_runtime)

    result = verify_host_tool("react-tailwind-output", cwd=tmp_path)

    assert result["ok"] is True
    assert result["metadata"]["manifest_summary"]["available"] is True
    assert result["metadata"]["manifest_summary"]["aesthetic_profile"] == "aesthetic.data_dense"
    assert result["metadata"]["host_verification"] == {
        "ok": True,
        "assertions": {
            "action_count": 1,
            "aesthetic_layout_assertion_count": 2,
            "aesthetic_profile_assertion_count": 1,
            "dom_count": 4,
            "grid_column_assertion_count": 2,
            "grid_span_assertion_count": 0,
            "payload_binding_count": 1,
            "style_assertion_count": 7,
        },
        "error_codes": [],
    }
    assert result["proof_report"]["manifest_summary"] == result["metadata"]["manifest_summary"]


def test_verify_host_mcp_tool_metadata_exposes_failure_codes(tmp_path, monkeypatch):
    _write_tailwind_artifact(tmp_path)

    def weak_runtime(host_dir, *, install, started, timings):
        runtime = _fake_runtime(host_dir, install=install, started=started, timings=timings)
        runtime["assertions"].pop("aesthetic_profile_assertion_count")
        return runtime

    monkeypatch.setattr("viewspec.host_verify._run_host_browser_phases", weak_runtime)

    result = verify_host_tool("react-tailwind-output", cwd=tmp_path)

    assert result["ok"] is False
    assert result["metadata"]["manifest_summary"]["aesthetic_profile"] == "aesthetic.data_dense"
    assert result["metadata"]["host_verification"]["ok"] is False
    assert result["metadata"]["host_verification"]["error_codes"] == ["HOST_VERIFY_AESTHETIC_PROFILE_ASSERTION_MISSING"]
