from __future__ import annotations

import json
from pathlib import Path

import pytest

from viewspec.cli import main as cli_main
from viewspec.local_verify import BrowserEvidence, BrowserPlanOutcome, _execute_browser_plan, verify_local_artifact
from viewspec.sdk.builder import ViewSpecBuilder
from viewspec.verification import VerificationDiagnostic, VerificationPlan, VerificationResult


def _write_artifact(tmp_path: Path) -> Path:
    builder = ViewSpecBuilder("local_verify")
    dashboard = builder.add_dashboard("metrics", region="main", group_id="cards")
    dashboard.add_card(label="Open", value="18", id="open")
    intent_path = tmp_path / "intent.json"
    intent_path.write_text(json.dumps(builder.build_bundle().to_json()), encoding="utf-8")
    artifact_dir = tmp_path / "artifact"
    assert cli_main(
        ["compile", str(intent_path), "--target", "react-tailwind-tsx", "--out", str(artifact_dir)]
    ) == 0
    return artifact_dir


def _passing_browser(artifact_dir: Path, plan: VerificationPlan, evidence_dir: Path, *, install: bool) -> BrowserPlanOutcome:
    files: list[BrowserEvidence] = []
    for viewport in plan.viewports:
        screenshot = evidence_dir / f"{viewport.name}.png"
        dom = evidence_dir / f"{viewport.name}.dom.json"
        accessibility = evidence_dir / f"{viewport.name}.a11y.json"
        screenshot.write_bytes(f"png:{viewport.width}x{viewport.height}".encode())
        dom.write_text(json.dumps({"viewport": viewport.name, "nodes": 4}), encoding="utf-8")
        accessibility.write_text(json.dumps({"viewport": viewport.name, "violations": []}), encoding="utf-8")
        files.extend(
            [
                BrowserEvidence(f"evidence/{screenshot.name}", "screenshot"),
                BrowserEvidence(f"evidence/{dom.name}", "dom"),
                BrowserEvidence(f"evidence/{accessibility.name}", "accessibility"),
            ]
        )
    return BrowserPlanOutcome(complete=True, diagnostics=(), evidence=tuple(reversed(files)))


def test_local_verify_writes_canonical_cross_viewport_evidence(tmp_path, monkeypatch):
    artifact_dir = _write_artifact(tmp_path)
    evidence_dir = tmp_path / "evidence"
    report_path = tmp_path / "verification.json"
    monkeypatch.setattr("viewspec.local_verify._execute_browser_plan", _passing_browser)

    result = verify_local_artifact(
        artifact_dir,
        plan=VerificationPlan.default(),
        evidence_dir=evidence_dir,
        report_out=report_path,
    )

    assert result.status == "conformant"
    assert result.complete is True
    assert len(result.evidence) == 9
    assert [item.path for item in result.evidence] == sorted(item.path for item in result.evidence)
    assert {item.role for item in result.evidence} == {"screenshot", "dom", "accessibility"}
    assert VerificationResult.from_json(json.loads(report_path.read_text(encoding="utf-8"))) == result
    for item in result.evidence:
        assert item.verify(evidence_dir.joinpath(Path(item.path).relative_to("evidence")).read_bytes())


def test_local_verify_returns_source_addressable_nonconformance(tmp_path, monkeypatch):
    artifact_dir = _write_artifact(tmp_path)

    def overflow_browser(artifact_dir, plan, evidence_dir, *, install):
        screenshot = evidence_dir / "mobile.png"
        screenshot.write_bytes(b"overflow")
        return BrowserPlanOutcome(
            complete=True,
            diagnostics=(
                VerificationDiagnostic(
                    code="VERIFY_LAYOUT_OVERFLOW",
                    severity="error",
                    message="The content grid extends past the mobile viewport.",
                    fix="Constrain the content grid width and retry.",
                    source_ref="ir:content-grid",
                    viewport="mobile",
                    evidence_refs=("evidence/mobile.png",),
                ),
            ),
            evidence=(BrowserEvidence("evidence/mobile.png", "screenshot"),),
        )

    monkeypatch.setattr("viewspec.local_verify._execute_browser_plan", overflow_browser)

    result = verify_local_artifact(artifact_dir, evidence_dir=tmp_path / "evidence")

    assert result.status == "nonconformant"
    assert result.diagnostics[0].code == "VERIFY_LAYOUT_OVERFLOW"
    assert result.diagnostics[0].source_ref == "ir:content-grid"


def test_local_verify_preserves_indeterminate_browser_execution(tmp_path, monkeypatch):
    artifact_dir = _write_artifact(tmp_path)

    def unavailable_browser(artifact_dir, plan, evidence_dir, *, install):
        return BrowserPlanOutcome(
            complete=False,
            diagnostics=(
                VerificationDiagnostic(
                    code="VERIFY_BROWSER_UNAVAILABLE",
                    severity="warning",
                    message="Chromium is unavailable.",
                    fix="Install the bundled Chromium runtime and retry.",
                ),
            ),
        )

    monkeypatch.setattr("viewspec.local_verify._execute_browser_plan", unavailable_browser)

    result = verify_local_artifact(artifact_dir, evidence_dir=tmp_path / "evidence")

    assert result.status == "indeterminate"
    assert result.complete is False


def test_host_executor_preserves_browser_diagnostics_and_declared_evidence(tmp_path, monkeypatch):
    artifact_dir = tmp_path / "artifact"
    artifact_dir.mkdir()
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    plan = VerificationPlan.default()

    def fake_host_verify(artifact, *, install, report_out, verification_plan, evidence_dir):
        assert verification_plan == plan
        assert evidence_dir == tmp_path / "evidence"
        (evidence_dir / "mobile.png").write_bytes(b"overflow")
        report = {
            "ok": True,
            "errors": [],
            "verification_diagnostics": [
                {
                    "code": "VERIFY_LAYOUT_OVERFLOW",
                    "severity": "error",
                    "message": "The content grid exceeds the viewport.",
                    "fix": "Constrain the content grid width.",
                    "source_ref": "ir:content-grid",
                    "viewport": "mobile",
                    "evidence_refs": ["evidence/mobile.png"],
                }
            ],
            "evidence": [
                {
                    "path": "evidence/mobile.png",
                    "role": "screenshot",
                    "content_type": "image/png",
                }
            ],
        }
        Path(report_out).write_text(json.dumps(report), encoding="utf-8")
        return report

    monkeypatch.setattr("viewspec.local_verify.verify_host_artifact_dir", fake_host_verify)

    outcome = _execute_browser_plan(artifact_dir, plan, evidence_dir, install=False)

    assert outcome.complete is True
    assert outcome.diagnostics[0].code == "VERIFY_LAYOUT_OVERFLOW"
    assert outcome.diagnostics[0].source_ref == "ir:content-grid"
    assert {item.path for item in outcome.evidence} == {
        "evidence/host-report.json",
        "evidence/mobile.png",
    }


def test_host_executor_normalizes_blank_runtime_failure_text(tmp_path, monkeypatch):
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()

    def fake_host_verify(artifact, *, install, report_out, verification_plan, evidence_dir):
        report = {
            "ok": False,
            "errors": [
                {
                    "code": "HOST_VERIFY_BROWSER_RUNTIME_ERROR",
                    "message": "   ",
                    "fix": "\t",
                }
            ],
        }
        Path(report_out).write_text(json.dumps(report), encoding="utf-8")
        return report

    monkeypatch.setattr("viewspec.local_verify.verify_host_artifact_dir", fake_host_verify)

    outcome = _execute_browser_plan(
        tmp_path / "artifact",
        VerificationPlan.default(),
        evidence_dir,
        install=False,
    )

    assert outcome.complete is False
    assert outcome.diagnostics[0].code == "VERIFY_BROWSER_EXECUTION_FAILED"
    assert outcome.diagnostics[0].message == "Browser verification failed."
    assert outcome.diagnostics[0].fix == "Repair the artifact or browser environment and retry."


def test_local_verify_refuses_nonempty_evidence_directory(tmp_path, monkeypatch):
    artifact_dir = _write_artifact(tmp_path)
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    (evidence_dir / "user-file.txt").write_text("keep", encoding="utf-8")
    monkeypatch.setattr("viewspec.local_verify._execute_browser_plan", _passing_browser)

    with pytest.raises(FileExistsError, match="evidence directory"):
        verify_local_artifact(artifact_dir, evidence_dir=evidence_dir)

    assert (evidence_dir / "user-file.txt").read_text(encoding="utf-8") == "keep"


def test_result_hash_is_independent_of_executor_output_order(tmp_path, monkeypatch):
    artifact_dir = _write_artifact(tmp_path)

    def browser(reverse):
        def ordered_browser(artifact_dir, plan, evidence_dir, *, install):
            files = [
                BrowserEvidence("evidence/mobile.png", "screenshot"),
                BrowserEvidence("evidence/mobile.dom.json", "dom"),
            ]
            (evidence_dir / "mobile.png").write_bytes(b"png")
            (evidence_dir / "mobile.dom.json").write_bytes(b"{}")
            return BrowserPlanOutcome(complete=True, evidence=tuple(reversed(files)) if reverse else tuple(files))

        return ordered_browser

    monkeypatch.setattr("viewspec.local_verify._execute_browser_plan", browser(False))
    forward = verify_local_artifact(artifact_dir, evidence_dir=tmp_path / "evidence-forward")
    monkeypatch.setattr("viewspec.local_verify._execute_browser_plan", browser(True))
    reverse = verify_local_artifact(artifact_dir, evidence_dir=tmp_path / "evidence-reverse")

    assert forward.result_sha256 == reverse.result_sha256


def test_verify_cli_emits_json_and_uses_status_specific_exit_codes(tmp_path, monkeypatch, capsys):
    artifact_dir = _write_artifact(tmp_path)
    monkeypatch.setattr("viewspec.local_verify._execute_browser_plan", _passing_browser)
    capsys.readouterr()

    exit_code = cli_main(
        [
            "verify",
            str(artifact_dir),
            "--evidence-out",
            str(tmp_path / "evidence"),
            "--report-out",
            str(tmp_path / "report.json"),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "conformant"
    assert payload["verification_id"].startswith("vvr_")
    assert payload["repair_plan"]["disposition"] == "done"
    repair_path = tmp_path / "repair.json"
    assert json.loads(repair_path.read_text(encoding="utf-8")) == payload["repair_plan"]
