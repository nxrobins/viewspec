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
    deployment_manifest_sha256,
    evaluate_canary,
)


RUN_ID = "vsrcan_" + "1" * 32
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


def _recovery_payload() -> dict[str, object]:
    snapshot = {
        "snapshot_id_sha256": "9" * 64,
        "source_volume_id_sha256": "5" * 64,
        "provider_observation_sha256": "a" * 64,
        "requested_at_epoch_ms": 1_101_000,
        "completed_at_epoch_ms": 1_120_000,
        "size_bytes": 4096,
        "volume_size_gb": 1,
        "status": "completed",
    }
    return {
        **_common("recovery"),
        "source": {
            "app_id": "viewspec-review",
            "machine_id_sha256": "2" * 64,
            "machine_version_sha256": "4" * 64,
            "image_digest": BUILD_MANIFEST["studio_image_digest"],
            "volume_id_sha256": "5" * 64,
            "region": "sjc",
            "zone_sha256": "7" * 64,
            "size_gb": 1,
            "encrypted": True,
            "inventory_sha256": "6" * 64,
            "provider_observation_sha256": "8" * 64,
        },
        "maintenance_window": {
            "ready_at_epoch_ms": 1_100_000,
            "completed_at_epoch_ms": 1_125_000,
            "max_pause_seconds": 30,
            "guard_id_sha256": "5" * 64,
            "ready_observation_sha256": "4" * 64,
            "request_exclusion_observation_sha256": "a" * 64,
            "health_observation_sha256": "b" * 64,
            "health_observed_at_epoch_ms": 1_115_000,
            "source_inventory_sha256": "6" * 64,
            "final_inventory_sha256": "6" * 64,
            "completion_binding_sha256": _sha256_bytes(_canonical_bytes(snapshot)),
            "ack_sha256": "6" * 64,
            "result_sha256": "7" * 64,
            "non_health_requests_excluded": True,
            "health_available": True,
            "source_unchanged": True,
            "lease_released": True,
        },
        "snapshot": snapshot,
        "restored_volume": {
            "volume_id_sha256": "b" * 64,
            "snapshot_id_sha256": "9" * 64,
            "provider_observation_sha256": "c" * 64,
            "requested_at_epoch_ms": 1_130_000,
            "ready_at_epoch_ms": 1_160_000,
            "region": "sjc",
            "zone_sha256": "d" * 64,
            "size_gb": 1,
            "state": "created",
            "encrypted": True,
            "initially_detached": True,
        },
        "offline_inspection": {
            "machine_id_sha256": "f" * 64,
            "machine_version_sha256": "0" * 64,
            "provider_observation_sha256": "1" * 64,
            "image_digest": BUILD_MANIFEST["studio_image_digest"],
            "volume_id_sha256": "b" * 64,
            "completed_at_epoch_ms": 1_200_000,
            "source_inventory_sha256": "6" * 64,
            "restored_inventory_sha256": "6" * 64,
            "build": {
                key: BUILD_MANIFEST[key]
                for key in ("schema_version", "source_revision", "public_sdk_revision", "public_sdk_wheel_sha256")
            },
            "network_service_count": 0,
            "normal_startup_reconciliation_count": 0,
            "storage_verified": True,
            "receipt_key_rotation_passed": True,
        },
        "lifecycle": {
            "evidence_sha256": "2" * 64,
            "restart_recovery_passed": True,
            "idempotent_retry_passed": True,
            "expiry_passed": True,
            "reviewer_rotation_passed": True,
            "revocation_passed": True,
            "deletion_passed": True,
            "orphan_usable_session_count": 0,
            "duplicate_session_count": 0,
            "sensitive_telemetry_field_count": 0,
        },
        "cleanup": {
            "provider_observation_sha256": "3" * 64,
            "inspection_machine_destroyed": True,
            "restored_volume_destroyed": True,
            "source_machine_unchanged": True,
            "source_volume_unchanged": True,
        },
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
                    "machine_id_sha256": character * 64,
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
        return _recovery_payload()
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


def test_deployment_accepts_distinct_role_images_bound_to_one_reviewed_build(tmp_path: Path) -> None:
    assert BUILD_MANIFEST["api_image_digest"] != BUILD_MANIFEST["studio_image_digest"]
    assert evaluate_canary(_canary(tmp_path))["deployment_sha256"] == DEPLOYMENT_SHA
    assert deployment_manifest_sha256(dict(reversed(list(BUILD_MANIFEST.items())))) == DEPLOYMENT_SHA


@pytest.mark.parametrize("app_index", [0, 1, 2])
def test_an_approved_image_cannot_be_used_in_the_wrong_role(tmp_path, app_index):
    report = _canary(tmp_path)
    wrong_image = BUILD_MANIFEST["studio_image_digest" if app_index == 0 else "api_image_digest"]
    _mutate_stage(report, "deployment", lambda value: value["apps"][app_index].update({"image_digest": wrong_image}))
    with pytest.raises(CanaryError, match="role-specific build"):
        evaluate_canary(report)


@pytest.mark.parametrize("app_index", [0, 1, 2])
@pytest.mark.parametrize("field,replacement", [
    ("image_digest", "sha256:" + "f" * 64),
    ("source_revision", "f" * 40),
    ("public_sdk_revision", "f" * 40),
    ("public_sdk_wheel_sha256", "f" * 64),
])
def test_each_live_role_must_match_frozen_provenance(tmp_path, app_index, field, replacement):
    report = _canary(tmp_path)
    _mutate_stage(report, "deployment", lambda value: value["apps"][app_index].update({field: replacement}))
    with pytest.raises(CanaryError, match="differs from its frozen"):
        evaluate_canary(report)


@pytest.mark.parametrize("field", [
    "source_revision", "public_sdk_revision", "public_sdk_wheel_sha256", "api_image_digest", "studio_image_digest",
])
def test_rebinding_manifest_and_live_roles_cannot_replace_frozen_build(tmp_path, field):
    report = _canary(tmp_path)
    def mutate(value):
        replacement = "sha256:" + "f" * 64 if field.endswith("image_digest") else "f" * len(BUILD_MANIFEST[field])
        value["build_manifest"][field] = replacement
        for app in value["apps"]:
            if field in app:
                app[field] = replacement
            elif (field == "api_image_digest") == (app["app_id"] == "viewspec-api"):
                app["image_digest"] = replacement
    _mutate_stage(report, "deployment", mutate)
    with pytest.raises(CanaryError, match="does not match the frozen deployment hash"):
        evaluate_canary(report)


@pytest.mark.parametrize("change", [
    {"schema_version": True}, {"schema_version": 2}, {"source_revision": "main"},
    {"public_sdk_revision": "latest"}, {"public_sdk_wheel_sha256": "not-a-hash"},
    {"api_image_digest": "registry/app:latest"}, {"studio_image_digest": "sha256:" + "E" * 64},
    {"unknown": True},
])
def test_build_manifest_is_closed_and_immutable(change):
    with pytest.raises(CanaryError):
        deployment_manifest_sha256({**BUILD_MANIFEST, **change})


def test_legacy_same_image_evidence_without_build_manifest_cannot_pass(tmp_path):
    report = _canary(tmp_path)
    _mutate_stage(report, "deployment", lambda value: value.pop("build_manifest"))
    with pytest.raises(CanaryError, match="shape mismatch"):
        evaluate_canary(report)


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


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda value: value["source"].update({"machine_id_sha256": "e" * 64}),
            "deployed review Machine",
        ),
        (
            lambda value: value["snapshot"].update({"source_volume_id_sha256": "f" * 64}),
            "snapshot source volume differs",
        ),
        (
            lambda value: value["maintenance_window"].update({"final_inventory_sha256": "f" * 64}),
            "maintenance inventory changed",
        ),
        (
            lambda value: value["maintenance_window"].update({"health_observation_sha256": "invalid"}),
            "health_observation_sha256",
        ),
        (
            lambda value: value["maintenance_window"].update({"health_observed_at_epoch_ms": 1_126_000}),
            "not observed inside the maintenance window",
        ),
        (
            lambda value: value["snapshot"].update({"completed_at_epoch_ms": 1_126_000}),
            "inside the held maintenance window",
        ),
        (
            lambda value: value["maintenance_window"].update({"completion_binding_sha256": "f" * 64}),
            "not bound to the snapshot evidence",
        ),
        (
            lambda value: value["snapshot"].update({"status": "pending"}),
            "snapshot is not completed",
        ),
        (
            lambda value: value["restored_volume"].update({"snapshot_id_sha256": "f" * 64}),
            "uses the wrong snapshot",
        ),
        (
            lambda value: value["restored_volume"].update({"volume_id_sha256": "5" * 64}),
            "use a new volume",
        ),
        (
            lambda value: value["restored_volume"].update({"region": "iad"}),
            "region differs",
        ),
        (
            lambda value: value["restored_volume"].update({"state": "restoring"}),
            "not ready",
        ),
        (
            lambda value: value["offline_inspection"].update({"volume_id_sha256": "f" * 64}),
            "wrong restored volume",
        ),
        (
            lambda value: value["offline_inspection"].update({"restored_inventory_sha256": "f" * 64}),
            "inventory differs",
        ),
        (
            lambda value: value["offline_inspection"]["build"].update({"source_revision": "f" * 40}),
            "frozen deployment",
        ),
        (
            lambda value: value["offline_inspection"].update({"normal_startup_reconciliation_count": 1}),
            "startup reconciliation count",
        ),
        (
            lambda value: value["lifecycle"].update({"restart_recovery_passed": False}),
            "restart_recovery_passed",
        ),
        (
            lambda value: value["cleanup"].update({"restored_volume_destroyed": False}),
            "restored_volume_destroyed",
        ),
    ],
)
def test_recovery_requires_exact_provider_lineage(tmp_path: Path, mutate, message: str) -> None:
    report_path = _canary(tmp_path)
    _mutate_stage(report_path, "recovery", mutate)

    with pytest.raises(CanaryError, match=message):
        evaluate_canary(report_path)


def test_legacy_recovery_assertion_booleans_cannot_pass(tmp_path: Path) -> None:
    report_path = _canary(tmp_path)

    def legacy(value):
        for field in (
            "source",
            "maintenance_window",
            "snapshot",
            "restored_volume",
            "offline_inspection",
            "lifecycle",
            "cleanup",
        ):
            value.pop(field)
        value.update(
            {
                "backup_restore_passed": True,
                "receipt_key_rotation_passed": True,
                "storage_verification_passed": True,
            }
        )

    _mutate_stage(report_path, "recovery", legacy)
    with pytest.raises(CanaryError, match="recovery stage shape mismatch"):
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
