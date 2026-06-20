"""Local AppBundle contract, diff, and proof helpers."""

from __future__ import annotations

import json
import platform
import re
import shutil
import sys
import time
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any

from viewspec._version import __version__
from viewspec.agent import SAFE_AGENT_ID_PATTERN
from viewspec.intent_tools import (
    diff_intent_text,
    intent_semantic_change_lines,
    validate_intent_text,
)
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


APP_BUNDLE_SCHEMA_VERSION = 1
APP_BUNDLE_BOUND_SCHEMA_VERSION = 2
APP_BUNDLE_SUPPORTED_SCHEMA_VERSIONS = (APP_BUNDLE_SCHEMA_VERSION, APP_BUNDLE_BOUND_SCHEMA_VERSION)
APP_BUNDLE_RESULT_SCHEMA_VERSION = 1
APP_BUNDLE_DIFF_VERSION = 1
APP_BUNDLE_DIFF_BASIS = "app_bundle_v0_v2"
APP_BUNDLE_PROOF_SCHEMA_VERSION = 1
APP_BUNDLE_DEFAULT_OUT = ".viewspec-app-proof"
APP_BUNDLE_DEFAULT_REPORT = "app_proof_report.json"
APP_BUNDLE_DEFAULT_SUMMARY = "APP_PROOF.md"
APP_BUNDLE_DEFAULT_SUPPORT_BUNDLE = "app_support_bundle.json"
APP_BUNDLE_PROOF_LEVEL = "app_contract_source_artifacts"
APP_BUNDLE_RESOURCE_BINDING = "unbound_v0"
APP_BUNDLE_RESOURCE_BINDING_READONLY = "fixture_readonly_v0"
APP_BUNDLE_BINDING_SCOPE = "declared_resource_views_only"
APP_BUNDLE_TARGET = "html-tailwind"
APP_SHELL_TARGET = "html-tailwind-app"
APP_SHELL_ROUTE_NAVIGATION = "static_shell_v0"
APP_SHELL_DEFAULT_OUT = "app-dist"
APP_SHELL_DIR_NAME = "app-shell"
APP_SHELL_MANIFEST = "shell_manifest.json"
APP_SHELL_DIAGNOSTICS = "diagnostics.json"
APP_SHELL_INDEX = "index.html"
APP_BUNDLE_MAX_BYTES = 1024 * 1024
APP_BUNDLE_MAX_SCREENS = 16
APP_BUNDLE_MAX_ROUTES = 32
APP_BUNDLE_MAX_RESOURCES = 8
APP_BUNDLE_MAX_RECORDS_PER_RESOURCE = 100
APP_BUNDLE_MAX_RECORD_FIELDS = 32
APP_BUNDLE_MAX_SCALAR_STRING_CHARS = 2048
APP_BUNDLE_MAX_EMBEDDED_INTENT_BYTES = 256 * 1024
APP_BUNDLE_MAX_AGGREGATE_INTENT_BYTES = 1024 * 1024
APP_BUNDLE_MAX_PROOF_REPORT_BYTES = 256 * 1024
APP_BUNDLE_MAX_SUPPORT_BUNDLE_BYTES = 16 * 1024
APP_BUNDLE_MAX_SUMMARY_BYTES = 32 * 1024
APP_BUNDLE_MAX_ID_CHARS = 96
APP_BUNDLE_MAX_ROUTE_CHARS = 96
APP_SHELL_MAX_HTML_BYTES = 2 * 1024 * 1024
APP_SHELL_MAX_JS_BYTES = 64 * 1024
APP_SHELL_MAX_ROUTE_JSON_BYTES = 64 * 1024
APP_SHELL_MAX_AGGREGATE_SCREEN_HTML_BYTES = 8 * 1024 * 1024
APP_SHELL_MAX_MANIFEST_BYTES = 256 * 1024
APP_RESOURCE_BINDING_MAX_VIEWS = 32
APP_RESOURCE_BINDING_MAX_VIEWS_PER_SCREEN = 8
APP_RESOURCE_BINDING_MAX_RECORD_REFS_PER_VIEW = 50
APP_RESOURCE_BINDING_MAX_FIELDS_PER_VIEW = 16
APP_RESOURCE_BINDING_MAX_ASSERTIONS = 800
APP_RESOURCE_BINDING_MAX_REPORT_BYTES = 128 * 1024
APP_BUNDLE_ALLOWED_KINDS = ("internal_tool",)
APP_BUNDLE_ALLOWED_RESOURCE_KINDS = ("fixture",)
APP_BUNDLE_ALLOWED_ROOT_FIELDS = {"schema_version", "app", "routes", "resources", "screens"}
APP_BUNDLE_ALLOWED_ROOT_FIELDS_V2 = APP_BUNDLE_ALLOWED_ROOT_FIELDS | {"resource_binding"}
APP_BUNDLE_ALLOWED_APP_FIELDS = {"id", "title", "kind", "root_route"}
APP_BUNDLE_ALLOWED_ROUTE_FIELDS = {"id", "path", "label", "screen_id"}
APP_BUNDLE_ALLOWED_RESOURCE_FIELDS = {"id", "kind", "records"}
APP_BUNDLE_ALLOWED_SCREEN_FIELDS = {"id", "title", "intent_bundle"}
APP_BUNDLE_ALLOWED_SCREEN_FIELDS_V2 = APP_BUNDLE_ALLOWED_SCREEN_FIELDS | {"resource_views"}
APP_BUNDLE_ALLOWED_RESOURCE_VIEW_FIELDS = {"id", "resource_id", "mode", "record_ids", "fields", "target_motif_id"}
APP_RESOURCE_BINDING_TEXT_PRIMITIVES = {"badge", "label", "text", "value"}

SAFE_APP_ID_RE = re.compile(SAFE_AGENT_ID_PATTERN)
SAFE_ROUTE_RE = re.compile(r"^/[A-Za-z0-9_.~\-/]*$")
RESOURCE_BINDING_ATTR_RE = re.compile(r"^node:(?P<node>[A-Za-z0-9_.-]+)#attr:(?P<field>[A-Za-z0-9_.-]+)$")
URL_SCHEME_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]*://")
ENV_REF_RE = re.compile(r"(\$\{[^}]+\}|\$env:|%[A-Za-z_][A-Za-z0-9_]*%|process\.env|os\.environ)", re.IGNORECASE)
PACKAGE_INSTALL_RE = re.compile(r"(\b(?:npm|pnpm|yarn|pip)\s+install\b|--install\b)", re.IGNORECASE)
HTML_BODY_RE = re.compile(r"<body\b[^>]*>(?P<body>[\s\S]*?)</body>", re.IGNORECASE)
HTML_STYLE_RE = re.compile(r"<style\b[^>]*>(?P<style>[\s\S]*?)</style>", re.IGNORECASE)
HTML_SCRIPT_RE = re.compile(r"<script\b[\s\S]*?</script>", re.IGNORECASE)
HTML_FORBIDDEN_EMBED_RE = re.compile(r"<\s*(?:iframe|object|embed)\b", re.IGNORECASE)
HTML_FORBIDDEN_LINK_RE = re.compile(r"<\s*link\b", re.IGNORECASE)
HTML_INLINE_HANDLER_RE = re.compile(r"\son[a-z]+\s*=", re.IGNORECASE)
HTML_WORKER_IMPORT_RE = re.compile(r"\b(?:Worker|SharedWorker|importScripts)\s*\(", re.IGNORECASE)
HTML_IMPORT_MAP_RE = re.compile(r"<script\b[^>]*type\s*=\s*['\"]importmap['\"]", re.IGNORECASE)
HTML_PROTOCOL_RELATIVE_RE = re.compile(r"(?i)(?:src|href|action|formaction|poster|srcset)\s*=\s*['\"]//")
CSS_FORBIDDEN_RE = re.compile(r"(?i)(@import|url\s*\(|expression\s*\(|javascript:|vbscript:|data:)")
FORBIDDEN_APP_FIELD_NAMES = {
    "adapter",
    "api_key",
    "auth",
    "authorization",
    "compiler",
    "credential",
    "credentials",
    "env",
    "environment",
    "fetch",
    "hosted",
    "install",
    "mutation",
    "mutations",
    "package",
    "packages",
    "password",
    "secret",
    "token",
}


class AppBundleProofFailure(ValueError):
    """Stable-code AppBundle proof failure."""

    def __init__(self, code: str, message: str, fix: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.fix = fix


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


def validate_app_text(text: str, *, compile_check: bool = True) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    raw_bytes = len(text.encode("utf-8"))
    if raw_bytes > APP_BUNDLE_MAX_BYTES:
        issues.append(
            _issue(
                "APP_BUNDLE_TOO_LARGE",
                "$",
                f"AppBundle is {raw_bytes} bytes; the V0 limit is {APP_BUNDLE_MAX_BYTES} bytes.",
                "Split the app into a smaller AppBundle before validating.",
            )
        )
        return _validation_payload(None, issues, compile_check=compile_check, raw_bytes=raw_bytes)
    try:
        payload = json.loads(text, parse_constant=_reject_json_constant)
    except ValueError as exc:
        issues.append(
            _issue(
                "APP_INVALID_JSON",
                "$",
                f"AppBundle must be strict JSON: {exc}",
                "Regenerate strict AppBundle JSON without comments, markdown fences, or non-finite numbers.",
            )
        )
        return _validation_payload(None, issues, compile_check=compile_check, raw_bytes=raw_bytes)
    if not isinstance(payload, dict):
        issues.append(_issue("APP_ROOT_NOT_OBJECT", "$", "AppBundle root must be a JSON object."))
        return _validation_payload(payload, issues, compile_check=compile_check, raw_bytes=raw_bytes)

    _validate_app_payload(payload, issues, compile_check=compile_check)
    return _validation_payload(payload, issues, compile_check=compile_check, raw_bytes=raw_bytes)


def validate_app_file(path: str | Path, *, compile_check: bool = True) -> dict[str, Any]:
    return validate_app_text(Path(path).read_text(encoding="utf-8"), compile_check=compile_check)


def diff_app_text(left_text: str, right_text: str, *, compile_check: bool = True) -> dict[str, Any]:
    left_basic = validate_app_text(left_text, compile_check=False)
    right_basic = validate_app_text(right_text, compile_check=False)
    if not left_basic["ok"] or not right_basic["ok"]:
        if _validation_has_screen_intent_issue(left_basic) or _validation_has_screen_intent_issue(right_basic):
            return _app_diff_error_payload(
                "APP_DIFF_SCREEN_INTENT_INVALID",
                "One or both AppBundles contain an embedded screen intent that cannot be validated for diff-app.",
                "Regenerate invalid embedded screen IntentBundles before running diff-app.",
                validation={"left": left_basic, "right": right_basic},
            )
        return _app_diff_error_payload(
            "APP_DIFF_APP_INVALID",
            "One or both AppBundles failed V0 validation before diff.",
            "Fix AppBundle validation issues before running diff-app.",
            validation={"left": left_basic, "right": right_basic},
        )

    left_payload = json.loads(left_text, parse_constant=_reject_json_constant)
    right_payload = json.loads(right_text, parse_constant=_reject_json_constant)
    changes = {
        "app": _diff_named_items({"app": left_payload["app"]}, {"app": right_payload["app"]}),
        "routes": _diff_named_items(_index_by_id(left_payload["routes"]), _index_by_id(right_payload["routes"])),
        "resources": _diff_named_items(
            _resource_sections(left_payload["resources"]),
            _resource_sections(right_payload["resources"]),
        ),
        "screens": _diff_named_items(
            _screen_sections(left_payload["screens"]),
            _screen_sections(right_payload["screens"]),
        ),
    }
    semantic_changes = _app_semantic_changes(left_payload, right_payload)
    screen_intent_diffs: dict[str, Any] = {}
    left_screens = _index_by_id(left_payload["screens"])
    right_screens = _index_by_id(right_payload["screens"])
    for screen_id in sorted(set(left_screens) & set(right_screens)):
        left_intent = left_screens[screen_id].get("intent_bundle")
        right_intent = right_screens[screen_id].get("intent_bundle")
        if _stable_json(left_intent) == _stable_json(right_intent):
            continue
        intent_diff = diff_intent_text(
            _stable_json(left_intent),
            _stable_json(right_intent),
            compile_check=compile_check,
        )
        if not intent_diff.get("ok"):
            return _app_diff_error_payload(
                "APP_DIFF_SCREEN_INTENT_INVALID",
                f"Changed embedded screen intent could not be validated or diffed: {screen_id}",
                "Regenerate the changed screen IntentBundle before running diff-app.",
                validation={"left": left_basic, "right": right_basic},
                errors=[
                    {
                        "code": "APP_DIFF_SCREEN_INTENT_INVALID",
                        "message": f"Screen {screen_id} changed intent failed diff-intent validation.",
                        "fix": "Regenerate the changed screen IntentBundle before running diff-app.",
                        "screen_id": screen_id,
                    },
                    *_normalize_diff_errors(intent_diff.get("errors"), screen_id=screen_id),
                ],
            )
        summary = intent_semantic_change_lines(intent_diff.get("semantic_changes"))
        screen_intent_diffs[screen_id] = {
            "ok": True,
            "topology_similarity": intent_diff.get("topology_similarity"),
            "semantic_summary": summary,
            "semantic_changes": intent_diff.get("semantic_changes"),
            "changes": intent_diff.get("changes"),
        }
        semantic_changes["screen_intents"].append(
            {
                "screen_id": screen_id,
                "change": "intent_changed",
                "semantic_summary": summary,
            }
        )

    changed_fields = _app_changed_fields(left_payload, right_payload)
    return {
        "schema_version": APP_BUNDLE_RESULT_SCHEMA_VERSION,
        "diff_version": APP_BUNDLE_DIFF_VERSION,
        "basis": APP_BUNDLE_DIFF_BASIS,
        "ok": True,
        "compile_check": "skipped" if not compile_check else "passed",
        "validation": {"left": _validation_summary(left_basic), "right": _validation_summary(right_basic)},
        "changes": changes,
        "changed_fields": changed_fields,
        "semantic_changes": semantic_changes,
        "semantic_summary": app_semantic_change_lines(semantic_changes),
        "screen_intent_diffs": screen_intent_diffs,
        "counts": _app_counts(left_payload, right_payload),
        "topology_similarity": _app_topology_similarity(changes),
        "errors": [],
    }


def diff_app_files(left_path: str | Path, right_path: str | Path, *, compile_check: bool = True) -> dict[str, Any]:
    left_text = Path(left_path).read_text(encoding="utf-8")
    right_text = Path(right_path).read_text(encoding="utf-8")
    return diff_app_text(left_text, right_text, compile_check=compile_check)


def app_semantic_change_lines(semantic_changes: object) -> list[str]:
    if not isinstance(semantic_changes, dict):
        return []
    lines: list[str] = []
    for item in semantic_changes.get("app_metadata", []):
        if isinstance(item, dict):
            lines.append(
                "app_metadata: "
                f"{_diff_value(item.get('field'))} "
                f"{_diff_value(item.get('left'))} -> {_diff_value(item.get('right'))}"
            )
    for section in ("routes", "resources", "screens"):
        entries = semantic_changes.get(section)
        if not isinstance(entries, list):
            continue
        for item in entries:
            if not isinstance(item, dict):
                continue
            item_id = _diff_value(item.get("id"))
            change = _diff_value(item.get("change"))
            if "field" in item:
                lines.append(
                    f"{section}.{item_id}: {change} "
                    f"{_diff_value(item.get('field'))} "
                    f"{_diff_value(item.get('left'))} -> {_diff_value(item.get('right'))}"
                )
            else:
                lines.append(f"{section}.{item_id}: {change}")
    for item in semantic_changes.get("screen_intents", []):
        if not isinstance(item, dict):
            continue
        screen_id = _diff_value(item.get("screen_id"))
        summary = item.get("semantic_summary")
        if isinstance(summary, list) and summary:
            for line in summary:
                lines.append(f"screen_intents.{screen_id}: {line}")
        else:
            lines.append(f"screen_intents.{screen_id}: intent_changed")
    return lines


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


def _validate_app_payload(payload: dict[str, Any], issues: list[dict[str, str]], *, compile_check: bool) -> None:
    schema_version = payload.get("schema_version")
    version: int | None = None
    if type(schema_version) is not int:
        issues.append(_issue("APP_SCHEMA_VERSION_REQUIRED", "$.schema_version", "AppBundle schema_version must be integer 1 or 2."))
    elif schema_version not in APP_BUNDLE_SUPPORTED_SCHEMA_VERSIONS:
        issues.append(
            _issue(
                "APP_SCHEMA_VERSION_UNSUPPORTED",
                "$.schema_version",
                f"Unsupported AppBundle schema_version {schema_version}.",
                "Use AppBundle schema_version 1 for unbound V0 or 2 for fixture_readonly_v0.",
            )
        )
    else:
        version = schema_version

    allowed_root_fields = APP_BUNDLE_ALLOWED_ROOT_FIELDS_V2 if version == APP_BUNDLE_BOUND_SCHEMA_VERSION else APP_BUNDLE_ALLOWED_ROOT_FIELDS | {"resource_binding"}
    _reject_unknown_fields(payload, "$", allowed_root_fields, issues)
    _reject_forbidden_object_keys(payload, "$", issues)
    resource_binding = payload.get("resource_binding")
    if version == APP_BUNDLE_SCHEMA_VERSION:
        if "resource_binding" in payload:
            issues.append(
                _issue(
                    "APP_SCHEMA_VERSION_RESOURCE_BINDING_MISMATCH",
                    "$.resource_binding",
                    "schema_version 1 must not declare resource_binding.",
                    "Remove resource_binding or upgrade to schema_version 2 with fixture_readonly_v0.",
                )
            )
    elif version == APP_BUNDLE_BOUND_SCHEMA_VERSION:
        if resource_binding != APP_BUNDLE_RESOURCE_BINDING_READONLY:
            issues.append(
                _issue(
                    "APP_SCHEMA_VERSION_RESOURCE_BINDING_MISMATCH",
                    "$.resource_binding",
                    "schema_version 2 requires resource_binding fixture_readonly_v0.",
                    "Set resource_binding to fixture_readonly_v0 or use schema_version 1.",
                )
            )

    app = _required_object(payload, "app", "$", issues)
    routes = _required_array(payload, "routes", "$", issues)
    resources = _required_array(payload, "resources", "$", issues)
    screens = _required_array(payload, "screens", "$", issues)
    if app is not None:
        _validate_app_object(app, issues)
    _check_list_count(routes, APP_BUNDLE_MAX_ROUTES, "$.routes", "APP_TOO_MANY_ROUTES", "routes", issues)
    _check_list_count(resources, APP_BUNDLE_MAX_RESOURCES, "$.resources", "APP_TOO_MANY_RESOURCES", "resources", issues)
    _check_list_count(screens, APP_BUNDLE_MAX_SCREENS, "$.screens", "APP_TOO_MANY_SCREENS", "screens", issues)

    route_ids = _validate_routes(routes, issues)
    resource_ids = _validate_resources(resources, issues)
    screen_ids, aggregate_intent_bytes = _validate_screens(screens, issues, compile_check=compile_check, schema_version=version)
    if aggregate_intent_bytes > APP_BUNDLE_MAX_AGGREGATE_INTENT_BYTES:
        issues.append(
            _issue(
                "APP_EMBEDDED_INTENTS_TOO_LARGE",
                "$.screens",
                f"Embedded IntentBundles total {aggregate_intent_bytes} bytes; the V0 aggregate limit is {APP_BUNDLE_MAX_AGGREGATE_INTENT_BYTES} bytes.",
                "Split the app into fewer or smaller screen IntentBundles.",
            )
        )
    _validate_unique_ids(route_ids, "$.routes", "APP_DUPLICATE_ROUTE_ID", issues)
    _validate_unique_ids(resource_ids, "$.resources", "APP_DUPLICATE_RESOURCE_ID", issues)
    _validate_unique_ids(screen_ids, "$.screens", "APP_DUPLICATE_SCREEN_ID", issues)
    _validate_route_graph(app, routes, set(screen_ids), issues)
    if version == APP_BUNDLE_BOUND_SCHEMA_VERSION:
        _validate_resource_binding_v0(resources, screens, issues)


def _validate_app_object(app: dict[str, Any], issues: list[dict[str, str]]) -> None:
    _reject_unknown_fields(app, "$.app", APP_BUNDLE_ALLOWED_APP_FIELDS, issues)
    _reject_forbidden_object_keys(app, "$.app", issues)
    app_id = _required_string(app, "id", "$.app", issues)
    _validate_safe_id(app_id, "$.app.id", "app id", issues)
    title = _required_string(app, "title", "$.app", issues)
    _validate_app_scalar_string(title, "$.app.title", issues)
    kind = _required_string(app, "kind", "$.app", issues)
    if kind and kind not in APP_BUNDLE_ALLOWED_KINDS:
        issues.append(
            _issue(
                "APP_KIND_UNSUPPORTED",
                "$.app.kind",
                f"AppBundle supports {', '.join(APP_BUNDLE_ALLOWED_KINDS)} only.",
                "Use app.kind internal_tool for this slice.",
            )
        )
    root_route = _required_string(app, "root_route", "$.app", issues)
    _validate_route_path(root_route, "$.app.root_route", issues)


def _validate_routes(routes: list[Any], issues: list[dict[str, str]]) -> list[str]:
    ids: list[str] = []
    paths: dict[str, str] = {}
    for index, route in enumerate(routes):
        path = f"$.routes[{index}]"
        if not isinstance(route, dict):
            issues.append(_issue("APP_ROUTE_NOT_OBJECT", path, "Each route must be an object."))
            continue
        _reject_unknown_fields(route, path, APP_BUNDLE_ALLOWED_ROUTE_FIELDS, issues)
        _reject_forbidden_object_keys(route, path, issues)
        route_id = _required_string(route, "id", path, issues)
        if route_id:
            ids.append(route_id)
        _validate_safe_id(route_id, f"{path}.id", "route id", issues)
        route_path = _required_string(route, "path", path, issues)
        _validate_route_path(route_path, f"{path}.path", issues)
        if isinstance(route_path, str):
            previous = paths.setdefault(route_path, path)
            if previous != path:
                issues.append(
                    _issue(
                        "APP_DUPLICATE_ROUTE_PATH",
                        f"{path}.path",
                        f"Duplicate route path {route_path}.",
                        "Use one canonical static path per route.",
                    )
                )
        label = _required_string(route, "label", path, issues)
        _validate_app_scalar_string(label, f"{path}.label", issues)
        screen_id = _required_string(route, "screen_id", path, issues)
        _validate_safe_id(screen_id, f"{path}.screen_id", "route screen id", issues)
    return ids


def _validate_resources(resources: list[Any], issues: list[dict[str, str]]) -> list[str]:
    ids: list[str] = []
    for index, resource in enumerate(resources):
        path = f"$.resources[{index}]"
        if not isinstance(resource, dict):
            issues.append(_issue("APP_RESOURCE_NOT_OBJECT", path, "Each resource must be an object."))
            continue
        _reject_unknown_fields(resource, path, APP_BUNDLE_ALLOWED_RESOURCE_FIELDS, issues)
        _reject_forbidden_object_keys(resource, path, issues)
        resource_id = _required_string(resource, "id", path, issues)
        if resource_id:
            ids.append(resource_id)
        _validate_safe_id(resource_id, f"{path}.id", "resource id", issues)
        kind = _required_string(resource, "kind", path, issues)
        if kind and kind not in APP_BUNDLE_ALLOWED_RESOURCE_KINDS:
            issues.append(
                _issue(
                    "APP_RESOURCE_KIND_UNSUPPORTED",
                    f"{path}.kind",
                    "AppBundle resources support fixture kind only.",
                    "Use kind fixture or remove the resource adapter from this V0 bundle.",
                )
            )
        records = _required_array(resource, "records", path, issues)
        _check_list_count(
            records,
            APP_BUNDLE_MAX_RECORDS_PER_RESOURCE,
            f"{path}.records",
            "APP_RESOURCE_TOO_MANY_RECORDS",
            "fixture records per resource",
            issues,
        )
        _validate_fixture_records(records, f"{path}.records", issues)
    return ids


def _validate_fixture_records(records: list[Any], path: str, issues: list[dict[str, str]]) -> None:
    for index, record in enumerate(records):
        record_path = f"{path}[{index}]"
        if not isinstance(record, dict):
            issues.append(_issue("APP_FIXTURE_RECORD_NOT_OBJECT", record_path, "Fixture records must be objects."))
            continue
        _reject_forbidden_object_keys(record, record_path, issues)
        if len(record) > APP_BUNDLE_MAX_RECORD_FIELDS:
            issues.append(
                _issue(
                    "APP_FIXTURE_TOO_MANY_FIELDS",
                    record_path,
                    f"Fixture record declares {len(record)} fields; the V0 limit is {APP_BUNDLE_MAX_RECORD_FIELDS}.",
                    "Trim fixture records to scalar fields needed for app context.",
                )
            )
        for key, value in record.items():
            key_path = f"{record_path}.{key}"
            _validate_safe_id(key if isinstance(key, str) else None, key_path, "fixture field", issues)
            _validate_fixture_scalar(value, key_path, issues)


def _validate_screens(
    screens: list[Any],
    issues: list[dict[str, str]],
    *,
    compile_check: bool,
    schema_version: int | None,
) -> tuple[list[str], int]:
    ids: list[str] = []
    aggregate_intent_bytes = 0
    for index, screen in enumerate(screens):
        path = f"$.screens[{index}]"
        if not isinstance(screen, dict):
            issues.append(_issue("APP_SCREEN_NOT_OBJECT", path, "Each screen must be an object."))
            continue
        allowed_fields = APP_BUNDLE_ALLOWED_SCREEN_FIELDS_V2 if schema_version == APP_BUNDLE_BOUND_SCHEMA_VERSION else APP_BUNDLE_ALLOWED_SCREEN_FIELDS | {"resource_views"}
        _reject_unknown_fields(screen, path, allowed_fields, issues)
        _reject_forbidden_object_keys({key: value for key, value in screen.items() if key != "intent_bundle"}, path, issues)
        if schema_version == APP_BUNDLE_SCHEMA_VERSION and "resource_views" in screen:
            issues.append(
                _issue(
                    "APP_SCHEMA_VERSION_RESOURCE_BINDING_MISMATCH",
                    f"{path}.resource_views",
                    "schema_version 1 screens must not declare resource_views.",
                    "Remove resource_views or upgrade to schema_version 2 with fixture_readonly_v0.",
                )
            )
        if schema_version == APP_BUNDLE_BOUND_SCHEMA_VERSION and "resource_views" not in screen:
            issues.append(
                _issue(
                    "APP_RESOURCE_BINDING_VIEWS_REQUIRED",
                    f"{path}.resource_views",
                    "schema_version 2 screens must declare resource_views, even when the list is empty.",
                    "Add resource_views to every screen.",
                )
            )
        screen_id = _required_string(screen, "id", path, issues)
        if screen_id:
            ids.append(screen_id)
        _validate_safe_id(screen_id, f"{path}.id", "screen id", issues)
        title = _required_string(screen, "title", path, issues)
        _validate_app_scalar_string(title, f"{path}.title", issues)
        intent = screen.get("intent_bundle")
        if not isinstance(intent, dict):
            issues.append(_issue("APP_SCREEN_INTENT_NOT_OBJECT", f"{path}.intent_bundle", "screen.intent_bundle must be an IntentBundle object."))
            continue
        try:
            intent_text = _stable_json(intent)
        except ValueError as exc:
            issues.append(
                _issue(
                    "APP_SCREEN_INTENT_INVALID_JSON",
                    f"{path}.intent_bundle",
                    f"Embedded screen intent is not strict JSON: {exc}",
                    "Regenerate this screen IntentBundle as strict JSON.",
                )
            )
            continue
        intent_bytes = len(intent_text.encode("utf-8"))
        aggregate_intent_bytes += intent_bytes
        if intent_bytes > APP_BUNDLE_MAX_EMBEDDED_INTENT_BYTES:
            issues.append(
                _issue(
                    "APP_SCREEN_INTENT_TOO_LARGE",
                    f"{path}.intent_bundle",
                    f"Embedded screen intent is {intent_bytes} bytes; the V0 per-screen limit is {APP_BUNDLE_MAX_EMBEDDED_INTENT_BYTES}.",
                    "Split this screen into a smaller IntentBundle.",
                )
            )
            continue
        validation = validate_intent_text(intent_text, compile_check=compile_check)
        if not validation["ok"]:
            first_issue = validation["issues"][0] if validation["issues"] else {}
            nested_code = first_issue.get("code", "INTENT_INVALID") if isinstance(first_issue, dict) else "INTENT_INVALID"
            nested_message = first_issue.get("message", "Embedded IntentBundle validation failed.") if isinstance(first_issue, dict) else "Embedded IntentBundle validation failed."
            issues.append(
                _issue(
                    "APP_SCREEN_INTENT_INVALID",
                    f"{path}.intent_bundle",
                    f"Screen {screen_id or index} IntentBundle failed local V1 validation: {nested_code}: {nested_message}",
                    "Regenerate the full embedded IntentBundle using the local V1 contract.",
                )
            )
    return ids, aggregate_intent_bytes


def _validate_route_graph(
    app: dict[str, Any] | None,
    routes: list[Any],
    screen_ids: set[str],
    issues: list[dict[str, str]],
) -> None:
    if app is None:
        return
    route_paths = {route.get("path") for route in routes if isinstance(route, dict)}
    root_route = app.get("root_route")
    if isinstance(root_route, str) and root_route not in route_paths:
        issues.append(
            _issue(
                "APP_ROOT_ROUTE_MISSING",
                "$.app.root_route",
                f"Root route {root_route} does not match any route.path.",
                "Add a route for app.root_route.",
            )
        )
    route_screen_ids = set()
    for index, route in enumerate(routes):
        if not isinstance(route, dict):
            continue
        screen_id = route.get("screen_id")
        if isinstance(screen_id, str):
            route_screen_ids.add(screen_id)
            if screen_id not in screen_ids:
                issues.append(
                    _issue(
                        "APP_ROUTE_SCREEN_MISSING",
                        f"$.routes[{index}].screen_id",
                        f"Route {route.get('id')} references missing screen {screen_id}.",
                        "Add the screen or update route.screen_id.",
                    )
                )
    for screen_id in sorted(screen_ids - route_screen_ids):
        issues.append(
            _issue(
                "APP_SCREEN_UNREACHABLE",
                "$.screens",
                f"Screen {screen_id} is not reachable from any route.",
                "Add a static route pointing to this screen or remove the screen.",
            )
        )


def _validate_resource_binding_v0(resources: list[Any], screens: list[Any], issues: list[dict[str, str]]) -> None:
    resource_records = _fixture_records_by_resource(resources, issues)
    total_views = 0
    total_assertions = 0
    for screen_index, screen in enumerate(screens):
        if not isinstance(screen, dict):
            continue
        screen_path = f"$.screens[{screen_index}]"
        resource_views = screen.get("resource_views")
        if not isinstance(resource_views, list):
            if "resource_views" in screen:
                issues.append(_issue("APP_RESOURCE_BINDING_VIEWS_NOT_ARRAY", f"{screen_path}.resource_views", "resource_views must be an array."))
            continue
        if len(resource_views) > APP_RESOURCE_BINDING_MAX_VIEWS_PER_SCREEN:
            issues.append(
                _issue(
                    "APP_RESOURCE_BINDING_LIMIT_EXCEEDED",
                    f"{screen_path}.resource_views",
                    f"Screen declares {len(resource_views)} resource views; limit is {APP_RESOURCE_BINDING_MAX_VIEWS_PER_SCREEN}.",
                    "Split the app or remove resource views.",
                )
            )
        total_views += len(resource_views)
        target_motifs = _screen_target_motif_ids(screen)
        seen_view_ids: list[str] = []
        for view_index, resource_view in enumerate(resource_views):
            path = f"{screen_path}.resource_views[{view_index}]"
            if not isinstance(resource_view, dict):
                issues.append(_issue("APP_RESOURCE_BINDING_VIEW_NOT_OBJECT", path, "Each resource_view must be an object."))
                continue
            extra = sorted(set(resource_view) - APP_BUNDLE_ALLOWED_RESOURCE_VIEW_FIELDS)
            if extra:
                issues.append(
                    _issue(
                        "APP_RESOURCE_BINDING_QUERY_UNSUPPORTED",
                        path,
                        f"resource_view contains unsupported field(s): {', '.join(extra)}.",
                        "Remove transform, query, pagination, grouping, aggregation, or adapter fields from Resource Binding V0.",
                    )
                )
            view_id = _required_string(resource_view, "id", path, issues)
            if view_id:
                seen_view_ids.append(view_id)
            _validate_safe_id(view_id, f"{path}.id", "resource view id", issues)
            resource_id = _required_string(resource_view, "resource_id", path, issues)
            _validate_safe_id(resource_id, f"{path}.resource_id", "resource view resource id", issues)
            mode = _required_string(resource_view, "mode", path, issues)
            if mode and mode != "list":
                issues.append(
                    _issue(
                        "APP_RESOURCE_BINDING_MODE_UNSUPPORTED",
                        f"{path}.mode",
                        "Resource Binding V0 supports mode list only.",
                        "Use mode list or remove the resource_view.",
                    )
                )
            record_ids = _required_array(resource_view, "record_ids", path, issues)
            fields = _required_array(resource_view, "fields", path, issues)
            _check_list_count(record_ids, APP_RESOURCE_BINDING_MAX_RECORD_REFS_PER_VIEW, f"{path}.record_ids", "APP_RESOURCE_BINDING_LIMIT_EXCEEDED", "record refs per resource view", issues)
            _check_list_count(fields, APP_RESOURCE_BINDING_MAX_FIELDS_PER_VIEW, f"{path}.fields", "APP_RESOURCE_BINDING_LIMIT_EXCEEDED", "fields per resource view", issues)
            clean_record_ids = _validate_resource_binding_string_list(record_ids, f"{path}.record_ids", "record id", issues)
            clean_fields = _validate_resource_binding_string_list(fields, f"{path}.fields", "field", issues)
            _validate_unique_ids(clean_record_ids, f"{path}.record_ids", "APP_RESOURCE_BINDING_DUPLICATE_RECORD_REF", issues)
            _validate_unique_ids(clean_fields, f"{path}.fields", "APP_RESOURCE_BINDING_DUPLICATE_FIELD", issues)
            target_motif_id = _required_string(resource_view, "target_motif_id", path, issues)
            _validate_safe_id(target_motif_id, f"{path}.target_motif_id", "resource view target motif id", issues)
            if target_motif_id and target_motif_id not in target_motifs:
                issues.append(
                    _issue(
                        "APP_RESOURCE_BINDING_MOTIF_MISSING",
                        f"{path}.target_motif_id",
                        f"resource_view targets missing motif {target_motif_id}.",
                        "Use a motif id declared by this screen IntentBundle.",
                    )
                )
            records_by_id = resource_records.get(resource_id or "")
            if resource_id and records_by_id is None:
                issues.append(
                    _issue(
                        "APP_RESOURCE_BINDING_RESOURCE_MISSING",
                        f"{path}.resource_id",
                        f"resource_view references missing fixture resource {resource_id}.",
                        "Use an existing fixture resource id.",
                    )
                )
                continue
            for record_id in clean_record_ids:
                record = records_by_id.get(record_id) if isinstance(records_by_id, dict) else None
                if record is None:
                    issues.append(
                        _issue(
                            "APP_RESOURCE_BINDING_RECORD_MISSING",
                            f"{path}.record_ids",
                            f"resource_view references missing fixture record {record_id}.",
                            "Use a record id declared by the fixture resource.",
                        )
                    )
                    continue
                for field in clean_fields:
                    if field not in record:
                        issues.append(
                            _issue(
                                "APP_RESOURCE_BINDING_FIELD_MISSING",
                                f"{path}.fields",
                                f"resource_view references missing fixture field {field} on record {record_id}.",
                                "Use only fields present on every referenced fixture record.",
                            )
                        )
                    elif not _is_resource_binding_scalar(record.get(field)):
                        issues.append(
                            _issue(
                                "APP_RESOURCE_BINDING_VALUE_UNSUPPORTED",
                                f"{path}.fields",
                                f"resource_view references non-scalar fixture field {field} on record {record_id}.",
                                "Use only string, number, boolean, or null fixture scalars in Resource Binding V0.",
                            )
                        )
                    total_assertions += 1
        _validate_unique_ids(seen_view_ids, f"{screen_path}.resource_views", "APP_RESOURCE_BINDING_DUPLICATE_VIEW_ID", issues)
    if total_views > APP_RESOURCE_BINDING_MAX_VIEWS:
        issues.append(
            _issue(
                "APP_RESOURCE_BINDING_LIMIT_EXCEEDED",
                "$.screens",
                f"App declares {total_views} resource views; limit is {APP_RESOURCE_BINDING_MAX_VIEWS}.",
                "Split the app or remove resource views.",
            )
        )
    if total_assertions > APP_RESOURCE_BINDING_MAX_ASSERTIONS:
        issues.append(
            _issue(
                "APP_RESOURCE_BINDING_LIMIT_EXCEEDED",
                "$.screens",
                f"App declares {total_assertions} record-field assertions; limit is {APP_RESOURCE_BINDING_MAX_ASSERTIONS}.",
                "Reduce record refs or fields per resource view.",
            )
        )
    if total_assertions == 0:
        issues.append(
            _issue(
                "APP_RESOURCE_BINDING_EMPTY_ASSERTIONS",
                "$.screens",
                "fixture_readonly_v0 requires at least one concrete record-field assertion.",
                "Declare at least one resource_view with one record_id and one field.",
            )
        )


def _fixture_records_by_resource(resources: list[Any], issues: list[dict[str, str]]) -> dict[str, dict[str, dict[str, Any]]]:
    resources_by_id: dict[str, dict[str, dict[str, Any]]] = {}
    for resource_index, resource in enumerate(resources):
        if not isinstance(resource, dict) or not isinstance(resource.get("id"), str):
            continue
        resource_id = resource["id"]
        records = resource.get("records") if isinstance(resource.get("records"), list) else []
        by_id: dict[str, dict[str, Any]] = {}
        seen_ids: list[str] = []
        for record_index, record in enumerate(records):
            path = f"$.resources[{resource_index}].records[{record_index}].id"
            if not isinstance(record, dict):
                continue
            record_id = record.get("id")
            if not isinstance(record_id, str) or not record_id:
                issues.append(
                    _issue(
                        "APP_RESOURCE_BINDING_RECORD_ID_REQUIRED",
                        path,
                        "fixture_readonly_v0 fixture records must declare a non-empty string id.",
                        "Add a safe string id field to every fixture record.",
                    )
                )
                continue
            _validate_safe_id(record_id, path, "fixture record id", issues)
            seen_ids.append(record_id)
            if record_id not in by_id:
                by_id[record_id] = record
        _validate_unique_ids(seen_ids, f"$.resources[{resource_index}].records", "APP_RESOURCE_BINDING_DUPLICATE_RECORD_ID", issues)
        resources_by_id[resource_id] = by_id
    return resources_by_id


def _screen_target_motif_ids(screen: dict[str, Any]) -> set[str]:
    intent = screen.get("intent_bundle") if isinstance(screen.get("intent_bundle"), dict) else {}
    view_spec = intent.get("view_spec") if isinstance(intent.get("view_spec"), dict) else {}
    motifs = view_spec.get("motifs") if isinstance(view_spec.get("motifs"), list) else []
    return {motif.get("id") for motif in motifs if isinstance(motif, dict) and isinstance(motif.get("id"), str)}


def _validate_resource_binding_string_list(values: list[Any], path: str, label: str, issues: list[dict[str, str]]) -> list[str]:
    clean: list[str] = []
    for index, value in enumerate(values):
        item_path = f"{path}[{index}]"
        if not isinstance(value, str) or not value:
            issues.append(
                _issue(
                    "APP_RESOURCE_BINDING_REF_INVALID",
                    item_path,
                    f"resource_view {label} refs must be non-empty strings.",
                    "Use safe string ids only.",
                )
            )
            continue
        _validate_safe_id(value, item_path, label, issues)
        clean.append(value)
    return clean


def _is_resource_binding_scalar(value: Any) -> bool:
    return isinstance(value, str) or isinstance(value, bool) or value is None or type(value) in {int, float}


def _route_assertions(payload: dict[str, Any]) -> dict[str, bool]:
    routes = payload.get("routes") if isinstance(payload.get("routes"), list) else []
    screens = payload.get("screens") if isinstance(payload.get("screens"), list) else []
    app = payload.get("app") if isinstance(payload.get("app"), dict) else {}
    route_paths = {route.get("path") for route in routes if isinstance(route, dict)}
    screen_ids = {screen.get("id") for screen in screens if isinstance(screen, dict)}
    route_screen_ids = {route.get("screen_id") for route in routes if isinstance(route, dict)}
    return {
        "root_route_resolves": app.get("root_route") in route_paths,
        "all_routes_resolve": all(screen_id in screen_ids for screen_id in route_screen_ids),
        "all_screens_reachable": all(screen_id in route_screen_ids for screen_id in screen_ids),
    }


def _validation_payload(
    payload: dict[str, Any] | None,
    issues: list[dict[str, str]],
    *,
    compile_check: bool,
    raw_bytes: int,
) -> dict[str, Any]:
    summary = _app_summary(payload) if isinstance(payload, dict) else None
    route_assertions = _route_assertions(payload) if isinstance(payload, dict) else None
    compile_status = "skipped" if not compile_check else "passed" if not issues else "failed"
    resource_binding = _resource_binding_for_payload(payload)
    binding_validation = _resource_binding_validation_summary(payload) if isinstance(payload, dict) and resource_binding == APP_BUNDLE_RESOURCE_BINDING_READONLY else None
    return {
        "schema_version": APP_BUNDLE_RESULT_SCHEMA_VERSION,
        "app_schema_version": _app_schema_version(payload),
        "ok": not issues,
        "compile_check": compile_status,
        "resource_binding": resource_binding,
        **({"binding_scope": APP_BUNDLE_BINDING_SCOPE} if resource_binding == APP_BUNDLE_RESOURCE_BINDING_READONLY else {}),
        **({"resource_binding_validation": binding_validation} if binding_validation is not None else {}),
        "summary": summary,
        "route_assertions": route_assertions,
        "raw_bytes": raw_bytes,
        "limits": _app_limits(),
        "issues": issues,
    }


def _app_summary(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    app = payload.get("app") if isinstance(payload.get("app"), dict) else {}
    routes = payload.get("routes") if isinstance(payload.get("routes"), list) else []
    screens = payload.get("screens") if isinstance(payload.get("screens"), list) else []
    resources = payload.get("resources") if isinstance(payload.get("resources"), list) else []
    return {
        "schema_version": payload.get("schema_version"),
        "id": app.get("id"),
        "title": app.get("title"),
        "kind": app.get("kind"),
        "root_route": app.get("root_route"),
        "route_count": len(routes),
        "screen_count": len(screens),
        "resource_count": len(resources),
    }


def _app_schema_version(payload: dict[str, Any] | None) -> int | None:
    if not isinstance(payload, dict):
        return None
    schema_version = payload.get("schema_version")
    return schema_version if type(schema_version) is int else None


def _resource_binding_for_payload(payload: dict[str, Any] | None) -> str:
    if isinstance(payload, dict) and payload.get("schema_version") == APP_BUNDLE_BOUND_SCHEMA_VERSION:
        return APP_BUNDLE_RESOURCE_BINDING_READONLY
    return APP_BUNDLE_RESOURCE_BINDING


def _resource_binding_report_fields(
    payload: dict[str, Any] | None,
    resource_binding_report: dict[str, Any] | None,
) -> dict[str, Any]:
    resource_binding = _resource_binding_for_payload(payload)
    fields: dict[str, Any] = {"resource_binding": resource_binding}
    if resource_binding != APP_BUNDLE_RESOURCE_BINDING_READONLY:
        return fields
    fields["binding_scope"] = APP_BUNDLE_BINDING_SCOPE
    if isinstance(resource_binding_report, dict):
        fields["resource_binding_assertions"] = resource_binding_report
    return fields


def _resource_binding_fields_from_validation(validation: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(validation, dict):
        return {"resource_binding": APP_BUNDLE_RESOURCE_BINDING}
    resource_binding = str(validation.get("resource_binding") or APP_BUNDLE_RESOURCE_BINDING)
    fields: dict[str, Any] = {"resource_binding": resource_binding}
    if resource_binding == APP_BUNDLE_RESOURCE_BINDING_READONLY:
        fields["binding_scope"] = str(validation.get("binding_scope") or APP_BUNDLE_BINDING_SCOPE)
        binding_validation = validation.get("resource_binding_validation")
        if isinstance(binding_validation, dict):
            fields["resource_binding_validation"] = binding_validation
    return fields


def _resource_binding_validation_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    resource_view_count = 0
    assertion_count = 0
    screen_count = 0
    if not isinstance(payload, dict):
        return {"binding_scope": APP_BUNDLE_BINDING_SCOPE, "resource_view_count": 0, "assertion_count": 0, "bound_screen_count": 0}
    screens = payload.get("screens") if isinstance(payload.get("screens"), list) else []
    resources = _fixture_records_by_resource(payload.get("resources") if isinstance(payload.get("resources"), list) else [], [])
    for screen in screens:
        if not isinstance(screen, dict):
            continue
        views = screen.get("resource_views") if isinstance(screen.get("resource_views"), list) else []
        if views:
            screen_count += 1
        for resource_view in views:
            if not isinstance(resource_view, dict):
                continue
            resource_view_count += 1
            records = resources.get(str(resource_view.get("resource_id") or ""), {})
            record_ids = [item for item in resource_view.get("record_ids", []) if isinstance(item, str)]
            fields = [item for item in resource_view.get("fields", []) if isinstance(item, str)]
            for record_id in record_ids:
                record = records.get(record_id)
                if not isinstance(record, dict):
                    continue
                assertion_count += sum(1 for field in fields if field in record)
    return {
        "binding_scope": APP_BUNDLE_BINDING_SCOPE,
        "resource_view_count": resource_view_count,
        "bound_screen_count": screen_count,
        "assertion_count": assertion_count,
        "limits": _resource_binding_limits(),
    }


def _app_limits() -> dict[str, int]:
    return {
        "max_raw_json_bytes": APP_BUNDLE_MAX_BYTES,
        "max_screens": APP_BUNDLE_MAX_SCREENS,
        "max_routes": APP_BUNDLE_MAX_ROUTES,
        "max_fixture_resources": APP_BUNDLE_MAX_RESOURCES,
        "max_records_per_resource": APP_BUNDLE_MAX_RECORDS_PER_RESOURCE,
        "max_scalar_fields_per_record": APP_BUNDLE_MAX_RECORD_FIELDS,
        "max_scalar_string_chars": APP_BUNDLE_MAX_SCALAR_STRING_CHARS,
        "max_embedded_intent_bytes": APP_BUNDLE_MAX_EMBEDDED_INTENT_BYTES,
        "max_aggregate_embedded_intent_bytes": APP_BUNDLE_MAX_AGGREGATE_INTENT_BYTES,
        "max_proof_report_bytes": APP_BUNDLE_MAX_PROOF_REPORT_BYTES,
        "max_support_bundle_bytes": APP_BUNDLE_MAX_SUPPORT_BUNDLE_BYTES,
        "max_id_chars": APP_BUNDLE_MAX_ID_CHARS,
        "max_route_chars": APP_BUNDLE_MAX_ROUTE_CHARS,
        **_resource_binding_limits(),
    }


def _resource_binding_limits() -> dict[str, int]:
    return {
        "max_resource_views": APP_RESOURCE_BINDING_MAX_VIEWS,
        "max_resource_views_per_screen": APP_RESOURCE_BINDING_MAX_VIEWS_PER_SCREEN,
        "max_record_refs_per_resource_view": APP_RESOURCE_BINDING_MAX_RECORD_REFS_PER_VIEW,
        "max_fields_per_resource_view": APP_RESOURCE_BINDING_MAX_FIELDS_PER_VIEW,
        "max_resource_binding_assertions": APP_RESOURCE_BINDING_MAX_ASSERTIONS,
        "max_resource_binding_report_bytes": APP_RESOURCE_BINDING_MAX_REPORT_BYTES,
    }


def _reject_unknown_fields(
    obj: dict[str, Any],
    path: str,
    allowed: set[str],
    issues: list[dict[str, str]],
) -> None:
    for key in sorted(set(obj) - allowed):
        issues.append(
            _issue(
                "APP_UNKNOWN_FIELD",
                f"{path}.{key}",
                f"Unknown AppBundle field {key}.",
                "Remove extension fields; AppBundle rejects unknown fields.",
            )
        )


def _reject_forbidden_object_keys(obj: dict[str, Any], path: str, issues: list[dict[str, str]]) -> None:
    for key in obj:
        lowered = str(key).lower()
        if lowered in FORBIDDEN_APP_FIELD_NAMES:
            issues.append(
                _issue(
                    "APP_FORBIDDEN_SURFACE",
                    f"{path}.{key}",
                    f"AppBundle rejects local-only side-effect or credential field {key}.",
                    "Remove URL, env, credential, adapter, fetch, mutation, package install, or hosted compiler config.",
                )
            )


def _validate_safe_id(value: str | None, path: str, label: str, issues: list[dict[str, str]]) -> bool:
    if not value:
        return False
    if len(value) > APP_BUNDLE_MAX_ID_CHARS:
        issues.append(
            _issue(
                "APP_ID_TOO_LONG",
                path,
                f"{label} exceeds {APP_BUNDLE_MAX_ID_CHARS} characters.",
                "Use a shorter stable id.",
            )
        )
        return False
    if SAFE_APP_ID_RE.match(value):
        return True
    issues.append(
        _issue(
            "APP_INVALID_ID",
            path,
            f"{label} '{value}' must match {SAFE_AGENT_ID_PATTERN}.",
            "Use only letters, digits, underscore, dot, and dash. Do not use spaces, slashes, colons, markup, or paths.",
        )
    )
    return False


def _validate_route_path(value: str | None, path: str, issues: list[dict[str, str]]) -> None:
    if not isinstance(value, str) or value == "":
        return
    bad = (
        len(value) > APP_BUNDLE_MAX_ROUTE_CHARS
        or not value.startswith("/")
        or not SAFE_ROUTE_RE.match(value)
        or "//" in value
        or "/../" in value
        or value.endswith("/..")
        or "/./" in value
        or value.endswith("/.")
        or "%" in value
        or "?" in value
        or "#" in value
        or "\\" in value
    )
    if bad:
        issues.append(
            _issue(
                "APP_ROUTE_PATH_INVALID",
                path,
                f"Route path {value!r} is not a canonical static AppBundle path.",
                "Use a unique path starting with / and only letters, digits, _, ., ~, -, and /.",
            )
        )


def _validate_app_scalar_string(value: str | None, path: str, issues: list[dict[str, str]]) -> None:
    if value is None:
        return
    if len(value) > APP_BUNDLE_MAX_SCALAR_STRING_CHARS:
        issues.append(
            _issue(
                "APP_STRING_TOO_LONG",
                path,
                f"String exceeds {APP_BUNDLE_MAX_SCALAR_STRING_CHARS} characters.",
                "Shorten AppBundle-owned strings.",
            )
        )
    if URL_SCHEME_RE.search(value) or ENV_REF_RE.search(value) or PACKAGE_INSTALL_RE.search(value):
        issues.append(
            _issue(
                "APP_FORBIDDEN_SURFACE",
                path,
                "AppBundle rejects URL schemes, environment references, and package-install flags.",
                "Remove network, environment, package install, or hosted compiler references.",
            )
        )


def _validate_fixture_scalar(value: Any, path: str, issues: list[dict[str, str]]) -> None:
    if value is None or isinstance(value, bool) or type(value) in {int, float}:
        return
    if isinstance(value, str):
        _validate_app_scalar_string(value, path, issues)
        return
    issues.append(
        _issue(
            "APP_FIXTURE_VALUE_NOT_SCALAR",
            path,
            "Fixture record values must be scalar JSON values.",
            "Use only strings, numbers, booleans, or null in fixture records.",
        )
    )


def _required_object(obj: dict[str, Any], key: str, path: str, issues: list[dict[str, str]]) -> dict[str, Any] | None:
    value = obj.get(key)
    if not isinstance(value, dict):
        issues.append(_issue("APP_MISSING_FIELD", f"{path}.{key}", f"Missing required object field {key}."))
        return None
    return value


def _required_array(obj: dict[str, Any], key: str, path: str, issues: list[dict[str, str]]) -> list[Any]:
    value = obj.get(key)
    if not isinstance(value, list):
        issues.append(_issue("APP_MISSING_FIELD", f"{path}.{key}", f"Missing required array field {key}."))
        return []
    return value


def _required_string(obj: dict[str, Any], key: str, path: str, issues: list[dict[str, str]]) -> str | None:
    value = obj.get(key)
    if not isinstance(value, str) or value == "":
        issues.append(_issue("APP_MISSING_FIELD", f"{path}.{key}", f"Missing required string field {key}."))
        return None
    return value


def _check_list_count(
    values: list[Any],
    limit: int,
    path: str,
    code: str,
    label: str,
    issues: list[dict[str, str]],
) -> None:
    if len(values) <= limit:
        return
    issues.append(
        _issue(
            code,
            path,
            f"AppBundle declares {len(values)} {label}; the V0 limit is {limit}.",
            "Split the app into smaller AppBundles.",
        )
    )


def _validate_unique_ids(ids: list[str], path: str, code: str, issues: list[dict[str, str]]) -> None:
    seen: set[str] = set()
    for item_id in ids:
        if item_id in seen:
            issues.append(_issue(code, path, f"Duplicate id {item_id}.", "Use unique stable ids."))
        seen.add(item_id)


def _issue(code: str, path: str, message: str, suggestion: str | None = None) -> dict[str, str]:
    return {
        "severity": "error",
        "code": code,
        "path": path,
        "message": message,
        "suggestion": suggestion or "Regenerate the AppBundle using the local AppBundle contract.",
    }


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value {value} is not allowed")


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _index_by_id(items: list[Any]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        if isinstance(item_id, str) and item_id not in indexed:
            indexed[item_id] = item
    return indexed


def _resource_sections(resources: list[Any]) -> dict[str, dict[str, Any]]:
    sections: dict[str, dict[str, Any]] = {}
    for resource in resources:
        if not isinstance(resource, dict) or not isinstance(resource.get("id"), str):
            continue
        records = resource.get("records") if isinstance(resource.get("records"), list) else []
        sections[resource["id"]] = {
            "id": resource.get("id"),
            "kind": resource.get("kind"),
            "record_count": len(records),
            "records_hash": _stable_json(records),
        }
    return sections


def _screen_sections(screens: list[Any]) -> dict[str, dict[str, Any]]:
    sections: dict[str, dict[str, Any]] = {}
    for screen in screens:
        if not isinstance(screen, dict) or not isinstance(screen.get("id"), str):
            continue
        sections[screen["id"]] = {
            "id": screen.get("id"),
            "title": screen.get("title"),
            "resource_view_count": len(screen.get("resource_views")) if isinstance(screen.get("resource_views"), list) else 0,
            "resource_views_hash": _stable_json(screen.get("resource_views", [])),
            "intent_hash": _stable_json(screen.get("intent_bundle")),
        }
    return sections


def _diff_named_items(left: dict[str, dict[str, Any]], right: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    return {
        "added": sorted(set(right) - set(left)),
        "removed": sorted(set(left) - set(right)),
        "changed": sorted(
            item_id
            for item_id in set(left) & set(right)
            if _stable_json(left[item_id]) != _stable_json(right[item_id])
        ),
    }


def _app_semantic_changes(left: dict[str, Any], right: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    changes: dict[str, list[dict[str, Any]]] = {
        "app_metadata": [],
        "routes": [],
        "resources": [],
        "screens": [],
        "screen_intents": [],
    }
    left_app = left.get("app") if isinstance(left.get("app"), dict) else {}
    right_app = right.get("app") if isinstance(right.get("app"), dict) else {}
    for field in ("id", "title", "kind", "root_route"):
        if _stable_json(left_app.get(field)) != _stable_json(right_app.get(field)):
            changes["app_metadata"].append({"field": field, "left": left_app.get(field), "right": right_app.get(field)})
    for field in ("schema_version", "resource_binding"):
        if _stable_json(left.get(field)) != _stable_json(right.get(field)):
            changes["app_metadata"].append({"field": field, "left": left.get(field), "right": right.get(field)})
    for section, left_items, right_items in (
        ("routes", _index_by_id(left.get("routes", [])), _index_by_id(right.get("routes", []))),
        ("resources", _resource_sections(left.get("resources", [])), _resource_sections(right.get("resources", []))),
        ("screens", _screen_sections(left.get("screens", [])), _screen_sections(right.get("screens", []))),
    ):
        for item_id in sorted(set(right_items) - set(left_items)):
            changes[section].append({"id": item_id, "change": "added"})
        for item_id in sorted(set(left_items) - set(right_items)):
            changes[section].append({"id": item_id, "change": "removed"})
        for item_id in sorted(set(left_items) & set(right_items)):
            left_item = left_items[item_id]
            right_item = right_items[item_id]
            for field in sorted(set(left_item) | set(right_item)):
                if field in {"id", "intent_hash", "records_hash", "resource_views_hash"}:
                    continue
                if _stable_json(left_item.get(field)) != _stable_json(right_item.get(field)):
                    changes[section].append(
                        {
                            "id": item_id,
                            "change": "field_changed",
                            "field": field,
                            "left": left_item.get(field),
                            "right": right_item.get(field),
                        }
                    )
            if section == "resources" and left_item.get("records_hash") != right_item.get("records_hash"):
                changes[section].append({"id": item_id, "change": "records_changed"})
            if section == "screens" and left_item.get("intent_hash") != right_item.get("intent_hash"):
                changes[section].append({"id": item_id, "change": "intent_changed"})
            if section == "screens" and left_item.get("resource_views_hash") != right_item.get("resource_views_hash"):
                changes[section].append({"id": item_id, "change": "resource_views_changed"})
    return changes


def _app_changed_fields(left: dict[str, Any], right: dict[str, Any]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for item in _app_semantic_changes(left, right)["app_metadata"]:
        field = str(item["field"])
        path = f"$.{field}" if field in {"schema_version", "resource_binding"} else f"$.app.{field}"
        fields.append({"path": path, "left": item.get("left"), "right": item.get("right")})
    return fields


def _app_counts(left: dict[str, Any], right: dict[str, Any]) -> dict[str, dict[str, int]]:
    return {
        "routes": {"left": len(left.get("routes", [])), "right": len(right.get("routes", []))},
        "resources": {"left": len(left.get("resources", [])), "right": len(right.get("resources", []))},
        "screens": {"left": len(left.get("screens", [])), "right": len(right.get("screens", []))},
        "resource_views": {"left": _resource_view_count(left), "right": _resource_view_count(right)},
    }


def _resource_view_count(payload: dict[str, Any]) -> int:
    total = 0
    for screen in payload.get("screens", []) if isinstance(payload.get("screens"), list) else []:
        if isinstance(screen, dict) and isinstance(screen.get("resource_views"), list):
            total += len(screen["resource_views"])
    return total


def _app_topology_similarity(changes: dict[str, dict[str, list[str]]]) -> float:
    changed = 0
    total = 0
    for section_changes in changes.values():
        changed += len(section_changes["added"]) + len(section_changes["removed"]) + len(section_changes["changed"])
        total += changed
    if total == 0:
        return 1.0
    return round(max(0.0, 1.0 - (changed / max(total, 1))), 4)


def _validation_summary(validation: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": validation.get("ok"),
        "app_schema_version": validation.get("app_schema_version"),
        "compile_check": validation.get("compile_check"),
        "summary": validation.get("summary"),
        "issue_count": len(validation.get("issues", [])) if isinstance(validation.get("issues"), list) else 0,
    }


def _validation_has_screen_intent_issue(validation: dict[str, Any]) -> bool:
    issues = validation.get("issues")
    if not isinstance(issues, list):
        return False
    return any(isinstance(issue, dict) and str(issue.get("code", "")).startswith("APP_SCREEN_INTENT") for issue in issues)


def _app_diff_error_payload(
    code: str,
    message: str,
    fix: str,
    *,
    validation: dict[str, Any] | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": APP_BUNDLE_RESULT_SCHEMA_VERSION,
        "diff_version": APP_BUNDLE_DIFF_VERSION,
        "basis": APP_BUNDLE_DIFF_BASIS,
        "ok": False,
        "compile_check": "failed",
        "validation": validation or {"left": None, "right": None},
        "changes": {"app": _empty_change_set(), "routes": _empty_change_set(), "resources": _empty_change_set(), "screens": _empty_change_set()},
        "changed_fields": [],
        "semantic_changes": {"app_metadata": [], "routes": [], "resources": [], "screens": [], "screen_intents": []},
        "semantic_summary": [],
        "screen_intent_diffs": {},
        "counts": {"routes": {"left": 0, "right": 0}, "resources": {"left": 0, "right": 0}, "screens": {"left": 0, "right": 0}},
        "topology_similarity": 0.0,
        "errors": errors or [{"code": code, "message": message, "fix": fix}],
    }


def _empty_change_set() -> dict[str, list[str]]:
    return {"added": [], "removed": [], "changed": []}


def _normalize_diff_errors(errors: object, *, screen_id: str) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    if not isinstance(errors, list):
        return normalized
    for error in errors[:8]:
        if not isinstance(error, dict):
            continue
        normalized.append(
            {
                "code": str(error.get("code") or "APP_DIFF_SCREEN_INTENT_INVALID"),
                "message": f"Screen {screen_id}: {error.get('message') or 'Embedded intent diff failed.'}",
                "fix": str(error.get("fix") or "Regenerate the changed screen IntentBundle."),
            }
        )
    return normalized


def _diff_value(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


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
        },
        "route_assertions": shell_parts["route_assertions"],
        "shell_artifact_hash": shell_artifact_hash,
        "shell_manifest_hash": shell_manifest_hash,
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


def _build_static_app_shell(
    payload: dict[str, Any],
    screen_reports: list[dict[str, Any]],
    *,
    resource_binding_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    route_assertions = _shell_route_assertions(payload)
    if not all(route_assertions.values()):
        raise AppBundleProofFailure(
            "APP_SHELL_ROUTE_ASSERTION_FAILED",
            "Static shell route assertions failed before shell write.",
            "Fix the AppBundle static route graph and retry compile-app.",
        )
    screen_payloads = _collect_shell_screens(screen_reports)
    aggregate_screen_html = sum(len(screen["fragment"].encode("utf-8")) for screen in screen_payloads)
    if aggregate_screen_html > APP_SHELL_MAX_AGGREGATE_SCREEN_HTML_BYTES:
        raise AppBundleProofFailure(
            "APP_SHELL_SIZE_LIMIT_EXCEEDED",
            f"Embedded checked screen HTML totals {aggregate_screen_html} bytes; limit is {APP_SHELL_MAX_AGGREGATE_SCREEN_HTML_BYTES}.",
            "Split the app into smaller AppBundles before compiling a static shell.",
        )
    route_table = _shell_route_table(payload)
    route_json = _safe_json_for_script(
        {
            "app": {
                "id": payload["app"]["id"],
                "title": payload["app"]["title"],
                "kind": payload["app"]["kind"],
                "rootRoute": payload["app"]["root_route"],
            },
            "routes": route_table,
        }
    )
    route_json_bytes = len(route_json.encode("utf-8"))
    if route_json_bytes > APP_SHELL_MAX_ROUTE_JSON_BYTES:
        raise AppBundleProofFailure(
            "APP_SHELL_SIZE_LIMIT_EXCEEDED",
            f"Serialized shell route table is {route_json_bytes} bytes; limit is {APP_SHELL_MAX_ROUTE_JSON_BYTES}.",
            "Reduce route labels or split the app into smaller AppBundles.",
        )
    styles = _dedupe_styles(screen_payloads)
    route_script = _app_shell_route_script()
    script_bytes = len(route_script.encode("utf-8"))
    if script_bytes > APP_SHELL_MAX_JS_BYTES:
        raise AppBundleProofFailure(
            "APP_SHELL_SIZE_LIMIT_EXCEEDED",
            f"Static shell JS is {script_bytes} bytes; limit is {APP_SHELL_MAX_JS_BYTES}.",
            "Reduce the shell runtime before compiling.",
        )
    html = _render_static_app_shell_html(payload, screen_payloads, styles, route_json, route_script)
    html_bytes = len(html.encode("utf-8"))
    if html_bytes > APP_SHELL_MAX_HTML_BYTES:
        raise AppBundleProofFailure(
            "APP_SHELL_SIZE_LIMIT_EXCEEDED",
            f"Static shell HTML is {html_bytes} bytes; limit is {APP_SHELL_MAX_HTML_BYTES}.",
            "Split the app into smaller AppBundles before compiling a static shell.",
        )
    _assert_rendered_shell_static_contract(html)
    manifest = _static_shell_manifest(
        payload,
        screen_reports,
        route_assertions,
        html_bytes,
        script_bytes,
        route_json_bytes,
        aggregate_screen_html,
        resource_binding_report=resource_binding_report,
    )
    return {"html": html, "manifest": manifest, "route_assertions": route_assertions}


def _collect_shell_screens(screen_reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    screens: list[dict[str, Any]] = []
    for screen in screen_reports:
        if not isinstance(screen, dict) or screen.get("errors"):
            raise AppBundleProofFailure(
                "APP_SHELL_SCREEN_FAILED",
                f"Screen {screen.get('id') if isinstance(screen, dict) else 'unknown'} is not a passed checked artifact.",
                "Fix screen validation, compile, and check errors before compiling the shell.",
            )
        paths = screen.get("paths") if isinstance(screen.get("paths"), dict) else {}
        artifact = Path(str(paths.get("artifact") or ""))
        if not artifact.exists():
            raise AppBundleProofFailure(
                "APP_SHELL_SCREEN_ARTIFACT_MISSING",
                f"Screen {screen.get('id')} artifact is missing.",
                "Regenerate checked screen artifacts before compiling the shell.",
            )
        html = artifact.read_text(encoding="utf-8")
        screen_id = str(screen.get("id"))
        _assert_screen_artifact_shell_safe(html, screen_id)
        fragment = _extract_screen_body_fragment(html, screen_id)
        styles = _extract_screen_styles(html, screen_id)
        screens.append(
            {
                "id": screen_id,
                "title": str(screen.get("title") or screen_id),
                "fragment": fragment,
                "styles": styles,
                "artifact_hash": screen.get("artifact_hash"),
                "manifest_hash": screen.get("manifest_hash"),
            }
        )
    return screens


def _assert_screen_artifact_shell_safe(html: str, screen_id: str) -> None:
    if HTML_SCRIPT_RE.search(html):
        raise AppBundleProofFailure(
            "APP_SHELL_EMBEDDING_UNSUPPORTED",
            f"Screen {screen_id} contains a script tag; Static Shell V0 embeds inert screen fragments only.",
            "Remove action/runtime script surfaces or use a later app-generation slice.",
        )
    if HTML_FORBIDDEN_EMBED_RE.search(html) or HTML_IMPORT_MAP_RE.search(html) or HTML_WORKER_IMPORT_RE.search(html):
        raise AppBundleProofFailure(
            "APP_SHELL_EMBEDDING_UNSUPPORTED",
            f"Screen {screen_id} contains an unsupported embed, frame, import map, or worker surface.",
            "Remove embed/runtime surfaces before compiling the static shell.",
        )
    if HTML_FORBIDDEN_LINK_RE.search(html) or URL_SCHEME_RE.search(html) or HTML_PROTOCOL_RELATIVE_RE.search(html):
        raise AppBundleProofFailure(
            "APP_SHELL_NETWORK_SURFACE_REJECTED",
            f"Screen {screen_id} contains a URL-bearing or external resource surface.",
            "Remove external resources before compiling the static shell.",
        )
    if HTML_INLINE_HANDLER_RE.search(html):
        raise AppBundleProofFailure(
            "APP_SHELL_EMBEDDING_UNSUPPORTED",
            f"Screen {screen_id} contains inline event handlers.",
            "Remove inline event handlers before compiling the static shell.",
        )
    for style in HTML_STYLE_RE.findall(html):
        if CSS_FORBIDDEN_RE.search(style):
            raise AppBundleProofFailure(
                "APP_SHELL_NETWORK_SURFACE_REJECTED",
                f"Screen {screen_id} CSS contains an import, URL, expression, or script-like value.",
                "Remove external or executable CSS surfaces before compiling the static shell.",
            )


def _extract_screen_body_fragment(html: str, screen_id: str) -> str:
    match = HTML_BODY_RE.search(html)
    if not match:
        raise AppBundleProofFailure(
            "APP_SHELL_EMBEDDING_UNSUPPORTED",
            f"Screen {screen_id} artifact does not contain a body element.",
            "Regenerate the screen through the html-tailwind compiler.",
        )
    fragment = match.group("body").strip()
    if not fragment:
        raise AppBundleProofFailure(
            "APP_SHELL_EMBEDDING_UNSUPPORTED",
            f"Screen {screen_id} body is empty.",
            "Regenerate the screen through the html-tailwind compiler.",
        )
    return fragment


def _extract_screen_styles(html: str, screen_id: str) -> list[str]:
    del screen_id
    return [style.strip() for style in HTML_STYLE_RE.findall(html) if style.strip()]


def _dedupe_styles(screen_payloads: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    styles: list[str] = []
    for screen in screen_payloads:
        for style in screen["styles"]:
            if style in seen:
                continue
            seen.add(style)
            styles.append(style)
    return styles


def _shell_route_table(payload: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "id": str(route["id"]),
            "path": str(route["path"]),
            "label": str(route["label"]),
            "screenId": str(route["screen_id"]),
        }
        for route in payload["routes"]
    ]


def _shell_route_assertions(payload: dict[str, Any]) -> dict[str, bool]:
    routes = payload.get("routes") if isinstance(payload.get("routes"), list) else []
    screens = payload.get("screens") if isinstance(payload.get("screens"), list) else []
    screen_ids = {screen.get("id") for screen in screens if isinstance(screen, dict)}
    route_paths = [route.get("path") for route in routes if isinstance(route, dict)]
    route_screen_ids = [route.get("screen_id") for route in routes if isinstance(route, dict)]
    root_route = payload.get("app", {}).get("root_route") if isinstance(payload.get("app"), dict) else None
    return {
        "every_route_maps_exactly_one_screen": all(route_screen_ids.count(screen_id) >= 1 and screen_id in screen_ids for screen_id in route_screen_ids),
        "every_screen_has_route": all(screen_id in route_screen_ids for screen_id in screen_ids),
        "root_route_selects_exactly_one_screen": route_paths.count(root_route) == 1,
        "unknown_route_selects_no_screen_and_one_404": True,
    }


def _render_static_app_shell_html(
    payload: dict[str, Any],
    screen_payloads: list[dict[str, Any]],
    styles: list[str],
    route_json: str,
    route_script: str,
) -> str:
    root_route = payload["app"]["root_route"]
    route_by_screen = {route["screen_id"]: route["path"] for route in payload["routes"]}
    root_screen_id = next(route["screen_id"] for route in payload["routes"] if route["path"] == root_route)
    screen_sections: list[str] = []
    for screen in screen_payloads:
        screen_id = screen["id"]
        selected = screen_id == root_screen_id
        screen_sections.extend(
            [
                (
                    f'<section class="vs-app-screen" data-viewspec-app-screen="{escape(screen_id, quote=True)}" '
                    f'data-route-path="{escape(str(route_by_screen.get(screen_id, "")), quote=True)}" '
                    f'data-selected="{"true" if selected else "false"}"'
                    f'{" hidden" if not selected else ""}>'
                ),
                screen["fragment"],
                "</section>",
            ]
        )
    style_text = "\n\n".join([_app_shell_css(), *styles])
    return "\n".join(
        [
            "<!DOCTYPE html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            "<title>ViewSpec Static App</title>",
            "<style>",
            style_text,
            "</style>",
            "</head>",
            '<body data-viewspec-app-shell="static_shell_v0">',
            '<div class="vs-app-shell">',
            '<header class="vs-app-chrome">',
            '<div class="vs-app-title-block">',
            '<p class="vs-app-kicker">ViewSpec Static Shell</p>',
            '<h1 id="vs-app-title" class="vs-app-title"></h1>',
            "</div>",
            '<nav id="vs-app-nav" class="vs-app-nav" aria-label="App routes"></nav>',
            "</header>",
            '<main id="vs-app-main" class="vs-app-main">',
            *screen_sections,
            '<section class="vs-app-404" data-viewspec-app-404 hidden>',
            '<p class="vs-app-kicker">Route unavailable</p>',
            '<h2>Unknown route</h2>',
            '<p>The selected local route is not declared in this AppBundle.</p>',
            "</section>",
            "</main>",
            "</div>",
            f'<script type="application/json" id="viewspec-app-route-data">{route_json}</script>',
            "<script>",
            route_script,
            "</script>",
            "</body>",
            "</html>",
            "",
        ]
    )


def _app_shell_css() -> str:
    return """
.vs-app-shell {
  min-height: 100vh;
  background: #eef2f7;
  color: #0f172a;
}
.vs-app-chrome {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 18px;
  border-bottom: 1px solid #cbd5e1;
  background: #ffffff;
}
.vs-app-title-block { min-width: 0; }
.vs-app-kicker {
  margin: 0 0 3px;
  color: #64748b;
  font-size: 0.72rem;
  font-weight: 800;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.vs-app-title {
  margin: 0;
  color: #0f172a;
  font-size: 1.05rem;
  line-height: 1.2;
}
.vs-app-nav {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}
.vs-app-route-button {
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  background: #f8fafc;
  color: #334155;
  padding: 7px 10px;
  font: inherit;
  font-size: 0.86rem;
  font-weight: 800;
  cursor: pointer;
}
.vs-app-route-button[aria-current="page"] {
  border-color: #0f766e;
  background: #0f766e;
  color: #ffffff;
}
.vs-app-main { min-height: calc(100vh - 66px); }
.vs-app-screen[hidden], .vs-app-404[hidden] { display: none !important; }
.vs-app-404 {
  width: min(100%, 760px);
  margin: 42px auto;
  border: 1px solid #fecaca;
  border-radius: 8px;
  background: #fff1f2;
  color: #7f1d1d;
  padding: 24px;
}
@media (max-width: 760px) {
  .vs-app-chrome { align-items: stretch; flex-direction: column; }
  .vs-app-nav { justify-content: flex-start; }
}
""".strip()


def _app_shell_route_script() -> str:
    return """
(() => {
  const dataEl = document.getElementById('viewspec-app-route-data');
  const payload = JSON.parse(dataEl.textContent || '{}');
  const routes = Array.isArray(payload.routes) ? payload.routes : [];
  const app = payload.app || {};
  const rootRoute = typeof app.rootRoute === 'string' ? app.rootRoute : '/';
  const titleEl = document.getElementById('vs-app-title');
  const navEl = document.getElementById('vs-app-nav');
  const notFoundEl = document.querySelector('[data-viewspec-app-404]');
  const screens = Array.from(document.querySelectorAll('[data-viewspec-app-screen]'));
  const routeByPath = new Map(routes.map((route) => [route.path, route]));

  function hashPath() {
    const raw = window.location.hash.slice(1);
    if (!raw) return rootRoute;
    return raw.startsWith('/') ? raw : `/${raw}`;
  }

  function setRoute(path) {
    const route = routeByPath.get(path);
    const known = Boolean(route);
    let selectedCount = 0;
    screens.forEach((screen) => {
      const selected = known && screen.dataset.viewspecAppScreen === route.screenId;
      screen.hidden = !selected;
      screen.dataset.selected = selected ? 'true' : 'false';
      if (selected) selectedCount += 1;
    });
    if (notFoundEl) notFoundEl.hidden = known;
    Array.from(navEl.querySelectorAll('[data-route-path]')).forEach((button) => {
      button.setAttribute('aria-current', button.dataset.routePath === path && known ? 'page' : 'false');
    });
    const appTitle = typeof app.title === 'string' ? app.title : 'ViewSpec App';
    titleEl.textContent = appTitle;
    document.title = known ? `${appTitle} - ${route.label}` : `${appTitle} - Unknown route`;
    document.body.dataset.routeKnown = known ? 'true' : 'false';
    document.body.dataset.selectedScreenCount = String(selectedCount);
  }

  routes.forEach((route) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'vs-app-route-button';
    button.dataset.routePath = route.path;
    button.textContent = route.label;
    button.addEventListener('click', () => {
      window.location.hash = route.path;
      setRoute(route.path);
    });
    navEl.appendChild(button);
  });

  window.addEventListener('hashchange', () => setRoute(hashPath()));
  setRoute(hashPath());
})();
""".strip()


def _safe_json_for_script(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return text.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")


def _assert_rendered_shell_static_contract(html: str) -> None:
    if len(re.findall(r"<section\b[^>]*\bdata-viewspec-app-404\b", html, flags=re.IGNORECASE)) != 1:
        raise AppBundleProofFailure(
            "APP_SHELL_ROUTE_ASSERTION_FAILED",
            "Rendered shell must contain exactly one local 404 panel.",
            "Regenerate the shell from the validated AppBundle route graph.",
        )
    if "http:" in html.lower() or "https:" in html.lower() or HTML_PROTOCOL_RELATIVE_RE.search(html):
        raise AppBundleProofFailure(
            "APP_SHELL_NETWORK_SURFACE_REJECTED",
            "Rendered shell contains a network URL surface.",
            "Remove external resources before compiling the static shell.",
        )
    if HTML_FORBIDDEN_EMBED_RE.search(html) or HTML_IMPORT_MAP_RE.search(html) or HTML_WORKER_IMPORT_RE.search(html):
        raise AppBundleProofFailure(
            "APP_SHELL_EMBEDDING_UNSUPPORTED",
            "Rendered shell contains unsupported frame/embed/import/worker surfaces.",
            "Remove unsupported surfaces before compiling the static shell.",
        )
    if HTML_INLINE_HANDLER_RE.search(html):
        raise AppBundleProofFailure(
            "APP_SHELL_EMBEDDING_UNSUPPORTED",
            "Rendered shell contains inline event handlers.",
            "Use the compiler-owned static route script only.",
        )


def _static_shell_manifest(
    payload: dict[str, Any],
    screen_reports: list[dict[str, Any]],
    route_assertions: dict[str, bool],
    shell_html_bytes: int,
    shell_js_bytes: int,
    route_json_bytes: int,
    aggregate_screen_html_bytes: int,
    *,
    resource_binding_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "app_schema_version": _app_schema_version(payload),
        "kind": "app_static_shell_compile",
        "target": APP_SHELL_TARGET,
        "route_navigation": APP_SHELL_ROUTE_NAVIGATION,
        **_resource_binding_report_fields(payload, resource_binding_report),
        "policy": {"network_calls": "none"},
        "app": _app_summary(payload),
        "routes": _shell_route_table(payload),
        "route_assertions": route_assertions,
        "screens": _screen_shell_summaries(screen_reports),
        "limits": _app_shell_limits(),
        "sizes": {
            "shell_html_bytes": shell_html_bytes,
            "shell_js_bytes": shell_js_bytes,
            "route_json_bytes": route_json_bytes,
            "aggregate_screen_html_bytes": aggregate_screen_html_bytes,
        },
    }


def _screen_shell_summaries(screen_reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for screen in screen_reports:
        if not isinstance(screen, dict):
            continue
        summaries.append(
            {
                "id": screen.get("id"),
                "title": screen.get("title"),
                "validation_status": screen.get("validation_status"),
                "compile_status": screen.get("compile_status"),
                "check_status": screen.get("check_status"),
                "artifact_hash": screen.get("artifact_hash"),
                "manifest_hash": screen.get("manifest_hash"),
                "manifest_summary": screen.get("manifest_summary"),
            }
        )
    return summaries


def _app_shell_limits() -> dict[str, int]:
    return {
        "max_screens": APP_BUNDLE_MAX_SCREENS,
        "max_routes": APP_BUNDLE_MAX_ROUTES,
        "max_shell_html_bytes": APP_SHELL_MAX_HTML_BYTES,
        "max_shell_js_bytes": APP_SHELL_MAX_JS_BYTES,
        "max_route_json_bytes": APP_SHELL_MAX_ROUTE_JSON_BYTES,
        "max_aggregate_screen_html_bytes": APP_SHELL_MAX_AGGREGATE_SCREEN_HTML_BYTES,
        "max_external_network_surfaces": 0,
        "max_dynamic_route_features": 0,
        "max_third_party_executable_surfaces": 0,
        "max_generated_framework_files": 0,
        **_resource_binding_limits(),
    }


def _resource_binding_assertion_report(payload: dict[str, Any], screen_reports: list[dict[str, Any]]) -> dict[str, Any] | None:
    if _resource_binding_for_payload(payload) != APP_BUNDLE_RESOURCE_BINDING_READONLY:
        return None
    screen_reports_by_id = {screen.get("id"): screen for screen in screen_reports if isinstance(screen, dict)}
    resources = _fixture_records_by_resource(payload.get("resources") if isinstance(payload.get("resources"), list) else [], [])
    views: list[dict[str, Any]] = []
    assertions: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    occurrence_credit: set[tuple[str, str, str]] = set()
    screens = payload.get("screens") if isinstance(payload.get("screens"), list) else []
    for screen in screens:
        if not isinstance(screen, dict) or not isinstance(screen.get("id"), str):
            continue
        screen_id = screen["id"]
        projection = _screen_manifest_binding_projection(screen_reports_by_id.get(screen_id))
        for resource_view in screen.get("resource_views", []) if isinstance(screen.get("resource_views"), list) else []:
            if not isinstance(resource_view, dict):
                continue
            view_id = str(resource_view.get("id") or "")
            target_motif_id = str(resource_view.get("target_motif_id") or "")
            resource_id = str(resource_view.get("resource_id") or "")
            view_assertions: list[dict[str, Any]] = []
            records = resources.get(resource_id, {})
            record_ids = [item for item in resource_view.get("record_ids", []) if isinstance(item, str)]
            fields = [item for item in resource_view.get("fields", []) if isinstance(item, str)]
            ambiguous_view_values = _resource_binding_ambiguous_view_values(records, record_ids, fields)
            for record_id in record_ids:
                record = records.get(record_id, {})
                for field in fields:
                    value_text = _resource_binding_scalar_text(record.get(field) if isinstance(record, dict) else None)
                    ambiguous_value = (field, value_text) in ambiguous_view_values
                    candidates = (
                        []
                        if ambiguous_value
                        else _resource_binding_candidates(
                            projection,
                            screen,
                            target_motif_id=target_motif_id,
                            record_id=record_id,
                            field=field,
                            value_text=value_text,
                        )
                    )
                    assertion = {
                        "screen_id": screen_id,
                        "resource_view_id": view_id,
                        "resource_id": resource_id,
                        "record_id": record_id,
                        "field": field,
                        "target_motif_id": target_motif_id,
                        "expected": value_text,
                        "status": "passed" if len(candidates) == 1 else "failed",
                        "source": "compiler_semantic_inventory_text",
                        "matched_binding_id": candidates[0]["binding_id"] if len(candidates) == 1 else None,
                        "matched_dom_id": candidates[0]["dom_id"] if len(candidates) == 1 else None,
                    }
                    if ambiguous_value:
                        errors.append(
                            {
                                "code": "APP_RESOURCE_BINDING_AMBIGUOUS_VALUE",
                                "message": f"Resource view {view_id} repeats scalar value {value_text!r} for field {field}.",
                                "fix": "Use unique scalar values within each declared resource_view field or defer this proof to a later binding slice.",
                            }
                        )
                    elif len(candidates) == 1:
                        credit_key = (screen_id, str(candidates[0]["dom_id"]), f"{view_id}:{record_id}:{field}")
                        if any(existing[0] == credit_key[0] and existing[1] == credit_key[1] for existing in occurrence_credit):
                            assertion["status"] = "failed"
                            errors.append(
                                {
                                    "code": "APP_RESOURCE_BINDING_AMBIGUOUS_VALUE",
                                    "message": f"Screen {screen_id} value for {view_id}.{record_id}.{field} reused one semantic occurrence.",
                                    "fix": "Render each record-field assertion from a distinct target motif binding.",
                                }
                            )
                        else:
                            occurrence_credit.add(credit_key)
                    else:
                        error_code = "APP_RESOURCE_BINDING_ASSERTION_FAILED"
                        if len(candidates) > 1:
                            error_code = "APP_RESOURCE_BINDING_AMBIGUOUS_VALUE"
                        errors.append(
                            {
                                "code": error_code,
                                "message": f"Screen {screen_id} failed resource binding assertion {view_id}.{record_id}.{field}.",
                                "fix": "Render the exact fixture scalar as visible text in the declared target motif binding.",
                            }
                        )
                    view_assertions.append(assertion)
                    assertions.append(assertion)
            view_status = "passed" if view_assertions and all(item.get("status") == "passed" for item in view_assertions) else "failed"
            if not view_assertions:
                errors.append(
                    {
                        "code": "APP_RESOURCE_BINDING_EMPTY_ASSERTIONS",
                        "message": f"Resource view {view_id} produced no record-field assertions.",
                        "fix": "Declare at least one record_id and one field for every resource_view.",
                    }
                )
            views.append(
                {
                    "id": view_id,
                    "screen_id": screen_id,
                    "resource_id": resource_id,
                    "target_motif_id": target_motif_id,
                    "assertion_count": len(view_assertions),
                    "passed_count": sum(1 for item in view_assertions if item.get("status") == "passed"),
                    "status": view_status,
                    "assertions": view_assertions,
                }
            )
    if not assertions:
        errors.append(
            {
                "code": "APP_RESOURCE_BINDING_EMPTY_ASSERTIONS",
                "message": "fixture_readonly_v0 produced no record-field assertions.",
                "fix": "Declare at least one resource_view with one record_id and one field.",
            }
        )
    digest_payload = {
        "binding_scope": APP_BUNDLE_BINDING_SCOPE,
        "resource_binding": APP_BUNDLE_RESOURCE_BINDING_READONLY,
        "assertions": [
            {
                "screen_id": item.get("screen_id"),
                "resource_view_id": item.get("resource_view_id"),
                "record_id": item.get("record_id"),
                "field": item.get("field"),
                "target_motif_id": item.get("target_motif_id"),
                "expected": item.get("expected"),
                "matched_binding_id": item.get("matched_binding_id"),
                "matched_dom_id": item.get("matched_dom_id"),
                "status": item.get("status"),
            }
            for item in assertions
        ],
    }
    binding_digest = _sha256_text(_stable_json(digest_payload))
    report = {
        "ok": not errors,
        "resource_binding": APP_BUNDLE_RESOURCE_BINDING_READONLY,
        "binding_scope": APP_BUNDLE_BINDING_SCOPE,
        "proof_source": "compiler_semantic_inventory_text",
        "assertion_count": len(assertions),
        "passed_count": sum(1 for item in assertions if item.get("status") == "passed"),
        "failed_count": sum(1 for item in assertions if item.get("status") != "passed"),
        "view_count": len(views),
        "views": views,
        "binding_digest": binding_digest,
        "limits": _resource_binding_limits(),
        "errors": errors,
    }
    size = len(json.dumps(report, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    if size > APP_RESOURCE_BINDING_MAX_REPORT_BYTES:
        return {
            "ok": False,
            "resource_binding": APP_BUNDLE_RESOURCE_BINDING_READONLY,
            "binding_scope": APP_BUNDLE_BINDING_SCOPE,
            "proof_source": "compiler_semantic_inventory_text",
            "assertion_count": len(assertions),
            "passed_count": 0,
            "failed_count": len(assertions),
            "view_count": len(views),
            "views": [],
            "binding_digest": binding_digest,
            "limits": _resource_binding_limits(),
            "errors": [
                {
                    "code": "APP_RESOURCE_BINDING_REPORT_TOO_LARGE",
                    "message": f"Resource binding assertion report is {size} bytes; limit is {APP_RESOURCE_BINDING_MAX_REPORT_BYTES}.",
                    "fix": "Reduce resource views, record refs, or fields.",
                }
            ],
        }
    return report


def _screen_manifest_binding_projection(screen_report: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(screen_report, dict):
        return []
    paths = screen_report.get("paths") if isinstance(screen_report.get("paths"), dict) else {}
    manifest_path = Path(str(paths.get("manifest") or ""))
    if not manifest_path.exists():
        return []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except ValueError:
        return []
    nodes = manifest.get("nodes") if isinstance(manifest.get("nodes"), dict) else {}
    projection: list[dict[str, Any]] = []
    for dom_id, entry in nodes.items():
        if not isinstance(dom_id, str) or not isinstance(entry, dict):
            continue
        props = entry.get("props") if isinstance(entry.get("props"), dict) else {}
        primitive = str(entry.get("primitive") or "")
        binding_id = props.get("binding_id")
        if not isinstance(binding_id, str) or primitive not in APP_RESOURCE_BINDING_TEXT_PRIMITIVES:
            continue
        content_refs = entry.get("content_refs") if isinstance(entry.get("content_refs"), list) else []
        text = props.get("text")
        projection.append(
            {
                "dom_id": dom_id,
                "ir_id": str(entry.get("ir_id") or ""),
                "primitive": primitive,
                "binding_id": binding_id,
                "content_refs": [item for item in content_refs if isinstance(item, str)],
                "visible_text": str(text) if isinstance(text, str) else "",
            }
        )
    return projection


def _resource_binding_ambiguous_view_values(
    records: dict[str, dict[str, Any]],
    record_ids: list[str],
    fields: list[str],
) -> set[tuple[str, str]]:
    repeated: set[tuple[str, str]] = set()
    seen: set[tuple[str, str]] = set()
    for record_id in record_ids:
        record = records.get(record_id)
        if not isinstance(record, dict):
            continue
        for field in fields:
            value_text = _resource_binding_scalar_text(record.get(field))
            key = (field, value_text)
            if key in seen:
                repeated.add(key)
            seen.add(key)
    return repeated


def _resource_binding_candidates(
    projection: list[dict[str, Any]],
    screen: dict[str, Any],
    *,
    target_motif_id: str,
    record_id: str,
    field: str,
    value_text: str,
) -> list[dict[str, Any]]:
    motif_members = _screen_motif_members(screen, target_motif_id)
    expected_ref = f"node:{record_id}#attr:{field}"
    matches: list[dict[str, Any]] = []
    for item in projection:
        binding_id = item.get("binding_id")
        if binding_id not in motif_members:
            continue
        if item.get("visible_text") != value_text:
            continue
        content_refs = item.get("content_refs") if isinstance(item.get("content_refs"), list) else []
        if expected_ref not in content_refs:
            continue
        matches.append(item)
    return matches


def _screen_motif_members(screen: dict[str, Any], motif_id: str) -> set[str]:
    intent = screen.get("intent_bundle") if isinstance(screen.get("intent_bundle"), dict) else {}
    view_spec = intent.get("view_spec") if isinstance(intent.get("view_spec"), dict) else {}
    motifs = view_spec.get("motifs") if isinstance(view_spec.get("motifs"), list) else []
    for motif in motifs:
        if isinstance(motif, dict) and motif.get("id") == motif_id and isinstance(motif.get("members"), list):
            return {item for item in motif["members"] if isinstance(item, str)}
    return set()


def _resource_binding_scalar_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if type(value) in {int, float}:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return ""


def _sha256_text(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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
        }
        if shell or report.get("shell_artifact_hash") or report.get("shell_manifest_hash")
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
        "binding_scope": shell.get("binding_scope"),
        "resource_binding_assertions": _compact_resource_binding_report(shell.get("resource_binding_assertions")),
        "errors": _normalize_proof_errors(shell.get("errors")),
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
    for key in ("proof_dir", "app", "design", "report", "proof_summary", "support_bundle"):
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
    "title": "ViewSpec Agent AppBundle V1/V2",
    "description": "Local-only multi-screen app contract with embedded IntentBundles, static routes, V1 unbound fixtures, and V2 read-only fixture binding proof.",
    "oneOf": [{"$ref": "#/$defs/app_bundle_v1"}, {"$ref": "#/$defs/app_bundle_v2"}],
    "x-viewspec-resource-binding": APP_BUNDLE_RESOURCE_BINDING,
    "x-viewspec-resource-bindings": [APP_BUNDLE_RESOURCE_BINDING, APP_BUNDLE_RESOURCE_BINDING_READONLY],
    "x-viewspec-binding-scope": APP_BUNDLE_BINDING_SCOPE,
    "x-viewspec-embedded-intent-schema": "https://viewspec.dev/agent-intent-bundle.schema.json",
    "x-viewspec-invariants": [
        "AppBundles are local-only and no-network.",
        "schema_version 1 rejects resource_binding and resource_views, and reports unbound_v0.",
        "schema_version 2 requires resource_binding fixture_readonly_v0 and per-screen resource_views.",
        "Routes are static canonical paths only and must map to declared screens.",
        "The root route must resolve to exactly one route.",
        "Every screen must be reachable by at least one static route.",
        "V2 binding proof is exact byte-for-byte fixture scalar visibility in declared target motifs only.",
        "Every embedded screen intent must validate against the local V1 IntentBundle contract.",
        "Unknown AppBundle-owned fields are rejected instead of ignored.",
        "Proof output paths are derived from validated safe ids only.",
    ],
    "x-viewspec-anti-goals": [
        "No runtime browser navigation proof.",
        "No dynamic routes, route params, query strings, hashes, redirects, guards, nested routers, or locale routing.",
        "No runtime data binding, reducers, mutations, adapters, API clients, backend, or deployable framework generation.",
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
        "safe_id": {"type": "string", "pattern": SAFE_AGENT_ID_PATTERN, "maxLength": APP_BUNDLE_MAX_ID_CHARS},
        "safe_string": {"type": "string", "maxLength": APP_BUNDLE_MAX_SCALAR_STRING_CHARS},
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
    "APP_BUNDLE_RESOURCE_BINDING",
    "APP_BUNDLE_RESOURCE_BINDING_READONLY",
    "APP_BUNDLE_RESULT_SCHEMA_VERSION",
    "APP_BUNDLE_SCHEMA_VERSION",
    "APP_BUNDLE_TARGET",
    "APP_BUNDLE_PROOF_LEVEL",
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
