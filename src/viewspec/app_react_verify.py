"""Exact-artifact verification for runnable React/Tailwind AppBundle output."""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from viewspec.app_react import REACT_APP_MANIFEST, REACT_APP_TARGET
from viewspec.local_tools import file_hash
from viewspec.node_runtime import materialize_prebuilt_node_modules


APP_REACT_VERIFY_SCHEMA_VERSION = 1
APP_REACT_VERIFY_INSTALL_TIMEOUT_SECONDS = 120
APP_REACT_VERIFY_TYPECHECK_TIMEOUT_SECONDS = 90
APP_REACT_VERIFY_BUILD_TIMEOUT_SECONDS = 90
APP_REACT_VERIFY_BROWSER_TIMEOUT_SECONDS = 90
APP_REACT_VERIFY_ASSERTION_KEYS = (
    "route_count",
    "history_assertion_count",
    "unknown_route_assertion_count",
    "state_action_count",
    "rebound_binding_count",
    "selector_assertion_count",
    "visibility_assertion_count",
)


def verify_react_app_artifact_dir(
    artifact_dir: str | Path,
    *,
    install: bool = False,
    freerange: bool = False,
    pretext: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    timings: dict[str, int] = {}
    typecheck_status = "not_completed"
    static_analysis: dict[str, Any] | None = None
    text_layout: dict[str, Any] | None = None
    pretext_installation: dict[str, Any] | None = None
    assertions: dict[str, int] = {}
    verified_app_artifact_hash: str | None = None
    verified_manifest_hash: str | None = None
    phases = {
        "artifact_integrity": "not_completed",
        "typecheck": "not_completed",
        "freerange": "not_completed" if freerange else "not_requested",
        "build": "not_completed",
        "browser": "not_completed",
        **({"pretext": "not_completed"} if pretext else {}),
        "final_integrity": "not_completed",
    }
    artifact_path = Path(artifact_dir).resolve()
    manifest_path = artifact_path / REACT_APP_MANIFEST
    try:
        phases["artifact_integrity"] = "running"
        manifest = _time_phase(timings, "artifact_manifest", lambda: _preflight(artifact_path, manifest_path))
        entry_path = _manifest_file(artifact_path, str(manifest.get("entry_file", "")))
        verified_app_artifact_hash = file_hash(entry_path)
        verified_manifest_hash = file_hash(manifest_path)
        with tempfile.TemporaryDirectory(prefix="viewspec-react-app-verify-") as temp_name:
            host_dir = Path(temp_name) / "app"
            _time_phase(timings, "snapshot_copy", lambda: _copy_artifact(artifact_path, host_dir))
            snapshot_manifest = _time_phase(
                timings,
                "snapshot_manifest",
                lambda: _preflight(host_dir, host_dir / REACT_APP_MANIFEST),
            )
            if snapshot_manifest != manifest:
                raise ReactAppVerifyFailure(
                    "APP_REACT_VERIFY_HASH_MISMATCH",
                    "Generated app manifest changed while the private verification snapshot was created.",
                    "Stop concurrent edits, regenerate the AppBundle, and retry.",
                )
            phases["artifact_integrity"] = "passed"
            if install:
                seed = os.environ.get("VIEWSPEC_HOST_VERIFY_NODE_MODULES_DIR")
                if seed:
                    _link_prebuilt_node_modules(host_dir, Path(seed))
                else:
                    install_result = _time_phase(
                        timings,
                        "install",
                        lambda: _run_process(
                            ["npm", "ci", "--ignore-scripts"],
                            cwd=host_dir,
                            timeout=APP_REACT_VERIFY_INSTALL_TIMEOUT_SECONDS,
                        ),
                    )
                    _require_success(
                        install_result,
                        "APP_REACT_VERIFY_INSTALL_FAILED",
                        "npm ci --ignore-scripts",
                    )
            else:
                source_modules = artifact_path / "node_modules"
                if not source_modules.is_dir():
                    raise ReactAppVerifyFailure(
                        "APP_REACT_VERIFY_DEPENDENCIES_MISSING",
                        "Generated app dependencies are not installed.",
                        "Run with install=True or run npm ci in the generated app before verification.",
                    )
                shutil.copytree(source_modules, host_dir / "node_modules", symlinks=True)

            pretext_scope = _pretext_scope(manifest) if pretext else None
            if pretext and pretext_scope and pretext_scope.get("status") == "applicable":
                pretext_installation = _time_phase(
                    timings,
                    "pretext_installation",
                    lambda: _validate_pretext_installation(host_dir),
                )

            typecheck_status = "running"
            phases["typecheck"] = "running"
            typecheck_result = _time_phase(
                timings,
                "typecheck",
                lambda: _run_process(
                    ["npm", "run", "typecheck"],
                    cwd=host_dir,
                    timeout=APP_REACT_VERIFY_TYPECHECK_TIMEOUT_SECONDS,
                ),
            )
            _require_success(
                typecheck_result,
                "APP_REACT_VERIFY_TYPECHECK_FAILED",
                "npm run typecheck",
            )
            typecheck_status = "passed"
            phases["typecheck"] = "passed"

            if freerange:
                phases["freerange"] = "running"
                static_analysis = _time_phase(
                    timings,
                    "freerange",
                    lambda: _run_freerange(host_dir, manifest),
                )
                phases["freerange"] = str(static_analysis.get("status", "passed"))

            phases["build"] = "running"
            build_result = _time_phase(
                timings,
                "build",
                lambda: _run_process(
                    ["npm", "run", "build"],
                    cwd=host_dir,
                    timeout=APP_REACT_VERIFY_BUILD_TIMEOUT_SECONDS,
                ),
            )
            _require_success(build_result, "APP_REACT_VERIFY_BUILD_FAILED", "npm run build")
            phases["build"] = "passed"
            phases["browser"] = "running"
            browser_result = _time_phase(
                timings,
                "browser",
                lambda: _run_process(
                    ["npm", "run", "viewspec:verify"],
                    cwd=host_dir,
                    timeout=APP_REACT_VERIFY_BROWSER_TIMEOUT_SECONDS,
                ),
            )
            _require_success(browser_result, "APP_REACT_VERIFY_BROWSER_FAILED", "npm run viewspec:verify")
            assertions = _runtime_assertions(host_dir / "viewspec_runtime_report.json")
            phases["browser"] = "passed"
            if pretext:
                phases["pretext"] = "running"
                if pretext_scope and pretext_scope.get("status") == "applicable":
                    pretext_result = _time_phase(
                        timings,
                        "pretext_browser",
                        lambda: _run_process(
                            ["npm", "run", "viewspec:verify-pretext"],
                            cwd=host_dir,
                            timeout=APP_REACT_VERIFY_BROWSER_TIMEOUT_SECONDS,
                        ),
                    )
                    _require_success(
                        pretext_result,
                        "APP_PRETEXT_EXECUTION_FAILED",
                        "npm run viewspec:verify-pretext",
                    )
                text_layout = _time_phase(
                    timings,
                    "pretext",
                    lambda: _run_pretext_report(
                        host_dir,
                        pretext_scope,
                        installation=pretext_installation,
                    ),
                )
                phases["pretext"] = str(text_layout.get("status", "passed"))
            phases["final_integrity"] = "running"
            if freerange:
                _time_phase(
                    timings,
                    "post_freerange_integrity",
                    lambda: _assert_numeric_scope_unchanged(
                        host_dir,
                        manifest,
                        static_analysis=static_analysis,
                    ),
                )
            if pretext and pretext_scope and pretext_scope.get("status") == "applicable":
                final_pretext_installation = _time_phase(
                    timings,
                    "post_pretext_integrity",
                    lambda: _validate_pretext_installation(host_dir),
                )
                if final_pretext_installation != pretext_installation:
                    raise ReactAppVerifyFailure(
                        "APP_PRETEXT_SOURCE_CHANGED",
                        "The installed Pretext package changed during browser verification.",
                        "Run the proof against an immutable generated AppBundle snapshot.",
                        static_analysis=static_analysis,
                        text_layout=_invalidated_text_layout(
                            text_layout,
                            code="APP_PRETEXT_SOURCE_CHANGED",
                            message="The installed Pretext package changed during browser verification.",
                            fix="Run the proof against an immutable generated AppBundle snapshot.",
                        ),
                    )
            final_manifest = _time_phase(
                timings,
                "final_manifest",
                lambda: _preflight(host_dir, host_dir / REACT_APP_MANIFEST),
            )
            if final_manifest != manifest:
                raise ReactAppVerifyFailure(
                    "APP_REACT_VERIFY_HASH_MISMATCH",
                    "Generated source changed during composite verification.",
                    "Run the proof against an immutable generated AppBundle snapshot.",
                )
            final_artifact_manifest = _time_phase(
                timings,
                "final_artifact_manifest",
                lambda: _preflight(artifact_path, manifest_path),
            )
            if (
                final_artifact_manifest != manifest
                or file_hash(manifest_path) != verified_manifest_hash
                or file_hash(entry_path) != verified_app_artifact_hash
            ):
                raise ReactAppVerifyFailure(
                    "APP_REACT_VERIFY_HASH_MISMATCH",
                    "Generated output changed while its private verification snapshot was running.",
                    "Stop concurrent edits, regenerate the AppBundle, and retry.",
                    static_analysis=static_analysis,
                )
            phases["final_integrity"] = "passed"

        return {
            "schema_version": APP_REACT_VERIFY_SCHEMA_VERSION,
            "ok": True,
            "target": REACT_APP_TARGET,
            "artifact_dir": str(artifact_path),
            "app_artifact_hash": verified_app_artifact_hash,
            "manifest_hash": verified_manifest_hash,
            "install": bool(install),
            "typecheck": {"status": "passed", "command": "npm run typecheck"},
            **({"static_analysis": static_analysis} if static_analysis is not None else {}),
            **({"text_layout": text_layout} if text_layout is not None else {}),
            **(
                {"analyses": _analysis_reports(static_analysis, text_layout)}
                if static_analysis is not None or text_layout is not None
                else {}
            ),
            "assertions": assertions,
            "phases": dict(phases),
            "policy": {
                "install_command": (
                    "prebuilt_node_modules"
                    if install and os.environ.get("VIEWSPEC_HOST_VERIFY_NODE_MODULES_DIR")
                    else "npm ci --ignore-scripts"
                    if install
                    else "none"
                ),
                "typecheck_command": "npm run typecheck",
                "freerange": "requested" if freerange else "not_requested",
                **({"pretext": "requested"} if pretext else {}),
                "build_command": "npm run build",
                "browser_command": "npm run viewspec:verify",
                **({"pretext_command": "npm run viewspec:verify-pretext"} if pretext else {}),
            },
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "timings_ms": _final_timings(timings),
            "errors": [],
        }
    except ReactAppVerifyFailure as exc:
        return _error_report(
            artifact_path,
            exc.code,
            exc.message,
            exc.fix,
            install=install,
            freerange=freerange,
            pretext=pretext,
            started=started,
            timings=timings,
            typecheck_status=typecheck_status,
            static_analysis=exc.static_analysis if exc.static_analysis is not None else static_analysis,
            text_layout=exc.text_layout if exc.text_layout is not None else text_layout,
            assertions=assertions,
            phases=phases,
            verified_app_artifact_hash=verified_app_artifact_hash,
            verified_manifest_hash=verified_manifest_hash,
        )
    except Exception as exc:
        return _error_report(
            artifact_path,
            "APP_REACT_VERIFY_INTERNAL_ERROR",
            str(exc),
            "Fix the local Node/Playwright verification environment and retry.",
            install=install,
            freerange=freerange,
            pretext=pretext,
            started=started,
            timings=timings,
            typecheck_status=typecheck_status,
            static_analysis=static_analysis,
            text_layout=text_layout,
            assertions=assertions,
            phases=phases,
            verified_app_artifact_hash=verified_app_artifact_hash,
            verified_manifest_hash=verified_manifest_hash,
        )


class ReactAppVerifyFailure(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        fix: str,
        *,
        static_analysis: dict[str, Any] | None = None,
        text_layout: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.fix = fix
        self.static_analysis = static_analysis
        self.text_layout = text_layout


def _link_prebuilt_node_modules(host_dir: Path, configured: Path) -> None:
    if not configured.is_absolute():
        raise ReactAppVerifyFailure(
            "APP_REACT_VERIFY_DEPENDENCIES_MISSING",
            "VIEWSPEC_HOST_VERIFY_NODE_MODULES_DIR must be an absolute path.",
            "Configure an absolute prebuilt node_modules path and retry.",
        )
    seed = configured.resolve()
    if not seed.is_dir():
        raise ReactAppVerifyFailure(
            "APP_REACT_VERIFY_DEPENDENCIES_MISSING",
            "Configured prebuilt node_modules directory does not exist.",
            "Build the configured dependency bundle and retry.",
        )
    destination = host_dir / "node_modules"
    if destination.exists() or destination.is_symlink():
        raise ReactAppVerifyFailure(
            "APP_REACT_VERIFY_DEPENDENCIES_MISSING",
            "Host node_modules destination must be empty before linking dependencies.",
            "Remove the existing host dependency directory and retry.",
        )
    materialize_prebuilt_node_modules(destination, seed)


def _preflight(artifact_dir: Path, manifest_path: Path) -> dict[str, Any]:
    if not artifact_dir.is_dir() or not manifest_path.is_file():
        raise ReactAppVerifyFailure(
            "APP_REACT_VERIFY_MANIFEST_MISSING",
            f"{REACT_APP_MANIFEST} is missing from the generated app.",
            "Compile the AppBundle with --target react-tailwind-app and retry.",
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReactAppVerifyFailure(
            "APP_REACT_VERIFY_MANIFEST_INVALID",
            f"Could not read the generated app manifest: {exc}",
            "Regenerate the React app target and retry.",
        ) from exc
    if not isinstance(manifest, dict) or manifest.get("target") != REACT_APP_TARGET:
        raise ReactAppVerifyFailure(
            "APP_REACT_VERIFY_TARGET_MISMATCH",
            "Generated app manifest does not declare react-tailwind-app.",
            "Pass an exact react-tailwind-app output directory.",
        )
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ReactAppVerifyFailure(
            "APP_REACT_VERIFY_MANIFEST_INVALID",
            "Generated app manifest has no checked file inventory.",
            "Regenerate the React app target and retry.",
        )
    for entry in files:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("path"), str)
            or not isinstance(entry.get("sha256"), str)
        ):
            raise ReactAppVerifyFailure(
                "APP_REACT_VERIFY_MANIFEST_INVALID",
                "Generated app manifest contains an invalid file inventory entry.",
                "Regenerate the React app target and retry.",
            )
        path = _manifest_file(artifact_dir, entry["path"])
        if not path.is_file() or file_hash(path) != entry["sha256"]:
            raise ReactAppVerifyFailure(
                "APP_REACT_VERIFY_HASH_MISMATCH",
                f"Generated file hash does not match for {entry['path']}.",
                "Discard local generated-source edits, regenerate from viewspec.app.json, and retry.",
            )
    for screen in manifest.get("screen_artifacts", []):
        if not isinstance(screen, dict):
            continue
        for path_key, hash_key in (("tsx", "artifact_hash"), ("manifest", "manifest_hash")):
            relative = screen.get(path_key)
            expected = screen.get(hash_key)
            if not isinstance(relative, str) or not isinstance(expected, str):
                raise ReactAppVerifyFailure(
                    "APP_REACT_VERIFY_MANIFEST_INVALID",
                    f"Screen artifact inventory is incomplete for {screen.get('id')}.",
                    "Regenerate the React app target and retry.",
                )
            path = _manifest_file(artifact_dir, relative)
            if not path.is_file() or file_hash(path) != expected:
                raise ReactAppVerifyFailure(
                    "APP_REACT_VERIFY_HASH_MISMATCH",
                    f"Generated screen hash does not match for {relative}.",
                    "Discard local generated-source edits, regenerate from viewspec.app.json, and retry.",
                )
    return manifest


def _manifest_file(root: Path, relative: str) -> Path:
    resolved_root = root.resolve()
    candidate = (resolved_root / relative).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ReactAppVerifyFailure(
            "APP_REACT_VERIFY_MANIFEST_INVALID",
            f"Generated manifest path escapes the app directory: {relative}.",
            "Regenerate the React app target and retry.",
        ) from exc
    return candidate


def _copy_artifact(source: Path, destination: Path) -> None:
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns(
            "node_modules",
            "dist",
            "test-results",
            "viewspec_runtime_report.json",
            "viewspec_pretext_report.json",
        ),
    )


def _run_process(command: list[str], *, cwd: Path, timeout: int) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ReactAppVerifyFailure(
            "APP_REACT_VERIFY_RUNTIME_MISSING",
            f"Could not execute {command[0]}.",
            "Install Node.js 18+ and npm, then retry.",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ReactAppVerifyFailure(
            "APP_REACT_VERIFY_TIMEOUT",
            f"Command timed out after {timeout}s: {' '.join(command)}.",
            "Fix the local package or browser environment and retry.",
        ) from exc
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout[-8000:],
        "stderr": completed.stderr[-8000:],
    }


def _require_success(result: dict[str, Any], code: str, command: str) -> None:
    if result.get("returncode") == 0:
        return
    detail = str(result.get("stderr") or result.get("stdout") or "unknown command failure")
    raise ReactAppVerifyFailure(
        code,
        f"{command} failed: {detail}",
        "Fix the generated app or local Node/Playwright environment and retry.",
    )


def _runtime_assertions(path: Path) -> dict[str, int]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReactAppVerifyFailure(
            "APP_REACT_VERIFY_REPORT_MISSING",
            f"Browser verification did not write a valid runtime report: {exc}",
            "Inspect the generated Playwright test and retry verification.",
        ) from exc
    assertions: dict[str, int] = {}
    for key in APP_REACT_VERIFY_ASSERTION_KEYS:
        value = report.get(key) if isinstance(report, dict) else None
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ReactAppVerifyFailure(
                "APP_REACT_VERIFY_REPORT_INVALID",
                f"Runtime report has invalid {key}.",
                "Regenerate the React app target and rerun browser verification.",
            )
        assertions[key] = value
    if assertions["route_count"] < 1 or assertions["unknown_route_assertion_count"] != 1:
        raise ReactAppVerifyFailure(
            "APP_REACT_VERIFY_ROUTE_ASSERTION_MISSING",
            "Runtime report did not prove at least one route and exactly one unknown-route fallback.",
            "Regenerate the React app target and rerun browser verification.",
        )
    return assertions


def _run_freerange(host_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    from viewspec.app_freerange import FreerangeFailure, analyze_freerange_numeric_scope

    scope = manifest.get("numeric_analysis")
    if not isinstance(scope, dict):
        raise ReactAppVerifyFailure(
            "APP_FREERANGE_SCOPE_INVALID",
            "Generated app manifest has no valid numeric_analysis scope.",
            "Regenerate the React AppBundle with this ViewSpec version and retry.",
        )
    try:
        return analyze_freerange_numeric_scope(host_dir, scope)
    except FreerangeFailure as exc:
        raise ReactAppVerifyFailure(
            exc.code,
            exc.message,
            exc.fix,
            static_analysis=exc.report,
        ) from exc


def _pretext_scope(manifest: dict[str, Any]) -> dict[str, Any]:
    from viewspec.app_pretext import (
        PRETEXT_NPM_INTEGRITY,
        PRETEXT_NPM_RESOLVED,
        PRETEXT_PACKAGE,
        PRETEXT_PACKAGE_TREE,
        PRETEXT_VERSION,
        PretextFailure,
        validate_pretext_scope,
    )
    from viewspec.app_pretext_runtime import PRETEXT_RUNTIME_PATH

    scope = manifest.get("text_layout_analysis")
    if not isinstance(scope, dict):
        raise ReactAppVerifyFailure(
            "APP_PRETEXT_SCOPE_INVALID",
            "Generated app manifest has no valid text_layout_analysis scope.",
            "Regenerate the React AppBundle with --pretext and retry.",
        )
    try:
        scope = validate_pretext_scope(scope)
    except PretextFailure as exc:
        raise ReactAppVerifyFailure(
            exc.code,
            exc.message,
            exc.fix,
            text_layout=exc.report,
        ) from exc
    if scope.get("status") == "applicable":
        inventory = manifest.get("files") if isinstance(manifest.get("files"), list) else []
        runtime_hash = next(
            (
                item.get("sha256")
                for item in inventory
                if isinstance(item, dict) and item.get("path") == PRETEXT_RUNTIME_PATH
            ),
            None,
        )
        expected_engine = {
            "package": PRETEXT_PACKAGE,
            "version": PRETEXT_VERSION,
            "resolved": PRETEXT_NPM_RESOLVED,
            "integrity": PRETEXT_NPM_INTEGRITY,
            "package_tree": dict(PRETEXT_PACKAGE_TREE),
            "license": "MIT",
            "font_family": "Arial",
            "runtime_path": PRETEXT_RUNTIME_PATH,
            "runtime_sha256": runtime_hash,
        }
        if not isinstance(runtime_hash, str) or manifest.get("text_layout_engine") != expected_engine:
            raise ReactAppVerifyFailure(
                "APP_PRETEXT_SCOPE_INVALID",
                "Generated app manifest has invalid Pretext engine or runtime identity evidence.",
                "Regenerate the React AppBundle with --pretext and retry.",
            )
    elif "text_layout_engine" in manifest:
        raise ReactAppVerifyFailure(
            "APP_PRETEXT_SCOPE_INVALID",
            "A not-applicable Pretext scope cannot declare an installed runtime engine.",
            "Regenerate the React AppBundle with --pretext and retry.",
        )
    return scope


def _validate_pretext_installation(host_dir: Path) -> dict[str, Any]:
    from viewspec.app_pretext import PretextFailure, validate_pretext_installation

    try:
        return validate_pretext_installation(host_dir)
    except PretextFailure as exc:
        raise ReactAppVerifyFailure(
            exc.code,
            exc.message,
            exc.fix,
            text_layout=exc.report,
        ) from exc


def _run_pretext_report(
    host_dir: Path,
    scope: dict[str, Any] | None,
    *,
    installation: dict[str, Any] | None,
) -> dict[str, Any]:
    from viewspec.app_pretext import (
        PRETEXT_NPM_INTEGRITY,
        PRETEXT_PACKAGE,
        PRETEXT_PACKAGE_TREE,
        PRETEXT_PROFILE,
        PRETEXT_PROTOCOL,
        PRETEXT_VERSION,
        PretextFailure,
        validate_pretext_runtime_report,
    )

    if not isinstance(scope, dict):
        raise ReactAppVerifyFailure(
            "APP_PRETEXT_SCOPE_INVALID",
            "Generated app manifest has no valid Pretext scope.",
            "Regenerate the React AppBundle with --pretext and retry.",
        )
    if scope.get("status") == "not_applicable":
        return {
            "status": "not_applicable",
            "engine": {
                "name": "pretext",
                "package": PRETEXT_PACKAGE,
                "version": PRETEXT_VERSION,
                "integrity": PRETEXT_NPM_INTEGRITY,
                "package_tree_sha256": PRETEXT_PACKAGE_TREE["sha256"],
            },
            "profile": PRETEXT_PROFILE,
            "protocol": PRETEXT_PROTOCOL,
            "coverage": {"required": 0, "accounted": 0, "measured": 0, "hidden": 0, "unsupported": 0, "failed": 0},
            "cache": {"prepare_calls": 0, "unique_inputs": 0, "layout_calls": 0, "cache_hits": 0},
            "reason": scope.get("reason", "no compiler-owned text surfaces"),
            "errors": [],
        }
    try:
        report = validate_pretext_runtime_report(host_dir / "viewspec_pretext_report.json", scope)
    except PretextFailure as exc:
        raise ReactAppVerifyFailure(
            exc.code,
            exc.message,
            exc.fix,
            text_layout=exc.report,
        ) from exc
    package = (
        installation.get("package")
        if isinstance(installation, dict) and isinstance(installation.get("package"), dict)
        else {}
    )
    engine = report.get("engine") if isinstance(report.get("engine"), dict) else {}
    report["engine"] = {
        **engine,
        "integrity": package.get("integrity", PRETEXT_NPM_INTEGRITY),
        "package_tree_sha256": package.get("tree_sha256", PRETEXT_PACKAGE_TREE["sha256"]),
    }
    report["installation"] = installation
    report["font_family"] = "Arial"
    if isinstance(report.get("scope_sha256"), str):
        report["scope_digest"] = report["scope_sha256"]
    return report


def _assert_numeric_scope_unchanged(
    host_dir: Path,
    manifest: dict[str, Any],
    *,
    static_analysis: dict[str, Any] | None,
) -> None:
    scope = manifest.get("numeric_analysis")
    if not isinstance(scope, dict):
        raise ReactAppVerifyFailure(
            "APP_FREERANGE_SCOPE_INVALID",
            "Generated app manifest has no valid numeric_analysis scope.",
            "Regenerate the React AppBundle and retry.",
            static_analysis=static_analysis,
        )
    for group in ("files", "call_sites"):
        entries = scope.get(group)
        if not isinstance(entries, list):
            raise ReactAppVerifyFailure(
                "APP_FREERANGE_SCOPE_INVALID",
                f"numeric_analysis.{group} is not a checked array.",
                "Regenerate the React AppBundle and retry.",
                static_analysis=static_analysis,
            )
        for entry in entries:
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("path"), str)
                or not isinstance(entry.get("sha256"), str)
            ):
                raise ReactAppVerifyFailure(
                    "APP_FREERANGE_SCOPE_INVALID",
                    f"numeric_analysis.{group} contains invalid hash evidence.",
                    "Regenerate the React AppBundle and retry.",
                    static_analysis=static_analysis,
                )
            path = _manifest_file(host_dir, entry["path"])
            if not path.is_file() or file_hash(path) != entry["sha256"]:
                message = f"Freerange proof input changed after analysis: {entry['path']}."
                fix = "Run the proof against an immutable generated AppBundle snapshot."
                raise ReactAppVerifyFailure(
                    "APP_FREERANGE_SOURCE_CHANGED",
                    message,
                    fix,
                    static_analysis=_invalidated_static_analysis(
                        static_analysis,
                        code="APP_FREERANGE_SOURCE_CHANGED",
                        message=message,
                        fix=fix,
                    ),
                )


def _invalidated_static_analysis(
    report: dict[str, Any] | None,
    *,
    code: str,
    message: str,
    fix: str,
) -> dict[str, Any] | None:
    if not isinstance(report, dict):
        return report
    invalidated = copy.deepcopy(report)
    invalidated["status"] = "failed"
    invalidated["validity"] = "invalidated"
    errors = invalidated.get("errors") if isinstance(invalidated.get("errors"), list) else []
    invalidated["errors"] = [*errors, {"code": code, "message": message, "fix": fix}]
    return invalidated


def _invalidated_text_layout(
    report: dict[str, Any] | None,
    *,
    code: str,
    message: str,
    fix: str,
) -> dict[str, Any] | None:
    if not isinstance(report, dict):
        return report
    invalidated = copy.deepcopy(report)
    invalidated["status"] = "failed"
    invalidated["validity"] = "invalidated"
    errors = invalidated.get("errors") if isinstance(invalidated.get("errors"), list) else []
    invalidated["errors"] = [*errors, {"code": code, "message": message, "fix": fix}]
    return invalidated


def _analysis_reports(
    static_analysis: dict[str, Any] | None,
    text_layout: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    reports: dict[str, dict[str, Any]] = {}
    if isinstance(static_analysis, dict):
        reports["freerange"] = static_analysis
    if isinstance(text_layout, dict):
        reports["pretext"] = text_layout
    return reports


def _time_phase(timings: dict[str, int], name: str, operation: Any) -> Any:
    started = time.perf_counter()
    try:
        return operation()
    finally:
        timings[name] = int((time.perf_counter() - started) * 1000)


def _final_timings(timings: dict[str, int]) -> dict[str, int]:
    result = dict(sorted(timings.items()))
    result["total"] = sum(value for value in result.values() if isinstance(value, int))
    return result


def _error_report(
    artifact_dir: Path,
    code: str,
    message: str,
    fix: str,
    *,
    install: bool,
    freerange: bool,
    pretext: bool,
    started: float,
    timings: dict[str, int],
    typecheck_status: str = "not_completed",
    static_analysis: dict[str, Any] | None = None,
    text_layout: dict[str, Any] | None = None,
    assertions: dict[str, int] | None = None,
    phases: dict[str, str] | None = None,
    verified_app_artifact_hash: str | None = None,
    verified_manifest_hash: str | None = None,
) -> dict[str, Any]:
    reported_phases = {key: "failed" if value == "running" else value for key, value in (phases or {}).items()}
    reported_typecheck_status = (
        "failed" if code == "APP_REACT_VERIFY_TYPECHECK_FAILED" or typecheck_status == "running" else typecheck_status
    )
    return {
        "schema_version": APP_REACT_VERIFY_SCHEMA_VERSION,
        "ok": False,
        "target": REACT_APP_TARGET,
        "artifact_dir": str(artifact_dir),
        "app_artifact_hash": verified_app_artifact_hash,
        "manifest_hash": verified_manifest_hash,
        "install": bool(install),
        "typecheck": {
            "status": reported_typecheck_status,
            "command": "npm run typecheck",
        },
        **({"static_analysis": static_analysis} if static_analysis is not None else {}),
        **({"text_layout": text_layout} if text_layout is not None else {}),
        **(
            {"analyses": _analysis_reports(static_analysis, text_layout)}
            if static_analysis is not None or text_layout is not None
            else {}
        ),
        "assertions": dict(assertions or {}),
        "phases": reported_phases,
        "policy": {
            "install_command": (
                "prebuilt_node_modules"
                if install and os.environ.get("VIEWSPEC_HOST_VERIFY_NODE_MODULES_DIR")
                else "npm ci --ignore-scripts"
                if install
                else "none"
            ),
            "typecheck_command": "npm run typecheck",
            "freerange": "requested" if freerange else "not_requested",
            **({"pretext": "requested"} if pretext else {}),
            "build_command": "npm run build",
            "browser_command": "npm run viewspec:verify",
            **({"pretext_command": "npm run viewspec:verify-pretext"} if pretext else {}),
        },
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "timings_ms": _final_timings(timings),
        "errors": [{"code": code, "message": message, "fix": fix}],
    }


__all__ = ["verify_react_app_artifact_dir"]
