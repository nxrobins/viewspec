#!/usr/bin/env python3
"""Run the hash-bound, resumable Studio production private-review canary."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from typing import Any, Callable, Mapping, Sequence

if __package__:
    from scripts import check_studio_production_canary as checker
else:
    import check_studio_production_canary as checker


SCHEMA_VERSION = 1
RUNNER_ID = "viewspec-studio-production-canary-runner-v1"
REPORT_NAME = "production-canary-evidence.json"
PLAN_FIELDS = {
    "schema_version",
    "runner_id",
    "run_id",
    "environment",
    "origin",
    "deployment_sha256",
    "created_at_epoch_ms",
    "driver",
    "inputs",
    "stages",
}
DRIVER_FIELDS = {"path", "sha256"}
INPUT_FIELDS = {"runner_sha256", "verifier_sha256"}
LOCK_FIELDS = {
    "schema_version",
    "runner_id",
    "run_id",
    "plan_sha256",
    "driver_sha256",
    "runner_sha256",
    "verifier_sha256",
}
CHECKPOINT_FIELDS = {
    "schema_version",
    "runner_id",
    "run_id",
    "plan_sha256",
    "started_at_epoch_ms",
    "completed_stages",
    "commands",
    "failure",
}
FAILURE_FIELDS = {
    "kind",
    "reason",
    "exit_code",
    "elapsed_ms",
    "stdout_sha256",
    "stderr_sha256",
    "stdout_bytes",
    "stderr_bytes",
}
MAX_STREAM_BYTES = 1024 * 1024


class CanaryRunError(RuntimeError):
    """Raised when a canary run cannot produce trustworthy retained evidence."""


def _canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _write_atomic(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(value)
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)


def _write_json(path: Path, value: object) -> None:
    _write_atomic(path, _canonical_bytes(value))


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CanaryRunError(f"Could not read canary state {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CanaryRunError(f"Canary state must be one JSON object: {path}")
    return value


def _exact(value: Mapping[str, Any], fields: set[str], noun: str) -> None:
    if set(value) != fields:
        missing = sorted(fields - set(value))
        unknown = sorted(set(value) - fields)
        raise CanaryRunError(f"{noun} shape mismatch; missing={missing}, unknown={unknown}")


def _regular_file(path: Path, noun: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_file() or resolved.is_symlink():
        raise CanaryRunError(f"{noun} must be one regular non-symlink file: {resolved}")
    return resolved


def initialize_canary(
    output: str | Path,
    *,
    driver: str | Path,
    deployment_sha256: str,
    run_id: str | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Freeze one empty production run before any stage command executes."""

    destination = Path(output).resolve()
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise CanaryRunError(f"Canary output must be an empty directory: {destination}")
    if checker.SHA256_RE.fullmatch(deployment_sha256) is None:
        raise CanaryRunError("Production deployment identity must be one lowercase SHA-256")
    selected_run_id = run_id or f"vsrcan_{secrets.token_hex(16)}"
    if checker.RUN_ID_RE.fullmatch(selected_run_id) is None:
        raise CanaryRunError("Production canary run id is invalid")
    driver_path = _regular_file(Path(driver), "Production canary stage driver")
    runner_path = _regular_file(Path(__file__), "Production canary runner")
    verifier_path = _regular_file(Path(checker.__file__), "Production canary verifier")
    destination.mkdir(parents=True, exist_ok=True)
    created_at = int(time.time() * 1000) if now_ms is None else now_ms
    if type(created_at) is not int or created_at < 0:
        raise CanaryRunError("Production canary creation time is invalid")
    plan = {
        "schema_version": SCHEMA_VERSION,
        "runner_id": RUNNER_ID,
        "run_id": selected_run_id,
        "environment": "production",
        "origin": checker.ORIGIN,
        "deployment_sha256": deployment_sha256,
        "created_at_epoch_ms": created_at,
        "driver": {"path": str(driver_path), "sha256": _sha256_file(driver_path)},
        "inputs": {
            "runner_sha256": _sha256_file(runner_path),
            "verifier_sha256": _sha256_file(verifier_path),
        },
        "stages": list(checker.STAGE_KINDS),
    }
    _write_json(destination / "canary-plan.json", plan)
    plan_sha256 = _sha256_file(destination / "canary-plan.json")
    lock = {
        "schema_version": SCHEMA_VERSION,
        "runner_id": RUNNER_ID,
        "run_id": selected_run_id,
        "plan_sha256": plan_sha256,
        "driver_sha256": plan["driver"]["sha256"],
        "runner_sha256": plan["inputs"]["runner_sha256"],
        "verifier_sha256": plan["inputs"]["verifier_sha256"],
    }
    checkpoint = {
        "schema_version": SCHEMA_VERSION,
        "runner_id": RUNNER_ID,
        "run_id": selected_run_id,
        "plan_sha256": plan_sha256,
        "started_at_epoch_ms": None,
        "completed_stages": [],
        "commands": [],
        "failure": None,
    }
    _write_json(destination / "canary-lock.json", lock)
    _write_json(destination / "checkpoint.json", checkpoint)
    (destination / "stages").mkdir(mode=0o700)
    return {"root": str(destination), "plan": plan, "lock": lock}


def _load_state(root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], Path]:
    plan_path = root / "canary-plan.json"
    lock_path = root / "canary-lock.json"
    checkpoint_path = root / "checkpoint.json"
    plan = _read_object(plan_path)
    lock = _read_object(lock_path)
    checkpoint = _read_object(checkpoint_path)
    _exact(plan, PLAN_FIELDS, "canary plan")
    _exact(lock, LOCK_FIELDS, "canary lock")
    _exact(checkpoint, CHECKPOINT_FIELDS, "canary checkpoint")
    if (
        plan.get("schema_version") != SCHEMA_VERSION
        or plan.get("runner_id") != RUNNER_ID
        or plan.get("environment") != "production"
        or plan.get("origin") != checker.ORIGIN
        or plan.get("stages") != list(checker.STAGE_KINDS)
    ):
        raise CanaryRunError("Canary plan identity or stage order is invalid")
    run_id = plan.get("run_id")
    deployment_sha256 = plan.get("deployment_sha256")
    if not isinstance(run_id, str) or checker.RUN_ID_RE.fullmatch(run_id) is None:
        raise CanaryRunError("Canary plan run id is invalid")
    if not isinstance(deployment_sha256, str) or checker.SHA256_RE.fullmatch(deployment_sha256) is None:
        raise CanaryRunError("Canary plan deployment identity is invalid")
    driver = plan.get("driver")
    inputs = plan.get("inputs")
    if not isinstance(driver, dict) or not isinstance(inputs, dict):
        raise CanaryRunError("Canary plan input bindings are invalid")
    _exact(driver, DRIVER_FIELDS, "canary driver binding")
    _exact(inputs, INPUT_FIELDS, "canary input binding")
    driver_path = _regular_file(Path(str(driver.get("path"))), "Production canary stage driver")
    current = {
        "plan_sha256": _sha256_file(plan_path),
        "driver_sha256": _sha256_file(driver_path),
        "runner_sha256": _sha256_file(Path(__file__).resolve()),
        "verifier_sha256": _sha256_file(Path(checker.__file__).resolve()),
    }
    if (
        lock.get("schema_version") != SCHEMA_VERSION
        or lock.get("runner_id") != RUNNER_ID
        or lock.get("run_id") != run_id
        or any(lock.get(name) != value for name, value in current.items())
        or driver.get("sha256") != current["driver_sha256"]
        or inputs.get("runner_sha256") != current["runner_sha256"]
        or inputs.get("verifier_sha256") != current["verifier_sha256"]
    ):
        raise CanaryRunError("Canary plan, driver, runner, verifier, or lock hash changed")
    if (
        checkpoint.get("schema_version") != SCHEMA_VERSION
        or checkpoint.get("runner_id") != RUNNER_ID
        or checkpoint.get("run_id") != run_id
        or checkpoint.get("plan_sha256") != current["plan_sha256"]
    ):
        raise CanaryRunError("Canary checkpoint is not bound to this run")
    _validate_checkpoint(root, plan, checkpoint)
    return plan, lock, checkpoint, driver_path


def _validate_checkpoint(root: Path, plan: Mapping[str, Any], checkpoint: Mapping[str, Any]) -> None:
    completed = checkpoint.get("completed_stages")
    commands = checkpoint.get("commands")
    if not isinstance(completed, list) or not isinstance(commands, list) or len(completed) != len(commands):
        raise CanaryRunError("Canary checkpoint stage and command counts differ")
    expected_kinds = list(checker.STAGE_KINDS[: len(completed)])
    observed_kinds = [item.get("kind") for item in completed if isinstance(item, dict)]
    command_kinds = [item.get("kind") for item in commands if isinstance(item, dict)]
    if observed_kinds != expected_kinds or command_kinds != expected_kinds:
        raise CanaryRunError("Canary checkpoint stages are missing, duplicated, or reordered")
    for reference, command in zip(completed, commands, strict=True):
        if not isinstance(reference, dict) or not isinstance(command, dict):
            raise CanaryRunError("Canary checkpoint records must be objects")
        _exact(reference, checker.STAGE_REF_FIELDS, "checkpoint stage reference")
        _exact(command, checker.COMMAND_FIELDS, "checkpoint command receipt")
        relative = reference.get("path")
        if not isinstance(relative, str) or Path(relative).is_absolute():
            raise CanaryRunError("Checkpoint stage path is invalid")
        stage_path = (root / relative).resolve()
        try:
            stage_path.relative_to(root)
        except ValueError as exc:
            raise CanaryRunError("Checkpoint stage path escapes the run root") from exc
        if not stage_path.is_file() or stage_path.is_symlink():
            raise CanaryRunError("Checkpoint stage evidence is missing or not a regular file")
        if reference.get("sha256") != _sha256_file(stage_path):
            raise CanaryRunError("Checkpoint stage evidence hash changed")
        payload = _read_object(stage_path)
        try:
            checker.validate_stage_payload(
                payload,
                kind=str(reference["kind"]),
                run_id=str(plan["run_id"]),
                deployment_sha256=str(plan["deployment_sha256"]),
            )
        except checker.CanaryError as exc:
            raise CanaryRunError(f"Checkpoint stage evidence is invalid: {exc}") from exc
        if command.get("exit_code") != 0:
            raise CanaryRunError("Completed checkpoint command did not exit zero")
    failure = checkpoint.get("failure")
    if failure is not None:
        if not isinstance(failure, dict):
            raise CanaryRunError("Canary checkpoint failure must be an object or null")
        _exact(failure, FAILURE_FIELDS, "canary checkpoint failure")
        expected_failure = checker.STAGE_KINDS[len(completed)] if len(completed) < len(checker.STAGE_KINDS) else None
        if failure.get("kind") != expected_failure:
            raise CanaryRunError("Canary checkpoint failure names the wrong next stage")


def _default_execute(command: Sequence[str], *, cwd: Path, environment: Mapping[str, str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        list(command),
        cwd=cwd,
        env=dict(environment),
        capture_output=True,
        check=False,
        timeout=600,
    )


def _receipt(
    *,
    kind: str,
    command: Sequence[str],
    completed: subprocess.CompletedProcess[bytes],
    elapsed_ms: int,
) -> dict[str, Any]:
    stdout = completed.stdout if isinstance(completed.stdout, bytes) else str(completed.stdout or "").encode()
    stderr = completed.stderr if isinstance(completed.stderr, bytes) else str(completed.stderr or "").encode()
    return {
        "kind": kind,
        "argv_sha256": _sha256_bytes(_canonical_bytes(list(command))),
        "exit_code": int(completed.returncode),
        "elapsed_ms": elapsed_ms,
        "stdout_sha256": _sha256_bytes(stdout),
        "stderr_sha256": _sha256_bytes(stderr),
        "stdout_bytes": len(stdout),
        "stderr_bytes": len(stderr),
    }


def _failure(receipt: Mapping[str, Any], reason: str) -> dict[str, Any]:
    return {
        "kind": receipt["kind"],
        "reason": reason,
        "exit_code": receipt["exit_code"],
        "elapsed_ms": receipt["elapsed_ms"],
        "stdout_sha256": receipt["stdout_sha256"],
        "stderr_sha256": receipt["stderr_sha256"],
        "stdout_bytes": receipt["stdout_bytes"],
        "stderr_bytes": receipt["stderr_bytes"],
    }


def run_canary(
    root: str | Path,
    *,
    resume: bool = False,
    execute: Callable[..., subprocess.CompletedProcess[bytes]] = _default_execute,
    now_ms: Callable[[], int] | None = None,
) -> dict[str, Any]:
    """Run each fixed stage once, checkpointing only closed, validated evidence."""

    run_root = Path(root).resolve()
    plan, _, checkpoint, driver = _load_state(run_root)
    report_path = run_root / REPORT_NAME
    if report_path.exists():
        if not resume:
            raise CanaryRunError("Canary run already produced a final report; use --resume to verify it")
        return checker.evaluate_canary(report_path)
    already_started = checkpoint["started_at_epoch_ms"] is not None
    if already_started and not resume:
        raise CanaryRunError("Canary run has started; use --resume after validating its checkpoint")
    clock = now_ms or (lambda: int(time.time() * 1000))
    if checkpoint["started_at_epoch_ms"] is None:
        checkpoint["started_at_epoch_ms"] = clock()
        _write_json(run_root / "checkpoint.json", checkpoint)
    completed_stages = list(checkpoint["completed_stages"])
    commands = list(checkpoint["commands"])
    start_index = len(completed_stages)
    environment = dict(os.environ)
    environment.update(
        {
            "VIEWSPEC_CANARY_RUN_ID": str(plan["run_id"]),
            "VIEWSPEC_CANARY_DEPLOYMENT_SHA256": str(plan["deployment_sha256"]),
            "VIEWSPEC_CANARY_ORIGIN": str(plan["origin"]),
        }
    )
    for kind in checker.STAGE_KINDS[start_index:]:
        pending = run_root / "stages" / f".{kind}.{plan['run_id']}.pending.json"
        if pending.exists():
            pending.unlink()
        command = (
            sys.executable,
            str(driver),
            "--stage",
            kind,
            "--run-id",
            str(plan["run_id"]),
            "--deployment-sha256",
            str(plan["deployment_sha256"]),
            "--origin",
            str(plan["origin"]),
            "--out",
            str(pending),
        )
        started = time.monotonic()
        try:
            process = execute(command, cwd=driver.parent, environment=environment)
        except (OSError, subprocess.SubprocessError) as exc:
            process = subprocess.CompletedProcess(command, 125, b"", type(exc).__name__.encode("ascii"))
        elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
        receipt = _receipt(kind=kind, command=command, completed=process, elapsed_ms=elapsed_ms)
        failure_reason: str | None = None
        if receipt["stdout_bytes"] > MAX_STREAM_BYTES or receipt["stderr_bytes"] > MAX_STREAM_BYTES:
            failure_reason = "driver_output_exceeded_bound"
        elif process.returncode != 0:
            failure_reason = "driver_exit_nonzero"
        elif not pending.is_file() or pending.is_symlink() or pending.stat().st_size > MAX_STREAM_BYTES:
            failure_reason = "stage_evidence_missing_or_unbounded"
        else:
            try:
                payload = _read_object(pending)
                checker.validate_stage_payload(
                    payload,
                    kind=kind,
                    run_id=str(plan["run_id"]),
                    deployment_sha256=str(plan["deployment_sha256"]),
                )
            except (CanaryRunError, checker.CanaryError):
                failure_reason = "stage_evidence_invalid"
        if failure_reason is not None:
            checkpoint = {
                **checkpoint,
                "completed_stages": completed_stages,
                "commands": commands,
                "failure": _failure(receipt, failure_reason),
            }
            _write_json(run_root / "checkpoint.json", checkpoint)
            if pending.exists():
                pending.unlink()
            raise CanaryRunError(f"Production canary stopped at {kind}: {failure_reason}")
        destination = run_root / "stages" / f"{kind}.json"
        _write_json(destination, payload)
        pending.unlink()
        reference = {
            "kind": kind,
            "path": f"stages/{kind}.json",
            "sha256": _sha256_file(destination),
        }
        completed_stages.append(reference)
        commands.append(receipt)
        checkpoint = {
            **checkpoint,
            "completed_stages": completed_stages,
            "commands": commands,
            "failure": None,
        }
        _write_json(run_root / "checkpoint.json", checkpoint)
    completed_at = clock()
    started_at = int(checkpoint["started_at_epoch_ms"])
    report = {
        "schema_version": checker.SCHEMA_VERSION,
        "verifier_id": checker.VERIFIER_ID,
        "run_id": plan["run_id"],
        "environment": "production",
        "origin": checker.ORIGIN,
        "started_at_epoch_ms": started_at,
        "completed_at_epoch_ms": completed_at,
        "deployment_sha256": plan["deployment_sha256"],
        "stages": completed_stages,
        "commands": commands,
    }
    _write_json(report_path, report)
    try:
        result = checker.evaluate_canary(report_path)
    except checker.CanaryError as exc:
        raise CanaryRunError(f"Final production canary evidence failed verification: {exc}") from exc
    _write_json(run_root / "canary-verification.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    init = subparsers.add_parser("init", help="Freeze one empty production canary run.")
    init.add_argument("--out", required=True, type=Path)
    init.add_argument("--driver", required=True, type=Path)
    init.add_argument("--deployment-sha256", required=True)
    run = subparsers.add_parser("run", help="Execute or resume the fixed canary stages.")
    run.add_argument("--root", required=True, type=Path)
    run.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "init":
            result = initialize_canary(
                args.out,
                driver=args.driver,
                deployment_sha256=args.deployment_sha256,
            )
        else:
            result = run_canary(args.root, resume=args.resume)
    except (CanaryRunError, checker.CanaryError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps({"ok": True, "result": result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
