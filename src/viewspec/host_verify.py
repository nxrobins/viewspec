"""Bounded host verification for generated React Tailwind artifacts."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from viewspec.intent_tools import compile_intent_bundle_file_tool
from viewspec.local_tools import (
    LocalToolError,
    atomic_write,
    check_artifact_dir,
    file_hash,
    path_policy_metadata,
    resolve_cwd,
    resolve_local_path,
    tool_error_response,
    tool_response,
)


HOST_VERIFY_SCHEMA_VERSION = 1
HOST_VERIFY_REPORT_MAX_BYTES = 64 * 1024
HOST_VERIFY_TARGET = "react-tailwind-tsx"
HOST_VERIFY_EMITTER = "react_tailwind_tsx"
HOST_VERIFY_TEMPLATE_PACKAGE = "viewspec.host_verify_template"
HOST_VERIFY_TEMPLATE_NON_LOCK_FILE_LIMIT = 12
HOST_VERIFY_TEMPLATE_NON_LOCK_BYTES_LIMIT = 40 * 1024
HOST_VERIFY_TOTAL_TIMEOUT_MS = 180_000
HOST_VERIFY_PHASE_TIMEOUTS_MS = {
    "check_copy": 10_000,
    "install": 90_000,
    "build": 60_000,
    "preview_startup": 20_000,
    "browser": 30_000,
    "cleanup": 5_000,
}
HOST_VERIFY_TEMPLATE_FILES = (
    "package.json",
    "package-lock.json",
    "index.html",
    "vite.config.ts",
    "tsconfig.json",
    "playwright.config.ts",
    "src/App.tsx",
    "src/main.tsx",
    "src/index.css",
    "tests/host-verify.spec.ts",
)
HOST_VERIFY_EXPECTED_CSS_LINES = [
    '@import "tailwindcss";',
    '@source "./generated/*.tsx";',
    "html,",
    "body,",
    "#root {",
    "  min-height: 100%;",
    "}",
    "body {",
    "  margin: 0;",
    "}",
]
HOST_VERIFY_NODE_MODULE_BINS = ("vite", "playwright")
HOST_VERIFY_CODE_RE = re.compile(r"\b(HOST_VERIFY_[A-Z0-9_]+)\b")


class HostVerifyFailure(ValueError):
    """Stable-code host verification failure."""

    def __init__(self, code: str, message: str, fix: str | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.fix = fix or "Fix the artifact or host verification environment and retry."


@dataclass(frozen=True)
class CommandResult:
    stdout: str
    stderr: str
    returncode: int


def verify_host_artifact_dir(
    artifact_dir: str | Path,
    *,
    target: str = HOST_VERIFY_TARGET,
    install: bool = False,
    report_out: str | Path | None = None,
) -> dict[str, Any]:
    """Verify an already compiled React Tailwind artifact in the bounded reference host."""
    timings: dict[str, int] = {}
    started = time.perf_counter()
    artifact_path = Path(artifact_dir).resolve()
    manifest_summary: dict[str, Any] | None = None
    try:
        if target != HOST_VERIFY_TARGET:
            raise HostVerifyFailure("HOST_VERIFY_UNSUPPORTED_TARGET", f"Unsupported host verification target: {target}")

        checked = _time_phase(timings, "check_copy", lambda: check_artifact_dir(artifact_path))
        manifest_summary = checked.get("manifest_summary") if isinstance(checked.get("manifest_summary"), dict) else None
        if not checked["ok"]:
            return _finalize_report(
                _error_report(
                    "HOST_VERIFY_ARTIFACT_CHECK_FAILED",
                    "viewspec check failed before host verification.",
                    artifact_dir=artifact_path,
                    install=install,
                    timings=timings,
                    manifest_summary=manifest_summary,
                    errors=[
                        {
                            "code": "HOST_VERIFY_ARTIFACT_CHECK_FAILED",
                            "message": item,
                            "fix": "Fix the compiled artifact and re-run viewspec check.",
                        }
                        for item in checked["errors"]
                    ],
                ),
                report_out,
            )

        manifest = _read_manifest(artifact_path)
        _assert_supported_manifest(manifest)
        source_files = _artifact_files(artifact_path)
        artifact_hash = file_hash(source_files["tsx"])
        if artifact_hash != manifest.get("artifact_hash"):
            raise HostVerifyFailure(
                "HOST_VERIFY_ARTIFACT_HASH_MISMATCH",
                "ViewSpecView.tsx hash does not match manifest artifact_hash.",
            )

        with tempfile.TemporaryDirectory(prefix="viewspec-host-verify-") as temp_name:
            host_dir = Path(temp_name).resolve() / "host"
            host_dir.mkdir(parents=True)
            _assert_workspace_safe(host_dir, artifact_path)
            _time_phase(timings, "check_copy", lambda: _copy_template(host_dir))
            _assert_template_guard(host_dir)
            copied_hash = _copy_artifact_files(source_files, host_dir)
            if copied_hash != artifact_hash:
                raise HostVerifyFailure("HOST_VERIFY_ARTIFACT_HASH_MISMATCH", "Copied artifact hash does not match source artifact hash.")
            runtime = _run_host_browser_phases(host_dir, install=install, started=started, timings=timings)
            assertions = _assert_runtime_report(runtime, manifest_summary=manifest_summary)
            report = _base_report(
                ok=True,
                artifact_dir=artifact_path,
                install=install,
                timings=timings,
                artifact_hash=artifact_hash,
                manifest_hash=file_hash(source_files["manifest"]),
                diagnostics_hash=file_hash(source_files["diagnostics"]),
                host_template_lock_hash=file_hash(host_dir / "package-lock.json"),
                node_version=runtime["node_version"],
                npm_version=runtime["npm_version"],
                assertions=assertions,
                manifest_summary=manifest_summary,
                errors=[],
            )
            return _finalize_report(report, report_out)
    except HostVerifyFailure as exc:
        return _finalize_report(
            _error_report(
                exc.code,
                exc.message,
                artifact_dir=artifact_path,
                install=install,
                timings=timings,
                manifest_summary=manifest_summary,
                fix=exc.fix,
            ),
            report_out,
        )
    except Exception as exc:
        return _finalize_report(
            _error_report(
                "HOST_VERIFY_BROWSER_RUNTIME_ERROR",
                str(exc),
                artifact_dir=artifact_path,
                install=install,
                timings=timings,
                manifest_summary=manifest_summary,
            ),
            report_out,
        )


def verify_host_intent_file(
    intent_path: str | Path,
    out_dir: str | Path,
    *,
    design_path: str | Path | None = None,
    strict_design: bool = False,
    target: str = HOST_VERIFY_TARGET,
    install: bool = False,
    report_out: str | Path | None = None,
) -> dict[str, Any]:
    """Compile an IntentBundle through the existing tool path, then host-verify the artifact."""
    if target != HOST_VERIFY_TARGET:
        return _finalize_report(
            _error_report("HOST_VERIFY_UNSUPPORTED_TARGET", f"Unsupported host verification target: {target}", install=install),
            report_out,
        )
    compiled = compile_intent_bundle_file_tool(
        intent_path,
        out_dir,
        design_path=design_path,
        strict_design=strict_design,
        target=target,
        cwd=Path.cwd(),
        allow_outside_cwd=True,
    )
    if not compiled["ok"]:
        errors = [
            {
                "code": "HOST_VERIFY_ARTIFACT_CHECK_FAILED",
                "message": error.get("message", "Compile/check failed."),
                "fix": error.get("fix", "Fix the IntentBundle or generated artifact and retry."),
            }
            for error in compiled.get("errors", [])
        ]
        return _finalize_report(
            _error_report(
                "HOST_VERIFY_ARTIFACT_CHECK_FAILED",
                "Compile mode failed before host verification.",
                artifact_dir=Path(out_dir).resolve(),
                install=install,
                manifest_summary=_manifest_summary_from_tool_result(compiled),
                errors=errors,
            ),
            report_out,
        )
    return verify_host_artifact_dir(out_dir, target=target, install=install, report_out=report_out)


def verify_host_tool(
    artifact_dir: str | Path | None = None,
    *,
    intent_path: str | Path | None = None,
    out_dir: str | Path | None = None,
    design_path: str | Path | None = None,
    strict_design: bool = False,
    target: str = HOST_VERIFY_TARGET,
    install: bool = False,
    report_out: str | Path | None = None,
    cwd: str | Path | None = None,
    allow_outside_cwd: bool = False,
) -> dict[str, Any]:
    """MCP/native tool wrapper for bounded host verification."""
    root: Path | None = None
    try:
        root = resolve_cwd(cwd)
        resolved_report = (
            resolve_local_path(report_out, cwd=root, allow_outside_cwd=allow_outside_cwd)
            if report_out is not None
            else None
        )
        if intent_path is not None or out_dir is not None:
            if intent_path is None or out_dir is None or artifact_dir is not None:
                return tool_error_response(
                    "HOST_VERIFY_UNSUPPORTED_TARGET",
                    "Pass either artifact_dir or both intent_path and out_dir for verify_host.",
                    "Use artifact mode or compile mode, not both.",
                    metadata=path_policy_metadata(root, allow_outside_cwd),
                )
            compiled = compile_intent_bundle_file_tool(
                intent_path,
                out_dir,
                design_path=design_path,
                strict_design=strict_design,
                target=target,
                cwd=root,
                allow_outside_cwd=allow_outside_cwd,
            )
            if not compiled["ok"]:
                proof = _error_report(
                    "HOST_VERIFY_ARTIFACT_CHECK_FAILED",
                    "Compile mode failed before host verification.",
                    artifact_dir=resolve_local_path(out_dir, cwd=root, allow_outside_cwd=allow_outside_cwd),
                    install=install,
                    manifest_summary=_manifest_summary_from_tool_result(compiled),
                    errors=[
                        {
                            "code": "HOST_VERIFY_ARTIFACT_CHECK_FAILED",
                            "message": error.get("message", "Compile/check failed."),
                            "fix": error.get("fix", "Fix the IntentBundle or generated artifact and retry."),
                        }
                        for error in compiled.get("errors", [])
                    ],
                )
                return _tool_from_report(proof, root, allow_outside_cwd)
            artifact = resolve_local_path(out_dir, cwd=root, allow_outside_cwd=allow_outside_cwd, must_exist=True)
        else:
            if artifact_dir is None:
                return tool_error_response(
                    "INVALID_PATH",
                    "artifact_dir is required for verify_host artifact mode.",
                    "Pass a compiled React Tailwind artifact directory.",
                    metadata=path_policy_metadata(root, allow_outside_cwd),
                )
            artifact = resolve_local_path(artifact_dir, cwd=root, allow_outside_cwd=allow_outside_cwd, must_exist=True)
        proof = verify_host_artifact_dir(artifact, target=target, install=install, report_out=resolved_report)
        return _tool_from_report(proof, root, allow_outside_cwd)
    except Exception as exc:
        if isinstance(exc, LocalToolError):
            return tool_error_response(exc.code, exc.message, exc.fix, metadata=path_policy_metadata(root, allow_outside_cwd))
        proof = _error_report(
            "HOST_VERIFY_BROWSER_RUNTIME_ERROR",
            str(exc),
            install=install,
        )
        return _tool_from_report(proof, root, allow_outside_cwd)


def _tool_from_report(proof: dict[str, Any], root: Path | None, allow_outside_cwd: bool) -> dict[str, Any]:
    errors = [
        {
            "code": str(error.get("code", "HOST_VERIFY_BROWSER_RUNTIME_ERROR")),
            "message": str(error.get("message", "Host verification failed.")),
            "fix": str(error.get("fix", "Fix the artifact or host environment and retry.")),
        }
        for error in proof.get("errors", [])
    ]
    return tool_response(
        bool(proof.get("ok")),
        "Host verification passed." if proof.get("ok") else "Host verification failed.",
        paths={"artifact_dir": str(proof.get("artifact_dir") or "")},
        errors=errors,
        next_actions=[] if proof.get("ok") else ["Fix the reported host verification issue and retry verify_host."],
        metadata={
            **path_policy_metadata(root, allow_outside_cwd),
            "network_calls": "npm_ci_opt_in" if proof.get("install_used") else "none",
            "target": proof.get("target"),
            "manifest_summary": proof.get("manifest_summary"),
            "host_verification": summarize_host_verification_report(proof),
        },
        data={"proof_report": proof},
    )


def summarize_host_verification_report(report: object) -> dict[str, Any] | None:
    if not isinstance(report, dict):
        return None
    assertions = report.get("assertions")
    normalized_assertions: dict[str, int] = {}
    if isinstance(assertions, dict):
        normalized_assertions = {
            str(key): int(value)
            for key, value in assertions.items()
            if isinstance(value, int) and not isinstance(value, bool)
        }
    errors = report.get("errors")
    error_codes: list[str] = []
    if isinstance(errors, list):
        error_codes = [
            str(error.get("code"))
            for error in errors
            if isinstance(error, dict) and error.get("code")
        ]
    return {
        "ok": bool(report.get("ok")),
        "assertions": normalized_assertions,
        "error_codes": error_codes,
    }


def _manifest_summary_from_tool_result(result: dict[str, Any]) -> dict[str, Any] | None:
    metadata = result.get("metadata")
    if not isinstance(metadata, dict):
        return None
    summary = metadata.get("manifest_summary")
    return summary if isinstance(summary, dict) else None


def _run_host_browser_phases(
    host_dir: Path,
    *,
    install: bool,
    started: float,
    timings: dict[str, int],
) -> dict[str, Any]:
    node = _require_executable("node", "HOST_VERIFY_NODE_MISSING")
    npm = _require_executable("npm", "HOST_VERIFY_NPM_MISSING")
    node_version = _run_process([node, "--version"], cwd=host_dir, timeout_ms=5_000, code="HOST_VERIFY_NODE_MISSING").stdout.strip()
    npm_version = _run_process([npm, "--version"], cwd=host_dir, timeout_ms=5_000, code="HOST_VERIFY_NPM_MISSING").stdout.strip()

    if install:
        _time_phase(
            timings,
            "install",
            lambda: _run_process(
                [npm, "ci", "--ignore-scripts"],
                cwd=host_dir,
                timeout_ms=_remaining_timeout(started, HOST_VERIFY_PHASE_TIMEOUTS_MS["install"]),
                code="HOST_VERIFY_NPM_INSTALL_FAILED",
            ),
        )
    _assert_node_modules(host_dir)
    _time_phase(
        timings,
        "build",
        lambda: _run_process(
            [npm, "run", "build"],
            cwd=host_dir,
            timeout_ms=_remaining_timeout(started, HOST_VERIFY_PHASE_TIMEOUTS_MS["build"]),
            code="HOST_VERIFY_BUILD_FAILED",
        ),
    )

    browser_report = host_dir / ".viewspec-host-verify" / "browser-report.json"
    port = _free_port()
    preview = _start_preview(host_dir, npm, port)
    try:
        _time_phase(timings, "preview_startup", lambda: _wait_for_preview(port, started))
        result = _time_phase(
            timings,
            "browser",
            lambda: _run_process(
                [npm, "run", "test", "--", "--project=chromium"],
                cwd=host_dir,
                timeout_ms=_remaining_timeout(started, HOST_VERIFY_PHASE_TIMEOUTS_MS["browser"]),
                code="HOST_VERIFY_BROWSER_RUNTIME_ERROR",
                env={
                    "VIEWSPEC_HOST_VERIFY_BASE_URL": f"http://127.0.0.1:{port}",
                    "VIEWSPEC_HOST_VERIFY_BROWSER_REPORT": str(browser_report),
                },
            ),
        )
        if result.returncode != 0:
            code = _extract_host_verify_code(result.stdout + result.stderr) or "HOST_VERIFY_BROWSER_RUNTIME_ERROR"
            raise HostVerifyFailure(code, result.stderr or result.stdout or "Playwright failed.")
        if not browser_report.exists():
            raise HostVerifyFailure("HOST_VERIFY_PROOF_REPORT_INVALID", "Browser assertion report was not written.")
        loaded = json.loads(browser_report.read_text(encoding="utf-8"))
    finally:
        _time_phase(timings, "cleanup", lambda: _kill_process_tree(preview))
    return {
        "assertions": loaded.get("assertions", {}) if isinstance(loaded, dict) else {},
        "node_version": node_version,
        "npm_version": npm_version,
    }


def _read_manifest(artifact_path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads((artifact_path / "provenance_manifest.json").read_text(encoding="utf-8"))
    except Exception as exc:
        raise HostVerifyFailure("HOST_VERIFY_ARTIFACT_CHECK_FAILED", f"Could not read provenance_manifest.json: {exc}") from exc
    if not isinstance(manifest, dict):
        raise HostVerifyFailure("HOST_VERIFY_ARTIFACT_CHECK_FAILED", "provenance_manifest.json must be an object.")
    return manifest


def _assert_supported_manifest(manifest: dict[str, Any]) -> None:
    if (
        manifest.get("kind") != "intent_bundle_compile"
        or manifest.get("emitter") != HOST_VERIFY_EMITTER
        or manifest.get("artifact_file") != "ViewSpecView.tsx"
    ):
        raise HostVerifyFailure(
            "HOST_VERIFY_UNSUPPORTED_TARGET",
            "Host verification supports only intent_bundle_compile react_tailwind_tsx ViewSpecView.tsx artifacts.",
        )


def _artifact_files(artifact_path: Path) -> dict[str, Path]:
    files = {
        "tsx": artifact_path / "ViewSpecView.tsx",
        "manifest": artifact_path / "provenance_manifest.json",
        "diagnostics": artifact_path / "diagnostics.json",
    }
    missing = [path.name for path in files.values() if not path.exists()]
    if missing:
        raise HostVerifyFailure("HOST_VERIFY_ARTIFACT_CHECK_FAILED", f"Artifact is missing required files: {', '.join(missing)}")
    return files


def _assert_workspace_safe(host_dir: Path, artifact_path: Path) -> None:
    blocked = {Path.cwd().resolve(), artifact_path.resolve(), artifact_path.resolve().parent}
    for path in blocked:
        if host_dir == path or _is_relative_to(host_dir, path):
            raise HostVerifyFailure("HOST_VERIFY_WORKSPACE_UNSAFE", f"Temporary host resolved inside unsafe path: {path}")


def _copy_template(host_dir: Path) -> None:
    root = resources.files(HOST_VERIFY_TEMPLATE_PACKAGE)
    for rel in HOST_VERIFY_TEMPLATE_FILES:
        source = root.joinpath(*rel.split("/"))
        if not source.is_file():
            raise HostVerifyFailure("HOST_VERIFY_TEMPLATE_MISSING", f"Packaged host verifier template is missing {rel}")
        target = host_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())


def _assert_template_guard(host_dir: Path) -> None:
    for rel in HOST_VERIFY_TEMPLATE_FILES:
        if not (host_dir / rel).exists():
            raise HostVerifyFailure("HOST_VERIFY_TEMPLATE_MISSING", f"Host template copy is missing {rel}")
    source_files = [host_dir / rel for rel in HOST_VERIFY_TEMPLATE_FILES if rel != "package-lock.json"]
    if len(source_files) > HOST_VERIFY_TEMPLATE_NON_LOCK_FILE_LIMIT:
        raise HostVerifyFailure("HOST_VERIFY_FIXTURE_TOO_LARGE", f"Host template has {len(source_files)} non-lock files.")
    total = sum(path.stat().st_size for path in source_files)
    if total > HOST_VERIFY_TEMPLATE_NON_LOCK_BYTES_LIMIT:
        raise HostVerifyFailure("HOST_VERIFY_FIXTURE_TOO_LARGE", f"Host template source is {total} bytes.")
    css_lines = [
        line
        for line in (host_dir / "src" / "index.css").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if css_lines != HOST_VERIFY_EXPECTED_CSS_LINES or len(css_lines) > 20:
        raise HostVerifyFailure("HOST_VERIFY_FORBIDDEN_HOST_CSS", "Host CSS must be only Tailwind import/source plus root sizing/reset.")
    app = (host_dir / "src" / "App.tsx").read_text(encoding="utf-8")
    if app.count('from "./generated/ViewSpecView"') != 1:
        raise HostVerifyFailure("HOST_VERIFY_TEMPLATE_MISSING", "Host app must import exactly ./generated/ViewSpecView.")


def _copy_artifact_files(source_files: dict[str, Path], host_dir: Path) -> str:
    generated = host_dir / "src" / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    targets = {
        "tsx": generated / "ViewSpecView.tsx",
        "manifest": generated / "provenance_manifest.json",
        "diagnostics": generated / "diagnostics.json",
    }
    for key, source in source_files.items():
        shutil.copyfile(source, targets[key])
    copied = sorted(path.name for path in generated.iterdir() if path.is_file())
    if copied != ["ViewSpecView.tsx", "diagnostics.json", "provenance_manifest.json"]:
        raise HostVerifyFailure("HOST_VERIFY_ARTIFACT_HASH_MISMATCH", "Generated host artifact directory contains unexpected files.")
    return file_hash(targets["tsx"])


def _require_executable(name: str, code: str) -> str:
    path = shutil.which(name)
    if not path:
        raise HostVerifyFailure(code, f"{name} executable is required for host verification.")
    return path


def _assert_node_modules(host_dir: Path) -> None:
    bin_dir = host_dir / "node_modules" / ".bin"
    for name in HOST_VERIFY_NODE_MODULE_BINS:
        candidates = [bin_dir / name, bin_dir / f"{name}.cmd"]
        if not any(path.exists() for path in candidates):
            raise HostVerifyFailure(
                "HOST_VERIFY_NODE_MODULES_MISSING",
                f"Missing {name} in node_modules/.bin; re-run with --install to allow npm ci --ignore-scripts.",
            )


def _run_process(
    command: list[str],
    *,
    cwd: Path,
    timeout_ms: int,
    code: str,
    env: dict[str, str] | None = None,
) -> CommandResult:
    proc_env = {**os.environ, **(env or {})}
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["preexec_fn"] = os.setsid
    proc = subprocess.Popen(
        command,
        cwd=cwd,
        env=proc_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **kwargs,
    )
    try:
        stdout, stderr = proc.communicate(timeout=max(timeout_ms / 1000, 0.1))
    except subprocess.TimeoutExpired as exc:
        _kill_process_tree(proc)
        raise HostVerifyFailure("HOST_VERIFY_TIMEOUT", f"{' '.join(command)} exceeded {timeout_ms}ms.") from exc
    result = CommandResult(stdout=stdout, stderr=stderr, returncode=proc.returncode or 0)
    if result.returncode != 0:
        extracted = _extract_host_verify_code(stdout + stderr)
        raise HostVerifyFailure(extracted or code, stderr or stdout or f"{command[0]} exited {result.returncode}.")
    return result


def _start_preview(host_dir: Path, npm: str, port: int) -> subprocess.Popen[str]:
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["preexec_fn"] = os.setsid
    return subprocess.Popen(
        [npm, "run", "preview", "--", "--host", "127.0.0.1", "--port", str(port), "--strictPort"],
        cwd=host_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **kwargs,
    )


def _wait_for_preview(port: int, started: float) -> None:
    deadline = time.perf_counter() + min(HOST_VERIFY_PHASE_TIMEOUTS_MS["preview_startup"] / 1000, _remaining_timeout(started, HOST_VERIFY_PHASE_TIMEOUTS_MS["preview_startup"]) / 1000)
    url = f"http://127.0.0.1:{port}/"
    while time.perf_counter() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status < 500:
                    return
        except OSError:
            time.sleep(0.25)
    raise HostVerifyFailure("HOST_VERIFY_TIMEOUT", "Vite preview did not become ready before timeout.")


def _kill_process_tree(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        else:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        proc.kill()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _remaining_timeout(started: float, requested_ms: int) -> int:
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    remaining = HOST_VERIFY_TOTAL_TIMEOUT_MS - elapsed_ms
    if remaining <= 0:
        raise HostVerifyFailure("HOST_VERIFY_TIMEOUT", f"Host verification exceeded {HOST_VERIFY_TOTAL_TIMEOUT_MS}ms.")
    return min(requested_ms, remaining)


def _time_phase(timings: dict[str, int], phase: str, fn: Any) -> Any:
    started = time.perf_counter()
    result = fn()
    timings[phase] = timings.get(phase, 0) + int((time.perf_counter() - started) * 1000)
    return result


def _assert_runtime_report(runtime: dict[str, Any], *, manifest_summary: dict[str, Any] | None = None) -> dict[str, int]:
    assertions = runtime.get("assertions")
    if not isinstance(assertions, dict):
        raise HostVerifyFailure("HOST_VERIFY_PROOF_REPORT_INVALID", "Browser assertion report has no assertions object.")
    normalized = {
        "dom_count": int(assertions.get("dom_count", 0)),
        "grid_column_assertion_count": int(assertions.get("grid_column_assertion_count", 0)),
        "style_assertion_count": int(assertions.get("style_assertion_count", 0)),
        "action_count": int(assertions.get("action_count", 0)),
        "aesthetic_layout_assertion_count": int(assertions.get("aesthetic_layout_assertion_count", 0)),
        "aesthetic_profile_assertion_count": int(assertions.get("aesthetic_profile_assertion_count", 0)),
        "payload_binding_count": int(assertions.get("payload_binding_count", 0)),
    }
    if normalized["dom_count"] < 1:
        raise HostVerifyFailure("HOST_VERIFY_DOM_NODE_MISSING", "Browser report did not include a DOM assertion.")
    if normalized["style_assertion_count"] < 4:
        raise HostVerifyFailure("HOST_VERIFY_STYLE_ASSERTION_TOO_WEAK", "Browser report did not include four computed style assertions.")
    if _manifest_summary_has_aesthetic_profile(manifest_summary) and normalized["aesthetic_profile_assertion_count"] < 1:
        raise HostVerifyFailure(
            "HOST_VERIFY_AESTHETIC_PROFILE_ASSERTION_MISSING",
            "Browser report did not include a runtime aesthetic profile marker assertion.",
        )
    expected_layout_count = _manifest_summary_aesthetic_layout_node_count(manifest_summary)
    if expected_layout_count > 0 and normalized["aesthetic_layout_assertion_count"] < expected_layout_count:
        raise HostVerifyFailure(
            "HOST_VERIFY_AESTHETIC_LAYOUT_ASSERTION_MISSING",
            f"Browser report included {normalized['aesthetic_layout_assertion_count']} aesthetic layout assertions for {expected_layout_count} profiled layout nodes.",
        )
    return normalized


def _manifest_summary_has_aesthetic_profile(manifest_summary: dict[str, Any] | None) -> bool:
    if not isinstance(manifest_summary, dict):
        return False
    return isinstance(manifest_summary.get("aesthetic_profile"), str) and bool(manifest_summary["aesthetic_profile"])


def _manifest_summary_aesthetic_layout_node_count(manifest_summary: dict[str, Any] | None) -> int:
    if not isinstance(manifest_summary, dict):
        return 0
    layout = manifest_summary.get("aesthetic_layout")
    if not isinstance(layout, dict):
        return 0
    count = 0
    for item in layout.values():
        if not isinstance(item, dict):
            continue
        node_count = item.get("node_count")
        if isinstance(node_count, int) and not isinstance(node_count, bool) and node_count > 0:
            count += node_count
    return count


def _base_report(
    *,
    ok: bool,
    artifact_dir: Path | None,
    install: bool,
    timings: dict[str, int],
    artifact_hash: str | None = None,
    manifest_hash: str | None = None,
    diagnostics_hash: str | None = None,
    host_template_lock_hash: str | None = None,
    node_version: str | None = None,
    npm_version: str | None = None,
    assertions: dict[str, int] | None = None,
    manifest_summary: dict[str, Any] | None = None,
    errors: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": HOST_VERIFY_SCHEMA_VERSION,
        "ok": ok,
        "target": HOST_VERIFY_TARGET,
        "artifact_dir": str(artifact_dir) if artifact_dir is not None else None,
        "artifact_hash": artifact_hash,
        "manifest_hash": manifest_hash,
        "diagnostics_hash": diagnostics_hash,
        "host_template_lock_hash": host_template_lock_hash,
        "install_used": bool(install),
        "node_version": node_version,
        "npm_version": npm_version,
        "manifest_summary": manifest_summary,
        "assertions": assertions or {
            "action_count": 0,
            "aesthetic_layout_assertion_count": 0,
            "aesthetic_profile_assertion_count": 0,
            "dom_count": 0,
            "grid_column_assertion_count": 0,
            "payload_binding_count": 0,
            "style_assertion_count": 0,
        },
        "errors": errors or [],
        "timings_ms": dict(sorted(timings.items())),
    }


def _error_report(
    code: str,
    message: str,
    *,
    artifact_dir: Path | None = None,
    install: bool,
    timings: dict[str, int] | None = None,
    fix: str | None = None,
    manifest_summary: dict[str, Any] | None = None,
    errors: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return _base_report(
        ok=False,
        artifact_dir=artifact_dir,
        install=install,
        timings=timings or {},
        manifest_summary=manifest_summary,
        errors=errors
        or [
            {
                "code": code,
                "message": message,
                "fix": fix or "Fix the artifact or host verification environment and retry.",
            }
        ],
    )


def _finalize_report(report: dict[str, Any], report_out: str | Path | None) -> dict[str, Any]:
    payload = json.dumps(report, indent=2, sort_keys=True)
    if len(payload.encode("utf-8")) > HOST_VERIFY_REPORT_MAX_BYTES:
        report = _error_report(
            "HOST_VERIFY_PROOF_REPORT_INVALID",
            f"Host verification proof report exceeds {HOST_VERIFY_REPORT_MAX_BYTES} bytes.",
            artifact_dir=Path(report["artifact_dir"]) if report.get("artifact_dir") else None,
            install=bool(report.get("install_used")),
            timings=report.get("timings_ms") if isinstance(report.get("timings_ms"), dict) else {},
        )
        payload = json.dumps(report, indent=2, sort_keys=True)
    if report_out is not None:
        atomic_write(Path(report_out), payload + "\n")
    return report


def _extract_host_verify_code(text: str) -> str | None:
    match = HOST_VERIFY_CODE_RE.search(text)
    return match.group(1) if match else None


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


__all__ = [
    "HOST_VERIFY_SCHEMA_VERSION",
    "HOST_VERIFY_TARGET",
    "HostVerifyFailure",
    "summarize_host_verification_report",
    "verify_host_artifact_dir",
    "verify_host_intent_file",
    "verify_host_tool",
]
