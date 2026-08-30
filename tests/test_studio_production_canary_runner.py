from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from scripts.check_studio_production_canary import STAGE_KINDS, deployment_manifest_sha256, evaluate_canary
from scripts.run_studio_production_canary import (
    CanaryRunError,
    initialize_canary,
    run_canary,
)


RUN_ID = "vsrcan_" + "2" * 32
BUILD_MANIFEST = {
    "schema_version": 1,
    "source_revision": "a" * 40,
    "public_sdk_revision": "b" * 40,
    "public_sdk_wheel_sha256": "c" * 64,
    "api_image_digest": "sha256:" + "d" * 64,
    "studio_image_digest": "sha256:" + "e" * 64,
}
DEPLOYMENT_SHA = deployment_manifest_sha256(BUILD_MANIFEST)


def _common(kind: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": kind,
        "run_id": RUN_ID,
        "deployment_sha256": DEPLOYMENT_SHA,
    }


def _payload(kind: str) -> dict[str, object]:
    if kind == "deployment":
        return {
            **_common(kind),
            "origin": "https://review.viewspec.dev",
            "build_manifest": dict(BUILD_MANIFEST),
            "apps": [
                {
                    "app_id": app_id,
                    "machine_id_sha256": marker * 64,
                    "image_digest": BUILD_MANIFEST[
                        "api_image_digest" if app_id == "viewspec-api" else "studio_image_digest"
                    ],
                    "source_revision": BUILD_MANIFEST["source_revision"],
                    "public_sdk_revision": BUILD_MANIFEST["public_sdk_revision"],
                    "public_sdk_wheel_sha256": BUILD_MANIFEST["public_sdk_wheel_sha256"],
                    "public_https": public,
                    "durable_volume": volume,
                    "forbidden_secret_count": 0,
                }
                for app_id, marker, public, volume in (
                    ("viewspec-api", "1", True, True),
                    ("viewspec-review", "2", True, True),
                    ("viewspec-review-worker", "3", False, False),
                )
            ],
            "secret_boundary_checks": {
                "api_review_hmac_separate_from_review_worker_hmac": True,
                "capability_and_receipt_keys_separate": True,
                "worker_has_only_worker_hmac": True,
            },
        }
    if kind == "ingress":
        return {
            **_common(kind),
            "identities": {
                name: {"local_sha256": marker * 64, "remote_sha256": marker * 64}
                for name, marker in (
                    ("archive", "1"),
                    ("inspection", "2"),
                    ("package", "3"),
                    ("react_artifact_set", "4"),
                    ("root_manifest", "5"),
                    ("source", "6"),
                    ("static_artifact_set", "7"),
                )
            },
            "mismatch_rejected_before_session": True,
            "paid_key_forwarded": False,
            "internal_request_authenticated": True,
            "internal_response_authenticated": True,
            "idempotent_session_count": 1,
        }
    if kind == "rebuild":
        return {
            **_common(kind),
            "rebuild_evidence_sha256": "8" * 64,
            "expected_inventory_sha256": "9" * 64,
            "observed_inventory_sha256": "9" * 64,
            "source_only_request": True,
            "install_used": False,
            "lifecycle_hooks_disabled": True,
            "uploaded_artifacts_executed": False,
            "mismatch_rejected": True,
        }
    if kind == "isolation":
        return {
            **_common(kind),
            "runner_receipt_sha256": "a" * 64,
            "bubblewrap_path_verified": True,
            "network_namespace_new": True,
            "pid_namespace_new": True,
            "ipc_namespace_new": True,
            "uts_namespace_new": True,
            "egress_canary_denied": True,
            "lifecycle_hook_canary_denied": True,
            "arbitrary_command_canary_denied": True,
            "cpu_seconds_observed": 20,
            "cgroup_memory_limit_bytes": 512 * 1024 * 1024,
            "wall_time_seconds": 90,
            "file_limit_enforced": True,
            "byte_limit_enforced": True,
            "forbidden_environment_count": 0,
        }
    if kind.startswith("browser-"):
        return {
            **_common(kind),
            "engine": kind.removeprefix("browser-"),
            "probe_sha256": "b" * 64,
            "playwright_lock_sha256": "c" * 64,
            "elapsed_ms": 120_000,
            "create_passed": True,
            "anonymous_access_denied": True,
            "fragment_removed": True,
            "secure_http_only_same_site_cookie": True,
            "static_target_passed": True,
            "react_target_passed": True,
            "semantic_comment_acknowledged": True,
            "reviewer_approval_denied": True,
            "owner_approval_passed": True,
            "receipt_signature_verified": True,
            "external_request_count": 0,
            "capability_leak_count": 0,
            "csp_violation_count": 0,
            "console_error_count": 0,
        }
    if kind == "recovery":
        return {
            **_common(kind),
            "restart_recovery_passed": True,
            "idempotent_retry_passed": True,
            "expiry_passed": True,
            "reviewer_rotation_passed": True,
            "revocation_passed": True,
            "deletion_passed": True,
            "backup_restore_passed": True,
            "receipt_key_rotation_passed": True,
            "storage_verification_passed": True,
            "orphan_usable_session_count": 0,
            "duplicate_session_count": 0,
            "sensitive_telemetry_field_count": 0,
        }
    if kind == "leak-audit":
        return {
            **_common(kind),
            "authorization_header_hits": 0,
            "capability_hits": 0,
            "comment_body_hits": 0,
            "cookie_hits": 0,
            "external_request_count": 0,
            "fixture_value_hits": 0,
            "request_url_secret_hits": 0,
            "semantic_source_hits": 0,
        }
    raise AssertionError(kind)


def _initialized(tmp_path: Path) -> tuple[Path, Path]:
    driver = tmp_path / "driver.py"
    driver.write_text("# immutable test driver\n", encoding="utf-8")
    root = tmp_path / "canary"
    manifest = tmp_path / "reviewed-build.json"
    manifest.write_text(json.dumps(BUILD_MANIFEST), encoding="utf-8")
    initialize_canary(
        root,
        driver=driver,
        deployment_manifest=manifest,
        run_id=RUN_ID,
        now_ms=1_000_000,
    )
    return root, driver


def _executor(*, fail_once: str | None = None, invalid: str | None = None):
    calls: dict[str, int] = {}

    def execute(command, *, cwd, environment):
        del cwd
        kind = command[command.index("--stage") + 1]
        output = Path(command[command.index("--out") + 1])
        assert environment["VIEWSPEC_CANARY_RUN_ID"] == RUN_ID
        frozen = Path(command[command.index("--deployment-manifest") + 1])
        assert json.loads(frozen.read_text()) == BUILD_MANIFEST
        calls[kind] = calls.get(kind, 0) + 1
        if fail_once == kind and calls[kind] == 1:
            return subprocess.CompletedProcess(command, 7, b"canary-secret", b"private-detail")
        payload = _payload(kind)
        if invalid == kind:
            payload["unknown"] = True
        output.write_text(json.dumps(payload), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, b"canary-secret", b"private-detail")

    return execute, calls


def test_runner_produces_a_complete_verifiable_canary_without_retaining_streams(tmp_path: Path) -> None:
    root, _ = _initialized(tmp_path)
    execute, calls = _executor()

    result = run_canary(root, execute=execute, now_ms=lambda: 1_600_000)

    assert result["passed"] is True
    assert list(calls) == list(STAGE_KINDS)
    assert evaluate_canary(root / "production-canary-evidence.json")["passed"] is True
    retained = "".join(path.read_text(encoding="utf-8") for path in root.rglob("*.json"))
    assert "canary-secret" not in retained
    assert "private-detail" not in retained


def test_initialization_freezes_canonical_manifest_not_a_mutable_input_path(tmp_path):
    root, _ = _initialized(tmp_path)
    frozen = root / "deployment-manifest.json"
    assert frozen.read_text() == json.dumps(BUILD_MANIFEST, indent=2, sort_keys=True) + "\n"
    (tmp_path / "reviewed-build.json").write_text("{}")
    execute, calls = _executor()
    assert run_canary(root, execute=execute, now_ms=lambda: 1_600_000)["passed"] is True
    assert list(calls) == list(STAGE_KINDS)


def test_real_stage_process_receives_frozen_manifest_and_retains_no_streams(tmp_path):
    # Synthetic stages test process plumbing only, never production readiness.
    driver = tmp_path / "synthetic-driver.py"
    payloads = {kind: _payload(kind) for kind in STAGE_KINDS}
    driver.write_text(
        "import argparse,json\nfrom pathlib import Path\n"
        "parser=argparse.ArgumentParser()\n"
        "for name in ('stage','run-id','deployment-sha256','deployment-manifest','origin','out'):\n"
        "    parser.add_argument('--'+name, required=True)\n"
        "args=parser.parse_args()\n"
        f"payloads={payloads!r}\n"
        "manifest=json.loads(Path(args.deployment_manifest).read_text())\n"
        "assert manifest==payloads['deployment']['build_manifest']\n"
        "assert args.deployment_sha256==payloads['deployment']['deployment_sha256']\n"
        "Path(args.out).write_text(json.dumps(payloads[args.stage]))\n"
        "print('synthetic-private-stream')\n"
    )
    manifest = tmp_path / "build.json"
    manifest.write_text(json.dumps(BUILD_MANIFEST))
    root = tmp_path / "canary"
    initialize_canary(root, driver=driver, deployment_manifest=manifest, run_id=RUN_ID, now_ms=1_000_000)
    result = run_canary(root, now_ms=lambda: 1_600_000)
    assert result["passed"] is True
    assert result["deployment_sha256"] == DEPLOYMENT_SHA
    assert "synthetic-private-stream" not in "".join(path.read_text() for path in root.rglob("*.json"))


@pytest.mark.parametrize("after_checkpoint", [False, True])
@pytest.mark.parametrize("mutation", ["bytes", "build", "missing", "symlink"])
def test_changed_frozen_manifest_blocks_execution_and_resume(tmp_path, mutation, after_checkpoint):
    root, _ = _initialized(tmp_path)
    if after_checkpoint:
        with pytest.raises(CanaryRunError, match="stopped at ingress"):
            run_canary(root, execute=_executor(fail_once="ingress")[0])
    frozen = root / "deployment-manifest.json"
    if mutation == "bytes":
        frozen.write_bytes(frozen.read_bytes() + b" ")
    elif mutation == "build":
        frozen.write_text(json.dumps({**BUILD_MANIFEST, "source_revision": "f" * 40}))
    else:
        frozen.unlink()
        if mutation == "symlink":
            frozen.symlink_to(tmp_path / "reviewed-build.json")
    execute, calls = _executor()
    with pytest.raises(CanaryRunError, match="[Mm]anifest"):
        run_canary(root, resume=after_checkpoint, execute=execute)
    assert calls == {}


@pytest.mark.parametrize("mutation", ["missing", "oversize", "symlink", "mutable", "unknown"])
def test_invalid_build_input_creates_no_run(tmp_path, mutation):
    driver = tmp_path / "driver.py"
    driver.write_text("# test driver")
    manifest = tmp_path / "build.json"
    if mutation == "oversize":
        manifest.write_text(" " * (16 * 1024 + 1))
    elif mutation == "symlink":
        target = tmp_path / "target.json"
        target.write_text(json.dumps(BUILD_MANIFEST))
        manifest.symlink_to(target)
    elif mutation != "missing":
        change = {"source_revision": "main"} if mutation == "mutable" else {"unknown": True}
        manifest.write_text(json.dumps({**BUILD_MANIFEST, **change}))
    root = tmp_path / "canary"
    from scripts.check_studio_production_canary import CanaryError
    with pytest.raises((CanaryRunError, CanaryError)):
        initialize_canary(root, driver=driver, deployment_manifest=manifest)
    assert not root.exists()


def test_runner_resumes_only_after_validating_every_completed_stage(tmp_path: Path) -> None:
    root, _ = _initialized(tmp_path)
    first_execute, first_calls = _executor(fail_once="ingress")
    with pytest.raises(CanaryRunError, match="stopped at ingress"):
        run_canary(root, execute=first_execute, now_ms=lambda: 1_100_000)
    assert list(first_calls) == ["deployment", "ingress"]

    second_execute, second_calls = _executor()
    result = run_canary(root, resume=True, execute=second_execute, now_ms=lambda: 1_600_000)

    assert result["passed"] is True
    assert "deployment" not in second_calls
    assert list(second_calls) == list(STAGE_KINDS[1:])


def test_runner_rejects_driver_change_before_execution(tmp_path: Path) -> None:
    root, driver = _initialized(tmp_path)
    driver.write_text("# changed test driver\n", encoding="utf-8")

    with pytest.raises(CanaryRunError, match="driver, runner, verifier, or lock hash changed"):
        run_canary(root, execute=_executor()[0])


def test_runner_rejects_completed_stage_tampering_on_resume(tmp_path: Path) -> None:
    root, _ = _initialized(tmp_path)
    execute, _ = _executor(fail_once="ingress")
    with pytest.raises(CanaryRunError):
        run_canary(root, execute=execute)
    stage = root / "stages" / "deployment.json"
    stage.write_bytes(stage.read_bytes() + b" ")

    with pytest.raises(CanaryRunError, match="evidence hash changed"):
        run_canary(root, resume=True, execute=_executor()[0])


def test_runner_rejects_invalid_stage_before_promotion(tmp_path: Path) -> None:
    root, _ = _initialized(tmp_path)

    with pytest.raises(CanaryRunError, match="stage_evidence_invalid"):
        run_canary(root, execute=_executor(invalid="deployment")[0])

    checkpoint = json.loads((root / "checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["completed_stages"] == []
    assert checkpoint["failure"]["reason"] == "stage_evidence_invalid"
    assert not (root / "stages" / "deployment.json").exists()


def test_started_run_requires_explicit_resume(tmp_path: Path) -> None:
    root, _ = _initialized(tmp_path)
    execute, _ = _executor(fail_once="deployment")
    with pytest.raises(CanaryRunError):
        run_canary(root, execute=execute)

    with pytest.raises(CanaryRunError, match="use --resume"):
        run_canary(root, execute=_executor()[0])
