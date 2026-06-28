"""Local-tool wrappers for AppBundle operations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from viewspec._version import __version__
from viewspec.app_diff import app_semantic_change_lines, diff_app_files
from viewspec.app_errors import _normalize_proof_errors
from viewspec.app_pipeline import compile_app, init_app_file, prove_app
from viewspec.app_reports import APP_BUNDLE_DEFAULT_OUT, _app_tool_proof_identity
from viewspec.app_shell import APP_SHELL_DEFAULT_OUT, APP_SHELL_TARGET
from viewspec.app_state_artifacts import _state_conformance_status
from viewspec.app_validation import APP_BUNDLE_RESOURCE_BINDING, validate_app_file
from viewspec.local_tools import (
    MCP_RESULT_SCHEMA_VERSION,
    LocalToolError,
    exception_response,
    path_policy_metadata,
    resolve_cwd,
    resolve_local_path,
    tool_error_response,
    tool_response,
)

def init_app_tool(
    out: str | Path = "viewspec.app.json",
    *,
    kind: str = "internal_tool",
    resource_binding: str = APP_BUNDLE_RESOURCE_BINDING,
    force: bool = False,
    cwd: str | Path | None = None,
    allow_outside_cwd: bool = False,
    _init_app_file: Any = init_app_file,
) -> dict[str, Any]:
    root: Path | None = None
    try:
        root = resolve_cwd(cwd)
        output = resolve_local_path(out, cwd=root, allow_outside_cwd=allow_outside_cwd)
        normalized_binding = resource_binding.replace("-", "_")
        _init_app_file(output, kind=kind, resource_binding=normalized_binding, force=force)
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
    _compile_app: Any = compile_app,
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
        result = _compile_app(
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
    _prove_app: Any = prove_app,
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
        proof = _prove_app(
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



__all__ = [
    "compile_app_tool",
    "diff_app_files_tool",
    "init_app_tool",
    "prove_app_tool",
    "validate_app_file_tool",
]
