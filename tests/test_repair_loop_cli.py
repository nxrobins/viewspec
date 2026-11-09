"""The repair loop on the CLI: lossless fixes, --no-fix, --fixed-out, --patch and --patched-out.

check and compile apply lossless fixes before validation and report them under ``fixes`` in JSON and
human output. --patch applies an RFC 6902 document to an IntentBundle source in memory; the file on
disk never changes. A patch may target only an IntentBundle source file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from viewspec.cli import main as cli_main
from viewspec.error_codes import ERROR_CODES
from viewspec.intent_tools import starter_intent_payload
from viewspec.local_tools import file_hash, source_hash

ROOT_TITLE_POINTER = "/substrate/nodes/starter_dashboard/attrs/title"


def _run(capsys, argv: list[str]) -> tuple[int, str, str]:
    exit_code = cli_main(argv)
    captured = capsys.readouterr()
    return exit_code, captured.out, captured.err


def _run_json(capsys, argv: list[str]) -> tuple[int, dict, str]:
    exit_code, out, err = _run(capsys, [*argv, "--json"])
    return exit_code, json.loads(out), err


def _write(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


@pytest.fixture
def starter(tmp_path) -> Path:
    return _write(tmp_path / "viewspec.intent.json", starter_intent_payload("dashboard"))


@pytest.fixture
def fixable(tmp_path) -> Path:
    """A starter without schema_version and without the root node's slots map: valid after two fixes."""
    payload = starter_intent_payload("dashboard")
    del payload["schema_version"]
    del payload["substrate"]["nodes"][payload["substrate"]["root_id"]]["slots"]
    return _write(tmp_path / "fixable.intent.json", payload)


@pytest.fixture
def rename_patch(tmp_path) -> Path:
    return _write(
        tmp_path / "ops.json",
        [
            {"op": "test", "path": ROOT_TITLE_POINTER, "value": "Starter Dashboard"},
            {"op": "replace", "path": ROOT_TITLE_POINTER, "value": "Patched Dashboard"},
        ],
    )


# fixes -----------------------------------------------------------------------------------------


def test_check_json_on_the_starter_keeps_its_keys_and_adds_an_empty_fixes_list(starter, capsys) -> None:
    exit_code, payload, err = _run_json(capsys, ["check", str(starter)])

    assert exit_code == 0
    assert err == ""
    assert payload == {
        "schema_version": 1,
        "ok": True,
        "compile_check": "passed",
        "issues": [],
        "fixes": [],
        "repair_checklist": [],
        "correction_prompt": None,
    }


def test_check_applies_lossless_fixes_and_reports_each_with_its_path(fixable, capsys) -> None:
    exit_code, payload, _err = _run_json(capsys, ["check", str(fixable)])

    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["issues"] == []
    assert [(fix["code"], fix["path"], fix["lossy"]) for fix in payload["fixes"]] == [
        ("FIX_SCHEMA_VERSION_ADDED", "$.schema_version", False),
        ("FIX_EMPTY_RELATIONS_ADDED", "$.substrate.nodes.starter_dashboard.slots", False),
    ]
    assert all(fix["message"] and fix["code"] in ERROR_CODES for fix in payload["fixes"])
    # The source file is not changed by check.
    assert "schema_version" not in json.loads(fixable.read_text(encoding="utf-8"))


def test_check_human_output_lists_the_fixes_after_the_verdict(fixable, capsys) -> None:
    exit_code, out, err = _run(capsys, ["check", str(fixable)])

    assert exit_code == 0
    assert err == ""
    lines = out.splitlines()
    assert lines[0] == "ok: compile_check=passed"
    assert lines[1].startswith("fix: FIX_SCHEMA_VERSION_ADDED at $.schema_version: ")
    assert lines[2].startswith("fix: FIX_EMPTY_RELATIONS_ADDED at $.substrate.nodes.starter_dashboard.slots: ")
    assert len(lines) == 3


def test_no_fix_reports_the_same_problems_as_issues(fixable, capsys) -> None:
    exit_code, payload, _err = _run_json(capsys, ["check", str(fixable), "--no-fix"])

    assert exit_code == 2
    assert payload["ok"] is False
    assert payload["fixes"] == []
    assert [(issue["code"], issue["path"]) for issue in payload["issues"]] == [
        ("MISSING_FIELD", "$.substrate.nodes.starter_dashboard.slots")
    ]
    assert payload["correction_prompt"]


def test_fixed_out_writes_the_fixed_source_which_then_checks_clean(fixable, tmp_path, capsys) -> None:
    fixed = tmp_path / "fixed.intent.json"

    exit_code, payload, _err = _run_json(capsys, ["check", str(fixable), "--fixed-out", str(fixed)])

    assert exit_code == 0
    assert payload["fixed_out"] == str(fixed)
    document = json.loads(fixed.read_text(encoding="utf-8"))
    assert document["schema_version"] == 1
    assert document["substrate"]["nodes"]["starter_dashboard"]["slots"] == {}

    exit_code, payload, _err = _run_json(capsys, ["check", str(fixed)])

    assert exit_code == 0
    assert payload["fixes"] == []
    assert "fixed_out" not in payload

    exit_code, out, _err = _run(capsys, ["check", str(fixable), "--fixed-out", str(fixed)])
    assert exit_code == 0
    assert out.splitlines()[-1] == f"fixed_out: {fixed}"


def test_fixed_out_is_written_for_an_invalid_source_too(tmp_path, capsys) -> None:
    payload = starter_intent_payload("dashboard")
    del payload["schema_version"]
    payload["view_spec"]["bindings"][0]["present_as"] = "chart"
    source = _write(tmp_path / "broken.intent.json", payload)
    fixed = tmp_path / "fixed.json"

    exit_code, report, _err = _run_json(capsys, ["check", str(source), "--fixed-out", str(fixed)])

    assert exit_code == 2
    assert [fix["code"] for fix in report["fixes"]] == ["FIX_SCHEMA_VERSION_ADDED"]
    assert [issue["code"] for issue in report["issues"]] == ["UNKNOWN_PRESENT_AS"]
    assert report["fixed_out"] == str(fixed)
    assert json.loads(fixed.read_text(encoding="utf-8"))["schema_version"] == 1
    assert "applied_fixes" in report["correction_prompt"]


def test_compile_uses_the_fixed_document_and_reports_the_fixes(fixable, tmp_path, capsys) -> None:
    out_dir = tmp_path / "dist"

    exit_code, payload, err = _run_json(capsys, ["compile", str(fixable), "--out", str(out_dir)])

    assert exit_code == 0
    assert err == ""
    assert payload["ok"] is True
    assert [fix["code"] for fix in payload["fixes"]] == ["FIX_SCHEMA_VERSION_ADDED", "FIX_EMPTY_RELATIONS_ADDED"]
    assert set(payload["paths"]) == {"html", "manifest", "diagnostics"}
    assert cli_main(["check", str(out_dir), "--json"]) == 0
    capsys.readouterr()
    # Provenance hashes the source file as given, not the fixed document.
    manifest = json.loads((out_dir / "provenance_manifest.json").read_text(encoding="utf-8"))
    assert manifest["raw_source_hash"] == source_hash(fixable.read_text(encoding="utf-8"))
    assert manifest["command_args"] == ["viewspec", "compile", fixable.name, "--out", "<out>"]


def test_compile_human_mode_keeps_stdout_for_the_paths_and_notes_fixes_on_stderr(fixable, tmp_path, capsys) -> None:
    exit_code, out, err = _run(capsys, ["compile", str(fixable), "--out", str(tmp_path / "dist")])

    assert exit_code == 0
    assert set(json.loads(out)) == {"html", "manifest", "diagnostics"}
    assert [line.split(" at ")[0] for line in err.splitlines()] == [
        "fix: FIX_SCHEMA_VERSION_ADDED",
        "fix: FIX_EMPTY_RELATIONS_ADDED",
    ]


def test_compile_no_fix_fails_a_source_that_needs_a_fix(fixable, tmp_path, capsys) -> None:
    out_dir = tmp_path / "dist"

    exit_code, payload, _err = _run_json(capsys, ["compile", str(fixable), "--out", str(out_dir), "--no-fix"])

    assert exit_code == 2
    assert payload["ok"] is False
    assert payload["fixes"] == []
    assert [error["code"] for error in payload["errors"]] == ["MISSING_FIELD"]
    assert payload["validation"]["fixes"] == []
    assert not out_dir.exists()


def test_lossless_fixes_do_not_change_the_compiled_artifact(fixable, tmp_path, capsys) -> None:
    payload = json.loads(fixable.read_text(encoding="utf-8"))
    payload["schema_version"] = 1
    payload["substrate"]["nodes"]["starter_dashboard"]["slots"] = {}
    complete = _write(tmp_path / "complete.intent.json", payload)

    assert cli_main(["compile", str(fixable), "--out", str(tmp_path / "fixed-dist")]) == 0
    assert cli_main(["compile", str(complete), "--out", str(tmp_path / "complete-dist")]) == 0
    capsys.readouterr()

    assert file_hash(tmp_path / "fixed-dist" / "index.html") == file_hash(tmp_path / "complete-dist" / "index.html")


# --patch ---------------------------------------------------------------------------------------


def test_check_with_a_patch_validates_the_patched_source_and_leaves_the_file_alone(
    starter, rename_patch, tmp_path, capsys
) -> None:
    before = starter.read_bytes()
    patched = tmp_path / "patched.intent.json"

    exit_code, payload, _err = _run_json(
        capsys, ["check", str(starter), "--patch", str(rename_patch), "--patched-out", str(patched)]
    )

    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["fixes"] == []
    assert payload["patched_out"] == str(patched)
    assert starter.read_bytes() == before
    document = json.loads(patched.read_text(encoding="utf-8"))
    assert document["substrate"]["nodes"]["starter_dashboard"]["attrs"]["title"] == "Patched Dashboard"
    # The author's key order is kept: the patched file differs from the source only where patched.
    assert list(document) == list(json.loads(starter.read_text(encoding="utf-8")))


def test_compile_with_a_patch_compiles_the_patched_source_and_records_it(
    starter, rename_patch, tmp_path, capsys
) -> None:
    out_dir = tmp_path / "dist"
    patched = tmp_path / "patched.intent.json"

    exit_code, payload, _err = _run_json(
        capsys,
        ["compile", str(starter), "--patch", str(rename_patch), "--patched-out", str(patched), "--out", str(out_dir)],
    )

    assert exit_code == 0
    assert payload["paths"]["patched_out"] == str(patched)
    assert "Patched Dashboard" in (out_dir / "index.html").read_text(encoding="utf-8")
    manifest = json.loads((out_dir / "provenance_manifest.json").read_text(encoding="utf-8"))
    assert manifest["raw_source_hash"] == source_hash(patched.read_text(encoding="utf-8"))
    assert manifest["command_args"] == ["viewspec", "compile", starter.name, "--patch", "ops.json", "--out", "<out>"]
    assert cli_main(["check", str(out_dir), "--json"]) == 0
    capsys.readouterr()


def test_a_patch_can_fix_a_lossy_problem_the_tools_refuse_to_fix(tmp_path, capsys) -> None:
    payload = starter_intent_payload("dashboard")
    payload["view_spec"]["bindings"][0]["present_as"] = "chart"
    source = _write(tmp_path / "broken.intent.json", payload)
    patch = _write(
        tmp_path / "ops.json", [{"op": "replace", "path": "/view_spec/bindings/0/present_as", "value": "label"}]
    )

    exit_code, report, _err = _run_json(capsys, ["check", str(source)])
    assert exit_code == 2
    assert report["fixes"] == []
    assert [issue["code"] for issue in report["issues"]] == ["UNKNOWN_PRESENT_AS"]

    exit_code, report, _err = _run_json(capsys, ["check", str(source), "--patch", str(patch)])
    assert exit_code == 0
    assert report["ok"] is True


@pytest.mark.parametrize(
    ("operations", "code", "path"),
    [
        ([{"op": "remove", "path": "/view_spec/nope"}], "PATCH_APPLY_FAILED", "patch[0]"),
        ([{"op": "test", "path": ROOT_TITLE_POINTER, "value": "Other"}], "PATCH_TEST_FAILED", "patch[0]"),
        ([{"op": "add", "path": "/x", "value": 1}, {"op": "frob", "path": "/x"}], "PATCH_INVALID", "patch[1]"),
        ({"op": "add", "path": "/x", "value": 1}, "PATCH_INVALID", "patch"),
        ([{"op": "add", "path": "/x", "value": 1}] * 257, "PATCH_TOO_MANY_OPERATIONS", "patch"),
        # The patch file stays under the cap; the patched document does not.
        ([{"op": "add", "path": "/view_spec/blob", "value": "x" * (255 * 1024)}], "PATCH_RESULT_TOO_LARGE", "patch"),
    ],
)
def test_patch_failures_are_issues_with_registered_codes(starter, tmp_path, capsys, operations, code, path) -> None:
    patch = _write(tmp_path / "ops.json", operations)
    patched = tmp_path / "patched.json"

    exit_code, payload, err = _run_json(
        capsys, ["check", str(starter), "--patch", str(patch), "--patched-out", str(patched)]
    )

    assert exit_code == 2
    assert err == ""
    assert payload["ok"] is False
    assert payload["fixes"] == []
    assert [(issue["code"], issue["path"]) for issue in payload["issues"]] == [(code, path)]
    assert code in ERROR_CODES
    assert payload["issues"][0]["suggestion"]
    assert payload["correction_prompt"]
    assert not patched.exists()


def test_a_patch_file_that_is_not_strict_json_is_invalid(starter, tmp_path, capsys) -> None:
    patch = tmp_path / "ops.json"
    patch.write_text('[{"op": "add", "path": "/baz", "value": "qux", "op": "remove"}]', encoding="utf-8")

    exit_code, payload, _err = _run_json(capsys, ["check", str(starter), "--patch", str(patch)])

    assert exit_code == 2
    assert payload["issues"][0]["code"] == "PATCH_INVALID"
    assert "duplicate object key" in payload["issues"][0]["message"]

    exit_code, payload, _err = _run_json(capsys, ["check", str(starter), "--patch", str(tmp_path / "missing.json")])

    assert exit_code == 2
    assert payload["issues"][0]["code"] == "PATCH_INVALID"


def test_compile_with_a_failing_patch_writes_nothing(starter, tmp_path, capsys) -> None:
    patch = _write(tmp_path / "ops.json", [{"op": "remove", "path": "/view_spec/nope"}])
    out_dir = tmp_path / "dist"

    exit_code, payload, _err = _run_json(
        capsys, ["compile", str(starter), "--patch", str(patch), "--out", str(out_dir)]
    )

    assert exit_code == 2
    assert payload["ok"] is False
    assert [error["code"] for error in payload["errors"]] == ["PATCH_APPLY_FAILED"]
    assert payload["validation"]["issues"][0]["path"] == "patch[0]"
    assert not out_dir.exists()

    exit_code, out, err = _run(capsys, ["compile", str(starter), "--patch", str(patch), "--out", str(out_dir)])

    assert exit_code == 2
    assert out == ""
    assert "PATCH_APPLY_FAILED" in err
    assert not out_dir.exists()


def test_a_patch_may_target_only_an_intent_bundle_source_file(starter, rename_patch, tmp_path, capsys) -> None:
    compiled = tmp_path / "dist"
    assert cli_main(["compile", str(starter), "--out", str(compiled)]) == 0
    capsys.readouterr()
    html = tmp_path / "page.html"
    html.write_text("<main><h1>Hello</h1></main>", encoding="utf-8")
    app = tmp_path / "viewspec.app.json"
    assert cli_main(["init", "app", "--out", str(app)]) == 0
    capsys.readouterr()

    for target in (compiled, app):
        exit_code, payload, _err = _run_json(capsys, ["check", str(target), "--patch", str(rename_patch)])
        assert exit_code == 2
        assert [issue["code"] for issue in payload["issues"]] == ["PATCH_TARGET_FORBIDDEN"]
        assert payload["issues"][0]["path"] == "patch"

    for argv in (
        ["compile", str(html), "--patch", str(rename_patch), "--out", str(tmp_path / "o1")],
        ["compile", str(app), "--patch", str(rename_patch), "--out", str(tmp_path / "o2")],
    ):
        exit_code, payload, _err = _run_json(capsys, argv)
        assert exit_code == 2
        assert [error["code"] for error in payload["errors"]] == ["PATCH_TARGET_FORBIDDEN"]
        assert not Path(argv[-1]).exists()

    # The directory check keeps its own report shape in human mode, and the patch is refused.
    exit_code, out, _err = _run(capsys, ["check", str(compiled), "--patch", str(rename_patch)])
    assert exit_code == 2
    assert out.startswith("failed: compile_check=failed\n")
    assert "PATCH_TARGET_FORBIDDEN" in out


def test_patched_out_requires_patch_and_fixed_out_needs_an_intent_source(starter, tmp_path, capsys) -> None:
    app = tmp_path / "viewspec.app.json"
    assert cli_main(["init", "app", "--out", str(app)]) == 0
    capsys.readouterr()

    exit_code, out, err = _run(capsys, ["check", str(starter), "--patched-out", str(tmp_path / "p.json")])
    assert (exit_code, out) == (2, "")
    assert "--patched-out requires --patch" in err

    exit_code, out, err = _run(capsys, ["check", str(app), "--fixed-out", str(tmp_path / "f.json")])
    assert (exit_code, out) == (2, "")
    assert "--fixed-out applies to an IntentBundle source file" in err

    exit_code, out, err = _run(capsys, ["compile", str(app), "--fixed-out", str(tmp_path / "f.json"), "--out", "o"])
    assert (exit_code, out) == (2, "")
    assert "--fixed-out applies to an IntentBundle source file" in err
    assert not (tmp_path / "f.json").exists()


def test_no_fix_is_harmless_on_an_app_bundle_and_a_directory(starter, tmp_path, capsys) -> None:
    app = tmp_path / "viewspec.app.json"
    assert cli_main(["init", "app", "--out", str(app)]) == 0
    compiled = tmp_path / "dist"
    assert cli_main(["compile", str(starter), "--out", str(compiled)]) == 0
    capsys.readouterr()

    assert cli_main(["check", str(app), "--no-fix", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert cli_main(["check", str(compiled), "--no-fix"]) == 0
    assert capsys.readouterr().out.startswith("ok\n")
