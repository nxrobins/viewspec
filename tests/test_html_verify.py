"""The html-tailwind and mcp-app proof contract, without a browser.

tests/test_html_verify_e2e.py runs the same path in real Chromium. These tests pin what decides
whether a browser runs at all, how a report becomes a verdict, and what the packaged harness ships.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

import pytest

from viewspec import ViewSpecBuilder, api
from viewspec.app_pretext import (
    PRETEXT_NPM_INTEGRITY,
    PRETEXT_NPM_RESOLVED,
    PRETEXT_PACKAGE,
    PRETEXT_VERSION,
    PRETEXT_VIEWPORTS,
    PretextFailure,
)
from viewspec.cli import main as cli_main
from viewspec.host_verify import HOST_VERIFY_REPORT_MAX_BYTES, CommandResult, HostVerifyFailure
from viewspec.html_verify import (
    HTML_VERIFY_MAX_FINDINGS,
    HTML_VERIFY_MAX_FINDINGS_PER_KIND,
    HTML_VERIFY_MAX_RENDERS,
    HTML_VERIFY_NODE_MODULE_BINS,
    HTML_VERIFY_TEMPLATE_FILES,
    _bounded_findings,
    _validate_pretext,
    html_artifact_target,
    load_mcp_render,
    verify_html_artifact_dir,
)
from viewspec.local_tools_response import ProveFailure
from viewspec.local_verify import BrowserEvidence, BrowserPlanOutcome, verify_local_artifact
from viewspec.prove import prove as prove_intent
from viewspec.verification import VerificationDiagnostic, VerificationPlan, VerificationResult, VerificationViewport

ROOT = Path(__file__).resolve().parents[1]


def _intent(tmp_path: Path) -> Path:
    builder = ViewSpecBuilder("html_verify", root_attrs={"title": "HTML verify"})
    builder.add_dashboard("metrics", region="main", group_id="cards").add_card(label="Open", value="18", id="open")
    path = tmp_path / "viewspec.intent.json"
    path.write_text(json.dumps(builder.build_bundle().to_json()), encoding="utf-8")
    return path


def _artifact(tmp_path: Path, target: str) -> Path:
    out = tmp_path / f"dist-{target}"
    assert cli_main(["compile", str(_intent(tmp_path)), "--target", target, "--out", str(out)]) == 0
    return out


def _render(root: str = '<main class="vs-root"></main>', **fields: Any) -> dict[str, Any]:
    return {"content": [], "structuredContent": {"viewspecRoot": root}, **fields}


def _verdict(*, complete: bool = True, code: str | None = None, severity: str = "error") -> BrowserPlanOutcome:
    diagnostics = (
        (VerificationDiagnostic(code=code, severity=severity, message="found", fix="fix it", viewport="mobile"),)
        if code
        else ()
    )
    return BrowserPlanOutcome(complete=complete, diagnostics=diagnostics)


def _stub_browser(monkeypatch: pytest.MonkeyPatch, outcome: BrowserPlanOutcome) -> list[dict[str, Any]]:
    """Replace the Chromium run with ``outcome`` and record what each call asked for."""
    calls: list[dict[str, Any]] = []

    def run(artifact_dir, plan, evidence_dir, **options):
        calls.append({"artifact_dir": artifact_dir, "plan": plan, **options})
        (evidence_dir / "host-report.json").write_text(
            json.dumps({"ok": outcome.complete, "text_layout": {"status": "passed"}, "errors": []}), encoding="utf-8"
        )
        return BrowserPlanOutcome(
            complete=outcome.complete,
            diagnostics=outcome.diagnostics,
            evidence=(BrowserEvidence("evidence/host-report.json", "log", "application/json"),),
        )

    monkeypatch.setattr("viewspec.local_verify._execute_html_browser_plan", run)
    return calls


# ---- Whether a browser runs at all


@pytest.mark.parametrize("target", ["html-tailwind", "mcp-app"])
def test_a_source_proof_stays_fast_without_browser_flags(tmp_path, monkeypatch, target):
    calls = _stub_browser(monkeypatch, _verdict())

    report = prove_intent(intent_path=_intent(tmp_path), out_dir=tmp_path / "proof", target=target)

    assert report["ok"] is True
    assert calls == []
    assert report["proof_level"] == "source_artifact"
    assert report["checks"]["host_verify"] == "not_applicable"
    assert report["checks"]["freerange"] == "not_applicable"
    assert report["metadata"]["network_calls"] == "none"
    assert report["host_report"] is None
    assert "verification" not in report
    assert not (tmp_path / "proof" / "evidence").exists()


@pytest.mark.parametrize(
    ("target", "level"), [("html-tailwind", "html_reference_host"), ("mcp-app", "mcp_app_reference_host")]
)
@pytest.mark.parametrize("flags", [{"install": True}, {"pretext": True}, {"install": True, "pretext": True}])
def test_a_browser_flag_runs_the_chromium_proof_on_a_source(tmp_path, monkeypatch, target, level, flags):
    calls = _stub_browser(monkeypatch, _verdict())

    report = prove_intent(intent_path=_intent(tmp_path), out_dir=tmp_path / "proof", target=target, **flags)

    assert report["ok"] is True
    assert report["proof_level"] == level
    assert report["checks"]["host_verify"] == "passed"
    assert report["verification"]["status"] == "conformant"
    assert report["host_report"]["ok"] is True
    assert report["metadata"]["network_calls"] == ("npm_ci_opt_in" if flags.get("install") else "none")
    assert [(call["install"], call["pretext"]) for call in calls] == [
        (flags.get("install", False), flags.get("pretext", False))
    ]
    assert ("pretext" in report["checks"]) is flags.get("pretext", False)
    assert "Chromium" in (tmp_path / "proof" / "PROOF.md").read_text(encoding="utf-8")
    assert "## Host Verification" in (tmp_path / "proof" / "PROOF.md").read_text(encoding="utf-8")


def test_a_react_target_keeps_its_own_host_and_never_reaches_the_html_harness(tmp_path, monkeypatch):
    calls = _stub_browser(monkeypatch, _verdict())

    report = prove_intent(intent_path=_intent(tmp_path), out_dir=tmp_path / "proof", target="react-tsx", install=True)

    assert report["ok"] is True
    assert calls == []
    assert report["proof_level"] == "source_artifact"


@pytest.mark.parametrize(
    ("outcome", "check", "code"),
    [
        (_verdict(), "passed", 0),
        (_verdict(code="VERIFY_LAYOUT_OVERFLOW"), "failed", 1),
        (_verdict(complete=False, code="VERIFY_BROWSER_EXECUTION_FAILED", severity="warning"), "indeterminate", 2),
    ],
)
@pytest.mark.parametrize("target", ["html-tailwind", "mcp-app"])
def test_a_source_proof_exits_like_a_directory_proof(tmp_path, monkeypatch, capsys, outcome, check, code, target):
    _stub_browser(monkeypatch, outcome)
    out = tmp_path / "proof"

    exit_code = cli_main(
        ["prove", str(_intent(tmp_path)), "--target", target, "--install", "--out", str(out), "--json"]
    )

    assert exit_code == code
    report = json.loads(capsys.readouterr().out)
    assert report["checks"]["host_verify"] == check
    assert report["ok"] is (code == 0)
    if code:
        assert report["errors"][0]["code"].startswith("VERIFY_")
        assert report["errors"][0]["message"] == "[mobile] found"


@pytest.mark.parametrize("target", ["html-tailwind", "mcp-app"])
def test_a_directory_routes_to_the_html_harness(tmp_path, monkeypatch, target):
    calls = _stub_browser(monkeypatch, _verdict())
    artifact = _artifact(tmp_path, target)

    result = api.prove(artifact, out_dir=tmp_path / "proof", install=True, pretext=True)

    assert result["status"] == "conformant"
    assert calls[0]["pretext"] is True
    assert calls[0]["install"] is True


def test_a_react_directory_still_goes_to_the_reference_host(tmp_path, monkeypatch):
    html_calls = _stub_browser(monkeypatch, _verdict())
    react_calls: list[Path] = []

    def reference_host(artifact_dir, plan, evidence_dir, *, install):
        react_calls.append(artifact_dir)
        return BrowserPlanOutcome(complete=True)

    monkeypatch.setattr("viewspec.local_verify._execute_browser_plan", reference_host)
    artifact = _artifact(tmp_path, "react-tailwind-tsx")

    assert api.prove(artifact, out_dir=tmp_path / "proof")["status"] == "conformant"
    assert react_calls and html_calls == []


@pytest.mark.parametrize(
    ("target", "expected"),
    [("html-tailwind", "html-tailwind"), ("mcp-app", "mcp-app"), ("react-tailwind-tsx", None), ("react-tsx", None)],
)
def test_html_artifact_target_reads_the_manifest(tmp_path, target, expected):
    assert html_artifact_target(_artifact(tmp_path, target)) == expected


def test_html_artifact_target_refuses_what_it_cannot_read(tmp_path):
    assert html_artifact_target(tmp_path) is None
    (tmp_path / "provenance_manifest.json").write_text("not json", encoding="utf-8")
    assert html_artifact_target(tmp_path) is None


def test_a_raw_html_artifact_stays_out_of_the_harness(tmp_path):
    source = tmp_path / "page.html"
    source.write_text("<!doctype html><html><body><p>Hello</p></body></html>", encoding="utf-8")
    out = tmp_path / "raw"
    assert cli_main(["compile", str(source), "--out", str(out)]) == 0

    assert html_artifact_target(out) is None


# ---- Freerange, Pretext and render requests that cannot be honored


@pytest.mark.parametrize("target", ["html-tailwind", "mcp-app", "react-tsx", "react-tailwind-tsx"])
def test_freerange_fails_closed_on_every_intent_target(tmp_path, capsys, target):
    out = tmp_path / "proof"

    exit_code = cli_main(["prove", str(_intent(tmp_path)), "--target", target, "--freerange", "--out", str(out)])

    assert exit_code == 2
    error = capsys.readouterr().err
    assert "APP_FREERANGE_TARGET_UNSUPPORTED" in error
    assert target in error
    assert "numeric reducer kernel" in error
    assert not out.exists()


def test_freerange_names_the_default_target(tmp_path, capsys):
    assert cli_main(["prove", str(_intent(tmp_path)), "--freerange", "--out", str(tmp_path / "proof")]) == 2

    assert "html-tailwind emits no TypeScript numeric reducer kernel" in capsys.readouterr().err


@pytest.mark.parametrize("target", ["html-tailwind", "mcp-app"])
def test_freerange_fails_closed_on_a_compiled_directory(tmp_path, capsys, target):
    artifact = _artifact(tmp_path, target)
    capsys.readouterr()

    assert cli_main(["prove", str(artifact), "--freerange", "--out", str(tmp_path / "proof"), "--json"]) == 2

    report = json.loads(capsys.readouterr().out)
    assert report["errors"][0]["code"] == "APP_FREERANGE_TARGET_UNSUPPORTED"
    assert target in report["errors"][0]["message"]
    assert "--target react-tailwind-app" in report["errors"][0]["fix"]


@pytest.mark.parametrize("target", ["react-tsx", "react-tailwind-tsx"])
def test_pretext_is_refused_on_an_intent_target_without_a_probe(tmp_path, capsys, target):
    out = tmp_path / "proof"

    assert cli_main(["prove", str(_intent(tmp_path)), "--target", target, "--pretext", "--out", str(out)]) == 2

    assert "APP_PRETEXT_TARGET_UNSUPPORTED" in capsys.readouterr().err
    assert not out.exists()


def test_pretext_is_refused_on_a_react_directory(tmp_path):
    artifact = _artifact(tmp_path, "react-tailwind-tsx")

    with pytest.raises(ProveFailure, match="APP_PRETEXT_TARGET_UNSUPPORTED"):
        api.prove(artifact, out_dir=tmp_path / "proof", pretext=True)


def test_pretext_needs_the_canonical_viewports(tmp_path, monkeypatch):
    calls = _stub_browser(monkeypatch, _verdict())
    artifact = _artifact(tmp_path, "html-tailwind")
    plan = VerificationPlan(viewports=(VerificationViewport(name="wide", width=1920, height=1080),))

    with pytest.raises(ProveFailure, match="APP_PRETEXT_SCOPE_INVALID"):
        verify_local_artifact(artifact, plan=plan, evidence_dir=tmp_path / "evidence", pretext=True)

    assert calls == []
    assert not (tmp_path / "evidence").exists()


def test_the_default_plan_is_the_canonical_pretext_matrix():
    planned = [viewport.to_json() for viewport in VerificationPlan.default().viewports]

    assert planned == [{"name": v["id"], "width": v["width"], "height": v["height"]} for v in PRETEXT_VIEWPORTS]


def test_a_custom_plan_runs_without_pretext(tmp_path, monkeypatch):
    calls = _stub_browser(monkeypatch, _verdict())
    plan = VerificationPlan(viewports=(VerificationViewport(name="wide", width=1920, height=1080),))

    result = verify_local_artifact(_artifact(tmp_path, "html-tailwind"), plan=plan, evidence_dir=tmp_path / "evidence")

    assert result.status == "conformant"
    assert calls[0]["plan"] == plan


@pytest.mark.parametrize(
    "render",
    [
        {},
        {"structuredContent": {}},
        {"structuredContent": {"viewspecRoot": 42}},
        {"structuredContent": {"viewspecRoot": ""}},
        {"structuredContent": {"viewspecBindings": {"a": "b"}}},
        _render(isError=True),
        "not a tool result",
    ],
)
def test_a_render_must_be_a_successful_root(render, tmp_path):
    path = tmp_path / "render.json"
    path.write_text(json.dumps(render), encoding="utf-8")

    with pytest.raises(ProveFailure, match="PROVE_RENDER_INVALID"):
        load_mcp_render(path)


def test_a_render_loads_from_a_file_a_mapping_or_a_result_object(tmp_path):
    path = tmp_path / "render.json"
    path.write_text(json.dumps(_render()), encoding="utf-8")

    class Result:
        def model_dump(self, **options):
            assert options == {"mode": "json", "by_alias": True, "exclude_none": True}
            return _render()

    expected = {"viewspecRoot": '<main class="vs-root"></main>'}
    assert load_mcp_render(path) == load_mcp_render(_render()) == load_mcp_render(Result()) == expected


def test_a_render_file_that_cannot_be_read_says_so(tmp_path):
    with pytest.raises(ProveFailure, match="PROVE_RENDER_INVALID"):
        load_mcp_render(tmp_path / "missing.json")


def test_a_render_is_refused_on_html_tailwind(tmp_path, monkeypatch):
    calls = _stub_browser(monkeypatch, _verdict())
    artifact = _artifact(tmp_path, "html-tailwind")

    with pytest.raises(ProveFailure, match="PROVE_RENDER_INVALID"):
        api.prove(artifact, out_dir=tmp_path / "proof", renders=[_render()])

    assert calls == []


def test_a_render_needs_the_mcp_app_target_on_a_source(tmp_path, capsys):
    render = tmp_path / "render.json"
    render.write_text(json.dumps(_render()), encoding="utf-8")
    out = tmp_path / "proof"

    assert cli_main(["prove", str(_intent(tmp_path)), "--render", str(render), "--out", str(out)]) == 2

    assert "PROVE_RENDER_INVALID" in capsys.readouterr().err
    assert not out.exists()


def test_a_render_reaches_the_harness_from_the_cli(tmp_path, monkeypatch):
    calls = _stub_browser(monkeypatch, _verdict())
    render = tmp_path / "render.json"
    render.write_text(json.dumps(_render()), encoding="utf-8")
    artifact = _artifact(tmp_path, "mcp-app")

    assert cli_main(["prove", str(artifact), "--render", str(render), "--out", str(tmp_path / "proof")]) == 0

    assert calls[0]["renders"] == ({"viewspecRoot": '<main class="vs-root"></main>'},)


def test_renders_are_bounded(tmp_path, monkeypatch):
    calls = _stub_browser(monkeypatch, _verdict())
    artifact = _artifact(tmp_path, "mcp-app")
    too_many = [_render()] * (HTML_VERIFY_MAX_RENDERS + 1)

    with pytest.raises(ProveFailure, match="at most"):
        api.prove(artifact, out_dir=tmp_path / "proof", renders=too_many)

    assert calls == []


def test_a_proof_cannot_outgrow_the_evidence_limit(tmp_path, monkeypatch):
    calls = _stub_browser(monkeypatch, _verdict())
    plan = VerificationPlan(
        viewports=tuple(VerificationViewport(name=f"v{n}", width=400 + n, height=800) for n in range(8))
    )
    renders = tuple(load_mcp_render(_render()) for _ in range(HTML_VERIFY_MAX_RENDERS))

    with pytest.raises(ProveFailure, match="evidence limit"):
        verify_local_artifact(
            _artifact(tmp_path, "mcp-app"), plan=plan, evidence_dir=tmp_path / "evidence", renders=renders
        )

    assert calls == []


# ---- How a run becomes a verdict


def _fake_toolchain(monkeypatch: pytest.MonkeyPatch, *, node_modules: bool) -> list[list[str]]:
    """Stand in for node and npm, leaving node_modules as the test says."""
    commands: list[list[str]] = []

    def run(command, *, cwd, **_options):
        commands.append(command)
        if command[1:3] == ["ci", "--ignore-scripts"] and node_modules:
            bin_dir = cwd / "node_modules" / ".bin"
            bin_dir.mkdir(parents=True)
            for name in HTML_VERIFY_NODE_MODULE_BINS:
                (bin_dir / name).write_text("", encoding="utf-8")
        return CommandResult(stdout="v1.0.0\n", stderr="", returncode=0)

    monkeypatch.setattr("viewspec.html_verify._run_process", run)
    monkeypatch.setattr("viewspec.html_verify._require_executable", lambda name, _code: name)
    monkeypatch.delenv("VIEWSPEC_HTML_VERIFY_NODE_MODULES_DIR", raising=False)
    return commands


@pytest.mark.parametrize("target", ["html-tailwind", "mcp-app"])
def test_a_proof_with_no_harness_dependencies_is_indeterminate_not_a_failure(tmp_path, monkeypatch, capsys, target):
    _fake_toolchain(monkeypatch, node_modules=False)
    artifact = _artifact(tmp_path, target)
    capsys.readouterr()

    exit_code = cli_main(["prove", str(artifact), "--out", str(tmp_path / "proof"), "--json"])

    assert exit_code == 2
    result = VerificationResult.from_json(json.loads(capsys.readouterr().out))
    assert result.status == "indeterminate"
    assert result.diagnostics[0].code == "VERIFY_BROWSER_EXECUTION_FAILED"
    assert "--install" in result.diagnostics[0].message
    assert [item.path for item in result.evidence] == ["evidence/host-report.json"]


def test_a_failed_browser_run_is_indeterminate(tmp_path, monkeypatch):
    commands = _fake_toolchain(monkeypatch, node_modules=True)

    def fail_in_the_browser(host_dir, config, *, started, timings):
        raise HostVerifyFailure("HOST_VERIFY_BROWSER_RUNTIME_ERROR", "Executable doesn't exist")

    monkeypatch.setattr("viewspec.html_verify._run_browser", fail_in_the_browser)
    result = verify_local_artifact(
        _artifact(tmp_path, "html-tailwind"), evidence_dir=tmp_path / "evidence", install=True
    )

    assert result.status == "indeterminate"
    assert "Executable doesn't exist" in result.diagnostics[0].message
    assert any(command[1:3] == ["ci", "--ignore-scripts"] for command in commands)


def test_the_harness_refuses_a_document_that_does_not_match_its_manifest(tmp_path):
    artifact = _artifact(tmp_path, "html-tailwind")
    html = artifact / "index.html"
    html.write_text(html.read_text(encoding="utf-8").replace("Open", "Shut"), encoding="utf-8")

    report = verify_html_artifact_dir(artifact, plan=VerificationPlan.default(), evidence_dir=tmp_path / "evidence")

    assert report["ok"] is False
    assert {error["code"] for error in report["errors"]} == {"HOST_VERIFY_ARTIFACT_CHECK_FAILED"}


def test_the_harness_refuses_an_artifact_it_does_not_prove(tmp_path):
    artifact = _artifact(tmp_path, "react-tailwind-tsx")

    report = verify_html_artifact_dir(artifact, plan=VerificationPlan.default(), evidence_dir=tmp_path / "evidence")

    assert [error["code"] for error in report["errors"]] == ["HOST_VERIFY_UNSUPPORTED_TARGET"]


def test_every_report_says_freerange_does_not_apply(tmp_path):
    artifact = _artifact(tmp_path, "mcp-app")
    (artifact / "index.html").write_text("tampered", encoding="utf-8")

    report = verify_html_artifact_dir(artifact, plan=VerificationPlan.default(), evidence_dir=tmp_path / "evidence")

    assert report["target"] == "mcp-app"
    assert report["freerange"] == "not_applicable"
    assert report["text_layout"] == {"status": "not_requested"}


def _pretext_report(tmp_path: Path, *items: dict[str, Any]) -> Path:
    path = tmp_path / "pretext-report.json"
    path.write_text(json.dumps({"items": list(items)}), encoding="utf-8")
    return path


def _pretext_item(status: str, reason: str | None = None) -> dict[str, Any]:
    item = {
        "status": status,
        "surface_id": "dom-a",
        "ir_id": "a",
        "viewport_id": "mobile",
        "predicted_line_count": 1,
        "observed_line_count": 2,
    }
    return {**item, "reason": reason} if reason else item


@pytest.mark.parametrize(
    ("reason", "code"),
    [
        ("horizontal_overflow", "VERIFY_LAYOUT_OVERFLOW"),
        ("vertical_overflow", "VERIFY_LAYOUT_OVERFLOW"),
        ("line_count_mismatch", "VERIFY_LAYOUT_CONFORMANCE_FAILED"),
    ],
)
def test_a_surface_that_fails_pretext_is_a_finding_against_the_document(tmp_path, reason, code):
    report = _pretext_report(tmp_path, _pretext_item("failed", reason), _pretext_item("passed"))

    text_layout, findings = _validate_pretext(report, {})

    assert text_layout == {"status": "failed", "findings": 1}
    assert [(item["code"], item["viewport"], item["source_ref"]) for item in findings] == [(code, "mobile", "ir:a")]
    assert findings[0]["evidence_refs"] == ["evidence/pretext-report.json"]
    VerificationDiagnostic.from_json(findings[0])


@pytest.mark.parametrize(
    "item", [_pretext_item("unsupported", "unsupported_font_family"), _pretext_item("failed", "target_missing")]
)
def test_a_surface_pretext_could_not_measure_goes_to_the_validator(tmp_path, item):
    report = _pretext_report(tmp_path, item)

    # A measurement that never happened says nothing about the document, so it is not a finding.
    with pytest.raises(PretextFailure):
        _validate_pretext(report, {})


def _finding(code: str, viewport: str = "mobile", number: int = 0) -> dict[str, Any]:
    return {
        "code": code,
        "severity": "error",
        "message": f"finding {number}",
        "fix": "fix it",
        "source_ref": None,
        "viewport": viewport,
        "evidence_refs": ["evidence/mobile.png"],
    }


def test_each_state_of_the_view_keeps_its_own_findings():
    document = [_finding("VERIFY_LAYOUT_OVERFLOW", number=number) for number in range(20)]
    swapped = [
        {**_finding("VERIFY_LAYOUT_OVERFLOW", number=number), "evidence_refs": ["evidence/mobile.swap.png"]}
        for number in range(20)
    ]

    kept = _bounded_findings([*document, *swapped])

    assert sum(item["evidence_refs"] == ["evidence/mobile.swap.png"] for item in kept[:-1]) == 4
    assert sum(item["evidence_refs"] == ["evidence/mobile.png"] for item in kept[:-1]) == 4


def test_findings_under_the_bound_pass_through():
    findings = [_finding("VERIFY_LAYOUT_OVERFLOW")]

    assert _bounded_findings(findings) == findings


def test_one_loud_defect_cannot_hide_a_quiet_one():
    noise = [_finding("VERIFY_LAYOUT_OVERFLOW", number=number) for number in range(500)]
    quiet = _finding("VERIFY_A11Y_VIOLATION")

    kept = _bounded_findings([*noise, quiet])

    assert [item["code"] for item in kept[:-1]].count("VERIFY_LAYOUT_OVERFLOW") == HTML_VERIFY_MAX_FINDINGS_PER_KIND
    assert quiet in kept
    summary = kept[-1]
    assert summary["severity"] == "info"
    assert summary["viewport"] is None
    assert "496 further findings" in summary["message"]
    VerificationDiagnostic.from_json(summary)


def test_findings_stay_inside_the_total_bound():
    codes = [f"VERIFY_KIND_{index}" for index in range(HTML_VERIFY_MAX_FINDINGS * 2)]

    kept = _bounded_findings([_finding(code) for code in codes])

    assert len(kept) == HTML_VERIFY_MAX_FINDINGS + 1


def test_a_finding_with_no_evidence_is_still_bounded():
    findings = [{**_finding("VERIFY_RUNTIME_ERROR"), "evidence_refs": []} for _ in range(20)]

    assert len(_bounded_findings(findings)) == HTML_VERIFY_MAX_FINDINGS_PER_KIND + 1


def test_a_bounded_report_still_fits_the_host_report_ceiling():
    findings = [
        {**_finding(code, viewport, number), "message": "m" * 180, "fix": "f" * 100}
        for code in ("VERIFY_LAYOUT_OVERFLOW", "VERIFY_LAYOUT_OVERLAP", "VERIFY_A11Y_VIOLATION", "VERIFY_RUNTIME_ERROR")
        for viewport in ("mobile", "tablet", "desktop")
        for number in range(50)
    ]

    assert len(json.dumps(_bounded_findings(findings)).encode("utf-8")) < HOST_VERIFY_REPORT_MAX_BYTES / 2


# ---- What the package ships


def _template() -> Any:
    return resources.files("viewspec.html_verify_template")


def test_the_harness_template_is_packaged():
    for name in HTML_VERIFY_TEMPLATE_FILES:
        assert _template().joinpath(name).is_file(), name


def test_the_template_pins_the_pretext_package_the_validator_expects():
    package = json.loads(_template().joinpath("package.json").read_text(encoding="utf-8"))
    lock = json.loads(_template().joinpath("package-lock.json").read_text(encoding="utf-8"))
    entry = lock["packages"][f"node_modules/{PRETEXT_PACKAGE}"]

    assert package["dependencies"][PRETEXT_PACKAGE] == PRETEXT_VERSION
    assert lock["lockfileVersion"] == 3
    assert lock["packages"][""]["dependencies"][PRETEXT_PACKAGE] == PRETEXT_VERSION
    assert (entry["version"], entry["resolved"], entry["integrity"]) == (
        PRETEXT_VERSION,
        PRETEXT_NPM_RESOLVED,
        PRETEXT_NPM_INTEGRITY,
    )
    assert entry.get("dev") in (None, False)


def test_the_template_proves_against_the_sdk_the_host_test_pins():
    shipped = json.loads(_template().joinpath("package.json").read_text(encoding="utf-8"))["dependencies"]
    tested = json.loads((ROOT / "tests" / "mcp-app-host" / "package.json").read_text(encoding="utf-8"))

    for name, version in tested["devDependencies"].items():
        assert shipped[name] == version, name


def test_the_harness_builds_nothing_and_loads_the_document_as_it_stands():
    package = json.loads(_template().joinpath("package.json").read_text(encoding="utf-8"))
    driver = _template().joinpath("verify.mjs").read_text(encoding="utf-8")

    assert "vite" not in json.dumps(package)
    assert "vite" not in driver
    assert 'server.listen(0, "127.0.0.1"' in driver
    assert "adoptedStyleSheets" in driver
    assert "setContent" not in driver
    assert 'sandbox", "allow-scripts"' in _template().joinpath("host.ts").read_text(encoding="utf-8")
