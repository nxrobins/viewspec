"""Every CLI subcommand accepts --json.

init and compile print the standard tool envelope on stdout under --json, for success and for
failure, with the exit code their human mode uses (0 success, 2 input or contract error, 1 internal
error) and nothing else on stdout or stderr.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

import viewspec.cli as cli
from viewspec import __version__
from viewspec.app_validation import validate_app_file
from viewspec.cli import main as cli_main
from viewspec.compiler_errors import CompilerInternalError
from viewspec.error_codes import ERROR_CODES
from viewspec.local_tools import file_hash

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
SUBCOMMANDS = ("init", "compile", "check", "prove", "studio", "mcp")
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
    assert cli_main(["init", "--out", str(path)]) == 0
    capsys.readouterr()
    return path


def test_every_subcommand_accepts_json():
    subparsers = _subparsers()

    assert set(SUBCOMMANDS) == set(subparsers)
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


def test_compile_json_reports_invalid_intent(tmp_path, capsys):
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


def test_compile_json_does_not_repeat_the_error_code_in_the_message(tmp_path, capsys):
    # LocalToolContractError formats str(exc) as "CODE: message"; the envelope carries the code in
    # its own field and the bare message beside it.
    html = tmp_path / "input.html"
    html.write_text("<main><h1>Hello</h1></main>", encoding="utf-8")
    argv = ["compile", str(html), "--target", "react-tailwind-tsx", "--out", str(tmp_path / "o")]

    exit_code, payload, err = _run_json(capsys, [*argv, "--json"])

    assert exit_code == 2
    assert err == ""
    _assert_envelope(payload, ok=False)
    assert payload["errors"] == [
        {
            "code": "TAILWIND_IMPORT_NOT_SUPPORTED",
            "message": "Raw HTML import only supports --target html-tailwind",
            "fix": cli._JSON_FAILURE_DEFAULTS["compile"].fix,
        }
    ]
    assert payload["summary"] == "Raw HTML import only supports --target html-tailwind"

    # Human mode keeps the code in its text.
    assert cli_main(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert (
        captured.err == "error: TAILWIND_IMPORT_NOT_SUPPORTED: Raw HTML import only supports --target html-tailwind\n"
    )


# init ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "filename", "path_key"),
    [
        (["init", "design"], "DESIGN.md", "design"),
        (["init"], "viewspec.intent.json", "intent"),
        (["init", "app"], "viewspec.app.json", "app"),
    ],
)
def test_init_commands_json_report_the_written_file_and_refuse_to_overwrite(
    tmp_path, capsys, command, filename, path_key
):
    out = tmp_path / filename

    exit_code, payload, err = _run_json(capsys, [*command, "--out", str(out), "--json"])

    assert exit_code == 0
    assert err == ""
    _assert_envelope(payload, ok=True)
    assert payload["paths"] == {path_key: str(out)}
    assert payload["next_actions"]
    first = out.read_bytes()

    exit_code, payload, err = _run_json(capsys, [*command, "--out", str(out), "--json"])

    assert exit_code == 2
    assert err == ""
    _assert_envelope(payload, ok=False)
    assert payload["errors"][0]["code"] == "IO_ERROR"
    assert "already exists" in payload["errors"][0]["message"]
    assert out.read_bytes() == first

    # Human mode is unchanged: the path on success, the error on stderr on failure.
    out.unlink()
    assert cli_main([*command, "--out", str(out)]) == 0
    assert capsys.readouterr().out == f"{out}\n"
    assert cli_main([*command, "--out", str(out)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: ")


def test_init_intent_and_init_app_json_record_their_options(tmp_path, capsys):
    _code, intent, _err = _run_json(capsys, ["init", "--out", str(tmp_path / "i.json"), "--kind", "form", "--json"])
    _code, app, _err = _run_json(
        capsys,
        ["init", "app", "--out", str(tmp_path / "a.json"), "--resource-binding", "fixture-readonly-v0", "--json"],
    )

    assert intent["metadata"]["kind"] == "form"
    assert app["metadata"]["kind"] == "internal_tool"
    assert app["metadata"]["template"] == "contract"
    assert app["metadata"]["resource_binding"] == "fixture_readonly_v0"


@pytest.mark.parametrize("template", ["contract", "react-app"])
@pytest.mark.parametrize("binding", ["unbound-v0", "fixture-readonly-v0"])
def test_init_app_json_reports_the_binding_of_the_written_bundle(tmp_path, capsys, template, binding):
    # The react-app template writes a fixture_readonly_v0 AppBundle V4 whatever --resource-binding
    # says, including its unbound-v0 default, so the envelope must describe the file.
    out = tmp_path / "viewspec.app.json"

    exit_code, payload, err = _run_json(
        capsys, ["init", "app", "--out", str(out), "--template", template, "--resource-binding", binding, "--json"]
    )
    validation = validate_app_file(out)

    assert exit_code == 0
    assert err == ""
    _assert_envelope(payload, ok=True)
    assert validation["ok"] is True
    assert payload["metadata"]["resource_binding"] == validation["resource_binding"]
    assert payload["metadata"]["binding_scope"] == validation.get("binding_scope")
    if template == "react-app":
        assert json.loads(out.read_text(encoding="utf-8"))["schema_version"] == 4
        assert payload["metadata"]["resource_binding"] == "fixture_readonly_v0"


def test_json_reports_an_internal_error_with_exit_one(tmp_path, capsys, monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(cli, "init_design_file", broken)

    exit_code, payload, err = _run_json(capsys, ["init", "design", "--out", str(tmp_path / "D.md"), "--json"])

    assert exit_code == 1
    assert err == ""
    _assert_envelope(payload, ok=False)
    assert payload["errors"][0]["code"] == "IO_ERROR"
    assert payload["errors"][0]["message"] == "disk on fire"

    assert cli_main(["init", "design", "--out", str(tmp_path / "D.md")]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "internal error: disk on fire\n"


class _MessageWithoutCodeError(RuntimeError):
    """An exception with a .message attribute but no code: its full str() stays the message."""

    def __init__(self) -> None:
        super().__init__("full text with details")
        self.message = "short text"


@pytest.mark.parametrize(
    ("raised", "code", "message"),
    [
        (
            CompilerInternalError("PLANNER_ROLE_METADATA_ONLY", "unsupported product role 'x'."),
            "PLANNER_ROLE_METADATA_ONLY",
            "unsupported product role 'x'.",
        ),
        (_MessageWithoutCodeError(), "IO_ERROR", "full text with details"),
    ],
    ids=["coded", "uncoded"],
)
def test_json_reports_a_coded_error_by_its_bare_message(tmp_path, capsys, monkeypatch, raised, code, message):
    def broken(*_args, **_kwargs):
        raise raised

    monkeypatch.setattr(cli, "init_design_file", broken)

    exit_code, payload, err = _run_json(capsys, ["init", "design", "--out", str(tmp_path / "D.md"), "--json"])

    assert exit_code == 1
    assert err == ""
    _assert_envelope(payload, ok=False)
    assert payload["errors"] == [{"code": code, "message": message, "fix": cli._JSON_FAILURE_DEFAULTS["init"].fix}]
    assert payload["summary"] == message

    assert cli_main(["init", "design", "--out", str(tmp_path / "D.md")]) == 1
    assert capsys.readouterr().err == f"internal error: {raised}\n"


@pytest.mark.parametrize(
    "argv",
    [
        ["compile", "{intent}", "--design", "{directory}", "--out", "{tmp}/dist"],
        ["compile", "{intent}", "--out", "{file}"],
    ],
    ids=["compile-design-directory", "compile-out-file"],
)
def test_json_keeps_exit_one_for_a_path_the_operating_system_rejects(tmp_path, capsys, argv):
    # docs/getting-started.md lists these invalid paths as exiting 1, in both modes, as they always
    # have: the operating system rejects them, and the CLI classifies that OSError as internal.
    places = {
        "tmp": str(tmp_path),
        "intent": str(_intent_file(tmp_path, capsys)),
        "directory": str(tmp_path / "a-directory"),
        "file": str(tmp_path / "a-file"),
    }
    Path(places["directory"]).mkdir()
    Path(places["file"]).write_text("x", encoding="utf-8")
    command = [part.format(**places) for part in argv]

    exit_code, payload, err = _run_json(capsys, [*command, "--json"])

    assert exit_code == 1
    assert err == ""
    _assert_envelope(payload, ok=False)

    assert cli_main(command) == 1
    assert capsys.readouterr().err.startswith("internal error: ")


def test_json_names_an_exception_without_text_by_its_type(tmp_path, capsys, monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError()

    monkeypatch.setattr(cli, "init_design_file", broken)

    exit_code, payload, err = _run_json(capsys, ["init", "design", "--out", str(tmp_path / "D.md"), "--json"])

    assert exit_code == 1
    assert err == ""
    _assert_envelope(payload, ok=False)
    assert payload["summary"] == "RuntimeError"
    assert payload["errors"][0]["message"] == "RuntimeError"

    # Human mode names it the same way.
    assert cli_main(["init", "design", "--out", str(tmp_path / "D.md")]) == 1
    assert capsys.readouterr().err == "internal error: RuntimeError\n"
