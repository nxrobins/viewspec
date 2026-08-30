"""Private background process for the local Studio first-creation room."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import threading
import time

from viewspec.local_tools import atomic_write
from viewspec.review_cli import DEFAULT_REVIEW_PORT, open_review
from viewspec.review_contract import ReviewContractError
from viewspec.review_runtime import default_review_state_root
from viewspec.review_session import ReviewStateLock
from viewspec.studio_creation import StudioCreationError, inspect_studio_creation
from viewspec.studio_creation_room import (
    StudioCreationHandoff,
    StudioCreationRoomController,
    StudioCreationRoomServer,
    creation_room_configuration_sha256,
)


def creation_room_session_dir(task_id: str, state_root: str | Path) -> Path:
    digest = hashlib.sha256(f"viewspec.studio.creation-room.v1\x00{task_id}".encode("ascii")).hexdigest()
    return Path(state_root) / "creation-rooms" / digest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--task", required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--state-root")
    parser.add_argument("--convergence-state-root")
    parser.add_argument("--target")
    parser.add_argument("--design")
    parser.add_argument("--port", type=int, default=DEFAULT_REVIEW_PORT)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args(argv)
    state_root = Path(args.state_root) if args.state_root is not None else default_review_state_root()
    stop = threading.Event()
    server: StudioCreationRoomServer | None = None
    state_lock: ReviewStateLock | None = None
    metadata_path: Path | None = None
    capability_path: Path | None = None
    started = time.perf_counter()
    room_port = args.port
    try:
        inspected = inspect_studio_creation(args.task, cwd=args.cwd)
        task = inspected["task"]
        proof_path = inspected["proof_path"]
        assert isinstance(task, dict)
        assert isinstance(proof_path, Path)
        configuration_sha256 = creation_room_configuration_sha256(
            task_id=str(task["task_id"]),
            cwd=args.cwd,
            design=args.design,
            target=args.target,
            port=args.port,
            state_root=state_root,
            convergence_state_root=args.convergence_state_root,
            verify=args.verify,
            install=args.install,
        )
        session_dir = creation_room_session_dir(str(task["task_id"]), state_root)
        state_lock = ReviewStateLock(session_dir / ".writer.lock")
        state_lock.acquire(timeout_seconds=2.0)

        controller_ref: list[StudioCreationRoomController] = []

        def handoff(source: Path, accepted: dict[str, object]) -> StudioCreationHandoff:
            review_port = _available_loopback_port(exclude={room_port})
            payload = open_review(
                source,
                design=args.design,
                target=args.target,
                port=review_port,
                state_root=state_root,
                convergence_state_root=args.convergence_state_root,
                no_open=True,
                verify=args.verify,
                install=args.install,
            )
            review = payload.get("review")
            if not isinstance(review, dict) or not isinstance(review.get("url"), str):
                raise ReviewContractError(
                    "REVIEW_SERVER_START_FAILED",
                    "The checked Studio handoff did not return a complete local review.",
                    "Retry the exact Studio creation task.",
                    cli_exit=1,
                )
            creation = accepted.get("creation")
            source_sha256 = creation.get("source_sha256") if isinstance(creation, dict) else None
            if not isinstance(source_sha256, str):
                source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
            room_elapsed_ms = max(0, round((time.perf_counter() - started) * 1000))
            candidate_to_checked_ms = controller_ref[0].candidate_to_checked_ms
            receipt = {
                "schema_version": 1,
                "task_id": task["task_id"],
                "configuration_sha256": configuration_sha256,
                "status": "checked",
                "source_kind": task["source_kind"],
                "source_sha256": source_sha256,
                "candidate_validation": "passed",
                "artifact_check": "passed",
                "target": review.get("target"),
                "revision": review.get("revision"),
                "review_port": review.get("port"),
                "candidate_to_checked_ms": candidate_to_checked_ms,
                "room_elapsed_ms": room_elapsed_ms,
                "network_calls": "none",
            }
            receipt_path = proof_path / "room-transition.json"
            atomic_write(receipt_path, json.dumps(receipt, indent=2, sort_keys=True) + "\n")
            return StudioCreationHandoff(url=str(review["url"]), source_sha256=source_sha256, review=review)

        controller = StudioCreationRoomController(args.task, cwd=args.cwd, handoff=handoff)
        controller_ref.append(controller)
        server = StudioCreationRoomServer(controller, port=args.port)
        room_port = server.port
        metadata_path = session_dir / "server.json"
        capability_path = session_dir / "agent-capability.json"
        _write_private_json(
            capability_path,
            {"schema_version": 1, "agent_capability": server.agent_token},
        )
        _write_private_json(
            metadata_path,
            {
                "schema_version": 1,
                "pid": os.getpid(),
                "port": server.port,
                "task_id": task["task_id"],
                "configuration_sha256": configuration_sha256,
                "agent_capability_sha256": hashlib.sha256(server.agent_token.encode("ascii")).hexdigest(),
            },
        )
        _install_signal_handlers(stop)
        server.start()
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "ok": True,
                    "bootstrap_url": server.bootstrap_url,
                    "port": server.port,
                    "creation": controller.status(),
                    "_agent_capability": server.agent_token,
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
            flush=True,
        )
        while not stop.wait(0.1):
            controller.poll_once()
            if controller.should_exit:
                break
        return 0
    except (ReviewContractError, StudioCreationError) as exc:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "ok": False,
                    "error": exc.to_json(),
                    "cli_exit": exc.cli_exit,
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
            flush=True,
        )
        return exc.cli_exit
    except Exception as exc:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "ok": False,
                    "error": {
                        "code": "STUDIO_CREATION_ROOM_FAILED",
                        "message": str(exc)[:2048],
                        "fix": "Inspect the local creation task and retry viewspec studio.",
                    },
                    "cli_exit": 1,
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
            flush=True,
        )
        return 1
    finally:
        if server is not None:
            server.stop()
        if metadata_path is not None:
            metadata_path.unlink(missing_ok=True)
        if capability_path is not None:
            capability_path.unlink(missing_ok=True)
        if state_lock is not None:
            state_lock.release()


def _available_loopback_port(*, exclude: set[int]) -> int:
    for _ in range(8):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = int(probe.getsockname()[1])
        if port not in exclude and 1024 <= port <= 65535:
            return port
    raise ReviewContractError(
        "REVIEW_PORT_UNAVAILABLE",
        "Studio could not reserve a local handoff port.",
        "Close an unused local Studio session and retry.",
        cli_exit=2,
    )


def _write_private_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    atomic_write(path, json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n")
    path.chmod(0o600)


def _install_signal_handlers(stop: threading.Event) -> None:
    def handle_signal(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["creation_room_session_dir", "main"]
