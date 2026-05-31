from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path

import pytest

from viewspec import DesignSystemError, ViewSpecBuilder, compile, load_design_system
from viewspec.cli import main as cli_main
from viewspec.local_tools import file_hash


def _design(color: str = "#112233") -> str:
    return f"""---
name: Local
colors:
  primary: "{color}"
  secondary: "#445566"
typography:
  body:
    fontFamily: Inter
spacing:
  md: 10px
---
"""


def test_local_design_maps_shared_tokens_into_reference_compile():
    context = load_design_system(content=_design())
    builder = ViewSpecBuilder("local_design")
    dashboard = builder.add_dashboard("cards", region="main", group_id="cards")
    dashboard.add_card(label="Revenue", value="$12", id="revenue")

    ast = compile(builder.build_bundle(), design=context)

    assert "#112233" in ast.style_values["tone.neutral"]
    assert "#445566" in ast.style_values["tone.muted"]
    root = ast.result.root.root
    assert "tone.neutral" in root.style_tokens


def test_local_design_heading_typography_reaches_prominent_values(tmp_path):
    design = load_design_system(
        content="""---
name: Typography
typography:
  heading:
    fontFamily: Fraunces
    fontSize: 28px
    lineHeight: 1.1
    letterSpacing: 0.02em
---
"""
    )
    builder = ViewSpecBuilder("typography_design")
    dashboard = builder.add_dashboard("cards", region="main", group_id="cards")
    dashboard.add_card(label="Revenue", value="$12", id="revenue")

    ast = compile(builder.build_bundle(), design=design)

    assert "Fraunces" in ast.style_values["emphasis.high"]
    assert "font-size: 28px;" in ast.style_values["emphasis.high"]
    assert "line-height: 1.1;" in ast.style_values["emphasis.high"]
    assert "letter-spacing: 0.02em;" in ast.style_values["emphasis.high"]

    from viewspec.emitters.html_tailwind import HtmlTailwindEmitter

    HtmlTailwindEmitter().emit(ast, tmp_path)
    html = tmp_path.joinpath("index.html").read_text(encoding="utf-8")
    assert "font-family: &quot;Fraunces&quot;" in html
    assert "font-size: 28px;" in html


def test_local_design_accent_themes_action_buttons(tmp_path):
    design = load_design_system(
        content="""---
name: Actions
colors:
  primary: "#111111"
  accent: "#CC5500"
---
"""
    )
    builder = ViewSpecBuilder("action_theme")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Alpha", value="Ready", id="alpha")
    builder.add_action("open_alpha", "select", "Open Alpha", target_region="main")

    ast = compile(builder.build_bundle(), design=design)
    from viewspec.emitters.html_tailwind import HtmlTailwindEmitter

    HtmlTailwindEmitter().emit(ast, tmp_path)
    html = tmp_path.joinpath("index.html").read_text(encoding="utf-8")

    assert "action.accent" in ast.style_values
    assert "background-color: #CC5500;" in ast.style_values["action.accent"]
    assert 'style="background-color: #0f766e; color: #ffffff; background-color: #111111; background-color: #CC5500;"' in html


def test_local_design_rejects_broken_refs_and_cycles_but_warns_on_bad_colors():
    bad_color = load_design_system(content=_design("red"))
    assert any(finding.code == "DESIGN_COLOR_FORMAT_WARNING" for finding in bad_color.lint_report.findings)

    with pytest.raises(DesignSystemError):
        load_design_system(content='---\nname: Broken\ncolors:\n  primary: "{colors.missing}"\n---\n')

    with pytest.raises(DesignSystemError):
        load_design_system(
            content='---\nname: Cycle\ncolors:\n  primary: "{colors.accent}"\n  accent: "{colors.primary}"\n---\n'
        )

    with pytest.raises(DesignSystemError, match="strict"):
        load_design_system(content=_design("red"), strict=True)


def test_cli_compile_lift_and_diff_stay_local(tmp_path, capsys):
    design_path = tmp_path / "DESIGN.md"
    design_path.write_text(_design(), encoding="utf-8")
    html_path = tmp_path / "report.html"
    html_path.write_text("<h1>Report</h1><p>$1</p><button onclick='x()'>Open</button>", encoding="utf-8")
    out_dir = tmp_path / "dist"

    assert cli_main(["compile", str(html_path), "--design", str(design_path), "--out", str(out_dir), "--lift-json"]) == 0
    assert html_path.read_text(encoding="utf-8").startswith("<h1>Report</h1>")
    assert out_dir.joinpath("index.html").exists()
    manifest = json.loads(out_dir.joinpath("provenance_manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == 1
    assert manifest["manifest_schema_version"] == 1
    assert manifest["design_hash"]
    assert manifest["artifact_hash"]
    assert manifest["command_args"] == ["viewspec", "compile", "report.html", "--design", "DESIGN.md", "--out", "<out>", "--lift-json"]
    assert manifest["guarantees"]["network_calls"] == "none"
    assert cli_main(["check", str(out_dir)]) == 0

    lift_path = tmp_path / "lift.json"
    assert cli_main(["lift", str(html_path), "--out", str(lift_path)]) == 0
    assert json.loads(lift_path.read_text(encoding="utf-8"))["source_name"] == "report.html"

    right_path = tmp_path / "report-new.html"
    right_path.write_text("<h1>Report Updated</h1><p>$2</p><button>Open</button>", encoding="utf-8")
    assert cli_main(["diff", str(html_path), str(right_path), "--json"]) == 0
    stdout = capsys.readouterr().out
    assert "Report Updated" in stdout


def test_cli_diff_json_read_error_stays_machine_readable(tmp_path, capsys):
    left = tmp_path / "left.html"
    missing = tmp_path / "missing.html"
    left.write_text("<h1>Left</h1>", encoding="utf-8")

    assert cli_main(["diff", str(left), str(missing), "--json"]) == 2
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert captured.err == ""
    assert payload["diff_version"] == 1
    assert payload["basis"] == "lift_v1"
    assert payload["ok"] is False
    assert payload["errors"][0]["code"] == "DIFF_INPUT_READ_ERROR"


def test_cli_compile_json_wraps_stable_manifest(tmp_path):
    builder = ViewSpecBuilder("json_cli")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Alpha", value="1", id="alpha")
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(builder.build_bundle().to_json()), encoding="utf-8")
    out_dir = tmp_path / "json-dist"

    assert cli_main(["compile", str(bundle_path), "--out", str(out_dir)]) == 0
    manifest = json.loads(out_dir.joinpath("provenance_manifest.json").read_text(encoding="utf-8"))

    assert manifest["version"] == 1
    assert manifest["manifest_schema_version"] == 1
    assert manifest["kind"] == "intent_bundle_compile"
    assert manifest["artifact_hash"]
    assert manifest["command_args"] == ["viewspec", "compile", "bundle.json", "--out", "<out>"]
    assert manifest["guarantees"]["network_calls"] == "none"
    assert manifest["guarantees"]["sdk_network_calls"] == "none"
    assert manifest["guarantees"]["artifact_autofetch_network"] == "none"
    assert manifest["external_refs"] == []
    assert manifest["diagnostics"] == []
    assert "nodes" in manifest


def test_cli_compile_json_can_emit_react_tsx_target(tmp_path):
    builder = ViewSpecBuilder("react_cli")
    field = builder.add_text_input("message", label="Message", value="Hello", group_id="fields")
    builder.add_action("send", "submit", "Send", target_region="main", payload_bindings=[field])
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(builder.build_bundle().to_json()), encoding="utf-8")
    out_dir = tmp_path / "react-dist"

    assert cli_main(["compile", str(bundle_path), "--target", "react-tsx", "--out", str(out_dir)]) == 0
    manifest = json.loads(out_dir.joinpath("provenance_manifest.json").read_text(encoding="utf-8"))
    tsx = out_dir.joinpath("ViewSpecView.tsx").read_text(encoding="utf-8")

    assert out_dir.joinpath("index.html").exists() is False
    assert manifest["kind"] == "intent_bundle_compile"
    assert manifest["emitter"] == "react_tsx"
    assert manifest["artifact_file"] == "ViewSpecView.tsx"
    assert manifest["artifact_hash"] == file_hash(out_dir / "ViewSpecView.tsx")
    assert manifest["command_args"] == [
        "viewspec",
        "compile",
        "bundle.json",
        "--target",
        "react-tsx",
        "--out",
        "<out>",
    ]
    assert 'source: "viewspec-react-tsx"' in tsx
    assert "payloadValues: collectPayloadValues" in tsx


def test_cli_check_uses_byte_exact_artifact_hash(tmp_path, capsys):
    builder = ViewSpecBuilder("byte_exact_hash")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Alpha", value="1", id="alpha")
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(builder.build_bundle().to_json()), encoding="utf-8")
    out_dir = tmp_path / "dist"

    assert cli_main(["compile", str(bundle_path), "--out", str(out_dir)]) == 0
    html_path = out_dir / "index.html"
    manifest = json.loads(out_dir.joinpath("provenance_manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifact_hash"] == file_hash(html_path)
    assert cli_main(["check", str(out_dir), "--json"]) == 0

    html = html_path.read_text(encoding="utf-8")
    html_path.write_bytes(html.replace("\n", "\r\n").encode("utf-8"))

    assert cli_main(["check", str(out_dir)]) == 2
    assert "artifact_hash does not match" in capsys.readouterr().out


def test_cli_init_design_doctor_and_check_tamper(tmp_path, capsys):
    design_path = tmp_path / "DESIGN.md"
    assert cli_main(["init-design", "--out", str(design_path)]) == 0
    assert "colors:" in design_path.read_text(encoding="utf-8")
    capsys.readouterr()
    assert cli_main(["doctor"]) == 0
    doctor_payload = json.loads(capsys.readouterr().out)
    checks = doctor_payload["checks"]
    assert checks["pyyaml"] is True
    assert checks["intent_first_commands"]["validate_intent"] is True
    assert checks["intent_first_commands"]["diff_intent"] is True
    assert checks["intent_first_commands"]["export_agent_assets"] is True
    assert checks["intent_pipeline"]["ok"] is True
    assert checks["intent_pipeline"]["compile_check"] == "passed"
    assert "validate-intent" in checks["local_network_policy"]
    assert "diff-intent" in checks["local_network_policy"]
    assert "export-agent-assets" in checks["local_network_policy"]
    assert "check" in checks["local_network_policy"]

    html_path = tmp_path / "report.html"
    html_path.write_text("<h1>Report</h1>", encoding="utf-8")
    out_dir = tmp_path / "dist"
    assert cli_main(["compile", str(html_path), "--design", str(design_path), "--out", str(out_dir)]) == 0
    assert cli_main(["check", str(out_dir), "--json"]) == 0

    out_dir.joinpath("index.html").write_text("<h1>Tampered</h1>", encoding="utf-8")
    assert cli_main(["check", str(out_dir)]) == 2
    assert "artifact_hash does not match" in capsys.readouterr().out


def test_cli_check_rejects_machine_local_command_args(tmp_path, capsys):
    html_path = tmp_path / "report.html"
    html_path.write_text("<h1>Report</h1>", encoding="utf-8")
    out_dir = tmp_path / "dist"
    assert cli_main(["compile", str(html_path), "--out", str(out_dir)]) == 0

    manifest_path = out_dir / "provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["command_args"] = ["viewspec", "compile", str(html_path)]
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    assert cli_main(["check", str(out_dir)]) == 2
    assert "command_args must not contain absolute paths" in capsys.readouterr().out


def test_cli_version_and_design_lint_errors_are_stable(tmp_path, capsys):
    with pytest.raises(SystemExit) as version_exit:
        cli_main(["--version"])
    assert version_exit.value.code == 0
    assert "viewspec 0.3.0b1" in capsys.readouterr().out

    bad_design = tmp_path / "DESIGN.md"
    bad_design.write_text(_design("red"), encoding="utf-8")
    html_path = tmp_path / "input.html"
    html_path.write_text("<h1>Report</h1>", encoding="utf-8")

    exit_code = cli_main(["compile", str(html_path), "--design", str(bad_design), "--strict-design", "--out", str(tmp_path / "dist")])

    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert "DESIGN.md lint" in stderr
    assert "DESIGN_COLOR_FORMAT_WARNING" in stderr


def test_cli_refuses_input_overwrite(tmp_path, capsys):
    html_path = tmp_path / "index.html"
    html_path.write_text("<h1>Report</h1>", encoding="utf-8")

    exit_code = cli_main(["compile", str(html_path), "--out", str(tmp_path)])

    assert exit_code == 2
    assert "Refusing to overwrite input file" in capsys.readouterr().err


def test_cli_refuses_json_input_overwrite_before_design_load(tmp_path, capsys):
    builder = ViewSpecBuilder("overwrite_json")
    builder.add_dashboard("cards", region="main", group_id="cards").add_card(label="Revenue", value="$12", id="revenue")
    bundle_path = tmp_path / "provenance_manifest.json"
    bundle_path.write_text(json.dumps(builder.build_bundle().to_json()), encoding="utf-8")

    exit_code = cli_main(["compile", str(bundle_path), "--design", str(tmp_path / "missing-DESIGN.md"), "--out", str(tmp_path)])

    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert "Refusing to overwrite input file" in stderr
    assert "missing-DESIGN.md" not in stderr


def test_cli_accepts_explicit_stdin_formats(tmp_path, monkeypatch):
    html_out = tmp_path / "html-out"
    monkeypatch.setattr(sys, "stdin", StringIO("<h1>From stdin</h1>"))
    assert cli_main(["compile", "-", "--stdin-format", "html", "--out", str(html_out)]) == 0
    assert "From stdin" in html_out.joinpath("index.html").read_text(encoding="utf-8")

    builder = ViewSpecBuilder("stdin_json")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Alpha", value="1", id="alpha")
    json_out = tmp_path / "json-out"
    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps(builder.build_bundle().to_json())))
    assert cli_main(["compile", "-", "--stdin-format", "json", "--out", str(json_out)]) == 0
    assert json.loads(json_out.joinpath("provenance_manifest.json").read_text(encoding="utf-8"))["source_name"] == "<stdin>"


def test_claude_code_integration_mentions_only_local_commands():
    root = Path(__file__).resolve().parents[1]
    text = root.joinpath("integrations/claude-code/SKILL.md").read_text(encoding="utf-8")

    assert "viewspec init-intent" in text
    assert "viewspec validate-intent" in text
    assert "viewspec compile" in text
    assert "viewspec diff" in text
    assert "viewspec check" in text
    assert "viewspec.intent.json" in text
    assert "viewspec share" not in text
    assert "api.viewspec.dev" not in text
