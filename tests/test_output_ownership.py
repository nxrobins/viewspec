"""S-14: a refused ``viewspec prove`` writes nothing into a directory it does not own.

``--force`` replaces an existing directory only when ``is_replaceable_output_dir`` accepts it: an
empty directory, or one that holds a file ViewSpec writes, such as ``proof_report.json``. A refused
proof used to write its report files into the directory it had just refused, so the ``--force`` its
refusal recommended then deleted that directory. A run now writes report files into its output
directory only when it created that directory itself, or when the directory was replaceable before
the run wrote anything.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pytest

from viewspec.app_bundle import compile_app, prove_app, starter_app_bundle
from viewspec.app_errors import AppBundleProofFailure
from viewspec.app_reports import (
    APP_BUNDLE_DEFAULT_REPORT,
    APP_BUNDLE_DEFAULT_SUMMARY,
    APP_BUNDLE_DEFAULT_SUPPORT_BUNDLE,
)
from viewspec.cli import main as cli_main
from viewspec.intent_tools import init_intent_file
from viewspec.local_tools import LocalToolError
from viewspec.mcp_server import ViewSpecTools
from viewspec.prove import PROVE_DEFAULT_REPORT, PROVE_DEFAULT_SUMMARY, PROVE_DEFAULT_SUPPORT_BUNDLE, prove

NOTES = "precious\n"
PROOF_FILES = {PROVE_DEFAULT_REPORT, PROVE_DEFAULT_SUMMARY, PROVE_DEFAULT_SUPPORT_BUNDLE}
APP_PROOF_FILES = {APP_BUNDLE_DEFAULT_REPORT, APP_BUNDLE_DEFAULT_SUMMARY, APP_BUNDLE_DEFAULT_SUPPORT_BUNDLE}


def _names(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.iterdir())


def _codes(report: dict[str, Any]) -> list[str]:
    return [error["code"] for error in report["errors"]]


def _assert_docs_untouched(workspace: Path) -> None:
    docs = workspace / "docs"
    assert _names(docs) == ["notes.txt"]
    assert (docs / "notes.txt").read_text(encoding="utf-8") == NOTES


def _assert_fix_does_not_offer_force(report: dict[str, Any]) -> None:
    fix = report["errors"][0]["fix"]
    assert "--force" not in fix
    assert "--out" in fix


def _prove_cli(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict[str, Any]]:
    exit_code = cli_main(["prove", *argv, "--json"])
    return exit_code, json.loads(capsys.readouterr().out)


def _write_app(path: Path) -> Path:
    path.write_text(json.dumps(starter_app_bundle(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The audit's reproduction: a working directory that holds an unrelated ``docs/`` directory."""
    monkeypatch.chdir(tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "notes.txt").write_text(NOTES, encoding="utf-8")
    return tmp_path


# The reproduction, step by step -------------------------------------------------------------------


def test_cli_refusal_writes_nothing_and_the_next_force_is_refused(workspace, capsys) -> None:
    exit_code, refused = _prove_cli(capsys, "--out", "docs")

    assert exit_code == 2
    assert _codes(refused) == ["PROVE_OUTPUT_EXISTS"]
    _assert_fix_does_not_offer_force(refused)
    _assert_docs_untouched(workspace)

    exit_code, forced = _prove_cli(capsys, "--out", "docs", "--force")

    assert exit_code == 2
    assert _codes(forced) == ["PROVE_OUTPUT_UNRECOGNIZED"]
    _assert_docs_untouched(workspace)


@pytest.mark.parametrize(
    ("what", "source", "refused_code", "forced_code"),
    [
        pytest.param("intent", None, "PROVE_OUTPUT_EXISTS", "PROVE_OUTPUT_UNRECOGNIZED", id="starter"),
        pytest.param(
            "app", "viewspec.app.json", "APP_PROOF_OUTPUT_EXISTS", "APP_PROOF_OUTPUT_UNRECOGNIZED", id="app-bundle"
        ),
    ],
)
def test_mcp_refusal_writes_nothing_and_the_next_force_is_refused(
    workspace, what, source, refused_code, forced_code
) -> None:
    tools = ViewSpecTools(workspace)
    try:
        if source is not None:
            assert tools.init(what=what, out=source)["ok"] is True

        refused = tools.prove(source, out="docs")

        assert _codes(refused) == [refused_code]
        _assert_fix_does_not_offer_force(refused)
        _assert_docs_untouched(workspace)

        forced = tools.prove(source, out="docs", force=True)

        assert _codes(forced) == [forced_code]
        _assert_docs_untouched(workspace)
    finally:
        tools.close()


def test_cli_app_proof_of_a_missing_source_writes_nothing(workspace, capsys) -> None:
    exit_code, failed = _prove_cli(capsys, "typo.app.json", "--out", "docs")

    assert exit_code == 2
    assert _codes(failed) == ["APP_PROOF_INPUT_READ_ERROR"]
    _assert_docs_untouched(workspace)

    exit_code, forced = _prove_cli(capsys, "--out", "docs", "--force")

    assert exit_code == 2
    assert _codes(forced) == ["PROVE_OUTPUT_UNRECOGNIZED"]
    _assert_docs_untouched(workspace)


# --report-out ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("flags", "code"),
    [([], "PROVE_OUTPUT_EXISTS"), (["--force"], "PROVE_OUTPUT_UNRECOGNIZED")],
    ids=["refused", "forced"],
)
def test_report_out_is_the_only_file_a_refused_proof_writes(workspace, capsys, flags, code) -> None:
    exit_code, refused = _prove_cli(capsys, "--out", "docs", "--report-out", "r.json", *flags)

    assert exit_code == 2
    assert _codes(refused) == [code]
    _assert_docs_untouched(workspace)
    assert _names(workspace) == ["docs", "r.json"]
    assert _codes(json.loads((workspace / "r.json").read_text(encoding="utf-8"))) == [code]


def test_report_out_inside_a_refused_directory_is_not_written(workspace, capsys) -> None:
    # A report named like a marker would make the refused directory replaceable all by itself.
    exit_code, refused = _prove_cli(capsys, "--out", "docs", "--report-out", f"docs/{PROVE_DEFAULT_REPORT}")

    assert exit_code == 2
    assert _codes(refused) == ["PROVE_OUTPUT_EXISTS"]
    _assert_docs_untouched(workspace)


# Every failure branch ----------------------------------------------------------------------------


@pytest.mark.parametrize("contents", [["notes.txt"], []], ids=["workspace", "empty-workspace"])
def test_mcp_prove_with_an_unknown_target_writes_nothing_into_the_workspace_root(tmp_path, contents) -> None:
    for name in contents:
        (tmp_path / name).write_text(NOTES, encoding="utf-8")
    tools = ViewSpecTools(tmp_path)
    try:
        report = tools.prove(target="bogus", out=".")
    finally:
        tools.close()

    assert _codes(report) == ["PROVE_UNSUPPORTED_TARGET"]
    assert _names(tmp_path) == contents


@pytest.mark.parametrize(
    ("options", "code"),
    [
        pytest.param({}, "PROVE_OUTPUT_EXISTS", id="starter"),
        pytest.param({"intent_path": "viewspec.intent.json"}, "PROVE_OUTPUT_EXISTS", id="intent-source"),
        pytest.param({"intent_path": "page.html"}, "PROVE_OUTPUT_EXISTS", id="html-source"),
        pytest.param({"target": "mcp-app"}, "PROVE_OUTPUT_EXISTS", id="mcp-app"),
        pytest.param({"pretext": True}, "PROVE_OUTPUT_EXISTS", id="html-browser"),
        pytest.param({"target": "react-tailwind-tsx"}, "PROVE_OUTPUT_EXISTS", id="react-tailwind-tsx"),
        pytest.param({"force": True}, "PROVE_OUTPUT_UNRECOGNIZED", id="force"),
        pytest.param({"intent_path": "page.html", "force": True}, "PROVE_OUTPUT_UNRECOGNIZED", id="html-force"),
        pytest.param({"target": "bogus"}, "PROVE_UNSUPPORTED_TARGET", id="unknown-target"),
        pytest.param({"kind": "bogus"}, "PROVE_UNSUPPORTED_KIND", id="unknown-kind"),
    ],
)
def test_no_refused_intent_proof_writes_into_an_unrelated_directory(workspace, options, code) -> None:
    init_intent_file(workspace / "viewspec.intent.json")
    (workspace / "page.html").write_text("<!doctype html><title>Page</title><h1>Hello</h1>\n", encoding="utf-8")

    report = prove(out_dir="docs", cwd=workspace, **options)

    assert _codes(report) == [code]
    if code == "PROVE_OUTPUT_EXISTS":
        _assert_fix_does_not_offer_force(report)
    _assert_docs_untouched(workspace)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        pytest.param(RuntimeError("boom"), "PROVE_INTERNAL_ERROR", id="internal"),
        pytest.param(LocalToolError("INVALID_PATH", "boom", "Pass a valid path."), "PROVE_INPUT_READ_ERROR", id="path"),
    ],
)
def test_an_error_before_the_proof_directory_is_prepared_writes_nothing(workspace, monkeypatch, error, code) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise error

    monkeypatch.setattr(importlib.import_module("viewspec.prove"), "_assert_safe_output", fail)

    report = prove(out_dir="docs", cwd=workspace)

    assert _codes(report) == [code]
    _assert_docs_untouched(workspace)


@pytest.mark.parametrize(
    ("source", "options", "code"),
    [
        pytest.param("typo.app.json", {}, "APP_PROOF_INPUT_READ_ERROR", id="missing-source"),
        pytest.param("typo.app.json", {"force": True}, "APP_PROOF_INPUT_READ_ERROR", id="missing-source-force"),
        pytest.param("invalid.app.json", {}, "APP_ROUTE_PATH_INVALID", id="invalid-source"),
        pytest.param("viewspec.app.json", {}, "APP_PROOF_OUTPUT_EXISTS", id="exists"),
        pytest.param("viewspec.app.json", {"with_shell": True}, "APP_PROOF_OUTPUT_EXISTS", id="with-shell"),
        pytest.param("viewspec.app.json", {"force": True}, "APP_PROOF_OUTPUT_UNRECOGNIZED", id="force"),
        pytest.param(
            "viewspec.app.json",
            {"report_out": f"docs/{APP_BUNDLE_DEFAULT_REPORT}"},
            "APP_PROOF_OUTPUT_EXISTS",
            id="report-out",
        ),
        pytest.param("viewspec.app.json", {"report_out": "r.json"}, "APP_PROOF_REPORT_PATH_UNSAFE", id="report-path"),
    ],
)
def test_no_refused_app_proof_writes_into_an_unrelated_directory(workspace, source, options, code) -> None:
    _write_app(workspace / "viewspec.app.json")
    invalid = starter_app_bundle()
    invalid["routes"][0]["path"] = "/bad?query=1"
    (workspace / "invalid.app.json").write_text(json.dumps(invalid), encoding="utf-8")

    report = prove_app(app_path=source, out_dir="docs", cwd=workspace, **options)

    assert _codes(report)[0] == code
    if code == "APP_PROOF_OUTPUT_EXISTS":
        _assert_fix_does_not_offer_force(report)
    _assert_docs_untouched(workspace)
    assert not (workspace / "r.json").exists()


def test_an_app_error_before_the_proof_directory_is_prepared_writes_nothing(workspace, monkeypatch) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("boom")

    _write_app(workspace / "viewspec.app.json")
    monkeypatch.setattr(importlib.import_module("viewspec.app_pipeline"), "validate_app_text", fail)

    report = prove_app(app_path="viewspec.app.json", out_dir="docs", cwd=workspace)

    assert _codes(report) == ["APP_PROOF_INTERNAL_ERROR"]
    _assert_docs_untouched(workspace)


# A directory the run created is its own ------------------------------------------------------------


def test_a_proof_that_created_its_directory_still_writes_its_failure_report(tmp_path) -> None:
    report = prove(intent_path="missing.intent.json", out_dir="proof", cwd=tmp_path)

    assert _codes(report) == ["PROVE_INPUT_READ_ERROR"]
    assert set(_names(tmp_path / "proof")) >= PROOF_FILES


@pytest.mark.parametrize(
    ("error", "code"),
    [
        pytest.param(
            AppBundleProofFailure("APP_PROOF_OUTPUT_UNSAFE", "Resolved proof path escaped proof root.", "Retry."),
            "APP_PROOF_OUTPUT_UNSAFE",
            id="proof-failure",
        ),
        pytest.param(RuntimeError("boom"), "APP_PROOF_INTERNAL_ERROR", id="internal"),
    ],
)
def test_an_app_proof_that_created_its_directory_still_writes_its_failure_report(
    tmp_path, monkeypatch, error, code
) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise error

    _write_app(tmp_path / "viewspec.app.json")
    monkeypatch.setattr(importlib.import_module("viewspec.app_pipeline"), "prove_app_screens", fail)

    report = prove_app(app_path="viewspec.app.json", out_dir="app-proof", cwd=tmp_path)

    assert _codes(report) == [code]
    assert set(_names(tmp_path / "app-proof")) >= APP_PROOF_FILES


# What --force may replace is unchanged -------------------------------------------------------------


def _intent_output(tmp_path: Path, prior: str) -> Path:
    out = tmp_path / "out"
    if prior == "empty":
        out.mkdir()
    elif prior == "proof":
        assert prove(out_dir=out, cwd=tmp_path)["ok"] is True
    else:
        intent = init_intent_file(tmp_path / "viewspec.intent.json")
        assert cli_main(["compile", str(intent), "--out", str(out)]) == 0
    return out


def _app_output(tmp_path: Path, app: Path, prior: str) -> Path:
    out = tmp_path / "out"
    if prior == "empty":
        out.mkdir()
    elif prior == "proof":
        assert prove_app(app_path=app, out_dir=out, cwd=tmp_path)["ok"] is True
    else:
        assert compile_app(app, out_dir=out, cwd=tmp_path)["ok"] is True
    return out


@pytest.mark.parametrize("prior", ["empty", "proof", "compile"])
def test_a_refusal_offers_force_for_a_directory_force_replaces(tmp_path, prior) -> None:
    out = _intent_output(tmp_path, prior)

    refused = prove(out_dir=out, cwd=tmp_path)

    assert _codes(refused) == ["PROVE_OUTPUT_EXISTS"]
    assert "--force" in refused["errors"][0]["fix"]
    assert prove(out_dir=out, cwd=tmp_path, force=True)["ok"] is True


@pytest.mark.parametrize("prior", ["empty", "proof", "compile"])
def test_an_app_refusal_offers_force_for_a_directory_force_replaces(tmp_path, prior) -> None:
    app = _write_app(tmp_path / "viewspec.app.json")
    out = _app_output(tmp_path, app, prior)

    refused = prove_app(app_path=app, out_dir=out, cwd=tmp_path)

    assert _codes(refused) == ["APP_PROOF_OUTPUT_EXISTS"]
    assert "--force" in refused["errors"][0]["fix"]
    assert prove_app(app_path=app, out_dir=out, cwd=tmp_path, force=True)["ok"] is True


def test_both_proof_kinds_report_a_refusal_into_an_empty_directory(tmp_path) -> None:
    # tests/test_prove.py::test_prove_existing_output_requires_force pins this for an IntentBundle: an
    # empty directory is replaceable before the run writes anything. One rule decides it for both kinds.
    app = _write_app(tmp_path / "viewspec.app.json")
    intent_out, app_out = tmp_path / "intent-proof", tmp_path / "app-proof"
    intent_out.mkdir()
    app_out.mkdir()

    assert _codes(prove(out_dir=intent_out, cwd=tmp_path)) == ["PROVE_OUTPUT_EXISTS"]
    assert _codes(prove_app(app_path=app, out_dir=app_out, cwd=tmp_path)) == ["APP_PROOF_OUTPUT_EXISTS"]

    assert set(_names(intent_out)) == PROOF_FILES
    assert set(_names(app_out)) == APP_PROOF_FILES
