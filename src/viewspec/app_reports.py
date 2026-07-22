"""AppBundle proof and shell report helpers."""

from __future__ import annotations

import json
import platform
import sys
from pathlib import Path
from typing import Any

from viewspec._version import __version__
from viewspec.app_diff import _validation_summary
from viewspec.app_errors import AppBundleProofFailure, _normalize_proof_errors
from viewspec.app_prepared import _PreparedAppProof, _PreparedAppShell
from viewspec.app_react import REACT_APP_ROUTE_NAVIGATION, REACT_APP_TARGET
from viewspec.app_shell import (
    APP_SHELL_DIAGNOSTICS,
    APP_SHELL_INDEX,
    APP_SHELL_MANIFEST,
    APP_SHELL_ROUTE_NAVIGATION,
    APP_SHELL_TARGET,
    _screen_shell_summaries,
    _shell_route_assertions,
)
from viewspec.app_state_artifacts import _state_conformance_status
from viewspec.app_validation import (
    APP_BUNDLE_MAX_PROOF_REPORT_BYTES,
    APP_BUNDLE_MAX_SUPPORT_BUNDLE_BYTES,
    _app_schema_version,
    _app_summary,
    _resource_binding_fields_from_validation,
    _resource_binding_report_fields,
)
from viewspec.local_tools import atomic_write, file_hash

APP_BUNDLE_PROOF_SCHEMA_VERSION = 1
APP_BUNDLE_DEFAULT_OUT = ".viewspec-app-proof"
APP_BUNDLE_DEFAULT_REPORT = "app_proof_report.json"
APP_BUNDLE_DEFAULT_SUMMARY = "APP_PROOF.md"
APP_BUNDLE_DEFAULT_SUPPORT_BUNDLE = "app_support_bundle.json"
APP_BUNDLE_PROOF_LEVEL = "app_contract_source_artifacts"
APP_REACT_PROOF_LEVEL = "react_app_reference_host"
APP_BUNDLE_TARGET = "html-tailwind"
APP_BUNDLE_MAX_SUMMARY_BYTES = 32 * 1024


def _app_proof_report(
    *,
    ok: bool,
    prepared: _PreparedAppProof,
    app_payload: dict[str, Any],
    validation: dict[str, Any],
    route_assertions: dict[str, bool],
    screen_reports: list[dict[str, Any]],
    errors: list[dict[str, str]],
    timings: dict[str, int],
    strict_design: bool,
    shell: dict[str, Any] | None = None,
    react_app: dict[str, Any] | None = None,
    host_report: dict[str, Any] | None = None,
    install: bool = False,
    freerange: bool = False,
    pretext: bool = False,
    resource_binding_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    paths = {
        "proof_dir": str(prepared.output_dir),
        "app": str(prepared.app_path),
        "design": str(prepared.design_path) if prepared.design_path else None,
        "report": str(prepared.report_path),
        "proof_summary": str(prepared.summary_path),
        "support_bundle": str(prepared.support_path),
    }
    if isinstance(shell, dict) and isinstance(shell.get("paths"), dict):
        shell_paths = shell["paths"]
        if shell_paths.get("output_dir"):
            paths["app_shell"] = str(shell_paths["output_dir"])
        if shell_paths.get("index"):
            paths["app_shell_index"] = str(shell_paths["index"])
        if shell_paths.get("manifest"):
            paths["app_shell_manifest"] = str(shell_paths["manifest"])
        if shell_paths.get("diagnostics"):
            paths["app_shell_diagnostics"] = str(shell_paths["diagnostics"])
        if shell_paths.get("state_reducer"):
            paths["app_state_reducer"] = str(shell_paths["state_reducer"])
        if shell_paths.get("state_manifest"):
            paths["app_state_manifest"] = str(shell_paths["state_manifest"])
    if isinstance(react_app, dict) and isinstance(react_app.get("paths"), dict):
        react_paths = react_app["paths"]
        paths["react_app"] = str(react_paths.get("output_dir"))
        paths["react_app_entry"] = str(react_paths.get("app"))
        paths["react_app_manifest"] = str(react_paths.get("manifest"))
        paths["react_app_diagnostics"] = str(react_paths.get("diagnostics"))
        if react_paths.get("state_reducer"):
            paths["app_state_reducer"] = str(react_paths["state_reducer"])
        if react_paths.get("state_manifest"):
            paths["app_state_manifest"] = str(react_paths["state_manifest"])
    combined_route_assertions = dict(route_assertions)
    if isinstance(shell, dict) and isinstance(shell.get("route_assertions"), dict):
        combined_route_assertions.update(shell["route_assertions"])
    if isinstance(react_app, dict) and isinstance(react_app.get("route_assertions"), dict):
        combined_route_assertions.update(react_app["route_assertions"])
    binding_fields = _resource_binding_report_fields(app_payload, resource_binding_report)
    artifact_report = react_app if isinstance(react_app, dict) else shell
    is_react = isinstance(react_app, dict)
    static_analysis = (
        host_report.get("static_analysis")
        if isinstance(host_report, dict) and isinstance(host_report.get("static_analysis"), dict)
        else None
    )
    text_layout = (
        host_report.get("text_layout")
        if isinstance(host_report, dict) and isinstance(host_report.get("text_layout"), dict)
        else None
    )
    analyses = (
        host_report.get("analyses")
        if isinstance(host_report, dict) and isinstance(host_report.get("analyses"), dict)
        else {}
    )
    host_policy = (
        host_report.get("policy")
        if isinstance(host_report, dict) and isinstance(host_report.get("policy"), dict)
        else {}
    )
    install_command = (
        str(host_policy.get("install_command") or "npm ci --ignore-scripts") if is_react and install else "none"
    )
    return {
        "schema_version": APP_BUNDLE_PROOF_SCHEMA_VERSION,
        "app_schema_version": _app_schema_version(app_payload),
        "ok": ok,
        "proof_level": APP_REACT_PROOF_LEVEL if is_react else APP_BUNDLE_PROOF_LEVEL,
        "target": REACT_APP_TARGET if is_react else APP_SHELL_TARGET if shell else APP_BUNDLE_TARGET,
        "app": _app_summary(app_payload),
        "paths": paths,
        "route_assertions": combined_route_assertions,
        **(
            {"route_navigation": REACT_APP_ROUTE_NAVIGATION}
            if is_react
            else {"route_navigation": APP_SHELL_ROUTE_NAVIGATION}
            if shell
            else {}
        ),
        **binding_fields,
        "screens": screen_reports,
        **({"shell": _compact_shell_report(shell)} if shell else {}),
        **({"react_app": _compact_react_app_report(react_app)} if is_react else {}),
        **({"host_report": host_report} if isinstance(host_report, dict) else {}),
        **({"static_analysis": static_analysis} if static_analysis is not None else {}),
        **({"text_layout": text_layout} if text_layout is not None else {}),
        **({"analyses": analyses} if analyses else {}),
        **({"app_artifact_hash": react_app.get("app_artifact_hash")} if is_react else {}),
        **({"manifest_hash": react_app.get("manifest_hash")} if is_react else {}),
        **({"shell_artifact_hash": shell.get("shell_artifact_hash")} if shell else {}),
        **({"shell_manifest_hash": shell.get("shell_manifest_hash")} if shell else {}),
        **(
            {"state_reducer_hash": artifact_report.get("state_reducer_hash")}
            if artifact_report and artifact_report.get("state_reducer_hash")
            else {}
        ),
        **(
            {"state_manifest_hash": artifact_report.get("state_manifest_hash")}
            if artifact_report and artifact_report.get("state_manifest_hash")
            else {}
        ),
        **(
            {"state_contract_hash": artifact_report.get("state_contract_hash")}
            if artifact_report and artifact_report.get("state_contract_hash")
            else {}
        ),
        **(
            {"state_replay": artifact_report.get("state_replay")}
            if artifact_report and artifact_report.get("state_replay")
            else {}
        ),
        **(
            {"state_reducer_conformance": artifact_report.get("state_reducer_conformance")}
            if artifact_report and artifact_report.get("state_reducer_conformance")
            else {}
        ),
        "validation": _validation_summary(validation),
        "policy": {
            "network_calls": ("package_install_only" if install_command == "npm ci --ignore-scripts" else "none"),
            "install": bool(install) if is_react else False,
            "install_command": install_command,
            "freerange": "requested" if is_react and freerange else "not_requested",
            **({"pretext": "requested"} if is_react and pretext else {}),
        },
        "metadata": {
            "sdk_version": __version__,
            "strict_design": bool(strict_design),
            "screen_source": "embedded_intents",
            **({"shell_kind": "static_local_hash_shell"} if shell else {}),
            **({"app_kind": "runnable_vite_react_tailwind"} if is_react else {}),
        },
        "errors": errors,
        "timings_ms": _final_timings(timings),
    }


def _app_proof_failure_report(
    *,
    output_dir: Path,
    report_path: Path,
    errors: list[dict[str, str]],
    timings: dict[str, int],
    validation: dict[str, Any] | None,
    write: bool,
    target: str = APP_BUNDLE_TARGET,
) -> dict[str, Any]:
    del write
    binding_fields = _resource_binding_fields_from_validation(validation)
    return {
        "schema_version": APP_BUNDLE_PROOF_SCHEMA_VERSION,
        "app_schema_version": validation.get("app_schema_version") if isinstance(validation, dict) else None,
        "ok": False,
        "proof_level": APP_BUNDLE_PROOF_LEVEL,
        "target": target,
        "app": validation.get("summary") if isinstance(validation, dict) else None,
        "paths": {
            "proof_dir": str(output_dir),
            "app": None,
            "design": None,
            "report": str(report_path),
            "proof_summary": str(output_dir / APP_BUNDLE_DEFAULT_SUMMARY),
            "support_bundle": str(output_dir / APP_BUNDLE_DEFAULT_SUPPORT_BUNDLE),
        },
        "route_assertions": validation.get("route_assertions") if isinstance(validation, dict) else None,
        **binding_fields,
        "screens": [],
        "validation": _validation_summary(validation) if isinstance(validation, dict) else None,
        "policy": {"network_calls": "none"},
        "metadata": {"sdk_version": __version__, "strict_design": False, "screen_source": "embedded_intents"},
        "errors": _normalize_proof_errors(errors),
        "timings_ms": _final_timings(timings),
    }


def _app_shell_report(
    *,
    ok: bool,
    prepared: _PreparedAppShell,
    app_payload: dict[str, Any],
    validation: dict[str, Any],
    screen_reports: list[dict[str, Any]],
    errors: list[dict[str, str]],
    timings: dict[str, int],
    strict_design: bool,
    shell_payload: dict[str, Any] | None,
    resource_binding_report: dict[str, Any] | None = None,
    target: str = APP_SHELL_TARGET,
) -> dict[str, Any]:
    route_assertions = (
        dict(shell_payload["route_assertions"])
        if isinstance(shell_payload, dict) and isinstance(shell_payload.get("route_assertions"), dict)
        else _shell_route_assertions(app_payload)
    )
    paths = {
        "output_dir": str(prepared.output_dir),
        "index": str(prepared.index_path),
        "manifest": str(prepared.manifest_path),
        "diagnostics": str(prepared.diagnostics_path),
    }
    if isinstance(shell_payload, dict) and isinstance(shell_payload.get("paths"), dict):
        shell_paths = shell_payload["paths"]
        if shell_paths.get("state_reducer"):
            paths["state_reducer"] = str(shell_paths["state_reducer"])
        if shell_paths.get("state_manifest"):
            paths["state_manifest"] = str(shell_paths["state_manifest"])
    binding_fields = _resource_binding_report_fields(app_payload, resource_binding_report)
    if isinstance(shell_payload, dict):
        binding_fields = _resource_binding_report_fields(app_payload, shell_payload.get("resource_binding_assertions"))
    return {
        "schema_version": APP_BUNDLE_PROOF_SCHEMA_VERSION,
        "app_schema_version": _app_schema_version(app_payload),
        "ok": ok,
        "target": target,
        "route_navigation": REACT_APP_ROUTE_NAVIGATION if target == REACT_APP_TARGET else APP_SHELL_ROUTE_NAVIGATION,
        **binding_fields,
        "policy": {"network_calls": "none"},
        "app": _app_summary(app_payload),
        "paths": paths,
        "route_assertions": route_assertions,
        "shell_artifact_hash": shell_payload.get("shell_artifact_hash") if isinstance(shell_payload, dict) else None,
        "shell_manifest_hash": shell_payload.get("shell_manifest_hash") if isinstance(shell_payload, dict) else None,
        **(
            {
                "state_reducer_hash": shell_payload.get("state_reducer_hash"),
                "state_manifest_hash": shell_payload.get("state_manifest_hash"),
                "state_contract_hash": shell_payload.get("state_contract_hash"),
                "state_replay": shell_payload.get("state_replay"),
                "state_reducer_conformance": shell_payload.get("state_reducer_conformance"),
            }
            if isinstance(shell_payload, dict) and shell_payload.get("state_reducer_hash")
            else {}
        ),
        "screens": _screen_shell_summaries(screen_reports),
        "validation": _validation_summary(validation),
        "metadata": {
            "sdk_version": __version__,
            "strict_design": bool(strict_design),
            "screen_source": "embedded_intents",
            "shell_kind": "static_local_hash_shell",
        },
        "errors": _normalize_proof_errors(errors),
        "timings_ms": _final_timings(timings),
    }


def _app_shell_failure_report(
    *,
    output_dir: Path,
    errors: list[dict[str, str]],
    timings: dict[str, int],
    validation: dict[str, Any] | None,
    target: str = APP_SHELL_TARGET,
) -> dict[str, Any]:
    binding_fields = _resource_binding_fields_from_validation(validation)
    return {
        "schema_version": APP_BUNDLE_PROOF_SCHEMA_VERSION,
        "app_schema_version": validation.get("app_schema_version") if isinstance(validation, dict) else None,
        "ok": False,
        "target": target,
        "route_navigation": REACT_APP_ROUTE_NAVIGATION if target == REACT_APP_TARGET else APP_SHELL_ROUTE_NAVIGATION,
        **binding_fields,
        "policy": {"network_calls": "none"},
        "app": validation.get("summary") if isinstance(validation, dict) else None,
        "paths": {
            "output_dir": str(output_dir),
            "index": str(output_dir / APP_SHELL_INDEX),
            "manifest": str(output_dir / APP_SHELL_MANIFEST),
            "diagnostics": str(output_dir / APP_SHELL_DIAGNOSTICS),
        },
        "route_assertions": validation.get("route_assertions") if isinstance(validation, dict) else None,
        "shell_artifact_hash": None,
        "shell_manifest_hash": None,
        "screens": [],
        "validation": _validation_summary(validation) if isinstance(validation, dict) else None,
        "metadata": {"sdk_version": __version__, "strict_design": False, "screen_source": "embedded_intents"},
        "errors": _normalize_proof_errors(errors),
        "timings_ms": _final_timings(timings),
    }


def _write_app_proof(report: dict[str, Any], prepared: _PreparedAppProof) -> dict[str, Any]:
    report = _finalize_report_paths(report, prepared)
    report.setdefault("timings_ms", {})["total"] = sum(
        value for key, value in report.get("timings_ms", {}).items() if key != "total" and isinstance(value, int)
    )
    try:
        _write_bounded_json(
            prepared.report_path, report, limit=APP_BUNDLE_MAX_PROOF_REPORT_BYTES, code="APP_PROOF_REPORT_WRITE_FAILED"
        )
    except Exception as exc:
        failed = _append_app_proof_error(report, exc, fallback_code="APP_PROOF_REPORT_WRITE_FAILED")
        failed["ok"] = False
        return failed
    try:
        support_text = _render_app_support_bundle(report, proof_report_hash=file_hash(prepared.report_path))
        _write_app_support_bundle(prepared.support_path, support_text, report=report)
    except Exception as exc:
        failed = _append_app_proof_error(report, exc, fallback_code="APP_PROOF_SUPPORT_BUNDLE_WRITE_FAILED")
        failed["ok"] = False
        _write_bounded_json(
            prepared.report_path, failed, limit=APP_BUNDLE_MAX_PROOF_REPORT_BYTES, code="APP_PROOF_REPORT_WRITE_FAILED"
        )
        try:
            _write_app_summary(
                prepared.summary_path,
                _render_app_proof_summary(failed, proof_report_hash=file_hash(prepared.report_path)),
            )
        except Exception:
            pass
        return failed
    try:
        _write_app_summary(
            prepared.summary_path, _render_app_proof_summary(report, proof_report_hash=file_hash(prepared.report_path))
        )
    except Exception as exc:
        failed = _append_app_proof_error(report, exc, fallback_code="APP_PROOF_SUMMARY_WRITE_FAILED")
        failed["ok"] = False
        _write_bounded_json(
            prepared.report_path, failed, limit=APP_BUNDLE_MAX_PROOF_REPORT_BYTES, code="APP_PROOF_REPORT_WRITE_FAILED"
        )
        return failed
    return report


def _finalize_report_paths(report: dict[str, Any], prepared: _PreparedAppProof) -> dict[str, Any]:
    report.setdefault("paths", {})["report"] = str(prepared.report_path)
    report["paths"]["proof_summary"] = str(prepared.summary_path)
    report["paths"]["support_bundle"] = str(prepared.support_path)
    return report


def _write_bounded_json(path: Path, payload: dict[str, Any], *, limit: int, code: str) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if len(text.encode("utf-8")) > limit:
        raise AppBundleProofFailure(
            code,
            f"App proof JSON exceeds {limit} bytes.",
            "Reduce AppBundle size or inspect the in-memory CLI JSON result.",
        )
    atomic_write(path, text)


def _write_app_summary(path: Path, markdown: str) -> None:
    if len(markdown.encode("utf-8")) > APP_BUNDLE_MAX_SUMMARY_BYTES:
        raise AppBundleProofFailure(
            "APP_PROOF_SUMMARY_WRITE_FAILED",
            f"App proof summary exceeds {APP_BUNDLE_MAX_SUMMARY_BYTES} bytes.",
            "Inspect app_proof_report.json for machine-readable details.",
        )
    atomic_write(path, markdown)


def _write_app_support_bundle(path: Path, payload: str, *, report: dict[str, Any]) -> None:
    if len(payload.encode("utf-8")) > APP_BUNDLE_MAX_SUPPORT_BUNDLE_BYTES:
        raise AppBundleProofFailure(
            "APP_PROOF_SUPPORT_BUNDLE_WRITE_FAILED",
            f"App support bundle exceeds {APP_BUNDLE_MAX_SUPPORT_BUNDLE_BYTES} bytes.",
            "Inspect app_proof_report.json for machine-readable details.",
        )
    _assert_app_support_bundle_redacted(payload, report)
    atomic_write(path, payload)


def _render_app_support_bundle(report: dict[str, Any], *, proof_report_hash: str) -> str:
    app = report.get("app") if isinstance(report.get("app"), dict) else {}
    screens = report.get("screens") if isinstance(report.get("screens"), list) else []
    shell = report.get("shell") if isinstance(report.get("shell"), dict) else {}
    bundle = {
        "schema_version": 1,
        "kind": "viewspec_app_proof_support_bundle",
        "ok": bool(report.get("ok")),
        "proof_level": report.get("proof_level"),
        "target": report.get("target"),
        "proof_report_hash": proof_report_hash,
        "app": {
            "id": _support_scalar(app.get("id")),
            "kind": _support_scalar(app.get("kind")),
            "route_count": int(app.get("route_count")) if isinstance(app.get("route_count"), int) else 0,
            "screen_count": int(app.get("screen_count")) if isinstance(app.get("screen_count"), int) else 0,
            "resource_count": int(app.get("resource_count")) if isinstance(app.get("resource_count"), int) else 0,
        },
        "route_assertions": {
            str(key): bool(value)
            for key, value in (report.get("route_assertions") or {}).items()
            if isinstance(key, str)
        }
        if isinstance(report.get("route_assertions"), dict)
        else {},
        "resource_binding": _support_scalar(report.get("resource_binding")),
        "binding_scope": _support_scalar(report.get("binding_scope")) if report.get("binding_scope") else None,
        "resource_binding_assertions": _support_binding_summary(report.get("resource_binding_assertions")),
        "static_analysis": _support_static_analysis(report.get("static_analysis")),
        **(
            {"text_layout": _support_text_layout(report.get("text_layout"))}
            if isinstance(report.get("text_layout"), dict)
            else {}
        ),
        "shell": {
            "route_navigation": _support_scalar(shell.get("route_navigation")),
            "shell_artifact_hash": _support_scalar(
                report.get("shell_artifact_hash") or shell.get("shell_artifact_hash")
            ),
            "shell_manifest_hash": _support_scalar(
                report.get("shell_manifest_hash") or shell.get("shell_manifest_hash")
            ),
            "state_reducer_hash": _support_scalar(report.get("state_reducer_hash") or shell.get("state_reducer_hash")),
            "state_manifest_hash": _support_scalar(
                report.get("state_manifest_hash") or shell.get("state_manifest_hash")
            ),
            "state_contract_hash": _support_scalar(
                report.get("state_contract_hash") or shell.get("state_contract_hash")
            ),
            "state_reducer_conformance": _support_scalar(
                _state_conformance_status(
                    report.get("state_reducer_conformance") or shell.get("state_reducer_conformance")
                )
            ),
        }
        if shell
        or report.get("shell_artifact_hash")
        or report.get("shell_manifest_hash")
        or report.get("state_reducer_hash")
        else None,
        "screens": [
            {
                "id": _support_scalar(screen.get("id")),
                "validation_status": _support_scalar(screen.get("validation_status")),
                "compile_status": _support_scalar(screen.get("compile_status")),
                "check_status": _support_scalar(screen.get("check_status")),
                "artifact_hash": _support_scalar(screen.get("artifact_hash")),
                "manifest_hash": _support_scalar(screen.get("manifest_hash")),
                "manifest_summary": _support_manifest_summary(screen.get("manifest_summary")),
            }
            for screen in screens
            if isinstance(screen, dict)
        ],
        "errors": _support_errors(report.get("errors") if isinstance(report.get("errors"), list) else []),
        "policy": {
            "network_calls": _support_scalar(
                report.get("policy", {}).get("network_calls") if isinstance(report.get("policy"), dict) else "none"
            )
        },
        "metadata": {
            "sdk_version": __version__,
            "python_version": platform.python_version(),
            "platform": platform.system(),
            "executable": Path(sys.executable).name,
        },
        "paths": _support_path_names(report),
        "privacy": {
            "local_only": True,
            "contains_raw_app": False,
            "contains_raw_intent": False,
            "contains_raw_design": False,
            "contains_raw_artifact": False,
            "contains_raw_diagnostics": False,
            "contains_absolute_paths": False,
            "contains_environment_variables": False,
            "contains_credentials": False,
        },
    }
    return json.dumps(bundle, indent=2, sort_keys=True) + "\n"


def _compact_shell_report(shell: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(shell, dict):
        return {}
    return {
        "ok": bool(shell.get("ok")),
        "target": shell.get("target"),
        "route_navigation": shell.get("route_navigation"),
        "app_schema_version": shell.get("app_schema_version"),
        "resource_binding": shell.get("resource_binding"),
        "policy": shell.get("policy") if isinstance(shell.get("policy"), dict) else {"network_calls": "none"},
        "paths": shell.get("paths") if isinstance(shell.get("paths"), dict) else {},
        "route_assertions": shell.get("route_assertions") if isinstance(shell.get("route_assertions"), dict) else {},
        "shell_artifact_hash": shell.get("shell_artifact_hash"),
        "shell_manifest_hash": shell.get("shell_manifest_hash"),
        "state_reducer_hash": shell.get("state_reducer_hash"),
        "state_manifest_hash": shell.get("state_manifest_hash"),
        "state_contract_hash": shell.get("state_contract_hash"),
        "state_replay": shell.get("state_replay"),
        "state_reducer_conformance": shell.get("state_reducer_conformance"),
        "binding_scope": shell.get("binding_scope"),
        "resource_binding_assertions": _compact_resource_binding_report(shell.get("resource_binding_assertions")),
        "errors": _normalize_proof_errors(shell.get("errors")),
    }


def _compact_react_app_report(report: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(report, dict):
        return {}
    return {
        "ok": bool(report.get("ok")),
        "target": report.get("target"),
        "route_navigation": report.get("route_navigation"),
        "app_schema_version": report.get("app_schema_version"),
        "resource_binding": report.get("resource_binding"),
        "binding_scope": report.get("binding_scope"),
        "runtime": report.get("runtime") if isinstance(report.get("runtime"), dict) else {},
        "paths": report.get("paths") if isinstance(report.get("paths"), dict) else {},
        "route_assertions": report.get("route_assertions") if isinstance(report.get("route_assertions"), dict) else {},
        "app_artifact_hash": report.get("app_artifact_hash"),
        "manifest_hash": report.get("manifest_hash"),
        "diagnostics_hash": report.get("diagnostics_hash"),
        "state_reducer_hash": report.get("state_reducer_hash"),
        "state_manifest_hash": report.get("state_manifest_hash"),
        "state_contract_hash": report.get("state_contract_hash"),
        "state_replay": report.get("state_replay"),
        "state_reducer_conformance": report.get("state_reducer_conformance"),
        "errors": _normalize_proof_errors(report.get("errors")),
    }


def _compact_resource_binding_report(report: object) -> dict[str, Any] | None:
    if not isinstance(report, dict):
        return None
    views = report.get("views") if isinstance(report.get("views"), list) else []
    return {
        "ok": bool(report.get("ok")),
        "resource_binding": report.get("resource_binding"),
        "binding_scope": report.get("binding_scope"),
        "proof_source": report.get("proof_source"),
        "assertion_count": report.get("assertion_count"),
        "passed_count": report.get("passed_count"),
        "failed_count": report.get("failed_count"),
        "view_count": report.get("view_count"),
        "binding_digest": report.get("binding_digest"),
        "views": [
            {
                "id": view.get("id"),
                "screen_id": view.get("screen_id"),
                "resource_id": view.get("resource_id"),
                "target_motif_id": view.get("target_motif_id"),
                "assertion_count": view.get("assertion_count"),
                "passed_count": view.get("passed_count"),
                "status": view.get("status"),
            }
            for view in views
            if isinstance(view, dict)
        ],
        "errors": _normalize_proof_errors(report.get("errors")),
    }


def _support_binding_summary(report: object) -> dict[str, Any] | None:
    compact = _compact_resource_binding_report(report)
    if not compact:
        return None
    return {
        "ok": bool(compact.get("ok")),
        "binding_scope": _support_scalar(compact.get("binding_scope")),
        "assertion_count": int(compact.get("assertion_count"))
        if isinstance(compact.get("assertion_count"), int)
        else 0,
        "passed_count": int(compact.get("passed_count")) if isinstance(compact.get("passed_count"), int) else 0,
        "failed_count": int(compact.get("failed_count")) if isinstance(compact.get("failed_count"), int) else 0,
        "view_count": int(compact.get("view_count")) if isinstance(compact.get("view_count"), int) else 0,
        "binding_digest": _support_scalar(compact.get("binding_digest")),
    }


def _support_static_analysis(report: object) -> dict[str, Any] | None:
    if not isinstance(report, dict):
        return None
    engine = report.get("engine") if isinstance(report.get("engine"), dict) else {}
    runtime = report.get("runtime") if isinstance(report.get("runtime"), dict) else {}
    coverage = report.get("coverage") if isinstance(report.get("coverage"), dict) else {}
    required_functions = report.get("required_functions") if isinstance(report.get("required_functions"), list) else []
    source_groups = report.get("source_hashes") if isinstance(report.get("source_hashes"), dict) else {}
    source_hashes = (
        source_groups.get("analyzed_sources") if isinstance(source_groups.get("analyzed_sources"), list) else []
    )
    return {
        "status": _support_scalar(report.get("status")),
        "engine": {
            "name": _support_scalar(engine.get("name")),
            "version": _support_scalar(engine.get("version")),
            "integrity": _support_scalar(engine.get("integrity")),
        },
        "runtime": {
            "name": _support_scalar(runtime.get("name")),
            "version": _support_scalar(runtime.get("version")),
            "sha256": _support_scalar(runtime.get("sha256")),
        },
        "coverage": {
            key: int(value) if isinstance(value, int) else 0
            for key, value in (
                ("required", coverage.get("required")),
                ("fully_analyzed", coverage.get("fully_analyzed", coverage.get("analyzed"))),
                ("partial", coverage.get("partial")),
                ("unsupported", coverage.get("unsupported")),
            )
        },
        "required_functions": [_support_scalar(value) for value in required_functions[:16]],
        "source_sha256": [_support_scalar(item.get("sha256")) for item in source_hashes[:16] if isinstance(item, dict)],
        "finding_count": len(report.get("findings", [])) if isinstance(report.get("findings"), list) else 0,
        "audit_transcript_hash": _support_scalar(
            report.get("audit_transcript_hash", report.get("audit_transcript_sha256"))
        ),
    }


def _support_text_layout(report: object) -> dict[str, Any] | None:
    if not isinstance(report, dict):
        return None
    engine = report.get("engine") if isinstance(report.get("engine"), dict) else {}
    coverage = report.get("coverage") if isinstance(report.get("coverage"), dict) else {}
    cache = report.get("cache") if isinstance(report.get("cache"), dict) else {}
    environment = report.get("environment") if isinstance(report.get("environment"), dict) else {}
    return {
        "status": _support_scalar(report.get("status")),
        "profile": _support_scalar(report.get("profile")),
        "protocol": _support_scalar(report.get("protocol")),
        "font_family": _support_scalar(report.get("font_family")),
        "engine": {
            "name": _support_scalar(engine.get("name")),
            "version": _support_scalar(engine.get("version")),
            "integrity": _support_scalar(engine.get("integrity")),
            "package_tree_sha256": _support_scalar(engine.get("package_tree_sha256")),
        },
        "environment": {
            "browser": _support_scalar(environment.get("browser")),
            "device_scale_factor": _support_scalar(environment.get("device_scale_factor")),
            "font_status": _support_scalar(environment.get("font_status")),
        },
        "coverage": {
            key: int(value) if isinstance(value, int) else 0
            for key, value in (
                ("required", coverage.get("required")),
                ("accounted", coverage.get("accounted")),
                ("measured", coverage.get("measured")),
                ("hidden", coverage.get("hidden")),
                ("unsupported", coverage.get("unsupported")),
                ("failed", coverage.get("failed")),
            )
        },
        "cache": {
            key: int(value) if isinstance(value, int) else 0
            for key, value in (
                ("prepare_calls", cache.get("prepare_calls")),
                ("unique_inputs", cache.get("unique_inputs")),
                ("layout_calls", cache.get("layout_calls")),
                ("cache_hits", cache.get("cache_hits")),
            )
        },
        "scope_digest": _support_scalar(report.get("scope_digest")),
        "observation_digest": _support_scalar(report.get("observation_digest")),
        "report_sha256": _support_scalar(report.get("report_sha256")),
    }


def _render_app_proof_summary(report: dict[str, Any], *, proof_report_hash: str) -> str:
    app = report.get("app") if isinstance(report.get("app"), dict) else {}
    status = "PASSED" if report.get("ok") else "FAILED"
    is_react = report.get("target") == REACT_APP_TARGET
    if report.get("ok"):
        claim = (
            "Claim: AppBundle contract, checked per-screen provenance, generated React app source, browser-history "
            "routing, state mutation, resource data rebinding, selectors, and visibility passed in the exact reference host."
            if is_react
            else "Claim: AppBundle contract, static route graph, per-screen source artifact/provenance proof, and declared read-only fixture binding proof when enabled."
        )
    else:
        claim = (
            "Claim not established: the composite proof failed. Any completed phase evidence below is retained only "
            "for diagnosis and must not be read as a successful composite certification."
        )
    has_pretext_evidence = isinstance(report.get("text_layout"), dict)
    non_claim = (
        "Non-claim: Freerange does not analyze Tailwind class strings or CSS. "
        + (
            "Pretext evidence is limited to compiler-owned native DOM text, the recorded named font, supported CSS text "
            "profile, viewports, and exact Chromium environment; it does not certify Safari, Firefox, Retina rendering, "
            "arbitrary host text, or canvas glyph placement. "
            if has_pretext_evidence
            else ""
        )
        + "The React app proof does not prove authentication, persistence, arbitrary APIs, optimistic updates, accessibility "
        "certification, cross-browser equivalence, or production infrastructure behavior."
        if is_react
        else (
            "Non-claim: AppBundle proofs do not prove browser runtime navigation, dynamic routing, "
            "runtime data binding, deployable app scaffolding, pixel-perfect visual equivalence, "
            "accessibility certification, or hosted extended compiler behavior."
        )
    )
    lines = [
        "# ViewSpec App Proof",
        "",
        f"Status: **{status}**",
        f"Target: `{_summary_value(report.get('target'))}`",
        f"Proof level: `{_summary_value(report.get('proof_level'))}`",
        claim,
        non_claim,
        "",
        "## App",
        "",
        f"- Id: `{_summary_value(app.get('id'))}`",
        f"- Title: `{_summary_value(app.get('title'))}`",
        f"- Kind: `{_summary_value(app.get('kind'))}`",
        f"- Root route: `{_summary_value(app.get('root_route'))}`",
        f"- Routes: `{_summary_value(app.get('route_count'))}`",
        f"- Screens: `{_summary_value(app.get('screen_count'))}`",
        f"- Resources: `{_summary_value(app.get('resource_count'))}`",
        f"- Resource binding: `{_summary_value(report.get('resource_binding'))}`",
        "",
        "## Route Assertions",
        "",
    ]
    assertions = report.get("route_assertions") if isinstance(report.get("route_assertions"), dict) else {}
    for key in ("root_route_resolves", "all_routes_resolve", "all_screens_reachable"):
        lines.append(f"- {key}: `{_summary_value(assertions.get(key))}`")
    if is_react:
        lines.extend(["", "## React App Host", ""])
        lines.append(f"- Route navigation: `{_summary_value(report.get('route_navigation'))}`")
        lines.append(f"- App artifact SHA-256: `{_summary_value(report.get('app_artifact_hash'))}`")
        lines.append(f"- App manifest SHA-256: `{_summary_value(report.get('manifest_hash'))}`")
        host = report.get("host_report") if isinstance(report.get("host_report"), dict) else {}
        host_assertions = host.get("assertions") if isinstance(host.get("assertions"), dict) else {}
        host_phases = host.get("phases") if isinstance(host.get("phases"), dict) else {}
        for key in ("artifact_integrity", "typecheck", "freerange", "build", "browser", "pretext", "final_integrity"):
            if key in host_phases:
                lines.append(f"- phase.{key}: `{_summary_value(host_phases.get(key))}`")
        for key in (
            "route_count",
            "history_assertion_count",
            "unknown_route_assertion_count",
            "state_action_count",
            "rebound_binding_count",
            "selector_assertion_count",
            "visibility_assertion_count",
        ):
            lines.append(f"- {key}: `{_summary_value(host_assertions.get(key))}`")
        static_analysis = report.get("static_analysis") if isinstance(report.get("static_analysis"), dict) else None
        if static_analysis is not None:
            coverage = static_analysis.get("coverage") if isinstance(static_analysis.get("coverage"), dict) else {}
            engine = static_analysis.get("engine") if isinstance(static_analysis.get("engine"), dict) else {}
            lines.extend(["", "## Numeric Static Analysis", ""])
            lines.append(f"- Status: `{_summary_value(static_analysis.get('status'))}`")
            lines.append(f"- Engine: `{_summary_value(engine.get('name'))}` `{_summary_value(engine.get('version'))}`")
            lines.append(f"- Required functions: `{_summary_value(coverage.get('required'))}`")
            lines.append(
                f"- Fully analyzed: `{_summary_value(coverage.get('fully_analyzed', coverage.get('analyzed')))}`"
            )
            lines.append(f"- Partial: `{_summary_value(coverage.get('partial'))}`")
            lines.append(f"- Unsupported: `{_summary_value(coverage.get('unsupported'))}`")
            lines.append(
                "- Audit transcript SHA-256: "
                f"`{_summary_value(static_analysis.get('audit_transcript_hash', static_analysis.get('audit_transcript_sha256')))}`"
            )
        text_layout = report.get("text_layout") if isinstance(report.get("text_layout"), dict) else None
        if text_layout is not None:
            coverage = text_layout.get("coverage") if isinstance(text_layout.get("coverage"), dict) else {}
            engine = text_layout.get("engine") if isinstance(text_layout.get("engine"), dict) else {}
            cache = text_layout.get("cache") if isinstance(text_layout.get("cache"), dict) else {}
            lines.extend(["", "## Native DOM Text Layout", ""])
            lines.append(f"- Status: `{_summary_value(text_layout.get('status'))}`")
            lines.append(f"- Engine: `{_summary_value(engine.get('name'))}` `{_summary_value(engine.get('version'))}`")
            lines.append(f"- Profile: `{_summary_value(text_layout.get('profile'))}`")
            lines.append(f"- Font family: `{_summary_value(text_layout.get('font_family'))}`")
            lines.append(f"- Required observations: `{_summary_value(coverage.get('required'))}`")
            lines.append(f"- Measured: `{_summary_value(coverage.get('measured'))}`")
            lines.append(f"- Hidden: `{_summary_value(coverage.get('hidden'))}`")
            lines.append(f"- Unsupported: `{_summary_value(coverage.get('unsupported'))}`")
            lines.append(f"- Failed: `{_summary_value(coverage.get('failed'))}`")
            lines.append(f"- Prepare calls: `{_summary_value(cache.get('prepare_calls'))}`")
            lines.append(f"- Layout cache hits: `{_summary_value(cache.get('cache_hits'))}`")
            lines.append(f"- Scope digest: `{_summary_value(text_layout.get('scope_digest'))}`")
    if report.get("shell"):
        lines.extend(["", "## Static Shell", ""])
        lines.append(f"- Route navigation: `{_summary_value(report.get('route_navigation'))}`")
        lines.append(f"- Shell artifact SHA-256: `{_summary_value(report.get('shell_artifact_hash'))}`")
        lines.append(f"- Shell manifest SHA-256: `{_summary_value(report.get('shell_manifest_hash'))}`")
        if report.get("state_reducer_hash"):
            lines.append(f"- State reducer SHA-256: `{_summary_value(report.get('state_reducer_hash'))}`")
        if report.get("state_contract_hash"):
            lines.append(f"- State contract SHA-256: `{_summary_value(report.get('state_contract_hash'))}`")
        conformance_status = _state_conformance_status(report.get("state_reducer_conformance"))
        if conformance_status:
            lines.append(f"- State reducer conformance: `{_summary_value(conformance_status)}`")
        if isinstance(report.get("state_replay"), dict):
            replay = report["state_replay"]
            lines.append(f"- State replay: `{_summary_value('passed' if replay.get('ok') else 'failed')}`")
        for key in (
            "every_route_maps_exactly_one_screen",
            "every_screen_has_route",
            "root_route_selects_exactly_one_screen",
            "unknown_route_selects_no_screen_and_one_404",
        ):
            lines.append(f"- {key}: `{_summary_value(assertions.get(key))}`")
    binding = (
        report.get("resource_binding_assertions")
        if isinstance(report.get("resource_binding_assertions"), dict)
        else None
    )
    if binding:
        lines.extend(["", "## Resource Binding", ""])
        lines.append(
            f"- Binding scope: `{_summary_value(report.get('binding_scope') or binding.get('binding_scope'))}`"
        )
        lines.append(f"- Assertion count: `{_summary_value(binding.get('assertion_count'))}`")
        lines.append(f"- Passed count: `{_summary_value(binding.get('passed_count'))}`")
        lines.append(f"- Failed count: `{_summary_value(binding.get('failed_count'))}`")
        lines.append(f"- Binding digest: `{_summary_value(binding.get('binding_digest'))}`")
        views = binding.get("views") if isinstance(binding.get("views"), list) else []
        for view in views:
            if isinstance(view, dict):
                lines.append(
                    f"- `{_summary_value(view.get('id'))}` on `{_summary_value(view.get('screen_id'))}`: "
                    f"`{_summary_value(view.get('status'))}`"
                )
    lines.extend(["", "## Screens", ""])
    screens = report.get("screens") if isinstance(report.get("screens"), list) else []
    if screens:
        for screen in screens:
            if not isinstance(screen, dict):
                continue
            lines.append(
                f"- `{_summary_value(screen.get('id'))}`: validation `{_summary_value(screen.get('validation_status'))}`, "
                f"compile `{_summary_value(screen.get('compile_status'))}`, check `{_summary_value(screen.get('check_status'))}`"
            )
            lines.append(f"  artifact_sha256: `{_summary_value(screen.get('artifact_hash'))}`")
            lines.append(f"  manifest_sha256: `{_summary_value(screen.get('manifest_hash'))}`")
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Policy",
            "",
            f"- Network/install policy: `{_summary_value(report.get('policy', {}).get('network_calls'))}`",
            "",
            "## Files",
            "",
        ]
    )
    paths = report.get("paths") if isinstance(report.get("paths"), dict) else {}
    for key in (
        "app",
        "design",
        "report",
        "proof_summary",
        "support_bundle",
        "app_shell_index",
        "app_shell_manifest",
        "app_shell_diagnostics",
        "react_app_entry",
        "react_app_manifest",
        "react_app_diagnostics",
    ):
        lines.append(f"- {key}: `{_summary_value(paths.get(key))}`")
    lines.extend(["", "## Hashes", "", f"- App proof report SHA-256: `{_summary_value(proof_report_hash)}`"])
    lines.extend(["", "## Errors", ""])
    errors = report.get("errors") if isinstance(report.get("errors"), list) else []
    if errors:
        for error in errors:
            if isinstance(error, dict):
                lines.append(f"- `{_summary_value(error.get('code'))}`: {_summary_value(error.get('message'))}")
                lines.append(f"  Fix: {_summary_value(error.get('fix'))}")
    else:
        lines.append("- None")
    lines.append("")
    return "\n".join(lines)


def _append_app_proof_error(report: dict[str, Any], exc: Exception, *, fallback_code: str) -> dict[str, Any]:
    failed = dict(report)
    failed["errors"] = list(report.get("errors") if isinstance(report.get("errors"), list) else [])
    if isinstance(exc, AppBundleProofFailure):
        code = exc.code
        message = exc.message
        fix = exc.fix
    else:
        code = fallback_code
        message = str(exc)
        fix = "Inspect app_proof_report.json and retry viewspec prove-app."
    failed["errors"].append({"code": code, "message": message, "fix": fix})
    return failed


def _support_errors(errors: list[object]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for error in errors[:16]:
        if isinstance(error, dict):
            normalized.append(
                {
                    "code": _support_scalar(error.get("code")),
                    "screen_id": _support_scalar(error.get("screen_id")),
                    "fix": _support_scalar(error.get("fix")),
                }
            )
    return normalized


def _support_manifest_summary(summary: object) -> dict[str, Any] | None:
    if not isinstance(summary, dict):
        return None
    return {
        "available": bool(summary.get("available")),
        "kind": _support_scalar(summary.get("kind")),
        "emitter": _support_scalar(summary.get("emitter")),
        "artifact_file": _support_scalar(summary.get("artifact_file")),
        "node_count": int(summary.get("node_count")) if isinstance(summary.get("node_count"), int) else 0,
        "aesthetic_profile": _support_scalar(summary.get("aesthetic_profile")),
    }


def _support_path_names(report: dict[str, Any]) -> dict[str, str]:
    paths = report.get("paths", {}) if isinstance(report.get("paths"), dict) else {}
    names: dict[str, str] = {}
    for key in (
        "proof_dir",
        "app",
        "design",
        "report",
        "proof_summary",
        "support_bundle",
        "app_shell",
        "app_shell_index",
        "app_shell_manifest",
        "app_shell_diagnostics",
        "react_app",
        "react_app_entry",
        "react_app_manifest",
        "react_app_diagnostics",
        "app_state_reducer",
        "app_state_manifest",
    ):
        value = paths.get(key)
        if value:
            names[f"{key}_name"] = Path(str(value)).name
    return names


def _support_scalar(value: object) -> str:
    if value is None:
        return "not_recorded"
    return str(value).replace("\r", " ").replace("\n", " ").replace("`", "'")[:256]


def _assert_app_support_bundle_redacted(payload: str, report: dict[str, Any]) -> None:
    paths = report.get("paths", {}) if isinstance(report.get("paths"), dict) else {}
    for value in paths.values():
        if not value:
            continue
        raw = str(value)
        escaped = json.dumps(raw)[1:-1]
        if (":" in raw or "\\" in raw or "/" in raw) and (raw in payload or escaped in payload):
            raise AppBundleProofFailure(
                "APP_PROOF_SUPPORT_BUNDLE_CONTENT_FORBIDDEN",
                "App support bundle included an absolute or structured local path.",
                "Inspect app_proof_report.json locally instead of sharing support bundle path content.",
            )


def _summary_value(value: object) -> str:
    if value is None:
        return "not_recorded"
    return str(value).replace("\r", " ").replace("\n", " ").replace("`", "'")


def _final_timings(timings: dict[str, int]) -> dict[str, int]:
    payload = dict(sorted(timings.items()))
    payload["total"] = sum(value for value in payload.values() if isinstance(value, int))
    return payload


def _app_tool_proof_identity(proof: dict[str, Any]) -> dict[str, str | None]:
    paths = proof.get("paths") if isinstance(proof.get("paths"), dict) else {}
    screen_hashes: dict[str, dict[str, str | None]] = {}
    for screen in proof.get("screens", []) if isinstance(proof.get("screens"), list) else []:
        if not isinstance(screen, dict) or not isinstance(screen.get("id"), str):
            continue
        screen_hashes[screen["id"]] = {
            "artifact_hash": screen.get("artifact_hash") if isinstance(screen.get("artifact_hash"), str) else None,
            "manifest_hash": screen.get("manifest_hash") if isinstance(screen.get("manifest_hash"), str) else None,
        }
    static_analysis = proof.get("static_analysis") if isinstance(proof.get("static_analysis"), dict) else {}
    text_layout = proof.get("text_layout") if isinstance(proof.get("text_layout"), dict) else {}
    return {
        "proof_report_hash": _hash_path_if_present(paths.get("report")),
        "proof_summary_hash": _hash_path_if_present(paths.get("proof_summary")),
        "support_bundle_hash": _hash_path_if_present(paths.get("support_bundle")),
        "shell_artifact_hash": proof.get("shell_artifact_hash")
        if isinstance(proof.get("shell_artifact_hash"), str)
        else None,
        "shell_manifest_hash": proof.get("shell_manifest_hash")
        if isinstance(proof.get("shell_manifest_hash"), str)
        else None,
        "app_artifact_hash": proof.get("app_artifact_hash")
        if isinstance(proof.get("app_artifact_hash"), str)
        else None,
        "app_manifest_hash": proof.get("manifest_hash") if isinstance(proof.get("manifest_hash"), str) else None,
        "state_reducer_hash": proof.get("state_reducer_hash")
        if isinstance(proof.get("state_reducer_hash"), str)
        else None,
        "state_manifest_hash": proof.get("state_manifest_hash")
        if isinstance(proof.get("state_manifest_hash"), str)
        else None,
        "state_contract_hash": proof.get("state_contract_hash")
        if isinstance(proof.get("state_contract_hash"), str)
        else None,
        "state_reducer_conformance": _state_conformance_status(proof.get("state_reducer_conformance")),
        "freerange_audit_transcript_hash": (
            static_analysis.get("audit_transcript_hash", static_analysis.get("audit_transcript_sha256"))
            if isinstance(
                static_analysis.get("audit_transcript_hash", static_analysis.get("audit_transcript_sha256")),
                str,
            )
            else None
        ),
        "pretext_scope_digest": (
            text_layout.get("scope_digest") if isinstance(text_layout.get("scope_digest"), str) else None
        ),
        "pretext_observation_digest": (
            text_layout.get("observation_digest") if isinstance(text_layout.get("observation_digest"), str) else None
        ),
        "pretext_report_hash": (
            text_layout.get("report_sha256") if isinstance(text_layout.get("report_sha256"), str) else None
        ),
        "screen_hashes": screen_hashes,  # type: ignore[dict-item]
    }


def _hash_path_if_present(path: object) -> str | None:
    if not path:
        return None
    candidate = Path(str(path))
    if not candidate.exists() or not candidate.is_file():
        return None
    return file_hash(candidate)


__all__ = [
    "APP_BUNDLE_DEFAULT_OUT",
    "APP_BUNDLE_DEFAULT_REPORT",
    "APP_BUNDLE_DEFAULT_SUMMARY",
    "APP_BUNDLE_DEFAULT_SUPPORT_BUNDLE",
    "APP_BUNDLE_PROOF_LEVEL",
    "APP_BUNDLE_PROOF_SCHEMA_VERSION",
    "APP_BUNDLE_TARGET",
    "_app_proof_failure_report",
    "_app_proof_report",
    "_app_shell_failure_report",
    "_app_shell_report",
    "_app_tool_proof_identity",
    "_write_app_proof",
    "_write_bounded_json",
]
