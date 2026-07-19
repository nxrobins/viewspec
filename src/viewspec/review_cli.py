"""Public CLI adapters for local ViewSpec Review V0."""

from __future__ import annotations

import http.client
import hashlib
import hmac
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import time
from typing import Any

from viewspec._version import __version__
from viewspec.review_contract import ReviewContractError, canonical_json_bytes
from viewspec.review_runtime import ReviewRuntime, default_review_state_root, review_session_dir


DAEMON_START_TIMEOUT_SECONDS = 190
DEFAULT_REVIEW_PORT = 4388
MAX_SERVER_INFO_BYTES = 16 * 1024
MAX_CONTROL_RESPONSE_BYTES = 256 * 1024


def open_review(
    source: str | Path,
    *,
    design: str | Path | None = None,
    target: str | None = None,
    port: int = DEFAULT_REVIEW_PORT,
    state_root: str | Path | None = None,
    convergence_state_root: str | Path | None = None,
    reopen: bool = False,
    no_open: bool = False,
    verify: bool = False,
    install: bool = False,
) -> dict[str, object]:
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ReviewContractError(
            "REVIEW_PORT_UNAVAILABLE",
            "Review port must be an integer from 1024 through 65535.",
            "Use the default port 4388 or one explicit unprivileged local port.",
            cli_exit=2,
        )
    root = Path(state_root) if state_root is not None else default_review_state_root()
    expected_convergence_root = (
        str(Path(os.path.abspath(Path(convergence_state_root).expanduser())))
        if convergence_state_root is not None
        else None
    )
    from viewspec.verification import VerificationPlan

    expected_plan = VerificationPlan.default().plan_sha256 if verify else None
    info = _active_server_info(source, root)
    if info is not None:
        runtime = ReviewRuntime.resume(source, state_root=root)
        expected_target = target or ("html-tailwind" if runtime.configuration.source_kind == "intent_bundle" else "html-tailwind-app")
        expected_design = str(Path(os.path.abspath(Path(design).expanduser()))) if design is not None else None
        if (
            runtime.configuration.target != expected_target
            or runtime.configuration.design_path != expected_design
            or runtime.configuration.requested_port != port
            or runtime.configuration.verification_plan_sha256 != expected_plan
            or runtime.configuration.allow_install != (install if expected_target == "react-tailwind-app" else False)
            or runtime.configuration.convergence_state_root != expected_convergence_root
        ):
            raise ReviewContractError(
                "REVIEW_SESSION_CONFIGURATION_CONFLICT",
                "Active Review server configuration does not match this invocation.",
                "Use the exact original design, target, and requested port or a new state directory.",
                http_status=409,
            )
        if runtime.session.ended_by is not None:
            _agent_request(info, "POST", "/internal/v1/reopen", {"allow_human": reopen})
            review = {**runtime.status(), "status": "active", "ended_by": None}
        else:
            review = runtime.status()
        refreshed = _agent_request(info, "POST", "/internal/v1/bootstrap", {})
        bootstrap_url = refreshed.get("bootstrap_url")
        actual_port = info["port"]
    else:
        started = _spawn_daemon(
            source,
            state_root=root,
            design=design,
            target=target,
            port=port,
            reopen=reopen,
            verify=verify,
            install=install,
            convergence_state_root=convergence_state_root,
        )
        bootstrap_url = started.get("bootstrap_url")
        review = started.get("review")
        actual_port = started.get("port")
    if not isinstance(bootstrap_url, str) or not isinstance(review, dict):
        raise ReviewContractError(
            "REVIEW_SERVER_START_FAILED",
            "Review daemon did not return a complete readiness result.",
            "Restart the local review session.",
            cli_exit=1,
        )
    if not no_open:
        _open_browser(bootstrap_url)
        _wait_for_browser_handshake(source, root)
    return _tool_envelope(
        "ViewSpec review is ready.",
        review={**review, "url": bootstrap_url, "port": actual_port},
        next_actions=[
            "Open the local review URL and inspect the compiled interface.",
            f"Run viewspec review-poll {Path(source).name} --json to wait for feedback.",
        ],
    )


def poll_review(
    source: str | Path,
    *,
    ack: str | None = None,
    agent_reply: str | None = None,
    timeout_ms: int = 55_000,
    state_root: str | Path | None = None,
) -> dict[str, object]:
    root = Path(state_root) if state_root is not None else default_review_state_root()
    info = _require_active_server(source, root)
    return _agent_request(
        info,
        "POST",
        "/internal/v1/poll",
        {"ack_batch_id": ack, "agent_reply": agent_reply, "timeout_ms": timeout_ms},
        timeout=(timeout_ms / 1000) + 5,
    )


def end_review(source: str | Path, *, state_root: str | Path | None = None) -> dict[str, object]:
    root = Path(state_root) if state_root is not None else default_review_state_root()
    info = _require_active_server(source, root)
    return _agent_request(info, "POST", "/internal/v1/end", {})


def review_status(
    source: str | Path | None,
    *,
    state_root: str | Path | None = None,
) -> dict[str, object]:
    root = Path(state_root) if state_root is not None else default_review_state_root()
    if source is not None:
        info = _active_server_info(source, root)
        if info is not None:
            return _agent_request(info, "GET", "/internal/v1/status", None)
        runtime = ReviewRuntime.resume(source, state_root=root)
        review = runtime.status()
        if review["status"] == "active":
            review = {**review, "status": "suspended"}
        return _tool_envelope("ViewSpec review status.", review=review)
    sessions: list[dict[str, object]] = []
    sessions_dir = root / "sessions"
    if sessions_dir.is_dir():
        for directory in sorted(sessions_dir.iterdir()):
            info_path = directory / "server.json"
            try:
                if info_path.is_file() and not info_path.is_symlink():
                    try:
                        info = _read_server_info_path(info_path)
                        response = _agent_request(info, "GET", "/internal/v1/status", None)
                        review = response.get("review")
                        if isinstance(review, dict):
                            sessions.append(review)
                            continue
                    except (ReviewContractError, OSError, UnicodeDecodeError, json.JSONDecodeError):
                        info_path.unlink(missing_ok=True)
                        info_path.with_name("agent-capability.json").unlink(missing_ok=True)
                config_path = directory / "session.json"
                if not config_path.is_file() or config_path.is_symlink() or config_path.stat().st_size > 64 * 1024:
                    continue
                config = json.loads(config_path.read_text(encoding="utf-8"))
                source_path = config.get("source_path") if isinstance(config, dict) else None
                if not isinstance(source_path, str) or review_session_dir(source_path, root) != directory:
                    continue
                review = ReviewRuntime.resume(source_path, state_root=root).status()
                if review["status"] == "active":
                    review = {**review, "status": "suspended"}
                sessions.append(review)
            except (ReviewContractError, OSError, UnicodeDecodeError, json.JSONDecodeError):
                sessions.append(
                    {
                        "status": "corrupt",
                        "error": {
                            "code": "REVIEW_JOURNAL_INVALID",
                            "message": "A retained Review session could not be loaded.",
                            "fix": "Inspect or purge the corrupt private session before resuming it.",
                        },
                    }
                )
    return {
        "schema_version": 1,
        "ok": True,
        "summary": "ViewSpec review sessions.",
        "diagnostics": [],
        "external_refs": [],
        "paths": {},
        "errors": [],
        "next_actions": [],
        "metadata": {"sdk_version": __version__, "network_calls": "loopback_only"},
        "reviews": sessions,
    }


def _spawn_daemon(
    source: str | Path,
    *,
    state_root: Path,
    design: str | Path | None,
    target: str | None,
    port: int,
    reopen: bool,
    verify: bool,
    install: bool,
    convergence_state_root: str | Path | None,
) -> dict[str, object]:
    command = [
        sys.executable,
        "-m",
        "viewspec.review_daemon",
        "--source",
        str(source),
        "--state-root",
        str(state_root),
        "--port",
        str(port),
    ]
    if design is not None:
        command.extend(("--design", str(design)))
    if target is not None:
        command.extend(("--target", target))
    if reopen:
        command.append("--reopen")
    if verify:
        command.append("--verify")
    if install:
        command.append("--install")
    if convergence_state_root is not None:
        command.extend(("--convergence-state-root", str(convergence_state_root)))
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        start_new_session=True,
    )
    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    ready = selector.select(timeout=DAEMON_START_TIMEOUT_SECONDS)
    selector.close()
    if not ready:
        process.terminate()
        raise ReviewContractError(
            "REVIEW_SERVER_START_FAILED",
            "Review daemon did not become ready within 190 seconds.",
            "Fix compile/check failures or retry the local review.",
            http_status=504,
            cli_exit=2,
        )
    line = process.stdout.readline()
    try:
        payload = json.loads(line)
    except (json.JSONDecodeError, TypeError) as exc:
        process.terminate()
        raise ReviewContractError(
            "REVIEW_SERVER_START_FAILED",
            "Review daemon returned an invalid readiness record.",
            "Restart the local review daemon.",
            cli_exit=1,
        ) from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            raise ReviewContractError(
                str(error.get("code", "REVIEW_SERVER_START_FAILED")),
                str(error.get("message", "Review daemon failed to start.")),
                str(error.get("fix", "Retry the local review.")),
                cli_exit=int(payload.get("cli_exit", 2)),
            )
        raise ReviewContractError(
            "REVIEW_SERVER_START_FAILED",
            "Review daemon failed to start.",
            "Inspect the source and local state, then retry.",
            cli_exit=2,
        )
    return payload


def _open_browser(url: str) -> None:
    command = (
        sys.executable,
        "-c",
        "import sys,webbrowser;raise SystemExit(0 if webbrowser.open(sys.argv[1],new=2) else 1)",
        url,
    )
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReviewContractError(
            "REVIEW_BROWSER_OPEN_FAILED",
            "The local Review URL was ready but browser launch did not complete within 10 seconds.",
            "Open the returned loopback URL manually before its 60-second expiry.",
            cli_exit=2,
        ) from exc
    if result.returncode != 0:
        raise ReviewContractError(
            "REVIEW_BROWSER_OPEN_FAILED",
            "The local Review URL was ready but the browser could not be opened.",
            "Open the returned loopback URL manually before its 60-second expiry.",
            cli_exit=2,
        )


def _wait_for_browser_handshake(source: str | Path, state_root: Path) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        info = _active_server_info(source, state_root)
        if info is not None:
            response = _agent_request(info, "GET", "/internal/v1/status", None, timeout=1)
            review = response.get("review")
            if isinstance(review, dict) and review.get("browser_ready") is True:
                return
        time.sleep(0.05)
    raise ReviewContractError(
        "REVIEW_BROWSER_HANDSHAKE_TIMEOUT",
        "The opened Review frame did not complete its checked SDK handshake within 5 seconds.",
        "Reload the resumable local Review URL after checking browser CSP and script execution.",
        http_status=409,
        cli_exit=2,
    )


def _require_active_server(source: str | Path, state_root: Path) -> dict[str, Any]:
    info = _active_server_info(source, state_root)
    if info is None:
        raise ReviewContractError(
            "REVIEW_SESSION_NOT_FOUND",
            "No active Review server exists for this source.",
            "Run viewspec review SOURCE before polling or ending it.",
            http_status=404,
        )
    return info


def _active_server_info(source: str | Path, state_root: Path) -> dict[str, Any] | None:
    path = review_session_dir(source, state_root) / "server.json"
    if not path.is_file() or path.is_symlink():
        return None
    try:
        info = _read_server_info_path(path)
        _agent_request(info, "GET", "/internal/v1/status", None, timeout=1)
        return info
    except (ReviewContractError, OSError):
        path.unlink(missing_ok=True)
        path.with_name("agent-capability.json").unlink(missing_ok=True)
        return None


def _read_server_info_path(path: Path) -> dict[str, Any]:
    value = path.lstat()
    if value.st_size > MAX_SERVER_INFO_BYTES:
        raise ReviewContractError(
            "REVIEW_JOURNAL_INVALID",
            "Private Review server record is oversized.",
            "Restart the local Review server.",
            http_status=500,
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or type(payload.get("port")) is not int
        or not isinstance(payload.get("agent_capability_sha256"), str)
    ):
        raise ReviewContractError(
            "REVIEW_JOURNAL_INVALID",
            "Private Review server record is malformed.",
            "Restart the local Review server.",
            http_status=500,
        )
    capability_path = path.with_name("agent-capability.json")
    if (
        not capability_path.is_file()
        or capability_path.is_symlink()
        or capability_path.stat().st_size > MAX_SERVER_INFO_BYTES
    ):
        raise ReviewContractError(
            "REVIEW_JOURNAL_INVALID",
            "Private Review agent capability record is missing or unsafe.",
            "Restart the local Review server.",
            http_status=500,
        )
    capability_record = json.loads(capability_path.read_text(encoding="utf-8"))
    capability = capability_record.get("agent_capability") if isinstance(capability_record, dict) else None
    digest = hashlib.sha256(capability.encode("ascii")).hexdigest() if isinstance(capability, str) else ""
    if (
        not isinstance(capability_record, dict)
        or set(capability_record) != {"schema_version", "agent_capability"}
        or capability_record.get("schema_version") != 1
        or not isinstance(capability, str)
        or not hmac.compare_digest(digest, payload["agent_capability_sha256"])
    ):
        raise ReviewContractError(
            "REVIEW_JOURNAL_INVALID",
            "Private Review agent capability does not match its session digest.",
            "Restart the local Review server.",
            http_status=500,
        )
    return {**payload, "agent_capability": capability}


def _agent_request(
    info: dict[str, Any],
    method: str,
    path: str,
    payload: dict[str, object] | None,
    *,
    timeout: float = 5,
) -> dict[str, object]:
    port = int(info["port"])
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    headers = {"X-ViewSpec-Agent-Capability": str(info["agent_capability"])}
    body: bytes | None = None
    if payload is not None:
        body = canonical_json_bytes(payload)
        headers["Content-Type"] = "application/json"
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        content = response.read(MAX_CONTROL_RESPONSE_BYTES + 1)
    except OSError as exc:
        raise ReviewContractError(
            "REVIEW_SESSION_NOT_FOUND",
            "Active Review server is not reachable.",
            "Run viewspec review SOURCE to resume the local server.",
            http_status=404,
        ) from exc
    finally:
        connection.close()
    if len(content) > MAX_CONTROL_RESPONSE_BYTES:
        raise ReviewContractError(
            "REVIEW_RESPONSE_TOO_LARGE",
            "Review control response exceeds the bounded CLI limit.",
            "Request a smaller Review projection.",
            cli_exit=1,
        )
    try:
        result = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ReviewContractError(
            "REVIEW_SERVER_START_FAILED",
            "Review server returned invalid control JSON.",
            "Restart the local Review server.",
            cli_exit=1,
        ) from exc
    if not isinstance(result, dict):
        raise ReviewContractError(
            "REVIEW_SERVER_START_FAILED", "Review control response is invalid.", "Restart the local Review server.", cli_exit=1
        )
    if response.status >= 400 or result.get("ok") is False:
        error = result.get("error")
        if isinstance(error, dict):
            raise ReviewContractError(
                str(error.get("code", "REVIEW_SERVER_START_FAILED")),
                str(error.get("message", "Review control request failed.")),
                str(error.get("fix", "Retry the Review operation.")),
                http_status=response.status,
            )
    return result


def _tool_envelope(
    summary: str,
    *,
    review: dict[str, object],
    next_actions: list[str] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "ok": True,
        "summary": summary,
        "diagnostics": [],
        "external_refs": [],
        "paths": {},
        "errors": [],
        "next_actions": next_actions or [],
        "metadata": {"sdk_version": __version__, "network_calls": "loopback_only"},
        "review": review,
    }


__all__ = ["DEFAULT_REVIEW_PORT", "end_review", "open_review", "poll_review", "review_status"]
