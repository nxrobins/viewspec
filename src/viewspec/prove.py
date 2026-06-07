"""First-run proof orchestration for ViewSpec artifacts."""

from __future__ import annotations

import json
import platform
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from viewspec._version import __version__
from viewspec.host_verify import HOST_VERIFY_TARGET, verify_host_artifact_dir
from viewspec.intent_tools import INTENT_COMPILE_TARGETS, STARTER_INTENT_KINDS, compile_intent_bundle_file_tool, init_intent_file
from viewspec.local_tools import (
    MCP_RESULT_SCHEMA_VERSION,
    LocalToolError,
    atomic_write,
    file_hash,
    init_design_file,
    path_policy_metadata,
    resolve_cwd,
    resolve_local_path,
    tool_error_response,
    tool_response,
)


PROVE_SCHEMA_VERSION = 1
PROVE_DEFAULT_OUT = ".viewspec-proof"
PROVE_DEFAULT_REPORT = "proof_report.json"
PROVE_DEFAULT_SUMMARY = "PROOF.md"
PROVE_DEFAULT_SUPPORT_BUNDLE = "support_bundle.json"
PROVE_SUMMARY_MAX_BYTES = 32 * 1024
PROVE_SUPPORT_BUNDLE_MAX_BYTES = 16 * 1024
PROVE_ARTIFACT_DIR = "artifact"
PROVE_TARGETS = INTENT_COMPILE_TARGETS
PROOF_LEVEL_BY_TARGET = {
    "html-tailwind": "source_artifact",
    "react-tsx": "source_artifact",
    "react-tailwind-tsx": "react_tailwind_reference_host",
}
PROVE_NON_CLAIM = (
    "ViewSpec prove is not pixel-perfect visual regression, accessibility certification, "
    "arbitrary host-app certification, or hosted compiler publish automation."
)


class ProveFailure(ValueError):
    """Stable-code first proof failure."""

    def __init__(self, code: str, message: str, fix: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.fix = fix


@dataclass(frozen=True)
class _PreparedProof:
    output_dir: Path
    artifact_dir: Path
    intent_path: Path
    design_path: Path | None
    report_path: Path
    summary_path: Path
    intent_source: str
    design_source: str


def prove(
    *,
    intent_path: str | Path | None = None,
    out_dir: str | Path = PROVE_DEFAULT_OUT,
    design_path: str | Path | None = None,
    strict_design: bool = False,
    target: str = "html-tailwind",
    kind: str = "dashboard",
    install: bool = False,
    force: bool = False,
    report_out: str | Path | None = None,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    """Run a bounded first proof for a ViewSpec IntentBundle."""
    timings: dict[str, int] = {}
    root = resolve_cwd(cwd)
    output_dir = _resolve_output(out_dir, root)
    report_path = resolve_local_path(report_out, cwd=root, allow_outside_cwd=True) if report_out else output_dir / PROVE_DEFAULT_REPORT
    try:
        if target not in PROVE_TARGETS:
            raise ProveFailure(
                "PROVE_UNSUPPORTED_TARGET",
                f"Unsupported proof target: {target}",
                "Use html-tailwind, react-tsx, or react-tailwind-tsx.",
            )
        if kind not in STARTER_INTENT_KINDS:
            raise ProveFailure(
                "PROVE_UNSUPPORTED_KIND",
                f"Unsupported starter kind: {kind}",
                f"Use one of: {', '.join(STARTER_INTENT_KINDS)}.",
            )
        _prepare_output_dir(output_dir, root=root, force=force, raw_out=out_dir)
        prepared = _time_phase(
            timings,
            "prepare",
            lambda: _prepare_inputs(
                output_dir,
                root=root,
                intent_path=intent_path,
                design_path=design_path,
                kind=kind,
                report_path=report_path,
            ),
        )
        compiled = _time_phase(
            timings,
            "compile_check",
            lambda: compile_intent_bundle_file_tool(
                prepared.intent_path,
                prepared.artifact_dir,
                design_path=prepared.design_path,
                strict_design=strict_design,
                target=target,
                cwd=root,
                allow_outside_cwd=True,
            ),
        )
        if not compiled.get("ok"):
            report = _report(
                ok=False,
                target=target,
                prepared=prepared,
                timings=timings,
                checks=_checks(prepared, target=target, compile_check="failed"),
                errors=_normalize_errors(compiled.get("errors"), fallback_code="PROVE_COMPILE_FAILED"),
                metadata={"sdk_version": __version__, "network_calls": "none"},
            )
            return _write_report(report, prepared.report_path)

        manifest_path = prepared.artifact_dir / "provenance_manifest.json"
        artifact_path = _artifact_path(prepared.artifact_dir, target)
        host_report: dict[str, Any] | None = None
        errors: list[dict[str, str]] = []
        checks = _checks(prepared, target=target, compile_check="passed")
        if target == HOST_VERIFY_TARGET:
            host_report = _time_phase(
                timings,
                "host_verify",
                lambda: verify_host_artifact_dir(prepared.artifact_dir, target=target, install=install),
            )
            checks["host_verify"] = "passed" if host_report.get("ok") else "failed"
            if not host_report.get("ok"):
                errors = _normalize_errors(host_report.get("errors"), fallback_code="HOST_VERIFY_BROWSER_RUNTIME_ERROR")
        report = _report(
            ok=not errors,
            target=target,
            prepared=prepared,
            timings=timings,
            checks=checks,
            artifact_hash=file_hash(artifact_path) if artifact_path.exists() else None,
            manifest_hash=file_hash(manifest_path) if manifest_path.exists() else None,
            host_report=host_report,
            errors=errors,
            metadata={
                "sdk_version": __version__,
                "network_calls": "npm_ci_opt_in" if target == HOST_VERIFY_TARGET and install else "none",
                "install_used": bool(install),
                "strict_design": bool(strict_design),
            },
        )
        return _write_report(report, prepared.report_path)
    except ProveFailure as exc:
        report = _report(
            ok=False,
            target=target if target in PROVE_TARGETS else "unknown",
            prepared=None,
            output_dir=output_dir,
            report_path=report_path,
            timings=timings,
            checks={"prepare": "failed"},
            errors=[{"code": exc.code, "message": exc.message, "fix": exc.fix}],
            metadata={"sdk_version": __version__, "network_calls": "none"},
        )
        if report_out is None and exc.code == "PROVE_OUTPUT_UNSAFE":
            return _final_report(report, report_path)
        return _write_report(report, report_path)
    except Exception as exc:
        report = _report(
            ok=False,
            target=target if target in PROVE_TARGETS else "unknown",
            prepared=None,
            output_dir=output_dir,
            report_path=report_path,
            timings=timings,
            checks={"prepare": "failed"},
            errors=[
                {
                    "code": "PROVE_INTERNAL_ERROR",
                    "message": str(exc),
                    "fix": "Fix the local environment or paths and retry viewspec prove.",
                }
            ],
            metadata={"sdk_version": __version__, "network_calls": "none"},
        )
        if report_out is None and not output_dir.exists():
            return _final_report(report, report_path)
        return _write_report(report, report_path)


def prove_tool(
    *,
    intent_path: str | Path | None = None,
    out_dir: str | Path = PROVE_DEFAULT_OUT,
    design_path: str | Path | None = None,
    strict_design: bool = False,
    target: str = "html-tailwind",
    kind: str = "dashboard",
    install: bool = False,
    force: bool = False,
    report_out: str | Path | None = None,
    cwd: str | Path | None = None,
    allow_outside_cwd: bool = False,
) -> dict[str, Any]:
    """MCP/native tool wrapper for first-run proof orchestration."""
    root: Path | None = None
    try:
        root = resolve_cwd(cwd)
        resolved_intent = (
            resolve_local_path(intent_path, cwd=root, allow_outside_cwd=allow_outside_cwd, must_exist=True)
            if intent_path is not None
            else None
        )
        resolved_design = (
            resolve_local_path(design_path, cwd=root, allow_outside_cwd=allow_outside_cwd, must_exist=True)
            if design_path is not None
            else None
        )
        resolved_out = resolve_local_path(out_dir, cwd=root, allow_outside_cwd=allow_outside_cwd)
        resolved_report = (
            resolve_local_path(report_out, cwd=root, allow_outside_cwd=allow_outside_cwd)
            if report_out is not None
            else None
        )
        proof = prove(
            intent_path=resolved_intent,
            out_dir=resolved_out,
            design_path=resolved_design,
            strict_design=strict_design,
            target=target,
            kind=kind,
            install=install,
            force=force,
            report_out=resolved_report,
            cwd=root,
        )
        return _tool_from_report(proof, root, allow_outside_cwd)
    except Exception as exc:
        if isinstance(exc, LocalToolError):
            return tool_error_response(exc.code, exc.message, exc.fix, metadata=path_policy_metadata(root, allow_outside_cwd))
        return tool_error_response(
            "PROVE_INTERNAL_ERROR",
            str(exc),
            "Fix the first proof paths or local environment and retry prove.",
            metadata=path_policy_metadata(root, allow_outside_cwd),
        )


def _resolve_output(out_dir: str | Path, root: Path) -> Path:
    return resolve_local_path(out_dir, cwd=root, allow_outside_cwd=True)


def _prepare_output_dir(output_dir: Path, *, root: Path, force: bool, raw_out: str | Path) -> None:
    _assert_safe_output(output_dir, root=root, raw_out=raw_out)
    if output_dir.exists():
        if not force:
            raise ProveFailure(
                "PROVE_OUTPUT_EXISTS",
                f"Proof output already exists: {output_dir}",
                "Pass --force or choose a new --out directory.",
            )
        if not output_dir.is_dir():
            raise ProveFailure(
                "PROVE_OUTPUT_UNSAFE",
                f"Proof output is not a directory: {output_dir}",
                "Choose a dedicated proof output directory.",
            )
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)


def _assert_safe_output(output_dir: Path, *, root: Path, raw_out: str | Path) -> None:
    raw_parts = [str(part) for part in Path(raw_out).parts]
    if ".." in raw_parts:
        raise ProveFailure("PROVE_OUTPUT_UNSAFE", "Proof output path must not contain parent traversal.", "Use a direct child output path.")
    resolved = output_dir.resolve()
    home = Path.home().resolve()
    repo_root = _repo_root(root)
    drive_root = Path(resolved.anchor).resolve() if resolved.anchor else resolved
    blocked = {root.resolve(), repo_root, home, drive_root}
    if resolved in blocked:
        raise ProveFailure(
            "PROVE_OUTPUT_UNSAFE",
            f"Refusing unsafe proof output directory: {resolved}",
            "Use a dedicated proof output directory such as .viewspec-proof.",
        )
    for parent in (root.resolve(), repo_root, home):
        if _is_parent(resolved, parent):
            raise ProveFailure(
                "PROVE_OUTPUT_UNSAFE",
                f"Refusing proof output that is a parent of a protected directory: {resolved}",
                "Use a dedicated child output directory.",
            )


def _repo_root(root: Path) -> Path:
    current = root.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return current


def _is_parent(path: Path, child: Path) -> bool:
    try:
        child.relative_to(path)
    except ValueError:
        return False
    return path != child


def _prepare_inputs(
    output_dir: Path,
    *,
    root: Path,
    intent_path: str | Path | None,
    design_path: str | Path | None,
    kind: str,
    report_path: Path,
) -> _PreparedProof:
    if intent_path is None:
        intent = output_dir / "viewspec.intent.json"
        init_intent_file(intent, kind=kind, force=False)
        intent_source = "generated"
    else:
        intent = resolve_local_path(intent_path, cwd=root, allow_outside_cwd=True, must_exist=True)
        intent_source = "provided"
    if design_path is None:
        design = output_dir / "DESIGN.md"
        init_design_file(design, force=False)
        design_source = "generated"
    else:
        design = resolve_local_path(design_path, cwd=root, allow_outside_cwd=True, must_exist=True)
        design_source = "provided"
    return _PreparedProof(
        output_dir=output_dir,
        artifact_dir=output_dir / PROVE_ARTIFACT_DIR,
        intent_path=intent,
        design_path=design,
        report_path=report_path,
        summary_path=output_dir / PROVE_DEFAULT_SUMMARY,
        intent_source=intent_source,
        design_source=design_source,
    )


def _artifact_path(artifact_dir: Path, target: str) -> Path:
    return artifact_dir / ("ViewSpecView.tsx" if target in {"react-tsx", "react-tailwind-tsx"} else "index.html")


def _checks(prepared: _PreparedProof, *, target: str, compile_check: str) -> dict[str, str]:
    return {
        "intent": prepared.intent_source,
        "design": prepared.design_source,
        "compile": compile_check,
        "artifact_check": "passed" if compile_check == "passed" else "failed",
        "host_verify": "not_applicable" if target != HOST_VERIFY_TARGET else "pending",
        "proof_summary": "pending",
    }


def _report(
    *,
    ok: bool,
    target: str,
    prepared: _PreparedProof | None = None,
    output_dir: Path | None = None,
    report_path: Path | None = None,
    timings: dict[str, int],
    checks: dict[str, str],
    artifact_hash: str | None = None,
    manifest_hash: str | None = None,
    host_report: dict[str, Any] | None = None,
    errors: list[dict[str, str]],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    paths = {
        "proof_dir": str(prepared.output_dir if prepared else output_dir),
        "intent": str(prepared.intent_path) if prepared else None,
        "design": str(prepared.design_path) if prepared and prepared.design_path else None,
        "artifact_dir": str(prepared.artifact_dir) if prepared else None,
        "artifact": str(_artifact_path(prepared.artifact_dir, target)) if prepared and target in PROVE_TARGETS else None,
        "manifest": str(prepared.artifact_dir / "provenance_manifest.json") if prepared else None,
        "diagnostics": str(prepared.artifact_dir / "diagnostics.json") if prepared else None,
        "report": str(prepared.report_path if prepared else report_path),
        "proof_summary": str(prepared.summary_path if prepared else output_dir / PROVE_DEFAULT_SUMMARY if output_dir else None),
        "support_bundle": str(
            prepared.output_dir / PROVE_DEFAULT_SUPPORT_BUNDLE
            if prepared
            else output_dir / PROVE_DEFAULT_SUPPORT_BUNDLE
            if output_dir
            else None
        ),
    }
    return {
        "schema_version": PROVE_SCHEMA_VERSION,
        "ok": ok,
        "proof_level": PROOF_LEVEL_BY_TARGET.get(target, "unknown"),
        "target": target,
        "paths": paths,
        "checks": checks,
        "artifact_hash": artifact_hash,
        "manifest_hash": manifest_hash,
        "host_report": host_report,
        "errors": errors,
        "metadata": metadata,
        "timings_ms": dict(sorted(timings.items())),
    }


def _write_report(report: dict[str, Any], report_path: Path) -> dict[str, Any]:
    report = _final_report(report, report_path)
    report.setdefault("checks", {})["support_bundle"] = "written"
    report.setdefault("checks", {})["proof_summary"] = "written"
    atomic_write(report_path, json.dumps(report, indent=2, sort_keys=True) + "\n")
    try:
        support_path = report.get("paths", {}).get("support_bundle")
        if support_path:
            _write_support_bundle(Path(str(support_path)), _render_support_bundle(report, proof_report_hash=file_hash(report_path)), report=report)
    except Exception as exc:
        failed = _support_failure_report(report, report_path, exc)
        atomic_write(report_path, json.dumps(failed, indent=2, sort_keys=True) + "\n")
        try:
            _write_failed_summary(failed, report_path)
        except Exception as summary_exc:
            failed = _summary_failure_report(failed, report_path, summary_exc)
            atomic_write(report_path, json.dumps(failed, indent=2, sort_keys=True) + "\n")
        return failed
    try:
        summary_path = report.get("paths", {}).get("proof_summary")
        if summary_path:
            report_hash = file_hash(report_path)
            _write_proof_summary(Path(str(summary_path)), _render_proof_summary(report, proof_report_hash=report_hash))
    except Exception as exc:
        failed = _summary_failure_report(report, report_path, exc)
        atomic_write(report_path, json.dumps(failed, indent=2, sort_keys=True) + "\n")
        try:
            support_path = failed.get("paths", {}).get("support_bundle")
            if support_path:
                _write_support_bundle(
                    Path(str(support_path)),
                    _render_support_bundle(failed, proof_report_hash=file_hash(report_path)),
                    report=failed,
                )
        except Exception as support_exc:
            failed = _support_failure_report(failed, report_path, support_exc)
            atomic_write(report_path, json.dumps(failed, indent=2, sort_keys=True) + "\n")
        return failed
    return report


def _final_report(report: dict[str, Any], report_path: Path) -> dict[str, Any]:
    timings = report.get("timings_ms")
    if isinstance(timings, dict) and "total" not in timings:
        timings["total"] = sum(value for value in timings.values() if isinstance(value, int))
    report["paths"]["report"] = str(report_path)
    return report


def _normalize_errors(errors: object, *, fallback_code: str) -> list[dict[str, str]]:
    if not isinstance(errors, list) or not errors:
        return [{"code": fallback_code, "message": "ViewSpec proof failed.", "fix": "Inspect proof_report.json and retry."}]
    normalized: list[dict[str, str]] = []
    for item in errors:
        if isinstance(item, dict):
            normalized.append(
                {
                    "code": str(item.get("code") or fallback_code),
                    "message": str(item.get("message") or "ViewSpec proof failed."),
                    "fix": str(item.get("fix") or "Inspect proof_report.json and retry."),
                }
            )
        else:
            normalized.append({"code": fallback_code, "message": str(item), "fix": "Inspect proof_report.json and retry."})
    return normalized


def _summary_failure_report(report: dict[str, Any], report_path: Path, exc: Exception) -> dict[str, Any]:
    failed = json.loads(json.dumps(report))
    failed["ok"] = False
    failed.setdefault("checks", {})["proof_summary"] = "failed"
    message = exc.message if isinstance(exc, ProveFailure) else str(exc)
    failed_errors = _normalize_errors(failed.get("errors"), fallback_code="PROVE_INTERNAL_ERROR") if failed.get("errors") else []
    failed_errors.append(
        {
            "code": "PROVE_SUMMARY_WRITE_FAILED",
            "message": message or "Could not write PROOF.md.",
            "fix": "Inspect proof_report.json and retry viewspec prove with a writable proof output directory.",
        }
    )
    failed["errors"] = failed_errors
    return _final_report(failed, report_path)


def _support_failure_report(report: dict[str, Any], report_path: Path, exc: Exception) -> dict[str, Any]:
    failed = json.loads(json.dumps(report))
    failed["ok"] = False
    failed.setdefault("checks", {})["support_bundle"] = "failed"
    code = exc.code if isinstance(exc, ProveFailure) else "PROVE_SUPPORT_BUNDLE_WRITE_FAILED"
    message = exc.message if isinstance(exc, ProveFailure) else str(exc)
    failed_errors = _normalize_errors(failed.get("errors"), fallback_code="PROVE_INTERNAL_ERROR") if failed.get("errors") else []
    failed_errors.append(
        {
            "code": code,
            "message": message or "Could not write support_bundle.json.",
            "fix": "Inspect proof_report.json and retry viewspec prove with a writable proof output directory.",
        }
    )
    failed["errors"] = failed_errors
    return _final_report(failed, report_path)


def _write_failed_summary(report: dict[str, Any], report_path: Path) -> None:
    summary_path = report.get("paths", {}).get("proof_summary")
    if summary_path:
        _write_proof_summary(Path(str(summary_path)), _render_proof_summary(report, proof_report_hash=file_hash(report_path)))


def _write_proof_summary(summary_path: Path, markdown: str) -> None:
    byte_count = len(markdown.encode("utf-8"))
    if byte_count > PROVE_SUMMARY_MAX_BYTES:
        raise ProveFailure(
            "PROVE_SUMMARY_WRITE_FAILED",
            f"Proof summary exceeds {PROVE_SUMMARY_MAX_BYTES} bytes.",
            "Inspect proof_report.json for machine-readable details.",
        )
    atomic_write(summary_path, markdown)


def _write_support_bundle(support_path: Path, payload: str, *, report: dict[str, Any]) -> None:
    byte_count = len(payload.encode("utf-8"))
    if byte_count > PROVE_SUPPORT_BUNDLE_MAX_BYTES:
        raise ProveFailure(
            "PROVE_SUPPORT_BUNDLE_WRITE_FAILED",
            f"Support bundle exceeds {PROVE_SUPPORT_BUNDLE_MAX_BYTES} bytes.",
            "Inspect proof_report.json for machine-readable details.",
        )
    _assert_support_bundle_redacted(payload, report)
    atomic_write(support_path, payload)


def _render_support_bundle(report: dict[str, Any], *, proof_report_hash: str) -> str:
    bundle = _support_bundle_payload(report, proof_report_hash=proof_report_hash)
    return json.dumps(bundle, indent=2, sort_keys=True) + "\n"


def _support_bundle_payload(report: dict[str, Any], *, proof_report_hash: str) -> dict[str, Any]:
    checks = report.get("checks", {}) if isinstance(report.get("checks"), dict) else {}
    errors = report.get("errors", []) if isinstance(report.get("errors"), list) else []
    metadata = report.get("metadata", {}) if isinstance(report.get("metadata"), dict) else {}
    timings = report.get("timings_ms", {}) if isinstance(report.get("timings_ms"), dict) else {}
    host_report = report.get("host_report") if isinstance(report.get("host_report"), dict) else None
    return {
        "schema_version": 1,
        "kind": "viewspec_proof_support_bundle",
        "ok": bool(report.get("ok")),
        "target": report.get("target"),
        "proof_level": report.get("proof_level"),
        "proof_report_hash": proof_report_hash,
        "artifact_hash": report.get("artifact_hash"),
        "manifest_hash": report.get("manifest_hash"),
        "checks": {str(key): _support_scalar(value) for key, value in checks.items()},
        "errors": _support_errors(errors),
        "metadata": {
            "sdk_version": _support_scalar(metadata.get("sdk_version")),
            "network_calls": _support_scalar(metadata.get("network_calls")),
            "install_used": bool(metadata.get("install_used")),
            "strict_design": bool(metadata.get("strict_design")),
            "python_version": platform.python_version(),
            "platform": platform.system(),
            "executable": Path(sys.executable).name,
        },
        "paths": _support_path_hints(report),
        "host_report": _support_host_report(host_report),
        "timings_ms": {str(key): int(value) for key, value in timings.items() if isinstance(value, int)},
        "privacy": {
            "local_only": True,
            "contains_raw_intent": False,
            "contains_raw_design": False,
            "contains_raw_artifact": False,
            "contains_raw_diagnostics": False,
            "contains_absolute_paths": False,
            "contains_environment_variables": False,
            "contains_credentials": False,
        },
        "next_actions": _support_next_actions(report),
    }


def _support_errors(errors: list[object]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for error in errors[:16]:
        if isinstance(error, dict):
            normalized.append(
                {
                    "code": _support_scalar(error.get("code")),
                    "fix": _support_scalar(error.get("fix")),
                }
            )
        else:
            normalized.append({"code": "PROVE_ERROR", "fix": "Inspect proof_report.json and retry."})
    return normalized


def _support_host_report(host_report: dict[str, Any] | None) -> dict[str, Any] | None:
    if host_report is None:
        return None
    assertions = host_report.get("assertions") if isinstance(host_report.get("assertions"), dict) else {}
    return {
        "ok": bool(host_report.get("ok")),
        "artifact_hash": host_report.get("artifact_hash"),
        "manifest_hash": host_report.get("manifest_hash"),
        "diagnostics_hash": host_report.get("diagnostics_hash"),
        "host_template_lock_hash": host_report.get("host_template_lock_hash"),
        "install_used": bool(host_report.get("install_used")),
        "node_version": _support_scalar(host_report.get("node_version")),
        "npm_version": _support_scalar(host_report.get("npm_version")),
        "assertions": {str(key): int(value) for key, value in assertions.items() if isinstance(value, int)},
        "error_codes": [str(error.get("code")) for error in host_report.get("errors", []) if isinstance(error, dict) and error.get("code")],
    }


def _support_path_hints(report: dict[str, Any]) -> dict[str, str]:
    paths = report.get("paths", {}) if isinstance(report.get("paths"), dict) else {}
    hints: dict[str, str] = {}
    for key in ("proof_dir", "intent", "design", "artifact_dir", "artifact", "manifest", "diagnostics", "report", "proof_summary", "support_bundle"):
        value = paths.get(key)
        if value:
            hints[f"{key}_name"] = Path(str(value)).name
    return hints


def _support_next_actions(report: dict[str, Any]) -> list[str]:
    if report.get("ok"):
        return ["Keep support_bundle.json with proof_report.json for local audit records."]
    codes = [str(error.get("code")) for error in report.get("errors", []) if isinstance(error, dict) and error.get("code")]
    if codes:
        return [f"Fix {codes[0]} and rerun viewspec prove."]
    return ["Inspect proof_report.json and rerun viewspec prove."]


def _support_scalar(value: object) -> str:
    if value is None:
        return "not_recorded"
    return str(value).replace("\r", " ").replace("\n", " ").replace("`", "'")[:256]


def _assert_support_bundle_redacted(payload: str, report: dict[str, Any]) -> None:
    paths = report.get("paths", {}) if isinstance(report.get("paths"), dict) else {}
    for value in paths.values():
        if not value:
            continue
        raw = str(value)
        escaped = json.dumps(raw)[1:-1]
        if (":" in raw or "\\" in raw or "/" in raw) and (raw in payload or escaped in payload):
            raise ProveFailure(
                "PROVE_SUPPORT_BUNDLE_CONTENT_FORBIDDEN",
                "Support bundle included an absolute or structured local path.",
                "Inspect proof_report.json locally instead of sharing a support bundle with path content.",
            )


def _render_proof_summary(report: dict[str, Any], *, proof_report_hash: str) -> str:
    paths = report.get("paths", {}) if isinstance(report.get("paths"), dict) else {}
    checks = report.get("checks", {}) if isinstance(report.get("checks"), dict) else {}
    metadata = report.get("metadata", {}) if isinstance(report.get("metadata"), dict) else {}
    timings = report.get("timings_ms", {}) if isinstance(report.get("timings_ms"), dict) else {}
    errors = report.get("errors", []) if isinstance(report.get("errors"), list) else []
    target = str(report.get("target") or "unknown")
    proof_level = str(report.get("proof_level") or "unknown")
    status = "PASSED" if report.get("ok") else "FAILED"
    claim = _proof_claim(report)

    lines = [
        "# ViewSpec Proof",
        "",
        f"Status: **{status}**",
        f"Target: `{_summary_value(target)}`",
        f"Proof level: `{_summary_value(proof_level)}`",
        f"Claim: {_summary_value(claim)}",
        f"Non-claim: {PROVE_NON_CLAIM}",
        "",
        "## Inputs And Outputs",
        "",
        f"- Intent source: `{_summary_value(checks.get('intent'))}`",
        f"- Intent path: `{_summary_value(paths.get('intent'))}`",
        f"- Design source: `{_summary_value(checks.get('design'))}`",
        f"- Design path: `{_summary_value(paths.get('design'))}`",
        f"- Artifact path: `{_summary_value(paths.get('artifact'))}`",
        f"- Manifest path: `{_summary_value(paths.get('manifest'))}`",
        f"- JSON report path: `{_summary_value(paths.get('report'))}`",
        f"- Human summary path: `{_summary_value(paths.get('proof_summary'))}`",
        f"- Redacted support bundle path: `{_summary_value(paths.get('support_bundle'))}`",
        "",
        "## Hashes",
        "",
        f"- Artifact SHA-256: `{_summary_value(report.get('artifact_hash'))}`",
        f"- Manifest SHA-256: `{_summary_value(report.get('manifest_hash'))}`",
        f"- Proof report SHA-256: `{_summary_value(proof_report_hash)}`",
        "",
        "## Checks",
        "",
    ]
    for key in sorted(checks):
        lines.append(f"- {_summary_value(key)}: `{_summary_value(checks[key])}`")

    host_report = report.get("host_report")
    if target == HOST_VERIFY_TARGET:
        lines.extend(["", "## Host Verification", ""])
        if isinstance(host_report, dict):
            lines.append(f"- Status: `{'passed' if host_report.get('ok') else 'failed'}`")
            assertions = host_report.get("assertions")
            if isinstance(assertions, dict):
                for key in sorted(assertions):
                    lines.append(f"- Assertion {_summary_value(key)}: `{_summary_value(assertions[key])}`")
        else:
            lines.append("- Status: `not_run`")

    lines.extend(
        [
            "",
            "## Policy",
            "",
            f"- Network/install policy: `{_summary_value(metadata.get('network_calls'))}`",
            f"- npm install used: `{_summary_value(metadata.get('install_used'))}`",
            f"- Strict design: `{_summary_value(metadata.get('strict_design'))}`",
            "",
            "## Timings",
            "",
        ]
    )
    for key in sorted(timings):
        lines.append(f"- {_summary_value(key)}: `{_summary_value(timings[key])}ms`")

    lines.extend(["", "## Errors", ""])
    if errors:
        for error in errors:
            if isinstance(error, dict):
                lines.append(f"- `{_summary_value(error.get('code'))}`: {_summary_value(error.get('message'))}")
                lines.append(f"  Fix: {_summary_value(error.get('fix'))}")
            else:
                lines.append(f"- {_summary_value(error)}")
    else:
        lines.append("- None")
    lines.append("")
    return "\n".join(lines)


def _proof_claim(report: dict[str, Any]) -> str:
    target = report.get("target")
    if target == HOST_VERIFY_TARGET and report.get("ok") and isinstance(report.get("host_report"), dict) and report["host_report"].get("ok"):
        return "Bounded React/Vite/Tailwind reference-host proof for this generated artifact."
    return "Source artifact integrity and provenance proof for this generated artifact."


def _summary_value(value: object) -> str:
    if value is None:
        return "not_recorded"
    return str(value).replace("\r", " ").replace("\n", " ").replace("`", "'")


def _time_phase(timings: dict[str, int], phase: str, fn: Any) -> Any:
    started = time.perf_counter()
    try:
        return fn()
    finally:
        timings[phase] = timings.get(phase, 0) + int((time.perf_counter() - started) * 1000)


def _tool_from_report(proof: dict[str, Any], root: Path | None, allow_outside_cwd: bool) -> dict[str, Any]:
    errors = _normalize_errors(proof.get("errors"), fallback_code="PROVE_INTERNAL_ERROR") if not proof.get("ok") else []
    return tool_response(
        bool(proof.get("ok")),
        "ViewSpec proof passed." if proof.get("ok") else "ViewSpec proof failed.",
        paths={key: str(value) for key, value in proof.get("paths", {}).items() if value},
        errors=errors,
        next_actions=[] if proof.get("ok") else ["Fix the reported proof issue and retry prove."],
        metadata={
            **path_policy_metadata(root, allow_outside_cwd),
            "schema_version": MCP_RESULT_SCHEMA_VERSION,
            "target": proof.get("target"),
            "proof_level": proof.get("proof_level"),
            "network_calls": proof.get("metadata", {}).get("network_calls", "none"),
        },
        data={"proof_report": proof},
    )


__all__ = [
    "PROVE_DEFAULT_OUT",
    "PROVE_SCHEMA_VERSION",
    "PROVE_TARGETS",
    "ProveFailure",
    "prove",
    "prove_tool",
]
