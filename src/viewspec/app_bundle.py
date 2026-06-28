"""Local AppBundle contract, diff, and proof helpers."""

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
from viewspec.app_errors import AppBundleProofFailure
from viewspec.app_validation import (
    APP_BUNDLE_ALLOWED_KINDS,
    APP_BUNDLE_ALLOWED_RESOURCE_KINDS,
    APP_BUNDLE_BINDING_SCOPE,
    APP_BUNDLE_BOUND_SCHEMA_VERSION,
    APP_BUNDLE_MAX_BYTES,
    APP_BUNDLE_MAX_EMBEDDED_INTENT_BYTES,
    APP_BUNDLE_MAX_ID_CHARS,
    APP_BUNDLE_MAX_PROOF_REPORT_BYTES,
    APP_BUNDLE_MAX_RECORD_FIELDS,
    APP_BUNDLE_MAX_RECORDS_PER_RESOURCE,
    APP_BUNDLE_MAX_RESOURCES,
    APP_BUNDLE_MAX_ROUTE_CHARS,
    APP_BUNDLE_MAX_ROUTES,
    APP_BUNDLE_MAX_SCALAR_STRING_CHARS,
    APP_BUNDLE_MAX_SCREENS,
    APP_BUNDLE_MAX_SUPPORT_BUNDLE_BYTES,
    APP_BUNDLE_RESOURCE_BINDING,
    APP_BUNDLE_RESOURCE_BINDING_READONLY,
    APP_BUNDLE_RESULT_SCHEMA_VERSION,
    APP_BUNDLE_SCHEMA_VERSION,
    APP_BUNDLE_STATE_SCHEMA_VERSION,
    APP_BUNDLE_SUPPORTED_SCHEMA_VERSIONS,
    APP_RESOURCE_BINDING_MAX_FIELDS_PER_VIEW,
    APP_RESOURCE_BINDING_MAX_RECORD_REFS_PER_VIEW,
    APP_RESOURCE_BINDING_MAX_VIEWS_PER_SCREEN,
    _app_schema_version,
    _app_summary,
    _reject_json_constant,
    _resource_binding_fields_from_validation,
    _resource_binding_report_fields,
    _route_assertions,
    validate_app_file,
    validate_app_text,
)
from viewspec.app_resource_binding import _resource_binding_assertion_report
from viewspec.app_diff import (
    APP_BUNDLE_DIFF_BASIS,
    APP_BUNDLE_DIFF_VERSION,
    _validation_summary,
    app_semantic_change_lines,
    diff_app_files,
    diff_app_text,
)
from viewspec.app_shell import (
    APP_SHELL_DEFAULT_OUT,
    APP_SHELL_DIAGNOSTICS,
    APP_SHELL_DIR_NAME,
    APP_SHELL_INDEX,
    APP_SHELL_MANIFEST,
    APP_SHELL_MAX_MANIFEST_BYTES,
    APP_SHELL_ROUTE_NAVIGATION,
    APP_SHELL_TARGET,
    _app_shell_limits,
    _build_static_app_shell,
    _screen_shell_summaries,
    _shell_route_assertions,
)
from viewspec.agent import SAFE_AGENT_ID_PATTERN
from viewspec.local_tools import (
    MCP_RESULT_SCHEMA_VERSION,
    LocalToolError,
    atomic_write,
    check_artifact_dir,
    exception_response,
    file_hash,
    path_policy_metadata,
    resolve_cwd,
    resolve_local_path,
    tool_error_response,
    tool_response,
)
from viewspec.manifest_summary import summarize_intent_manifest
from viewspec.sdk.builder import ViewSpecBuilder
from viewspec.state_ir import (
    APP_STATE_MAX_ENTRIES,
    APP_STATE_MAX_EVENTS_PER_REPLAY,
    APP_STATE_MAX_MANIFEST_BYTES,
    APP_STATE_MAX_MUTATIONS,
    APP_STATE_MAX_OPS_PER_MUTATION,
    APP_STATE_MAX_REDUCER_BYTES,
    APP_STATE_MAX_REPLAY_ASSERTIONS,
    APP_STATE_MAX_SELECTOR_OPS,
    APP_STATE_MAX_SELECTORS,
    INTERACTIVE_STATE_PROFILE,
    check_reducer_conformance,
    generate_typescript_reducer,
    state_manifest,
)


APP_BUNDLE_PROOF_SCHEMA_VERSION = 1
APP_BUNDLE_DEFAULT_OUT = ".viewspec-app-proof"
APP_BUNDLE_DEFAULT_REPORT = "app_proof_report.json"
APP_BUNDLE_DEFAULT_SUMMARY = "APP_PROOF.md"
APP_BUNDLE_DEFAULT_SUPPORT_BUNDLE = "app_support_bundle.json"
APP_BUNDLE_PROOF_LEVEL = "app_contract_source_artifacts"
APP_BUNDLE_TARGET = "html-tailwind"
APP_STATE_REDUCER = "state_reducer.ts"
APP_STATE_MANIFEST = "state_manifest.json"
APP_BUNDLE_MAX_SUMMARY_BYTES = 32 * 1024


@dataclass(frozen=True)
class _PreparedAppProof:
    output_dir: Path
    app_path: Path
    design_path: Path | None
    report_path: Path
    summary_path: Path
    support_path: Path


@dataclass(frozen=True)
class _PreparedAppShell:
    output_dir: Path
    app_path: Path
    design_path: Path | None
    manifest_path: Path
    diagnostics_path: Path
    index_path: Path


def starter_app_bundle(kind: str = "internal_tool", *, resource_binding: str = APP_BUNDLE_RESOURCE_BINDING) -> dict[str, Any]:
    """Return a valid two-screen AppBundle starter."""
    if kind not in APP_BUNDLE_ALLOWED_KINDS:
        raise ValueError(f"Unknown starter app kind: {kind}")
    if resource_binding not in {APP_BUNDLE_RESOURCE_BINDING, APP_BUNDLE_RESOURCE_BINDING_READONLY}:
        raise ValueError(f"Unknown starter app resource binding: {resource_binding}")
    if resource_binding == APP_BUNDLE_RESOURCE_BINDING_READONLY:
        return _starter_bound_app_bundle(kind)
    return {
        "schema_version": APP_BUNDLE_SCHEMA_VERSION,
        "app": {
            "id": "incident_console",
            "title": "Incident Console",
            "kind": "internal_tool",
            "root_route": "/",
        },
        "routes": [
            {"id": "queue", "path": "/", "label": "Queue", "screen_id": "queue"},
            {"id": "detail", "path": "/incident", "label": "Incident", "screen_id": "detail"},
        ],
        "resources": [
            {
                "id": "incidents",
                "kind": "fixture",
                "records": [
                    {"id": "inc_1042", "severity": "high", "status": "investigating"},
                    {"id": "inc_1043", "severity": "medium", "status": "queued"},
                ],
            }
        ],
        "screens": [
            {
                "id": "queue",
                "title": "Incident Queue",
                "intent_bundle": _starter_queue_screen_intent(),
            },
            {
                "id": "detail",
                "title": "Incident Detail",
                "intent_bundle": _starter_detail_screen_intent(),
            },
        ],
    }


def _starter_bound_app_bundle(kind: str) -> dict[str, Any]:
    resources = [
        {
            "id": "incidents",
            "kind": "fixture",
            "records": [
                {"id": "inc_1042", "severity": "high", "status": "investigating"},
                {"id": "inc_1043", "severity": "medium", "status": "queued"},
            ],
        }
    ]
    return {
        "schema_version": APP_BUNDLE_BOUND_SCHEMA_VERSION,
        "resource_binding": APP_BUNDLE_RESOURCE_BINDING_READONLY,
        "app": {
            "id": "incident_console",
            "title": "Incident Console",
            "kind": kind,
            "root_route": "/",
        },
        "routes": [
            {"id": "queue", "path": "/", "label": "Queue", "screen_id": "queue"},
            {"id": "detail", "path": "/incident", "label": "Incident", "screen_id": "detail"},
        ],
        "resources": resources,
        "screens": [
            {
                "id": "queue",
                "title": "Incident Queue",
                "resource_views": [
                    {
                        "id": "queue_incidents",
                        "resource_id": "incidents",
                        "mode": "list",
                        "record_ids": ["inc_1042", "inc_1043"],
                        "fields": ["id", "severity", "status"],
                        "target_motif_id": "incidents",
                    }
                ],
                "intent_bundle": _starter_bound_queue_screen_intent(),
            },
            {
                "id": "detail",
                "title": "Incident Detail",
                "resource_views": [
                    {
                        "id": "detail_incident",
                        "resource_id": "incidents",
                        "mode": "list",
                        "record_ids": ["inc_1042"],
                        "fields": ["id", "severity", "status"],
                        "target_motif_id": "incident",
                    }
                ],
                "intent_bundle": _starter_bound_detail_screen_intent(),
            },
        ],
    }


def init_app_file(
    path: str | Path = "viewspec.app.json",
    *,
    kind: str = "internal_tool",
    force: bool = False,
    resource_binding: str = APP_BUNDLE_RESOURCE_BINDING,
) -> Path:
    output = Path(path)
    if output.exists() and not force:
        raise ValueError(f"{output} already exists; pass --force to overwrite")
    payload = starter_app_bundle(kind, resource_binding=resource_binding)
    atomic_write(output, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return output


def prove_app(
    *,
    app_path: str | Path,
    out_dir: str | Path = APP_BUNDLE_DEFAULT_OUT,
    design_path: str | Path | None = None,
    strict_design: bool = False,
    force: bool = False,
    report_out: str | Path | None = None,
    with_shell: bool = False,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    timings: dict[str, int] = {}
    root = resolve_cwd(cwd)
    output_dir = resolve_local_path(out_dir, cwd=root, allow_outside_cwd=True)
    report_path = resolve_local_path(report_out, cwd=root, allow_outside_cwd=True) if report_out else output_dir / APP_BUNDLE_DEFAULT_REPORT
    try:
        source = resolve_local_path(app_path, cwd=root, allow_outside_cwd=True, must_exist=True)
        design = (
            resolve_local_path(design_path, cwd=root, allow_outside_cwd=True, must_exist=True)
            if design_path is not None
            else None
        )
        app_text = source.read_text(encoding="utf-8")
        validation = _time_phase(timings, "validate_app", lambda: validate_app_text(app_text, compile_check=True))
        if not validation["ok"]:
            return _app_proof_failure_report(
                output_dir=output_dir,
                report_path=report_path,
                errors=[
                    {
                        "code": issue["code"],
                        "message": issue["message"],
                        "fix": issue.get("suggestion") or "Fix the AppBundle and retry prove-app.",
                    }
                    for issue in validation["issues"]
                ],
                timings=timings,
                validation=validation,
                write=False,
            )
        payload = json.loads(app_text, parse_constant=_reject_json_constant)
        _assert_report_under_output(report_path, output_dir)
        _prepare_app_output_dir(output_dir, root=root, force=force, raw_out=out_dir)
        prepared = _PreparedAppProof(
            output_dir=output_dir,
            app_path=source,
            design_path=design,
            report_path=report_path,
            summary_path=output_dir / APP_BUNDLE_DEFAULT_SUMMARY,
            support_path=output_dir / APP_BUNDLE_DEFAULT_SUPPORT_BUNDLE,
        )
        screen_reports = _time_phase(
            timings,
            "screens",
            lambda: _prove_app_screens(payload, prepared, root=root, strict_design=strict_design),
        )
        errors = [error for screen in screen_reports for error in screen.get("errors", []) if isinstance(error, dict)]
        binding_report: dict[str, Any] | None = None
        if not errors:
            binding_report = _time_phase(timings, "resource_binding", lambda: _resource_binding_assertion_report(payload, screen_reports))
            if isinstance(binding_report, dict) and not binding_report.get("ok"):
                errors.extend(_normalize_proof_errors(binding_report.get("errors")))
        shell_report: dict[str, Any] | None = None
        if with_shell and not errors:
            shell_report = _time_phase(
                timings,
                "shell",
                lambda: _write_static_app_shell(
                    payload,
                    screen_reports,
                    _PreparedAppShell(
                        output_dir=prepared.output_dir / APP_SHELL_DIR_NAME,
                        app_path=source,
                        design_path=design,
                        manifest_path=prepared.output_dir / APP_SHELL_DIR_NAME / APP_SHELL_MANIFEST,
                        diagnostics_path=prepared.output_dir / APP_SHELL_DIR_NAME / APP_SHELL_DIAGNOSTICS,
                        index_path=prepared.output_dir / APP_SHELL_DIR_NAME / APP_SHELL_INDEX,
                    ),
                    root=root,
                    force=False,
                    raw_out=APP_SHELL_DIR_NAME,
                    strict_design=strict_design,
                    validation=validation,
                    clean_output=True,
                    resource_binding_report=binding_report,
                ),
            )
            if not shell_report.get("ok"):
                errors.extend(_normalize_proof_errors(shell_report.get("errors")))
        route_assertions = _route_assertions(payload)
        report = _app_proof_report(
            ok=not errors,
            prepared=prepared,
            app_payload=payload,
            validation=validation,
            route_assertions=route_assertions,
            screen_reports=screen_reports,
            errors=_normalize_proof_errors(errors),
            timings=timings,
            strict_design=strict_design,
            shell=shell_report,
            resource_binding_report=binding_report,
        )
        return _write_app_proof(report, prepared)
    except AppBundleProofFailure as exc:
        report = _app_proof_failure_report(
            output_dir=output_dir,
            report_path=report_path,
            errors=[{"code": exc.code, "message": exc.message, "fix": exc.fix}],
            timings=timings,
            validation=None,
            write=_should_write_app_proof_failure(output_dir, exc.code),
        )
        if _should_write_app_proof_failure(output_dir, exc.code):
            prepared = _PreparedAppProof(output_dir, Path(app_path), None, report_path, output_dir / APP_BUNDLE_DEFAULT_SUMMARY, output_dir / APP_BUNDLE_DEFAULT_SUPPORT_BUNDLE)
            return _write_app_proof(report, prepared)
        return report
    except Exception as exc:
        report = _app_proof_failure_report(
            output_dir=output_dir,
            report_path=report_path,
            errors=[
                {
                    "code": "APP_PROOF_INTERNAL_ERROR",
                    "message": str(exc),
                    "fix": "Fix the local AppBundle proof environment or paths and retry prove-app.",
                }
            ],
            timings=timings,
            validation=None,
            write=output_dir.exists(),
        )
        if output_dir.exists():
            prepared = _PreparedAppProof(output_dir, Path(app_path), None, report_path, output_dir / APP_BUNDLE_DEFAULT_SUMMARY, output_dir / APP_BUNDLE_DEFAULT_SUPPORT_BUNDLE)
            return _write_app_proof(report, prepared)
        return report


def compile_app(
    app_path: str | Path,
    *,
    out_dir: str | Path = APP_SHELL_DEFAULT_OUT,
    design_path: str | Path | None = None,
    strict_design: bool = False,
    force: bool = False,
    target: str = APP_SHELL_TARGET,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    timings: dict[str, int] = {}
    root = resolve_cwd(cwd)
    output_dir = resolve_local_path(out_dir, cwd=root, allow_outside_cwd=True)
    if target != APP_SHELL_TARGET:
        return _app_shell_failure_report(
            output_dir=output_dir,
            errors=[
                {
                    "code": "APP_SHELL_TARGET_UNSUPPORTED",
                    "message": f"Static Shell V0 supports {APP_SHELL_TARGET} only.",
                    "fix": "Use --target html-tailwind-app.",
                }
            ],
            timings=timings,
            validation=None,
        )
    try:
        source = resolve_local_path(app_path, cwd=root, allow_outside_cwd=True, must_exist=True)
        design = (
            resolve_local_path(design_path, cwd=root, allow_outside_cwd=True, must_exist=True)
            if design_path is not None
            else None
        )
        app_text = source.read_text(encoding="utf-8")
        validation = _time_phase(timings, "validate_app", lambda: validate_app_text(app_text, compile_check=True))
        if not validation["ok"]:
            return _app_shell_failure_report(
                output_dir=output_dir,
                errors=[
                    {
                        "code": issue["code"],
                        "message": issue["message"],
                        "fix": issue.get("suggestion") or "Fix the AppBundle and retry compile-app.",
                    }
                    for issue in validation["issues"]
                ],
                timings=timings,
                validation=validation,
            )
        payload = json.loads(app_text, parse_constant=_reject_json_constant)
        prepared_shell = _PreparedAppShell(
            output_dir=output_dir,
            app_path=source,
            design_path=design,
            manifest_path=output_dir / APP_SHELL_MANIFEST,
            diagnostics_path=output_dir / APP_SHELL_DIAGNOSTICS,
            index_path=output_dir / APP_SHELL_INDEX,
        )
        _prepare_app_shell_output_dir(output_dir, root=root, force=force, raw_out=out_dir)
        prepared_proof = _PreparedAppProof(
            output_dir=output_dir,
            app_path=source,
            design_path=design,
            report_path=output_dir / "unused_app_proof_report.json",
            summary_path=output_dir / "unused_APP_PROOF.md",
            support_path=output_dir / "unused_support_bundle.json",
        )
        screen_reports = _time_phase(
            timings,
            "screens",
            lambda: _prove_app_screens(payload, prepared_proof, root=root, strict_design=strict_design),
        )
        errors = [error for screen in screen_reports for error in screen.get("errors", []) if isinstance(error, dict)]
        binding_report: dict[str, Any] | None = None
        if not errors:
            binding_report = _time_phase(timings, "resource_binding", lambda: _resource_binding_assertion_report(payload, screen_reports))
            if isinstance(binding_report, dict) and not binding_report.get("ok"):
                errors.extend(_normalize_proof_errors(binding_report.get("errors")))
        if errors:
            return _app_shell_report(
                ok=False,
                prepared=prepared_shell,
                app_payload=payload,
                validation=validation,
                screen_reports=screen_reports,
                errors=_normalize_proof_errors(errors),
                timings=timings,
                strict_design=strict_design,
                shell_payload=None,
                resource_binding_report=binding_report,
            )
        shell_report = _time_phase(
            timings,
            "shell",
            lambda: _write_static_app_shell(
                payload,
                screen_reports,
                prepared_shell,
                root=root,
                force=False,
                raw_out=out_dir,
                strict_design=strict_design,
                validation=validation,
                clean_output=False,
                resource_binding_report=binding_report,
            ),
        )
        if not shell_report.get("ok"):
            return _app_shell_report(
                ok=False,
                prepared=prepared_shell,
                app_payload=payload,
                validation=validation,
                screen_reports=screen_reports,
                errors=_normalize_proof_errors(shell_report.get("errors")),
                timings=timings,
                strict_design=strict_design,
                shell_payload=shell_report,
                resource_binding_report=binding_report,
            )
        return _app_shell_report(
            ok=True,
            prepared=prepared_shell,
            app_payload=payload,
            validation=validation,
            screen_reports=screen_reports,
            errors=[],
            timings=timings,
            strict_design=strict_design,
            shell_payload=shell_report,
            resource_binding_report=binding_report,
        )
    except AppBundleProofFailure as exc:
        return _app_shell_failure_report(
            output_dir=output_dir,
            errors=[{"code": exc.code, "message": exc.message, "fix": exc.fix}],
            timings=timings,
            validation=None,
        )
    except Exception as exc:
        return _app_shell_failure_report(
            output_dir=output_dir,
            errors=[
                {
                    "code": "APP_SHELL_INTERNAL_ERROR",
                    "message": str(exc),
                    "fix": "Fix the local AppBundle shell environment or paths and retry compile-app.",
                }
            ],
            timings=timings,
            validation=None,
        )


def init_app_tool(
    out: str | Path = "viewspec.app.json",
    *,
    kind: str = "internal_tool",
    resource_binding: str = APP_BUNDLE_RESOURCE_BINDING,
    force: bool = False,
    cwd: str | Path | None = None,
    allow_outside_cwd: bool = False,
) -> dict[str, Any]:
    root: Path | None = None
    try:
        root = resolve_cwd(cwd)
        output = resolve_local_path(out, cwd=root, allow_outside_cwd=allow_outside_cwd)
        normalized_binding = resource_binding.replace("-", "_")
        init_app_file(output, kind=kind, resource_binding=normalized_binding, force=force)
        validation = validate_app_file(output)
        return tool_response(
            validation["ok"],
            "Wrote starter AppBundle." if validation["ok"] else "Wrote starter AppBundle, but validation failed.",
            paths={"app": str(output)},
            data={"validation": validation},
            next_actions=[
                "Replace sample internal-tool labels and records with real app context.",
                "Run viewspec prove-app --app viewspec.app.json --out .viewspec-app-proof --json.",
            ],
            metadata={
                **path_policy_metadata(root, allow_outside_cwd),
                "sdk_version": __version__,
                "network_calls": "none",
                "kind": kind,
                "resource_binding": validation.get("resource_binding"),
                "binding_scope": validation.get("binding_scope"),
            },
        )
    except Exception as exc:
        return exception_response(
            exc,
            "IO_ERROR",
            "Choose a writable app path, valid kind, or pass force=True.",
            metadata=path_policy_metadata(root, allow_outside_cwd),
        )


def validate_app_file_tool(
    path: str | Path,
    *,
    compile_check: bool = True,
    cwd: str | Path | None = None,
    allow_outside_cwd: bool = False,
) -> dict[str, Any]:
    root: Path | None = None
    try:
        root = resolve_cwd(cwd)
        source = resolve_local_path(path, cwd=root, allow_outside_cwd=allow_outside_cwd, must_exist=True)
        validation = validate_app_file(source, compile_check=compile_check)
        return tool_response(
            validation["ok"],
            "AppBundle is valid." if validation["ok"] else "AppBundle validation failed.",
            paths={"app": str(source)},
            errors=[
                {
                    "code": issue["code"],
                    "message": issue["message"],
                    "fix": issue.get("suggestion") or "Regenerate the AppBundle.",
                }
                for issue in validation["issues"]
            ],
            data={"validation": validation},
            next_actions=[] if validation["ok"] else ["Regenerate viewspec.app.json using the AppBundle contract."],
            metadata={
                **path_policy_metadata(root, allow_outside_cwd),
                "sdk_version": __version__,
                "network_calls": "none",
                "compile_check": validation["compile_check"],
                "resource_binding": validation.get("resource_binding"),
                "binding_scope": validation.get("binding_scope"),
            },
        )
    except Exception as exc:
        return exception_response(
            exc,
            "INVALID_PATH",
            "Fix the app file path and retry validate_app_file.",
            metadata=path_policy_metadata(root, allow_outside_cwd),
        )


def diff_app_files_tool(
    left_path: str | Path,
    right_path: str | Path,
    *,
    compile_check: bool = True,
    cwd: str | Path | None = None,
    allow_outside_cwd: bool = False,
) -> dict[str, Any]:
    root: Path | None = None
    try:
        root = resolve_cwd(cwd)
        left = resolve_local_path(left_path, cwd=root, allow_outside_cwd=allow_outside_cwd, must_exist=True)
        right = resolve_local_path(right_path, cwd=root, allow_outside_cwd=allow_outside_cwd, must_exist=True)
        diff = diff_app_files(left, right, compile_check=compile_check)
        semantic_summary = app_semantic_change_lines(diff.get("semantic_changes"))
        return tool_response(
            diff["ok"],
            "Computed AppBundle semantic diff." if diff["ok"] else "AppBundle diff failed validation.",
            paths={"left": str(left), "right": str(right)},
            errors=[
                {
                    "code": error["code"],
                    "message": error["message"],
                    "fix": error.get("fix") or "Fix the invalid AppBundle.",
                }
                for error in diff["errors"]
            ],
            data={"diff": diff, "semantic_summary": semantic_summary},
            next_actions=[] if diff["ok"] else ["Fix invalid AppBundle files before comparing them."],
            metadata={
                **path_policy_metadata(root, allow_outside_cwd),
                "sdk_version": __version__,
                "network_calls": "none",
                "compile_check": diff["compile_check"],
                "semantic_change_count": len(semantic_summary),
                "topology_similarity": diff["topology_similarity"],
            },
        )
    except Exception as exc:
        return exception_response(
            exc,
            "DIFF_FAILED",
            "Fix the compared AppBundle paths and retry diff_app_files.",
            metadata=path_policy_metadata(root, allow_outside_cwd),
        )


def compile_app_tool(
    app_path: str | Path,
    out_dir: str | Path = APP_SHELL_DEFAULT_OUT,
    *,
    design_path: str | Path | None = None,
    strict_design: bool = False,
    force: bool = False,
    target: str = APP_SHELL_TARGET,
    cwd: str | Path | None = None,
    allow_outside_cwd: bool = False,
) -> dict[str, Any]:
    root: Path | None = None
    try:
        root = resolve_cwd(cwd)
        source = resolve_local_path(app_path, cwd=root, allow_outside_cwd=allow_outside_cwd, must_exist=True)
        output = resolve_local_path(out_dir, cwd=root, allow_outside_cwd=allow_outside_cwd)
        design = (
            resolve_local_path(design_path, cwd=root, allow_outside_cwd=allow_outside_cwd, must_exist=True)
            if design_path is not None
            else None
        )
        result = compile_app(
            source,
            out_dir=output,
            design_path=design,
            strict_design=strict_design,
            force=force,
            target=target,
            cwd=root,
        )
        errors = _normalize_proof_errors(result.get("errors")) if not result.get("ok") else []
        return tool_response(
            bool(result.get("ok")),
            "Compiled Static Shell V0 app artifact." if result.get("ok") else "Static Shell V0 compile failed.",
            paths={key: str(value) for key, value in result.get("paths", {}).items() if value},
            errors=errors,
            next_actions=[] if result.get("ok") else ["Fix the reported app shell issue and retry compile_app."],
            metadata={
                **path_policy_metadata(root, allow_outside_cwd),
                "schema_version": MCP_RESULT_SCHEMA_VERSION,
                "target": result.get("target"),
                "route_navigation": result.get("route_navigation"),
                "network_calls": result.get("policy", {}).get("network_calls", "none"),
                "resource_binding": result.get("resource_binding"),
                "binding_scope": result.get("binding_scope"),
                "binding_digest": (
                    result.get("resource_binding_assertions", {}).get("binding_digest")
                    if isinstance(result.get("resource_binding_assertions"), dict)
                    else None
                ),
                "route_assertions": result.get("route_assertions"),
                "app": result.get("app"),
                "shell_artifact_hash": result.get("shell_artifact_hash"),
                "shell_manifest_hash": result.get("shell_manifest_hash"),
                "state_reducer_hash": result.get("state_reducer_hash"),
                "state_manifest_hash": result.get("state_manifest_hash"),
                "state_contract_hash": result.get("state_contract_hash"),
                "state_reducer_conformance": _state_conformance_status(result.get("state_reducer_conformance")),
            },
            data={"compile_report": result},
        )
    except Exception as exc:
        if isinstance(exc, LocalToolError):
            return tool_error_response(exc.code, exc.message, exc.fix, metadata=path_policy_metadata(root, allow_outside_cwd))
        return tool_error_response(
            "APP_SHELL_INTERNAL_ERROR",
            str(exc),
            "Fix the app shell paths or local environment and retry compile_app.",
            metadata=path_policy_metadata(root, allow_outside_cwd),
        )


def prove_app_tool(
    *,
    app_path: str | Path,
    out_dir: str | Path = APP_BUNDLE_DEFAULT_OUT,
    design_path: str | Path | None = None,
    strict_design: bool = False,
    force: bool = False,
    report_out: str | Path | None = None,
    with_shell: bool = False,
    cwd: str | Path | None = None,
    allow_outside_cwd: bool = False,
) -> dict[str, Any]:
    root: Path | None = None
    try:
        root = resolve_cwd(cwd)
        source = resolve_local_path(app_path, cwd=root, allow_outside_cwd=allow_outside_cwd, must_exist=True)
        output = resolve_local_path(out_dir, cwd=root, allow_outside_cwd=allow_outside_cwd)
        design = (
            resolve_local_path(design_path, cwd=root, allow_outside_cwd=allow_outside_cwd, must_exist=True)
            if design_path is not None
            else None
        )
        report = (
            resolve_local_path(report_out, cwd=root, allow_outside_cwd=allow_outside_cwd)
            if report_out is not None
            else None
        )
        proof = prove_app(
            app_path=source,
            out_dir=output,
            design_path=design,
            strict_design=strict_design,
            force=force,
            report_out=report,
            with_shell=with_shell,
            cwd=root,
        )
        errors = _normalize_proof_errors(proof.get("errors")) if not proof.get("ok") else []
        return tool_response(
            bool(proof.get("ok")),
            "ViewSpec app proof passed." if proof.get("ok") else "ViewSpec app proof failed.",
            paths={key: str(value) for key, value in proof.get("paths", {}).items() if value},
            errors=errors,
            next_actions=[] if proof.get("ok") else ["Fix the reported app proof issue and retry prove_app."],
            metadata={
                **path_policy_metadata(root, allow_outside_cwd),
                "schema_version": MCP_RESULT_SCHEMA_VERSION,
                "target": proof.get("target"),
                "proof_level": proof.get("proof_level"),
                "network_calls": proof.get("policy", {}).get("network_calls", "none"),
                "resource_binding": proof.get("resource_binding"),
                "binding_scope": proof.get("binding_scope"),
                "binding_digest": (
                    proof.get("resource_binding_assertions", {}).get("binding_digest")
                    if isinstance(proof.get("resource_binding_assertions"), dict)
                    else None
                ),
                "route_assertions": proof.get("route_assertions"),
                "route_navigation": proof.get("route_navigation"),
                "shell_artifact_hash": proof.get("shell_artifact_hash"),
                "shell_manifest_hash": proof.get("shell_manifest_hash"),
                "state_reducer_hash": proof.get("state_reducer_hash"),
                "state_manifest_hash": proof.get("state_manifest_hash"),
                "state_contract_hash": proof.get("state_contract_hash"),
                "state_reducer_conformance": _state_conformance_status(proof.get("state_reducer_conformance")),
                "app": proof.get("app"),
                "screen_count": len(proof.get("screens", [])) if isinstance(proof.get("screens"), list) else 0,
                "proof_identity": _app_tool_proof_identity(proof),
            },
            data={"proof_report": proof},
        )
    except Exception as exc:
        if isinstance(exc, LocalToolError):
            return tool_error_response(exc.code, exc.message, exc.fix, metadata=path_policy_metadata(root, allow_outside_cwd))
        return tool_error_response(
            "APP_PROOF_INTERNAL_ERROR",
            str(exc),
            "Fix the app proof paths or local environment and retry prove_app.",
            metadata=path_policy_metadata(root, allow_outside_cwd),
        )


def _starter_queue_screen_intent() -> dict[str, Any]:
    builder = ViewSpecBuilder("incident_queue", root_attrs={"title": "Incident Queue"})
    table = builder.add_table("incidents", region="main", group_id="incident_rows")
    table.add_row(label="INC-1042", value="High - Investigating", id="inc_1042")
    table.add_row(label="INC-1043", value="Medium - Queued", id="inc_1043")
    return builder.build_bundle().to_json()


def _starter_detail_screen_intent() -> dict[str, Any]:
    builder = ViewSpecBuilder("incident_detail", root_attrs={"title": "Incident Detail"})
    detail = builder.add_detail("incident", region="main", group_id="incident_fields")
    detail.add_field(label="Incident", value="INC-1042", id="identifier")
    detail.add_field(label="Severity", value="High", id="severity")
    detail.add_field(label="Status", value="Investigating", id="status")
    detail.add_field(label="Owner", value="On-call Response", id="owner")
    return builder.build_bundle().to_json()


def _starter_bound_queue_screen_intent() -> dict[str, Any]:
    builder = ViewSpecBuilder("incident_queue", root_attrs={"title": "Incident Queue"})
    members: list[str] = []
    for record in (
        {"id": "inc_1042", "severity": "high", "status": "investigating"},
        {"id": "inc_1043", "severity": "medium", "status": "queued"},
    ):
        record_id = str(record["id"])
        builder.add_node(record_id, "table_row", attrs=dict(record))
        members.extend(
            [
                builder.bind_attr(f"{record_id}_id", record_id, "id", present_as="label"),
                builder.bind_attr(f"{record_id}_severity", record_id, "severity", present_as="value"),
                builder.bind_attr(f"{record_id}_status", record_id, "status", present_as="value"),
            ]
        )
    builder.add_group("incident_rows", "ordered", members, target_region="main")
    builder.add_motif("incidents", "table", "main", members)
    return builder.build_bundle().to_json()


def _starter_bound_detail_screen_intent() -> dict[str, Any]:
    builder = ViewSpecBuilder("incident_detail", root_attrs={"title": "Incident Detail"})
    record = {"id": "inc_1042", "severity": "high", "status": "investigating"}
    builder.add_node("inc_1042", "detail_field", attrs=dict(record))
    members = [
        builder.bind_attr("inc_1042_id", "inc_1042", "id", present_as="label"),
        builder.bind_attr("inc_1042_severity", "inc_1042", "severity", present_as="value"),
        builder.bind_attr("inc_1042_status", "inc_1042", "status", present_as="value"),
    ]
    builder.add_group("incident_fields", "ordered", members, target_region="main")
    builder.add_motif("incident", "detail", "main", members)
    return builder.build_bundle().to_json()


def _prepare_app_output_dir(output_dir: Path, *, root: Path, force: bool, raw_out: str | Path) -> None:
    _assert_safe_app_output(output_dir, root=root, raw_out=raw_out)
    if output_dir.exists():
        if not force:
            raise AppBundleProofFailure(
                "APP_PROOF_OUTPUT_EXISTS",
                f"App proof output already exists: {output_dir}",
                "Pass --force or choose a new --out directory.",
            )
        if not output_dir.is_dir():
            raise AppBundleProofFailure(
                "APP_PROOF_OUTPUT_UNSAFE",
                f"App proof output is not a directory: {output_dir}",
                "Choose a dedicated app proof output directory.",
            )
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)


def _assert_safe_app_output(output_dir: Path, *, root: Path, raw_out: str | Path) -> None:
    raw_parts = [str(part) for part in Path(raw_out).parts]
    if ".." in raw_parts:
        raise AppBundleProofFailure(
            "APP_PROOF_OUTPUT_UNSAFE",
            "App proof output path must not contain parent traversal.",
            "Use a direct child output path.",
        )
    resolved = output_dir.resolve()
    home = Path.home().resolve()
    repo_root = _repo_root(root)
    drive_root = Path(resolved.anchor).resolve() if resolved.anchor else resolved
    blocked = {root.resolve(), repo_root, home, drive_root}
    if resolved in blocked:
        raise AppBundleProofFailure(
            "APP_PROOF_OUTPUT_UNSAFE",
            f"Refusing unsafe app proof output directory: {resolved}",
            "Use a dedicated app proof output directory such as .viewspec-app-proof.",
        )
    for parent in (root.resolve(), repo_root, home):
        if _is_parent(resolved, parent):
            raise AppBundleProofFailure(
                "APP_PROOF_OUTPUT_UNSAFE",
                f"Refusing app proof output that is a parent of a protected directory: {resolved}",
                "Use a dedicated child output directory.",
            )


def _assert_report_under_output(report_path: Path, output_dir: Path) -> None:
    if not _is_relative_to(report_path.resolve(), output_dir.resolve()):
        raise AppBundleProofFailure(
            "APP_PROOF_REPORT_PATH_UNSAFE",
            f"App proof report path must stay under proof root: {report_path}",
            "Write the proof report under the --out directory or omit --report-out.",
        )


def _should_write_app_proof_failure(output_dir: Path, code: str) -> bool:
    no_write_codes = {
        "APP_PROOF_OUTPUT_EXISTS",
        "APP_PROOF_OUTPUT_UNSAFE",
        "APP_PROOF_REPORT_PATH_UNSAFE",
    }
    return output_dir.exists() and code not in no_write_codes


def _prepare_app_shell_output_dir(output_dir: Path, *, root: Path, force: bool, raw_out: str | Path) -> None:
    _assert_safe_app_shell_output(output_dir, root=root, raw_out=raw_out)
    if output_dir.exists():
        if not force:
            raise AppBundleProofFailure(
                "APP_SHELL_OUTPUT_EXISTS",
                f"Static shell output already exists: {output_dir}",
                "Pass --force or choose a new --out directory.",
            )
        if not output_dir.is_dir():
            raise AppBundleProofFailure(
                "APP_SHELL_OUTPUT_PATH_UNSAFE",
                f"Static shell output is not a directory: {output_dir}",
                "Choose a dedicated app shell output directory.",
            )
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)


def _assert_safe_app_shell_output(output_dir: Path, *, root: Path, raw_out: str | Path) -> None:
    raw_parts = [str(part) for part in Path(raw_out).parts]
    if ".." in raw_parts:
        raise AppBundleProofFailure(
            "APP_SHELL_OUTPUT_PATH_UNSAFE",
            "Static shell output path must not contain parent traversal.",
            "Use a direct child output path.",
        )
    resolved = output_dir.resolve()
    home = Path.home().resolve()
    repo_root = _repo_root(root)
    drive_root = Path(resolved.anchor).resolve() if resolved.anchor else resolved
    blocked = {root.resolve(), repo_root, home, drive_root}
    if resolved in blocked:
        raise AppBundleProofFailure(
            "APP_SHELL_OUTPUT_PATH_UNSAFE",
            f"Refusing unsafe static shell output directory: {resolved}",
            "Use a dedicated app shell output directory such as app-dist.",
        )
    for parent in (root.resolve(), repo_root, home):
        if _is_parent(resolved, parent):
            raise AppBundleProofFailure(
                "APP_SHELL_OUTPUT_PATH_UNSAFE",
                f"Refusing static shell output that is a parent of a protected directory: {resolved}",
                "Use a dedicated child output directory.",
            )


def _write_static_app_shell(
    payload: dict[str, Any],
    screen_reports: list[dict[str, Any]],
    prepared: _PreparedAppShell,
    *,
    root: Path,
    force: bool,
    raw_out: str | Path,
    strict_design: bool,
    validation: dict[str, Any],
    clean_output: bool,
    resource_binding_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if clean_output:
        _prepare_app_shell_output_dir(prepared.output_dir, root=root, force=force, raw_out=raw_out)
    _assert_under_proof_root(prepared.output_dir, prepared.output_dir)
    binding_fields = _resource_binding_report_fields(payload, resource_binding_report)
    shell_parts = _build_static_app_shell(payload, screen_reports, resource_binding_report=resource_binding_report)
    atomic_write(prepared.index_path, shell_parts["html"])
    shell_artifact_hash = file_hash(prepared.index_path)
    manifest = dict(shell_parts["manifest"])
    manifest["shell_artifact_hash"] = shell_artifact_hash
    state_artifacts = _write_state_artifacts(payload, prepared.output_dir)
    if state_artifacts is not None:
        manifest["state_ir"] = state_artifacts["manifest_summary"]
    _write_bounded_json(prepared.manifest_path, manifest, limit=APP_SHELL_MAX_MANIFEST_BYTES, code="APP_SHELL_MANIFEST_WRITE_FAILED")
    shell_manifest_hash = file_hash(prepared.manifest_path)
    diagnostics = {
        "schema_version": 1,
        "app_schema_version": _app_schema_version(payload),
        "ok": True,
        "target": APP_SHELL_TARGET,
        "route_navigation": APP_SHELL_ROUTE_NAVIGATION,
        "route_assertions": shell_parts["route_assertions"],
        "shell_artifact_hash": shell_artifact_hash,
        "shell_manifest_hash": shell_manifest_hash,
        "limits": _app_shell_limits(),
        **binding_fields,
        **({"state_ir": state_artifacts["manifest_summary"]} if state_artifacts is not None else {}),
        **({"state_contract_hash": state_artifacts["contract_hash"]} if state_artifacts is not None else {}),
        **({"state_reducer_conformance": state_artifacts["conformance"]} if state_artifacts is not None else {}),
    }
    _write_bounded_json(prepared.diagnostics_path, diagnostics, limit=APP_SHELL_MAX_MANIFEST_BYTES, code="APP_SHELL_DIAGNOSTICS_WRITE_FAILED")
    return {
        "ok": True,
        "target": APP_SHELL_TARGET,
        "route_navigation": APP_SHELL_ROUTE_NAVIGATION,
        "app_schema_version": _app_schema_version(payload),
        **binding_fields,
        "policy": {"network_calls": "none"},
        "app": _app_summary(payload),
        "paths": {
            "output_dir": str(prepared.output_dir),
            "index": str(prepared.index_path),
            "manifest": str(prepared.manifest_path),
            "diagnostics": str(prepared.diagnostics_path),
            **(
                {
                    "state_reducer": str(state_artifacts["reducer_path"]),
                    "state_manifest": str(state_artifacts["manifest_path"]),
                }
                if state_artifacts is not None
                else {}
            ),
        },
        "route_assertions": shell_parts["route_assertions"],
        "shell_artifact_hash": shell_artifact_hash,
        "shell_manifest_hash": shell_manifest_hash,
        **(
            {
                "state_reducer_hash": state_artifacts["reducer_hash"],
                "state_manifest_hash": state_artifacts["manifest_hash"],
                "state_contract_hash": state_artifacts["contract_hash"],
                "state_replay": state_artifacts["replay"],
                "state_reducer_conformance": state_artifacts["conformance"],
            }
            if state_artifacts is not None
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
        "errors": [],
    }


def _write_state_artifacts(payload: dict[str, Any], output_dir: Path) -> dict[str, Any] | None:
    if payload.get("schema_version") != APP_BUNDLE_STATE_SCHEMA_VERSION:
        return None
    reducer_path = output_dir / APP_STATE_REDUCER
    manifest_path = output_dir / APP_STATE_MANIFEST
    try:
        reducer = generate_typescript_reducer(payload)
        reducer_bytes = len(reducer.encode("utf-8"))
        if reducer_bytes > APP_STATE_MAX_REDUCER_BYTES:
            raise AppBundleProofFailure(
                "APP_STATE_REDUCER_LIMIT_EXCEEDED",
                f"Generated state reducer is {reducer_bytes} bytes; limit is {APP_STATE_MAX_REDUCER_BYTES}.",
                "Reduce AppBundle V3 state, mutation, or selector declarations.",
            )
        atomic_write(reducer_path, reducer)
        reducer_hash = file_hash(reducer_path)
        conformance = check_reducer_conformance(payload, reducer_source=reducer)
        if not conformance.get("ok"):
            errors = conformance.get("errors") if isinstance(conformance.get("errors"), list) else []
            message = errors[0].get("message") if errors and isinstance(errors[0], dict) else "Generated reducer diverged from the Python state interpreter."
            raise AppBundleProofFailure(
                "APP_STATE_REDUCER_CONFORMANCE_FAILED",
                str(message),
                "Fix the AppBundle V3 state contract or generated reducer semantics and retry.",
            )
        manifest = state_manifest(payload, reducer_hash=reducer_hash, conformance_report=conformance)
        replay = manifest.get("replay") if isinstance(manifest.get("replay"), dict) else {}
        if replay and not replay.get("ok"):
            raise AppBundleProofFailure(
                "APP_STATE_REPLAY_ASSERTION_FAILED",
                "State replay assertions failed.",
                "Fix state_replay_assertions or the referenced mutation operations.",
            )
        _write_bounded_json(
            manifest_path,
            manifest,
            limit=APP_STATE_MAX_MANIFEST_BYTES,
            code="APP_STATE_MANIFEST_WRITE_FAILED",
        )
    except AppBundleProofFailure:
        raise
    except Exception as exc:
        raise AppBundleProofFailure(
            "APP_STATE_REDUCER_WRITE_FAILED",
            f"Failed to write state reducer artifacts: {exc}",
            "Fix the AppBundle V3 state contract and retry.",
        ) from exc
    manifest_hash = file_hash(manifest_path)
    return {
        "reducer_path": reducer_path,
        "manifest_path": manifest_path,
        "reducer_hash": reducer_hash,
        "manifest_hash": manifest_hash,
        "manifest_summary": {
            "profile": INTERACTIVE_STATE_PROFILE,
            "reducer_hash": reducer_hash,
            "manifest_hash": manifest_hash,
            "state_count": len(payload.get("state", [])) if isinstance(payload.get("state"), list) else 0,
            "mutation_count": len(payload.get("mutations", [])) if isinstance(payload.get("mutations"), list) else 0,
            "selector_count": len(payload.get("selectors", [])) if isinstance(payload.get("selectors"), list) else 0,
            "replay_ok": bool(manifest.get("replay", {}).get("ok")) if isinstance(manifest.get("replay"), dict) else True,
            "contract_hash": manifest.get("contract_hash"),
            "reducer_conformance": _state_conformance_status(conformance),
        },
        "replay": manifest.get("replay") if isinstance(manifest.get("replay"), dict) else None,
        "contract_hash": manifest.get("contract_hash"),
        "conformance": conformance,
    }


def _prove_app_screens(
    payload: dict[str, Any],
    prepared: _PreparedAppProof,
    *,
    root: Path,
    strict_design: bool,
) -> list[dict[str, Any]]:
    screen_reports: list[dict[str, Any]] = []
    screens = payload.get("screens") if isinstance(payload.get("screens"), list) else []
    for screen in screens:
        screen_id = str(screen["id"])
        screen_dir = prepared.output_dir / "screens" / screen_id
        artifact_dir = screen_dir / "artifact"
        _assert_under_proof_root(screen_dir, prepared.output_dir)
        intent_path = screen_dir / "viewspec.intent.json"
        intent_text = json.dumps(screen["intent_bundle"], indent=2, sort_keys=True) + "\n"
        atomic_write(intent_path, intent_text)
        compiled = _compile_screen(
            intent_path,
            artifact_dir,
            design_path=prepared.design_path,
            strict_design=strict_design,
            root=root,
        )
        errors = _normalize_proof_errors(compiled.get("errors")) if not compiled.get("ok") else []
        manifest_path = artifact_dir / "provenance_manifest.json"
        diagnostics_path = artifact_dir / "diagnostics.json"
        artifact_path = artifact_dir / "index.html"
        check = check_artifact_dir(artifact_dir) if artifact_dir.exists() else {"ok": False, "errors": ["artifact directory missing"], "manifest_summary": None}
        if not check.get("ok") and not errors:
            errors = [
                {
                    "code": "APP_PROOF_SCREEN_CHECK_FAILED",
                    "message": str(item),
                    "fix": "Fix the embedded screen IntentBundle and retry prove-app.",
                }
                for item in check.get("errors", [])
            ]
        manifest_summary = summarize_intent_manifest(manifest_path) if manifest_path.exists() else None
        if not errors and (not isinstance(manifest_summary, dict) or manifest_summary.get("available") is not True):
            errors.append(
                {
                    "code": "APP_PROOF_MANIFEST_SUMMARY_FAILED",
                    "message": f"Screen {screen_id} manifest summary unavailable.",
                    "fix": "Regenerate the screen artifact from a valid embedded IntentBundle.",
                }
            )
        screen_reports.append(
            {
                "id": screen_id,
                "title": screen.get("title"),
                "validation_status": "passed" if not errors else "failed",
                "compile_status": "passed" if compiled.get("ok") else "failed",
                "check_status": "passed" if check.get("ok") else "failed",
                "artifact_hash": file_hash(artifact_path) if artifact_path.exists() and not errors else None,
                "manifest_hash": file_hash(manifest_path) if manifest_path.exists() and not errors else None,
                "manifest_summary": manifest_summary,
                "paths": {
                    "intent": str(intent_path),
                    "artifact_dir": str(artifact_dir),
                    "artifact": str(artifact_path),
                    "manifest": str(manifest_path),
                    "diagnostics": str(diagnostics_path),
                },
                "errors": [
                    {
                        **error,
                        "screen_id": screen_id,
                    }
                    for error in errors
                ],
            }
        )
        if errors:
            break
    return screen_reports


def _compile_screen(
    intent_path: Path,
    artifact_dir: Path,
    *,
    design_path: Path | None,
    strict_design: bool,
    root: Path,
) -> dict[str, Any]:
    from viewspec.intent_tools import compile_intent_bundle_file_tool

    return compile_intent_bundle_file_tool(
        intent_path,
        artifact_dir,
        design_path=design_path,
        strict_design=strict_design,
        target=APP_BUNDLE_TARGET,
        cwd=root,
        allow_outside_cwd=True,
    )


def _assert_under_proof_root(path: Path, proof_root: Path) -> None:
    resolved = path.resolve()
    root = proof_root.resolve()
    if not _is_relative_to(resolved, root):
        raise AppBundleProofFailure(
            "APP_PROOF_OUTPUT_UNSAFE",
            f"Resolved proof path escaped proof root: {resolved}",
            "Use safe AppBundle ids and a dedicated proof output directory.",
        )


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
    combined_route_assertions = dict(route_assertions)
    if isinstance(shell, dict) and isinstance(shell.get("route_assertions"), dict):
        combined_route_assertions.update(shell["route_assertions"])
    binding_fields = _resource_binding_report_fields(app_payload, resource_binding_report)
    return {
        "schema_version": APP_BUNDLE_PROOF_SCHEMA_VERSION,
        "app_schema_version": _app_schema_version(app_payload),
        "ok": ok,
        "proof_level": APP_BUNDLE_PROOF_LEVEL,
        "target": APP_SHELL_TARGET if shell else APP_BUNDLE_TARGET,
        "app": _app_summary(app_payload),
        "paths": paths,
        "route_assertions": combined_route_assertions,
        **({"route_navigation": APP_SHELL_ROUTE_NAVIGATION} if shell else {}),
        **binding_fields,
        "screens": screen_reports,
        **({"shell": _compact_shell_report(shell)} if shell else {}),
        **({"shell_artifact_hash": shell.get("shell_artifact_hash")} if shell else {}),
        **({"shell_manifest_hash": shell.get("shell_manifest_hash")} if shell else {}),
        **({"state_reducer_hash": shell.get("state_reducer_hash")} if shell and shell.get("state_reducer_hash") else {}),
        **({"state_manifest_hash": shell.get("state_manifest_hash")} if shell and shell.get("state_manifest_hash") else {}),
        **({"state_contract_hash": shell.get("state_contract_hash")} if shell and shell.get("state_contract_hash") else {}),
        **({"state_replay": shell.get("state_replay")} if shell and shell.get("state_replay") else {}),
        **({"state_reducer_conformance": shell.get("state_reducer_conformance")} if shell and shell.get("state_reducer_conformance") else {}),
        "validation": _validation_summary(validation),
        "policy": {"network_calls": "none"},
        "metadata": {
            "sdk_version": __version__,
            "strict_design": bool(strict_design),
            "screen_source": "embedded_intents",
            **({"shell_kind": "static_local_hash_shell"} if shell else {}),
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
) -> dict[str, Any]:
    del write
    binding_fields = _resource_binding_fields_from_validation(validation)
    return {
        "schema_version": APP_BUNDLE_PROOF_SCHEMA_VERSION,
        "app_schema_version": validation.get("app_schema_version") if isinstance(validation, dict) else None,
        "ok": False,
        "proof_level": APP_BUNDLE_PROOF_LEVEL,
        "target": APP_BUNDLE_TARGET,
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
        "target": APP_SHELL_TARGET,
        "route_navigation": APP_SHELL_ROUTE_NAVIGATION,
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
) -> dict[str, Any]:
    binding_fields = _resource_binding_fields_from_validation(validation)
    return {
        "schema_version": APP_BUNDLE_PROOF_SCHEMA_VERSION,
        "app_schema_version": validation.get("app_schema_version") if isinstance(validation, dict) else None,
        "ok": False,
        "target": APP_SHELL_TARGET,
        "route_navigation": APP_SHELL_ROUTE_NAVIGATION,
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
    report.setdefault("timings_ms", {})["total"] = sum(value for value in report.get("timings_ms", {}).values() if isinstance(value, int))
    try:
        _write_bounded_json(prepared.report_path, report, limit=APP_BUNDLE_MAX_PROOF_REPORT_BYTES, code="APP_PROOF_REPORT_WRITE_FAILED")
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
        _write_bounded_json(prepared.report_path, failed, limit=APP_BUNDLE_MAX_PROOF_REPORT_BYTES, code="APP_PROOF_REPORT_WRITE_FAILED")
        try:
            _write_app_summary(prepared.summary_path, _render_app_proof_summary(failed, proof_report_hash=file_hash(prepared.report_path)))
        except Exception:
            pass
        return failed
    try:
        _write_app_summary(prepared.summary_path, _render_app_proof_summary(report, proof_report_hash=file_hash(prepared.report_path)))
    except Exception as exc:
        failed = _append_app_proof_error(report, exc, fallback_code="APP_PROOF_SUMMARY_WRITE_FAILED")
        failed["ok"] = False
        _write_bounded_json(prepared.report_path, failed, limit=APP_BUNDLE_MAX_PROOF_REPORT_BYTES, code="APP_PROOF_REPORT_WRITE_FAILED")
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
        "shell": {
            "route_navigation": _support_scalar(shell.get("route_navigation")),
            "shell_artifact_hash": _support_scalar(report.get("shell_artifact_hash") or shell.get("shell_artifact_hash")),
            "shell_manifest_hash": _support_scalar(report.get("shell_manifest_hash") or shell.get("shell_manifest_hash")),
            "state_reducer_hash": _support_scalar(report.get("state_reducer_hash") or shell.get("state_reducer_hash")),
            "state_manifest_hash": _support_scalar(report.get("state_manifest_hash") or shell.get("state_manifest_hash")),
            "state_contract_hash": _support_scalar(report.get("state_contract_hash") or shell.get("state_contract_hash")),
            "state_reducer_conformance": _support_scalar(
                _state_conformance_status(report.get("state_reducer_conformance") or shell.get("state_reducer_conformance"))
            ),
        }
        if shell or report.get("shell_artifact_hash") or report.get("shell_manifest_hash") or report.get("state_reducer_hash")
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
        "policy": {"network_calls": "none"},
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


def _state_conformance_status(report: object) -> str | None:
    if not isinstance(report, dict):
        return None
    return "passed" if report.get("ok") else "failed"


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
        "assertion_count": int(compact.get("assertion_count")) if isinstance(compact.get("assertion_count"), int) else 0,
        "passed_count": int(compact.get("passed_count")) if isinstance(compact.get("passed_count"), int) else 0,
        "failed_count": int(compact.get("failed_count")) if isinstance(compact.get("failed_count"), int) else 0,
        "view_count": int(compact.get("view_count")) if isinstance(compact.get("view_count"), int) else 0,
        "binding_digest": _support_scalar(compact.get("binding_digest")),
    }


def _render_app_proof_summary(report: dict[str, Any], *, proof_report_hash: str) -> str:
    app = report.get("app") if isinstance(report.get("app"), dict) else {}
    status = "PASSED" if report.get("ok") else "FAILED"
    lines = [
        "# ViewSpec App Proof",
        "",
        f"Status: **{status}**",
        f"Target: `{_summary_value(report.get('target'))}`",
        f"Proof level: `{_summary_value(report.get('proof_level'))}`",
        "Claim: AppBundle contract, static route graph, per-screen source artifact/provenance proof, and declared read-only fixture binding proof when enabled.",
        (
            "Non-claim: AppBundle proofs do not prove browser runtime navigation, dynamic routing, "
            "runtime data binding, deployable app scaffolding, pixel-perfect visual equivalence, "
            "accessibility certification, or hosted extended compiler behavior."
        ),
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
    binding = report.get("resource_binding_assertions") if isinstance(report.get("resource_binding_assertions"), dict) else None
    if binding:
        lines.extend(["", "## Resource Binding", ""])
        lines.append(f"- Binding scope: `{_summary_value(report.get('binding_scope') or binding.get('binding_scope'))}`")
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
            "- Network/install policy: `none`",
            "",
            "## Files",
            "",
        ]
    )
    paths = report.get("paths") if isinstance(report.get("paths"), dict) else {}
    for key in ("app", "design", "report", "proof_summary", "support_bundle", "app_shell_index", "app_shell_manifest", "app_shell_diagnostics"):
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


def _normalize_proof_errors(errors: object) -> list[dict[str, str]]:
    if not isinstance(errors, list) or not errors:
        return []
    normalized: list[dict[str, str]] = []
    for item in errors:
        if isinstance(item, dict):
            normalized.append(
                {
                    "code": str(item.get("code") or "APP_PROOF_FAILED"),
                    "message": str(item.get("message") or "App proof failed."),
                    "fix": str(item.get("fix") or "Inspect app_proof_report.json and retry."),
                    **({"screen_id": str(item["screen_id"])} if item.get("screen_id") else {}),
                }
            )
        else:
            normalized.append({"code": "APP_PROOF_FAILED", "message": str(item), "fix": "Inspect app_proof_report.json and retry."})
    return normalized


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


def _time_phase(timings: dict[str, int], phase: str, fn: Any) -> Any:
    started = time.perf_counter()
    try:
        return fn()
    finally:
        timings[phase] = timings.get(phase, 0) + int((time.perf_counter() - started) * 1000)


def _final_timings(timings: dict[str, int]) -> dict[str, int]:
    payload = dict(sorted(timings.items()))
    payload["total"] = sum(value for value in payload.values() if isinstance(value, int))
    return payload


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


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


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
    return {
        "proof_report_hash": _hash_path_if_present(paths.get("report")),
        "proof_summary_hash": _hash_path_if_present(paths.get("proof_summary")),
        "support_bundle_hash": _hash_path_if_present(paths.get("support_bundle")),
        "shell_artifact_hash": proof.get("shell_artifact_hash") if isinstance(proof.get("shell_artifact_hash"), str) else None,
        "shell_manifest_hash": proof.get("shell_manifest_hash") if isinstance(proof.get("shell_manifest_hash"), str) else None,
        "state_reducer_hash": proof.get("state_reducer_hash") if isinstance(proof.get("state_reducer_hash"), str) else None,
        "state_manifest_hash": proof.get("state_manifest_hash") if isinstance(proof.get("state_manifest_hash"), str) else None,
        "state_contract_hash": proof.get("state_contract_hash") if isinstance(proof.get("state_contract_hash"), str) else None,
        "state_reducer_conformance": _state_conformance_status(proof.get("state_reducer_conformance")),
        "screen_hashes": screen_hashes,  # type: ignore[dict-item]
    }


def _hash_path_if_present(path: object) -> str | None:
    if not path:
        return None
    candidate = Path(str(path))
    if not candidate.exists() or not candidate.is_file():
        return None
    return file_hash(candidate)


AGENT_APP_BUNDLE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://viewspec.dev/agent-app-bundle.schema.json",
    "title": "ViewSpec Agent AppBundle V1/V2/V3",
    "description": (
        "Local-only multi-screen app contract with embedded IntentBundles, static routes, V1 unbound "
        "fixtures, V2 read-only fixture binding proof, and V3 bounded interactive_state_v0 reducers."
    ),
    "oneOf": [
        {"$ref": "#/$defs/app_bundle_v1"},
        {"$ref": "#/$defs/app_bundle_v2"},
        {"$ref": "#/$defs/app_bundle_v3"},
    ],
    "x-viewspec-app-schema-versions": list(APP_BUNDLE_SUPPORTED_SCHEMA_VERSIONS),
    "x-viewspec-resource-binding": APP_BUNDLE_RESOURCE_BINDING,
    "x-viewspec-resource-bindings": [APP_BUNDLE_RESOURCE_BINDING, APP_BUNDLE_RESOURCE_BINDING_READONLY],
    "x-viewspec-binding-scope": APP_BUNDLE_BINDING_SCOPE,
    "x-viewspec-interactive-state": INTERACTIVE_STATE_PROFILE,
    "x-viewspec-embedded-intent-schema": "https://viewspec.dev/agent-intent-bundle.schema.json",
    "x-viewspec-invariants": [
        "AppBundles are local-only and no-network.",
        "schema_version 1 rejects resource_binding and resource_views, and reports unbound_v0.",
        "schema_version 2 requires resource_binding fixture_readonly_v0 and per-screen resource_views.",
        "schema_version 3 requires fixture_readonly_v0 plus interactive_state_v0 state, mutations, and selectors.",
        "Routes are static canonical paths only and must map to declared screens.",
        "The root route must resolve to exactly one route.",
        "Every screen must be reachable by at least one static route.",
        "V2 binding proof is exact byte-for-byte fixture scalar visibility in declared target motifs only.",
        "V3 state mutations are declarative reducer operations triggered by declared embedded screen actions only.",
        "V3 selectors are deterministic read-only derived views over declared state.",
        "Every embedded screen intent must validate against the local V1 IntentBundle contract.",
        "Unknown AppBundle-owned fields are rejected instead of ignored.",
        "Proof output paths are derived from validated safe ids only.",
    ],
    "x-viewspec-anti-goals": [
        "No runtime browser navigation proof.",
        "No dynamic routes, route params, query strings, hashes, redirects, guards, nested routers, or locale routing.",
        "No live DOM rebinding, framework state adapter, optimistic server reconciliation, persistence, CRDT, websocket sync, or gesture runtime.",
        "No transformed, localized, formatted, joined, sorted, filtered, paginated, grouped, or aggregated fixture proof.",
        "No whole-app data-flow consistency proof beyond explicitly declared resource_views.",
        "No accessibility, pixel-perfect, cross-browser, production deployment, arbitrary host-app, or hosted extended compiler certification.",
    ],
    "$defs": {
        "app_bundle_v1": {
            "type": "object",
            "required": ["schema_version", "app", "routes", "resources", "screens"],
            "additionalProperties": False,
            "properties": {
                "schema_version": {"const": APP_BUNDLE_SCHEMA_VERSION},
                "app": {"$ref": "#/$defs/app"},
                "routes": {"$ref": "#/$defs/routes"},
                "resources": {"$ref": "#/$defs/resources"},
                "screens": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": APP_BUNDLE_MAX_SCREENS,
                    "items": {"$ref": "#/$defs/screen_v1"},
                },
            },
        },
        "app_bundle_v2": {
            "type": "object",
            "required": ["schema_version", "resource_binding", "app", "routes", "resources", "screens"],
            "additionalProperties": False,
            "properties": {
                "schema_version": {"const": APP_BUNDLE_BOUND_SCHEMA_VERSION},
                "resource_binding": {"const": APP_BUNDLE_RESOURCE_BINDING_READONLY},
                "app": {"$ref": "#/$defs/app"},
                "routes": {"$ref": "#/$defs/routes"},
                "resources": {"$ref": "#/$defs/resources"},
                "screens": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": APP_BUNDLE_MAX_SCREENS,
                    "items": {"$ref": "#/$defs/screen_v2"},
                },
            },
        },
        "app_bundle_v3": {
            "type": "object",
            "required": [
                "schema_version",
                "resource_binding",
                "interactive_state",
                "app",
                "routes",
                "resources",
                "screens",
                "state",
                "mutations",
                "selectors",
            ],
            "additionalProperties": False,
            "properties": {
                "schema_version": {"const": APP_BUNDLE_STATE_SCHEMA_VERSION},
                "resource_binding": {"const": APP_BUNDLE_RESOURCE_BINDING_READONLY},
                "interactive_state": {"const": INTERACTIVE_STATE_PROFILE},
                "app": {"$ref": "#/$defs/app"},
                "routes": {"$ref": "#/$defs/routes"},
                "resources": {"$ref": "#/$defs/resources"},
                "screens": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": APP_BUNDLE_MAX_SCREENS,
                    "items": {"$ref": "#/$defs/screen_v2"},
                },
                "state": {"$ref": "#/$defs/state_entries"},
                "mutations": {"$ref": "#/$defs/state_mutations"},
                "selectors": {"$ref": "#/$defs/state_selectors"},
                "state_replay_assertions": {"$ref": "#/$defs/state_replay_assertions"},
            },
        },
        "safe_id": {"type": "string", "pattern": SAFE_AGENT_ID_PATTERN, "maxLength": APP_BUNDLE_MAX_ID_CHARS},
        "safe_string": {"type": "string", "maxLength": APP_BUNDLE_MAX_SCALAR_STRING_CHARS},
        "json_value": {
            "anyOf": [
                {"type": "string", "maxLength": APP_BUNDLE_MAX_SCALAR_STRING_CHARS},
                {"type": "number"},
                {"type": "boolean"},
                {"type": "null"},
                {
                    "type": "array",
                    "maxItems": APP_BUNDLE_MAX_RECORDS_PER_RESOURCE,
                    "items": {"$ref": "#/$defs/json_value"},
                },
                {
                    "type": "object",
                    "maxProperties": APP_BUNDLE_MAX_RECORD_FIELDS,
                    "propertyNames": {"pattern": SAFE_AGENT_ID_PATTERN, "maxLength": APP_BUNDLE_MAX_ID_CHARS},
                    "additionalProperties": {"$ref": "#/$defs/json_value"},
                },
            ]
        },
        "payload_expr": {
            "anyOf": [
                {"$ref": "#/$defs/json_value"},
                {
                    "type": "object",
                    "required": ["from_payload"],
                    "additionalProperties": False,
                    "properties": {"from_payload": {"$ref": "#/$defs/safe_id"}},
                },
            ]
        },
        "app": {
            "type": "object",
            "required": ["id", "title", "kind", "root_route"],
            "additionalProperties": False,
            "properties": {
                "id": {"$ref": "#/$defs/safe_id"},
                "title": {"$ref": "#/$defs/safe_string"},
                "kind": {"enum": list(APP_BUNDLE_ALLOWED_KINDS)},
                "root_route": {"type": "string", "maxLength": APP_BUNDLE_MAX_ROUTE_CHARS, "pattern": "^/[A-Za-z0-9_.~\\-/]*$"},
            },
        },
        "routes": {
            "type": "array",
            "minItems": 1,
            "maxItems": APP_BUNDLE_MAX_ROUTES,
            "items": {"$ref": "#/$defs/route"},
        },
        "route": {
            "type": "object",
            "required": ["id", "path", "label", "screen_id"],
            "additionalProperties": False,
            "properties": {
                "id": {"$ref": "#/$defs/safe_id"},
                "path": {"type": "string", "maxLength": APP_BUNDLE_MAX_ROUTE_CHARS, "pattern": "^/[A-Za-z0-9_.~\\-/]*$"},
                "label": {"$ref": "#/$defs/safe_string"},
                "screen_id": {"$ref": "#/$defs/safe_id"},
            },
        },
        "resources": {
            "type": "array",
            "maxItems": APP_BUNDLE_MAX_RESOURCES,
            "items": {"$ref": "#/$defs/resource"},
        },
        "resource": {
            "type": "object",
            "required": ["id", "kind", "records"],
            "additionalProperties": False,
            "properties": {
                "id": {"$ref": "#/$defs/safe_id"},
                "kind": {"enum": list(APP_BUNDLE_ALLOWED_RESOURCE_KINDS)},
                "records": {
                    "type": "array",
                    "maxItems": APP_BUNDLE_MAX_RECORDS_PER_RESOURCE,
                    "items": {"$ref": "#/$defs/fixture_record"},
                },
            },
        },
        "fixture_record": {
            "type": "object",
            "maxProperties": APP_BUNDLE_MAX_RECORD_FIELDS,
            "propertyNames": {"pattern": SAFE_AGENT_ID_PATTERN, "maxLength": APP_BUNDLE_MAX_ID_CHARS},
            "additionalProperties": {
                "anyOf": [
                    {"type": "string", "maxLength": APP_BUNDLE_MAX_SCALAR_STRING_CHARS},
                    {"type": "number"},
                    {"type": "boolean"},
                    {"type": "null"},
                ]
            },
        },
        "screen_v1": {
            "type": "object",
            "required": ["id", "title", "intent_bundle"],
            "additionalProperties": False,
            "properties": {
                "id": {"$ref": "#/$defs/safe_id"},
                "title": {"$ref": "#/$defs/safe_string"},
                "intent_bundle": {"$ref": "#/$defs/intent_bundle"},
            },
        },
        "screen_v2": {
            "type": "object",
            "required": ["id", "title", "resource_views", "intent_bundle"],
            "additionalProperties": False,
            "properties": {
                "id": {"$ref": "#/$defs/safe_id"},
                "title": {"$ref": "#/$defs/safe_string"},
                "resource_views": {
                    "type": "array",
                    "maxItems": APP_RESOURCE_BINDING_MAX_VIEWS_PER_SCREEN,
                    "items": {"$ref": "#/$defs/resource_view"},
                },
                "intent_bundle": {"$ref": "#/$defs/intent_bundle"},
            },
        },
        "resource_view": {
            "type": "object",
            "required": ["id", "resource_id", "mode", "record_ids", "fields", "target_motif_id"],
            "additionalProperties": False,
            "properties": {
                "id": {"$ref": "#/$defs/safe_id"},
                "resource_id": {"$ref": "#/$defs/safe_id"},
                "mode": {"const": "list"},
                "record_ids": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": APP_RESOURCE_BINDING_MAX_RECORD_REFS_PER_VIEW,
                    "items": {"$ref": "#/$defs/safe_id"},
                },
                "fields": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": APP_RESOURCE_BINDING_MAX_FIELDS_PER_VIEW,
                    "items": {"$ref": "#/$defs/safe_id"},
                },
                "target_motif_id": {"$ref": "#/$defs/safe_id"},
            },
        },
        "state_entries": {
            "type": "array",
            "maxItems": APP_STATE_MAX_ENTRIES,
            "items": {"$ref": "#/$defs/state_entry"},
        },
        "state_entry": {
            "type": "object",
            "required": ["id", "kind", "scope", "initial"],
            "additionalProperties": False,
            "properties": {
                "id": {"$ref": "#/$defs/safe_id"},
                "kind": {"enum": ["collection", "record", "scalar", "selection"]},
                "scope": {"enum": ["app", "screen"]},
                "screen_id": {"$ref": "#/$defs/safe_id"},
                "initial": {"$ref": "#/$defs/state_initial"},
            },
        },
        "state_initial": {
            "oneOf": [
                {
                    "type": "object",
                    "required": ["value"],
                    "additionalProperties": False,
                    "properties": {"value": {"$ref": "#/$defs/json_value"}},
                },
                {
                    "type": "object",
                    "required": ["from_resource_view"],
                    "additionalProperties": False,
                    "properties": {"from_resource_view": {"$ref": "#/$defs/resource_view_ref"}},
                },
            ]
        },
        "resource_view_ref": {
            "type": "object",
            "required": ["screen_id", "view_id"],
            "additionalProperties": False,
            "properties": {
                "screen_id": {"$ref": "#/$defs/safe_id"},
                "view_id": {"$ref": "#/$defs/safe_id"},
            },
        },
        "state_mutations": {
            "type": "array",
            "maxItems": APP_STATE_MAX_MUTATIONS,
            "items": {"$ref": "#/$defs/state_mutation"},
        },
        "state_mutation": {
            "type": "object",
            "required": ["id", "trigger", "ops"],
            "additionalProperties": False,
            "properties": {
                "id": {"$ref": "#/$defs/safe_id"},
                "trigger": {"$ref": "#/$defs/state_mutation_trigger"},
                "ops": {
                    "type": "array",
                    "maxItems": APP_STATE_MAX_OPS_PER_MUTATION,
                    "items": {"$ref": "#/$defs/state_mutation_op"},
                },
            },
        },
        "state_mutation_trigger": {
            "type": "object",
            "required": ["screen_id", "action_id"],
            "additionalProperties": False,
            "properties": {
                "screen_id": {"$ref": "#/$defs/safe_id"},
                "action_id": {"$ref": "#/$defs/safe_id"},
            },
        },
        "state_mutation_op": {
            "oneOf": [
                {"$ref": "#/$defs/state_op_set"},
                {"$ref": "#/$defs/state_op_patch"},
                {"$ref": "#/$defs/state_op_toggle"},
                {"$ref": "#/$defs/state_op_append"},
                {"$ref": "#/$defs/state_op_remove"},
                {"$ref": "#/$defs/state_op_move"},
                {"$ref": "#/$defs/state_op_increment"},
            ]
        },
        "state_op_set": {
            "type": "object",
            "required": ["op", "state", "value"],
            "additionalProperties": False,
            "properties": {
                "op": {"const": "set"},
                "state": {"$ref": "#/$defs/safe_id"},
                "value": {"$ref": "#/$defs/payload_expr"},
            },
        },
        "state_op_patch": {
            "type": "object",
            "required": ["op", "state", "value"],
            "additionalProperties": False,
            "properties": {
                "op": {"const": "patch"},
                "state": {"$ref": "#/$defs/safe_id"},
                "item_id": {"$ref": "#/$defs/payload_expr"},
                "value": {"$ref": "#/$defs/payload_expr"},
            },
        },
        "state_op_toggle": {
            "type": "object",
            "required": ["op", "state"],
            "additionalProperties": False,
            "properties": {
                "op": {"const": "toggle"},
                "state": {"$ref": "#/$defs/safe_id"},
                "item_id": {"$ref": "#/$defs/payload_expr"},
                "field": {"$ref": "#/$defs/safe_id"},
            },
        },
        "state_op_append": {
            "type": "object",
            "required": ["op", "state", "value"],
            "additionalProperties": False,
            "properties": {
                "op": {"const": "append"},
                "state": {"$ref": "#/$defs/safe_id"},
                "value": {"$ref": "#/$defs/payload_expr"},
            },
        },
        "state_op_remove": {
            "type": "object",
            "required": ["op", "state", "item_id"],
            "additionalProperties": False,
            "properties": {
                "op": {"const": "remove"},
                "state": {"$ref": "#/$defs/safe_id"},
                "item_id": {"$ref": "#/$defs/payload_expr"},
            },
        },
        "state_op_move": {
            "type": "object",
            "required": ["op", "state", "item_id", "to_index"],
            "additionalProperties": False,
            "properties": {
                "op": {"const": "move"},
                "state": {"$ref": "#/$defs/safe_id"},
                "item_id": {"$ref": "#/$defs/payload_expr"},
                "to_index": {"$ref": "#/$defs/payload_expr"},
            },
        },
        "state_op_increment": {
            "type": "object",
            "required": ["op", "state"],
            "additionalProperties": False,
            "properties": {
                "op": {"const": "increment"},
                "state": {"$ref": "#/$defs/safe_id"},
                "item_id": {"$ref": "#/$defs/payload_expr"},
                "field": {"$ref": "#/$defs/safe_id"},
                "amount": {"$ref": "#/$defs/payload_expr"},
            },
        },
        "state_selectors": {
            "type": "array",
            "maxItems": APP_STATE_MAX_SELECTORS,
            "items": {"$ref": "#/$defs/state_selector"},
        },
        "state_selector": {
            "type": "object",
            "required": ["id", "source_state", "ops"],
            "additionalProperties": False,
            "properties": {
                "id": {"$ref": "#/$defs/safe_id"},
                "source_state": {"$ref": "#/$defs/safe_id"},
                "ops": {
                    "type": "array",
                    "maxItems": APP_STATE_MAX_SELECTOR_OPS,
                    "items": {"$ref": "#/$defs/state_selector_op"},
                },
            },
        },
        "state_selector_op": {
            "oneOf": [
                {"$ref": "#/$defs/selector_op_filter_eq"},
                {"$ref": "#/$defs/selector_op_sort_by"},
                {"$ref": "#/$defs/selector_op_slice"},
            ]
        },
        "selector_op_filter_eq": {
            "type": "object",
            "required": ["op", "field", "value"],
            "additionalProperties": False,
            "properties": {
                "op": {"const": "filter_eq"},
                "field": {"$ref": "#/$defs/safe_id"},
                "value": {"$ref": "#/$defs/json_value"},
            },
        },
        "selector_op_sort_by": {
            "type": "object",
            "required": ["op", "field"],
            "additionalProperties": False,
            "properties": {
                "op": {"const": "sort_by"},
                "field": {"$ref": "#/$defs/safe_id"},
                "direction": {"enum": ["asc", "desc"]},
            },
        },
        "selector_op_slice": {
            "type": "object",
            "required": ["op"],
            "additionalProperties": False,
            "properties": {
                "op": {"const": "slice"},
                "start": {"type": "integer", "minimum": 0},
                "end": {"type": "integer", "minimum": 0},
            },
        },
        "state_replay_assertions": {
            "type": "array",
            "maxItems": APP_STATE_MAX_REPLAY_ASSERTIONS,
            "items": {"$ref": "#/$defs/state_replay_assertion"},
        },
        "state_replay_assertion": {
            "type": "object",
            "required": ["id", "events", "expect_state", "expect_selectors"],
            "additionalProperties": False,
            "properties": {
                "id": {"$ref": "#/$defs/safe_id"},
                "events": {
                    "type": "array",
                    "maxItems": APP_STATE_MAX_EVENTS_PER_REPLAY,
                    "items": {"$ref": "#/$defs/state_replay_event"},
                },
                "expect_state": {
                    "type": "object",
                    "propertyNames": {"$ref": "#/$defs/safe_id"},
                    "additionalProperties": {"$ref": "#/$defs/json_value"},
                },
                "expect_selectors": {
                    "type": "object",
                    "propertyNames": {"$ref": "#/$defs/safe_id"},
                    "additionalProperties": {"$ref": "#/$defs/json_value"},
                },
            },
        },
        "state_replay_event": {
            "type": "object",
            "required": ["mutation_id"],
            "additionalProperties": False,
            "properties": {
                "mutation_id": {"$ref": "#/$defs/safe_id"},
                "payload_values": {
                    "type": "object",
                    "propertyNames": {"$ref": "#/$defs/safe_id"},
                    "additionalProperties": {"$ref": "#/$defs/json_value"},
                },
            },
        },
        "intent_bundle": {
            "type": "object",
            "description": "Embedded local V1 IntentBundle. validate-app/prove-app enforce the full local V1 validator.",
        },
    },
}


__all__ = [
    "AGENT_APP_BUNDLE_SCHEMA",
    "APP_BUNDLE_BINDING_SCOPE",
    "APP_BUNDLE_BOUND_SCHEMA_VERSION",
    "APP_BUNDLE_DEFAULT_OUT",
    "APP_BUNDLE_DIFF_BASIS",
    "APP_BUNDLE_DIFF_VERSION",
    "APP_BUNDLE_MAX_BYTES",
    "APP_BUNDLE_MAX_EMBEDDED_INTENT_BYTES",
    "APP_BUNDLE_RESOURCE_BINDING",
    "APP_BUNDLE_RESOURCE_BINDING_READONLY",
    "APP_BUNDLE_RESULT_SCHEMA_VERSION",
    "APP_BUNDLE_SCHEMA_VERSION",
    "APP_BUNDLE_STATE_SCHEMA_VERSION",
    "APP_BUNDLE_TARGET",
    "APP_BUNDLE_PROOF_LEVEL",
    "APP_STATE_MANIFEST",
    "APP_STATE_REDUCER",
    "APP_SHELL_DEFAULT_OUT",
    "APP_SHELL_ROUTE_NAVIGATION",
    "APP_SHELL_TARGET",
    "app_semantic_change_lines",
    "compile_app",
    "compile_app_tool",
    "diff_app_files",
    "diff_app_files_tool",
    "diff_app_text",
    "init_app_file",
    "init_app_tool",
    "prove_app",
    "prove_app_tool",
    "starter_app_bundle",
    "validate_app_file",
    "validate_app_file_tool",
    "validate_app_text",
]
