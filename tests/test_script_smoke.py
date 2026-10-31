"""Smoke coverage for operator scripts that previously had no tests.

Each script is imported as a module and exercised through its ``--help`` entry
point and its cheap, pure helpers. The install, network, and browser paths of
these scripts are never run here.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import run_verification_corpus
from viewspec.conformance import ConformanceCorpus

ROOT = Path(__file__).resolve().parents[1]
CORPUS_MANIFEST = ROOT / "conformance" / "verification" / "corpus.json"
SCRIPT_OPTIONS = {
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
