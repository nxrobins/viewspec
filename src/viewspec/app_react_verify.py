"""Exact-artifact verification for runnable React/Tailwind AppBundle output."""

from __future__ import annotations

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
APP_REACT_VERIFY_BUILD_TIMEOUT_SECONDS = 90
APP_REACT_VERIFY_BROWSER_TIMEOUT_SECONDS = 60
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
) -> dict[str, Any]:
    started = time.perf_counter()
    artifact_path = Path(artifact_dir).resolve()
    manifest_path = artifact_path / REACT_APP_MANIFEST
    try:
        manifest = _preflight(artifact_path, manifest_path)
        with tempfile.TemporaryDirectory(prefix="viewspec-react-app-verify-") as temp_name:
            host_dir = Path(temp_name) / "app"
            _copy_artifact(artifact_path, host_dir)
            if install:
                seed = os.environ.get("VIEWSPEC_HOST_VERIFY_NODE_MODULES_DIR")
                if seed:
                    _link_prebuilt_node_modules(host_dir, Path(seed))
                else:
                    install_result = _run_process(
                        ["npm", "ci", "--ignore-scripts"],
                        cwd=host_dir,
                        timeout=APP_REACT_VERIFY_INSTALL_TIMEOUT_SECONDS,
                    )
                    _require_success(
                        install_result,
                        "APP_REACT_VERIFY_INSTALL_FAILED",
                        "npm ci --ignore-scripts",
                    )
            else:
                source_modules = artifact_path / "node_modules"
                if not source_modules.is_dir():
                    return _error_report(
                        artifact_path,
                        "APP_REACT_VERIFY_DEPENDENCIES_MISSING",
                        "Generated app dependencies are not installed.",
                        "Run with install=True or run npm ci in the generated app before verification.",
                        install=install,
                        started=started,
                    )
                shutil.copytree(source_modules, host_dir / "node_modules", symlinks=True)

            build_result = _run_process(
                ["npm", "run", "build"],
                cwd=host_dir,
                timeout=APP_REACT_VERIFY_BUILD_TIMEOUT_SECONDS,
            )
            _require_success(build_result, "APP_REACT_VERIFY_BUILD_FAILED", "npm run build")
            browser_result = _run_process(
                ["npm", "run", "viewspec:verify"],
                cwd=host_dir,
                timeout=APP_REACT_VERIFY_BROWSER_TIMEOUT_SECONDS,
            )
            _require_success(browser_result, "APP_REACT_VERIFY_BROWSER_FAILED", "npm run viewspec:verify")
            assertions = _runtime_assertions(host_dir / "viewspec_runtime_report.json")

        app_path = artifact_path / str(manifest["entry_file"])
        return {
            "schema_version": APP_REACT_VERIFY_SCHEMA_VERSION,
            "ok": True,
            "target": REACT_APP_TARGET,
            "artifact_dir": str(artifact_path),
            "app_artifact_hash": file_hash(app_path),
            "manifest_hash": file_hash(manifest_path),
            "install": bool(install),
            "assertions": assertions,
            "policy": {
                "install_command": (
                    "prebuilt_node_modules"
                    if install and os.environ.get("VIEWSPEC_HOST_VERIFY_NODE_MODULES_DIR")
                    else "npm ci --ignore-scripts"
                    if install
                    else "none"
                ),
                "build_command": "npm run build",
                "browser_command": "npm run viewspec:verify",
            },
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "errors": [],
        }
    except ReactAppVerifyFailure as exc:
        return _error_report(
            artifact_path,
            exc.code,
            exc.message,
            exc.fix,
            install=install,
            started=started,
        )
    except Exception as exc:
        return _error_report(
            artifact_path,
            "APP_REACT_VERIFY_INTERNAL_ERROR",
            str(exc),
            "Fix the local Node/Playwright verification environment and retry.",
            install=install,
            started=started,
        )


class ReactAppVerifyFailure(ValueError):
    def __init__(self, code: str, message: str, fix: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.fix = fix


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
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not isinstance(entry.get("sha256"), str):
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
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
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
        ignore=shutil.ignore_patterns("node_modules", "dist", "test-results", "viewspec_runtime_report.json"),
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


def _error_report(
    artifact_dir: Path,
    code: str,
    message: str,
    fix: str,
    *,
    install: bool,
    started: float,
) -> dict[str, Any]:
    return {
        "schema_version": APP_REACT_VERIFY_SCHEMA_VERSION,
        "ok": False,
        "target": REACT_APP_TARGET,
        "artifact_dir": str(artifact_dir),
        "app_artifact_hash": None,
        "manifest_hash": file_hash(artifact_dir / REACT_APP_MANIFEST)
        if (artifact_dir / REACT_APP_MANIFEST).is_file()
        else None,
        "install": bool(install),
        "assertions": {},
        "policy": {
            "install_command": "npm ci --ignore-scripts" if install else "none",
            "build_command": "npm run build",
            "browser_command": "npm run viewspec:verify",
        },
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "errors": [{"code": code, "message": message, "fix": fix}],
    }


__all__ = ["verify_react_app_artifact_dir"]
