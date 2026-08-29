"""Local, content-addressed preparation for future private Studio review sharing.

This module performs no network operation. It turns one already-checked Studio
static/React comparison revision into a private immutable payload plus the exact
disclosure a future hosted Share action must present.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import zipfile

from viewspec._version import __version__
from viewspec.local_tools import LocalToolError, resolve_cwd, resolve_local_path
from viewspec.review_compile import (
    MAX_ARTIFACT_BYTES,
    MAX_ARTIFACT_FILES,
    STUDIO_COMPARE_MANIFEST,
    STUDIO_COMPARE_TARGET,
    capture_source_snapshot,
)
from viewspec.review_contract import ReviewContractError, canonical_json_bytes
from viewspec.review_runtime import ReviewRuntime, default_review_state_root
from viewspec.studio import resolve_studio_source


STUDIO_SHARE_SCHEMA_VERSION = 1
STUDIO_SHARE_ROOT_DEFAULT = ".viewspec/studio-share"
STUDIO_SHARE_REFERENCE_MAX_BYTES = 10 * 1024 * 1024
STUDIO_SHARE_MAX_BYTES = MAX_ARTIFACT_BYTES + (14 * 1024 * 1024)
STUDIO_SHARE_MAX_FILES = MAX_ARTIFACT_FILES + 3
STUDIO_SHARE_DISCLOSURE_NAME = "share-disclosure.md"
STUDIO_SHARE_ENVELOPE_NAME = "envelope.json"
STUDIO_SHARE_PAYLOAD_DIR = "payload"
STUDIO_SHARE_ARCHIVE_SUFFIX = ".vsreview"
STUDIO_SHARE_ARCHIVE_MAX_BYTES = STUDIO_SHARE_MAX_BYTES + (2 * 1024 * 1024)

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ARCHIVE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]{1,160}$")
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("github_token", re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{16,}\b")),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{16,}={0,2}\b", re.IGNORECASE)),
    (
        "credential_field",
        re.compile(
            r'"(?:api[_-]?key|access[_-]?token|client[_-]?secret|password|passwd|private[_-]?key)"'
            r"\s*:\s*\"(?!example|placeholder|redacted|not_claimed|none|null)[^\"\r\n]{8,}\"",
            re.IGNORECASE,
        ),
    ),
)
_REFERENCE_SIGNATURES = (
    ("image/png", b"\x89PNG\r\n\x1a\n"),
    ("image/jpeg", b"\xff\xd8\xff"),
    ("image/webp", b"RIFF"),
)
_REFERENCE_SUFFIXES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
_STUDIO_SHARE_POLICY = {
    "network_calls": "none",
    "upload_performed": False,
    "capability_created": False,
    "confirmation_required": True,
    "confirmation_accepted": False,
    "existing_local_comments": "excluded",
    "future_remote_comments": "disclosed_if_service_is_created",
    "production_data": "not_claimed",
    "secret_detection": "bounded_pattern_scan_not_certification",
    "visual_parity": "not_proven",
}


class StudioShareError(ValueError):
    """Stable fail-closed error for the local Studio share package contract."""

    def __init__(self, code: str, message: str, fix: str, *, cli_exit: int = 2) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.fix = fix
        self.cli_exit = cli_exit

    def to_json(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "fix": self.fix}


def prepare_studio_share(
    source: str | Path | None = None,
    *,
    reference: str | Path | None = None,
    state_root: str | Path | None = None,
    out_root: str | Path = STUDIO_SHARE_ROOT_DEFAULT,
    cwd: str | Path | None = None,
) -> dict[str, object]:
    """Prepare one immutable local package without uploading or granting access."""

    root = resolve_cwd(cwd)
    source_path = _workspace_path(resolve_studio_source(source, cwd=root), root=root, must_exist=True)
    review_root = Path(state_root).expanduser() if state_root is not None else default_review_state_root()
    try:
        runtime = ReviewRuntime.resume(source_path, state_root=review_root)
    except ReviewContractError as exc:
        raise StudioShareError(
            "STUDIO_SHARE_REVIEW_NOT_READY",
            f"Studio could not load an exact checked revision: {exc.code}: {exc.message}",
            "Open the AppBundle with viewspec studio --compare --install, then prepare sharing again.",
        ) from exc
    if runtime.configuration.target != STUDIO_COMPARE_TARGET or runtime.built.revision.source_kind != "app_bundle":
        raise StudioShareError(
            "STUDIO_SHARE_COMPARISON_REQUIRED",
            "Private review preparation requires one checked static/React AppBundle comparison revision.",
            "Open viewspec.app.json with viewspec studio --compare --install first.",
        )

    snapshot = capture_source_snapshot(source_path, design_path=runtime.configuration.design_path)
    revision = runtime.built.revision
    if snapshot.source_sha256 != revision.source_sha256 or snapshot.design_sha256 != revision.design_sha256:
        raise StudioShareError(
            "STUDIO_SHARE_REVISION_STALE",
            "The workspace source or DESIGN.md no longer matches the checked Studio revision.",
            "Let Studio build the newest exact revision, review it, then prepare sharing again.",
        )

    comparison = _read_json_object(runtime.built.artifact_dir / STUDIO_COMPARE_MANIFEST, maximum=MAX_ARTIFACT_BYTES)
    reference_path = _workspace_path(reference, root=root, must_exist=True) if reference is not None else None
    output = _workspace_path(out_root, root=root, must_exist=False)
    _prepare_private_output_root(output, workspace=root)
    candidate: Path | None = Path(tempfile.mkdtemp(prefix=".candidate-", dir=output))
    candidate.chmod(0o700)
    created_final: Path | None = None
    created_archive: Path | None = None
    try:
        payload_root = candidate / STUDIO_SHARE_PAYLOAD_DIR
        payload_root.mkdir(mode=0o700)
        files: list[dict[str, object]] = []

        source_copy = runtime.built.revision_dir / "source.json"
        source_size, source_copy_sha256 = _regular_file_identity(source_copy, maximum=MAX_ARTIFACT_BYTES)
        if source_size == 0 or source_copy_sha256 != revision.source_sha256:
            raise StudioShareError(
                "STUDIO_SHARE_REVISION_INVALID",
                "The immutable Studio source copy no longer matches its revision hash.",
                "Discard the changed Review state and open a new Studio comparison.",
                cli_exit=1,
            )
        source_entry = _copy_payload_file(
            source_copy,
            payload_root=payload_root,
            archive_path="source/viewspec.app.json",
            role="semantic_source",
            maximum=MAX_ARTIFACT_BYTES,
            media_type="application/json",
        )
        files.append(source_entry)
        _assert_no_sensitive_pattern(payload_root / str(source_entry["path"]), noun="semantic source")

        design_copy = runtime.built.revision_dir / "DESIGN.md"
        if revision.design_sha256 is not None:
            design_size, design_copy_sha256 = _regular_file_identity(design_copy, maximum=MAX_ARTIFACT_BYTES)
            if design_size == 0 or design_copy_sha256 != revision.design_sha256:
                raise StudioShareError(
                    "STUDIO_SHARE_REVISION_INVALID",
                    "The immutable Studio DESIGN.md copy no longer matches its revision hash.",
                    "Discard the changed Review state and open a new Studio comparison.",
                    cli_exit=1,
                )
            design_entry = _copy_payload_file(
                design_copy,
                payload_root=payload_root,
                archive_path="design/DESIGN.md",
                role="design_system",
                maximum=MAX_ARTIFACT_BYTES,
                media_type="text/markdown",
            )
            files.append(design_entry)
            _assert_no_sensitive_pattern(payload_root / str(design_entry["path"]), noun="DESIGN.md")
        elif design_copy.exists():
            raise StudioShareError(
                "STUDIO_SHARE_REVISION_INVALID",
                "The checked revision contains an undeclared DESIGN.md copy.",
                "Discard the changed Review state and open a new Studio comparison.",
                cli_exit=1,
            )

        if reference_path is not None:
            reference_type = _reference_media_type(reference_path)
            reference_entry = _copy_payload_file(
                reference_path,
                payload_root=payload_root,
                archive_path=f"reference/reference{_REFERENCE_SUFFIXES[reference_type]}",
                role="reference_image",
                maximum=STUDIO_SHARE_REFERENCE_MAX_BYTES,
                media_type=reference_type,
            )
            files.append(reference_entry)

        for artifact_path in _regular_tree_files(runtime.built.artifact_dir):
            relative = artifact_path.relative_to(runtime.built.artifact_dir).as_posix()
            files.append(
                _copy_payload_file(
                    artifact_path,
                    payload_root=payload_root,
                    archive_path=f"artifacts/{relative}",
                    role="checked_artifact",
                    maximum=MAX_ARTIFACT_BYTES,
                    media_type=_media_type(artifact_path),
                )
            )

        files.sort(key=lambda item: str(item["path"]))
        _assert_payload_bounds(files)
        source_payload = _read_json_object(payload_root / "source/viewspec.app.json", maximum=MAX_ARTIFACT_BYTES)
        fixture_field_count = _fixture_field_count(source_payload)
        identity = _comparison_identity(comparison)
        disclosure = _disclosure(files, fixture_field_count=fixture_field_count)
        basis = {
            "schema_version": STUDIO_SHARE_SCHEMA_VERSION,
            "kind": "studio_private_review_upload_envelope",
            "status": "awaiting_disclosure_acceptance",
            "revision": {
                "number": revision.number,
                "source_kind": revision.source_kind,
                "source_sha256": revision.source_sha256,
                "design_sha256": revision.design_sha256,
                "target": revision.target,
                "artifact_set_sha256": revision.artifact_set_sha256,
                "root_manifest_kind": revision.root_manifest_kind,
                "root_manifest_sha256": revision.root_manifest_sha256,
                "compiler_version": revision.compiler_version,
                "contract_profile": revision.contract_profile,
                **identity,
            },
            "files": files,
            "totals": {
                "file_count": len(files),
                "bytes": sum(int(item["bytes"]) for item in files),
            },
            "disclosure": disclosure,
            "policy": dict(_STUDIO_SHARE_POLICY),
        }
        package_id = hashlib.sha256(canonical_json_bytes(basis)).hexdigest()
        envelope = {**basis, "package_id": package_id}
        _write_private_file(candidate / STUDIO_SHARE_ENVELOPE_NAME, canonical_json_bytes(envelope))
        _write_private_file(candidate / STUDIO_SHARE_DISCLOSURE_NAME, _disclosure_markdown(envelope).encode("utf-8"))
        _seal_private_tree(candidate)

        final = output / package_id
        action = "create"
        try:
            os.rename(candidate, final)
            created_final = final
            candidate = None
        except OSError:
            if not final.exists() and not final.is_symlink():
                raise
            existing = load_studio_share_package(final)
            if existing != envelope:
                raise StudioShareError(
                    "STUDIO_SHARE_PACKAGE_CONFLICT",
                    "A different package already occupies this content-addressed share path.",
                    "Inspect or move the conflicting private package; do not overwrite it.",
                    cli_exit=1,
                )
            action = "unchanged"
            shutil.rmtree(candidate)
            candidate = None
        checked = load_studio_share_package(final)
        assert checked == envelope
        archive_path, archive_action = _ensure_share_archive(final)
        if archive_action == "create":
            created_archive = archive_path
        envelope_sha256 = _sha256_file(final / STUDIO_SHARE_ENVELOPE_NAME)
        return {
            "schema_version": STUDIO_SHARE_SCHEMA_VERSION,
            "ok": True,
            "summary": "Private review package is prepared locally; nothing was uploaded.",
            "diagnostics": [],
            "external_refs": [],
            "errors": [],
            "paths": {
                "package": str(final),
                "envelope": str(final / STUDIO_SHARE_ENVELOPE_NAME),
                "disclosure": str(final / STUDIO_SHARE_DISCLOSURE_NAME),
                "upload_archive": str(archive_path),
            },
            "share": {
                "status": "awaiting_disclosure_acceptance",
                "package_action": action,
                "package_id": package_id,
                "envelope_sha256": envelope_sha256,
                "revision": revision.number,
                "source_sha256": revision.source_sha256,
                "file_count": envelope["totals"]["file_count"],
                "bytes": envelope["totals"]["bytes"],
                "archive_action": archive_action,
                "archive_bytes": archive_path.stat().st_size,
                "archive_sha256": _sha256_file(archive_path),
                "confirmation_required": True,
                "upload_performed": False,
                "capability_created": False,
            },
            "next_actions": [
                f"Read {final / STUDIO_SHARE_DISCLOSURE_NAME}.",
                f"The deterministic upload body is prepared at {archive_path}; it has not been sent.",
                "No upload command exists until a private review HTTPS deployment is authorized.",
            ],
            "metadata": {"sdk_version": __version__, "network_calls": "none"},
        }
    except Exception:
        if candidate is not None and candidate.exists() and candidate.parent == output and candidate.name.startswith(".candidate-"):
            shutil.rmtree(candidate)
        if (
            created_final is not None
            and created_final.exists()
            and created_final.parent == output
            and _HASH_RE.fullmatch(created_final.name) is not None
        ):
            shutil.rmtree(created_final)
        if created_archive is not None and created_archive.exists():
            created_archive.unlink()
        raise


def load_studio_share_package(package_dir: str | Path) -> dict[str, object]:
    """Revalidate every byte and identity in one prepared local share package."""

    package = Path(package_dir)
    if not package.is_absolute():
        package = Path(os.path.abspath(package))
    value = package.lstat()
    if package.is_symlink() or not stat.S_ISDIR(value.st_mode):
        raise StudioShareError(
            "STUDIO_SHARE_PACKAGE_INVALID",
            "Prepared Studio share package is not a private directory.",
            "Prepare a fresh local share package.",
            cli_exit=1,
        )
    envelope = _read_json_object(package / STUDIO_SHARE_ENVELOPE_NAME, maximum=512 * 1024)
    package_id = envelope.get("package_id")
    if not isinstance(package_id, str) or _HASH_RE.fullmatch(package_id) is None or package.name != package_id:
        raise _invalid_package("Package id does not match its content-addressed directory.")
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
    if (
        set(envelope) != expected_fields
        or envelope.get("schema_version") != STUDIO_SHARE_SCHEMA_VERSION
        or envelope.get("kind") != "studio_private_review_upload_envelope"
        or envelope.get("status") != "awaiting_disclosure_acceptance"
    ):
        raise _invalid_package("Share envelope fields or version are unsupported.")
    basis = {key: value for key, value in envelope.items() if key != "package_id"}
    if hashlib.sha256(canonical_json_bytes(basis)).hexdigest() != package_id:
        raise _invalid_package("Share envelope content changed after preparation.")
    if envelope.get("policy") != _STUDIO_SHARE_POLICY:
        raise _invalid_package("Share envelope policy is not the bounded no-upload policy.")
    revision = _validate_revision_identity(envelope.get("revision"))

    raw_files = envelope.get("files")
    if not isinstance(raw_files, list) or not 1 <= len(raw_files) <= STUDIO_SHARE_MAX_FILES:
        raise _invalid_package("Share envelope file inventory is missing or exceeds its bound.")
    expected_payload: set[str] = set()
    roles: dict[str, list[dict[str, object]]] = {}
    total = 0
    for entry in raw_files:
        if not isinstance(entry, dict) or set(entry) != {"role", "path", "bytes", "sha256", "media_type"}:
            raise _invalid_package("Share envelope contains an invalid file entry.")
        archive_path = _safe_archive_path(entry.get("path"))
        role = entry.get("role")
        media_type = entry.get("media_type")
        if not isinstance(role, str) or not isinstance(media_type, str):
            raise _invalid_package("Share envelope file role or media type is invalid.")
        _assert_role_path(role, archive_path, media_type=media_type)
        roles.setdefault(role, []).append(entry)
        if archive_path in expected_payload:
            raise _invalid_package("Share envelope repeats an archive path.")
        expected_payload.add(archive_path)
        size = entry.get("bytes")
        sha256 = entry.get("sha256")
        if type(size) is not int or not 0 <= size <= STUDIO_SHARE_MAX_BYTES:
            raise _invalid_package("Share envelope contains an invalid file size.")
        if not isinstance(sha256, str) or _HASH_RE.fullmatch(sha256) is None:
            raise _invalid_package("Share envelope contains an invalid file hash.")
        payload_path = package / STUDIO_SHARE_PAYLOAD_DIR / archive_path
        actual_size, actual_sha256 = _regular_file_identity(payload_path, maximum=STUDIO_SHARE_MAX_BYTES)
        if actual_size != size or actual_sha256 != sha256:
            raise _invalid_package(f"Prepared payload changed: {archive_path}.")
        total += size
    if total > STUDIO_SHARE_MAX_BYTES or envelope.get("totals") != {"file_count": len(raw_files), "bytes": total}:
        raise _invalid_package("Share envelope totals do not match its exact payload.")
    if (
        len(roles.get("semantic_source", [])) != 1
        or len(roles.get("design_system", [])) > 1
        or len(roles.get("reference_image", [])) > 1
        or not roles.get("checked_artifact")
        or set(roles) - {"semantic_source", "design_system", "reference_image", "checked_artifact"}
    ):
        raise _invalid_package("Share envelope role cardinality is invalid.")

    source_entry = roles["semantic_source"][0]
    if source_entry["sha256"] != revision["source_sha256"]:
        raise _invalid_package("Packaged semantic source does not match the checked revision hash.")
    design_entries = roles.get("design_system", [])
    if bool(design_entries) != (revision["design_sha256"] is not None):
        raise _invalid_package("Packaged DESIGN.md presence does not match the checked revision.")
    if design_entries and design_entries[0]["sha256"] != revision["design_sha256"]:
        raise _invalid_package("Packaged DESIGN.md does not match the checked revision hash.")
    artifact_entries = roles["checked_artifact"]
    if _artifact_inventory_sha256(artifact_entries) != revision["artifact_set_sha256"]:
        raise _invalid_package("Packaged artifact inventory does not match the checked revision hash.")
    root_manifest = next(
        (entry for entry in artifact_entries if entry["path"] == f"artifacts/{STUDIO_COMPARE_MANIFEST}"),
        None,
    )
    if root_manifest is None or root_manifest["sha256"] != revision["root_manifest_sha256"]:
        raise _invalid_package("Packaged comparison manifest does not match the checked revision.")

    actual_payload = {
        path.relative_to(package / STUDIO_SHARE_PAYLOAD_DIR).as_posix()
        for path in _regular_tree_files(package / STUDIO_SHARE_PAYLOAD_DIR)
    }
    if actual_payload != expected_payload:
        raise _invalid_package("Prepared package contains an unlisted or missing payload file.")
    source_payload = _read_json_object(package / STUDIO_SHARE_PAYLOAD_DIR / "source/viewspec.app.json", maximum=MAX_ARTIFACT_BYTES)
    comparison = _read_json_object(
        package / STUDIO_SHARE_PAYLOAD_DIR / f"artifacts/{STUDIO_COMPARE_MANIFEST}",
        maximum=MAX_ARTIFACT_BYTES,
    )
    expected_comparison_identity = _comparison_identity(comparison)
    if any(revision.get(key) != value for key, value in expected_comparison_identity.items()):
        raise _invalid_package("Packaged comparison identities do not match the share revision projection.")
    inspection_path = comparison.get("inspection", {}).get("path") if isinstance(comparison.get("inspection"), dict) else None
    if not isinstance(inspection_path, str):
        raise _invalid_package("Packaged comparison does not declare an inspection path.")
    inspection_entry = next(
        (entry for entry in artifact_entries if entry["path"] == f"artifacts/{inspection_path}"),
        None,
    )
    if inspection_entry is None or inspection_entry["sha256"] != revision["inspection_sha256"]:
        raise _invalid_package("Packaged inspection evidence does not match the share revision.")
    if envelope.get("disclosure") != _disclosure(raw_files, fixture_field_count=_fixture_field_count(source_payload)):
        raise _invalid_package("Share disclosure does not match the exact payload categories.")
    root_files = {path.name for path in package.iterdir() if path.is_file()}
    root_dirs = {path.name for path in package.iterdir() if path.is_dir()}
    if root_files != {STUDIO_SHARE_ENVELOPE_NAME, STUDIO_SHARE_DISCLOSURE_NAME} or root_dirs != {
        STUDIO_SHARE_PAYLOAD_DIR
    }:
        raise _invalid_package("Prepared package contains an unlisted root entry.")
    disclosure = (package / STUDIO_SHARE_DISCLOSURE_NAME).read_bytes()
    if disclosure != _disclosure_markdown(envelope).encode("utf-8"):
        raise _invalid_package("Human disclosure changed after preparation.")
    return envelope


def load_studio_share_archive(archive_path: str | Path) -> dict[str, object]:
    """Revalidate one deterministic transport archive without retaining its payload."""

    with tempfile.TemporaryDirectory(prefix="viewspec-share-ingress-") as directory:
        package = materialize_studio_share_archive(archive_path, directory)
        return load_studio_share_package(package)


def materialize_studio_share_archive(archive_path: str | Path, out_root: str | Path) -> Path:
    """Strictly materialize one transport archive into an empty private directory."""

    archive = Path(archive_path).resolve()
    try:
        archive_info = archive.lstat()
    except OSError as exc:
        raise _invalid_package("Studio share transport archive is unavailable.") from exc
    if (
        archive.is_symlink()
        or not stat.S_ISREG(archive_info.st_mode)
        or archive_info.st_nlink != 1
        or not 1 <= archive_info.st_size <= STUDIO_SHARE_ARCHIVE_MAX_BYTES
    ):
        raise _invalid_package("Studio share transport archive is linked, empty, or exceeds its bound.")
    root = Path(out_root).resolve()
    if root.exists():
        root_info = root.lstat()
        if root.is_symlink() or not stat.S_ISDIR(root_info.st_mode) or any(root.iterdir()):
            raise _invalid_package("Studio share ingress root must be one empty normal directory.")
    else:
        root.mkdir(mode=0o700, parents=True)
    root.chmod(0o700)

    package: Path | None = None
    try:
        with zipfile.ZipFile(archive) as transport:
            infos = transport.infolist()
            if not 3 <= len(infos) <= STUDIO_SHARE_MAX_FILES + 2:
                raise _invalid_package("Studio share archive member count is outside its bound.")
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise _invalid_package("Studio share archive repeats a member name.")
            indexed = {info.filename: info for info in infos}
            envelope_info = indexed.get(STUDIO_SHARE_ENVELOPE_NAME)
            if envelope_info is None or envelope_info.file_size > 512 * 1024:
                raise _invalid_package("Studio share archive has no bounded envelope.")
            total = 0
            for info in infos:
                name = _safe_archive_path(info.filename)
                mode = info.external_attr >> 16
                if (
                    info.is_dir()
                    or info.flag_bits & 0x1
                    or info.compress_type != zipfile.ZIP_STORED
                    or info.compress_size != info.file_size
                    or info.date_time != (1980, 1, 1, 0, 0, 0)
                    or not stat.S_ISREG(mode)
                    or stat.S_IMODE(mode) != 0o600
                    or not (
                        name in {STUDIO_SHARE_ENVELOPE_NAME, STUDIO_SHARE_DISCLOSURE_NAME}
                        or name.startswith(f"{STUDIO_SHARE_PAYLOAD_DIR}/")
                    )
                ):
                    raise _invalid_package("Studio share archive contains an unsafe or non-canonical member.")
                total += info.file_size
                if total > STUDIO_SHARE_ARCHIVE_MAX_BYTES:
                    raise _invalid_package("Studio share archive expands beyond its byte bound.")
            try:
                raw_envelope = transport.read(envelope_info)
                envelope = json.loads(raw_envelope)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, RuntimeError) as exc:
                raise _invalid_package("Studio share archive envelope is invalid.") from exc
            package_id = envelope.get("package_id") if isinstance(envelope, dict) else None
            if not isinstance(package_id, str) or _HASH_RE.fullmatch(package_id) is None:
                raise _invalid_package("Studio share archive package id is invalid.")
            package = root / package_id
            package.mkdir(mode=0o700)
            for info in infos:
                destination = package / PurePosixPath(info.filename)
                destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                try:
                    content = transport.read(info)
                except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                    raise _invalid_package("Studio share archive member could not be read exactly.") from exc
                if len(content) != info.file_size:
                    raise _invalid_package("Studio share archive member size changed during ingress.")
                _write_private_file(destination, content)
        _seal_private_tree(package)
        load_studio_share_package(package)
        return package
    except StudioShareError:
        if package is not None and package.exists():
            shutil.rmtree(package)
        raise
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        if package is not None and package.exists():
            shutil.rmtree(package)
        raise _invalid_package("Studio share transport archive is invalid.") from exc


def _ensure_share_archive(package: Path) -> tuple[Path, str]:
    envelope = load_studio_share_package(package)
    package_id = str(envelope["package_id"])
    archive = package.parent / f"{package_id}{STUDIO_SHARE_ARCHIVE_SUFFIX}"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{package_id}.",
        suffix=STUDIO_SHARE_ARCHIVE_SUFFIX,
        dir=package.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED, allowZip64=False) as transport:
            files = _regular_tree_files(package)
            for path in files:
                relative = path.relative_to(package).as_posix()
                info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_STORED
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o600) << 16
                transport.writestr(info, path.read_bytes())
        temporary.chmod(0o600)
        if temporary.stat().st_size > STUDIO_SHARE_ARCHIVE_MAX_BYTES:
            raise StudioShareError(
                "STUDIO_SHARE_PACKAGE_TOO_LARGE",
                f"Studio share transport archive exceeds {STUDIO_SHARE_ARCHIVE_MAX_BYTES} bytes.",
                "Reduce the checked product or omit the optional reference image.",
            )
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        load_studio_share_archive(temporary)
        if archive.exists() or archive.is_symlink():
            existing_size, existing_sha256 = _regular_file_identity(
                archive,
                maximum=STUDIO_SHARE_ARCHIVE_MAX_BYTES,
            )
            if existing_size != temporary.stat().st_size or existing_sha256 != _sha256_file(temporary):
                raise StudioShareError(
                    "STUDIO_SHARE_PACKAGE_CONFLICT",
                    "A different transport archive already exists for this exact package id.",
                    "Inspect or move the conflicting private archive; do not overwrite it.",
                    cli_exit=1,
                )
            temporary.unlink()
            return archive, "unchanged"
        os.rename(temporary, archive)
        descriptor = os.open(package.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return archive, "create"
    finally:
        if temporary.exists():
            temporary.unlink()


def _workspace_path(value: str | Path | None, *, root: Path, must_exist: bool) -> Path:
    if value is None:
        raise StudioShareError("STUDIO_SHARE_PATH_INVALID", "Required local path is missing.", "Pass a path under the workspace.")
    try:
        return resolve_local_path(value, cwd=root, must_exist=must_exist)
    except LocalToolError as exc:
        raise StudioShareError("STUDIO_SHARE_PATH_INVALID", str(exc), "Use one normal path under the workspace.") from exc


def _prepare_private_output_root(path: Path, *, workspace: Path) -> None:
    try:
        path.relative_to(workspace)
    except ValueError as exc:
        raise StudioShareError(
            "STUDIO_SHARE_PATH_INVALID",
            "Share package output must remain under the current workspace.",
            f"Use {STUDIO_SHARE_ROOT_DEFAULT} or another workspace-relative directory.",
        ) from exc
    if path.exists():
        value = path.lstat()
        if path.is_symlink() or not stat.S_ISDIR(value.st_mode):
            raise StudioShareError(
                "STUDIO_SHARE_PATH_INVALID",
                "Share package output is not a normal local directory.",
                "Choose a new workspace-relative output directory.",
            )
    else:
        path.mkdir(mode=0o700, parents=True)
    path.chmod(0o700)


def _copy_payload_file(
    source: Path,
    *,
    payload_root: Path,
    archive_path: str,
    role: str,
    maximum: int,
    media_type: str,
) -> dict[str, object]:
    safe_path = _safe_archive_path(archive_path)
    before = source.lstat()
    if source.is_symlink() or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size <= maximum:
        raise StudioShareError(
            "STUDIO_SHARE_FILE_UNSAFE",
            f"Share input is linked, non-regular, empty, or exceeds {maximum} bytes: {source.name}.",
            "Use the exact checked regular file and retry.",
        )
    destination = payload_root / safe_path
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    digest = hashlib.sha256()
    source_fd: int | None = None
    destination_fd: int | None = None
    copied = 0
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        source_fd = os.open(source, flags)
        opened = os.fstat(source_fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
            != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        ):
            raise _unsafe_change(source.name)
        destination_fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        while chunk := os.read(source_fd, min(1024 * 1024, maximum + 1 - copied)):
            copied += len(chunk)
            if copied > maximum:
                raise _unsafe_change(source.name)
            digest.update(chunk)
            _write_all(destination_fd, chunk)
        after = os.fstat(source_fd)
        if copied != before.st_size or (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns):
            raise _unsafe_change(source.name)
        os.fsync(destination_fd)
    finally:
        if source_fd is not None:
            os.close(source_fd)
        if destination_fd is not None:
            os.close(destination_fd)
    return {"role": role, "path": safe_path, "bytes": copied, "sha256": digest.hexdigest(), "media_type": media_type}


def _regular_tree_files(root: Path) -> tuple[Path, ...]:
    value = root.lstat()
    if root.is_symlink() or not stat.S_ISDIR(value.st_mode):
        raise _invalid_package("Expected payload root is not a normal directory.")
    files: list[Path] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode) and not path.is_symlink():
            continue
        if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise StudioShareError(
                "STUDIO_SHARE_FILE_UNSAFE",
                "Share payload contains a linked or non-regular filesystem entry.",
                "Rebuild the exact checked Studio revision and retry.",
                cli_exit=1,
            )
        files.append(path)
        if len(files) > STUDIO_SHARE_MAX_FILES:
            raise StudioShareError(
                "STUDIO_SHARE_PACKAGE_TOO_LARGE",
                f"Share payload exceeds {STUDIO_SHARE_MAX_FILES} files.",
                "Reduce the checked product before preparing private review.",
            )
    return tuple(files)


def _assert_payload_bounds(files: list[dict[str, object]]) -> None:
    if not 1 <= len(files) <= STUDIO_SHARE_MAX_FILES:
        raise StudioShareError(
            "STUDIO_SHARE_PACKAGE_TOO_LARGE",
            f"Share payload must contain 1 through {STUDIO_SHARE_MAX_FILES} files.",
            "Reduce the checked product before preparing private review.",
        )
    total = sum(int(item["bytes"]) for item in files)
    if total > STUDIO_SHARE_MAX_BYTES:
        raise StudioShareError(
            "STUDIO_SHARE_PACKAGE_TOO_LARGE",
            f"Share payload exceeds {STUDIO_SHARE_MAX_BYTES} bytes.",
            "Reduce the checked product or omit the optional reference image.",
        )


def _assert_no_sensitive_pattern(path: Path, *, noun: str) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise StudioShareError(
            "STUDIO_SHARE_FILE_UNSAFE",
            f"The {noun} is not UTF-8 text.",
            "Rebuild the checked revision from strict UTF-8 source.",
        ) from exc
    for name, pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            raise StudioShareError(
                "STUDIO_SHARE_SENSITIVE_PATTERN",
                f"The {noun} matches the forbidden sensitive-value pattern {name!r}.",
                "Remove or replace the sensitive value before preparing any upload package.",
            )


def _comparison_identity(comparison: dict[str, object]) -> dict[str, object]:
    screens = comparison.get("screens")
    routes = comparison.get("routes")
    inspection = comparison.get("inspection")
    targets = comparison.get("targets")
    if not isinstance(screens, list) or not isinstance(routes, list) or not isinstance(inspection, dict) or not isinstance(targets, list):
        raise StudioShareError(
            "STUDIO_SHARE_REVISION_INVALID",
            "Checked Studio comparison identity is incomplete.",
            "Rebuild the exact Studio comparison before preparing sharing.",
            cli_exit=1,
        )
    semantic = [
        {"screen_id": item.get("id"), "semantic_identity_sha256": item.get("semantic_identity_sha256")}
        for item in screens
        if isinstance(item, dict)
    ]
    return {
        "routes": routes,
        "screens": semantic,
        "inspection_sha256": inspection.get("sha256"),
        "target_artifact_sets": [
            {"id": item.get("id"), "artifact_set_sha256": item.get("artifact_set_sha256")}
            for item in targets
            if isinstance(item, dict)
        ],
    }


def _validate_revision_identity(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise _invalid_package("Share revision identity is missing.")
    expected = {
        "number",
        "source_kind",
        "source_sha256",
        "design_sha256",
        "target",
        "artifact_set_sha256",
        "root_manifest_kind",
        "root_manifest_sha256",
        "compiler_version",
        "contract_profile",
        "routes",
        "screens",
        "inspection_sha256",
        "target_artifact_sets",
    }
    if set(value) != expected:
        raise _invalid_package("Share revision identity fields are invalid.")
    if (
        type(value.get("number")) is not int
        or not 1 <= int(value["number"]) <= (2**63 - 1)
        or value.get("source_kind") != "app_bundle"
        or value.get("target") != STUDIO_COMPARE_TARGET
        or value.get("root_manifest_kind") != "studio_comparison_manifest"
        or value.get("contract_profile") != "local_v1"
        or not isinstance(value.get("compiler_version"), str)
        or not value["compiler_version"]
    ):
        raise _invalid_package("Share revision is not a checked Studio AppBundle comparison.")
    for key in (
        "source_sha256",
        "artifact_set_sha256",
        "root_manifest_sha256",
        "inspection_sha256",
    ):
        if not isinstance(value.get(key), str) or _HASH_RE.fullmatch(str(value[key])) is None:
            raise _invalid_package(f"Share revision contains an invalid {key}.")
    design_sha256 = value.get("design_sha256")
    if design_sha256 is not None and (not isinstance(design_sha256, str) or _HASH_RE.fullmatch(design_sha256) is None):
        raise _invalid_package("Share revision contains an invalid design_sha256.")
    routes = value.get("routes")
    screens = value.get("screens")
    target_sets = value.get("target_artifact_sets")
    if not isinstance(routes, list) or not routes or not isinstance(screens, list) or not screens:
        raise _invalid_package("Share revision must retain checked routes and screens.")
    if not isinstance(target_sets, list) or [item.get("id") for item in target_sets if isinstance(item, dict)] != [
        "static",
        "react",
    ]:
        raise _invalid_package("Share revision must retain the static and React artifact identities.")
    for item in screens:
        if (
            not isinstance(item, dict)
            or set(item) != {"screen_id", "semantic_identity_sha256"}
            or not isinstance(item.get("screen_id"), str)
            or not isinstance(item.get("semantic_identity_sha256"), str)
            or _HASH_RE.fullmatch(str(item["semantic_identity_sha256"])) is None
        ):
            raise _invalid_package("Share revision contains an invalid screen identity.")
    for item in target_sets:
        if (
            not isinstance(item, dict)
            or set(item) != {"id", "artifact_set_sha256"}
            or not isinstance(item.get("artifact_set_sha256"), str)
            or _HASH_RE.fullmatch(str(item["artifact_set_sha256"])) is None
        ):
            raise _invalid_package("Share revision contains an invalid target artifact identity.")
    return value


def _assert_role_path(role: str, path: str, *, media_type: str) -> None:
    valid = False
    if role == "semantic_source":
        valid = path == "source/viewspec.app.json" and media_type == "application/json"
    elif role == "design_system":
        valid = path == "design/DESIGN.md" and media_type == "text/markdown"
    elif role == "reference_image":
        valid = path in {"reference/reference.png", "reference/reference.jpg", "reference/reference.webp"} and media_type in {
            "image/png",
            "image/jpeg",
            "image/webp",
        }
    elif role == "checked_artifact":
        valid = path.startswith("artifacts/") and len(PurePosixPath(path).parts) > 1
    if not valid:
        raise _invalid_package("Share envelope role, archive path, and media type do not agree.")


def _artifact_inventory_sha256(entries: list[dict[str, object]]) -> str:
    digest = hashlib.sha256()
    digest.update(b"viewspec.review.artifact-set.v1\x00")
    normalized: list[tuple[str, int, str]] = []
    for entry in entries:
        path = str(entry["path"])
        if not path.startswith("artifacts/"):
            raise _invalid_package("Checked artifact path is outside its namespace.")
        normalized.append((path.removeprefix("artifacts/"), int(entry["bytes"]), str(entry["sha256"])))
    for relative, size, sha256 in sorted(normalized):
        record = canonical_json_bytes({"path": relative, "sha256": sha256, "size": size})
        digest.update(len(record).to_bytes(8, "big"))
        digest.update(record)
    return digest.hexdigest()


def _fixture_field_count(source: dict[str, object]) -> int:
    resources = source.get("resources")
    if not isinstance(resources, list):
        return 0
    count = 0
    for resource in resources:
        if not isinstance(resource, dict) or not isinstance(resource.get("records"), list):
            continue
        for record in resource["records"]:
            if isinstance(record, dict) and isinstance(record.get("fields"), dict):
                count += len(record["fields"])
    return count


def _disclosure(files: list[dict[str, object]], *, fixture_field_count: int) -> dict[str, object]:
    def category(role: str, label: str) -> dict[str, object]:
        matched = [item for item in files if item["role"] == role]
        return {"category": label, "file_count": len(matched), "bytes": sum(int(item["bytes"]) for item in matched)}

    return {
        "title": "Prepare a private ViewSpec review",
        "confirmation": "required_before_any_upload",
        "will_leave_machine": [
            category("semantic_source", "exact semantic source"),
            category("design_system", "exact DESIGN.md"),
            {
                "category": "declared fixture values embedded in semantic source",
                "field_count": fixture_field_count,
            },
            category("reference_image", "optional reference image"),
            category("checked_artifact", "checked static/React artifacts and manifests"),
            {"category": "future remote review comments", "current_count": 0},
        ],
        "will_not_leave_machine": [
            "absolute local paths",
            "environment variables",
            "existing local Review comments and journal",
            "local approval or server authority",
            "unrelated workspace files",
            "production data",
        ],
    }


def _disclosure_markdown(envelope: dict[str, object]) -> str:
    disclosure = envelope["disclosure"]
    assert isinstance(disclosure, dict)
    leaving = disclosure["will_leave_machine"]
    excluded = disclosure["will_not_leave_machine"]
    lines = [
        "# Prepare a private ViewSpec review",
        "",
        "Status: prepared locally; nothing uploaded; no review link or capability created.",
        "",
        f"Package: `{envelope['package_id']}`",
        f"Exact Studio revision: `{envelope['revision']['number']}`",
        f"Deterministic transport file: `../{envelope['package_id']}{STUDIO_SHARE_ARCHIVE_SUFFIX}` (still local).",
        "",
        "## Would leave this machine only after a future explicit confirmation",
        "",
    ]
    for item in leaving:
        assert isinstance(item, dict)
        details = []
        for key in ("file_count", "field_count", "current_count", "bytes"):
            if key in item:
                details.append(f"{key.replace('_', ' ')}: {item[key]}")
        suffix = f" ({', '.join(details)})" if details else ""
        lines.append(f"- {item['category']}{suffix}")
    lines.extend(["", "## Would not leave this machine", ""])
    lines.extend(f"- {item}" for item in excluded)
    lines.extend(
        [
            "",
            "The sensitive-value check is a bounded pattern scan, not certification that content is non-sensitive.",
            "Review the exact inventory in `envelope.json` before any future upload.",
            "",
        ]
    )
    return "\n".join(lines)


def _reference_media_type(path: Path) -> str:
    content = path.read_bytes()[:12]
    for media_type, signature in _REFERENCE_SIGNATURES:
        if not content.startswith(signature):
            continue
        if media_type == "image/webp" and content[8:12] != b"WEBP":
            break
        return media_type
    raise StudioShareError(
        "STUDIO_SHARE_REFERENCE_INVALID",
        "Optional share reference must be a PNG, JPEG, or WebP image.",
        "Choose the exact local reference image used for creation.",
    )


def _media_type(path: Path) -> str:
    return {
        ".css": "text/css",
        ".html": "text/html",
        ".js": "text/javascript",
        ".json": "application/json",
        ".map": "application/json",
        ".md": "text/markdown",
        ".tsx": "text/tsx",
    }.get(path.suffix.lower(), "application/octet-stream")


def _safe_archive_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise _invalid_package("Share archive path is invalid.")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} or _SAFE_ARCHIVE_SEGMENT_RE.fullmatch(part) is None for part in path.parts):
        raise _invalid_package("Share archive path escapes its bounded payload namespace.")
    return path.as_posix()


def _read_json_object(path: Path, *, maximum: int) -> dict[str, object]:
    size, _ = _regular_file_identity(path, maximum=maximum)
    if size == 0:
        raise _invalid_package(f"Required JSON file is empty: {path.name}.")
    try:
        value = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _invalid_package(f"Required JSON file is invalid: {path.name}.") from exc
    if not isinstance(value, dict):
        raise _invalid_package(f"Required JSON file is not an object: {path.name}.")
    return value


def _regular_file_identity(path: Path, *, maximum: int) -> tuple[int, str]:
    try:
        value = path.lstat()
    except OSError as exc:
        raise _invalid_package(f"Required share file is unavailable: {path.name}.") from exc
    if path.is_symlink() or not stat.S_ISREG(value.st_mode) or value.st_nlink != 1 or value.st_size > maximum:
        raise _invalid_package(f"Share file is linked, non-regular, or exceeds {maximum} bytes: {path.name}.")
    return value.st_size, _sha256_file(path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _write_private_file(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        _write_all(descriptor, content)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    written = 0
    while written < len(view):
        count = os.write(descriptor, view[written:])
        if count <= 0:
            raise OSError("short write while preparing Studio share package")
        written += count


def _seal_private_tree(root: Path) -> None:
    directories: list[Path] = [root]
    for path in root.rglob("*"):
        value = path.lstat()
        if path.is_symlink():
            raise _invalid_package("Prepared package contains a symbolic link.")
        if stat.S_ISDIR(value.st_mode):
            path.chmod(0o700)
            directories.append(path)
        elif stat.S_ISREG(value.st_mode):
            path.chmod(0o600)
        else:
            raise _invalid_package("Prepared package contains a non-regular filesystem entry.")
    for directory in reversed(directories):
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _unsafe_change(name: str) -> StudioShareError:
    return StudioShareError(
        "STUDIO_SHARE_FILE_CHANGED",
        f"Share input changed while it was being captured: {name}.",
        "Stop changing the input and prepare a fresh package.",
    )


def _invalid_package(message: str) -> StudioShareError:
    return StudioShareError(
        "STUDIO_SHARE_PACKAGE_INVALID",
        message,
        "Do not upload this package; prepare a fresh package from the exact checked revision.",
        cli_exit=1,
    )


__all__ = [
    "STUDIO_SHARE_ARCHIVE_MAX_BYTES",
    "STUDIO_SHARE_ARCHIVE_SUFFIX",
    "STUDIO_SHARE_DISCLOSURE_NAME",
    "STUDIO_SHARE_ENVELOPE_NAME",
    "STUDIO_SHARE_MAX_BYTES",
    "STUDIO_SHARE_MAX_FILES",
    "STUDIO_SHARE_REFERENCE_MAX_BYTES",
    "STUDIO_SHARE_ROOT_DEFAULT",
    "STUDIO_SHARE_SCHEMA_VERSION",
    "StudioShareError",
    "load_studio_share_archive",
    "load_studio_share_package",
    "materialize_studio_share_archive",
    "prepare_studio_share",
]
