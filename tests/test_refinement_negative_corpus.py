from __future__ import annotations

import importlib
import json
from pathlib import Path

from viewspec.host_verify import verify_host_artifact_dir
from viewspec.intent_tools import (
    compile_intent_bundle_file_tool,
    init_intent_file,
    starter_intent_bundle,
    validate_intent_text,
)
from viewspec.local_verify import BrowserEvidence, BrowserPlanOutcome, verify_local_artifact
from viewspec.prove import prove
from viewspec.verification import VerificationDiagnostic


NEGATIVE_CORPUS_PATH = Path("conformance/refinement/negative-corpus.json")


def _corpus() -> dict[str, dict[str, object]]:
    payload = json.loads(NEGATIVE_CORPUS_PATH.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    return {case["id"]: case for case in payload["cases"]}


def _case(case_id: str) -> dict[str, object]:
    return _corpus()[case_id]


def _issue(result: dict[str, object], code: str) -> dict[str, object]:
    return next(issue for issue in result["issues"] if issue["code"] == code)


def _write_react_artifact(tmp_path: Path) -> Path:
    intent_path = init_intent_file(tmp_path / "viewspec.intent.json")
    artifact_dir = tmp_path / "artifact"
    result = compile_intent_bundle_file_tool(
        intent_path,
        artifact_dir,
        target="react-tailwind-tsx",
        cwd=tmp_path,
        allow_outside_cwd=True,
    )
    assert result["ok"] is True
    return artifact_dir


def test_negative_corpus_covers_every_required_failure_class_once():
    corpus = _corpus()

    assert list(corpus) == [
        "invalid-semantic-input",
        "unresolved-target",
        "unsupported-construct",
        "artifact-tampering",
        "browser-environment-failure",
        "visible-conformance-failure",
    ]
    assert {case["kind"] for case in corpus.values()} == {
        "semantic_validation",
        "artifact_preflight",
        "browser_environment",
        "browser_conformance",
    }
    for case in corpus.values():
        assert case["diagnostic_code"]
        assert case["message_contains"]
        assert case["next_action"]
        assert case.get("source_path") or case.get("source_ref") or case.get("evidence_ref")


def test_invalid_semantic_input_fails_with_source_path_and_bounded_retry():
    case = _case("invalid-semantic-input")
    result = validate_intent_text('{"schema_version": 1')
    issue = _issue(result, str(case["diagnostic_code"]))

    assert result["ok"] is False
    assert result["compile_check"] == "failed"
    assert issue["path"] == case["source_path"]
    assert str(case["message_contains"]) in issue["message"]
    assert issue["suggestion"] == case["next_action"]
    assert "viewspec validate-intent viewspec.intent.json --json" in result["correction_prompt"]


def test_unresolved_target_fails_at_exact_semantic_reference():
    case = _case("unresolved-target")
    payload = starter_intent_bundle("dashboard").to_json()
    payload["view_spec"]["actions"].append(
        {
            "id": "open_missing",
            "kind": "navigate",
            "label": "Open missing item",
            "target_region": "main",
            "target_ref": "motif:missing",
            "payload_bindings": [],
        }
    )
    result = validate_intent_text(json.dumps(payload))
    issue = _issue(result, str(case["diagnostic_code"]))

    assert result["ok"] is False
    assert issue["path"] == case["source_path"]
    assert str(case["message_contains"]) in issue["message"]
    assert issue["suggestion"] == case["next_action"]


def test_unsupported_construct_fails_with_supported_semantic_alternatives():
    case = _case("unsupported-construct")
    payload = starter_intent_bundle("dashboard").to_json()
    payload["view_spec"]["motifs"][0]["kind"] = "chat"
    result = validate_intent_text(json.dumps(payload))
    issue = _issue(result, str(case["diagnostic_code"]))

    assert result["ok"] is False
    assert issue["path"] == case["source_path"]
    assert str(case["message_contains"]) in issue["message"]
    assert issue["suggestion"] == case["next_action"]


def test_artifact_tampering_fails_before_browser_execution(tmp_path):
    case = _case("artifact-tampering")
    artifact_dir = _write_react_artifact(tmp_path)
    tsx_path = artifact_dir / "ViewSpecView.tsx"
    tsx_path.write_text(tsx_path.read_text(encoding="utf-8") + "\n// tampered\n", encoding="utf-8")

    report = verify_host_artifact_dir(artifact_dir)

    assert report["ok"] is False
    assert report["errors"][0]["code"] == case["diagnostic_code"]
    assert any(str(case["message_contains"]) in error["message"] for error in report["errors"])
    assert all(error["fix"] == case["next_action"] for error in report["errors"])
    assert report["manifest_summary"]["available"] is True
    assert report["artifact_hash"] is None


def test_browser_environment_failure_keeps_the_proof_failed_and_actionable(tmp_path, monkeypatch):
    case = _case("browser-environment-failure")

    def unavailable_browser(*_args, **_kwargs):
        return {
            "ok": False,
            "errors": [
                {
                    "code": case["diagnostic_code"],
                    "message": "The reference browser dependencies are not installed.",
                    "fix": case["next_action"],
                }
            ],
        }

    prove_module = importlib.import_module("viewspec.prove")
    monkeypatch.setattr(prove_module, "verify_host_artifact_dir", unavailable_browser)
    proof_dir = tmp_path / "proof"

    report = prove(out_dir=proof_dir, target="react-tailwind-tsx", cwd=tmp_path)

    assert report["ok"] is False
    assert report["checks"]["artifact_check"] == "passed"
    assert report["checks"]["host_verify"] == "failed"
    assert report["errors"] == [
        {
            "code": case["diagnostic_code"],
            "message": "The reference browser dependencies are not installed.",
            "fix": case["next_action"],
        }
    ]
    proof_text = (proof_dir / "PROOF.md").read_text(encoding="utf-8")
    assert "Status: **FAILED**" in proof_text
    assert str(case["diagnostic_code"]) in proof_text
    assert "Status: **PASSED**" not in proof_text


def test_visible_conformance_failure_binds_source_evidence_and_next_action(tmp_path, monkeypatch):
    case = _case("visible-conformance-failure")
    artifact_dir = _write_react_artifact(tmp_path)

    def overflow_browser(_artifact_dir, _plan, evidence_dir, *, install):
        del install
        screenshot = evidence_dir / "mobile.png"
        screenshot.write_bytes(b"seeded mobile overflow")
        return BrowserPlanOutcome(
            complete=True,
            diagnostics=(
                VerificationDiagnostic(
                    code=str(case["diagnostic_code"]),
                    severity="error",
                    message="The content grid extends past the mobile viewport.",
                    fix=str(case["next_action"]),
                    source_ref=str(case["source_ref"]),
                    viewport="mobile",
                    evidence_refs=(str(case["evidence_ref"]),),
                ),
            ),
            evidence=(BrowserEvidence(str(case["evidence_ref"]), "screenshot"),),
        )

    monkeypatch.setattr("viewspec.local_verify._execute_browser_plan", overflow_browser)
    report_path = tmp_path / "verification.json"
    result = verify_local_artifact(
        artifact_dir,
        evidence_dir=tmp_path / "evidence",
        report_out=report_path,
    )
    diagnostic = result.diagnostics[0]

    assert result.status == "nonconformant"
    assert result.complete is True
    assert diagnostic.code == case["diagnostic_code"]
    assert str(case["message_contains"]) in diagnostic.message
    assert diagnostic.source_ref == case["source_ref"]
    assert diagnostic.evidence_refs == (case["evidence_ref"],)
    assert diagnostic.fix == case["next_action"]
    assert result.evidence[0].path == case["evidence_ref"]
    assert json.loads(report_path.read_text(encoding="utf-8"))["status"] == "nonconformant"
