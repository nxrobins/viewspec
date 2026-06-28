"""Local AppBundle contract, diff, and proof helpers."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from viewspec._version import __version__
from viewspec.app_errors import AppBundleProofFailure, _normalize_proof_errors
from viewspec.app_paths import (
    _assert_report_under_output,
    _prepare_app_output_dir,
    _prepare_app_shell_output_dir,
    _should_write_app_proof_failure,
)
from viewspec.app_prepared import _PreparedAppProof, _PreparedAppShell
from viewspec.app_reports import (
    APP_BUNDLE_DEFAULT_OUT,
    APP_BUNDLE_DEFAULT_REPORT,
    APP_BUNDLE_DEFAULT_SUMMARY,
    APP_BUNDLE_DEFAULT_SUPPORT_BUNDLE,
    APP_BUNDLE_PROOF_LEVEL,
    APP_BUNDLE_TARGET,
    _app_proof_failure_report,
    _app_proof_report,
    _app_shell_failure_report,
    _app_shell_report,
    _app_tool_proof_identity,
    _write_app_proof,
)
from viewspec.app_validation import (
    APP_BUNDLE_ALLOWED_KINDS,
    APP_BUNDLE_ALLOWED_RESOURCE_KINDS,
    APP_BUNDLE_BINDING_SCOPE,
    APP_BUNDLE_BOUND_SCHEMA_VERSION,
    APP_BUNDLE_MAX_BYTES,
    APP_BUNDLE_MAX_EMBEDDED_INTENT_BYTES,
    APP_BUNDLE_MAX_ID_CHARS,
    APP_BUNDLE_MAX_RECORD_FIELDS,
    APP_BUNDLE_MAX_RECORDS_PER_RESOURCE,
    APP_BUNDLE_MAX_RESOURCES,
    APP_BUNDLE_MAX_ROUTE_CHARS,
    APP_BUNDLE_MAX_ROUTES,
    APP_BUNDLE_MAX_SCALAR_STRING_CHARS,
    APP_BUNDLE_MAX_SCREENS,
    APP_BUNDLE_RESOURCE_BINDING,
    APP_BUNDLE_RESOURCE_BINDING_READONLY,
    APP_BUNDLE_RESULT_SCHEMA_VERSION,
    APP_BUNDLE_SCHEMA_VERSION,
    APP_BUNDLE_STATE_SCHEMA_VERSION,
    APP_BUNDLE_SUPPORTED_SCHEMA_VERSIONS,
    APP_RESOURCE_BINDING_MAX_FIELDS_PER_VIEW,
    APP_RESOURCE_BINDING_MAX_RECORD_REFS_PER_VIEW,
    APP_RESOURCE_BINDING_MAX_VIEWS_PER_SCREEN,
    _reject_json_constant,
    _route_assertions,
    validate_app_file,
    validate_app_text,
)
from viewspec.app_resource_binding import _resource_binding_assertion_report
from viewspec.app_diff import (
    APP_BUNDLE_DIFF_BASIS,
    APP_BUNDLE_DIFF_VERSION,
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
    APP_SHELL_ROUTE_NAVIGATION,
    APP_SHELL_TARGET,
)
from viewspec.app_shell_writer import _write_static_app_shell
from viewspec.app_screens import _prove_app_screens
from viewspec.app_starters import starter_app_bundle
from viewspec.app_state_artifacts import (
    APP_STATE_MANIFEST,
    APP_STATE_REDUCER,
    _state_conformance_status,
)
from viewspec.agent import SAFE_AGENT_ID_PATTERN
from viewspec.local_tools import (
    MCP_RESULT_SCHEMA_VERSION,
    LocalToolError,
    atomic_write,
    exception_response,
    path_policy_metadata,
    resolve_cwd,
    resolve_local_path,
    tool_error_response,
    tool_response,
)
from viewspec.state_ir import (
    APP_STATE_MAX_ENTRIES,
    APP_STATE_MAX_EVENTS_PER_REPLAY,
    APP_STATE_MAX_MUTATIONS,
    APP_STATE_MAX_OPS_PER_MUTATION,
    APP_STATE_MAX_REPLAY_ASSERTIONS,
    APP_STATE_MAX_SELECTOR_OPS,
    APP_STATE_MAX_SELECTORS,
    INTERACTIVE_STATE_PROFILE,
    check_reducer_conformance,
    generate_typescript_reducer,
    state_manifest,
)


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
            lambda: _prove_app_screens(
                payload,
                prepared.output_dir,
                design_path=prepared.design_path,
                root=root,
                strict_design=strict_design,
                target=APP_BUNDLE_TARGET,
            ),
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
                    generate_reducer=generate_typescript_reducer,
                    check_conformance=check_reducer_conformance,
                    build_manifest=state_manifest,
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
            lambda: _prove_app_screens(
                payload,
                prepared_proof.output_dir,
                design_path=prepared_proof.design_path,
                root=root,
                strict_design=strict_design,
                target=APP_BUNDLE_TARGET,
            ),
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
                generate_reducer=generate_typescript_reducer,
                check_conformance=check_reducer_conformance,
                build_manifest=state_manifest,
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


def _time_phase(timings: dict[str, int], phase: str, fn: Any) -> Any:
    started = time.perf_counter()
    try:
        return fn()
    finally:
        timings[phase] = timings.get(phase, 0) + int((time.perf_counter() - started) * 1000)


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
