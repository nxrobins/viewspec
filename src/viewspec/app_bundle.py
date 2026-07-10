"""Local AppBundle contract, diff, and proof helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import viewspec.app_pipeline as _app_pipeline
import viewspec.app_tools as _app_tools
from viewspec.app_agent_schema import AGENT_APP_BUNDLE_SCHEMA
from viewspec.app_reports import (
    APP_BUNDLE_DEFAULT_OUT,
    APP_BUNDLE_PROOF_LEVEL,
    APP_BUNDLE_TARGET,
)
from viewspec.app_react import REACT_APP_ROUTE_NAVIGATION, REACT_APP_TARGET
from viewspec.app_validation import (
    APP_BUNDLE_ALLOWED_KINDS,
    APP_BUNDLE_BINDING_SCOPE,
    APP_BUNDLE_BOUND_SCHEMA_VERSION,
    APP_BUNDLE_MAX_BYTES,
    APP_BUNDLE_MAX_EMBEDDED_INTENT_BYTES,
    APP_BUNDLE_RESOURCE_BINDING,
    APP_BUNDLE_RESOURCE_BINDING_READONLY,
    APP_BUNDLE_RESULT_SCHEMA_VERSION,
    APP_BUNDLE_SCHEMA_VERSION,
    APP_BUNDLE_STATE_SCHEMA_VERSION,
    APP_BUNDLE_VISIBILITY_SCHEMA_VERSION,
    validate_app_file,
    validate_app_text,
)
from viewspec.app_diff import (
    APP_BUNDLE_DIFF_BASIS,
    APP_BUNDLE_DIFF_VERSION,
    app_semantic_change_lines,
    diff_app_files,
    diff_app_text,
)
from viewspec.app_shell import (
    APP_SHELL_DEFAULT_OUT,
    APP_SHELL_ROUTE_NAVIGATION,
    APP_SHELL_TARGET,
)
from viewspec.app_starters import starter_app_bundle, starter_react_app_bundle
from viewspec.app_state_artifacts import (
    APP_STATE_MANIFEST,
    APP_STATE_REDUCER,
)
from viewspec.state_ir import (
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
    template: str = "contract",
) -> Path:
    return _app_pipeline.init_app_file(
        path,
        kind=kind,
        force=force,
        resource_binding=resource_binding,
        template=template,
    )


def prove_app(
    *,
    app_path: str | Path,
    out_dir: str | Path = APP_BUNDLE_DEFAULT_OUT,
    design_path: str | Path | None = None,
    strict_design: bool = False,
    force: bool = False,
    report_out: str | Path | None = None,
    with_shell: bool = False,
    target: str = APP_BUNDLE_TARGET,
    install: bool = False,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    return _app_pipeline.prove_app(
        app_path=app_path,
        out_dir=out_dir,
        design_path=design_path,
        strict_design=strict_design,
        force=force,
        report_out=report_out,
        with_shell=with_shell,
        target=target,
        install=install,
        cwd=cwd,
        _generate_reducer=generate_typescript_reducer,
        _check_conformance=check_reducer_conformance,
        _build_manifest=state_manifest,
    )


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
    return _app_pipeline.compile_app(
        app_path,
        out_dir=out_dir,
        design_path=design_path,
        strict_design=strict_design,
        force=force,
        target=target,
        cwd=cwd,
        _generate_reducer=generate_typescript_reducer,
        _check_conformance=check_reducer_conformance,
        _build_manifest=state_manifest,
    )


def init_app_tool(
    out: str | Path = "viewspec.app.json",
    *,
    kind: str = "internal_tool",
    resource_binding: str = APP_BUNDLE_RESOURCE_BINDING,
    template: str = "contract",
    force: bool = False,
    cwd: str | Path | None = None,
    allow_outside_cwd: bool = False,
) -> dict[str, Any]:
    return _app_tools.init_app_tool(
        out,
        kind=kind,
        resource_binding=resource_binding,
        template=template,
        force=force,
        cwd=cwd,
        allow_outside_cwd=allow_outside_cwd,
        _init_app_file=init_app_file,
    )


def validate_app_file_tool(
    path: str | Path,
    *,
    compile_check: bool = True,
    cwd: str | Path | None = None,
    allow_outside_cwd: bool = False,
) -> dict[str, Any]:
    return _app_tools.validate_app_file_tool(
        path,
        compile_check=compile_check,
        cwd=cwd,
        allow_outside_cwd=allow_outside_cwd,
    )


def diff_app_files_tool(
    left_path: str | Path,
    right_path: str | Path,
    *,
    compile_check: bool = True,
    cwd: str | Path | None = None,
    allow_outside_cwd: bool = False,
) -> dict[str, Any]:
    return _app_tools.diff_app_files_tool(
        left_path,
        right_path,
        compile_check=compile_check,
        cwd=cwd,
        allow_outside_cwd=allow_outside_cwd,
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
    return _app_tools.compile_app_tool(
        app_path,
        out_dir,
        design_path=design_path,
        strict_design=strict_design,
        force=force,
        target=target,
        cwd=cwd,
        allow_outside_cwd=allow_outside_cwd,
        _compile_app=compile_app,
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
    target: str = APP_BUNDLE_TARGET,
    install: bool = False,
    cwd: str | Path | None = None,
    allow_outside_cwd: bool = False,
) -> dict[str, Any]:
    return _app_tools.prove_app_tool(
        app_path=app_path,
        out_dir=out_dir,
        design_path=design_path,
        strict_design=strict_design,
        force=force,
        report_out=report_out,
        with_shell=with_shell,
        target=target,
        install=install,
        cwd=cwd,
        allow_outside_cwd=allow_outside_cwd,
        _prove_app=prove_app,
    )



__all__ = [
    "AGENT_APP_BUNDLE_SCHEMA",
    "APP_BUNDLE_ALLOWED_KINDS",
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
    "APP_BUNDLE_VISIBILITY_SCHEMA_VERSION",
    "APP_BUNDLE_TARGET",
    "APP_BUNDLE_PROOF_LEVEL",
    "APP_STATE_MANIFEST",
    "APP_STATE_REDUCER",
    "APP_SHELL_DEFAULT_OUT",
    "APP_SHELL_ROUTE_NAVIGATION",
    "APP_SHELL_TARGET",
    "REACT_APP_ROUTE_NAVIGATION",
    "REACT_APP_TARGET",
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
    "starter_react_app_bundle",
    "validate_app_file",
    "validate_app_file_tool",
    "validate_app_text",
]
