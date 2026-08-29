from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.check_studio_production_canary import (
    CanaryError,
    STAGE_KINDS,
    _canonical_bytes,
    _sha256_bytes,
    build_share_release_payload,
    evaluate_canary,
)


RUN_ID = "vsrcan_" + "1" * 32
DEPLOYMENT_SHA = "d" * 64


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
            "apps": [
                {
                    "app_id": app_id,
                    "machine_id_sha256": character * 64,
                    "image_digest": "sha256:" + "e" * 64,
                    "public_https": public,
                    "durable_volume": volume,
                    "forbidden_secret_count": 0,
                }
                for app_id, character, public, volume in (
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
                name: {"local_sha256": character * 64, "remote_sha256": character * 64}
                for name, character in (
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
        engine = kind.removeprefix("browser-")
        return {
            **_common(kind),
            "engine": engine,
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


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical_bytes(value))


def _canary(tmp_path: Path) -> Path:
    stages = []
    for kind in STAGE_KINDS:
        path = tmp_path / "stages" / f"{kind}.json"
        _write_json(path, _payload(kind))
        stages.append(
            {
                "kind": kind,
                "path": f"stages/{kind}.json",
                "sha256": _sha256_bytes(path.read_bytes()),
            }
        )
    report = {
        "schema_version": 1,
        "verifier_id": "viewspec-studio-production-canary-v1",
        "run_id": RUN_ID,
        "environment": "production",
        "origin": "https://review.viewspec.dev",
        "started_at_epoch_ms": 1_000_000,
        "completed_at_epoch_ms": 1_600_000,
        "deployment_sha256": DEPLOYMENT_SHA,
        "stages": stages,
        "commands": [
            {
                "kind": kind,
                "argv_sha256": _sha256_bytes(kind.encode()),
                "exit_code": 0,
                "elapsed_ms": 10_000,
                "stdout_sha256": _sha256_bytes(b""),
                "stderr_sha256": _sha256_bytes(b""),
                "stdout_bytes": 0,
                "stderr_bytes": 0,
            }
            for kind in STAGE_KINDS
        ],
    }
    report_path = tmp_path / "production-canary-evidence.json"
    _write_json(report_path, report)
    return report_path


def _mutate_stage(report_path: Path, kind: str, mutate) -> None:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    reference = next(item for item in report["stages"] if item["kind"] == kind)
    stage_path = report_path.parent / reference["path"]
    stage = json.loads(stage_path.read_text(encoding="utf-8"))
    mutate(stage)
    _write_json(stage_path, stage)
    reference["sha256"] = _sha256_bytes(stage_path.read_bytes())
    _write_json(report_path, report)


def test_complete_production_canary_passes_all_conjunctive_gates(tmp_path: Path) -> None:
    result = evaluate_canary(_canary(tmp_path))

    assert result["passed"] is True
    assert all(result["checks"].values())
    assert list(result["stage_sha256"]) == list(STAGE_KINDS)


def test_passing_canary_builds_one_short_lived_hash_bound_share_release_payload(tmp_path: Path) -> None:
    result = evaluate_canary(_canary(tmp_path))
    release = build_share_release_payload(result, issued_at_epoch_s=2_000_000_000, lifetime_seconds=900)

    assert release == {
        "schema_version": 1,
        "kind": "studio_share_release",
        "status": "active",
        "api_origin": "https://api.viewspec.dev",
        "review_origin": "https://review.viewspec.dev",
        "deployment_sha256": DEPLOYMENT_SHA,
        "verifier_id": "viewspec-studio-production-canary-v1",
        "run_id": RUN_ID,
        "report_sha256": result["report_sha256"],
        "checks": result["checks"],
        "issued_at_epoch_s": 2_000_000_000,
        "expires_at_epoch_s": 2_000_000_900,
    }

    failed = {**result, "passed": False}
    with pytest.raises(CanaryError, match="complete passing"):
        build_share_release_payload(failed, issued_at_epoch_s=2_000_000_000)

    with pytest.raises(CanaryError, match="lifetime"):
        build_share_release_payload(result, issued_at_epoch_s=2_000_000_000, lifetime_seconds=3601)


@pytest.mark.parametrize(
    ("kind", "mutate", "message"),
    [
        (
            "deployment",
            lambda value: value["apps"][2].update({"public_https": True}),
            "public HTTPS boundary",
        ),
        (
            "ingress",
            lambda value: value["identities"]["source"].update({"remote_sha256": "f" * 64}),
            "differs locally and remotely",
        ),
        ("rebuild", lambda value: value.update({"install_used": True}), "dependency installation"),
        (
            "isolation",
            lambda value: value.update({"cgroup_memory_limit_bytes": 513 * 1024 * 1024}),
            "cgroup memory limit",
        ),
        (
            "browser-firefox",
            lambda value: value.update({"console_error_count": 1}),
            "console_error_count",
        ),
        (
            "recovery",
            lambda value: value.update({"backup_restore_passed": False}),
            "backup_restore_passed",
        ),
        (
            "leak-audit",
            lambda value: value.update({"capability_hits": 1}),
            "capability_hits",
        ),
    ],
)
def test_canary_rejects_semantically_failed_stage_evidence(
    tmp_path: Path,
    kind: str,
    mutate,
    message: str,
) -> None:
    report_path = _canary(tmp_path)
    _mutate_stage(report_path, kind, mutate)

    with pytest.raises(CanaryError, match=message):
        evaluate_canary(report_path)


def test_canary_rejects_stage_tampering_even_when_json_remains_valid(tmp_path: Path) -> None:
    report_path = _canary(tmp_path)
    stage = tmp_path / "stages" / "rebuild.json"
    stage.write_bytes(stage.read_bytes() + b" ")

    with pytest.raises(CanaryError, match="bytes changed"):
        evaluate_canary(report_path)


def test_canary_rejects_command_failure_or_missing_browser_stage(tmp_path: Path) -> None:
    report_path = _canary(tmp_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["commands"][0]["exit_code"] = 1
    _write_json(report_path, report)
    with pytest.raises(CanaryError, match="exit zero"):
        evaluate_canary(report_path)

    report_path = _canary(tmp_path / "missing")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["stages"] = [item for item in report["stages"] if item["kind"] != "browser-webkit"]
    _write_json(report_path, report)
    with pytest.raises(CanaryError, match="canonical order"):
        evaluate_canary(report_path)


def test_canary_report_shape_is_closed(tmp_path: Path) -> None:
    report_path = _canary(tmp_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["claim"] = "production ready"
    _write_json(report_path, report)

    with pytest.raises(CanaryError, match="shape mismatch"):
        evaluate_canary(report_path)
