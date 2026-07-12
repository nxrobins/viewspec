"""Verified client contract for paid hosted artifact delivery."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from viewspec.types import CompileRequestPayload, IntentBundle


ARTIFACT_SCHEMA_VERSION = 1
ARTIFACT_TARGETS = ("html-tailwind", "react-tsx", "swiftui", "flutter")


class ArtifactContractError(ValueError):
    """Raised when a hosted artifact response fails integrity validation."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _mapping(payload: Any, name: str) -> dict:
    if not isinstance(payload, dict):
        raise ArtifactContractError(f"{name} must be an object")
    return payload


@dataclass(frozen=True)
class ArtifactFile:
    path: str
    role: str
    content_type: str
    sha256: str
    bytes: int
    content: str

    @classmethod
    def from_json(cls, payload: Any) -> ArtifactFile:
        data = _mapping(payload, "artifact file")
        path = data.get("path")
        content = data.get("content")
        if not isinstance(path, str) or not path or Path(path).name != path or "/" in path or "\\" in path:
            raise ArtifactContractError("Artifact file path must be a safe basename")
        if not isinstance(content, str):
            raise ArtifactContractError(f"Artifact file {path} content must be text")
        encoded = content.encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        if data.get("sha256") != digest:
            raise ArtifactContractError(f"Artifact file {path} hash does not match content")
        if data.get("bytes") != len(encoded):
            raise ArtifactContractError(f"Artifact file {path} byte count does not match content")
        role = data.get("role")
        content_type = data.get("content_type")
        if not isinstance(role, str) or not role:
            raise ArtifactContractError(f"Artifact file {path} role is required")
        if not isinstance(content_type, str) or not content_type:
            raise ArtifactContractError(f"Artifact file {path} content_type is required")
        return cls(path, role, content_type, digest, len(encoded), content)


@dataclass(frozen=True)
class ArtifactProvenance:
    manifest_file: str | None
    sha256: str | None
    entry_count: int

    @classmethod
    def from_json(cls, payload: Any) -> ArtifactProvenance:
        data = _mapping(payload, "artifact provenance")
        count = data.get("entry_count")
        if type(count) is not int or count < 0:
            raise ArtifactContractError("Artifact provenance entry_count must be a non-negative integer")
        return cls(data.get("manifest_file"), data.get("sha256"), count)


@dataclass(frozen=True)
class ArtifactUsage:
    tier: str
    usage: int
    limit: int | None

    @classmethod
    def from_json(cls, payload: Any) -> ArtifactUsage:
        data = _mapping(payload, "artifact usage")
        if not isinstance(data.get("tier"), str) or type(data.get("usage")) is not int:
            raise ArtifactContractError("Artifact usage metadata is invalid")
        limit = data.get("limit")
        if limit is not None and type(limit) is not int:
            raise ArtifactContractError("Artifact usage limit must be an integer or null")
        return cls(data["tier"], data["usage"], limit)


@dataclass(frozen=True)
class ArtifactResponse:
    schema_version: int
    build_id: str
    target: str
    input_sha256: str
    artifact_set_sha256: str
    files: tuple[ArtifactFile, ...]
    provenance: ArtifactProvenance
    diagnostics: tuple[dict, ...]
    usage: ArtifactUsage

    @classmethod
    def from_json(
        cls,
        payload: Any,
        *,
        expected_input: dict | None = None,
        expected_target: str | None = None,
    ) -> ArtifactResponse:
        data = _mapping(payload, "artifact response")
        if data.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
            raise ArtifactContractError("Unsupported artifact schema_version")
        target = data.get("target")
        if target not in ARTIFACT_TARGETS or (expected_target is not None and target != expected_target):
            raise ArtifactContractError("Artifact target does not match the request")
        raw_files = data.get("files")
        if not isinstance(raw_files, list) or not raw_files:
            raise ArtifactContractError("Artifact response must contain files")
        files = tuple(ArtifactFile.from_json(file) for file in raw_files)
        if len({file.path for file in files}) != len(files):
            raise ArtifactContractError("Artifact file paths must be unique")

        input_hash = data.get("input_sha256")
        if expected_input is not None:
            expected_input_hash = hashlib.sha256(_canonical(expected_input)).hexdigest()
            if input_hash != expected_input_hash:
                raise ArtifactContractError("Artifact input hash does not match the request")
        artifact_index = sorted(
            ({"path": file.path, "sha256": file.sha256} for file in files),
            key=lambda item: item["path"],
        )
        artifact_set_hash = hashlib.sha256(_canonical(artifact_index)).hexdigest()
        if data.get("artifact_set_sha256") != artifact_set_hash:
            raise ArtifactContractError("Artifact set hash does not match files")
        material = {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "target": target,
            "input_sha256": input_hash,
            "artifact_set_sha256": artifact_set_hash,
        }
        build_id = f"vsb_{hashlib.sha256(_canonical(material)).hexdigest()[:32]}"
        if data.get("build_id") != build_id:
            raise ArtifactContractError("Artifact build_id does not match build material")
        diagnostics = data.get("diagnostics")
        if not isinstance(diagnostics, list) or not all(isinstance(item, dict) for item in diagnostics):
            raise ArtifactContractError("Artifact diagnostics must be a list of objects")
        return cls(
            ARTIFACT_SCHEMA_VERSION,
            build_id,
            target,
            input_hash,
            artifact_set_hash,
            files,
            ArtifactProvenance.from_json(data.get("provenance")),
            tuple(diagnostics),
            ArtifactUsage.from_json(data.get("usage")),
        )


def _request_payload(request: IntentBundle | CompileRequestPayload) -> dict:
    if isinstance(request, CompileRequestPayload):
        return request.to_json()
    if isinstance(request, IntentBundle):
        return request.to_json()
    raise TypeError("Expected IntentBundle or CompileRequestPayload")


def compile_artifact_remote(
    request: IntentBundle | CompileRequestPayload,
    target: str,
    *,
    api_url: str = "https://api.viewspec.dev",
    api_key: str | None = None,
) -> ArtifactResponse:
    """Compile and integrity-check one paid hosted artifact target."""
    from viewspec.compiler import CompilerAPIError

    if target not in ARTIFACT_TARGETS:
        raise ValueError(f"target must be one of: {', '.join(ARTIFACT_TARGETS)}")
    try:
        import httpx
    except ImportError:
        raise ImportError(
            "httpx is required for remote artifacts. Install it: pip install viewspec[remote]"
        ) from None
    bundle = _request_payload(request)
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        response = httpx.post(
            f"{api_url.rstrip('/')}/v1/artifacts",
            json={"target": target, "bundle": bundle},
            headers=headers,
            timeout=45.0,
        )
    except httpx.HTTPError as exc:
        raise CompilerAPIError(f"Remote artifact request failed: {exc}") from exc
    try:
        data = response.json()
    except ValueError as exc:
        raise CompilerAPIError("Artifact request failed: response was not valid JSON") from exc
    if response.status_code != 200:
        message = ""
        if isinstance(data, dict):
            error = data.get("error")
            if isinstance(error, dict):
                message = str(error.get("message", ""))
        raise CompilerAPIError(message or f"Artifact request failed (HTTP {response.status_code})")
    try:
        return ArtifactResponse.from_json(data, expected_input=bundle, expected_target=target)
    except ArtifactContractError:
        raise
    except Exception as exc:
        raise ArtifactContractError("Artifact response was invalid") from exc
