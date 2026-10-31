from __future__ import annotations

import hashlib
import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

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
