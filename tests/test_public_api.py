"""The public verbs dispatch on what they are given: bundle kind, source file, or artifact directory."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import viewspec
from viewspec import AppBundle, AppBundleError, IntentBundle, check, compile, prove
from viewspec import api as api_module
from viewspec.app_bundle import starter_app_bundle
from viewspec.cli import main as cli_main
from viewspec.intent_tools import starter_intent_payload
from viewspec.types import ASTBundle


@pytest.fixture
def intent_path(tmp_path: Path) -> Path:
    path = tmp_path / "viewspec.intent.json"
    path.write_text(json.dumps(starter_intent_payload("dashboard")), encoding="utf-8")
    return path


@pytest.fixture
def app_path(tmp_path: Path) -> Path:
    path = tmp_path / "viewspec.app.json"
    path.write_text(json.dumps(starter_app_bundle("internal_tool")), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("name", "payload", "expected"),
    [
        ("viewspec.intent.json", {"substrate": {}, "view_spec": {}}, "intent_bundle"),
        ("viewspec.app.json", {"app": {}, "routes": [], "screens": []}, "app_bundle"),
        ("source.json", {"app": {}, "routes": [], "screens": []}, "app_bundle"),
        ("source.json", {"screens": []}, "app_bundle"),
        ("source.json", {"unrelated": True}, "intent_bundle"),
        ("source.json", [], "intent_bundle"),
    ],
)
def test_source_kind_reads_the_root_keys(tmp_path, name, payload, expected):
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert api_module.source_kind(path) == expected


@pytest.mark.parametrize(("name", "expected"), [("x.app.json", "app_bundle"), ("x.json", "intent_bundle")])
def test_source_kind_falls_back_to_the_name_for_unreadable_json(tmp_path, name, expected):
    assert api_module.source_kind(tmp_path / name) == expected
    (tmp_path / name).write_text("{", encoding="utf-8")
    assert api_module.source_kind(tmp_path / name) == expected


def test_app_bundle_loads_validates_and_round_trips(app_path):
    bundle = AppBundle.load(app_path)
    assert bundle.to_json() == json.loads(app_path.read_text(encoding="utf-8"))
    assert AppBundle.from_json(bundle.to_json()) == bundle


def test_app_bundle_rejects_an_invalid_document():
    with pytest.raises(AppBundleError) as info:
        AppBundle.from_json({"app": {}})
    assert info.value.issues
    assert str(info.value).startswith(info.value.issues[0]["code"])


def test_compile_keeps_intent_bundles_in_memory(intent_path):
    bundle = IntentBundle.from_json(json.loads(intent_path.read_text(encoding="utf-8")))
    assert isinstance(compile(bundle), ASTBundle)


def test_compile_writes_an_app_bundle_to_its_output_directory(app_path, tmp_path):
    report = compile(AppBundle.load(app_path), out_dir=tmp_path / "dist")
    assert report["ok"] is True
    assert (tmp_path / "dist" / "index.html").is_file()


def test_compile_requires_an_output_directory_for_an_app_bundle(app_path):
    with pytest.raises(ValueError, match="out_dir"):
        compile(AppBundle.load(app_path))


def test_check_routes_each_source_kind_to_its_validator(intent_path, app_path):
    assert check(intent_path)["ok"] is True
    assert check(app_path)["summary"]["route_count"] == 2


def test_check_reports_a_missing_file_instead_of_raising(tmp_path):
    assert check(tmp_path / "gone.json")["issues"][0]["code"] == "INTENT_FILE_NOT_FOUND"
    assert check(tmp_path / "gone.app.json")["issues"][0]["code"] == "APP_FILE_NOT_FOUND"


def test_check_reads_a_compiled_intent_directory_and_catches_tampering(intent_path, tmp_path):
    out = tmp_path / "dist"
    assert cli_main(["compile", str(intent_path), "--out", str(out)]) == 0
    assert check(out)["ok"] is True

    (out / "index.html").write_text("<h1>Tampered</h1>", encoding="utf-8")
    assert check(out)["ok"] is False


@pytest.mark.parametrize("target", ["html-tailwind-app", "react-tailwind-app"])
def test_check_reads_a_compiled_app_directory_screen_by_screen(app_path, tmp_path, target):
    out = tmp_path / "dist"
    assert cli_main(["compile", str(app_path), "--out", str(out), "--target", target]) == 0

    report = check(out)
    assert report["ok"] is True, report["errors"]
    assert sorted(report["screens"]) == ["detail", "queue"]

    artifact = out / "screens" / "queue" / "artifact" / "index.html"
    artifact.write_text("<h1>Tampered</h1>", encoding="utf-8")
    report = check(out)
    assert report["ok"] is False
    assert all(error.startswith("queue: ") for error in report["errors"])


def test_check_rejects_a_directory_with_no_compiled_screens(tmp_path):
    (tmp_path / "dist" / "screens").mkdir(parents=True)
    report = check(tmp_path / "dist")
    assert report["ok"] is False
    assert report["errors"] == ["no compiled screens under screens/*/artifact"]


@pytest.mark.parametrize(
    ("target", "edited"),
    [
        ("html-tailwind-app", "index.html"),
        ("react-tailwind-app", "src/ViewSpecApp.tsx"),
        ("react-tailwind-app", "src/screens/queue/ViewSpecView.tsx"),
    ],
)
def test_check_catches_tampering_above_the_screens(app_path, tmp_path, target, edited):
    out = tmp_path / "dist"
    assert cli_main(["compile", str(app_path), "--out", str(out), "--target", target]) == 0

    (out / edited).write_text("<script>alert(1)</script>", encoding="utf-8")

    report = check(out)
    assert report["ok"] is False
    assert report["errors"] == [f"{edited}: does not match the hash in its manifest"]


def test_check_fails_an_app_output_that_lost_its_manifest(app_path, tmp_path):
    out = tmp_path / "dist"
    assert cli_main(["compile", str(app_path), "--out", str(out)]) == 0
    (out / "shell_manifest.json").unlink()

    assert check(out)["errors"] == ["missing shell_manifest.json or viewspec_app_manifest.json"]


def test_check_passes_an_app_proof_directory(app_path, tmp_path):
    out = tmp_path / "proof"
    assert prove(app_path, out_dir=out)["ok"] is True

    assert check(out)["ok"] is True


@pytest.mark.parametrize("name", ["deep.json", "deep.app.json"])
def test_check_reports_json_too_deep_to_parse(tmp_path, name):
    path = tmp_path / name
    path.write_text("[" * 200_000, encoding="utf-8")

    assert check(path)["ok"] is False


def test_prove_resolves_relative_paths_against_cwd(intent_path, tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    assert prove("viewspec.intent.json", cwd=tmp_path)["ok"] is True

    assert (tmp_path / ".viewspec-proof" / "PROOF.md").is_file()
    assert list(elsewhere.iterdir()) == []


@pytest.mark.parametrize("option", [{"out_dir": "dist"}, {"target": "react-tsx"}, {"force": True}])
def test_compile_refuses_file_options_for_an_intent_bundle(intent_path, option):
    bundle = IntentBundle.from_json(json.loads(intent_path.read_text(encoding="utf-8")))

    with pytest.raises(ValueError, match="in memory"):
        compile(bundle, **option)


def test_an_app_bundle_is_an_immutable_value(app_path):
    bundle = AppBundle.load(app_path)

    assert bundle == AppBundle.from_json(bundle.to_json())
    assert len({bundle, AppBundle.load(app_path)}) == 1

    edited = bundle.to_json()
    edited["app"]["title"] = "Edited"
    assert bundle.to_json()["app"]["title"] != "Edited"


def test_prove_dispatches_on_the_source(intent_path, app_path, tmp_path, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(api_module, "prove_intent", lambda **_: calls.append("intent") or {})
    monkeypatch.setattr(api_module, "prove_app", lambda **_: calls.append("app") or {})

    class Result:
        def to_json(self):
            calls.append("artifact")
            return {}

    monkeypatch.setattr(api_module, "verify_local_artifact", lambda *_, **__: Result())

    prove()
    prove(intent_path)
    prove(app_path)
    prove(tmp_path)

    assert calls == ["intent", "intent", "app", "artifact"]


def test_prove_force_clears_only_evidence_viewspec_wrote(tmp_path, monkeypatch):
    calls: list[Path] = []

    class Result:
        def to_json(self):
            return {}

    def fake_verify(_source, *, evidence_dir, **_kwargs):
        calls.append(evidence_dir)
        return Result()

    monkeypatch.setattr(api_module, "verify_local_artifact", fake_verify)
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    ours = tmp_path / "ours" / "evidence"
    ours.mkdir(parents=True)
    (ours / "host-report.json").write_text("{}", encoding="utf-8")
    theirs = tmp_path / "theirs" / "evidence"
    theirs.mkdir(parents=True)
    (theirs / "keep.txt").write_text("keep", encoding="utf-8")

    prove(artifact, out_dir=tmp_path / "ours", force=True)
    prove(artifact, out_dir=tmp_path / "theirs", force=True)

    assert not ours.exists()
    assert (theirs / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert calls == [ours, theirs]


def test_prove_writes_real_proofs_for_both_bundle_kinds(intent_path, app_path, tmp_path):
    assert prove(intent_path, out_dir=tmp_path / "intent-proof")["ok"] is True
    assert prove(app_path, out_dir=tmp_path / "app-proof")["ok"] is True


def test_top_level_verbs_are_the_dispatching_ones():
    assert viewspec.compile is api_module.compile
    assert viewspec.check is api_module.check
    assert viewspec.prove is api_module.prove


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["compile", "{intent}", "--out", "{tmp}/x", "--target", "react-tailwind-app"], "requires an AppBundle"),
        (["compile", "{app}", "--out", "{tmp}/x", "--target", "react-tsx"], "requires an IntentBundle"),
        (["prove", "{intent}", "--freerange"], "require an AppBundle"),
        (["prove", "{intent}", "--plan", "plan.json"], "requires a compiled artifact directory"),
    ],
)
def test_cli_rejects_options_that_do_not_fit_the_source(intent_path, app_path, tmp_path, capsys, argv, message):
    places = {"intent": str(intent_path), "app": str(app_path), "tmp": str(tmp_path)}
    assert cli_main([part.format(**places) for part in argv]) == 2
    assert message in capsys.readouterr().err


def test_cli_init_writes_each_starter_to_its_default_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli_main(["init"]) == 0
    assert cli_main(["init", "app"]) == 0
    assert cli_main(["init", "design"]) == 0
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "DESIGN.md",
        "viewspec.app.json",
        "viewspec.intent.json",
    ]


def test_cli_init_rejects_a_kind_from_the_other_bundle(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli_main(["init", "--kind", "internal_tool"]) == 2
    assert cli_main(["init", "app", "--kind", "dashboard"]) == 2
    capsys.readouterr()


def test_cli_exposes_exactly_the_five_commands(capsys):
    with pytest.raises(SystemExit):
        cli_main(["--help"])
    assert "{init,compile,check,prove,studio,mcp}" in capsys.readouterr().out
