"""Bounded Chromium verification for compiled html-tailwind and mcp-app artifacts.

These documents are already self-contained, so nothing is built. A temporary copy of the packaged
harness loads ``index.html`` as it stands: html-tailwind from a loopback port, and an MCP App in the
ext-apps SDK's ``AppBridge``, in a sandboxed iframe under the specification's default CSP. Each
viewport gets the manifest DOM, grid and span, aesthetic layout, overflow, overlap, and accessibility
checks that the React host runs. An MCP App also has to finish ``ui/initialize``, and to take its own
compiled root, and any ``app.render()`` result, through the same path a tool result takes.

Pretext is opt-in and arrives by evaluation, so the document and its pinned bridge stay byte for
byte what ``check`` passed.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from viewspec.app_pretext import (
    PRETEXT_PACKAGE,
    PRETEXT_VIEWPORTS,
    PretextFailure,
    build_pretext_scope,
    validate_pretext_installation,
    validate_pretext_runtime_report,
)
from viewspec.app_pretext_runtime import generate_pretext_runtime_typescript
from viewspec.host_verify import (
    HOST_VERIFY_PHASE_TIMEOUTS_MS,
    HostVerifyFailure,
    _assert_node_modules_dir,
    _assert_runtime_report,
    _assert_workspace_safe,
    _base_report,
    _collect_planned_browser_results,
    _copy_template,
    _error_report,
    _finalize_report,
    _host_assertion_requirements,
    _link_prebuilt_node_modules,
    _remaining_timeout,
    _require_executable,
    _run_process,
    _time_phase,
)
from viewspec.local_tools import MCP_APP_TARGET, atomic_write, check_artifact_dir, file_hash
from viewspec.local_tools_response import ProveFailure
from viewspec.mcp_apps import MCPAppArtifact
from viewspec.verification import MAX_EVIDENCE_FILES, VerificationPlan

HTML_VERIFY_TARGET = "html-tailwind"
HTML_VERIFY_TARGETS = (HTML_VERIFY_TARGET, MCP_APP_TARGET)
HTML_VERIFY_EMITTER = "html_tailwind"
HTML_VERIFY_TEMPLATE_PACKAGE = "viewspec.html_verify_template"
HTML_VERIFY_TEMPLATE_FILES = ("package.json", "package-lock.json", "host.ts", "verify.mjs")
HTML_VERIFY_NODE_MODULE_BINS = ("esbuild", "playwright")
HTML_VERIFY_NODE_MODULES_ENV = "VIEWSPEC_HTML_VERIFY_NODE_MODULES_DIR"
HTML_VERIFY_BROWSER_TIMEOUT_MS = 90_000
HTML_VERIFY_MAX_RENDERS = 8
# Enough to show every kind of finding in every state of the view, inside the host report's 64 KiB.
HTML_VERIFY_MAX_FINDINGS_PER_KIND = 4
HTML_VERIFY_MAX_FINDINGS = 96
HTML_VERIFY_EXTRA_ASSERTIONS = ("handshake_count", "swap_count")
# Every document has a root, so this holds for the smallest one, at any number of viewports.
HTML_VERIFY_STYLE_ASSERTIONS_PER_VIEWPORT = 2

# The surface is on screen but does not lay out as predicted, so the document is at fault. Every
# other probe failure means the measurement could not be made.
_PRETEXT_FINDING_CODES = {
    "horizontal_overflow": "VERIFY_LAYOUT_OVERFLOW",
    "vertical_overflow": "VERIFY_LAYOUT_OVERFLOW",
    "line_count_mismatch": "VERIFY_LAYOUT_CONFORMANCE_FAILED",
}
_PRETEXT_FINDING_FIX = "Fix the responsive text surface and rerun the browser evidence pass."
_RENDER_FIX = (
    f"Pass the JSON of a successful app.render() result to an mcp-app proof, {HTML_VERIFY_MAX_RENDERS} at most."
)
_PRETEXT_REPORT = "pretext-report.json"
_PRETEXT_SUMMARY_FIELDS = (
    "status",
    "engine",
    "profile",
    "coverage",
    "cache",
    "eligible_surface_coverage",
    "observation_digest",
    "scope_sha256",
    "report_sha256",
)


def html_artifact_target(artifact_dir: str | Path) -> str | None:
    """Return the proof target of a compiled html-tailwind or MCP App directory, else ``None``."""
    try:
        manifest = json.loads((Path(artifact_dir) / "provenance_manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        not isinstance(manifest, dict)
        or manifest.get("kind") != "intent_bundle_compile"
        or manifest.get("emitter") != HTML_VERIFY_EMITTER
        or manifest.get("artifact_file") != "index.html"
    ):
        return None
    return MCP_APP_TARGET if manifest.get("target") == MCP_APP_TARGET else HTML_VERIFY_TARGET


def load_mcp_render(render: Any) -> dict[str, Any]:
    """Return one tool result as JSON, from ``app.render()``, a mapping, or a JSON file path.

    Only ``structuredContent`` reaches the view, so that is all a proof sends.
    """
    if isinstance(render, (str, Path)):
        try:
            render = json.loads(Path(render).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ProveFailure("PROVE_RENDER_INVALID", f"Could not read render {render}: {exc}", _RENDER_FIX) from exc
    elif hasattr(render, "model_dump"):
        render = render.model_dump(mode="json", by_alias=True, exclude_none=True)
    content = render.get("structuredContent") if isinstance(render, Mapping) else None
    if not isinstance(content, Mapping) or not content.get("viewspecRoot") or render.get("isError"):
        raise ProveFailure(
            "PROVE_RENDER_INVALID",
            "A render must be a successful result with structuredContent.viewspecRoot.",
            _RENDER_FIX,
        )
    if not isinstance(content["viewspecRoot"], str):
        raise ProveFailure("PROVE_RENDER_INVALID", "structuredContent.viewspecRoot must be text.", _RENDER_FIX)
    return {str(key): value for key, value in content.items()}


def assert_html_proof_request(
    target: str | None,
    plan: VerificationPlan,
    *,
    pretext: bool,
    renders: Sequence[Mapping[str, Any]],
) -> None:
    """Refuse a request the proof could not honor, before any browser starts."""
    if pretext and target not in HTML_VERIFY_TARGETS:
        raise ProveFailure(
            "APP_PRETEXT_TARGET_UNSUPPORTED",
            "--pretext on a compiled directory needs an html-tailwind or mcp-app artifact.",
            "Compile the source for html-tailwind or mcp-app, or prove an AppBundle with --target react-tailwind-app.",
        )
    if renders and target != MCP_APP_TARGET:
        raise ProveFailure("PROVE_RENDER_INVALID", "--render applies to an mcp-app artifact.", _RENDER_FIX)
    if len(renders) > HTML_VERIFY_MAX_RENDERS:
        raise ProveFailure(
            "PROVE_RENDER_INVALID", f"A proof takes at most {HTML_VERIFY_MAX_RENDERS} renders.", _RENDER_FIX
        )
    # Screenshot, DOM, and accessibility files per state: the document, its swap, and each render.
    states = 1 + (0 if target != MCP_APP_TARGET else 1 + len(renders))
    if target is not None and 3 * states * len(plan.viewports) + 2 > MAX_EVIDENCE_FILES:
        raise ProveFailure(
            "PROVE_RENDER_INVALID", "This many renders and viewports exceed the evidence limit.", _RENDER_FIX
        )
    planned = [viewport.to_json() for viewport in plan.viewports]
    canonical = [{"name": item["id"], "width": item["width"], "height": item["height"]} for item in PRETEXT_VIEWPORTS]
    if pretext and planned != canonical:
        raise ProveFailure(
            "APP_PRETEXT_SCOPE_INVALID",
            "--pretext measures the canonical viewports, so a custom --plan cannot carry it.",
            "Remove --plan or --pretext.",
        )


def verify_html_artifact_dir(
    artifact_dir: str | Path,
    *,
    plan: VerificationPlan,
    evidence_dir: str | Path,
    report_out: str | Path | None = None,
    install: bool = False,
    pretext: bool = False,
    renders: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Prove a compiled html-tailwind or mcp-app artifact across ``plan`` in Chromium.

    The report's ``verification_diagnostics`` hold what the document got wrong. Its ``errors`` hold
    what stopped the proof from reaching a verdict, so a broken environment is never read as a
    broken document.
    """
    timings: dict[str, int] = {}
    started = time.perf_counter()
    artifact_path = Path(artifact_dir).resolve()
    target = html_artifact_target(artifact_path) or HTML_VERIFY_TARGET
    manifest_summary: dict[str, Any] | None = None

    def failed(code: str, message: str, fix: str | None = None, **fields: Any) -> dict[str, Any]:
        report = _error_report(
            code,
            message,
            artifact_dir=artifact_path,
            install=install,
            timings=timings,
            manifest_summary=manifest_summary,
            fix=fix,
            target=target,
            **fields,
        )
        return _finalize_report(_with_proof_scope(report, None), report_out)

    try:
        if html_artifact_target(artifact_path) is None:
            raise HostVerifyFailure(
                "HOST_VERIFY_UNSUPPORTED_TARGET",
                "Chromium verification supports only intent_bundle_compile html_tailwind index.html artifacts.",
            )
        checked = _time_phase(timings, "check_copy", lambda: check_artifact_dir(artifact_path))
        manifest_summary = (
            checked.get("manifest_summary") if isinstance(checked.get("manifest_summary"), dict) else None
        )
        if not checked["ok"]:
            errors = [
                {
                    "code": "HOST_VERIFY_ARTIFACT_CHECK_FAILED",
                    "message": item,
                    "fix": "Fix the compiled artifact and re-run viewspec check.",
                }
                for item in checked["errors"]
            ]
            return failed("HOST_VERIFY_ARTIFACT_CHECK_FAILED", "viewspec check failed.", errors=errors)

        with tempfile.TemporaryDirectory(prefix="viewspec-html-verify-") as temp_name:
            host_dir = Path(temp_name).resolve() / "host"
            host_dir.mkdir(parents=True)
            _assert_workspace_safe(host_dir, artifact_path)
            _copy_template(host_dir, package=HTML_VERIFY_TEMPLATE_PACKAGE, files=HTML_VERIFY_TEMPLATE_FILES)
            copied = _copy_artifact(artifact_path, host_dir)
            versions = _install(host_dir, install=install, started=started, timings=timings)
            scope = _pretext_scope(host_dir, copied) if pretext else None
            browser_root = host_dir / ".viewspec-html-verify"
            config = _write_config(
                host_dir, browser_root, copied, target=target, plan=plan, renders=renders, pretext_scope=scope
            )
            _run_browser(host_dir, config, started=started, timings=timings)
            collected = _collect_planned_browser_results(
                browser_root / "browser-reports", browser_root / "evidence", Path(evidence_dir).resolve(), plan
            )
            diagnostics = collected["verification_diagnostics"]
            evidence = collected["evidence"]
            requirements = {
                **_host_assertion_requirements(manifest_summary),
                "style_assertion_count": HTML_VERIFY_STYLE_ASSERTIONS_PER_VIEWPORT * len(plan.viewports),
            }
            try:
                text_layout = None
                if scope is not None and scope["status"] == "applicable":
                    report_path = browser_root / _PRETEXT_REPORT
                    shutil.copyfile(report_path, Path(evidence_dir).resolve() / _PRETEXT_REPORT)
                    evidence = [
                        *evidence,
                        {"path": f"evidence/{_PRETEXT_REPORT}", "role": "log", "content_type": "application/json"},
                    ]
                    text_layout, findings = _validate_pretext(report_path, scope)
                    diagnostics = [*diagnostics, *findings]
                assertions = {
                    **_assert_runtime_report(collected, requirements=requirements),
                    **{key: int(collected["assertions"].get(key, 0)) for key in HTML_VERIFY_EXTRA_ASSERTIONS},
                }
            except (HostVerifyFailure, PretextFailure) as exc:
                # The evidence is already on disk, and a verdict needs every file it holds declared.
                return failed(
                    exc.code,
                    exc.message,
                    exc.fix,
                    verification_diagnostics=_bounded_findings(diagnostics),
                    evidence=evidence,
                )
            report = _base_report(
                ok=True,
                artifact_dir=artifact_path,
                install=install,
                timings=timings,
                artifact_hash=file_hash(artifact_path / "index.html"),
                manifest_hash=file_hash(artifact_path / "provenance_manifest.json"),
                diagnostics_hash=file_hash(artifact_path / "diagnostics.json"),
                host_template_lock_hash=file_hash(host_dir / "package-lock.json"),
                node_version=versions["node"],
                npm_version=versions["npm"],
                assertions=assertions,
                assertion_requirements=requirements,
                verification_diagnostics=_bounded_findings(diagnostics),
                evidence=evidence,
                viewport_count=collected["viewport_count"],
                manifest_summary=manifest_summary,
                errors=[],
                target=target,
            )
            return _finalize_report(_with_proof_scope(report, text_layout), report_out)
    except PretextFailure as exc:
        return failed(exc.code, exc.message, exc.fix)
    except HostVerifyFailure as exc:
        return failed(exc.code, exc.message, exc.fix)
    except Exception as exc:
        return failed("HOST_VERIFY_BROWSER_RUNTIME_ERROR", str(exc))


def _with_proof_scope(report: dict[str, Any], text_layout: dict[str, Any] | None) -> dict[str, Any]:
    return {
        **report,
        "freerange": "not_applicable",
        "text_layout": text_layout or {"status": "not_requested"},
    }


def _copy_artifact(artifact_path: Path, host_dir: Path) -> Path:
    copied = host_dir / "artifact"
    copied.mkdir()
    for name in ("index.html", "provenance_manifest.json"):
        shutil.copyfile(artifact_path / name, copied / name)
    manifest = json.loads((copied / "provenance_manifest.json").read_text(encoding="utf-8"))
    if file_hash(copied / "index.html") != manifest.get("artifact_hash"):
        raise HostVerifyFailure(
            "HOST_VERIFY_ARTIFACT_HASH_MISMATCH", "index.html hash does not match manifest artifact_hash."
        )
    return copied


def _install(host_dir: Path, *, install: bool, started: float, timings: dict[str, int]) -> dict[str, str]:
    node = _require_executable("node", "HOST_VERIFY_NODE_MISSING")
    npm = _require_executable("npm", "HOST_VERIFY_NPM_MISSING")
    versions = {
        "node": _run_process(
            [node, "--version"], cwd=host_dir, timeout_ms=5_000, code="HOST_VERIFY_NODE_MISSING"
        ).stdout.strip(),
        "npm": _run_process(
            [npm, "--version"], cwd=host_dir, timeout_ms=5_000, code="HOST_VERIFY_NPM_MISSING"
        ).stdout.strip(),
    }
    seed = os.environ.get(HTML_VERIFY_NODE_MODULES_ENV)
    if install and seed:
        _time_phase(
            timings,
            "install",
            lambda: _link_prebuilt_node_modules(
                host_dir,
                Path(seed),
                bins=HTML_VERIFY_NODE_MODULE_BINS,
                env_name=HTML_VERIFY_NODE_MODULES_ENV,
                # The tree hash refuses a symlinked package, so the one it pins is a real copy.
                copy_packages=frozenset({PRETEXT_PACKAGE}),
            ),
        )
    elif install:
        _time_phase(
            timings,
            "install",
            lambda: _run_process(
                [npm, "ci", "--ignore-scripts"],
                cwd=host_dir,
                timeout_ms=_remaining_timeout(started, HOST_VERIFY_PHASE_TIMEOUTS_MS["install"]),
                code="HOST_VERIFY_NPM_INSTALL_FAILED",
            ),
        )
    _assert_node_modules_dir(host_dir / "node_modules", bins=HTML_VERIFY_NODE_MODULE_BINS)
    return versions


def _pretext_scope(host_dir: Path, copied: Path) -> dict[str, Any]:
    validate_pretext_installation(host_dir)
    (host_dir / "pretext-probe.ts").write_text(
        generate_pretext_runtime_typescript() + "\ninstallViewSpecPretextProbe();\n", encoding="utf-8"
    )
    # A document is one screen at one route, which is the shape the React scope already has.
    return build_pretext_scope(
        {"routes": [{"id": "document", "path": "/", "screen_id": "document"}]},
        [{"id": "document", "paths": {"manifest": "provenance_manifest.json"}}],
        copied,
    )


def _write_config(
    host_dir: Path,
    browser_root: Path,
    copied: Path,
    *,
    target: str,
    plan: VerificationPlan,
    renders: Sequence[Mapping[str, Any]],
    pretext_scope: Mapping[str, Any] | None,
) -> Path:
    browser_root.mkdir()
    html = (copied / "index.html").read_text(encoding="utf-8")
    config = {
        "target": target,
        "artifact": str(copied / "index.html"),
        "manifest": str(copied / "provenance_manifest.json"),
        "plan": plan.to_json(),
        "reports_dir": str(browser_root / "browser-reports"),
        "evidence_dir": str(browser_root / "evidence"),
        "compiled_root": MCPAppArtifact(uri="", html=html).root if target == MCP_APP_TARGET else None,
        "renders": [
            {"name": f"render-{index}", "structuredContent": dict(render)} for index, render in enumerate(renders, 1)
        ],
        "pretext": {
            "scope": dict(pretext_scope),
            "probe": "pretext-probe.ts",
            "report": str(browser_root / _PRETEXT_REPORT),
        }
        if pretext_scope is not None and pretext_scope["status"] == "applicable"
        else None,
    }
    path = browser_root / "config.json"
    atomic_write(path, json.dumps(config))
    return path


def _run_browser(host_dir: Path, config: Path, *, started: float, timings: dict[str, int]) -> None:
    node = _require_executable("node", "HOST_VERIFY_NODE_MISSING")
    _time_phase(
        timings,
        "browser",
        lambda: _run_process(
            [node, "verify.mjs", str(config)],
            cwd=host_dir,
            timeout_ms=_remaining_timeout(started, HTML_VERIFY_BROWSER_TIMEOUT_MS),
            code="HOST_VERIFY_BROWSER_RUNTIME_ERROR",
        ),
    )


def _validate_pretext(report_path: Path, scope: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return the sanitized evidence, or the document's own failures as findings.

    The validator stops at its first failure, which is right for a contract but thin for a repair
    loop, so failing surfaces are read out first and each becomes a finding.
    """
    report = json.loads(report_path.read_text(encoding="utf-8"))
    items = report.get("items") if isinstance(report, dict) else None
    findings = [
        {
            "code": _PRETEXT_FINDING_CODES[item["reason"]],
            "severity": "error",
            "message": (
                f"{item['surface_id']} failed the Pretext text layout check ({item['reason']}): predicted "
                f"{item.get('predicted_line_count')} lines, observed {item.get('observed_line_count')}."
            ),
            "fix": _PRETEXT_FINDING_FIX,
            "source_ref": f"ir:{item['ir_id']}",
            "viewport": item["viewport_id"],
            "evidence_refs": [f"evidence/{_PRETEXT_REPORT}"],
        }
        for item in (items if isinstance(items, list) else [])
        if isinstance(item, dict) and item.get("status") == "failed" and item.get("reason") in _PRETEXT_FINDING_CODES
    ]
    if findings:
        return {"status": "failed", "findings": len(findings)}, findings
    evidence = validate_pretext_runtime_report(report_path, scope)
    return {key: evidence[key] for key in _PRETEXT_SUMMARY_FIELDS}, []


def _bounded_findings(diagnostics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the host report small while each kind of finding stays visible in each state of the view.

    One overflowing root can flag each node beneath it, and an oversized report would turn that
    plain failure into an unreadable one. A state is a screenshot, so the first evidence file tells
    the compiled document from its swap and each render. The evidence files keep what this drops.
    """
    seen: Counter[tuple[str, str | None, str | None]] = Counter()
    kept: list[dict[str, Any]] = []
    for item in diagnostics:
        refs = item.get("evidence_refs") or [None]
        key = (item["code"], item.get("viewport"), refs[0])
        seen[key] += 1
        if seen[key] <= HTML_VERIFY_MAX_FINDINGS_PER_KIND and len(kept) < HTML_VERIFY_MAX_FINDINGS:
            kept.append(item)
    omitted = len(diagnostics) - len(kept)
    if omitted and kept:
        first = kept[0]
        kept.append(
            {
                **first,
                "severity": "info",
                "source_ref": None,
                "viewport": None,
                "message": f"{omitted} further findings omitted; the evidence files hold the full record.",
                "fix": "Fix the reported findings, then rerun the proof.",
            }
        )
    return kept


__all__ = [
    "HTML_VERIFY_MAX_RENDERS",
    "HTML_VERIFY_NODE_MODULES_ENV",
    "HTML_VERIFY_TARGETS",
    "assert_html_proof_request",
    "html_artifact_target",
    "load_mcp_render",
    "verify_html_artifact_dir",
]
