from __future__ import annotations

import hashlib
import json
from pathlib import Path

from hypothesis import given, strategies as st
import pytest

from viewspec.conformance import ConformanceCorpus, load_conformance_corpus, run_conformance_corpus
from viewspec.verification import EvidenceFile, RetryLineage, VerificationPlan, VerificationResult


def _case(case_id: str, source: str = "demos/example.json") -> dict:
    return {
        "id": case_id,
        "kind": "intent",
        "source": source,
        "expected_status": "conformant",
        "required_evidence_roles": ["accessibility", "dom", "log", "screenshot"],
    }


def test_public_verification_corpus_has_unique_executable_semantic_cases():
    corpus = load_conformance_corpus("conformance/verification/corpus.json")

    assert [case.id for case in corpus.cases] == [
        "app-detail",
        "app-queue",
        "collection-states",
        "data-dense-dashboard",
        "dense-operational-console",
        "interactive-form",
        "landing-intent",
        "multi-step-workflow",
        "outcome-states",
        "settings",
    ]
    assert len(corpus.cases) == 10
    assert all(case.source_path.is_file() for case in corpus.cases)
    assert all(case.expected_status == "conformant" for case in corpus.cases)
    assert {case.screen_id for case in corpus.cases if case.kind == "app_screen"} == {
        "detail",
        "queue",
    }


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_core_refinement_scorecard_is_bound_to_the_fixed_corpus(version):
    corpus = load_conformance_corpus("conformance/verification/corpus.json")
    scorecard = json.loads(
        Path(f"conformance/refinement/scorecard-{version}.json").read_text(encoding="utf-8")
    )

    assert scorecard["schema_version"] == 1
    assert scorecard["verification"]["case_count"] == 10
    assert scorecard["verification"]["conformant_count"] == 10
    assert scorecard["verification"]["report_sha256"]
    assert [case["id"] for case in scorecard["cases"]] == [case.id for case in corpus.cases]
    for case in scorecard["cases"]:
        assert set(case["scores"]) == set(scorecard["rubric"]["dimensions"])
        assert all(1 <= score <= 5 for score in case["scores"].values())
        assert case["source_sha256"]
        assert case["intent_sha256"]
        assert case["artifact_sha256"]
        assert case["desktop_screenshot_sha256"]
        if case["pass"]:
            assert min(case["scores"].values()) >= scorecard["rubric"]["brief_pass"]["minimum_dimension"]
            assert case["mean"] >= scorecard["rubric"]["brief_pass"]["minimum_mean"]
            assert case["critical_issues"] == []


def test_passing_refinement_scorecard_binds_checked_in_canonical_screenshots():
    corpus = load_conformance_corpus("conformance/verification/corpus.json")
    corpus_cases = {case.id: case for case in corpus.cases}
    scorecard = json.loads(
        Path("conformance/refinement/scorecard-v2.json").read_text(encoding="utf-8")
    )
    report_path = Path(scorecard["verification"]["report_path"])
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert scorecard["summary"]["corpus_pass"] is True
    assert scorecard["summary"]["first_compile_pass_count"] == 10
    assert all(case["pass"] for case in scorecard["cases"])
    assert hashlib.sha256(report_path.read_bytes()).hexdigest() == scorecard["verification"]["report_sha256"]
    assert report["ok"] is True
    assert report["case_count"] == 10
    for case, reported_case in zip(scorecard["cases"], report["cases"], strict=True):
        corpus_case = corpus_cases[case["id"]]
        assert reported_case["id"] == case["id"]
        assert reported_case["actual_status"] == "conformant"
        assert reported_case["source_sha256"] == case["source_sha256"]
        assert reported_case["intent_sha256"] == case["intent_sha256"]
        assert reported_case["artifact_sha256"] == case["artifact_sha256"]
        assert reported_case["verification_id"] == case["verification_id"]
        assert set(reported_case["evidence_roles"]) == {
            "accessibility",
            "dom",
            "log",
            "screenshot",
        }
        assert hashlib.sha256(corpus_case.source_path.read_bytes()).hexdigest() == reported_case["source_sha256"]
        if corpus_case.kind == "intent":
            expected_intent = corpus_case.source_path.read_bytes()
        else:
            app = json.loads(corpus_case.source_path.read_text(encoding="utf-8"))
            screen = next(item for item in app["screens"] if item["id"] == corpus_case.screen_id)
            expected_intent = (
                json.dumps(screen["intent_bundle"], indent=2, sort_keys=True) + "\n"
            ).encode()
        assert hashlib.sha256(expected_intent).hexdigest() == reported_case["intent_sha256"]
        screenshot_dir = Path(case["screenshot_directory"])
        assert {path.name for path in screenshot_dir.glob("*.png")} == {
            "desktop.png",
            "mobile.png",
            "tablet.png",
        }
        desktop = screenshot_dir / "desktop.png"
        assert hashlib.sha256(desktop.read_bytes()).hexdigest() == case["desktop_screenshot_sha256"]
        reported_screenshots = {
            Path(item["path"]).name: item
            for item in reported_case["evidence"]
            if item["role"] == "screenshot"
        }
        assert set(reported_screenshots) == {"desktop.png", "mobile.png", "tablet.png"}
        for name, evidence in reported_screenshots.items():
            assert hashlib.sha256(screenshot_dir.joinpath(name).read_bytes()).hexdigest() == evidence["sha256"]
        reported_desktop = reported_screenshots["desktop.png"]
        assert reported_desktop["sha256"] == case["desktop_screenshot_sha256"]


def test_core_refinement_exit_record_binds_all_eight_gates_to_current_evidence():
    status_path = Path("conformance/refinement/gate-status-v1.json")
    status = json.loads(status_path.read_text(encoding="utf-8"))
    browser = status["same_revision_evidence"]["browser_corpus"]

    assert status["schema_version"] == 1
    assert status["status"] == "passed"
    assert [gate["id"] for gate in status["gates"]] == list(range(1, 9))
    assert all(gate["status"] == "passed" for gate in status["gates"])
    for gate in status["gates"]:
        assert gate["evidence"]
        assert all(Path(path).exists() for path in gate["evidence"])

    report_path = Path(browser["path"])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert hashlib.sha256(report_path.read_bytes()).hexdigest() == browser["sha256"]
    assert report["case_count"] == browser["case_count"] == 10
    assert sum(case["actual_status"] == "conformant" for case in report["cases"]) == browser["conformant_count"] == 10


@given(st.permutations(("charlie", "alpha", "bravo")))
def test_corpus_case_order_is_canonical(case_ids):
    corpus = ConformanceCorpus.from_json(
        {"schema_version": 1, "cases": [_case(case_id) for case_id in case_ids]},
        root=".",
        require_sources=False,
    )

    assert [case.id for case in corpus.cases] == ["alpha", "bravo", "charlie"]


@pytest.mark.parametrize(
    "source",
    ["../secret.json", "/tmp/secret.json", "demos/../secret.json", "demos\\secret.json"],
)
def test_corpus_rejects_unsafe_source_paths(source):
    with pytest.raises(ValueError, match="source"):
        ConformanceCorpus.from_json(
            {"schema_version": 1, "cases": [_case("unsafe", source)]},
            root=".",
            require_sources=False,
        )


def test_corpus_rejects_ambiguous_app_screen_contract():
    payload = _case("ambiguous")
    payload["kind"] = "app_screen"

    with pytest.raises(ValueError, match="screen_id"):
        ConformanceCorpus.from_json(
            {"schema_version": 1, "cases": [payload]},
            root=".",
            require_sources=False,
        )


def test_corpus_manifest_round_trips_canonically():
    corpus = load_conformance_corpus("conformance/verification/corpus.json")

    reparsed = ConformanceCorpus.from_json(
        json.loads(json.dumps(corpus.to_json())),
        root=corpus.root,
    )

    assert reparsed == corpus


def test_corpus_report_binds_retained_evidence_to_source_intent_and_artifact(tmp_path, monkeypatch):
    source = tmp_path / "demos" / "example.json"
    source.parent.mkdir()
    source.write_text('{"fixture":"source"}\n', encoding="utf-8")
    corpus = ConformanceCorpus.from_json(
        {"schema_version": 1, "cases": [_case("example")]},
        root=tmp_path,
    )
    artifact_sha256 = "a" * 64

    monkeypatch.setattr(
        "viewspec.conformance.compile_intent_bundle_file_tool",
        lambda *_args, **_kwargs: {"ok": True},
    )

    def verify(_artifact_dir, *, plan, evidence_dir, install, lineage):
        assert install is False
        assert lineage == RetryLineage.root()
        evidence_path = evidence_dir
        evidence_path.mkdir()
        evidence = []
        for role, name in (
            ("accessibility", "mobile.a11y.json"),
            ("dom", "mobile.dom.json"),
            ("log", "host-report.json"),
            ("screenshot", "mobile.png"),
        ):
            content = f"{role}-evidence".encode()
            (evidence_path / name).write_bytes(content)
            evidence.append(EvidenceFile.from_content(f"evidence/{name}", role, content))
        return VerificationResult.create(
            artifact_sha256=artifact_sha256,
            plan=plan,
            complete=True,
            diagnostics=(),
            evidence=evidence,
            lineage=lineage,
        )

    monkeypatch.setattr("viewspec.conformance.verify_local_artifact", verify)
    evidence_out = tmp_path / "retained"

    report = run_conformance_corpus(corpus, evidence_out=evidence_out)

    case = report["cases"][0]
    expected_source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    assert report["ok"] is True
    assert report["evidence_root"] == str(evidence_out)
    assert case["source_sha256"] == expected_source_sha256
    assert case["intent_sha256"] == expected_source_sha256
    assert case["artifact_sha256"] == artifact_sha256
    assert case["plan_sha256"] == VerificationPlan.default().plan_sha256
    assert case["evidence_dir"] == "example/evidence"
    assert len(case["evidence"]) == 4
    assert {item["role"] for item in case["evidence"]} == {
        "accessibility",
        "dom",
        "log",
        "screenshot",
    }


def test_corpus_rejects_nonempty_retained_evidence_directory(tmp_path):
    evidence_out = tmp_path / "retained"
    evidence_out.mkdir()
    (evidence_out / "existing.txt").write_text("keep", encoding="utf-8")
    corpus = ConformanceCorpus.from_json(
        {"schema_version": 1, "cases": [_case("example")]},
        root=tmp_path,
        require_sources=False,
    )

    with pytest.raises(FileExistsError, match="empty directory"):
        run_conformance_corpus(corpus, evidence_out=evidence_out)


def test_corpus_compile_failure_reports_tool_error_codes(tmp_path, monkeypatch):
    source = tmp_path / "demos" / "example.json"
    source.parent.mkdir()
    source.write_text('{"fixture":"source"}\n', encoding="utf-8")
    corpus = ConformanceCorpus.from_json(
        {"schema_version": 1, "cases": [_case("example")]},
        root=tmp_path,
    )
    monkeypatch.setattr(
        "viewspec.conformance.compile_intent_bundle_file_tool",
        lambda *_args, **_kwargs: {
            "ok": False,
            "errors": [
                {
                    "code": "UNKNOWN_ACTION_TARGET",
                    "message": "The action target does not resolve.",
                    "fix": "Point the action at a declared semantic target.",
                }
            ],
        },
    )

    report = run_conformance_corpus(corpus)

    case = report["cases"][0]
    assert report["ok"] is False
    assert case["actual_status"] == "compile_failed"
    assert case["diagnostic_codes"] == ["UNKNOWN_ACTION_TARGET"]
    assert case["compile_errors"][0]["fix"] == "Point the action at a declared semantic target."
    assert case["artifact_sha256"] is None
    assert case["evidence"] == []
