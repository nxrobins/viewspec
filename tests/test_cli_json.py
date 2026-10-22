"""Every CLI subcommand accepts --json (audit C-9).

compile, lift, doctor, init-app, init-design, init-intent, init-agent and export-agent-assets print
the standard tool envelope on stdout under --json, for success and for failure, with the exit code
their human mode uses (0 success, 2 input or contract error, 1 internal error) and nothing else on
stdout or stderr. `mcp` is a stdio server: --json only turns a startup failure into one line of
JSON on stderr, and stdout stays reserved for the protocol.
"""

from __future__ import annotations

import argparse
import json
import sys
import types
from pathlib import Path

import pytest

import viewspec.cli as cli
import viewspec.mcp_server as mcp_server_module
from viewspec import ERROR_CODES, __version__
from viewspec.app_tools import init_app_tool
from viewspec.app_validation import validate_app_file
from viewspec.cli import main as cli_main
from viewspec.local_tools import export_agent_assets_tool, file_hash
from viewspec.mcp_server import MCP_INSTALL_HINT, MissingMCPDependency
from viewspec.native_agents import BEGIN_MARKER, END_MARKER

ENVELOPE_KEYS = {
    "schema_version",
    "ok",
    "summary",
    "diagnostics",
    "external_refs",
    "paths",
    "next_actions",
    "errors",
    "metadata",
}
NEWLY_JSON_SUBCOMMANDS = (
    "compile",
    "lift",
    "init-app",
    "init-design",
    "init-intent",
    "doctor",
    "init-agent",
    "export-agent-assets",
    "mcp",
)
DESIGN = """---
name: CLI JSON
colors:
  primary: "#123456"
---
"""
BROKEN_DESIGN = '---\nname: Broken\ncolors:\n  primary: "{colors.missing}"\n---\n'


def _run_json(capsys, argv: list[str]) -> tuple[int, dict, str]:
    exit_code = cli_main(argv)
    captured = capsys.readouterr()
    # The whole of stdout is one JSON document.
    return exit_code, json.loads(captured.out), captured.err


def _assert_envelope(payload: dict, *, ok: bool) -> None:
    assert set(payload) >= ENVELOPE_KEYS
    assert payload["schema_version"] == 1
    assert payload["ok"] is ok
    assert isinstance(payload["summary"], str) and payload["summary"]
    assert payload["metadata"]["sdk_version"] == __version__
    assert payload["metadata"]["network_calls"] == "none"
    if ok:
        assert payload["errors"] == []
    else:
        assert payload["errors"]
        for error in payload["errors"]:
            assert set(error) == {"code", "message", "fix"}
            assert error["code"] in ERROR_CODES
            assert error["message"] and error["fix"]


def _subparsers() -> dict[str, argparse.ArgumentParser]:
    parser = cli._build_parser()
    action = next(item for item in parser._actions if isinstance(item, argparse._SubParsersAction))
    return dict(action.choices)


def _intent_file(tmp_path: Path, capsys) -> Path:
    path = tmp_path / "viewspec.intent.json"
    assert cli_main(["init-intent", "--out", str(path)]) == 0
    capsys.readouterr()
    return path


def test_every_subcommand_accepts_json():
    subparsers = _subparsers()

    assert set(NEWLY_JSON_SUBCOMMANDS) <= set(subparsers)
    assert sorted(name for name, sub in subparsers.items() if "--json" not in sub._option_string_actions) == []


# compile -------------------------------------------------------------------------------------


def test_compile_json_reports_the_emitted_paths_in_the_envelope(tmp_path, capsys):
    intent = _intent_file(tmp_path, capsys)
    design = tmp_path / "DESIGN.md"
    design.write_text(DESIGN, encoding="utf-8")
    out_dir = tmp_path / "dist"

    exit_code, payload, err = _run_json(
        capsys, ["compile", str(intent), "--design", str(design), "--out", str(out_dir), "--json"]
    )

    assert exit_code == 0
    assert err == ""
    _assert_envelope(payload, ok=True)
    assert payload["paths"] == {
        "html": str(out_dir / "index.html"),
        "manifest": str(out_dir / "provenance_manifest.json"),
        "diagnostics": str(out_dir / "diagnostics.json"),
    }
    assert payload["metadata"]["target"] == "html-tailwind"
    assert payload["metadata"]["emitter"] == "html_tailwind"
    assert cli_main(["check", str(out_dir)]) == 0


def test_compile_json_writes_the_same_artifact_bytes_as_human_mode(tmp_path, capsys):
    intent = _intent_file(tmp_path, capsys)
    human_out = tmp_path / "human"
    json_out = tmp_path / "json"

    assert cli_main(["compile", str(intent), "--target", "react-tailwind-tsx", "--out", str(human_out)]) == 0
    human_paths = json.loads(capsys.readouterr().out)
    exit_code, payload, _err = _run_json(
        capsys, ["compile", str(intent), "--target", "react-tailwind-tsx", "--out", str(json_out), "--json"]
    )

    assert exit_code == 0
    # Default output is unchanged: the bare paths mapping, not the envelope.
    assert set(human_paths) == {"tsx", "manifest", "diagnostics"}
    assert set(payload["paths"]) == set(human_paths)
    for name in ("ViewSpecView.tsx", "provenance_manifest.json", "diagnostics.json"):
        assert file_hash(json_out / name) == file_hash(human_out / name)


def test_compile_json_reports_invalid_intent_like_the_mcp_tool(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text('{"bad": 1}', encoding="utf-8")

    exit_code, payload, err = _run_json(capsys, ["compile", str(bad), "--out", str(tmp_path / "out"), "--json"])

    assert exit_code == 2
    assert err == ""
    _assert_envelope(payload, ok=False)
    assert payload["summary"] == "IntentBundle validation failed."
    assert {error["code"] for error in payload["errors"]} == {"UNKNOWN_FIELD", "MISSING_FIELD"}
    assert payload["validation"]["ok"] is False
    assert payload["correction_prompt"] == payload["validation"]["correction_prompt"]
    assert not (tmp_path / "out").exists()

    # Human mode keeps reporting the failure on stderr.
    assert cli_main(["compile", str(bad), "--out", str(tmp_path / "out")]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: IntentBundle validation failed\n")


def test_compile_json_reports_a_design_error_with_its_findings(tmp_path, capsys):
    intent = _intent_file(tmp_path, capsys)
    design = tmp_path / "BROKEN.md"
    design.write_text(BROKEN_DESIGN, encoding="utf-8")
    argv = ["compile", str(intent), "--design", str(design), "--out", str(tmp_path / "dist")]

    exit_code, payload, err = _run_json(capsys, [*argv, "--json"])

    assert exit_code == 2
    assert err == ""
    _assert_envelope(payload, ok=False)
    assert payload["errors"][0]["code"] == "COMPILE_FAILED"
    assert payload["errors"][0]["fix"] == "Fix DESIGN.md and retry."
    assert [finding["code"] for finding in payload["diagnostics"]] == ["DESIGN_TOKEN_REF_ERROR"]

    assert cli_main(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "DESIGN.md lint: 1 error(s), 0 warning(s), 0 info" in captured.err
    assert "error: DESIGN_TOKEN_REF_ERROR at colors.primary" in captured.err


def test_compile_json_imports_raw_html_with_diagnostics_and_external_refs(tmp_path, capsys):
    html = tmp_path / "input.html"
    html.write_text('<main><h1>Hello</h1><img src="https://example.com/a.png" alt="A"></main>', encoding="utf-8")
    out_dir = tmp_path / "dist"

    exit_code, payload, err = _run_json(capsys, ["compile", str(html), "--out", str(out_dir), "--json"])

    assert exit_code == 0
    assert err == ""
    _assert_envelope(payload, ok=True)
    assert payload["paths"]["html"] == str(out_dir / "index.html")
    assert "HTML_REMOTE_IMAGE_INERT" in {item["code"] for item in payload["diagnostics"]}
    assert payload["external_refs"] == [
        {"attr": "src", "behavior": "inert_placeholder", "kind": "image", "url": "https://example.com/a.png"}
    ]
    assert payload["metadata"]["input_format"] == "html"


def test_compile_json_keeps_a_coded_contract_error_code(tmp_path, capsys):
    html = tmp_path / "input.html"
    html.write_text("<main><h1>Hello</h1></main>", encoding="utf-8")

    exit_code, payload, _err = _run_json(
        capsys, ["compile", str(html), "--target", "react-tailwind-tsx", "--out", str(tmp_path / "o"), "--json"]
    )

    assert exit_code == 2
    assert payload["errors"][0]["code"] == "TAILWIND_IMPORT_NOT_SUPPORTED"


# lift ----------------------------------------------------------------------------------------


def test_lift_json_returns_the_lift_and_its_paths(tmp_path, capsys):
    html = tmp_path / "input.html"
    html.write_text("<main><h1>Report</h1><p>Body</p></main>", encoding="utf-8")
    lift_path = tmp_path / "lift.json"

    exit_code, payload, err = _run_json(capsys, ["lift", str(html), "--out", str(lift_path), "--json"])

    assert exit_code == 0
    assert err == ""
    _assert_envelope(payload, ok=True)
    assert payload["paths"] == {"input": str(html), "lift": str(lift_path)}
    assert payload["lift"] == json.loads(lift_path.read_text(encoding="utf-8"))

    assert cli_main(["lift", str(html), "--out", str(lift_path)]) == 0
    assert capsys.readouterr().out == f"{lift_path}\n"


def test_lift_json_reports_a_missing_input(tmp_path, capsys):
    exit_code, payload, err = _run_json(
        capsys, ["lift", str(tmp_path / "missing.html"), "--out", str(tmp_path / "lift.json"), "--json"]
    )

    assert exit_code == 2
    assert err == ""
    _assert_envelope(payload, ok=False)
    assert payload["errors"][0]["code"] == "COMPILE_FAILED"
    assert "missing.html" in payload["errors"][0]["message"]


# init-* --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "filename", "path_key"),
    [
        ("init-design", "DESIGN.md", "design"),
        ("init-intent", "viewspec.intent.json", "intent"),
        ("init-app", "viewspec.app.json", "app"),
    ],
)
def test_init_commands_json_report_the_written_file_and_refuse_to_overwrite(
    tmp_path, capsys, command, filename, path_key
):
    out = tmp_path / filename

    exit_code, payload, err = _run_json(capsys, [command, "--out", str(out), "--json"])

    assert exit_code == 0
    assert err == ""
    _assert_envelope(payload, ok=True)
    assert payload["paths"] == {path_key: str(out)}
    assert payload["next_actions"]
    first = out.read_bytes()

    exit_code, payload, err = _run_json(capsys, [command, "--out", str(out), "--json"])

    assert exit_code == 2
    assert err == ""
    _assert_envelope(payload, ok=False)
    assert payload["errors"][0]["code"] == "IO_ERROR"
    assert "already exists" in payload["errors"][0]["message"]
    assert out.read_bytes() == first

    # Human mode is unchanged: the path on success, the error on stderr on failure.
    out.unlink()
    assert cli_main([command, "--out", str(out)]) == 0
    assert capsys.readouterr().out == f"{out}\n"
    assert cli_main([command, "--out", str(out)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: ")


def test_init_intent_and_init_app_json_record_their_options(tmp_path, capsys):
    _code, intent, _err = _run_json(
        capsys, ["init-intent", "--out", str(tmp_path / "i.json"), "--kind", "form", "--json"]
    )
    _code, app, _err = _run_json(
        capsys,
        ["init-app", "--out", str(tmp_path / "a.json"), "--resource-binding", "fixture-readonly-v0", "--json"],
    )

    assert intent["metadata"]["kind"] == "form"
    assert app["metadata"]["kind"] == "internal_tool"
    assert app["metadata"]["template"] == "contract"
    assert app["metadata"]["resource_binding"] == "fixture_readonly_v0"


@pytest.mark.parametrize("template", ["contract", "react-app"])
@pytest.mark.parametrize("binding", ["unbound-v0", "fixture-readonly-v0"])
def test_init_app_json_reports_the_binding_of_the_written_bundle(tmp_path, capsys, template, binding):
    # The react-app template writes a fixture_readonly_v0 AppBundle V4 whatever --resource-binding
    # says, including its unbound-v0 default, so the envelope must describe the file, as MCP
    # init_app does for the same arguments.
    out = tmp_path / "viewspec.app.json"

    exit_code, payload, err = _run_json(
        capsys, ["init-app", "--out", str(out), "--template", template, "--resource-binding", binding, "--json"]
    )
    validation = validate_app_file(out)
    mcp_payload = init_app_tool("mcp.app.json", template=template, resource_binding=binding, cwd=tmp_path)

    assert exit_code == 0
    assert err == ""
    _assert_envelope(payload, ok=True)
    assert validation["ok"] is True
    assert payload["metadata"]["resource_binding"] == validation["resource_binding"]
    assert payload["metadata"]["binding_scope"] == validation.get("binding_scope")
    keys = ("kind", "template", "resource_binding", "binding_scope")
    assert {key: payload["metadata"][key] for key in keys} == {key: mcp_payload["metadata"][key] for key in keys}
    if template == "react-app":
        assert json.loads(out.read_text(encoding="utf-8"))["schema_version"] == 4
        assert payload["metadata"]["resource_binding"] == "fixture_readonly_v0"


def test_json_reports_an_internal_error_with_exit_one(tmp_path, capsys, monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(cli, "init_design_file", broken)

    exit_code, payload, err = _run_json(capsys, ["init-design", "--out", str(tmp_path / "D.md"), "--json"])

    assert exit_code == 1
    assert err == ""
    _assert_envelope(payload, ok=False)
    assert payload["errors"][0]["code"] == "IO_ERROR"
    assert payload["errors"][0]["message"] == "disk on fire"

    assert cli_main(["init-design", "--out", str(tmp_path / "D.md")]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "internal error: disk on fire\n"


# doctor --------------------------------------------------------------------------------------


def test_doctor_json_wraps_the_same_checks_the_default_output_prints(capsys):
    assert cli_main(["doctor"]) == 0
    default = json.loads(capsys.readouterr().out)

    exit_code, payload, err = _run_json(capsys, ["doctor", "--json"])

    assert set(default) == {"ok", "checks"}
    assert exit_code == 0
    assert err == ""
    _assert_envelope(payload, ok=True)
    # --json is a superset of the default output.
    assert payload["checks"] == default["checks"]
    assert payload["ok"] is default["ok"]


def test_doctor_json_names_each_failing_check(capsys, monkeypatch):
    monkeypatch.setattr(cli, "mcp_dependency_available", lambda: False)

    exit_code, payload, err = _run_json(capsys, ["doctor", "--agents", "--json"])

    assert exit_code == 2
    assert err == ""
    _assert_envelope(payload, ok=False)
    assert payload["checks"]["mcp_dependency"] is False
    assert {
        "code": "DOCTOR_CHECK_FAILED",
        "message": "Readiness check failed: mcp_dependency.",
        "fix": MCP_INSTALL_HINT,
    } in payload["errors"]


def test_doctor_failing_checks_walks_nested_reports():
    checks = {
        "pyyaml": True,
        "b": {"ok": False, "items": [True, False], "note": "text"},
        "a": False,
    }

    assert cli._doctor_failing_checks(checks) == ["a", "b.items[1]", "b.ok"]
    assert cli._doctor_failing_checks({"ok": True, "items": []}) == []


def test_doctor_json_reports_a_missing_bun_with_the_probe_error_and_fix(tmp_path, capsys, monkeypatch):
    # Bun is not a viewspec dependency, so reinstalling viewspec can never fix the Freerange probe:
    # the envelope carries the probe's own coded error and its fix.
    monkeypatch.setenv("PATH", str(tmp_path))

    exit_code, payload, err = _run_json(capsys, ["doctor", "--freerange", "--json"])

    assert exit_code == 2
    assert err == ""
    _assert_envelope(payload, ok=False)
    probe_errors = payload["checks"]["freerange"]["errors"]
    assert [error["code"] for error in probe_errors] == ["APP_FREERANGE_RUNTIME_MISSING"]
    assert payload["errors"] == [
        {
            "code": "DOCTOR_CHECK_FAILED",
            "message": "Readiness check failed: freerange.ok.",
            "fix": "Install Bun explicitly, then rerun viewspec doctor --freerange.",
        },
        {
            "code": "APP_FREERANGE_RUNTIME_MISSING",
            "message": "Bun is required for the pinned Freerange analyzer but was not found.",
            "fix": "Install Bun explicitly, then rerun viewspec doctor --freerange.",
        },
    ]


def test_doctor_json_falls_back_to_a_bun_fix_when_the_probe_names_no_error(capsys, monkeypatch):
    monkeypatch.setattr(
        "viewspec.app_freerange.freerange_readiness", lambda: {"ok": False, "status": "unavailable", "errors": []}
    )

    exit_code, payload, _err = _run_json(capsys, ["doctor", "--freerange", "--json"])

    assert exit_code == 2
    assert payload["errors"] == [
        {
            "code": "DOCTOR_CHECK_FAILED",
            "message": "Readiness check failed: freerange.ok.",
            "fix": "Install or upgrade Bun explicitly (ViewSpec never installs it), then rerun viewspec doctor --freerange.",
        }
    ]


@pytest.mark.parametrize(
    ("directory", "check"), [("demos", "published_agent_assets"), (".viewspec", "local_agent_assets")]
)
def test_doctor_json_says_how_to_refresh_stale_agent_assets(tmp_path, capsys, monkeypatch, directory, check):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "mcp_dependency_available", lambda: True)
    assert cli_main(["export-agent-assets", "--out", directory]) == 0
    capsys.readouterr()
    with (tmp_path / directory / "agent-system-prompt.txt").open("a", encoding="utf-8") as handle:
        handle.write("A local edit.\n")

    exit_code, payload, err = _run_json(capsys, ["doctor", "--agents", "--json"])

    assert exit_code == 2
    assert err == ""
    _assert_envelope(payload, ok=False)
    assert payload["checks"][check]["ok"] is False
    assert payload["errors"] == [
        {
            "code": "DOCTOR_CHECK_FAILED",
            "message": f"Readiness check failed: {check}.ok.",
            "fix": (
                f"Re-export the agent assets with viewspec export-agent-assets --out {directory} --force, or "
                f"upgrade viewspec instead if a newer ViewSpec release exported {directory}; then rerun "
                "viewspec doctor --agents."
            ),
        }
    ]

    # The re-export that the fix names repairs the check.
    assert cli_main(["export-agent-assets", "--out", directory, "--force"]) == 0
    capsys.readouterr()
    exit_code, payload, _err = _run_json(capsys, ["doctor", "--agents", "--json"])
    assert exit_code == 0
    assert payload["errors"] == []


# init-agent and export-agent-assets ----------------------------------------------------------


def test_init_agent_json_is_a_superset_of_the_default_output(tmp_path, capsys):
    assert cli_main(["init-agent", "--target", "codex", "--root", str(tmp_path / "a"), "--dry-run"]) == 0
    default = json.loads(capsys.readouterr().out)

    exit_code, payload, err = _run_json(
        capsys, ["init-agent", "--target", "codex", "--root", str(tmp_path / "a"), "--dry-run", "--json"]
    )

    assert exit_code == 0
    assert err == ""
    _assert_envelope(payload, ok=True)
    assert {key: payload[key] for key in default} == {**default, "ok": True}
    assert payload["paths"] == {"root": default["root"]}
    assert payload["metadata"]["dry_run"] is True
    assert not (tmp_path / "a" / "AGENTS.md").exists()


def test_init_agent_json_reports_a_marker_conflict(tmp_path, capsys):
    (tmp_path / "AGENTS.md").write_text(
        f"{BEGIN_MARKER}\none\n{END_MARKER}\n{BEGIN_MARKER}\ntwo\n{END_MARKER}\n", encoding="utf-8"
    )

    exit_code, payload, err = _run_json(capsys, ["init-agent", "--target", "codex", "--root", str(tmp_path), "--json"])

    assert exit_code == 2
    assert err == ""
    _assert_envelope(payload, ok=False)
    assert payload["errors"][0]["code"] == "MARKER_CONFLICT"


def test_export_agent_assets_json_matches_the_mcp_tool_envelope(tmp_path, capsys):
    out_dir = tmp_path / ".viewspec"

    exit_code, payload, err = _run_json(capsys, ["export-agent-assets", "--out", str(out_dir), "--json"])
    mcp_payload = export_agent_assets_tool(out_dir, dry_run=True, cwd=tmp_path)

    assert exit_code == 0
    assert err == ""
    _assert_envelope(payload, ok=True)
    assert payload["paths"] == mcp_payload["paths"]
    assert payload["next_actions"] == mcp_payload["next_actions"]
    assert payload["assets"]["files"]
    assert payload["metadata"]["changes"] == len(payload["assets"]["files"])
    assert cli_main(["check-agent-assets", str(out_dir)]) == 0


def test_export_agent_assets_json_reports_a_conflict(tmp_path, capsys):
    out_dir = tmp_path / ".viewspec"
    out_dir.mkdir()
    (out_dir / "agent-system-prompt.txt").write_text("custom prompt\n", encoding="utf-8")

    exit_code, payload, err = _run_json(capsys, ["export-agent-assets", "--out", str(out_dir), "--json"])

    assert exit_code == 2
    assert err == ""
    _assert_envelope(payload, ok=False)
    assert payload["errors"][0]["code"] == "AGENT_ASSET_CONFLICT"
    assert (out_dir / "agent-system-prompt.txt").read_text(encoding="utf-8") == "custom prompt\n"


# mcp -----------------------------------------------------------------------------------------


def _stderr_envelope(err: str) -> dict:
    assert err.endswith("\n") and err.count("\n") == 1, err
    return json.loads(err)


def test_mcp_json_reports_a_missing_sdk_on_stderr_only(capsys, monkeypatch):
    def missing_mcp(**_kwargs):
        raise MissingMCPDependency(MCP_INSTALL_HINT)

    monkeypatch.setattr(cli, "run_mcp_server", missing_mcp)

    assert cli_main(["mcp", "--json"]) == 2
    captured = capsys.readouterr()

    assert captured.out == ""
    payload = _stderr_envelope(captured.err)
    _assert_envelope(payload, ok=False)
    assert payload["errors"][0]["code"] == "MCP_DEPENDENCY_MISSING"
    assert payload["errors"][0]["fix"] == MCP_INSTALL_HINT

    # Human mode keeps printing only the install hint.
    assert cli_main(["mcp"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"{MCP_INSTALL_HINT}\n"


def test_mcp_json_reports_an_invalid_cwd_on_stderr_only(tmp_path, capsys, monkeypatch):
    class UnusedFastMCP:
        def __init__(self, _name: str) -> None:
            raise AssertionError("the server must not start with an invalid --cwd")

    fake_fastmcp = types.ModuleType("mcp.server.fastmcp")
    fake_fastmcp.FastMCP = UnusedFastMCP  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fake_fastmcp)
    monkeypatch.setattr(mcp_server_module, "mcp_dependency_available", lambda: True)
    missing = tmp_path / "missing"

    assert cli_main(["mcp", "--cwd", str(missing), "--json"]) == 2
    captured = capsys.readouterr()

    assert captured.out == ""
    payload = _stderr_envelope(captured.err)
    _assert_envelope(payload, ok=False)
    assert payload["errors"][0]["code"] == "INVALID_PATH"

    assert cli_main(["mcp", "--cwd", str(missing)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"error: Invalid cwd: {missing}\n"


def test_mcp_json_reports_an_unexpected_server_failure_with_exit_one(capsys, monkeypatch):
    def crashing(**_kwargs):
        raise RuntimeError("transport closed")

    monkeypatch.setattr(cli, "run_mcp_server", crashing)

    assert cli_main(["mcp", "--json"]) == 1
    captured = capsys.readouterr()

    assert captured.out == ""
    payload = _stderr_envelope(captured.err)
    assert payload["errors"][0] == {
        "code": "MCP_SERVER_FAILED",
        "message": "transport closed",
        "fix": "Fix the reported problem and restart viewspec mcp.",
    }
