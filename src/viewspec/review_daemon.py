"""Private background process used by the public Review CLI commands."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time

from viewspec.review_contract import ReviewContractError, canonical_json_bytes
from viewspec.review_cli import DEFAULT_REVIEW_PORT
from viewspec.review_compile import bounded_review_phase, capture_source_snapshot
from viewspec.review_runtime import ReviewRuntime, review_session_dir
from viewspec.review_server import ReviewServer
from viewspec.review_session import ReviewStateLock
from viewspec.local_verify import verify_local_artifact
from viewspec.verification import VerificationDiagnostic, VerificationPlan, VerificationResult


WATCH_QUIET_SECONDS = 0.250
WATCH_MAX_COALESCE_SECONDS = 2.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--source", required=True)
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--convergence-state-root")
    parser.add_argument("--target")
    parser.add_argument("--design")
    parser.add_argument("--port", type=int, default=DEFAULT_REVIEW_PORT)
    parser.add_argument("--reopen", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args(argv)
    server: ReviewServer | None = None
    metadata_path: Path | None = None
    capability_path: Path | None = None
    state_lock: ReviewStateLock | None = None
    stop = threading.Event()
    try:
        snapshot = capture_source_snapshot(args.source, design_path=args.design)
        session_dir = review_session_dir(snapshot.source_path, args.state_root)
        state_lock = ReviewStateLock(session_dir / ".writer.lock")
        state_lock.acquire(timeout_seconds=2.0)
        _enforce_active_session_limit(Path(args.state_root), session_dir)
        compile_lock = _acquire_resource_slot(Path(args.state_root), "compile", count=2)
        try:
            runtime = ReviewRuntime.open(
                args.source,
                state_root=args.state_root,
                convergence_state_root=args.convergence_state_root,
                target=args.target,
                design_path=args.design,
                requested_port=args.port,
                verification_plan_sha256=VerificationPlan.default().plan_sha256 if args.verify else None,
                allow_install=args.install,
                reopen=args.reopen,
            )
        finally:
            compile_lock.release()
        if args.verify:
            _run_verification(runtime, install=args.install)
        server = ReviewServer(runtime, port=args.port)
        metadata_path = runtime.session_dir / "server.json"
        capability_path = runtime.session_dir / "agent-capability.json"
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
                "review_id": runtime.session.review_id,
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
                    "review": runtime.status(),
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
            flush=True,
        )
        _watch_sources(runtime, server, stop, verify=args.verify, install=args.install)
        return 0
    except ReviewContractError as exc:
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
                        "code": "REVIEW_SERVER_START_FAILED",
                        "message": str(exc)[:2048],
                        "fix": "Inspect the private local runtime and retry viewspec review.",
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


def _watch_sources(
    runtime: ReviewRuntime,
    server: ReviewServer,
    stop: threading.Event,
    *,
    verify: bool,
    install: bool,
) -> None:
    paths = [Path(runtime.configuration.source_path)]
    if runtime.configuration.design_path is not None:
        paths.append(Path(runtime.configuration.design_path))
    observed = tuple(_signature(path) for path in paths)
    pending_since: float | None = None
    last_change: float | None = None
    while not stop.wait(0.1):
        if server.should_auto_exit or server.should_suspend:
            stop.set()
            break
        current = tuple(_signature(path) for path in paths)
        now = time.monotonic()
        if current != observed:
            observed = current
            pending_since = pending_since or now
            last_change = now
        if pending_since is None or last_change is None:
            continue
        if now - last_change < WATCH_QUIET_SECONDS and now - pending_since < WATCH_MAX_COALESCE_SECONDS:
            continue
        try:
            compile_lock = _acquire_resource_slot(runtime.state_root, "compile", count=2)
            try:
                runtime.rebuild()
            finally:
                compile_lock.release()
            if verify:
                _run_verification(runtime, install=install)
        except ReviewContractError:
            pass
        finally:
            server.notify_state_changed()
        pending_since = None
        last_change = None


def _run_verification(runtime: ReviewRuntime, *, install: bool) -> None:
    verification_lock = _acquire_resource_slot(runtime.state_root, "verification", count=1)
    plan = VerificationPlan.default()
    verification_dir = runtime.built.revision_dir / "verification"
    evidence_dir = verification_dir / "evidence"
    raw_report = verification_dir / "raw_result.json"
    try:
        try:
            with bounded_review_phase("REVIEW_VERIFICATION_TIMEOUT", 180):
                result = verify_local_artifact(
                    runtime.built.artifact_dir,
                    plan=plan,
                    evidence_dir=evidence_dir,
                    report_out=raw_report,
                    install=install,
                )
            normalized = VerificationResult.create(
                artifact_sha256=runtime.built.revision.artifact_set_sha256,
                plan=plan,
                complete=result.complete,
                diagnostics=result.diagnostics,
                evidence=result.evidence,
                lineage=result.lineage,
            )
        except ReviewContractError as exc:
            if exc.code == "REVIEW_VERIFICATION_TIMEOUT":
                raise
            diagnostic = VerificationDiagnostic(
                code="VERIFY_BROWSER_EXECUTION_FAILED",
                severity="warning",
                message=exc.message[:2048],
                fix=exc.fix[:2048],
            )
            normalized = VerificationResult.create(
                artifact_sha256=runtime.built.revision.artifact_set_sha256,
                plan=plan,
                complete=False,
                diagnostics=(diagnostic,),
            )
        except Exception as exc:
            diagnostic = VerificationDiagnostic(
                code="VERIFY_BROWSER_EXECUTION_FAILED",
                severity="warning",
                message=str(exc)[:2048] or "Canonical viewport verification was unavailable.",
                fix="Repair the local verifier environment and retry with the same checked revision.",
            )
            normalized = VerificationResult.create(
                artifact_sha256=runtime.built.revision.artifact_set_sha256,
                plan=plan,
                complete=False,
                diagnostics=(diagnostic,),
            )
        runtime.record_verification(normalized)
        _secure_tree(verification_dir)
    finally:
        verification_lock.release()


def _acquire_resource_slot(state_root: Path, name: str, *, count: int) -> ReviewStateLock:
    state_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    for index in range(count):
        lock = ReviewStateLock(state_root / f".{name}-{index}.lock")
        try:
            lock.acquire(timeout_seconds=0.01)
            return lock
        except ReviewContractError as exc:
            if exc.code != "REVIEW_STATE_LOCKED":
                raise
    raise ReviewContractError(
        "REVIEW_SERVER_BUSY",
        f"All {count} bounded Review {name} slot(s) are active.",
        "Retry after another bounded local worker completes.",
        http_status=503,
        cli_exit=2,
    )


def _secure_tree(root: Path) -> None:
    if not root.exists():
        return
    for path in root.rglob("*"):
        if path.is_dir() and not path.is_symlink():
            path.chmod(0o700)
        elif path.is_file() and not path.is_symlink():
            path.chmod(0o600)
    root.chmod(0o700)


def _signature(path: Path) -> tuple[int, int, int, int, int] | tuple[str]:
    try:
        value = path.lstat()
    except OSError:
        return ("missing",)
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _write_private_json(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        content = canonical_json_bytes(payload)
        offset = 0
        while offset < len(content):
            offset += os.write(descriptor, content[offset:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _install_signal_handlers(stop: threading.Event) -> None:
    def terminate(signum: int, frame: object) -> None:
        del signum, frame
        stop.set()

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)


def _enforce_active_session_limit(state_root: Path, current_session: Path) -> None:
    sessions = state_root / "sessions"
    if not sessions.is_dir():
        return
    active = 0
    for path in sessions.glob("*/server.json"):
        if path.parent == current_session:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            pid = payload.get("pid") if isinstance(payload, dict) else None
            if type(pid) is not int:
                continue
            os.kill(pid, 0)
        except (OSError, json.JSONDecodeError):
            continue
        active += 1
    if active >= 16:
        raise ReviewContractError(
            "REVIEW_SESSION_LIMIT_EXCEEDED",
            "Review state already has 16 active local sessions.",
            "End an active review before starting another.",
            cli_exit=2,
        )


if __name__ == "__main__":
    sys.exit(main())
