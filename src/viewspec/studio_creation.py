"""Bounded first-creation handoff from a product brief to checked semantic source."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any

from viewspec._version import __version__
from viewspec.app_bundle import prove_app
from viewspec.app_reports import APP_BUNDLE_TARGET
from viewspec.app_starters import starter_app_bundle, starter_react_app_bundle
from viewspec.intent_tools import STARTER_INTENT_KINDS, starter_intent_payload
from viewspec.local_tools import LocalToolError, atomic_write, resolve_cwd, resolve_local_path
from viewspec.prove import prove
from viewspec.review_compile import capture_source_snapshot
from viewspec.review_contract import ReviewContractError, canonical_json_bytes


STUDIO_CREATION_SCHEMA_VERSION = 1
STUDIO_CREATION_TASK_DEFAULT = ".viewspec/studio-creation-task.json"
STUDIO_CREATION_TASK_MAX_BYTES = 128 * 1024
STUDIO_CREATION_BRIEF_MAX_BYTES = 32 * 1024
STUDIO_CREATION_REFERENCE_MAX_BYTES = 10 * 1024 * 1024
STUDIO_CREATION_SOURCE_KINDS = ("app_bundle", "intent_bundle")
STUDIO_CREATION_KIND_ALIASES = {"app": "app_bundle", "view": "intent_bundle"}
STUDIO_CREATION_SOURCE_PATHS = {
    "app_bundle": "viewspec.app.json",
    "intent_bundle": "viewspec.intent.json",
}
STUDIO_CREATION_CANDIDATE_PATHS = {
    "app_bundle": ".viewspec/studio-candidate.app.json",
    "intent_bundle": ".viewspec/studio-candidate.intent.json",
}
STUDIO_CREATION_SCHEMA_IDS = {
    "app_bundle": "https://viewspec.dev/agent-app-bundle.schema.json",
    "intent_bundle": "https://viewspec.dev/agent-intent-bundle.schema.json",
}
STUDIO_CREATION_ACCEPTANCE = {
    "artifact_check": "required",
    "candidate_validation": "required",
    "generated_output_editable": False,
    "network_calls": "none",
    "reference_fidelity": "not_proven",
    "starter_copy": "forbidden",
}
_TASK_FIELDS = {
    "acceptance",
    "brief",
    "brief_sha256",
    "candidate_path",
    "candidate_schema",
    "contract_profile",
    "proof_path",
    "reference",
    "schema_version",
    "source_kind",
    "source_path",
    "status",
    "task_id",
}
_REFERENCE_FIELDS = {"bytes", "height", "media_type", "path", "sha256", "width"}
_REFERENCE_MEDIA_TYPES = {"image/jpeg", "image/png", "image/webp"}


class StudioCreationError(ValueError):
    """Stable fail-closed error for the Studio first-creation contract."""

    def __init__(self, code: str, message: str, fix: str, *, cli_exit: int = 2) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.fix = fix
        self.cli_exit = cli_exit

    def to_json(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "fix": self.fix}


def prepare_studio_creation(
    *,
    brief: str | None = None,
    brief_file: str | Path | None = None,
    reference: str | Path | None = None,
    kind: str = "app",
    task_out: str | Path = STUDIO_CREATION_TASK_DEFAULT,
    cwd: str | Path | None = None,
) -> dict[str, object]:
    """Write one deterministic, local-only agent creation task without inventing source."""

    root = resolve_cwd(cwd)
    source_kind = _source_kind(kind)
    _require_blank_workspace(root)
    normalized_brief = _load_brief(brief=brief, brief_file=brief_file, root=root)
    reference_identity = _reference_identity(reference, root=root) if reference is not None else None
    source_path = STUDIO_CREATION_SOURCE_PATHS[source_kind]
    candidate_path = STUDIO_CREATION_CANDIDATE_PATHS[source_kind]
    task_id = _task_id(
        source_kind=source_kind,
        brief_sha256=_sha256(normalized_brief.encode("utf-8")),
        reference=reference_identity,
        source_path=source_path,
    )
    proof_path = f".viewspec/studio-creation/{task_id}"
    task = {
        "schema_version": STUDIO_CREATION_SCHEMA_VERSION,
        "task_id": task_id,
        "status": "awaiting_agent",
        "contract_profile": "local_v1",
        "source_kind": source_kind,
        "source_path": source_path,
        "candidate_path": candidate_path,
        "candidate_schema": STUDIO_CREATION_SCHEMA_IDS[source_kind],
        "proof_path": proof_path,
        "brief": normalized_brief,
        "brief_sha256": _sha256(normalized_brief.encode("utf-8")),
        "reference": reference_identity,
        "acceptance": dict(STUDIO_CREATION_ACCEPTANCE),
    }
    _validate_task(task)
    task_path = _local_path(task_out, root=root, must_exist=False)
    if task_path in {root / source_path, root / candidate_path}:
        raise StudioCreationError(
            "STUDIO_CREATION_PATH_INVALID",
            "The Studio creation task cannot replace its reserved candidate or canonical source path.",
            f"Use the default task path {STUDIO_CREATION_TASK_DEFAULT} or another dedicated JSON file.",
        )
    task_text = json.dumps(task, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    action = "create"
    if task_path.exists():
        if not task_path.is_file() or task_path.is_symlink():
            raise StudioCreationError(
                "STUDIO_CREATION_TASK_EXISTS",
                "The requested Studio creation task path is not a replaceable regular file.",
                "Choose a new --task-out path under the workspace.",
            )
        if task_path.read_text(encoding="utf-8") != task_text:
            raise StudioCreationError(
                "STUDIO_CREATION_TASK_EXISTS",
                "A different Studio creation task already exists at the requested path.",
                "Finish or move the existing task before preparing another first creation.",
            )
        action = "unchanged"
    else:
        atomic_write(task_path, task_text)
    return {
        "schema_version": 1,
        "ok": True,
        "summary": "Studio creation task is ready for your agent.",
        "diagnostics": [],
        "external_refs": [],
        "errors": [],
        "paths": {
            "task": str(task_path),
            "candidate": str(root / candidate_path),
            "source": str(root / source_path),
            "proof": str(root / proof_path),
        },
        "creation": {**task, "task_action": action},
        "next_actions": [
            f"Author the exact {source_kind} candidate at {candidate_path} from this task; do not copy a starter.",
            f"Run viewspec studio-accept {task_path.relative_to(root).as_posix()} --json.",
            "Open the checked result with viewspec studio.",
        ],
        "metadata": {
            "sdk_version": __version__,
            "network_calls": "none",
            "reference_uploaded": False,
        },
    }


def accept_studio_creation(
    task_path: str | Path = STUDIO_CREATION_TASK_DEFAULT,
    *,
    cwd: str | Path | None = None,
) -> dict[str, object]:
    """Prove one task-bound agent candidate, then publish canonical source exactly once."""

    root = resolve_cwd(cwd)
    task_file = _local_path(task_path, root=root, must_exist=True)
    task = _read_task(task_file)
    _require_blank_workspace(root)
    _verify_reference(task.get("reference"), root=root)
    candidate_path = _local_path(str(task["candidate_path"]), root=root, must_exist=True)
    try:
        snapshot = capture_source_snapshot(candidate_path)
    except ReviewContractError as exc:
        raise StudioCreationError(
            "STUDIO_CREATION_CANDIDATE_INVALID",
            f"Candidate capture failed with {exc.code}: {exc.message}",
            f"Fix only {task['candidate_path']}, then rerun studio-accept.",
        ) from exc
    if snapshot.source_kind != task["source_kind"]:
        raise StudioCreationError(
            "STUDIO_CREATION_CANDIDATE_INVALID",
            f"The candidate is {snapshot.source_kind}, but the task requires {task['source_kind']}.",
            f"Rewrite {task['candidate_path']} against {task['candidate_schema']}.",
        )
    try:
        candidate_payload = json.loads(snapshot.source_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StudioCreationError(
            "STUDIO_CREATION_CANDIDATE_INVALID",
            "The Studio creation candidate is not strict UTF-8 JSON.",
            f"Rewrite {task['candidate_path']} as one strict JSON object.",
        ) from exc
    if _is_unchanged_starter(candidate_payload, source_kind=str(task["source_kind"])):
        raise StudioCreationError(
            "STUDIO_CREATION_STARTER_FORBIDDEN",
            "An unchanged ViewSpec starter cannot satisfy a real product brief.",
            "Replace all sample content and structure with the product described by the creation task.",
        )

    proof_path = _local_path(str(task["proof_path"]), root=root, must_exist=False)
    captured_candidate_path = _captured_candidate_path(
        proof_path=proof_path,
        task_id=str(task["task_id"]),
        source_kind=str(task["source_kind"]),
        source_sha256=snapshot.source_sha256,
    )
    _retain_immutable(captured_candidate_path, snapshot.source_bytes)
    proof = _prove_creation_candidate(
        candidate_path=captured_candidate_path,
        source_kind=str(task["source_kind"]),
        proof_path=proof_path,
        root=root,
    )
    if proof.get("ok") is not True:
        errors = proof.get("errors") if isinstance(proof.get("errors"), list) else []
        first = errors[0] if errors and isinstance(errors[0], dict) else {}
        code = str(first.get("code", "STUDIO_CREATION_PROOF_FAILED"))
        message = str(first.get("message", "The candidate did not pass its local creation proof."))
        raise StudioCreationError(
            "STUDIO_CREATION_PROOF_FAILED",
            f"Candidate proof failed with {code}: {message}"[:2048],
            f"Fix only {task['candidate_path']}, then rerun studio-accept; canonical source was not published.",
        )

    source_path = _local_path(str(task["source_path"]), root=root, must_exist=False)
    _publish_exclusive(source_path, snapshot.source_bytes)
    source_sha256 = _sha256(snapshot.source_bytes)
    return {
        "schema_version": 1,
        "ok": True,
        "summary": "Studio semantic source is checked and ready.",
        "diagnostics": [],
        "external_refs": [],
        "errors": [],
        "paths": {
            "task": str(task_file),
            "candidate": str(candidate_path),
            "captured_candidate": str(captured_candidate_path),
            "source": str(source_path),
            "proof": str(proof_path),
        },
        "creation": {
            "schema_version": STUDIO_CREATION_SCHEMA_VERSION,
            "task_id": task["task_id"],
            "status": "source_ready",
            "source_kind": task["source_kind"],
            "source_name": source_path.name,
            "source_sha256": source_sha256,
            "brief_sha256": task["brief_sha256"],
            "reference_sha256": task["reference"]["sha256"] if isinstance(task["reference"], dict) else None,
            "candidate_validation": "passed",
            "artifact_check": "passed",
            "proof_ok": True,
            "network_calls": "none",
            "reference_fidelity": "not_proven",
        },
        "next_actions": ["Run viewspec studio to enter Preview → Comment → Approve."],
        "metadata": {
            "sdk_version": __version__,
            "network_calls": "none",
            "reference_uploaded": False,
        },
    }


def _source_kind(kind: str) -> str:
    source_kind = STUDIO_CREATION_KIND_ALIASES.get(kind, kind)
    if source_kind not in STUDIO_CREATION_SOURCE_KINDS:
        raise StudioCreationError(
            "STUDIO_CREATION_KIND_INVALID",
            f"Unsupported Studio creation kind: {kind!r}.",
            "Use app for a product or view for one bounded screen.",
        )
    return source_kind


def _load_brief(*, brief: str | None, brief_file: str | Path | None, root: Path) -> str:
    if (brief is None) == (brief_file is None):
        raise StudioCreationError(
            "STUDIO_CREATION_BRIEF_INVALID",
            "Studio creation requires exactly one inline brief or brief file.",
            "Pass --brief TEXT or --brief-file PATH, but not both.",
        )
    if brief_file is not None:
        path = _local_path(brief_file, root=root, must_exist=True)
        raw = _read_regular_file(path, maximum=STUDIO_CREATION_BRIEF_MAX_BYTES, noun="brief")
        try:
            value = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise StudioCreationError(
                "STUDIO_CREATION_BRIEF_INVALID",
                "The Studio creation brief is not strict UTF-8 text.",
                "Save the brief as UTF-8 plain text.",
            ) from exc
    else:
        assert brief is not None
        value = brief
        if len(value.encode("utf-8")) > STUDIO_CREATION_BRIEF_MAX_BYTES:
            raise StudioCreationError(
                "STUDIO_CREATION_BRIEF_TOO_LARGE",
                "The Studio creation brief exceeds 32 KiB.",
                "Reduce it to the essential product outcome, users, content, states, and constraints.",
            )
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized or "\x00" in normalized:
        raise StudioCreationError(
            "STUDIO_CREATION_BRIEF_INVALID",
            "The Studio creation brief is empty or contains a null byte.",
            "Describe the product outcome in plain text.",
        )
    if len(normalized.encode("utf-8")) > STUDIO_CREATION_BRIEF_MAX_BYTES:
        raise StudioCreationError(
            "STUDIO_CREATION_BRIEF_TOO_LARGE",
            "The normalized Studio creation brief exceeds 32 KiB.",
            "Reduce it to the essential product outcome, users, content, states, and constraints.",
        )
    return normalized


def _reference_identity(reference: str | Path, *, root: Path) -> dict[str, object]:
    path = _local_path(reference, root=root, must_exist=True)
    content = _read_regular_file(path, maximum=STUDIO_CREATION_REFERENCE_MAX_BYTES, noun="reference image")
    image = _image_identity(content)
    if image is None:
        raise StudioCreationError(
            "STUDIO_CREATION_REFERENCE_INVALID",
            "Studio creation supports structurally valid local PNG, JPEG, or WebP reference images only.",
            "Convert the reference to PNG, JPEG, or WebP and keep it under the workspace.",
        )
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha256(content),
        "bytes": len(content),
        **image,
    }


def _verify_reference(value: object, *, root: Path) -> None:
    if value is None:
        return
    if not isinstance(value, dict) or set(value) != _REFERENCE_FIELDS:
        raise _invalid_task("reference identity is invalid")
    current = _reference_identity(str(value["path"]), root=root)
    if current != value:
        raise StudioCreationError(
            "STUDIO_CREATION_REFERENCE_CHANGED",
            "The task-bound reference image changed after the creation task was prepared.",
            "Restore the exact reference or prepare a new Studio creation task.",
        )


def _task_id(*, source_kind: str, brief_sha256: str, reference: object, source_path: str) -> str:
    identity = {
        "schema_version": STUDIO_CREATION_SCHEMA_VERSION,
        "contract_profile": "local_v1",
        "source_kind": source_kind,
        "source_path": source_path,
        "brief_sha256": brief_sha256,
        "reference": reference,
    }
    digest = hashlib.sha256(b"viewspec.studio.creation-task.v1\x00" + canonical_json_bytes(identity)).hexdigest()
    return f"vsct_{digest[:32]}"


def _read_task(path: Path) -> dict[str, Any]:
    raw = _read_regular_file(path, maximum=STUDIO_CREATION_TASK_MAX_BYTES, noun="creation task")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _invalid_task("task is not strict UTF-8 JSON") from exc
    return _validate_task(value)


def _validate_task(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _TASK_FIELDS:
        raise _invalid_task("task fields do not match Studio Creation V1")
    if value.get("schema_version") != STUDIO_CREATION_SCHEMA_VERSION:
        raise _invalid_task("schema_version is unsupported")
    if value.get("status") != "awaiting_agent" or value.get("contract_profile") != "local_v1":
        raise _invalid_task("status or contract profile is invalid")
    source_kind = value.get("source_kind")
    if source_kind not in STUDIO_CREATION_SOURCE_KINDS:
        raise _invalid_task("source_kind is unsupported")
    brief = value.get("brief")
    if not isinstance(brief, str) or not brief or len(brief.encode("utf-8")) > STUDIO_CREATION_BRIEF_MAX_BYTES:
        raise _invalid_task("brief is invalid")
    brief_sha256 = value.get("brief_sha256")
    if brief_sha256 != _sha256(brief.encode("utf-8")):
        raise _invalid_task("brief hash does not match")
    reference = value.get("reference")
    if reference is not None:
        if not isinstance(reference, dict) or set(reference) != _REFERENCE_FIELDS:
            raise _invalid_task("reference is invalid")
        if (
            not isinstance(reference.get("path"), str)
            or not isinstance(reference.get("sha256"), str)
            or len(reference["sha256"]) != 64
            or any(character not in "0123456789abcdef" for character in reference["sha256"])
            or type(reference.get("bytes")) is not int
            or not 1 <= reference["bytes"] <= STUDIO_CREATION_REFERENCE_MAX_BYTES
            or reference.get("media_type") not in _REFERENCE_MEDIA_TYPES
            or type(reference.get("width")) is not int
            or type(reference.get("height")) is not int
            or not 1 <= reference["width"] <= 32768
            or not 1 <= reference["height"] <= 32768
        ):
            raise _invalid_task("reference identity is invalid")
    if value.get("source_path") != STUDIO_CREATION_SOURCE_PATHS[source_kind]:
        raise _invalid_task("canonical source path is invalid")
    if value.get("candidate_path") != STUDIO_CREATION_CANDIDATE_PATHS[source_kind]:
        raise _invalid_task("candidate path is invalid")
    if value.get("candidate_schema") != STUDIO_CREATION_SCHEMA_IDS[source_kind]:
        raise _invalid_task("candidate schema is invalid")
    expected_id = _task_id(
        source_kind=source_kind,
        brief_sha256=brief_sha256,
        reference=reference,
        source_path=str(value["source_path"]),
    )
    if value.get("task_id") != expected_id:
        raise _invalid_task("task identity does not match its content")
    if value.get("proof_path") != f".viewspec/studio-creation/{expected_id}":
        raise _invalid_task("proof path is invalid")
    if value.get("acceptance") != STUDIO_CREATION_ACCEPTANCE:
        raise _invalid_task("acceptance contract is invalid")
    if len(canonical_json_bytes(value)) > STUDIO_CREATION_TASK_MAX_BYTES:
        raise StudioCreationError(
            "STUDIO_CREATION_TASK_TOO_LARGE",
            "The Studio creation task exceeds 128 KiB.",
            "Prepare a smaller brief and one bounded reference image.",
        )
    return value


def _prove_creation_candidate(*, candidate_path: Path, source_kind: str, proof_path: Path, root: Path) -> dict[str, Any]:
    force = proof_path.exists()
    if source_kind == "intent_bundle":
        return prove(
            intent_path=candidate_path,
            out_dir=proof_path,
            target="html-tailwind",
            force=force,
            cwd=root,
        )
    return prove_app(
        app_path=candidate_path,
        out_dir=proof_path,
        target=APP_BUNDLE_TARGET,
        with_shell=True,
        force=force,
        cwd=root,
    )


def _is_unchanged_starter(value: object, *, source_kind: str) -> bool:
    if source_kind == "intent_bundle":
        return any(value == starter_intent_payload(kind) for kind in STARTER_INTENT_KINDS)
    return value in (
        starter_app_bundle("internal_tool"),
        starter_react_app_bundle("internal_tool"),
    )


def _captured_candidate_path(*, proof_path: Path, task_id: str, source_kind: str, source_sha256: str) -> Path:
    suffix = "app" if source_kind == "app_bundle" else "intent"
    return proof_path.parent / f"{task_id}.{source_sha256}.candidate.{suffix}.json"


def _retain_immutable(path: Path, content: bytes) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StudioCreationError(
            "STUDIO_CREATION_PUBLISH_FAILED",
            f"Could not prepare captured candidate evidence: {exc}",
            "Fix workspace storage permissions and rerun studio-accept.",
            cli_exit=1,
        ) from exc
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != content:
            raise StudioCreationError(
                "STUDIO_CREATION_PUBLISH_FAILED",
                "The hash-named captured candidate path does not contain the expected bytes.",
                "Move the conflicting .viewspec Studio creation evidence and rerun studio-accept.",
                cli_exit=1,
            )
        return
    try:
        _publish_exclusive(path, content)
    except StudioCreationError as exc:
        if exc.code != "STUDIO_CREATION_SOURCE_EXISTS":
            raise
        if path.is_symlink() or not path.is_file() or path.read_bytes() != content:
            raise StudioCreationError(
                "STUDIO_CREATION_PUBLISH_FAILED",
                "The captured candidate changed during immutable retention.",
                "Stop concurrent acceptance attempts and rerun studio-accept.",
                cli_exit=1,
            ) from exc


def _publish_exclusive(path: Path, content: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise StudioCreationError(
            "STUDIO_CREATION_SOURCE_EXISTS",
            f"Canonical semantic source already exists: {path.name}.",
            "Open the existing source with viewspec studio; first creation never overwrites it.",
        )
    temp_path: str | None = None
    try:
        descriptor, temp_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".ready", dir=path.parent)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o600)
        os.link(temp_path, path)
        os.unlink(temp_path)
        temp_path = None
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except FileExistsError as exc:
        raise StudioCreationError(
            "STUDIO_CREATION_SOURCE_EXISTS",
            f"Canonical semantic source was created concurrently: {path.name}.",
            "Inspect the existing source and open it with viewspec studio.",
        ) from exc
    except OSError as exc:
        raise StudioCreationError(
            "STUDIO_CREATION_PUBLISH_FAILED",
            f"Could not publish checked semantic source: {exc}",
            "Fix workspace storage permissions and rerun studio-accept; do not copy the candidate manually.",
            cli_exit=1,
        ) from exc
    finally:
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def _require_blank_workspace(root: Path) -> None:
    existing = [
        name
        for name in STUDIO_CREATION_SOURCE_PATHS.values()
        if (root / name).exists() or (root / name).is_symlink()
    ]
    if existing:
        raise StudioCreationError(
            "STUDIO_CREATION_SOURCE_EXISTS",
            f"Studio found existing semantic source: {', '.join(existing)}.",
            "Open it with viewspec studio; first creation never overwrites semantic source.",
        )


def _read_regular_file(path: Path, *, maximum: int, noun: str) -> bytes:
    try:
        info = path.lstat()
    except OSError as exc:
        raise StudioCreationError(
            "STUDIO_CREATION_PATH_INVALID",
            f"Could not inspect the {noun}: {exc}",
            f"Use one readable regular {noun} file under the workspace.",
        ) from exc
    if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise StudioCreationError(
            "STUDIO_CREATION_PATH_INVALID",
            f"The {noun} must be one regular non-linked file.",
            f"Copy the {noun} into a normal file under the workspace.",
        )
    if not 1 <= info.st_size <= maximum:
        code = "STUDIO_CREATION_REFERENCE_TOO_LARGE" if noun == "reference image" else "STUDIO_CREATION_TASK_TOO_LARGE"
        if noun == "brief":
            code = "STUDIO_CREATION_BRIEF_TOO_LARGE"
        raise StudioCreationError(
            code,
            f"The {noun} must contain 1 through {maximum} bytes.",
            f"Reduce the {noun} to the documented bound and retry.",
        )
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise StudioCreationError(
            "STUDIO_CREATION_PATH_INVALID",
            f"Could not read the {noun}: {exc}",
            f"Use one readable regular {noun} file under the workspace.",
        ) from exc
    if len(content) != info.st_size:
        raise StudioCreationError(
            "STUDIO_CREATION_PATH_INVALID",
            f"The {noun} changed while it was being read.",
            f"Stop changing the {noun} and prepare a fresh creation task.",
        )
    return content


def _local_path(value: str | Path, *, root: Path, must_exist: bool) -> Path:
    try:
        return resolve_local_path(value, cwd=root, allow_outside_cwd=False, must_exist=must_exist)
    except LocalToolError as exc:
        raise StudioCreationError(
            "STUDIO_CREATION_PATH_INVALID",
            exc.message,
            "Keep the brief, reference, task, candidate, proof, and source under the workspace.",
        ) from exc


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _invalid_task(f"duplicate key {key!r}")
        result[key] = value
    return result


def _image_identity(content: bytes) -> dict[str, object] | None:
    dimensions: tuple[int, int] | None = None
    media_type: str | None = None
    if (
        len(content) >= 45
        and content.startswith(b"\x89PNG\r\n\x1a\n")
        and content[12:16] == b"IHDR"
        and content[-12:-8] == b"\x00\x00\x00\x00"
        and content[-8:-4] == b"IEND"
    ):
        media_type = "image/png"
        dimensions = (int.from_bytes(content[16:20], "big"), int.from_bytes(content[20:24], "big"))
    elif len(content) >= 4 and content.startswith(b"\xff\xd8\xff") and content.endswith(b"\xff\xd9"):
        media_type = "image/jpeg"
        dimensions = _jpeg_dimensions(content)
    elif (
        len(content) >= 30
        and content[:4] == b"RIFF"
        and int.from_bytes(content[4:8], "little") + 8 == len(content)
        and content[8:12] == b"WEBP"
    ):
        media_type = "image/webp"
        dimensions = _webp_dimensions(content)
    if media_type is None or dimensions is None:
        return None
    width, height = dimensions
    if not 1 <= width <= 32768 or not 1 <= height <= 32768:
        return None
    return {"media_type": media_type, "width": width, "height": height}


def _jpeg_dimensions(content: bytes) -> tuple[int, int] | None:
    position = 2
    start_of_frame = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while position + 3 < len(content):
        if content[position] != 0xFF:
            position += 1
            continue
        while position < len(content) and content[position] == 0xFF:
            position += 1
        if position >= len(content):
            return None
        marker = content[position]
        position += 1
        if marker in {0x01, *range(0xD0, 0xDA)}:
            continue
        if position + 2 > len(content):
            return None
        segment_length = int.from_bytes(content[position : position + 2], "big")
        if segment_length < 2 or position + segment_length > len(content):
            return None
        if marker in start_of_frame:
            if segment_length < 7:
                return None
            height = int.from_bytes(content[position + 3 : position + 5], "big")
            width = int.from_bytes(content[position + 5 : position + 7], "big")
            return width, height
        position += segment_length
    return None


def _webp_dimensions(content: bytes) -> tuple[int, int] | None:
    kind = content[12:16]
    if kind == b"VP8X" and len(content) >= 30:
        width = 1 + int.from_bytes(content[24:27], "little")
        height = 1 + int.from_bytes(content[27:30], "little")
        return width, height
    if kind == b"VP8L" and len(content) >= 25 and content[20] == 0x2F:
        b0, b1, b2, b3 = content[21:25]
        width = 1 + b0 + ((b1 & 0x3F) << 8)
        height = 1 + (b1 >> 6) + (b2 << 2) + ((b3 & 0x0F) << 10)
        return width, height
    if kind == b"VP8 " and len(content) >= 30 and content[23:26] == b"\x9d\x01\x2a":
        width = int.from_bytes(content[26:28], "little") & 0x3FFF
        height = int.from_bytes(content[28:30], "little") & 0x3FFF
        return width, height
    return None


def _invalid_task(reason: str) -> StudioCreationError:
    return StudioCreationError(
        "STUDIO_CREATION_TASK_INVALID",
        f"Studio creation task is invalid: {reason}.",
        "Prepare a fresh task with viewspec studio-create; do not edit task JSON.",
    )


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


__all__ = [
    "STUDIO_CREATION_ACCEPTANCE",
    "STUDIO_CREATION_BRIEF_MAX_BYTES",
    "STUDIO_CREATION_REFERENCE_MAX_BYTES",
    "STUDIO_CREATION_SCHEMA_VERSION",
    "STUDIO_CREATION_TASK_DEFAULT",
    "STUDIO_CREATION_TASK_MAX_BYTES",
    "StudioCreationError",
    "accept_studio_creation",
    "prepare_studio_creation",
]
