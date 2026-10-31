"""Local AppBundle contract and proof helpers."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import viewspec.app_pipeline as _app_pipeline
from viewspec.app_agent_schema import AGENT_APP_BUNDLE_SCHEMA
from viewspec.app_react import REACT_APP_ROUTE_NAVIGATION, REACT_APP_TARGET
from viewspec.app_reports import (
    APP_BUNDLE_DEFAULT_OUT,
    APP_BUNDLE_PROOF_LEVEL,
    APP_BUNDLE_TARGET,
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
    freerange: bool = False,
    pretext: bool = False,
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
        freerange=freerange,
        pretext=pretext,
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


class AppBundleError(ValueError):
    """An AppBundle document failed validation; ``issues`` holds the validator's findings."""

    def __init__(self, issues: list[dict[str, Any]]) -> None:
        first = issues[0] if issues else {"code": "APP_BUNDLE_INVALID", "message": "AppBundle is invalid."}
        super().__init__(f"{first['code']}: {first['message']}")
        self.issues = issues


@dataclass(frozen=True)
class AppBundle:
    """A validated AppBundle: the JSON document a ``viewspec.app.json`` file holds."""

    # Canonical JSON text, so a bundle is immutable, hashable, and equal to the same document.
    document: str

    @classmethod
    def from_json(cls, payload: Mapping[str, Any] | str) -> AppBundle:
        text = payload if isinstance(payload, str) else json.dumps(payload)
        report = validate_app_text(text)
        if not report["ok"]:
            raise AppBundleError(report["issues"])
        return cls(json.dumps(json.loads(text), sort_keys=True))

    @classmethod
    def load(cls, path: str | Path) -> AppBundle:
        return cls.from_json(Path(path).read_text(encoding="utf-8"))

    def to_json(self) -> dict[str, Any]:
        return json.loads(self.document)


__all__ = [
    "AGENT_APP_BUNDLE_SCHEMA",
    "AppBundle",
    "AppBundleError",
    "APP_BUNDLE_ALLOWED_KINDS",
    "APP_BUNDLE_BINDING_SCOPE",
    "APP_BUNDLE_BOUND_SCHEMA_VERSION",
    "APP_BUNDLE_DEFAULT_OUT",
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
    "compile_app",
    "init_app_file",
    "prove_app",
    "starter_app_bundle",
    "starter_react_app_bundle",
    "validate_app_file",
    "validate_app_text",
]
