"""Verified client contract for hosted AppBundle project builds."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tempfile
from typing import Any, Mapping

from viewspec.hosted_receipts import ReceiptPublicKey, verify_signed_receipt


APP_BUILD_SCHEMA_VERSION = 1
APP_BUILD_TARGET = "react-tailwind-app"
APP_BUILD_MANIFEST = "hosted_app_manifest.json"
MAX_APP_BUILD_FILES = 96
MAX_APP_BUILD_FILE_BYTES = 5_000_000
MAX_APP_BUILD_TOTAL_BYTES = 10_000_000
MAX_APP_BUILD_PATH_BYTES = 512


class AppBundleBuildContractError(ValueError):
    """Raised when a hosted AppBundle build fails integrity verification."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _mapping(payload: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise AppBundleBuildContractError(f"{name} must be an object")
    return payload


def _safe_path(candidate: Any) -> str:
    if not isinstance(candidate, str) or not candidate or "\0" in candidate or "\\" in candidate:
        raise AppBundleBuildContractError("App build file path must be a non-empty POSIX path")
    if len(candidate.encode("utf-8")) > MAX_APP_BUILD_PATH_BYTES:
        raise AppBundleBuildContractError("App build file path is too long")
    path = PurePosixPath(candidate)
    if path.is_absolute() or path.as_posix() != candidate or any(part in {"", ".", ".."} for part in path.parts):
        raise AppBundleBuildContractError("App build file path must be canonical and relative")
    return candidate


@dataclass(frozen=True)
class AppBuildFile:
    path: str
    role: str
    content_type: str
    sha256: str
    bytes: int
    content: str

    @classmethod
    def from_json(cls, payload: Any) -> AppBuildFile:
        data = _mapping(payload, "app build file")
        path = _safe_path(data.get("path"))
        content = data.get("content")
        if not isinstance(content, str):
            raise AppBundleBuildContractError(f"App build file {path} content must be text")
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_APP_BUILD_FILE_BYTES:
            raise AppBundleBuildContractError(f"App build file {path} exceeds the client size limit")
        digest = hashlib.sha256(encoded).hexdigest()
        if data.get("sha256") != digest or data.get("bytes") != len(encoded):
            raise AppBundleBuildContractError(f"App build file {path} integrity metadata does not match content")
        role = data.get("role")
        content_type = data.get("content_type")
        if not isinstance(role, str) or not role or not isinstance(content_type, str) or not content_type:
            raise AppBundleBuildContractError(f"App build file {path} metadata is incomplete")
        return cls(path, role, content_type, digest, len(encoded), content)


@dataclass(frozen=True)
class AppBuildProvenance:
    manifest_file: str
    sha256: str
    entry_count: int


@dataclass(frozen=True)
class AppBuildUsage:
    tier: str
    usage: int
    limit: int | None

    @classmethod
    def from_json(cls, payload: Any) -> AppBuildUsage:
        data = _mapping(payload, "app build usage")
        if not isinstance(data.get("tier"), str) or type(data.get("usage")) is not int:
            raise AppBundleBuildContractError("App build usage metadata is invalid")
        limit = data.get("limit")
        if limit is not None and type(limit) is not int:
            raise AppBundleBuildContractError("App build usage limit must be an integer or null")
        return cls(str(data["tier"]), int(data["usage"]), limit)


@dataclass(frozen=True)
class AppBundleBuildResponse:
    schema_version: int
    build_id: str
    target: str
    app_schema_version: int
    sdk_version: str
    input_sha256: str
    artifact_set_sha256: str
    files: tuple[AppBuildFile, ...]
    provenance: AppBuildProvenance
    usage: AppBuildUsage
    receipt: Mapping[str, Any]

    @classmethod
    def from_json(
        cls,
        payload: Any,
        *,
        expected_input: dict,
        receipt_public_key: ReceiptPublicKey | Mapping[str, Any],
    ) -> AppBundleBuildResponse:
        data = _mapping(payload, "AppBundle build response")
        if data.get("schema_version") != APP_BUILD_SCHEMA_VERSION or data.get("target") != APP_BUILD_TARGET:
            raise AppBundleBuildContractError("Unsupported hosted AppBundle build contract")
        app_schema_version = data.get("app_schema_version")
        sdk_version = data.get("sdk_version")
        if type(app_schema_version) is not int or not isinstance(sdk_version, str) or not sdk_version:
            raise AppBundleBuildContractError("AppBundle build compiler identity is invalid")
        raw_files = data.get("files")
        if not isinstance(raw_files, list) or not raw_files:
            raise AppBundleBuildContractError("AppBundle build response must contain files")
        if len(raw_files) > MAX_APP_BUILD_FILES:
            raise AppBundleBuildContractError("AppBundle build response contains too many files")
        files = tuple(AppBuildFile.from_json(item) for item in raw_files)
        if sum(item.bytes for item in files) > MAX_APP_BUILD_TOTAL_BYTES:
            raise AppBundleBuildContractError("AppBundle build response exceeds the client size limit")
        if len({item.path for item in files}) != len(files):
            raise AppBundleBuildContractError("AppBundle build file paths must be unique")
        files_by_path = {item.path: item for item in files}

        provenance_data = _mapping(data.get("provenance"), "app build provenance")
        manifest_path = _safe_path(provenance_data.get("manifest_file"))
        manifest_file = files_by_path.get(manifest_path)
        entry_count = provenance_data.get("entry_count")
        if (
            manifest_file is None
            or manifest_file.role != "manifest"
            or provenance_data.get("sha256") != manifest_file.sha256
            or type(entry_count) is not int
            or entry_count < 0
        ):
            raise AppBundleBuildContractError("AppBundle build provenance does not match its manifest file")
        try:
            manifest = json.loads(manifest_file.content)
        except json.JSONDecodeError as exc:
            raise AppBundleBuildContractError("AppBundle build manifest is not valid JSON") from exc

        input_hash = hashlib.sha256(_canonical(expected_input)).hexdigest()
        if data.get("input_sha256") != input_hash:
            raise AppBundleBuildContractError("AppBundle build input hash does not match the request")
        expected_manifest_files = [
            {"bytes": item.bytes, "path": item.path, "role": item.role, "sha256": item.sha256}
            for item in files
            if item.path != manifest_path
        ]
        if not isinstance(manifest, dict) or manifest.get("files") != expected_manifest_files:
            raise AppBundleBuildContractError("AppBundle build manifest does not account for every returned file")
        if (
            entry_count != len(expected_manifest_files)
            or manifest.get("schema_version") != APP_BUILD_SCHEMA_VERSION
            or manifest.get("target") != APP_BUILD_TARGET
            or manifest.get("input_sha256") != input_hash
            or manifest.get("app_schema_version") != app_schema_version
            or manifest.get("sdk_version") != sdk_version
        ):
            raise AppBundleBuildContractError("AppBundle build manifest identity does not match the response")

        artifact_index = [{"path": item.path, "sha256": item.sha256} for item in files]
        artifact_set_hash = hashlib.sha256(_canonical(artifact_index)).hexdigest()
        if data.get("artifact_set_sha256") != artifact_set_hash:
            raise AppBundleBuildContractError("AppBundle artifact set hash does not match files")
        material = {
            "schema_version": APP_BUILD_SCHEMA_VERSION,
            "target": APP_BUILD_TARGET,
            "input_sha256": input_hash,
            "artifact_set_sha256": artifact_set_hash,
        }
        build_id = f"vab_{hashlib.sha256(_canonical(material)).hexdigest()[:32]}"
        if data.get("build_id") != build_id:
            raise AppBundleBuildContractError("AppBundle build_id does not match build material")

        receipt = _mapping(data.get("receipt"), "app build receipt")
        receipt_payload = receipt.get("payload")
        expected_receipt_fields = {
            "receipt_type": "viewspec_app_build_v1",
            "build_id": build_id,
            "target": APP_BUILD_TARGET,
            "input_sha256": input_hash,
            "artifact_set_sha256": artifact_set_hash,
            "manifest_sha256": manifest_file.sha256,
            "app_schema_version": app_schema_version,
            "sdk_version": sdk_version,
        }
        if not isinstance(receipt_payload, dict) or any(
            receipt_payload.get(key) != value for key, value in expected_receipt_fields.items()
        ):
            raise AppBundleBuildContractError("Signed AppBundle receipt does not match the build identity")
        if not isinstance(receipt_payload.get("issued_at"), str) or not verify_signed_receipt(
            receipt, receipt_public_key
        ):
            raise AppBundleBuildContractError("AppBundle build receipt signature is invalid")

        return cls(
            APP_BUILD_SCHEMA_VERSION,
            build_id,
            APP_BUILD_TARGET,
            app_schema_version,
            sdk_version,
            input_hash,
            artifact_set_hash,
            files,
            AppBuildProvenance(manifest_path, manifest_file.sha256, entry_count),
            AppBuildUsage.from_json(data.get("usage")),
            receipt,
        )

    def write_to(self, output_dir: str | Path) -> Path:
        """Materialize the already-verified build without overwriting an existing directory."""
        destination = Path(output_dir).resolve()
        if destination.exists():
            raise FileExistsError(f"AppBundle output already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
        try:
            for item in self.files:
                path = staging.joinpath(*PurePosixPath(item.path).parts)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(item.content, encoding="utf-8")
            staging.replace(destination)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return destination


def compile_app_remote(
    app_bundle: dict,
    *,
    api_url: str = "https://api.viewspec.dev",
    api_key: str | None = None,
    receipt_public_key: ReceiptPublicKey | Mapping[str, Any] | None = None,
) -> AppBundleBuildResponse:
    """Build and fully verify one runnable hosted AppBundle project."""
    from viewspec.compiler import CompilerAPIError, _compiler_api_error

    if not isinstance(app_bundle, dict):
        raise TypeError("app_bundle must be a JSON object")
    try:
        import httpx
    except ImportError:
        raise ImportError("httpx is required for hosted AppBundle builds. Install it: pip install viewspec[remote]") from None
    base_url = api_url.rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        response = httpx.post(
            f"{base_url}/v1/app-bundles/build",
            json={"target": APP_BUILD_TARGET, "app_bundle": app_bundle},
            headers=headers,
            timeout=60.0,
        )
    except httpx.HTTPError as exc:
        raise CompilerAPIError(f"Remote AppBundle build failed: {exc}", code="network_error") from exc
    try:
        data = response.json()
    except ValueError as exc:
        raise CompilerAPIError("AppBundle build failed: response was not valid JSON") from exc
    if response.status_code != 200:
        raise _compiler_api_error(response, data)
    if receipt_public_key is None:
        try:
            key_response = httpx.get(f"{base_url}/v1/receipt-key", timeout=15.0)
            key_data = key_response.json()
        except httpx.HTTPError as exc:
            raise CompilerAPIError(f"Receipt key request failed: {exc}", code="network_error") from exc
        except ValueError as exc:
            raise CompilerAPIError("Receipt key response was not valid JSON") from exc
        if key_response.status_code != 200:
            raise _compiler_api_error(key_response, key_data)
        receipt_public_key = key_data
    return AppBundleBuildResponse.from_json(
        data,
        expected_input=app_bundle,
        receipt_public_key=receipt_public_key,
    )


__all__ = [
    "APP_BUILD_MANIFEST",
    "APP_BUILD_SCHEMA_VERSION",
    "APP_BUILD_TARGET",
    "AppBuildFile",
    "AppBuildProvenance",
    "AppBuildUsage",
    "AppBundleBuildContractError",
    "AppBundleBuildResponse",
    "compile_app_remote",
]
