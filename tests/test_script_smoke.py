"""Smoke coverage for operator scripts that previously had no tests.

Each script is imported as a module and exercised through its ``--help`` entry
point and its cheap, pure helpers. The install, network, and browser paths of
these scripts are never run here.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from scripts import check_studio_release, run_refinement_corrections, run_verification_corpus
from viewspec import __version__
from viewspec.conformance import ConformanceCorpus, load_conformance_corpus
from viewspec.intent_patch import INTENT_PATCH_CONTRACT_PROFILE, source_sha256

ROOT = Path(__file__).resolve().parents[1]
CORPUS_MANIFEST = ROOT / "conformance" / "verification" / "corpus.json"
CORRECTIONS = ROOT / "conformance" / "refinement" / "corrections.json"
SCRIPT_OPTIONS = {
    "check_studio_release.py": ("--dist", "--published", "--out"),
    "run_refinement_corrections.py": ("--corpus", "--corrections", "--output", "--install"),
    "run_verification_corpus.py": ("--manifest", "--output", "--evidence-out", "--install"),
}


def _run_script(name: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(entry for entry in (str(ROOT / "src"), env.get("PYTHONPATH")) if entry)
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / name), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


@pytest.mark.parametrize(("script", "options"), sorted(SCRIPT_OPTIONS.items()))
def test_script_help_exits_cleanly_and_documents_its_options(script, options):
    result = _run_script(script, "--help")

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout.startswith("usage:")
    for option in options:
        assert option in result.stdout, option


def test_release_check_requires_a_mode_before_doing_any_work():
    result = _run_script("check_studio_release.py")

    assert result.returncode == 2
    assert result.stdout == ""
    assert "one of the arguments --dist --published is required" in result.stderr


# --- scripts/check_studio_release.py -------------------------------------------------------------


def test_homepage_contract_binds_the_public_facts_to_the_package_version():
    facts = json.loads((ROOT / "demos" / "public-facts.json").read_text(encoding="utf-8"))
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    version, commands = check_studio_release.homepage_contract()

    assert "version" in project["project"]["dynamic"]
    assert version == facts["sdk_version"] == __version__
    assert commands == {
        "install": facts["installation"]["current_command"],
        "creation": facts["studio"]["creation_command"],
        "compare": facts["studio"]["compare_command"],
    }
    assert commands["install"].split() == ["python", "-m", "pip", "install", "--pre", "viewspec"]
    assert commands["creation"].split()[:2] == ["viewspec", "studio"]
    assert commands["compare"].split()[:2] == ["viewspec", "studio"]


def test_homepage_contract_rejects_a_homepage_that_advertises_another_version(tmp_path):
    (tmp_path / "demos").mkdir()
    shutil.copyfile(ROOT / "demos" / "public-facts.json", tmp_path / "demos" / "public-facts.json")
    shutil.copyfile(ROOT / "demos" / "index.html", tmp_path / "demos" / "index.html")
    (tmp_path / "src" / "viewspec").mkdir(parents=True)
    (tmp_path / "src" / "viewspec" / "_version.py").write_text('__version__ = "0.0.0"\n', encoding="utf-8")

    with pytest.raises(check_studio_release.ReleaseCheckError, match="versions differ"):
        check_studio_release.homepage_contract(tmp_path)


def test_homepage_command_parser_reads_the_copy_text_and_each_release_command_once():
    parser = check_studio_release.HomepageCommands()
    parser.feed(
        '<button id="copyCmd" data-copy-text="python -m pip install --pre viewspec">Copy</button>'
        '<code data-release-command="creation">viewspec studio <em>--brief-file</em> brief.md</code>'
        "<code>unrelated snippet</code>"
    )

    assert parser.commands == {
        "install": "python -m pip install --pre viewspec",
        "creation": "viewspec studio --brief-file brief.md",
    }


def test_homepage_command_parser_rejects_duplicate_release_commands():
    parser = check_studio_release.HomepageCommands()

    with pytest.raises(check_studio_release.ReleaseCheckError, match="duplicate homepage release command: compare"):
        parser.feed('<code data-release-command="compare">a</code><code data-release-command="compare">b</code>')


def test_release_environment_isolates_the_candidate_interpreter(tmp_path, monkeypatch):
    monkeypatch.setenv("PIP_INDEX_URL", "https://example.invalid/simple")
    monkeypatch.setenv("VIEWSPEC_REVIEW_STATE_DIR", "/elsewhere/review")
    monkeypatch.setenv("PYTHONPATH", "/elsewhere/src")
    monkeypatch.setenv("VIRTUAL_ENV", "/elsewhere/venv")
    monkeypatch.setenv("RELEASE_SMOKE_KEEP_ME", "1")
    binary_dir = tmp_path / "venv" / "bin"

    env = check_studio_release._environment(tmp_path, binary_dir)

    own_settings = {
        "PIP_CONFIG_FILE",
        "PIP_DISABLE_PIP_VERSION_CHECK",
        "PIP_NO_CACHE_DIR",
        "VIEWSPEC_REVIEW_STATE_DIR",
        "VIEWSPEC_CONVERGE_STATE_DIR",
    }
    inherited = {key for key in env if key.startswith(("PIP_", "VIEWSPEC_"))} - own_settings
    assert inherited == set()
    assert "PIP_INDEX_URL" not in env
    assert "PYTHONPATH" not in env
    assert "VIRTUAL_ENV" not in env
    assert env["RELEASE_SMOKE_KEEP_ME"] == "1"
    assert env["PATH"].split(os.pathsep)[0] == str(binary_dir)
    assert env["PIP_CONFIG_FILE"] == os.devnull
    assert env["VIEWSPEC_REVIEW_STATE_DIR"] == str(tmp_path / "review-state")
    assert env["VIEWSPEC_CONVERGE_STATE_DIR"] == str(tmp_path / "converge-state")
    assert env["BROWSER"] == f"{binary_dir / 'python'} {tmp_path / 'capture_browser.py'} %s"
    assert (tmp_path / "capture_browser.py").is_file()
    assert env["RELEASE_BROWSER_SCRIPT"] == str(ROOT / "scripts" / "check_studio_release_browser.mjs")
    assert env["RELEASE_BROWSER_URL"] == str(tmp_path / "browser-url.txt")


def test_browser_url_accepts_only_a_captured_loopback_launch(tmp_path):
    capture = tmp_path / "browser-url.txt"
    env = {"RELEASE_BROWSER_URL": str(capture)}

    with pytest.raises(check_studio_release.ReleaseCheckError, match="did not request a browser"):
        check_studio_release._browser_url(env)

    capture.write_text("https://example.invalid/open/room", encoding="utf-8")
    with pytest.raises(check_studio_release.ReleaseCheckError, match="loopback-only"):
        check_studio_release._browser_url(env)
    assert not capture.exists()

    capture.write_text("http://127.0.0.1:4388/open/room", encoding="utf-8")
    assert check_studio_release._browser_url(env) == "http://127.0.0.1:4388/open/room"
    assert not capture.exists()


# --- scripts/run_refinement_corrections.py -------------------------------------------------------


def _corrections() -> list[dict[str, object]]:
    return json.loads(CORRECTIONS.read_text(encoding="utf-8"))["corrections"]


def test_refinement_correction_patches_bind_each_fixed_case_to_its_source_hash():
    corpus = load_conformance_corpus(CORPUS_MANIFEST)
    cases = {case.id: case for case in corpus.cases}
    corrections = _corrections()

    assert [correction["case_id"] for correction in corrections] == sorted(cases)
    for correction in corrections:
        source_text = cases[str(correction["case_id"])].source_path.read_text(encoding="utf-8")

        patch = run_refinement_corrections._patch_payload(source_text, correction)

        assert patch["schema_version"] == 1
        assert patch["contract_profile"] == INTENT_PATCH_CONTRACT_PROFILE
        assert patch["source_kind"] == correction["source_kind"]
        assert patch["base_source_sha256"] == source_sha256(source_text)
        assert patch["evidence_refs"] == [f"scorecard:{correction['case_id']}"]
        (operation,) = patch["operations"]
        assert operation["op"] == "replace_semantic_attr"
        assert operation["node_id"] == correction["node_id"]
        assert operation["attr"] == correction["attr"]
        assert operation["old_value"] == correction["old_value"]
        assert operation["value"] == correction["value"]
        if correction["source_kind"] == "app_bundle":
            assert operation["screen_id"] == correction["screen_id"]
        else:
            assert "screen_id" not in operation


def test_refinement_corrections_run_rejects_manifests_that_do_not_cover_the_corpus(tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("a malformed correction manifest must be rejected before any preview runs")

    monkeypatch.setattr(run_refinement_corrections, "preview_intent_patch", forbidden)
    monkeypatch.setattr(run_refinement_corrections, "apply_intent_patch_file", forbidden)
    not_an_array = tmp_path / "not-an-array.json"
    not_an_array.write_text(json.dumps({"corrections": {}}), encoding="utf-8")
    incomplete = tmp_path / "incomplete.json"
    incomplete.write_text(json.dumps({"corrections": _corrections()[:-1]}), encoding="utf-8")
    reordered = tmp_path / "reordered.json"
    reordered.write_text(json.dumps({"corrections": list(reversed(_corrections()))}), encoding="utf-8")

    with pytest.raises(ValueError, match="must be an array"):
        run_refinement_corrections.run(corpus_path=CORPUS_MANIFEST, corrections_path=not_an_array, install=False)
    for manifest in (incomplete, reordered):
        with pytest.raises(ValueError, match="exactly once in canonical order"):
            run_refinement_corrections.run(corpus_path=CORPUS_MANIFEST, corrections_path=manifest, install=False)


@pytest.mark.parametrize(("ok", "exit_code"), [(True, 0), (False, 1)])
def test_refinement_corrections_main_writes_its_report_and_exits_on_the_verdict(
    tmp_path, monkeypatch, capsys, ok, exit_code
):
    seen: dict[str, object] = {}
    report = {"schema_version": 1, "ok": ok, "case_count": 0, "cases": []}

    def fake_run(**kwargs):
        seen.update(kwargs)
        return report

    monkeypatch.setattr(run_refinement_corrections, "run", fake_run)
    output = tmp_path / "nested" / "correction-proof-report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_refinement_corrections.py",
            "--corpus",
            str(CORPUS_MANIFEST),
            "--corrections",
            str(CORRECTIONS),
            "--output",
            str(output),
        ],
    )

    assert run_refinement_corrections.main() == exit_code
    assert seen == {"corpus_path": str(CORPUS_MANIFEST), "corrections_path": str(CORRECTIONS), "install": False}
    assert output.read_text(encoding="utf-8") == json.dumps(report, indent=2, sort_keys=True) + "\n"
    assert json.loads(capsys.readouterr().out) == report


# --- scripts/run_verification_corpus.py ----------------------------------------------------------


@pytest.mark.parametrize(("ok", "exit_code"), [(True, 0), (False, 1)])
def test_verification_corpus_main_loads_the_manifest_and_delegates_to_the_runner(
    tmp_path, monkeypatch, capsys, ok, exit_code
):
    seen: dict[str, object] = {}
    report = {"ok": ok, "case_count": 0, "cases": []}

    def fake_run_conformance_corpus(corpus, *, install, evidence_out):
        seen.update(corpus=corpus, install=install, evidence_out=evidence_out)
        return report

    monkeypatch.setattr(run_verification_corpus, "run_conformance_corpus", fake_run_conformance_corpus)
    output = tmp_path / "nested" / "verification-corpus-report.json"
    evidence = tmp_path / "evidence"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_verification_corpus.py",
            "--manifest",
            str(CORPUS_MANIFEST),
            "--output",
            str(output),
            "--evidence-out",
            str(evidence),
            "--install",
        ],
    )

    assert run_verification_corpus.main() == exit_code
    manifest_case_ids = {case["id"] for case in json.loads(CORPUS_MANIFEST.read_text(encoding="utf-8"))["cases"]}
    assert isinstance(seen["corpus"], ConformanceCorpus)
    assert {case.id for case in seen["corpus"].cases} == manifest_case_ids
    assert seen["install"] is True
    assert seen["evidence_out"] == str(evidence)
    assert output.read_text(encoding="utf-8") == json.dumps(report, indent=2, sort_keys=True) + "\n"
    assert json.loads(capsys.readouterr().out) == report
