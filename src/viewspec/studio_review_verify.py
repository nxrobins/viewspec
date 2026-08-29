"""Deterministic source-to-artifact verification for hosted Studio review.

This module proves that the semantic source inside one ``.vsreview`` package
rebuilds the exact checked static and React artifact inventory.  It deliberately
does not claim that the current process had no network access.  A trusted hosted
runner must bind its own sandbox attestation before the result is accepted by
``StudioReviewService``.
"""

from __future__ import annotations

from collections.abc import Mapping
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile

from viewspec.review_compile import (
    APP_SOURCE_MAX_BYTES,
    DESIGN_MAX_BYTES,
    MAX_ARTIFACT_BYTES,
    MAX_ARTIFACT_FILES,
    STUDIO_COMPARE_MANIFEST,
    STUDIO_COMPARE_TARGET,
)
from viewspec.review_contract import ReviewContractError, canonical_json_bytes
from viewspec.review_runtime import ReviewRuntime
from viewspec.studio_review_service import (
    STUDIO_REVIEW_MAX_CPU_SECONDS,
    STUDIO_REVIEW_MAX_MEMORY_BYTES,
    STUDIO_REVIEW_MAX_WALL_SECONDS,
    STUDIO_REVIEW_SERVICE_SCHEMA_VERSION,
)
from viewspec.studio_share import (
    STUDIO_SHARE_MAX_BYTES,
    STUDIO_SHARE_MAX_FILES,
    STUDIO_SHARE_PAYLOAD_DIR,
    STUDIO_SHARE_SCHEMA_VERSION,
    StudioShareError,
    load_studio_share_package,
)


STUDIO_REVIEW_REBUILD_SCHEMA_VERSION = 1
STUDIO_REVIEW_REBUILD_VERIFIER_ID = "viewspec-studio-rebuild-v1"
STUDIO_REVIEW_DEPENDENCY_SEED_ENV = "VIEWSPEC_STUDIO_REVIEW_NODE_MODULES_DIR"
STUDIO_REVIEW_DEPENDENCY_LOCK_MAX_BYTES = 2 * 1024 * 1024
STUDIO_REVIEW_REBUILD_REQUEST_MAX_BYTES = 4 * 1024 * 1024

_HASH_FIELDS = (
    "source_sha256",
    "artifact_set_sha256",
    "root_manifest_sha256",
    "inspection_sha256",
)
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class StudioReviewVerificationError(ValueError):
    """Fail-closed package rebuild or attestation error."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = "STUDIO_REVIEW_VERIFICATION_FAILED"
        self.message = message
        self.fix = "Reject the upload and inspect the trusted rebuild worker evidence."

    def to_json(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "fix": self.fix}


def rebuild_studio_review_package(
    package_dir: str | Path,
    envelope: dict[str, object],
) -> dict[str, object]:
    """Rebuild one package from semantic source and compare every artifact byte.

    The operator must configure a prebuilt ``node_modules`` directory.  There is
    intentionally no dependency-install fallback in this verification path.
    Uploaded checked artifacts are compared as inert bytes and never executed.
    """

    request = make_studio_review_rebuild_request(package_dir, envelope)
    return rebuild_studio_review_request(request)


def make_studio_review_rebuild_request(
    package_dir: str | Path,
    envelope: dict[str, object],
) -> dict[str, object]:
    """Create the bounded, artifact-free request for an isolated rebuild worker."""

    package = Path(package_dir)
    if not package.is_absolute():
        package = Path(os.path.abspath(package))
    checked = _load_exact_package(package, envelope)
    payload = package / STUDIO_SHARE_PAYLOAD_DIR
    source = _read_bounded_file(payload / "source/viewspec.app.json", maximum=APP_SOURCE_MAX_BYTES)
    revision = checked.get("revision")
    assert isinstance(revision, dict)
    design = (
        _read_bounded_file(payload / "design/DESIGN.md", maximum=DESIGN_MAX_BYTES)
        if revision.get("design_sha256") is not None
        else None
    )
    request = {
        "schema_version": STUDIO_REVIEW_REBUILD_SCHEMA_VERSION,
        "kind": "studio_review_rebuild_request",
        "envelope": checked,
        "semantic_source_base64": _encode_base64(source),
        "design_system_base64": _encode_base64(design) if design is not None else None,
    }
    if len(canonical_json_bytes(request)) > STUDIO_REVIEW_REBUILD_REQUEST_MAX_BYTES:
        raise StudioReviewVerificationError("Isolated rebuild request exceeded its byte boundary.")
    return request


def rebuild_studio_review_request(request: Mapping[str, object]) -> dict[str, object]:
    """Verify one artifact-free wire request inside an isolated rebuild worker."""

    if not isinstance(request, Mapping):
        raise StudioReviewVerificationError("Isolated rebuild request is missing.")
    value = dict(request)
    if (
        set(value) != {
            "schema_version",
            "kind",
            "envelope",
            "semantic_source_base64",
            "design_system_base64",
        }
        or value.get("schema_version") != STUDIO_REVIEW_REBUILD_SCHEMA_VERSION
        or value.get("kind") != "studio_review_rebuild_request"
        or len(canonical_json_bytes(value)) > STUDIO_REVIEW_REBUILD_REQUEST_MAX_BYTES
    ):
        raise StudioReviewVerificationError("Isolated rebuild request fields or size are invalid.")
    checked = _validated_rebuild_envelope(value.get("envelope"))
    source = _decode_base64(value.get("semantic_source_base64"), maximum=APP_SOURCE_MAX_BYTES)
    revision = checked["revision"]
    assert isinstance(revision, dict)
    design_value = value.get("design_system_base64")
    design = None if design_value is None else _decode_base64(design_value, maximum=DESIGN_MAX_BYTES)
    _assert_rebuild_input_identity(checked, source=source, design=design)
    return _rebuild_studio_review_inputs(source=source, design=design, checked=checked)


def _rebuild_studio_review_inputs(
    *,
    source: bytes,
    design: bytes | None,
    checked: dict[str, object],
) -> dict[str, object]:
    dependency_seed, dependency_lock_sha256 = _trusted_dependency_seed()

    try:
        with tempfile.TemporaryDirectory(prefix="viewspec-studio-rebuild-") as temporary:
            workspace = Path(temporary)
            source_copy = workspace / "viewspec.app.json"
            source_copy.write_bytes(source)
            design_copy: Path | None = None
            revision = checked.get("revision")
            assert isinstance(revision, dict)
            if design is not None:
                design_copy = workspace / "DESIGN.md"
                design_copy.write_bytes(design)
            runtime = ReviewRuntime.open(
                source_copy,
                state_root=workspace / "review-state",
                design_path=design_copy,
                target=STUDIO_COMPARE_TARGET,
                allow_install=True,
            )
            rebuilt_revision = runtime.built.revision.to_json()
            rebuilt_inventory = _artifact_inventory(runtime.built.artifact_dir)
            rebuilt_comparison = _read_json(runtime.built.artifact_dir / STUDIO_COMPARE_MANIFEST)
            _assert_revision_equivalent(revision, rebuilt_revision, rebuilt_comparison)
            expected_inventory = _expected_artifact_inventory(checked)
            if rebuilt_inventory != expected_inventory:
                raise StudioReviewVerificationError(
                    "Semantic source rebuild did not reproduce the uploaded checked artifact inventory."
                )
            if _sha256_file(dependency_seed / ".package-lock.json") != dependency_lock_sha256:
                raise StudioReviewVerificationError("Trusted dependency install lock changed during the rebuild.")
    except StudioReviewVerificationError:
        raise
    except (OSError, ReviewContractError, ValueError) as exc:
        raise StudioReviewVerificationError(
            "Semantic source could not be rebuilt into one exact checked Studio comparison."
        ) from exc

    artifact_bytes = sum(int(item[1]) for item in rebuilt_inventory)
    evidence = {
        "schema_version": STUDIO_REVIEW_REBUILD_SCHEMA_VERSION,
        "kind": "studio_review_rebuild_evidence",
        "status": "passed",
        "verifier_id": STUDIO_REVIEW_REBUILD_VERIFIER_ID,
        "package_id": checked["package_id"],
        "source_sha256": revision["source_sha256"],
        "design_sha256": revision["design_sha256"],
        "artifact_set_sha256": revision["artifact_set_sha256"],
        "root_manifest_sha256": revision["root_manifest_sha256"],
        "inspection_sha256": revision["inspection_sha256"],
        "target_artifact_sets": revision["target_artifact_sets"],
        "compiler_version": rebuilt_revision["compiler_version"],
        "contract_profile": rebuilt_revision["contract_profile"],
        "artifact_inventory": {
            "file_count": len(rebuilt_inventory),
            "byte_count": artifact_bytes,
            "sha256": rebuilt_revision["artifact_set_sha256"],
        },
        "dependency_seed": {
            "kind": "operator_pinned_prebuilt_node_modules",
            "lock_file": "node_modules/.package-lock.json",
            "lock_sha256": dependency_lock_sha256,
            "install_command_invoked": False,
        },
        "execution_policy": {
            "uploaded_artifacts_executed": False,
            "package_lifecycle_hooks_invoked": False,
            "fixed_build_command_only": True,
            "external_runtime_references": "rejected_by_compiler",
            "sandbox_attestation": "required_separately",
        },
    }
    # Keep verifier output safely below the service's evidence ceiling.
    if len(canonical_json_bytes(evidence)) > 64 * 1024:
        raise StudioReviewVerificationError("Deterministic rebuild evidence exceeded its response boundary.")
    return evidence


def bind_studio_review_sandbox_attestation(
    rebuild_evidence: Mapping[str, object],
    sandbox_attestation: Mapping[str, object],
    *,
    envelope: Mapping[str, object],
) -> dict[str, object]:
    """Bind trusted runner isolation evidence to one successful exact rebuild.

    This function validates and projects an attestation supplied by the hosted
    runner.  It does not create, sign, or independently prove that attestation.
    """

    evidence = _validated_rebuild_evidence(rebuild_evidence, envelope=envelope)
    if not isinstance(sandbox_attestation, Mapping):
        raise StudioReviewVerificationError("Hosted runner sandbox attestation is missing.")
    attestation = dict(sandbox_attestation)
    expected_fields = {
        "schema_version",
        "kind",
        "status",
        "runner_id",
        "rebuild_evidence_sha256",
        "network",
        "lifecycle_hooks",
        "arbitrary_commands",
        "limits",
    }
    evidence_sha256 = hashlib.sha256(canonical_json_bytes(evidence)).hexdigest()
    runner_id = attestation.get("runner_id")
    if (
        set(attestation) != expected_fields
        or attestation.get("schema_version") != STUDIO_REVIEW_REBUILD_SCHEMA_VERSION
        or attestation.get("kind") != "studio_review_sandbox_attestation"
        or attestation.get("status") != "passed"
        or not isinstance(runner_id, str)
        or not 1 <= len(runner_id) <= 96
        or attestation.get("rebuild_evidence_sha256") != evidence_sha256
        or attestation.get("network") != "denied"
        or attestation.get("lifecycle_hooks") != "disabled"
        or attestation.get("arbitrary_commands") != "disabled"
    ):
        raise StudioReviewVerificationError("Hosted runner sandbox attestation is invalid or unbound.")
    limits = _validated_sandbox_limits(attestation.get("limits"), envelope=envelope)
    verifier_id = f"{evidence['verifier_id']}+{runner_id}"
    if len(verifier_id) > 128:
        raise StudioReviewVerificationError("Combined hosted verifier identity exceeds its boundary.")
    return {
        "schema_version": STUDIO_REVIEW_SERVICE_SCHEMA_VERSION,
        "status": "passed",
        "verifier_id": verifier_id,
        "package_id": evidence["package_id"],
        "source_sha256": evidence["source_sha256"],
        "artifact_set_sha256": evidence["artifact_set_sha256"],
        "root_manifest_sha256": evidence["root_manifest_sha256"],
        "inspection_sha256": evidence["inspection_sha256"],
        "target_artifact_sets": evidence["target_artifact_sets"],
        "rebuild": {
            "evidence_sha256": evidence_sha256,
            "expected_inventory_sha256": evidence["artifact_set_sha256"],
            "observed_inventory_sha256": evidence["artifact_inventory"]["sha256"],
            "source_only_request": True,
            "install_used": evidence["dependency_seed"]["install_command_invoked"],
            "lifecycle_hooks_disabled": not evidence["execution_policy"][
                "package_lifecycle_hooks_invoked"
            ],
            "uploaded_artifacts_executed": evidence["execution_policy"][
                "uploaded_artifacts_executed"
            ],
        },
        "sandbox": {
            "network": "denied",
            "lifecycle_hooks": "disabled",
            "arbitrary_commands": "disabled",
            "limits": limits,
        },
    }


def studio_review_rebuild_evidence_sha256(evidence: Mapping[str, object]) -> str:
    """Return the canonical identity a trusted runner must attest."""

    if not isinstance(evidence, Mapping):
        raise StudioReviewVerificationError("Deterministic rebuild evidence is missing.")
    return hashlib.sha256(canonical_json_bytes(dict(evidence))).hexdigest()


def _load_exact_package(package: Path, envelope: object) -> dict[str, object]:
    try:
        checked = load_studio_share_package(package)
    except (OSError, StudioShareError, ValueError) as exc:
        raise StudioReviewVerificationError("Uploaded Studio review package failed exact revalidation.") from exc
    if not isinstance(envelope, dict) or checked != envelope:
        raise StudioReviewVerificationError("Verifier envelope does not match the exact uploaded package.")
    return checked


def _validated_rebuild_envelope(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise StudioReviewVerificationError("Isolated rebuild envelope is missing.")
    envelope = json.loads(canonical_json_bytes(value))
    expected_fields = {
        "schema_version",
        "kind",
        "status",
        "revision",
        "files",
        "totals",
        "disclosure",
        "policy",
        "package_id",
    }
    package_id = envelope.get("package_id")
    basis = {key: item for key, item in envelope.items() if key != "package_id"}
    revision = envelope.get("revision")
    if (
        set(envelope) != expected_fields
        or envelope.get("schema_version") != STUDIO_SHARE_SCHEMA_VERSION
        or envelope.get("kind") != "studio_private_review_upload_envelope"
        or envelope.get("status") != "awaiting_disclosure_acceptance"
        or not isinstance(package_id, str)
        or _HASH_RE.fullmatch(package_id) is None
        or hashlib.sha256(canonical_json_bytes(basis)).hexdigest() != package_id
        or not isinstance(revision, dict)
    ):
        raise StudioReviewVerificationError("Isolated rebuild envelope identity is invalid.")
    for field in (*_HASH_FIELDS, "compiler_version", "contract_profile"):
        if not isinstance(revision.get(field), str):
            raise StudioReviewVerificationError("Isolated rebuild revision identity is incomplete.")
    if any(_HASH_RE.fullmatch(str(revision[field])) is None for field in _HASH_FIELDS):
        raise StudioReviewVerificationError("Isolated rebuild revision contains an invalid identity hash.")
    files = envelope.get("files")
    totals = envelope.get("totals")
    if not isinstance(files, list) or not 1 <= len(files) <= STUDIO_SHARE_MAX_FILES or not isinstance(totals, dict):
        raise StudioReviewVerificationError("Isolated rebuild file inventory is invalid.")
    if any(
        not isinstance(item, dict)
        or set(item) != {"role", "path", "bytes", "sha256", "media_type"}
        or type(item.get("bytes")) is not int
        or not 0 <= int(item["bytes"]) <= STUDIO_SHARE_MAX_BYTES
        or not isinstance(item.get("sha256"), str)
        or _HASH_RE.fullmatch(str(item["sha256"])) is None
        for item in files
    ):
        raise StudioReviewVerificationError("Isolated rebuild file inventory contains an invalid entry.")
    total = sum(int(item["bytes"]) for item in files)
    if total > STUDIO_SHARE_MAX_BYTES or totals != {"file_count": len(files), "bytes": total}:
        raise StudioReviewVerificationError("Isolated rebuild file inventory totals are invalid.")
    inventory = _expected_artifact_inventory(envelope)
    if not inventory or _inventory_sha256(inventory) != revision.get("artifact_set_sha256"):
        raise StudioReviewVerificationError("Isolated rebuild artifact inventory identity is invalid.")
    return envelope


def _assert_rebuild_input_identity(
    envelope: Mapping[str, object],
    *,
    source: bytes,
    design: bytes | None,
) -> None:
    revision = envelope.get("revision")
    files = envelope.get("files")
    assert isinstance(revision, dict) and isinstance(files, list)
    source_entries = [item for item in files if isinstance(item, dict) and item.get("role") == "semantic_source"]
    design_entries = [item for item in files if isinstance(item, dict) and item.get("role") == "design_system"]
    source_sha256 = hashlib.sha256(source).hexdigest()
    if (
        len(source_entries) != 1
        or source_entries[0].get("path") != "source/viewspec.app.json"
        or source_entries[0].get("bytes") != len(source)
        or source_entries[0].get("sha256") != source_sha256
        or revision.get("source_sha256") != source_sha256
    ):
        raise StudioReviewVerificationError("Isolated semantic source does not match its package identity.")
    if design is None:
        if design_entries or revision.get("design_sha256") is not None:
            raise StudioReviewVerificationError("Isolated design-system absence does not match its package identity.")
        return
    design_sha256 = hashlib.sha256(design).hexdigest()
    if (
        len(design_entries) != 1
        or design_entries[0].get("path") != "design/DESIGN.md"
        or design_entries[0].get("bytes") != len(design)
        or design_entries[0].get("sha256") != design_sha256
        or revision.get("design_sha256") != design_sha256
    ):
        raise StudioReviewVerificationError("Isolated design system does not match its package identity.")


def _trusted_dependency_seed() -> tuple[Path, str]:
    configured = os.environ.get(STUDIO_REVIEW_DEPENDENCY_SEED_ENV)
    if not configured:
        raise StudioReviewVerificationError(
            "Hosted rebuild requires an operator-configured prebuilt dependency seed; install fallback is forbidden."
        )
    seed = Path(configured)
    if not seed.is_absolute():
        raise StudioReviewVerificationError("Configured Studio review dependency seed is not one absolute normal directory.")
    try:
        seed_stat = seed.lstat()
    except OSError as exc:
        raise StudioReviewVerificationError("Configured Studio review dependency seed is unavailable.") from exc
    if seed.is_symlink() or not stat.S_ISDIR(seed_stat.st_mode):
        raise StudioReviewVerificationError("Configured Studio review dependency seed is not one absolute normal directory.")
    lock = seed / ".package-lock.json"
    try:
        lock_stat = lock.lstat()
    except OSError as exc:
        raise StudioReviewVerificationError("Configured Studio review dependency seed has no retained install lock.") from exc
    if (
        lock.is_symlink()
        or not stat.S_ISREG(lock_stat.st_mode)
        or not 1 <= lock_stat.st_size <= STUDIO_REVIEW_DEPENDENCY_LOCK_MAX_BYTES
    ):
        raise StudioReviewVerificationError("Configured Studio review dependency install lock is unsafe or unbounded.")
    return seed, _sha256_file(lock)


def _assert_revision_equivalent(
    expected: Mapping[str, object],
    rebuilt: Mapping[str, object],
    comparison: Mapping[str, object],
) -> None:
    base_fields = {
        "source_kind",
        "source_sha256",
        "design_sha256",
        "target",
        "artifact_set_sha256",
        "root_manifest_kind",
        "root_manifest_sha256",
        "compiler_version",
        "contract_profile",
    }
    if any(expected.get(field) != rebuilt.get(field) for field in base_fields):
        raise StudioReviewVerificationError("Rebuilt Studio revision identity differs from the uploaded checked revision.")
    inspection = comparison.get("inspection")
    screens = comparison.get("screens")
    targets = comparison.get("targets")
    if not isinstance(inspection, dict) or not isinstance(screens, list) or not isinstance(targets, list):
        raise StudioReviewVerificationError("Rebuilt Studio comparison identity is incomplete.")
    derived = {
        "routes": comparison.get("routes"),
        "screens": [
            {"screen_id": item.get("id"), "semantic_identity_sha256": item.get("semantic_identity_sha256")}
            for item in screens
            if isinstance(item, dict)
        ],
        "inspection_sha256": inspection.get("sha256"),
        "target_artifact_sets": [
            {"id": item.get("id"), "artifact_set_sha256": item.get("artifact_set_sha256")}
            for item in targets
            if isinstance(item, dict)
        ],
    }
    if any(expected.get(field) != value for field, value in derived.items()):
        raise StudioReviewVerificationError("Rebuilt Studio comparison projection differs from the uploaded revision.")


def _expected_artifact_inventory(envelope: Mapping[str, object]) -> list[tuple[str, int, str]]:
    files = envelope.get("files")
    if not isinstance(files, list):
        raise StudioReviewVerificationError("Uploaded checked artifact inventory is missing.")
    entries = []
    for item in files:
        if not isinstance(item, dict) or item.get("role") != "checked_artifact":
            continue
        path = item.get("path")
        size = item.get("bytes")
        sha256 = item.get("sha256")
        if (
            not isinstance(path, str)
            or not path.startswith("artifacts/")
            or type(size) is not int
            or not isinstance(sha256, str)
        ):
            raise StudioReviewVerificationError("Uploaded checked artifact inventory is invalid.")
        entries.append((path.removeprefix("artifacts/"), size, sha256))
    return sorted(entries)


def _inventory_sha256(entries: list[tuple[str, int, str]]) -> str:
    digest = hashlib.sha256()
    digest.update(b"viewspec.review.artifact-set.v1\x00")
    for relative, size, sha256 in sorted(entries):
        record = canonical_json_bytes({"path": relative, "sha256": sha256, "size": size})
        digest.update(len(record).to_bytes(8, "big"))
        digest.update(record)
    return digest.hexdigest()


def _artifact_inventory(root: Path) -> list[tuple[str, int, str]]:
    entries: list[tuple[str, int, str]] = []
    total = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        value = path.lstat()
        if stat.S_ISDIR(value.st_mode) and not path.is_symlink():
            continue
        if not stat.S_ISREG(value.st_mode) or path.is_symlink():
            raise StudioReviewVerificationError("Rebuilt Studio artifacts contain an unsafe filesystem entry.")
        if len(entries) >= MAX_ARTIFACT_FILES:
            raise StudioReviewVerificationError("Rebuilt Studio artifact count exceeds its boundary.")
        total += value.st_size
        if value.st_size > MAX_ARTIFACT_BYTES or total > MAX_ARTIFACT_BYTES:
            raise StudioReviewVerificationError("Rebuilt Studio artifacts exceed their byte boundary.")
        entries.append((path.relative_to(root).as_posix(), value.st_size, _sha256_file(path)))
    if not entries:
        raise StudioReviewVerificationError("Rebuilt Studio artifact inventory is empty.")
    return entries


def _validated_rebuild_evidence(
    value: Mapping[str, object],
    *,
    envelope: Mapping[str, object],
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise StudioReviewVerificationError("Deterministic rebuild evidence is missing.")
    evidence = dict(value)
    expected_fields = {
        "schema_version",
        "kind",
        "status",
        "verifier_id",
        "package_id",
        "source_sha256",
        "design_sha256",
        "artifact_set_sha256",
        "root_manifest_sha256",
        "inspection_sha256",
        "target_artifact_sets",
        "compiler_version",
        "contract_profile",
        "artifact_inventory",
        "dependency_seed",
        "execution_policy",
    }
    revision = envelope.get("revision")
    if not isinstance(revision, Mapping):
        raise StudioReviewVerificationError("Studio review envelope has no revision identity.")
    if (
        set(evidence) != expected_fields
        or evidence.get("schema_version") != STUDIO_REVIEW_REBUILD_SCHEMA_VERSION
        or evidence.get("kind") != "studio_review_rebuild_evidence"
        or evidence.get("status") != "passed"
        or evidence.get("verifier_id") != STUDIO_REVIEW_REBUILD_VERIFIER_ID
        or evidence.get("package_id") != envelope.get("package_id")
        or any(evidence.get(field) != revision.get(field) for field in (*_HASH_FIELDS, "design_sha256"))
        or evidence.get("target_artifact_sets") != revision.get("target_artifact_sets")
        or evidence.get("compiler_version") != revision.get("compiler_version")
        or evidence.get("contract_profile") != revision.get("contract_profile")
    ):
        raise StudioReviewVerificationError("Deterministic rebuild evidence is invalid or package-unbound.")
    for field in _HASH_FIELDS:
        if not isinstance(evidence.get(field), str) or _HASH_RE.fullmatch(str(evidence[field])) is None:
            raise StudioReviewVerificationError("Deterministic rebuild evidence contains an invalid identity hash.")
    inventory = evidence.get("artifact_inventory")
    dependency = evidence.get("dependency_seed")
    policy = evidence.get("execution_policy")
    if (
        not isinstance(inventory, dict)
        or set(inventory) != {"file_count", "byte_count", "sha256"}
        or type(inventory.get("file_count")) is not int
        or not 1 <= int(inventory["file_count"]) <= MAX_ARTIFACT_FILES
        or type(inventory.get("byte_count")) is not int
        or not 1 <= int(inventory["byte_count"]) <= MAX_ARTIFACT_BYTES
        or inventory.get("sha256") != evidence.get("artifact_set_sha256")
        or not isinstance(dependency, dict)
        or dependency.get("kind") != "operator_pinned_prebuilt_node_modules"
        or dependency.get("lock_file") != "node_modules/.package-lock.json"
        or not isinstance(dependency.get("lock_sha256"), str)
        or _HASH_RE.fullmatch(str(dependency["lock_sha256"])) is None
        or dependency.get("install_command_invoked") is not False
        or not isinstance(policy, dict)
        or policy
        != {
            "uploaded_artifacts_executed": False,
            "package_lifecycle_hooks_invoked": False,
            "fixed_build_command_only": True,
            "external_runtime_references": "rejected_by_compiler",
            "sandbox_attestation": "required_separately",
        }
    ):
        raise StudioReviewVerificationError("Deterministic rebuild evidence policy or inventory is invalid.")
    if len(canonical_json_bytes(evidence)) > 64 * 1024:
        raise StudioReviewVerificationError("Deterministic rebuild evidence exceeds its response boundary.")
    return json.loads(canonical_json_bytes(evidence))


def _validated_sandbox_limits(value: object, *, envelope: Mapping[str, object]) -> dict[str, int]:
    if not isinstance(value, Mapping) or set(value) != {
        "cpu_seconds",
        "memory_bytes",
        "wall_seconds",
        "file_count",
        "byte_count",
    }:
        raise StudioReviewVerificationError("Hosted runner sandbox limits are missing or invalid.")
    limits = dict(value)
    bounds = {
        "cpu_seconds": STUDIO_REVIEW_MAX_CPU_SECONDS,
        "memory_bytes": STUDIO_REVIEW_MAX_MEMORY_BYTES,
        "wall_seconds": STUDIO_REVIEW_MAX_WALL_SECONDS,
        "file_count": STUDIO_SHARE_MAX_FILES,
        "byte_count": STUDIO_SHARE_MAX_BYTES,
    }
    if any(type(limits.get(key)) is not int or not 1 <= int(limits[key]) <= maximum for key, maximum in bounds.items()):
        raise StudioReviewVerificationError("Hosted runner sandbox limits exceed the accepted service boundary.")
    totals = envelope.get("totals")
    if (
        not isinstance(totals, Mapping)
        or int(limits["file_count"]) < int(totals.get("file_count", STUDIO_SHARE_MAX_FILES + 1))
        or int(limits["byte_count"]) < int(totals.get("bytes", STUDIO_SHARE_MAX_BYTES + 1))
    ):
        raise StudioReviewVerificationError("Hosted runner sandbox limits do not cover the exact upload package.")
    return {key: int(limits[key]) for key in bounds}


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise StudioReviewVerificationError("Rebuilt Studio comparison manifest is not an object.")
    return value


def _read_bounded_file(path: Path, *, maximum: int) -> bytes:
    value = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(value.st_mode) or not 1 <= value.st_size <= maximum:
        raise StudioReviewVerificationError("Studio rebuild input file is unsafe or outside its byte boundary.")
    content = path.read_bytes()
    if len(content) != value.st_size:
        raise StudioReviewVerificationError("Studio rebuild input file changed while it was read.")
    return content


def _encode_base64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_base64(value: object, *, maximum: int) -> bytes:
    if not isinstance(value, str) or not value or len(value) > ((maximum + 2) // 3) * 4:
        raise StudioReviewVerificationError("Isolated rebuild input encoding is missing or exceeds its boundary.")
    try:
        encoded = value.encode("ascii")
        content = base64.b64decode(encoded + (b"=" * (-len(encoded) % 4)), altchars=b"-_", validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise StudioReviewVerificationError("Isolated rebuild input is not strict URL-safe base64.") from exc
    if not 1 <= len(content) <= maximum or _encode_base64(content) != value:
        raise StudioReviewVerificationError("Isolated rebuild input encoding is noncanonical or outside its boundary.")
    return content


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(64 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "STUDIO_REVIEW_DEPENDENCY_SEED_ENV",
    "STUDIO_REVIEW_REBUILD_SCHEMA_VERSION",
    "STUDIO_REVIEW_REBUILD_VERIFIER_ID",
    "StudioReviewVerificationError",
    "bind_studio_review_sandbox_attestation",
    "make_studio_review_rebuild_request",
    "rebuild_studio_review_package",
    "rebuild_studio_review_request",
    "studio_review_rebuild_evidence_sha256",
]
