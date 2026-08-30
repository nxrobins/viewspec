#!/usr/bin/env python3
"""Verify the complete deployed Studio private-review canary evidence tree."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any, Mapping


SCHEMA_VERSION = 1
VERIFIER_ID = "viewspec-studio-production-canary-v1"
ORIGIN = "https://review.viewspec.dev"
API_ORIGIN = "https://api.viewspec.dev"
SHARE_RELEASE_KIND = "studio_share_release"
SHARE_RELEASE_MAX_LIFETIME_SECONDS = 60 * 60
RUN_ID_RE = re.compile(r"^vsrcan_[0-9a-f]{32}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IMAGE_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
REVISION_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
BUILD_MANIFEST_FIELDS = {
    "schema_version",
    "source_revision",
    "public_sdk_revision",
    "public_sdk_wheel_sha256",
    "api_image_digest",
    "studio_image_digest",
}
STAGE_KINDS = (
    "deployment",
    "ingress",
    "rebuild",
    "isolation",
    "browser-chromium",
    "browser-firefox",
    "browser-webkit",
    "recovery",
    "leak-audit",
)
REPORT_FIELDS = {
    "schema_version",
    "verifier_id",
    "run_id",
    "environment",
    "origin",
    "started_at_epoch_ms",
    "completed_at_epoch_ms",
    "deployment_sha256",
    "stages",
    "commands",
}
STAGE_REF_FIELDS = {"kind", "path", "sha256"}
COMMAND_FIELDS = {
    "kind",
    "argv_sha256",
    "exit_code",
    "elapsed_ms",
    "stdout_sha256",
    "stderr_sha256",
    "stdout_bytes",
    "stderr_bytes",
}
COMMON_STAGE_FIELDS = {"schema_version", "kind", "run_id", "deployment_sha256"}
APP_FIELDS = {
    "app_id",
    "machine_id_sha256",
    "image_digest",
    "source_revision",
    "public_sdk_revision",
    "public_sdk_wheel_sha256",
    "public_https",
    "durable_volume",
    "forbidden_secret_count",
}
DEPLOYMENT_FIELDS = COMMON_STAGE_FIELDS | {"origin", "build_manifest", "apps", "secret_boundary_checks"}
SECRET_BOUNDARY_CHECKS = {
    "api_review_hmac_separate_from_review_worker_hmac",
    "capability_and_receipt_keys_separate",
    "worker_has_only_worker_hmac",
}
INGRESS_FIELDS = COMMON_STAGE_FIELDS | {
    "identities",
    "mismatch_rejected_before_session",
    "paid_key_forwarded",
    "internal_request_authenticated",
    "internal_response_authenticated",
    "idempotent_session_count",
}
IDENTITY_FIELDS = {"local_sha256", "remote_sha256"}
IDENTITY_NAMES = {
    "archive",
    "inspection",
    "package",
    "react_artifact_set",
    "root_manifest",
    "source",
    "static_artifact_set",
}
REBUILD_FIELDS = COMMON_STAGE_FIELDS | {
    "rebuild_evidence_sha256",
    "expected_inventory_sha256",
    "observed_inventory_sha256",
    "source_only_request",
    "install_used",
    "lifecycle_hooks_disabled",
    "uploaded_artifacts_executed",
    "mismatch_rejected",
}
ISOLATION_FIELDS = COMMON_STAGE_FIELDS | {
    "runner_receipt_sha256",
    "bubblewrap_path_verified",
    "network_namespace_new",
    "pid_namespace_new",
    "ipc_namespace_new",
    "uts_namespace_new",
    "egress_canary_denied",
    "lifecycle_hook_canary_denied",
    "arbitrary_command_canary_denied",
    "cpu_seconds_observed",
    "cgroup_memory_limit_bytes",
    "wall_time_seconds",
    "file_limit_enforced",
    "byte_limit_enforced",
    "forbidden_environment_count",
}
BROWSER_FIELDS = COMMON_STAGE_FIELDS | {
    "engine",
    "probe_sha256",
    "playwright_lock_sha256",
    "elapsed_ms",
    "create_passed",
    "anonymous_access_denied",
    "fragment_removed",
    "secure_http_only_same_site_cookie",
    "static_target_passed",
    "react_target_passed",
    "semantic_comment_acknowledged",
    "reviewer_approval_denied",
    "owner_approval_passed",
    "receipt_signature_verified",
    "external_request_count",
    "capability_leak_count",
    "csp_violation_count",
    "console_error_count",
}
RECOVERY_FIELDS = COMMON_STAGE_FIELDS | {
    "restart_recovery_passed",
    "idempotent_retry_passed",
    "expiry_passed",
    "reviewer_rotation_passed",
    "revocation_passed",
    "deletion_passed",
    "backup_restore_passed",
    "receipt_key_rotation_passed",
    "storage_verification_passed",
    "orphan_usable_session_count",
    "duplicate_session_count",
    "sensitive_telemetry_field_count",
}
LEAK_FIELDS = COMMON_STAGE_FIELDS | {
    "authorization_header_hits",
    "capability_hits",
    "comment_body_hits",
    "cookie_hits",
    "external_request_count",
    "fixture_value_hits",
    "request_url_secret_hits",
    "semantic_source_hits",
}


class CanaryError(ValueError):
    """Raised when deployed canary evidence is malformed, incomplete, or inconsistent."""


def _canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CanaryError(f"Could not read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CanaryError(f"{path} must contain one JSON object")
    return value


def _exact(value: Mapping[str, Any], fields: set[str], noun: str) -> None:
    if set(value) != fields:
        missing = sorted(fields - set(value))
        unknown = sorted(set(value) - fields)
        raise CanaryError(f"{noun} shape mismatch; missing={missing}, unknown={unknown}")


def _sha(value: object, noun: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise CanaryError(f"{noun} must be one lowercase SHA-256")
    return value


def _bounded_int(value: object, noun: str, *, maximum: int | None = None) -> int:
    if type(value) is not int or value < 0 or (maximum is not None and value > maximum):
        raise CanaryError(f"{noun} must be a non-negative bounded integer")
    return value


def _true(value: object, noun: str) -> None:
    if value is not True:
        raise CanaryError(f"{noun} must be true")


def _false(value: object, noun: str) -> None:
    if value is not False:
        raise CanaryError(f"{noun} must be false")


def _zero(value: object, noun: str) -> None:
    if value != 0 or type(value) is not int:
        raise CanaryError(f"{noun} must be zero")


def _safe_stage(root: Path, reference: Mapping[str, Any]) -> tuple[Path, dict[str, Any]]:
    _exact(reference, STAGE_REF_FIELDS, "canary stage reference")
    relative = reference.get("path")
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise CanaryError("Canary stage path must be a non-empty evidence-relative path")
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise CanaryError("Canary stage path escapes the evidence root") from exc
    if not path.is_file() or path.is_symlink():
        raise CanaryError("Canary stage path must identify one regular file")
    expected = _sha(reference.get("sha256"), "canary stage reference sha256")
    if _sha256_bytes(path.read_bytes()) != expected:
        raise CanaryError(f"Canary stage bytes changed: {path}")
    return path, _read_object(path)


def _common(payload: Mapping[str, Any], *, kind: str, run_id: str, deployment_sha256: str) -> None:
    if (
        payload.get("schema_version") != SCHEMA_VERSION
        or payload.get("kind") != kind
        or payload.get("run_id") != run_id
        or payload.get("deployment_sha256") != deployment_sha256
    ):
        raise CanaryError(f"{kind} stage is not bound to this run and deployment")


def deployment_manifest_sha256(manifest: object) -> str:
    """Validate and identify the exact two-image build approved for one deployment."""
    if not isinstance(manifest, dict):
        raise CanaryError("Deployment build manifest must be an object")
    _exact(manifest, BUILD_MANIFEST_FIELDS, "deployment build manifest")
    if type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
        raise CanaryError("Deployment build manifest version is invalid")
    for field in ("source_revision", "public_sdk_revision"):
        value = manifest.get(field)
        if not isinstance(value, str) or REVISION_RE.fullmatch(value) is None:
            raise CanaryError(f"Deployment {field} must be one immutable Git revision")
    _sha(manifest.get("public_sdk_wheel_sha256"), "deployment SDK wheel hash")
    for field in ("api_image_digest", "studio_image_digest"):
        value = manifest.get(field)
        if not isinstance(value, str) or IMAGE_DIGEST_RE.fullmatch(value) is None:
            raise CanaryError(f"Deployment {field} must be one immutable sha256 digest")
    return _sha256_bytes(_canonical_bytes(manifest))


def _deployment(payload: Mapping[str, Any], *, run_id: str, deployment_sha256: str) -> None:
    _exact(payload, DEPLOYMENT_FIELDS, "deployment stage")
    _common(payload, kind="deployment", run_id=run_id, deployment_sha256=deployment_sha256)
    if payload.get("origin") != ORIGIN:
        raise CanaryError("Deployment stage must bind the canonical review origin")
    manifest = payload.get("build_manifest")
    if deployment_manifest_sha256(manifest) != deployment_sha256:
        raise CanaryError("Deployment build manifest does not match the frozen deployment hash")
    apps = payload.get("apps")
    if not isinstance(apps, list) or len(apps) != 3:
        raise CanaryError("Deployment stage must record exactly three apps")
    by_id: dict[str, Mapping[str, Any]] = {}
    for app in apps:
        if not isinstance(app, dict):
            raise CanaryError("Deployment app record must be an object")
        _exact(app, APP_FIELDS, "deployment app")
        app_id = app.get("app_id")
        if app_id not in {"viewspec-api", "viewspec-review", "viewspec-review-worker"} or app_id in by_id:
            raise CanaryError("Deployment app identity is missing, duplicated, or unsupported")
        _sha(app.get("machine_id_sha256"), "deployment machine id hash")
        digest = app.get("image_digest")
        if not isinstance(digest, str) or IMAGE_DIGEST_RE.fullmatch(digest) is None:
            raise CanaryError("Deployment image digest must be one immutable sha256 digest")
        expected_image = manifest["api_image_digest" if app_id == "viewspec-api" else "studio_image_digest"]
        if digest != expected_image:
            raise CanaryError(f"{app_id} image differs from its frozen role-specific build")
        for field in ("source_revision", "public_sdk_revision", "public_sdk_wheel_sha256"):
            if app.get(field) != manifest[field]:
                raise CanaryError(f"{app_id} {field} differs from its frozen build provenance")
        _zero(app.get("forbidden_secret_count"), f"{app_id} forbidden secret count")
        by_id[str(app_id)] = app
    expected_boundaries = {
        "viewspec-api": (True, True),
        "viewspec-review": (True, True),
        "viewspec-review-worker": (False, False),
    }
    for app_id, (public_https, durable_volume) in expected_boundaries.items():
        if by_id[app_id].get("public_https") is not public_https:
            raise CanaryError(f"{app_id} public HTTPS boundary is incorrect")
        if by_id[app_id].get("durable_volume") is not durable_volume:
            raise CanaryError(f"{app_id} durable-volume boundary is incorrect")
    checks = payload.get("secret_boundary_checks")
    if not isinstance(checks, dict):
        raise CanaryError("Deployment secret boundary checks must be an object")
    _exact(checks, SECRET_BOUNDARY_CHECKS, "deployment secret boundary checks")
    for name in SECRET_BOUNDARY_CHECKS:
        _true(checks[name], f"deployment check {name}")


def _ingress(payload: Mapping[str, Any], *, run_id: str, deployment_sha256: str) -> None:
    _exact(payload, INGRESS_FIELDS, "ingress stage")
    _common(payload, kind="ingress", run_id=run_id, deployment_sha256=deployment_sha256)
    identities = payload.get("identities")
    if not isinstance(identities, dict):
        raise CanaryError("Ingress identities must be an object")
    _exact(identities, IDENTITY_NAMES, "ingress identities")
    for name, identity in identities.items():
        if not isinstance(identity, dict):
            raise CanaryError(f"Ingress identity {name} must be an object")
        _exact(identity, IDENTITY_FIELDS, f"ingress identity {name}")
        local = _sha(identity.get("local_sha256"), f"{name} local hash")
        remote = _sha(identity.get("remote_sha256"), f"{name} remote hash")
        if local != remote:
            raise CanaryError(f"Ingress identity {name} differs locally and remotely")
    _true(payload.get("mismatch_rejected_before_session"), "deliberate ingress mismatch rejection")
    _false(payload.get("paid_key_forwarded"), "paid-key forwarding")
    _true(payload.get("internal_request_authenticated"), "internal request authentication")
    _true(payload.get("internal_response_authenticated"), "internal response authentication")
    if payload.get("idempotent_session_count") != 1:
        raise CanaryError("Ingress retry must create exactly one session")


def _rebuild(payload: Mapping[str, Any], *, run_id: str, deployment_sha256: str) -> None:
    _exact(payload, REBUILD_FIELDS, "rebuild stage")
    _common(payload, kind="rebuild", run_id=run_id, deployment_sha256=deployment_sha256)
    _sha(payload.get("rebuild_evidence_sha256"), "rebuild evidence hash")
    expected = _sha(payload.get("expected_inventory_sha256"), "expected rebuild inventory hash")
    observed = _sha(payload.get("observed_inventory_sha256"), "observed rebuild inventory hash")
    if expected != observed:
        raise CanaryError("Independent rebuild inventory differs from the local checked inventory")
    _true(payload.get("source_only_request"), "source-only rebuild request")
    _false(payload.get("install_used"), "rebuild dependency installation")
    _true(payload.get("lifecycle_hooks_disabled"), "rebuild lifecycle-hook denial")
    _false(payload.get("uploaded_artifacts_executed"), "uploaded artifact execution")
    _true(payload.get("mismatch_rejected"), "rebuild mismatch rejection")


def _isolation(payload: Mapping[str, Any], *, run_id: str, deployment_sha256: str) -> None:
    _exact(payload, ISOLATION_FIELDS, "isolation stage")
    _common(payload, kind="isolation", run_id=run_id, deployment_sha256=deployment_sha256)
    _sha(payload.get("runner_receipt_sha256"), "isolation runner receipt hash")
    for field in (
        "bubblewrap_path_verified",
        "network_namespace_new",
        "pid_namespace_new",
        "ipc_namespace_new",
        "uts_namespace_new",
        "egress_canary_denied",
        "lifecycle_hook_canary_denied",
        "arbitrary_command_canary_denied",
        "file_limit_enforced",
        "byte_limit_enforced",
    ):
        _true(payload.get(field), f"isolation {field}")
    _bounded_int(payload.get("cpu_seconds_observed"), "isolation CPU seconds", maximum=30)
    _bounded_int(
        payload.get("cgroup_memory_limit_bytes"),
        "isolation cgroup memory limit",
        maximum=512 * 1024 * 1024,
    )
    _bounded_int(payload.get("wall_time_seconds"), "isolation wall seconds", maximum=120)
    _zero(payload.get("forbidden_environment_count"), "worker forbidden environment count")


def _browser(
    payload: Mapping[str, Any],
    *,
    kind: str,
    engine: str,
    run_id: str,
    deployment_sha256: str,
) -> None:
    _exact(payload, BROWSER_FIELDS, f"{engine} browser stage")
    _common(payload, kind=kind, run_id=run_id, deployment_sha256=deployment_sha256)
    if payload.get("engine") != engine:
        raise CanaryError(f"{kind} stage records the wrong browser engine")
    _sha(payload.get("probe_sha256"), f"{engine} browser probe hash")
    _sha(payload.get("playwright_lock_sha256"), f"{engine} Playwright lock hash")
    _bounded_int(payload.get("elapsed_ms"), f"{engine} browser elapsed_ms", maximum=300_000)
    for field in (
        "create_passed",
        "anonymous_access_denied",
        "fragment_removed",
        "secure_http_only_same_site_cookie",
        "static_target_passed",
        "react_target_passed",
        "semantic_comment_acknowledged",
        "reviewer_approval_denied",
        "owner_approval_passed",
        "receipt_signature_verified",
    ):
        _true(payload.get(field), f"{engine} browser {field}")
    for field in (
        "external_request_count",
        "capability_leak_count",
        "csp_violation_count",
        "console_error_count",
    ):
        _zero(payload.get(field), f"{engine} browser {field}")


def _recovery(payload: Mapping[str, Any], *, run_id: str, deployment_sha256: str) -> None:
    _exact(payload, RECOVERY_FIELDS, "recovery stage")
    _common(payload, kind="recovery", run_id=run_id, deployment_sha256=deployment_sha256)
    for field in (
        "restart_recovery_passed",
        "idempotent_retry_passed",
        "expiry_passed",
        "reviewer_rotation_passed",
        "revocation_passed",
        "deletion_passed",
        "backup_restore_passed",
        "receipt_key_rotation_passed",
        "storage_verification_passed",
    ):
        _true(payload.get(field), f"recovery {field}")
    for field in (
        "orphan_usable_session_count",
        "duplicate_session_count",
        "sensitive_telemetry_field_count",
    ):
        _zero(payload.get(field), f"recovery {field}")


def _leaks(payload: Mapping[str, Any], *, run_id: str, deployment_sha256: str) -> None:
    _exact(payload, LEAK_FIELDS, "leak-audit stage")
    _common(payload, kind="leak-audit", run_id=run_id, deployment_sha256=deployment_sha256)
    for field in LEAK_FIELDS - COMMON_STAGE_FIELDS:
        _zero(payload.get(field), f"leak audit {field}")


def validate_stage_payload(
    payload: Mapping[str, Any],
    *,
    kind: str,
    run_id: str,
    deployment_sha256: str,
) -> None:
    """Validate one stage before the runner promotes it into retained evidence."""

    if kind == "deployment":
        _deployment(payload, run_id=run_id, deployment_sha256=deployment_sha256)
    elif kind == "ingress":
        _ingress(payload, run_id=run_id, deployment_sha256=deployment_sha256)
    elif kind == "rebuild":
        _rebuild(payload, run_id=run_id, deployment_sha256=deployment_sha256)
    elif kind == "isolation":
        _isolation(payload, run_id=run_id, deployment_sha256=deployment_sha256)
    elif kind.startswith("browser-") and kind.removeprefix("browser-") in {
        "chromium",
        "firefox",
        "webkit",
    }:
        _browser(
            payload,
            kind=kind,
            engine=kind.removeprefix("browser-"),
            run_id=run_id,
            deployment_sha256=deployment_sha256,
        )
    elif kind == "recovery":
        _recovery(payload, run_id=run_id, deployment_sha256=deployment_sha256)
    elif kind == "leak-audit":
        _leaks(payload, run_id=run_id, deployment_sha256=deployment_sha256)
    else:
        raise CanaryError(f"Unsupported production canary stage: {kind}")


def evaluate_canary(path: str | Path) -> dict[str, Any]:
    report_path = Path(path).resolve()
    report = _read_object(report_path)
    _exact(report, REPORT_FIELDS, "production canary report")
    if (
        report.get("schema_version") != SCHEMA_VERSION
        or report.get("verifier_id") != VERIFIER_ID
        or report.get("environment") != "production"
        or report.get("origin") != ORIGIN
    ):
        raise CanaryError("Production canary report identity, environment, or origin is invalid")
    run_id = report.get("run_id")
    if not isinstance(run_id, str) or RUN_ID_RE.fullmatch(run_id) is None:
        raise CanaryError("Production canary run_id is invalid")
    deployment_sha256 = _sha(report.get("deployment_sha256"), "production deployment hash")
    started = _bounded_int(report.get("started_at_epoch_ms"), "canary start time")
    completed = _bounded_int(report.get("completed_at_epoch_ms"), "canary completion time")
    if completed < started or completed - started > 30 * 60 * 1000:
        raise CanaryError("Production canary timestamps are reversed or exceed 30 minutes")
    root = report_path.parent.resolve()
    stages = report.get("stages")
    if not isinstance(stages, list) or [item.get("kind") for item in stages if isinstance(item, dict)] != list(
        STAGE_KINDS
    ):
        raise CanaryError("Production canary stages must appear exactly once in canonical order")
    payloads: dict[str, dict[str, Any]] = {}
    stage_hashes: dict[str, str] = {}
    for reference in stages:
        if not isinstance(reference, dict):
            raise CanaryError("Production canary stage reference must be an object")
        path_value, payload = _safe_stage(root, reference)
        kind = str(reference["kind"])
        payloads[kind] = payload
        stage_hashes[kind] = _sha256_bytes(path_value.read_bytes())
    commands = report.get("commands")
    if not isinstance(commands, list) or len(commands) != len(STAGE_KINDS):
        raise CanaryError("Production canary must retain one command receipt per stage")
    command_kinds: list[str] = []
    for command in commands:
        if not isinstance(command, dict):
            raise CanaryError("Production canary command receipt must be an object")
        _exact(command, COMMAND_FIELDS, "production canary command receipt")
        command_kinds.append(str(command.get("kind")))
        _sha(command.get("argv_sha256"), "canary command argv hash")
        _sha(command.get("stdout_sha256"), "canary command stdout hash")
        _sha(command.get("stderr_sha256"), "canary command stderr hash")
        if command.get("exit_code") != 0:
            raise CanaryError("Every production canary command must exit zero")
        _bounded_int(command.get("elapsed_ms"), "canary command elapsed_ms", maximum=600_000)
        _bounded_int(command.get("stdout_bytes"), "canary command stdout_bytes", maximum=1024 * 1024)
        _bounded_int(command.get("stderr_bytes"), "canary command stderr_bytes", maximum=1024 * 1024)
    if command_kinds != list(STAGE_KINDS):
        raise CanaryError("Production canary command order must match the stage order")

    for kind in STAGE_KINDS:
        validate_stage_payload(
            payloads[kind],
            kind=kind,
            run_id=run_id,
            deployment_sha256=deployment_sha256,
        )
    checks = {
        "report_contract": True,
        "artifact_integrity": True,
        "deployment_and_secret_boundaries": True,
        "exact_ingress": True,
        "independent_rebuild": True,
        "real_isolation": True,
        "three_browser_journey": True,
        "recovery_and_rotation": True,
        "zero_sensitive_leaks": True,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "verifier_id": VERIFIER_ID,
        "run_id": run_id,
        "deployment_sha256": deployment_sha256,
        "report_sha256": _sha256_bytes(report_path.read_bytes()),
        "stage_sha256": stage_hashes,
        "checks": checks,
        "passed": all(checks.values()),
    }


def build_share_release_payload(
    canary_result: Mapping[str, Any],
    *,
    issued_at_epoch_s: int | None = None,
    lifetime_seconds: int = 15 * 60,
) -> dict[str, Any]:
    """Build the exact unsigned payload the production API may sign and publish."""

    expected_fields = {
        "schema_version",
        "verifier_id",
        "run_id",
        "deployment_sha256",
        "report_sha256",
        "stage_sha256",
        "checks",
        "passed",
    }
    if set(canary_result) != expected_fields or canary_result.get("passed") is not True:
        raise CanaryError("Studio Share release requires one complete passing canonical canary result")
    checks = canary_result.get("checks")
    expected_checks = {
        "report_contract",
        "artifact_integrity",
        "deployment_and_secret_boundaries",
        "exact_ingress",
        "independent_rebuild",
        "real_isolation",
        "three_browser_journey",
        "recovery_and_rotation",
        "zero_sensitive_leaks",
    }
    if not isinstance(checks, dict) or set(checks) != expected_checks or not all(value is True for value in checks.values()):
        raise CanaryError("Studio Share release requires every production canary gate")
    if canary_result.get("verifier_id") != VERIFIER_ID:
        raise CanaryError("Studio Share release verifier identity is invalid")
    run_id = canary_result.get("run_id")
    deployment_sha256 = canary_result.get("deployment_sha256")
    report_sha256 = canary_result.get("report_sha256")
    if not isinstance(run_id, str) or RUN_ID_RE.fullmatch(run_id) is None:
        raise CanaryError("Studio Share release run identity is invalid")
    _sha(deployment_sha256, "Studio Share release deployment hash")
    _sha(report_sha256, "Studio Share release report hash")
    now = int(time.time()) if issued_at_epoch_s is None else issued_at_epoch_s
    if (
        type(now) is not int
        or now < 0
        or type(lifetime_seconds) is not int
        or not 60 <= lifetime_seconds <= SHARE_RELEASE_MAX_LIFETIME_SECONDS
    ):
        raise CanaryError("Studio Share release lifetime is invalid")
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": SHARE_RELEASE_KIND,
        "status": "active",
        "api_origin": API_ORIGIN,
        "review_origin": ORIGIN,
        "deployment_sha256": deployment_sha256,
        "verifier_id": VERIFIER_ID,
        "run_id": run_id,
        "report_sha256": report_sha256,
        "checks": dict(checks),
        "issued_at_epoch_s": now,
        "expires_at_epoch_s": now + lifetime_seconds,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--share-release-payload-out", type=Path)
    args = parser.parse_args()
    try:
        result = evaluate_canary(args.evidence)
    except CanaryError as exc:
        result = {
            "schema_version": SCHEMA_VERSION,
            "verifier_id": VERIFIER_ID,
            "passed": False,
            "error": str(exc),
        }
    encoded = _canonical_bytes(result)
    if args.out is not None:
        temporary = args.out.with_name(f".{args.out.name}.tmp")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(encoded)
        temporary.replace(args.out)
    if result["passed"] and args.share_release_payload_out is not None:
        release_payload = _canonical_bytes(build_share_release_payload(result))
        temporary = args.share_release_payload_out.with_name(f".{args.share_release_payload_out.name}.tmp")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(release_payload)
        temporary.replace(args.share_release_payload_out)
    print(encoded.decode("utf-8"), end="")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
