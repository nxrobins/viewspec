"""Static Shell V0 artifact writer."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from viewspec._version import __version__
from viewspec.app_diff import _validation_summary
from viewspec.app_paths import _assert_under_proof_root, _prepare_app_shell_output_dir
from viewspec.app_prepared import _PreparedAppShell
from viewspec.app_reports import _write_bounded_json
from viewspec.app_shell import (
    APP_SHELL_MAX_MANIFEST_BYTES,
    APP_SHELL_ROUTE_NAVIGATION,
    APP_SHELL_TARGET,
    _app_shell_limits,
    _build_static_app_shell,
    _screen_shell_summaries,
)
from viewspec.app_state_artifacts import _write_state_artifacts
from viewspec.app_validation import _app_schema_version, _app_summary, _resource_binding_report_fields
from viewspec.local_tools import atomic_write, file_hash


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
    generate_reducer: Callable[[dict[str, Any]], str],
    check_conformance: Callable[..., dict[str, Any]],
    build_manifest: Callable[..., dict[str, Any]],
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
    state_artifacts = _write_state_artifacts(
        payload,
        prepared.output_dir,
        generate_reducer=generate_reducer,
        check_conformance=check_conformance,
        build_manifest=build_manifest,
    )
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


__all__ = ["_write_static_app_shell"]
