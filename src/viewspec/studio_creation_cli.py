"""CLI adapter for the local Studio first-creation room."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import selectors
import subprocess
import sys
from typing import Any

from viewspec._version import __version__
from viewspec.review_cli import (
    DAEMON_START_TIMEOUT_SECONDS,
    DEFAULT_REVIEW_PORT,
    MAX_SERVER_INFO_BYTES,
    _agent_request,
    _open_browser,
)
from viewspec.review_contract import ReviewContractError
from viewspec.review_runtime import default_review_state_root
from viewspec.studio_creation import STUDIO_CREATION_TASK_DEFAULT, inspect_studio_creation
from viewspec.studio_creation_daemon import creation_room_session_dir
from viewspec.studio_creation_room import creation_room_configuration_sha256


def open_studio_creation_room(
    task_path: str | Path = STUDIO_CREATION_TASK_DEFAULT,
    *,
    cwd: str | Path | None = None,
    design: str | Path | None = None,
    target: str | None = None,
    port: int = DEFAULT_REVIEW_PORT,
    state_root: str | Path | None = None,
    convergence_state_root: str | Path | None = None,
    no_open: bool = False,
    verify: bool = False,
    install: bool = False,
) -> dict[str, object]:
    """Start one resumable local creation room and optionally launch its browser tab."""

    if type(port) is not int or not 1024 <= port <= 65535:
        raise ReviewContractError(
            "REVIEW_PORT_UNAVAILABLE",
            "Studio creation port must be an integer from 1024 through 65535.",
            "Use the default port 4388 or one explicit unprivileged local port.",
            cli_exit=2,
        )
    inspected = inspect_studio_creation(task_path, cwd=cwd)
    root = inspected["root"]
    task = inspected["task"]
    assert isinstance(root, Path)
    assert isinstance(task, dict)
    effective_state_root = Path(state_root) if state_root is not None else default_review_state_root()
    configuration_sha256 = creation_room_configuration_sha256(
        task_id=str(task["task_id"]),
        cwd=root,
        design=design,
        target=target,
        port=port,
        state_root=effective_state_root,
        convergence_state_root=convergence_state_root,
        verify=verify,
        install=install,
    )
    active = _active_creation_room(str(task["task_id"]), effective_state_root)
    if active is not None:
        if active["configuration_sha256"] != configuration_sha256:
            raise ReviewContractError(
                "REVIEW_SESSION_CONFIGURATION_CONFLICT",
                "Active Studio creation room configuration does not match this invocation.",
                "Use the exact original design, target, verification, install, state, and requested port options.",
                http_status=409,
            )
        refreshed = _agent_request(active, "POST", "/internal/v1/bootstrap", None)
        bootstrap_url = refreshed.get("bootstrap_url")
        creation = _agent_request(active, "GET", "/internal/v1/status", None)
        if not isinstance(bootstrap_url, str):
            raise ReviewContractError(
                "STUDIO_CREATION_ROOM_FAILED",
                "Active Studio creation room did not return a fresh browser entry.",
                "Restart the exact local Studio creation task.",
                cli_exit=1,
            )
        payload: dict[str, Any] = {
            "schema_version": 1,
            "ok": True,
            "bootstrap_url": bootstrap_url,
            "port": active["port"],
            "creation": creation,
            "_agent_capability": active["agent_capability"],
        }
    else:
        command = [
            sys.executable,
            "-m",
            "viewspec.studio_creation_daemon",
            "--task",
            str(task_path),
            "--cwd",
            str(root),
            "--state-root",
            str(effective_state_root),
            "--port",
            str(port),
        ]
        if design is not None:
            command.extend(("--design", str(design)))
        if target is not None:
            command.extend(("--target", target))
        if convergence_state_root is not None:
            command.extend(("--convergence-state-root", str(convergence_state_root)))
        if verify:
            command.append("--verify")
        if install:
            command.append("--install")
        payload = _spawn_creation_daemon(command)
    private_capability = payload.pop("_agent_capability", None)
    if not isinstance(private_capability, str):
        raise ReviewContractError(
            "STUDIO_CREATION_ROOM_FAILED",
            "Studio creation daemon did not return its private local control identity.",
            "Restart the exact local Studio creation task.",
            cli_exit=1,
        )
    bootstrap_url = payload.get("bootstrap_url")
    creation = payload.get("creation")
    actual_port = payload.get("port")
    if not isinstance(bootstrap_url, str) or not isinstance(creation, dict) or type(actual_port) is not int:
        raise ReviewContractError(
            "STUDIO_CREATION_ROOM_FAILED",
            "Studio creation daemon did not return a complete readiness result.",
            "Restart the exact local Studio creation task.",
            cli_exit=1,
        )
    if not no_open:
        _open_browser(bootstrap_url)
    return {
        "schema_version": 1,
        "ok": True,
        "summary": "ViewSpec Studio creation room is ready.",
        "diagnostics": [],
        "external_refs": [],
        "paths": {
            "task": str(inspected["task_path"]),
            "candidate": str(inspected["candidate_path"]),
            "source": str(inspected["source_path"]),
            "proof": str(inspected["proof_path"]),
        },
        "errors": [],
        "next_actions": [
            f"Author the exact {task['source_kind']} candidate at {task['candidate_path']} from the task; do not copy a starter.",
            "Keep the creation room open while Studio checks the candidate and continues to the product.",
        ],
        "metadata": {
            "sdk_version": __version__,
            "network_calls": "loopback_only",
            "reference_uploaded": False,
        },
        "creation": {**creation, "url": bootstrap_url, "port": actual_port},
        "studio": {
            "experience_version": 1,
            "status": "creating",
            "primary_loop": ["brief", "check", "preview", "comment", "approve"],
            "primary_action": "wait_for_checked_product",
            "network": "loopback_only",
        },
    }


def accepted_creation_handoff_port(
    task_path: str | Path = STUDIO_CREATION_TASK_DEFAULT,
    *,
    cwd: str | Path | None = None,
    design: str | Path | None = None,
    target: str | None = None,
    requested_port: int = DEFAULT_REVIEW_PORT,
    state_root: str | Path | None = None,
    convergence_state_root: str | Path | None = None,
    verify: bool = False,
    install: bool = False,
    expected_source_sha256: str,
) -> int | None:
    """Return the exact retained review port for a completed room, if one exists."""

    inspected = inspect_studio_creation(task_path, cwd=cwd)
    root = inspected["root"]
    task = inspected["task"]
    proof_path = inspected["proof_path"]
    assert isinstance(root, Path)
    assert isinstance(task, dict)
    assert isinstance(proof_path, Path)
    receipt_path = proof_path / "room-transition.json"
    if not receipt_path.exists() and not receipt_path.is_symlink():
        return None
    try:
        if receipt_path.is_symlink() or not receipt_path.is_file() or receipt_path.stat().st_size > MAX_SERVER_INFO_BYTES:
            raise ValueError("unsafe receipt")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ReviewContractError(
            "STUDIO_CREATION_RECEIPT_INVALID",
            "The retained Studio creation-room transition receipt is unsafe or invalid.",
            "Inspect the local creation evidence before reopening the checked product.",
            cli_exit=2,
        ) from exc
    fields = {
        "artifact_check",
        "candidate_to_checked_ms",
        "candidate_validation",
        "configuration_sha256",
        "network_calls",
        "review_port",
        "revision",
        "room_elapsed_ms",
        "schema_version",
        "source_kind",
        "source_sha256",
        "status",
        "target",
        "task_id",
    }
    if (
        not isinstance(receipt, dict)
        or set(receipt) != fields
        or receipt.get("schema_version") != 1
        or receipt.get("task_id") != task["task_id"]
        or receipt.get("status") != "checked"
        or receipt.get("source_kind") != task["source_kind"]
        or receipt.get("source_sha256") != expected_source_sha256
        or receipt.get("candidate_validation") != "passed"
        or receipt.get("artifact_check") != "passed"
        or receipt.get("network_calls") != "none"
        or type(receipt.get("review_port")) is not int
        or not 1024 <= receipt["review_port"] <= 65535
        or type(receipt.get("revision")) is not int
        or not isinstance(receipt.get("target"), str)
        or type(receipt.get("room_elapsed_ms")) is not int
        or receipt["room_elapsed_ms"] < 0
        or (
            receipt.get("candidate_to_checked_ms") is not None
            and (type(receipt["candidate_to_checked_ms"]) is not int or receipt["candidate_to_checked_ms"] < 0)
        )
    ):
        raise ReviewContractError(
            "STUDIO_CREATION_RECEIPT_INVALID",
            "The retained Studio creation-room transition receipt does not match the checked source.",
            "Inspect the local task, candidate, proof, and source before reopening Studio.",
            cli_exit=2,
        )
    effective_state_root = Path(state_root) if state_root is not None else default_review_state_root()
    expected_configuration = creation_room_configuration_sha256(
        task_id=str(task["task_id"]),
        cwd=root,
        design=design,
        target=target,
        port=requested_port,
        state_root=effective_state_root,
        convergence_state_root=convergence_state_root,
        verify=verify,
        install=install,
    )
    if receipt.get("configuration_sha256") != expected_configuration:
        raise ReviewContractError(
            "REVIEW_SESSION_CONFIGURATION_CONFLICT",
            "Checked Studio creation-room configuration does not match this invocation.",
            "Use the exact original design, target, verification, install, state, and requested port options.",
            http_status=409,
        )
    return int(receipt["review_port"])


def _spawn_creation_daemon(command: list[str]) -> dict[str, Any]:
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
            "STUDIO_CREATION_ROOM_FAILED",
            "Studio creation room did not become ready within 190 seconds.",
            "Inspect the exact local task and retry viewspec studio.",
            cli_exit=2,
        )
    line = process.stdout.readline()
    try:
        payload = json.loads(line)
    except (json.JSONDecodeError, TypeError) as exc:
        process.terminate()
        raise ReviewContractError(
            "STUDIO_CREATION_ROOM_FAILED",
            "Studio creation daemon returned an invalid readiness record.",
            "Restart the exact local Studio creation task.",
            cli_exit=1,
        ) from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            raise ReviewContractError(
                str(error.get("code", "STUDIO_CREATION_ROOM_FAILED")),
                str(error.get("message", "Studio creation room failed to start.")),
                str(error.get("fix", "Retry the exact local Studio creation task.")),
                cli_exit=int(payload.get("cli_exit", 2)),
            )
        raise ReviewContractError(
            "STUDIO_CREATION_ROOM_FAILED",
            "Studio creation room failed to start.",
            "Inspect the exact local task and retry.",
            cli_exit=2,
        )
    return payload


def _active_creation_room(task_id: str, state_root: Path) -> dict[str, Any] | None:
    session_dir = creation_room_session_dir(task_id, state_root)
    metadata_path = session_dir / "server.json"
    capability_path = session_dir / "agent-capability.json"
    if not metadata_path.is_file() or metadata_path.is_symlink():
        return None
    try:
        if metadata_path.stat().st_size > MAX_SERVER_INFO_BYTES:
            raise ValueError("oversized metadata")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if (
            not isinstance(metadata, dict)
            or set(metadata)
            != {
                "agent_capability_sha256",
                "configuration_sha256",
                "pid",
                "port",
                "schema_version",
                "task_id",
            }
            or metadata.get("schema_version") != 1
            or metadata.get("task_id") != task_id
            or type(metadata.get("port")) is not int
            or not 1024 <= metadata["port"] <= 65535
            or not isinstance(metadata.get("configuration_sha256"), str)
            or len(metadata["configuration_sha256"]) != 64
            or not isinstance(metadata.get("agent_capability_sha256"), str)
        ):
            raise ValueError("invalid metadata")
        if (
            not capability_path.is_file()
            or capability_path.is_symlink()
            or capability_path.stat().st_size > MAX_SERVER_INFO_BYTES
        ):
            raise ValueError("invalid capability file")
        capability_record = json.loads(capability_path.read_text(encoding="utf-8"))
        capability = capability_record.get("agent_capability") if isinstance(capability_record, dict) else None
        if (
            not isinstance(capability_record, dict)
            or set(capability_record) != {"agent_capability", "schema_version"}
            or capability_record.get("schema_version") != 1
            or not isinstance(capability, str)
            or not hmac.compare_digest(
                hashlib.sha256(capability.encode("ascii")).hexdigest(),
                metadata["agent_capability_sha256"],
            )
        ):
            raise ValueError("invalid capability")
        active = {**metadata, "agent_capability": capability}
        _agent_request(active, "GET", "/internal/v1/status", None, timeout=1)
        return active
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, ReviewContractError):
        metadata_path.unlink(missing_ok=True)
        capability_path.unlink(missing_ok=True)
        return None


__all__ = ["accepted_creation_handoff_port", "open_studio_creation_room"]
