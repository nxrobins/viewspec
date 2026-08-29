"""Immutable input capture and stale-generation exclusion for Review V0."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
import errno
import functools
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import stat
import subprocess
import threading
import time
from typing import Callable, TypeVar

from viewspec._version import __version__
from viewspec.app_bundle import compile_app, diff_app_text, validate_app_text
from viewspec.intent_tools import compile_intent_bundle_file_tool, diff_intent_text, validate_intent_text
from viewspec.local_tools import check_artifact_dir
from viewspec.node_runtime import materialize_prebuilt_node_modules
from viewspec.review_contract import (
    KIB,
    MIB,
    ReviewContractError,
    ReviewRevision,
    canonical_json_bytes,
    canonical_json_sha256,
)
from viewspec.review_manifest import ReviewManifestIndex
from viewspec.review_errors import make_review_error
from viewspec.state_ir import validate_state_ir


INTENT_SOURCE_MAX_BYTES = 256 * KIB
APP_SOURCE_MAX_BYTES = 1 * MIB
DESIGN_MAX_BYTES = 64 * KIB
AGGREGATE_CAPTURE_MAX_BYTES = APP_SOURCE_MAX_BYTES + DESIGN_MAX_BYTES
MAX_GENERATION = (2**64) - 1
MAX_ARTIFACT_BYTES = 24 * MIB
MAX_ARTIFACT_FILES = 4096
MAX_SESSION_STORAGE_BYTES = 256 * MIB
MAX_ACTIVE_STORAGE_BYTES = 1024 * MIB
MAX_RETAINED_STORAGE_BYTES = 4096 * MIB
BUILD_STORAGE_RESERVATION_BYTES = MAX_ARTIFACT_BYTES + APP_SOURCE_MAX_BYTES + DESIGN_MAX_BYTES
MAX_DIFF_ENTRIES = 128
MAX_DIFF_ENTRY_BYTES = 1 * KIB
MAX_DIFF_BYTES = 64 * KIB
STUDIO_COMPARE_TARGET = "studio-static-react-compare"
STUDIO_COMPARE_MANIFEST = "studio_comparison_manifest.json"
STUDIO_INSPECTION_MANIFEST = "studio_inspection_manifest.json"
STUDIO_INSPECTION_MAX_BYTES = 224 * KIB
_READ_CHUNK_BYTES = 64 * KIB
_NOFOLLOW_FLAG = getattr(os, "O" + "_NOFOLLOW", 0)
_ARTIFACT_SET_DOMAIN = b"viewspec.review.artifact-set.v1\x00"
_T = TypeVar("_T")


@contextmanager
def bounded_review_phase(code: str, timeout_seconds: float):
    """Interrupt one local phase at its monotonic deadline on the daemon main thread."""
    if not isinstance(code, str) or not code.startswith("REVIEW_"):
        raise ValueError("bounded Review phase requires a stable REVIEW_* code")
    if not isinstance(timeout_seconds, (int, float)) or not 0 < timeout_seconds <= 180:
        raise ValueError("bounded Review phase timeout must be greater than zero and at most 180 seconds")
    started = time.monotonic()
    can_interrupt = (
        threading.current_thread() is threading.main_thread()
        and hasattr(signal, "SIGALRM")
        and hasattr(signal, "setitimer")
    )
    if not can_interrupt:
        yield
        if time.monotonic() - started > timeout_seconds:
            raise make_review_error(code, f"Review phase exceeded its {timeout_seconds:g}-second deadline.")
        return

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_delay, previous_interval = signal.getitimer(signal.ITIMER_REAL)
    delay = min(float(timeout_seconds), previous_delay) if previous_delay > 0 else float(timeout_seconds)

    def deadline(signum: int, frame: object) -> None:
        del signum, frame
        raise make_review_error(code, f"Review phase exceeded its {timeout_seconds:g}-second deadline.")

    signal.signal(signal.SIGALRM, deadline)
    signal.setitimer(signal.ITIMER_REAL, delay)
    try:
        yield
    finally:
        elapsed = time.monotonic() - started
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_delay > 0:
            remaining = max(0.000001, previous_delay - elapsed)
            signal.setitimer(signal.ITIMER_REAL, remaining, previous_interval)


def bounded_review_operation(code: str, timeout_seconds: float):
    def decorate(function: Callable[..., _T]) -> Callable[..., _T]:
        @functools.wraps(function)
        def wrapped(*args: object, **kwargs: object) -> _T:
            with bounded_review_phase(code, timeout_seconds):
                return function(*args, **kwargs)

        return wrapped

    return decorate


@dataclass(frozen=True, slots=True)
class CapturedFileIdentity:
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int

    @classmethod
    def from_stat(cls, value: os.stat_result) -> CapturedFileIdentity:
        return cls(
            device=value.st_dev,
            inode=value.st_ino,
            size=value.st_size,
            mtime_ns=value.st_mtime_ns,
            ctime_ns=value.st_ctime_ns,
        )


@dataclass(frozen=True, slots=True)
class ReviewSourceSnapshot:
    source_path: Path
    source_kind: str
    source_bytes: bytes
    source_sha256: str
    source_identity: CapturedFileIdentity
    design_path: Path | None
    design_bytes: bytes | None
    design_sha256: str | None
    design_identity: CapturedFileIdentity | None


@dataclass(frozen=True, slots=True)
class BuiltReviewRevision:
    revision: ReviewRevision
    revision_dir: Path
    artifact_dir: Path
    manifest_indexes: dict[str | None, ReviewManifestIndex]


@dataclass(frozen=True, slots=True)
class ReviewSemanticDiff:
    from_revision: int
    to_revision: int
    from_source_sha256: str
    to_source_sha256: str
    entries: tuple[dict[str, object], ...]
    projection_sha256: str

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "status": "available",
            "from_revision": self.from_revision,
            "to_revision": self.to_revision,
            "from_source_sha256": self.from_source_sha256,
            "to_source_sha256": self.to_source_sha256,
            "entries": [dict(entry) for entry in self.entries],
            "projection_sha256": self.projection_sha256,
        }


class GenerationGate:
    """Monotonic generation counter whose promotion check is one locked operation."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._observed_generation = 0
        self._promoted_generation = 0

    @property
    def observed_generation(self) -> int:
        with self._lock:
            return self._observed_generation

    @property
    def promoted_generation(self) -> int:
        with self._lock:
            return self._promoted_generation

    def observe(self) -> int:
        with self._lock:
            if self._observed_generation >= MAX_GENERATION:
                raise ReviewContractError(
                    "REVIEW_REVISION_SUPERSEDED",
                    "Review source generation counter is exhausted.",
                    "End this bounded review session and start a new one.",
                    http_status=409,
                )
            self._observed_generation += 1
            return self._observed_generation

    def assert_current(self, generation: int) -> int:
        with self._lock:
            self._assert_current_locked(generation)
            return generation

    def promote(self, generation: int, action: Callable[[], _T] | None = None) -> int | _T:
        """Run the irreversible promotion action while newest-generation status is locked."""
        with self._lock:
            self._assert_current_locked(generation)
            result: int | _T = generation if action is None else action()
            self._promoted_generation = generation
            return result

    def _assert_current_locked(self, generation: int) -> None:
        if type(generation) is not int or generation != self._observed_generation:
            raise ReviewContractError(
                "REVIEW_REVISION_SUPERSEDED",
                "Candidate generation is not the newest observed source generation.",
                "Discard the stale candidate and build the newest captured generation.",
                http_status=409,
            )


def build_review_revision(
    snapshot: ReviewSourceSnapshot,
    *,
    session_dir: str | Path,
    revision_number: int,
    generation: int,
    gate: GenerationGate,
    target: str,
    previous_manifest_indexes: dict[str | None, ReviewManifestIndex] | None = None,
    semantic_diff: ReviewSemanticDiff | None = None,
    allow_install: bool = False,
) -> BuiltReviewRevision:
    """Compile, check, identify, and atomically promote one immutable snapshot."""
    if not isinstance(snapshot, ReviewSourceSnapshot) or not isinstance(gate, GenerationGate):
        raise TypeError("snapshot and gate must be Review capture objects")

    # This check deliberately precedes every filesystem mutation. A candidate already known
    # to be stale is not allowed to reserve storage or create even an empty revision path.
    gate.assert_current(generation)
    state = _absolute_path(session_dir)
    _reserve_build_storage(state)
    if type(revision_number) is not int or not 1 <= revision_number <= (2**63 - 1):
        raise ReviewContractError(
            "REVIEW_REVISION_IDENTITY_MISMATCH",
            "Revision number must be a positive 64-bit integer.",
            "Use the session-assigned monotonic revision number.",
            http_status=500,
            cli_exit=1,
        )

    final_dir = state / "revisions" / str(revision_number)
    if final_dir.exists():
        raise ReviewContractError(
            "REVIEW_REVISION_IDENTITY_MISMATCH",
            "Review revision number already exists and is immutable.",
            "Allocate the next monotonic revision number.",
            http_status=500,
            cli_exit=1,
        )

    candidate_root = state / ".candidates"
    candidate_dir = candidate_root / f"r{revision_number}-g{generation}-{secrets.token_hex(8)}"
    artifact_dir = candidate_dir / "artifact"
    try:
        _make_private_dir(state)
        _make_private_dir(candidate_root)
        _make_private_dir(candidate_dir)
        source_copy = candidate_dir / "source.json"
        design_copy = candidate_dir / "DESIGN.md" if snapshot.design_bytes is not None else None
        _write_private_file(source_copy, snapshot.source_bytes)
        if design_copy is not None:
            assert snapshot.design_bytes is not None
            _write_private_file(design_copy, snapshot.design_bytes)

        if snapshot.source_kind == "intent_bundle":
            manifest_indexes, manifest_path, root_manifest_kind = _build_intent_artifact(
                snapshot,
                source_copy=source_copy,
                design_copy=design_copy,
                artifact_dir=artifact_dir,
                candidate_dir=candidate_dir,
                target=target,
            )
        else:
            manifest_indexes, manifest_path, root_manifest_kind = _build_app_artifact(
                snapshot,
                source_copy=source_copy,
                design_copy=design_copy,
                artifact_dir=artifact_dir,
                candidate_dir=candidate_dir,
                target=target,
                allow_install=allow_install,
            )
        root_manifest_bytes = _read_bounded_regular_file(manifest_path, maximum=MAX_ARTIFACT_BYTES)
        root_manifest_sha256 = hashlib.sha256(root_manifest_bytes).hexdigest()
        if previous_manifest_indexes is not None:
            for screen_id in manifest_indexes.keys() & previous_manifest_indexes.keys():
                manifest_indexes[screen_id].assert_identity_compatible(previous_manifest_indexes[screen_id])
        artifact_set_sha256 = _artifact_set_sha256(artifact_dir)
        revision = ReviewRevision(
            number=revision_number,
            source_kind=snapshot.source_kind,
            source_sha256=snapshot.source_sha256,
            design_sha256=snapshot.design_sha256,
            target=target,
            artifact_set_sha256=artifact_set_sha256,
            root_manifest_kind=root_manifest_kind,
            root_manifest_sha256=root_manifest_sha256,
            compiler_version=__version__,
            contract_profile="local_v1",
        )
        if semantic_diff is not None:
            if (
                semantic_diff.to_revision != revision.number
                or semantic_diff.to_source_sha256 != revision.source_sha256
            ):
                raise ReviewContractError(
                    "REVIEW_REVISION_IDENTITY_MISMATCH",
                    "Semantic diff identity does not match the candidate revision.",
                    "Compute the diff only from the exact previous and candidate captured source bytes.",
                    http_status=500,
                    cli_exit=1,
                )
            _write_private_file(candidate_dir / "semantic_diff.json", canonical_json_bytes(semantic_diff.to_json()))
        _write_private_file(candidate_dir / "revision.json", canonical_json_bytes(revision.to_json()))
        _seal_private_tree(candidate_dir)

        def promote_candidate() -> None:
            revisions_dir = state / "revisions"
            _make_private_dir(revisions_dir)
            if final_dir.exists():
                raise ReviewContractError(
                    "REVIEW_REVISION_IDENTITY_MISMATCH",
                    "Review revision number already exists and is immutable.",
                    "Allocate the next monotonic revision number.",
                    http_status=500,
                    cli_exit=1,
                )
            os.replace(candidate_dir, final_dir)
            _fsync_directory(revisions_dir)

        gate.promote(generation, promote_candidate)
        return BuiltReviewRevision(
            revision=revision,
            revision_dir=final_dir,
            artifact_dir=final_dir / "artifact",
            manifest_indexes=manifest_indexes,
        )
    except ReviewContractError:
        _discard_candidate(candidate_dir)
        raise
    except OSError as exc:
        _discard_candidate(candidate_dir)
        raise ReviewContractError(
            "REVIEW_REVISION_WRITE_FAILED",
            f"Could not construct or promote the private Review revision: {exc}",
            "Free local storage, verify private state-directory ownership, and retry.",
            http_status=507,
            cli_exit=1,
        ) from exc
    except Exception as exc:
        _discard_candidate(candidate_dir)
        raise ReviewContractError(
            "REVIEW_COMPILE_FAILED",
            f"Captured source failed during Review compilation: {exc}",
            "Fix the source or compiler failure and retry the newest generation.",
            http_status=422,
        ) from exc


@bounded_review_operation("REVIEW_DIFF_TIMEOUT", 10)
def compute_review_semantic_diff(
    previous: BuiltReviewRevision,
    snapshot: ReviewSourceSnapshot,
    *,
    to_revision: int,
) -> ReviewSemanticDiff:
    """Compute a bounded projection only from exact stored/captured semantic source bytes."""
    if previous.revision.source_kind != snapshot.source_kind or to_revision != previous.revision.number + 1:
        raise ReviewContractError(
            "REVIEW_REVISION_IDENTITY_MISMATCH",
            "Semantic diff endpoints do not form one consecutive source-kind transition.",
            "Diff the exact current head against the next captured source revision.",
            http_status=500,
            cli_exit=1,
        )
    maximum = INTENT_SOURCE_MAX_BYTES if snapshot.source_kind == "intent_bundle" else APP_SOURCE_MAX_BYTES
    previous_bytes = _read_bounded_regular_file(previous.revision_dir / "source.json", maximum=maximum)
    if hashlib.sha256(previous_bytes).hexdigest() != previous.revision.source_sha256:
        raise _stored_revision_mismatch("Stored previous semantic source changed before diff.")
    try:
        left = previous_bytes.decode("utf-8")
        right = snapshot.source_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReviewContractError(
            "REVIEW_SOURCE_INVALID",
            "Semantic diff source is not strict UTF-8.",
            "Fix semantic source encoding before rebuilding.",
            http_status=422,
        ) from exc
    result = (
        diff_intent_text(left, right, compile_check=False)
        if snapshot.source_kind == "intent_bundle"
        else diff_app_text(left, right, compile_check=False)
    )
    if result.get("ok") is not True:
        raise ReviewContractError(
            "REVIEW_SOURCE_INVALID",
            "Exact semantic source diff could not be computed.",
            "Fix both consecutive semantic source revisions and retry.",
            http_status=422,
        )
    entries = _diff_entries(result)
    identity = {
        "schema_version": 1,
        "from_revision": previous.revision.number,
        "to_revision": to_revision,
        "from_source_sha256": previous.revision.source_sha256,
        "to_source_sha256": snapshot.source_sha256,
        "entries": entries,
    }
    projection_sha256 = canonical_json_sha256(identity)
    diff = ReviewSemanticDiff(
        from_revision=previous.revision.number,
        to_revision=to_revision,
        from_source_sha256=previous.revision.source_sha256,
        to_source_sha256=snapshot.source_sha256,
        entries=tuple(entries),
        projection_sha256=projection_sha256,
    )
    if len(canonical_json_bytes(diff.to_json())) > MAX_DIFF_BYTES:
        raise _diff_too_large("Semantic diff projection exceeds 64 KiB.")
    return diff


def load_review_semantic_diff(built: BuiltReviewRevision) -> dict[str, object]:
    path = built.revision_dir / "semantic_diff.json"
    if not path.exists():
        return {"status": "unavailable", "from_revision": None, "to_revision": built.revision.number, "entries": []}
    content = _read_bounded_regular_file(path, maximum=MAX_DIFF_BYTES)
    payload = _strict_json_object(content, code="REVIEW_REVISION_IDENTITY_MISMATCH")
    if (
        payload.get("status") != "available"
        or payload.get("to_revision") != built.revision.number
        or payload.get("to_source_sha256") != built.revision.source_sha256
    ):
        raise _stored_revision_mismatch("Stored semantic diff does not match the current revision.")
    projection = {key: value for key, value in payload.items() if key not in {"status", "projection_sha256"}}
    if canonical_json_sha256(projection) != payload.get("projection_sha256"):
        raise _stored_revision_mismatch("Stored semantic diff projection hash does not match.")
    return payload


def _diff_entries(result: dict[str, object]) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    fields = result.get("changed_fields")
    if isinstance(fields, list):
        for path in fields:
            if isinstance(path, str):
                entries.append({"kind": "field_changed", "path": path})
    changes = result.get("changes")
    if isinstance(changes, dict):
        for section, value in sorted(changes.items()):
            if not isinstance(section, str) or not isinstance(value, dict):
                continue
            for change_kind in ("added", "removed", "changed"):
                identifiers = value.get(change_kind)
                if not isinstance(identifiers, list):
                    continue
                for identifier in identifiers:
                    if isinstance(identifier, str):
                        entries.append({"kind": change_kind, "path": f"{section}/{identifier}"})
    unique: dict[bytes, dict[str, object]] = {}
    for entry in entries:
        encoded = canonical_json_bytes(entry)
        if len(encoded) > MAX_DIFF_ENTRY_BYTES:
            raise _diff_too_large("One semantic diff entry exceeds 1 KiB.")
        unique[encoded] = entry
    ordered = [unique[key] for key in sorted(unique)]
    if len(ordered) > MAX_DIFF_ENTRIES:
        raise _diff_too_large("Semantic diff projection contains more than 128 entries.")
    return ordered


def _diff_too_large(message: str) -> ReviewContractError:
    return ReviewContractError(
        "REVIEW_DIFF_TOO_LARGE",
        message,
        "Reduce one semantic source change set before rebuilding Review.",
        http_status=422,
    )


def load_review_revision(session_dir: str | Path, revision_number: int) -> BuiltReviewRevision:
    """Revalidate and load one already-promoted immutable revision from private state."""
    state = _absolute_path(session_dir)
    revision_dir = state / "revisions" / str(revision_number)
    revision_bytes = _read_bounded_regular_file(revision_dir / "revision.json", maximum=64 * KIB)
    try:
        revision = ReviewRevision.from_json(_strict_json_object(revision_bytes, code="REVIEW_REVISION_IDENTITY_MISMATCH"))
    except ReviewContractError:
        raise
    if revision.number != revision_number:
        raise ReviewContractError(
            "REVIEW_REVISION_IDENTITY_MISMATCH",
            "Stored Review revision number does not match its immutable directory.",
            "Treat the session revision as corrupt and open a new review.",
            http_status=500,
            cli_exit=1,
        )
    artifact_dir = revision_dir / "artifact"
    if revision.source_kind == "intent_bundle":
        checked = check_artifact_dir(artifact_dir)
        if checked.get("ok") is not True:
            raise _stored_revision_mismatch("Stored IntentBundle artifact no longer passes check.")
        manifest_path = artifact_dir / "provenance_manifest.json"
        manifest_bytes = _read_bounded_regular_file(manifest_path, maximum=MAX_ARTIFACT_BYTES)
        indexes: dict[str | None, ReviewManifestIndex] = {
            None: ReviewManifestIndex.from_bytes(manifest_bytes, screen_id=None)
        }
        _assert_no_external_runtime_references(artifact_dir)
    elif revision.target == STUDIO_COMPARE_TARGET:
        indexes, manifest_path = _load_studio_comparison_indexes(
            artifact_dir,
            expected_source_sha256=revision.source_sha256,
        )
        manifest_bytes = _read_bounded_regular_file(manifest_path, maximum=MAX_ARTIFACT_BYTES)
        _assert_no_external_runtime_references(artifact_dir)
    elif revision.target == "react-tailwind-app":
        indexes, manifest_path = _load_react_app_indexes(artifact_dir)
        manifest_bytes = _read_bounded_regular_file(manifest_path, maximum=MAX_ARTIFACT_BYTES)
        _assert_no_external_runtime_references(artifact_dir)
    else:
        indexes, manifest_path = _load_app_indexes(artifact_dir, expected_target=revision.target)
        manifest_bytes = _read_bounded_regular_file(manifest_path, maximum=MAX_ARTIFACT_BYTES)
        _assert_no_external_runtime_references(artifact_dir)
    if (
        hashlib.sha256(manifest_bytes).hexdigest() != revision.root_manifest_sha256
        or _artifact_set_sha256(artifact_dir) != revision.artifact_set_sha256
    ):
        raise _stored_revision_mismatch("Stored Review artifact identity changed after promotion.")
    return BuiltReviewRevision(
        revision=revision,
        revision_dir=revision_dir,
        artifact_dir=artifact_dir,
        manifest_indexes=indexes,
    )


def _build_intent_artifact(
    snapshot: ReviewSourceSnapshot,
    *,
    source_copy: Path,
    design_copy: Path | None,
    artifact_dir: Path,
    candidate_dir: Path,
    target: str,
) -> tuple[dict[str | None, ReviewManifestIndex], Path, str]:
    with bounded_review_phase("REVIEW_VALIDATE_TIMEOUT", 5):
        _validate_intent_snapshot(snapshot)
    with bounded_review_phase("REVIEW_COMPILE_TIMEOUT", 30):
        compiled = compile_intent_bundle_file_tool(
            source_copy.name,
            artifact_dir.name,
            design_path=design_copy.name if design_copy is not None else None,
            target=target,
            cwd=candidate_dir,
        )
    if compiled.get("ok") is not True:
        raise ReviewContractError(
            "REVIEW_COMPILE_FAILED",
            "Captured IntentBundle did not compile into a checked Review artifact.",
            "Fix the reported ViewSpec compile errors and retry the newest source generation.",
            http_status=422,
        )
    _assert_no_external_runtime_references(artifact_dir)
    with bounded_review_phase("REVIEW_CHECK_TIMEOUT", 10):
        checked = check_artifact_dir(artifact_dir)
        if checked.get("ok") is not True:
            raise ReviewContractError(
                "REVIEW_CHECK_FAILED",
                "Compiled Review artifact failed the mandatory artifact check.",
                "Fix the checked artifact errors and rebuild from semantic source.",
                http_status=422,
            )
        manifest_path = artifact_dir / "provenance_manifest.json"
        manifest_bytes = _read_bounded_regular_file(manifest_path, maximum=MAX_ARTIFACT_BYTES)
        manifest_index = ReviewManifestIndex.from_bytes(manifest_bytes, screen_id=None)
    return {None: manifest_index}, manifest_path, "provenance_manifest"


def _build_app_artifact(
    snapshot: ReviewSourceSnapshot,
    *,
    source_copy: Path,
    design_copy: Path | None,
    artifact_dir: Path,
    candidate_dir: Path,
    target: str,
    allow_install: bool,
) -> tuple[dict[str | None, ReviewManifestIndex], Path, str]:
    with bounded_review_phase("REVIEW_VALIDATE_TIMEOUT", 5):
        _validate_app_snapshot(snapshot)
    if target == STUDIO_COMPARE_TARGET:
        return _build_studio_comparison_artifact(
            snapshot,
            source_copy=source_copy,
            design_copy=design_copy,
            artifact_dir=artifact_dir,
            candidate_dir=candidate_dir,
            allow_install=allow_install,
        )
    with bounded_review_phase("REVIEW_COMPILE_TIMEOUT", 30):
        compiled = compile_app(
            source_copy.name,
            out_dir=artifact_dir.name,
            design_path=design_copy.name if design_copy is not None else None,
            target=target,
            cwd=candidate_dir,
        )
        if compiled.get("ok") is True and target == "react-tailwind-app":
            _build_react_review_runtime(snapshot, artifact_dir, allow_install=allow_install)
    if compiled.get("ok") is not True:
        raise ReviewContractError(
            "REVIEW_COMPILE_FAILED",
            "Captured AppBundle did not compile into a checked Review artifact.",
            "Fix the reported ViewSpec AppBundle errors and retry the newest source generation.",
            http_status=422,
        )
    with bounded_review_phase("REVIEW_CHECK_TIMEOUT", 10):
        if target == "html-tailwind-app":
            indexes, manifest_path = _load_app_indexes(artifact_dir, expected_target=target)
            root_kind = "shell_manifest"
        elif target == "react-tailwind-app":
            indexes, manifest_path = _load_react_app_indexes(artifact_dir)
            root_kind = "review_react_manifest"
        else:
            raise ReviewContractError(
                "REVIEW_SOURCE_UNSUPPORTED",
                "Review AppBundle target is unsupported.",
                "Use html-tailwind-app or react-tailwind-app.",
                http_status=422,
            )
        _assert_no_external_runtime_references(artifact_dir)
    return indexes, manifest_path, root_kind


def _build_studio_comparison_artifact(
    snapshot: ReviewSourceSnapshot,
    *,
    source_copy: Path,
    design_copy: Path | None,
    artifact_dir: Path,
    candidate_dir: Path,
    allow_install: bool,
) -> tuple[dict[str | None, ReviewManifestIndex], Path, str]:
    if not allow_install:
        raise ReviewContractError(
            "REVIEW_SOURCE_UNSUPPORTED",
            "Studio static/React comparison requires the exact locked React runtime dependencies.",
            "Retry viewspec studio --compare --install to authorize the existing npm ci --ignore-scripts flow.",
            http_status=422,
        )
    static_dir = artifact_dir / "static"
    react_dir = artifact_dir / "react"
    with bounded_review_phase("REVIEW_COMPILE_TIMEOUT", 60):
        static_result = compile_app(
            source_copy.name,
            out_dir=str(static_dir.relative_to(candidate_dir)),
            design_path=design_copy.name if design_copy is not None else None,
            target="html-tailwind-app",
            cwd=candidate_dir,
        )
        if static_result.get("ok") is not True:
            raise ReviewContractError(
                "REVIEW_COMPILE_FAILED",
                "Captured AppBundle did not compile into the static Studio comparison target.",
                "Fix the reported AppBundle errors and retry the exact source.",
                http_status=422,
            )
        react_result = compile_app(
            source_copy.name,
            out_dir=str(react_dir.relative_to(candidate_dir)),
            design_path=design_copy.name if design_copy is not None else None,
            target="react-tailwind-app",
            cwd=candidate_dir,
        )
        if react_result.get("ok") is not True:
            raise ReviewContractError(
                "REVIEW_COMPILE_FAILED",
                "Captured AppBundle did not compile into the React Studio comparison target.",
                "Fix the reported AppBundle errors and retry the exact source.",
                http_status=422,
            )
        _build_react_review_runtime(snapshot, react_dir, allow_install=True)

    with bounded_review_phase("REVIEW_CHECK_TIMEOUT", 15):
        static_indexes, static_manifest = _load_app_indexes(static_dir, expected_target="html-tailwind-app")
        react_indexes, react_manifest = _load_react_app_indexes(react_dir)
        _assert_cross_target_identity(static_indexes, react_indexes)
        static_routes = _checked_routes(static_manifest)
        react_routes = _checked_routes(react_manifest)
        if static_routes != react_routes:
            raise ReviewContractError(
                "REVIEW_COMPARISON_IDENTITY_MISMATCH",
                "Static and React comparison targets do not expose the same checked routes.",
                "Fix cross-target route generation before opening Studio comparison.",
                http_status=422,
            )
        source_payload = _strict_json_object(snapshot.source_bytes, code="REVIEW_SOURCE_INVALID")
        inspection = _studio_inspection_projection(
            source_payload,
            source_sha256=snapshot.source_sha256,
            static_result=static_result,
            react_result=react_result,
        )
        inspection_path = artifact_dir / STUDIO_INSPECTION_MANIFEST
        inspection_bytes = canonical_json_bytes(inspection)
        if len(inspection_bytes) > STUDIO_INSPECTION_MAX_BYTES:
            raise ReviewContractError(
                "REVIEW_COMPARISON_IDENTITY_MISMATCH",
                "Studio state and resource inspection evidence exceeds its 224 KiB response boundary.",
                "Reduce declared replay or resource-view evidence before opening Studio comparison.",
                http_status=422,
            )
        _write_private_file(inspection_path, inspection_bytes)
        manifest_path = artifact_dir / STUDIO_COMPARE_MANIFEST
        manifest = {
            "schema_version": 1,
            "kind": "studio_static_react_comparison",
            "target": STUDIO_COMPARE_TARGET,
            "source_sha256": snapshot.source_sha256,
            "policy": {
                "dependency_install": "explicit_opt_in",
                "runtime_network_calls": "none",
                "visual_parity": "not_proven",
            },
            "routes": static_routes,
            "inspection": {
                "path": STUDIO_INSPECTION_MANIFEST,
                "sha256": hashlib.sha256(inspection_bytes).hexdigest(),
                "coherence_status": inspection["coherence"]["status"],
                "coherence_contract": inspection["coherence"]["contract"],
                "state_status": inspection["state"]["status"],
                "resource_status": inspection["resources"]["status"],
            },
            "screens": [
                {
                    "id": screen_id,
                    "semantic_identity_sha256": static_indexes[screen_id].semantic_identity_sha256,
                    "static_manifest_sha256": static_indexes[screen_id].manifest_sha256,
                    "react_manifest_sha256": react_indexes[screen_id].manifest_sha256,
                }
                for screen_id in sorted(static_indexes)
                if screen_id is not None
            ],
            "targets": [
                {
                    "id": "static",
                    "compiler_target": "html-tailwind-app",
                    "artifact_path": "static",
                    "artifact_set_sha256": _artifact_set_sha256(static_dir),
                },
                {
                    "id": "react",
                    "compiler_target": "react-tailwind-app",
                    "artifact_path": "react",
                    "artifact_set_sha256": _artifact_set_sha256(react_dir),
                },
            ],
        }
        _write_private_file(manifest_path, canonical_json_bytes(manifest))
        _assert_no_external_runtime_references(artifact_dir)
    return static_indexes, manifest_path, "studio_comparison_manifest"


def _studio_inspection_projection(
    payload: dict[str, object],
    *,
    source_sha256: str,
    static_result: dict[str, object],
    react_result: dict[str, object],
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "studio_state_resource_inspection",
        "source_sha256": source_sha256,
        "policy": {
            "browser_replay": "exact_declared_action_clicks_v1",
            "coherence_measurement": "browser_observed_semantic_geometry_v1",
            "production_data": "not_claimed",
            "resource_scope": "declared_fixture_views_only",
        },
        "coherence": {
            "status": "browser_observed",
            "contract": "semantic_geometry_v1",
            "source_binding": "checked_cross_target_dom_identity",
            "viewports": ["mobile", "tablet", "desktop"],
            "thresholds": {
                "position_px": 3,
                "size_px": 3,
                "font_size_px": 0.5,
                "font_weight": 50,
            },
            "claim": "not_pixel_parity",
        },
        "state": _studio_state_inspection(payload, static_result=static_result, react_result=react_result),
        "resources": _studio_resource_inspection(static_result=static_result, react_result=react_result),
    }


def _studio_state_inspection(
    payload: dict[str, object],
    *,
    static_result: dict[str, object],
    react_result: dict[str, object],
) -> dict[str, object]:
    state_ir, issues = validate_state_ir(payload)
    if state_ir is None:
        if payload.get("schema_version") not in {3, 4}:
            return {"status": "not_declared", "replays": []}
        raise ReviewContractError(
            "REVIEW_COMPARISON_IDENTITY_MISMATCH",
            f"Studio could not reconstruct checked state inspection: {issues[0].message if issues else 'invalid state IR'}",
            "Fix the AppBundle state contract before opening Studio comparison.",
            http_status=422,
        )
    static_hash = static_result.get("state_contract_hash")
    react_hash = react_result.get("state_contract_hash")
    static_replay = static_result.get("state_replay")
    react_replay = react_result.get("state_replay")
    static_conformance = static_result.get("state_reducer_conformance")
    react_conformance = react_result.get("state_reducer_conformance")
    if (
        not isinstance(static_hash, str)
        or static_hash != react_hash
        or static_replay != react_replay
        or not isinstance(static_replay, dict)
        or static_replay.get("ok") is not True
        or not isinstance(static_conformance, dict)
        or static_conformance.get("ok") is not True
        or not isinstance(react_conformance, dict)
        or react_conformance.get("ok") is not True
    ):
        raise ReviewContractError(
            "REVIEW_COMPARISON_IDENTITY_MISMATCH",
            "Static and React state contracts or replay proof differ.",
            "Fix cross-target state generation before opening Studio comparison.",
            http_status=422,
        )
    mutations = {mutation.id: mutation for mutation in state_ir.mutations}
    trigger_counts: dict[tuple[str, str], int] = {}
    for mutation in state_ir.mutations:
        key = (mutation.trigger["screen_id"], mutation.trigger["action_id"])
        trigger_counts[key] = trigger_counts.get(key, 0) + 1
    routes = {
        str(route.get("screen_id")): str(route.get("path"))
        for route in payload.get("routes", [])
        if isinstance(route, dict) and isinstance(route.get("screen_id"), str) and isinstance(route.get("path"), str)
    }
    replay_proofs = {
        str(entry.get("id")): entry
        for entry in static_replay.get("assertions", [])
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    }
    replays: list[dict[str, object]] = []
    for assertion in state_ir.replay_assertions:
        checkpoints: list[dict[str, object]] = [
            {
                "index": 0,
                "label": "Initial",
                "evidence_ref": f"studio-inspection/replays/{assertion.id}/checkpoints/0",
                "event": None,
            }
        ]
        browser_replayable = True
        for index, event in enumerate(assertion.events, start=1):
            mutation_id = str(event.get("mutation_id"))
            mutation = mutations.get(mutation_id)
            if mutation is None:
                browser_replayable = False
                trigger = {"screen_id": "", "action_id": ""}
            else:
                trigger = mutation.trigger
                browser_replayable = browser_replayable and trigger_counts.get(
                    (trigger["screen_id"], trigger["action_id"]), 0
                ) == 1 and trigger["screen_id"] in routes
            checkpoints.append(
                {
                    "index": index,
                    "label": f"Event {index}",
                    "evidence_ref": f"studio-inspection/replays/{assertion.id}/checkpoints/{index}",
                    "event": {
                        "mutation_id": mutation_id,
                        "screen_id": trigger["screen_id"],
                        "route": routes.get(trigger["screen_id"]),
                        "action_id": trigger["action_id"],
                        "payload_values": event.get("payload_values", {}),
                    },
                }
            )
        checkpoints[-1]["expected"] = _studio_replay_expected(assertion)
        proof = replay_proofs.get(assertion.id)
        if not isinstance(proof, dict) or proof.get("status") != "passed":
            raise ReviewContractError(
                "REVIEW_COMPARISON_IDENTITY_MISMATCH",
                f"Studio replay inspection lacks passing proof for {assertion.id!r}.",
                "Fix the declared replay before opening Studio comparison.",
                http_status=422,
            )
        replays.append(
            {
                "id": assertion.id,
                "status": "passed",
                "browser_status": "replayable" if browser_replayable else "not_replayable",
                "checkpoints": checkpoints,
                "expectation_counts": {
                    "state": len(assertion.expect_state),
                    "selectors": len(assertion.expect_selectors),
                    "visibility": len(assertion.expect_visibility),
                    "text": len(assertion.expect_text),
                },
            }
        )
    return {
        "status": "ready",
        "contract_hash": static_hash,
        "reducer_conformance": "passed",
        "replay_proof": "passed",
        "replays": replays,
    }


def _studio_replay_expected(assertion: object) -> dict[str, object]:
    expect_state = getattr(assertion, "expect_state", {})
    expect_selectors = getattr(assertion, "expect_selectors", {})
    expect_visibility = getattr(assertion, "expect_visibility", {})
    expect_text = getattr(assertion, "expect_text", {})
    expected: dict[str, object] = {
        "state": [
            {
                "id": state_id,
                "kind": "scalar" if value is None or isinstance(value, (bool, int, float, str)) else "checked",
                **({"value": value} if value is None or isinstance(value, (bool, int, float, str)) else {}),
            }
            for state_id, value in expect_state.items()
        ],
        "selectors": [{"id": selector_id, "status": "checked"} for selector_id in expect_selectors],
        "visibility": [
            {"id": visibility_id, "visible": expected is True}
            for visibility_id, expected in expect_visibility.items()
        ],
    }
    if expect_text:
        expected["text"] = [
            {"id": projection_id, "value": expected}
            for projection_id, expected in expect_text.items()
        ]
    return expected


def _studio_resource_inspection(
    *,
    static_result: dict[str, object],
    react_result: dict[str, object],
) -> dict[str, object]:
    static_report = static_result.get("resource_binding_assertions")
    react_report = react_result.get("resource_binding_assertions")
    if not isinstance(static_report, dict) or not isinstance(react_report, dict):
        return {"status": "not_declared", "views": []}
    if (
        static_report.get("ok") is not True
        or static_report.get("binding_scope") != "declared_resource_views_only"
        or static_report.get("binding_digest") != react_report.get("binding_digest")
        or static_report.get("assertion_count") != react_report.get("assertion_count")
    ):
        raise ReviewContractError(
            "REVIEW_COMPARISON_IDENTITY_MISMATCH",
            "Static and React resource-binding proof differs.",
            "Fix cross-target resource binding before opening Studio comparison.",
            http_status=422,
        )
    views: list[dict[str, object]] = []
    raw_views = static_report.get("views")
    if not isinstance(raw_views, list):
        raise ReviewContractError(
            "REVIEW_COMPARISON_IDENTITY_MISMATCH",
            "Studio resource inspection has no checked declared views.",
            "Recompile the exact AppBundle resource views.",
            http_status=422,
        )
    allowed = {
        "canonical_identity",
        "expected",
        "field",
        "matched_binding_id",
        "matched_dom_id",
        "record_id",
        "resource_id",
        "resource_view_id",
        "screen_id",
        "status",
        "target_motif_id",
    }
    for view in raw_views:
        if not isinstance(view, dict) or not isinstance(view.get("assertions"), list):
            raise ReviewContractError(
                "REVIEW_COMPARISON_IDENTITY_MISMATCH",
                "Studio resource inspection contains an invalid checked view.",
                "Recompile the exact AppBundle resource views.",
                http_status=422,
            )
        views.append(
            {
                "id": view.get("id"),
                "screen_id": view.get("screen_id"),
                "resource_id": view.get("resource_id"),
                "target_motif_id": view.get("target_motif_id"),
                "status": view.get("status"),
                "assertions": [
                    {key: assertion.get(key) for key in allowed if key in assertion}
                    for assertion in view["assertions"]
                    if isinstance(assertion, dict)
                ],
            }
        )
    return {
        "status": "ready",
        "proof_status": "passed",
        "binding_scope": "declared_fixture_views_only",
        "binding_digest": static_report.get("binding_digest"),
        "assertion_count": static_report.get("assertion_count"),
        "views": views,
    }


def _build_react_review_runtime(snapshot: ReviewSourceSnapshot, artifact_dir: Path, *, allow_install: bool) -> None:
    if not allow_install:
        raise ReviewContractError(
            "REVIEW_SOURCE_UNSUPPORTED",
            "React AppBundle review requires its exact locked runtime dependencies.",
            "Retry with --install to authorize the existing npm ci --ignore-scripts flow.",
            http_status=422,
        )
    try:
        dependency_seed = os.environ.get("VIEWSPEC_STUDIO_REVIEW_NODE_MODULES_DIR")
        if dependency_seed:
            seed = Path(dependency_seed)
            if not seed.is_absolute() or not seed.is_dir() or seed.is_symlink():
                raise OSError("configured Studio review dependency seed is not one absolute normal directory")
            destination = artifact_dir / "node_modules"
            if destination.exists() or destination.is_symlink():
                raise OSError("Studio review dependency destination is not empty")
            materialize_prebuilt_node_modules(destination, seed.resolve())
            build_command = (
                str(destination / ".bin/vite"),
                "build",
                "--base",
                "./",
                "--outDir",
                "runtime-dist",
            )
        else:
            subprocess.run(
                ("npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"),
                cwd=artifact_dir,
                check=True,
                capture_output=True,
                timeout=30,
            )
            build_command = ("npm", "run", "build", "--", "--base", "./", "--outDir", "runtime-dist")
        subprocess.run(
            build_command,
            cwd=artifact_dir,
            check=True,
            capture_output=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise make_review_error("REVIEW_COMPILE_TIMEOUT", "React AppBundle dependency/build command exceeded 30 seconds.") from exc
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = (
            (exc.stderr or b"").decode("utf-8", errors="replace")[-2048:]
            if isinstance(exc, subprocess.CalledProcessError)
            else str(exc)
        )
        raise ReviewContractError(
            "REVIEW_COMPILE_FAILED",
            f"React AppBundle runtime build failed: {detail}",
            "Restore the locked npm runtime dependencies and retry with --install.",
            http_status=422,
        ) from exc

    runtime_dir = artifact_dir / "runtime-dist"
    _inline_vite_runtime(runtime_dir)
    react_manifest = _strict_json_object(
        _read_bounded_regular_file(artifact_dir / "viewspec_app_manifest.json", maximum=MAX_ARTIFACT_BYTES),
        code="REVIEW_CHECK_FAILED",
    )
    payload = _strict_json_object(snapshot.source_bytes, code="REVIEW_SOURCE_INVALID")
    raw_screens = react_manifest.get("screen_artifacts")
    if not isinstance(raw_screens, list):
        raise ReviewContractError(
            "REVIEW_CHECK_FAILED",
            "React AppBundle manifest has no checked screen artifacts.",
            "Recompile with the current ViewSpec React AppBundle target.",
            http_status=422,
        )
    manifest_dir = runtime_dir / "viewspec-manifests"
    _make_private_dir(manifest_dir)
    screens: list[dict[str, object]] = []
    for entry in raw_screens:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not isinstance(entry.get("manifest"), str):
            raise _ambiguous_app_manifest()
        screen_id = entry["id"]
        source_manifest = artifact_dir / entry["manifest"]
        manifest_bytes = _read_bounded_regular_file(source_manifest, maximum=MAX_ARTIFACT_BYTES)
        if hashlib.sha256(manifest_bytes).hexdigest() != entry.get("manifest_hash"):
            raise _stored_revision_mismatch("React AppBundle screen manifest hash does not match.")
        destination = manifest_dir / f"{screen_id}.json"
        _write_private_file(destination, manifest_bytes)
        screens.append(
            {
                "id": screen_id,
                "manifest": destination.relative_to(runtime_dir).as_posix(),
                "manifest_hash": entry["manifest_hash"],
            }
        )
    routes = payload.get("routes")
    if not isinstance(routes, list):
        raise _ambiguous_app_manifest()
    review_manifest = {
        "schema_version": 1,
        "kind": "review_react_app_manifest",
        "target": "react-tailwind-app",
        "policy": {"network_calls": "none"},
        "index_sha256": _sha256_file(runtime_dir / "index.html"),
        "routes": [
            {"id": route.get("id"), "path": route.get("path"), "screenId": route.get("screen_id")}
            for route in routes
            if isinstance(route, dict)
        ],
        "screens": screens,
    }
    _write_private_file(runtime_dir / "review_manifest.json", canonical_json_bytes(review_manifest))
    source_tree = artifact_dir.with_name(f".{artifact_dir.name}.react-source")
    os.replace(artifact_dir, source_tree)
    os.replace(source_tree / "runtime-dist", artifact_dir)
    shutil.rmtree(source_tree)


def _inline_vite_runtime(runtime_dir: Path) -> None:
    index = runtime_dir / "index.html"
    try:
        html = index.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ReviewContractError(
            "REVIEW_COMPILE_FAILED",
            f"React runtime index could not be read: {exc}",
            "Rebuild the locked Vite runtime.",
            http_status=422,
        ) from exc

    script_pattern = re.compile(r'<script\s+type="module"\s+crossorigin\s+src="([^"]+)"></script>')
    style_pattern = re.compile(r'<link\s+rel="stylesheet"\s+crossorigin\s+href="([^"]+)">')

    def script(match: re.Match[str]) -> str:
        content = _runtime_asset(runtime_dir, match.group(1)).decode("utf-8").replace("</script", "<\\/script")
        return f'<script type="module">{content}</script>'

    def style(match: re.Match[str]) -> str:
        content = _runtime_asset(runtime_dir, match.group(1)).decode("utf-8").replace("</style", "<\\/style")
        return f"<style>{content}</style>"

    html, script_count = script_pattern.subn(script, html)
    html, _ = style_pattern.subn(style, html)
    if script_count != 1:
        raise ReviewContractError(
            "REVIEW_COMPILE_FAILED",
            "React runtime must contain exactly one local Vite module entry.",
            "Rebuild with the pinned ViewSpec Vite template.",
            http_status=422,
        )
    index.write_text(html, encoding="utf-8")
    assets = runtime_dir / "assets"
    if assets.exists():
        shutil.rmtree(assets)


def _runtime_asset(runtime_dir: Path, reference: str) -> bytes:
    relative = reference.removeprefix("./")
    path = runtime_dir / relative
    if path.parent != runtime_dir / "assets" or not path.is_file() or path.is_symlink():
        raise ReviewContractError(
            "REVIEW_EXTERNAL_REFERENCE_FORBIDDEN",
            "React runtime references a nonlocal or unbounded build asset.",
            "Use the exact pinned Vite build output.",
            http_status=422,
        )
    return _read_bounded_regular_file(path, maximum=MAX_ARTIFACT_BYTES)


def _load_react_app_indexes(artifact_dir: Path) -> tuple[dict[str | None, ReviewManifestIndex], Path]:
    manifest_path = artifact_dir / "review_manifest.json"
    manifest = _strict_json_object(
        _read_bounded_regular_file(manifest_path, maximum=MAX_ARTIFACT_BYTES),
        code="REVIEW_CHECK_FAILED",
    )
    if (
        manifest.get("kind") != "review_react_app_manifest"
        or manifest.get("target") != "react-tailwind-app"
        or manifest.get("policy") != {"network_calls": "none"}
        or manifest.get("index_sha256") != _sha256_file(artifact_dir / "index.html")
    ):
        raise ReviewContractError(
            "REVIEW_CHECK_FAILED",
            "React Review runtime manifest does not prove its checked local artifact.",
            "Rebuild the exact React AppBundle runtime.",
            http_status=422,
        )
    screens = manifest.get("screens")
    if not isinstance(screens, list) or not 1 <= len(screens) <= 16:
        raise _ambiguous_app_manifest()
    indexes: dict[str | None, ReviewManifestIndex] = {}
    for entry in screens:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not isinstance(entry.get("manifest"), str):
            raise _ambiguous_app_manifest()
        screen_id = entry["id"]
        path = artifact_dir / entry["manifest"]
        content = _read_bounded_regular_file(path, maximum=MAX_ARTIFACT_BYTES)
        index = ReviewManifestIndex.from_bytes(content, screen_id=screen_id)
        if screen_id in indexes or index.manifest_sha256 != entry.get("manifest_hash"):
            raise _ambiguous_app_manifest()
        indexes[screen_id] = index
    return indexes, manifest_path


def _load_studio_comparison_indexes(
    artifact_dir: Path,
    *,
    expected_source_sha256: str,
) -> tuple[dict[str | None, ReviewManifestIndex], Path]:
    manifest_path = artifact_dir / STUDIO_COMPARE_MANIFEST
    manifest = _strict_json_object(
        _read_bounded_regular_file(manifest_path, maximum=MAX_ARTIFACT_BYTES),
        code="REVIEW_CHECK_FAILED",
    )
    if (
        set(manifest)
        != {
            "schema_version",
            "kind",
            "target",
            "source_sha256",
            "policy",
            "routes",
            "inspection",
            "screens",
            "targets",
        }
        or manifest.get("schema_version") != 1
        or manifest.get("kind") != "studio_static_react_comparison"
        or manifest.get("target") != STUDIO_COMPARE_TARGET
        or manifest.get("source_sha256") != expected_source_sha256
        or manifest.get("policy")
        != {
            "dependency_install": "explicit_opt_in",
            "runtime_network_calls": "none",
            "visual_parity": "not_proven",
        }
    ):
        raise ReviewContractError(
            "REVIEW_COMPARISON_IDENTITY_MISMATCH",
            "Studio comparison manifest does not match its exact source and bounded policy.",
            "Rebuild both targets from the exact current AppBundle.",
            http_status=422,
        )

    static_dir = artifact_dir / "static"
    react_dir = artifact_dir / "react"
    static_indexes, static_manifest = _load_app_indexes(static_dir, expected_target="html-tailwind-app")
    react_indexes, react_manifest = _load_react_app_indexes(react_dir)
    _assert_cross_target_identity(static_indexes, react_indexes)
    routes = _checked_routes(static_manifest)
    if routes != _checked_routes(react_manifest) or manifest.get("routes") != routes:
        raise ReviewContractError(
            "REVIEW_COMPARISON_IDENTITY_MISMATCH",
            "Studio comparison routes changed or diverge across targets.",
            "Rebuild both targets from the exact current AppBundle.",
            http_status=422,
        )

    expected_targets = [
        {
            "id": "static",
            "compiler_target": "html-tailwind-app",
            "artifact_path": "static",
            "artifact_set_sha256": _artifact_set_sha256(static_dir),
        },
        {
            "id": "react",
            "compiler_target": "react-tailwind-app",
            "artifact_path": "react",
            "artifact_set_sha256": _artifact_set_sha256(react_dir),
        },
    ]
    expected_screens = [
        {
            "id": screen_id,
            "semantic_identity_sha256": static_indexes[screen_id].semantic_identity_sha256,
            "static_manifest_sha256": static_indexes[screen_id].manifest_sha256,
            "react_manifest_sha256": react_indexes[screen_id].manifest_sha256,
        }
        for screen_id in sorted(static_indexes)
        if screen_id is not None
    ]
    inspection_path = artifact_dir / STUDIO_INSPECTION_MANIFEST
    inspection_bytes = _read_bounded_regular_file(inspection_path, maximum=STUDIO_INSPECTION_MAX_BYTES)
    inspection = _strict_json_object(inspection_bytes, code="REVIEW_CHECK_FAILED")
    state = inspection.get("state") if isinstance(inspection.get("state"), dict) else {}
    resources = inspection.get("resources") if isinstance(inspection.get("resources"), dict) else {}
    coherence = inspection.get("coherence") if isinstance(inspection.get("coherence"), dict) else {}
    expected_inspection = {
        "path": STUDIO_INSPECTION_MANIFEST,
        "sha256": hashlib.sha256(inspection_bytes).hexdigest(),
        "coherence_status": coherence.get("status"),
        "coherence_contract": coherence.get("contract"),
        "state_status": state.get("status"),
        "resource_status": resources.get("status"),
    }
    if (
        inspection.get("schema_version") != 1
        or inspection.get("kind") != "studio_state_resource_inspection"
        or inspection.get("source_sha256") != expected_source_sha256
        or inspection.get("policy")
        != {
            "browser_replay": "exact_declared_action_clicks_v1",
            "coherence_measurement": "browser_observed_semantic_geometry_v1",
            "production_data": "not_claimed",
            "resource_scope": "declared_fixture_views_only",
        }
        or coherence
        != {
            "status": "browser_observed",
            "contract": "semantic_geometry_v1",
            "source_binding": "checked_cross_target_dom_identity",
            "viewports": ["mobile", "tablet", "desktop"],
            "thresholds": {
                "position_px": 3,
                "size_px": 3,
                "font_size_px": 0.5,
                "font_weight": 50,
            },
            "claim": "not_pixel_parity",
        }
        or state.get("status") not in {"ready", "not_declared"}
        or resources.get("status") not in {"ready", "not_declared"}
        or manifest.get("inspection") != expected_inspection
        or manifest.get("targets") != expected_targets
        or manifest.get("screens") != expected_screens
    ):
        raise ReviewContractError(
            "REVIEW_COMPARISON_IDENTITY_MISMATCH",
            "Studio comparison target, screen, or inspection identity no longer matches its checked artifacts.",
            "Rebuild the immutable static/React comparison revision.",
            http_status=422,
        )
    return static_indexes, manifest_path


def _assert_cross_target_identity(
    static_indexes: dict[str | None, ReviewManifestIndex],
    react_indexes: dict[str | None, ReviewManifestIndex],
) -> None:
    if set(static_indexes) != set(react_indexes):
        raise ReviewContractError(
            "REVIEW_COMPARISON_IDENTITY_MISMATCH",
            "Static and React comparison targets do not expose the same checked screens.",
            "Fix cross-target screen generation before opening Studio comparison.",
            http_status=422,
        )
    for screen_id in static_indexes:
        if static_indexes[screen_id].semantic_identity != react_indexes[screen_id].semantic_identity:
            raise ReviewContractError(
                "REVIEW_COMPARISON_IDENTITY_MISMATCH",
                f"Static and React semantic identity differs for screen {screen_id!r}.",
                "Fix cross-target DOM, IR, binding, action, and provenance identity before comparison.",
                http_status=422,
            )


def _checked_routes(manifest_path: Path) -> list[dict[str, str]]:
    manifest = _strict_json_object(
        _read_bounded_regular_file(manifest_path, maximum=MAX_ARTIFACT_BYTES),
        code="REVIEW_CHECK_FAILED",
    )
    raw_routes = manifest.get("routes")
    if not isinstance(raw_routes, list) or not 1 <= len(raw_routes) <= 32:
        raise _ambiguous_app_manifest()
    routes: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    for route in raw_routes:
        if not isinstance(route, dict):
            raise _ambiguous_app_manifest()
        normalized = {
            "id": route.get("id"),
            "path": route.get("path"),
            "screenId": route.get("screenId"),
        }
        if (
            not all(isinstance(value, str) and value for value in normalized.values())
            or normalized["path"] in seen_paths
        ):
            raise _ambiguous_app_manifest()
        seen_paths.add(normalized["path"])
        routes.append(normalized)
    return routes


def _assert_no_external_runtime_references(artifact_dir: Path) -> None:
    external = re.compile(
        rb"(?ix)(?:"
        rb"(?:src|href|action)\s*=\s*['\"]\s*(?:https?:)?//"
        rb"|(?:url|@import)\s*\(?\s*['\"]?\s*(?:https?:)?//"
        rb"|(?:fetch|WebSocket|EventSource|import)\s*\(\s*['\"]\s*(?:https?:)?//"
        rb")"
    )
    for path in artifact_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".html", ".css", ".js", ".mjs"}:
            continue
        content = _read_bounded_regular_file(path, maximum=MAX_ARTIFACT_BYTES)
        if external.search(content):
            raise make_review_error(
                "REVIEW_EXTERNAL_REFERENCE_FORBIDDEN",
                f"Checked runtime artifact {path.name} contains a remote URL reference.",
            )


def _load_app_indexes(
    artifact_dir: Path,
    *,
    expected_target: str,
) -> tuple[dict[str | None, ReviewManifestIndex], Path]:
    manifest_path = artifact_dir / "shell_manifest.json"
    manifest_bytes = _read_bounded_regular_file(manifest_path, maximum=MAX_ARTIFACT_BYTES)
    shell = _strict_json_object(manifest_bytes, code="REVIEW_CHECK_FAILED")
    if (
        shell.get("kind") != "app_static_shell_compile"
        or shell.get("target") != expected_target
        or shell.get("policy") != {"network_calls": "none"}
    ):
        raise ReviewContractError(
            "REVIEW_CHECK_FAILED",
            "AppBundle shell manifest does not prove the required local Review policy.",
            "Recompile the AppBundle with the current no-network HTML app target.",
            http_status=422,
        )
    index_path = artifact_dir / "index.html"
    if shell.get("shell_artifact_hash") != _sha256_file(index_path):
        raise ReviewContractError(
            "REVIEW_CHECK_FAILED",
            "AppBundle shell artifact hash does not match its checked manifest.",
            "Recompile the complete AppBundle artifact set.",
            http_status=422,
        )
    raw_screens = shell.get("screens")
    if not isinstance(raw_screens, list) or not 1 <= len(raw_screens) <= 16:
        raise ReviewContractError(
            "REVIEW_MANIFEST_AMBIGUOUS",
            "AppBundle shell must declare between 1 and 16 checked screens.",
            "Fix the bounded AppBundle screen manifest and recompile.",
            http_status=422,
        )
    indexes: dict[str | None, ReviewManifestIndex] = {}
    for entry in raw_screens:
        if not isinstance(entry, dict):
            raise _ambiguous_app_manifest()
        screen_id = entry.get("id")
        if (
            not isinstance(screen_id, str)
            or not screen_id
            or len(screen_id.encode("utf-8")) > 128
            or "/" in screen_id
            or "\\" in screen_id
            or screen_id in indexes
        ):
            raise _ambiguous_app_manifest()
        screen_artifact = artifact_dir / "screens" / screen_id / "artifact"
        checked = check_artifact_dir(screen_artifact)
        if checked.get("ok") is not True:
            raise ReviewContractError(
                "REVIEW_CHECK_FAILED",
                f"Compiled AppBundle screen {screen_id!r} failed the mandatory artifact check.",
                "Fix the checked screen artifact errors and rebuild from AppBundle source.",
                http_status=422,
            )
        screen_manifest_path = screen_artifact / "provenance_manifest.json"
        screen_manifest_bytes = _read_bounded_regular_file(screen_manifest_path, maximum=MAX_ARTIFACT_BYTES)
        screen_index = ReviewManifestIndex.from_bytes(screen_manifest_bytes, screen_id=screen_id)
        screen_html_path = screen_artifact / "index.html"
        if (
            entry.get("manifest_hash") != screen_index.manifest_sha256
            or entry.get("artifact_hash") != _sha256_file(screen_html_path)
            or entry.get("validation_status") != "passed"
            or entry.get("compile_status") != "passed"
            or entry.get("check_status") != "passed"
        ):
            raise ReviewContractError(
                "REVIEW_REVISION_IDENTITY_MISMATCH",
                f"AppBundle screen {screen_id!r} identity does not match the shell manifest.",
                "Recompile the exact AppBundle artifact set before promotion.",
                http_status=500,
                cli_exit=1,
            )
        indexes[screen_id] = screen_index
    return indexes, manifest_path


def _validate_intent_snapshot(snapshot: ReviewSourceSnapshot) -> None:
    try:
        text = snapshot.source_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReviewContractError(
            "REVIEW_SOURCE_INVALID",
            "Captured IntentBundle is not UTF-8 text.",
            "Encode the source as strict UTF-8 JSON and retry.",
            cli_exit=2,
        ) from exc
    validation = validate_intent_text(text, compile_check=True)
    if validation.get("ok") is not True:
        raise ReviewContractError(
            "REVIEW_SOURCE_INVALID",
            "Captured IntentBundle failed ViewSpec validation.",
            "Fix the validation issues in semantic source and retry.",
            http_status=422,
        )


def _validate_app_snapshot(snapshot: ReviewSourceSnapshot) -> None:
    try:
        text = snapshot.source_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReviewContractError(
            "REVIEW_SOURCE_INVALID",
            "Captured AppBundle is not UTF-8 text.",
            "Encode the source as strict UTF-8 JSON and retry.",
            cli_exit=2,
        ) from exc
    validation = validate_app_text(text, compile_check=True)
    if validation.get("ok") is not True:
        raise ReviewContractError(
            "REVIEW_SOURCE_INVALID",
            "Captured AppBundle failed ViewSpec validation.",
            "Fix the validation issues in semantic source and retry.",
            http_status=422,
        )


def _strict_json_object(content: bytes, *, code: str) -> dict[str, object]:
    try:
        value = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_json_object, parse_constant=_reject_json_constant)
    except ReviewContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ReviewContractError(
            code,
            f"Generated Review manifest is not strict UTF-8 JSON: {exc}",
            "Recompile with the current ViewSpec compiler.",
            http_status=422,
        ) from exc
    if not isinstance(value, dict):
        raise ReviewContractError(
            code,
            "Generated Review manifest root must be an object.",
            "Recompile with the current ViewSpec compiler.",
            http_status=422,
        )
    return value


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ReviewContractError(
                "REVIEW_MANIFEST_AMBIGUOUS",
                f"Generated Review manifest repeats object key {key!r}.",
                "Recompile with a canonical ViewSpec manifest serializer.",
                http_status=422,
            )
        result[key] = value
    return result


def _ambiguous_app_manifest() -> ReviewContractError:
    return ReviewContractError(
        "REVIEW_MANIFEST_AMBIGUOUS",
        "AppBundle shell contains an invalid or duplicate screen identity.",
        "Recompile a bounded AppBundle with unique canonical screen ids.",
        http_status=422,
    )


def _stored_revision_mismatch(message: str) -> ReviewContractError:
    return ReviewContractError(
        "REVIEW_REVISION_IDENTITY_MISMATCH",
        message,
        "Do not serve the changed artifact; create a new checked Review session.",
        http_status=500,
        cli_exit=1,
    )


def _make_private_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=False, exist_ok=True)
    current = path.lstat()
    if stat.S_ISLNK(current.st_mode) or not stat.S_ISDIR(current.st_mode):
        raise ReviewContractError(
            "REVIEW_FILESYSTEM_UNSAFE",
            "Review state path is not a private directory.",
            "Choose a local owner-controlled state directory.",
            cli_exit=2,
        )
    path.chmod(0o700)


def _write_private_file(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(content)
        written = 0
        while written < len(view):
            written += os.write(descriptor, view[written:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_bounded_regular_file(path: Path, *, maximum: int) -> bytes:
    value = path.lstat()
    if not stat.S_ISREG(value.st_mode) or stat.S_ISLNK(value.st_mode) or value.st_size > maximum:
        raise ReviewContractError(
            "REVIEW_STORAGE_LIMIT_EXCEEDED",
            f"Generated Review artifact file is not regular or exceeds {maximum} bytes.",
            "Reduce the generated artifact below the Review V0 storage limit.",
            http_status=507,
        )
    with path.open("rb") as handle:
        content = handle.read(maximum + 1)
    if len(content) > maximum:
        raise ReviewContractError(
            "REVIEW_STORAGE_LIMIT_EXCEEDED",
            f"Generated Review artifact file exceeds {maximum} bytes.",
            "Reduce the generated artifact below the Review V0 storage limit.",
            http_status=507,
        )
    return content


def _artifact_set_sha256(root: Path) -> str:
    entries: list[tuple[str, int, str]] = []
    total = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        value = path.lstat()
        if stat.S_ISDIR(value.st_mode) and not stat.S_ISLNK(value.st_mode):
            continue
        if not stat.S_ISREG(value.st_mode) or stat.S_ISLNK(value.st_mode):
            raise ReviewContractError(
                "REVIEW_FILESYSTEM_UNSAFE",
                "Generated Review artifact contains a non-regular file.",
                "Emit only regular allowlisted artifact files.",
                cli_exit=2,
            )
        if len(entries) >= MAX_ARTIFACT_FILES:
            raise ReviewContractError(
                "REVIEW_STORAGE_LIMIT_EXCEEDED",
                f"Generated Review artifact contains more than {MAX_ARTIFACT_FILES} files.",
                "Reduce the generated artifact file count.",
                http_status=507,
            )
        total += value.st_size
        if value.st_size > MAX_ARTIFACT_BYTES or total > MAX_ARTIFACT_BYTES:
            raise ReviewContractError(
                "REVIEW_STORAGE_LIMIT_EXCEEDED",
                f"Generated Review artifact exceeds {MAX_ARTIFACT_BYTES} retained bytes.",
                "Reduce the generated artifact below the 24 MiB revision limit.",
                http_status=507,
            )
        entries.append((path.relative_to(root).as_posix(), value.st_size, _sha256_file(path)))
    if not entries:
        raise ReviewContractError(
            "REVIEW_CHECK_FAILED",
            "Compiler produced an empty Review artifact set.",
            "Retry with a supported checked ViewSpec target.",
            http_status=422,
        )
    digest = hashlib.sha256()
    digest.update(_ARTIFACT_SET_DOMAIN)
    for relative, size, file_sha256 in entries:
        record = canonical_json_bytes({"path": relative, "sha256": file_sha256, "size": size})
        digest.update(len(record).to_bytes(8, "big"))
        digest.update(record)
    return digest.hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_READ_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _seal_private_tree(root: Path) -> None:
    directories = [root]
    for path in root.rglob("*"):
        value = path.lstat()
        if stat.S_ISLNK(value.st_mode):
            raise ReviewContractError(
                "REVIEW_FILESYSTEM_UNSAFE",
                "Generated Review candidate contains a symbolic link.",
                "Emit only private regular files and directories.",
                cli_exit=2,
            )
        if stat.S_ISDIR(value.st_mode):
            path.chmod(0o700)
            directories.append(path)
        elif stat.S_ISREG(value.st_mode):
            path.chmod(0o600)
            descriptor = os.open(path, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        else:
            raise ReviewContractError(
                "REVIEW_FILESYSTEM_UNSAFE",
                "Generated Review candidate contains a non-regular filesystem entry.",
                "Emit only private regular files and directories.",
                cli_exit=2,
            )
    for directory in reversed(directories):
        _fsync_directory(directory)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _discard_candidate(path: Path) -> None:
    if path.exists() and path.parent.name == ".candidates":
        shutil.rmtree(path)


def _reserve_build_storage(session_dir: Path) -> None:
    sessions_dir = session_dir.parent
    session_size = _bounded_tree_size(session_dir, MAX_SESSION_STORAGE_BYTES)
    if session_size + BUILD_STORAGE_RESERVATION_BYTES > MAX_SESSION_STORAGE_BYTES:
        raise ReviewContractError(
            "REVIEW_STORAGE_LIMIT_EXCEEDED",
            "Review session cannot reserve 25 MiB for another checked candidate.",
            "Purge old retained revisions or verification evidence before rebuilding.",
            http_status=507,
            cli_exit=2,
        )
    retained_size = _bounded_tree_size(sessions_dir, MAX_RETAINED_STORAGE_BYTES)
    if retained_size + BUILD_STORAGE_RESERVATION_BYTES > MAX_RETAINED_STORAGE_BYTES:
        raise ReviewContractError(
            "REVIEW_STORAGE_LIMIT_EXCEEDED",
            "Retained Review state cannot reserve another checked candidate within 4 GiB.",
            "Purge ended retained sessions before rebuilding.",
            http_status=507,
            cli_exit=2,
        )
    active_size = session_size
    if sessions_dir.is_dir():
        for directory in sessions_dir.iterdir():
            if directory == session_dir or not directory.is_dir() or not (directory / "server.json").is_file():
                continue
            active_size += _bounded_tree_size(directory, MAX_ACTIVE_STORAGE_BYTES)
            if active_size > MAX_ACTIVE_STORAGE_BYTES:
                break
    if active_size + BUILD_STORAGE_RESERVATION_BYTES > MAX_ACTIVE_STORAGE_BYTES:
        raise ReviewContractError(
            "REVIEW_STORAGE_LIMIT_EXCEEDED",
            "Active Review sessions cannot reserve another candidate within 1 GiB.",
            "End or purge another active session before rebuilding.",
            http_status=507,
            cli_exit=2,
        )


def _bounded_tree_size(root: Path, stop_after: int) -> int:
    if not root.exists():
        return 0
    total = 0
    for path in root.rglob("*"):
        value = path.lstat()
        if stat.S_ISLNK(value.st_mode):
            raise ReviewContractError(
                "REVIEW_FILESYSTEM_UNSAFE",
                "Review state storage contains a symbolic link.",
                "Use only owner-controlled regular files and directories.",
                cli_exit=2,
            )
        if stat.S_ISREG(value.st_mode):
            total += value.st_size
            if total > stop_after:
                return total
    return total


@bounded_review_operation("REVIEW_SNAPSHOT_TIMEOUT", 5)
def capture_source_snapshot(
    source_path: str | Path,
    *,
    design_path: str | Path | None = None,
    _after_source_read: Callable[[], None] | None = None,
) -> ReviewSourceSnapshot:
    """Read source/design bytes once and prove their path identities remained stable."""
    source = _absolute_path(source_path)
    source_bytes, source_identity = _read_stable_file(
        source,
        maximum=APP_SOURCE_MAX_BYTES,
        after_read=_after_source_read,
    )
    source_kind = _source_kind(source_bytes)
    kind_limit = INTENT_SOURCE_MAX_BYTES if source_kind == "intent_bundle" else APP_SOURCE_MAX_BYTES
    if len(source_bytes) > kind_limit:
        raise _too_large(source, len(source_bytes), kind_limit)

    design: Path | None = None
    design_bytes: bytes | None = None
    design_identity: CapturedFileIdentity | None = None
    if design_path is not None:
        design = _absolute_path(design_path)
        if source == design:
            raise ReviewContractError(
                "REVIEW_FILESYSTEM_UNSAFE",
                "Review source and DESIGN.md must be different regular files.",
                "Pass distinct source and design paths.",
            )
        design_bytes, design_identity = _read_stable_file(design, maximum=DESIGN_MAX_BYTES)

    aggregate = len(source_bytes) + len(design_bytes or b"")
    if aggregate > AGGREGATE_CAPTURE_MAX_BYTES:
        raise ReviewContractError(
            "REVIEW_SOURCE_TOO_LARGE",
            f"Captured source and design total {aggregate} bytes, above {AGGREGATE_CAPTURE_MAX_BYTES}.",
            "Reduce source or DESIGN.md before opening Review.",
            cli_exit=2,
        )
    return ReviewSourceSnapshot(
        source_path=source,
        source_kind=source_kind,
        source_bytes=source_bytes,
        source_sha256=hashlib.sha256(source_bytes).hexdigest(),
        source_identity=source_identity,
        design_path=design,
        design_bytes=design_bytes,
        design_sha256=hashlib.sha256(design_bytes).hexdigest() if design_bytes is not None else None,
        design_identity=design_identity,
    )


def _absolute_path(value: str | Path) -> Path:
    raw = Path(value).expanduser()
    return Path(os.path.abspath(raw))


def _read_stable_file(
    path: Path,
    *,
    maximum: int,
    after_read: Callable[[], None] | None = None,
) -> tuple[bytes, CapturedFileIdentity]:
    try:
        before_path = path.lstat()
    except FileNotFoundError as exc:
        raise ReviewContractError(
            "REVIEW_SOURCE_NOT_FOUND",
            f"Review input does not exist: {path.name}",
            "Pass an existing local IntentBundle, AppBundle, or DESIGN.md file.",
            cli_exit=2,
        ) from exc
    except OSError as exc:
        raise ReviewContractError(
            "REVIEW_FILESYSTEM_UNSAFE",
            f"Could not inspect Review input {path.name}: {exc}",
            "Pass a readable regular file owned by the current user.",
            cli_exit=2,
        ) from exc
    _assert_safe_file(path, before_path)
    flags = os.O_RDONLY | _NOFOLLOW_FLAG
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        before_descriptor = os.fstat(descriptor)
        if (before_descriptor.st_dev, before_descriptor.st_ino) != (before_path.st_dev, before_path.st_ino):
            raise _source_changed(path)
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(_READ_CHUNK_BYTES, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise _too_large(path, total, maximum)
        content = b"".join(chunks)
        if after_read is not None:
            after_read()
        after_descriptor = os.fstat(descriptor)
        try:
            after_path = path.lstat()
        except OSError as exc:
            raise _source_changed(path) from exc
        before_identity = CapturedFileIdentity.from_stat(before_descriptor)
        if (
            before_identity != CapturedFileIdentity.from_stat(after_descriptor)
            or before_identity != CapturedFileIdentity.from_stat(after_path)
            or len(content) != before_identity.size
        ):
            raise _source_changed(path)
        _assert_safe_file(path, after_path)
        return content, before_identity
    except ReviewContractError:
        raise
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.EMLINK}:
            raise ReviewContractError(
                "REVIEW_FILESYSTEM_UNSAFE",
                f"Review input is a symbolic link: {path.name}",
                "Use an owner-controlled regular file with one hard link.",
                cli_exit=2,
            ) from exc
        raise ReviewContractError(
            "REVIEW_FILESYSTEM_UNSAFE",
            f"Could not read Review input {path.name}: {exc}",
            "Use an owner-readable regular file with one hard link.",
            cli_exit=2,
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _assert_safe_file(path: Path, value: os.stat_result) -> None:
    owner = os.geteuid() if hasattr(os, "geteuid") else value.st_uid
    if (
        stat.S_ISLNK(value.st_mode)
        or not stat.S_ISREG(value.st_mode)
        or value.st_uid != owner
        or value.st_nlink != 1
        or not value.st_mode & stat.S_IRUSR
    ):
        raise ReviewContractError(
            "REVIEW_FILESYSTEM_UNSAFE",
            f"Review input is not an owner-readable, single-link regular file: {path.name}",
            "Copy the input to a private regular file owned by the current user.",
            cli_exit=2,
        )


def _source_kind(content: bytes) -> str:
    try:
        text = content.decode("utf-8")
        payload = json.loads(text, parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ReviewContractError(
            "REVIEW_SOURCE_INVALID",
            f"Review source is not strict UTF-8 JSON: {exc}",
            "Fix the source JSON before opening Review.",
            cli_exit=2,
        ) from exc
    if not isinstance(payload, dict):
        raise ReviewContractError(
            "REVIEW_SOURCE_UNSUPPORTED",
            "Review source root must be an IntentBundle or AppBundle object.",
            "Pass a strict ViewSpec IntentBundle or AppBundle JSON file.",
            cli_exit=2,
        )
    is_intent = {"substrate", "view_spec"} <= set(payload)
    is_app = {"app", "routes", "screens"} <= set(payload)
    if is_intent == is_app:
        raise ReviewContractError(
            "REVIEW_SOURCE_UNSUPPORTED",
            "Review source does not identify exactly one supported bundle kind.",
            "Pass one strict IntentBundle or AppBundle JSON file.",
            cli_exit=2,
        )
    return "intent_bundle" if is_intent else "app_bundle"


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value}")


def _too_large(path: Path, observed: int, maximum: int) -> ReviewContractError:
    return ReviewContractError(
        "REVIEW_SOURCE_TOO_LARGE",
        f"Review input {path.name} is {observed} bytes, above {maximum}.",
        f"Reduce {path.name} to at most {maximum} bytes.",
        cli_exit=2,
    )


def _source_changed(path: Path) -> ReviewContractError:
    return ReviewContractError(
        "REVIEW_SOURCE_CHANGED_DURING_CAPTURE",
        f"Review input changed while it was being captured: {path.name}",
        "Wait for the editor write to finish and retry the newest generation.",
        http_status=409,
        cli_exit=2,
    )


__all__ = [
    "AGGREGATE_CAPTURE_MAX_BYTES",
    "APP_SOURCE_MAX_BYTES",
    "BuiltReviewRevision",
    "CapturedFileIdentity",
    "DESIGN_MAX_BYTES",
    "GenerationGate",
    "INTENT_SOURCE_MAX_BYTES",
    "MAX_ARTIFACT_BYTES",
    "MAX_ARTIFACT_FILES",
    "ReviewSourceSnapshot",
    "STUDIO_COMPARE_MANIFEST",
    "STUDIO_COMPARE_TARGET",
    "STUDIO_INSPECTION_MANIFEST",
    "bounded_review_phase",
    "bounded_review_operation",
    "build_review_revision",
    "capture_source_snapshot",
    "compute_review_semantic_diff",
    "load_review_revision",
    "load_review_semantic_diff",
]
