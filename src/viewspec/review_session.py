"""Durable single-consumer Review V0 event journal and delivery state machine."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import struct
import stat
import time
from typing import Any, Mapping

from viewspec.review_contract import (
    MAX_BATCH_EVENTS,
    MAX_BODY_BYTES,
    MAX_SESSION_EVENTS,
    MAX_UNACKNOWLEDGED_EVENTS,
    MIB,
    ReviewBatch,
    ReviewContext,
    ReviewContractError,
    ReviewEvent,
    ReviewRevision,
    ReviewTarget,
    canonical_json_bytes,
    canonical_json_sha256,
    new_batch_id,
    new_event_id,
    new_review_id,
    validate_idempotency_key,
)
from viewspec.review_compile import bounded_review_phase
from viewspec.review_errors import make_review_error


MAX_FRAME_BYTES = 24 * 1024
MAX_JOURNAL_BYTES = 16 * MIB
MAX_SESSION_STORAGE_BYTES = 256 * MIB
COMPACTION_JOURNAL_BYTES = 8 * MIB
COMPACTION_ACKNOWLEDGEMENTS = 256
COMPACTION_TIMEOUT_SECONDS = 2.0
_FRAME_LENGTH = struct.Struct(">I")
_FRAME_DIGEST_BYTES = hashlib.sha256().digest_size


class ReviewStateLock:
    """Advisory OS lock with a bounded acquisition deadline for one Review writer."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._descriptor: int | None = None

    def acquire(self, *, timeout_seconds: float = 2.0) -> None:
        if self._descriptor is not None:
            return
        if not 0 < timeout_seconds <= 2.0:
            raise ValueError("Review lock timeout must be greater than zero and at most 2 seconds")
        try:
            import fcntl
        except ImportError as exc:
            raise ReviewContractError(
                "REVIEW_FILESYSTEM_UNSAFE",
                "Review V0 requires local OS advisory file locking.",
                "Use a supported local filesystem and operating system.",
                cli_exit=2,
            ) from exc
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        value = os.fstat(descriptor)
        owner = os.geteuid() if hasattr(os, "geteuid") else value.st_uid
        if not stat.S_ISREG(value.st_mode) or value.st_uid != owner or value.st_nlink != 1:
            os.close(descriptor)
            raise ReviewContractError(
                "REVIEW_FILESYSTEM_UNSAFE",
                "Review writer lock is not an owner-controlled single-link regular file.",
                "Use a private local Review state directory.",
                cli_exit=2,
            )
        os.chmod(self.path, 0o600)
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._descriptor = descriptor
                return
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(descriptor)
                    raise ReviewContractError(
                        "REVIEW_STATE_LOCKED",
                        "Another process owns the Review session writer lock.",
                        "Wait for the active local Review server to exit and retry.",
                        http_status=409,
                        cli_exit=2,
                    )
                time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
            except OSError as exc:
                os.close(descriptor)
                raise ReviewContractError(
                    "REVIEW_FILESYSTEM_UNSAFE",
                    f"Could not acquire the local Review state lock: {exc}",
                    "Use an owner-controlled local filesystem.",
                    cli_exit=2,
                ) from exc

    def release(self) -> None:
        descriptor = self._descriptor
        if descriptor is None:
            return
        self._descriptor = None
        try:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def __enter__(self) -> ReviewStateLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        self.release()


def read_current_revision(state_dir: str | Path) -> ReviewRevision:
    """Read the durable current head without creating or mutating session state."""
    path = Path(state_dir) / "events.vrj"
    if not path.is_file() or path.is_symlink():
        raise ReviewContractError(
            "REVIEW_SESSION_NOT_FOUND",
            "Review session journal does not exist.",
            "Open the source with viewspec review before resuming it.",
            http_status=404,
        )
    records = ReviewJournal(path).records()
    if not records or records[0].get("type") != "session_initialized":
        raise _journal_invalid("Review session has no valid initialization record.")
    try:
        current = ReviewRevision.from_json(records[0].get("revision"))
        for record in records[1:]:
            if record.get("type") != "revision_promoted":
                continue
            candidate = ReviewRevision.from_json(record.get("revision"))
            if (
                candidate.number != current.number + 1
                or candidate.source_kind != current.source_kind
                or candidate.target != current.target
                or candidate.root_manifest_kind != current.root_manifest_kind
                or candidate.compiler_version != current.compiler_version
                or candidate.contract_profile != current.contract_profile
            ):
                raise _journal_invalid("Review journal contains an invalid promoted revision transition.")
            current = candidate
        return current
    except ReviewContractError as exc:
        if exc.code == "REVIEW_JOURNAL_INVALID":
            raise
        raise _journal_invalid(f"Review journal revision identity is invalid: {exc}") from exc


class ReviewJournal:
    """Length-delimited, SHA-256-framed append journal with fail-closed recovery."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._prepare_file()
        with bounded_review_phase("REVIEW_JOURNAL_INVALID", 2):
            self._records = self._read_records()

    def records(self) -> tuple[dict[str, Any], ...]:
        return self._records

    def append(self, record: Mapping[str, Any]) -> None:
        normalized = dict(record)
        frame = _journal_frame(normalized)
        try:
            old_size = self.path.stat().st_size
        except OSError as exc:
            raise ReviewContractError(
                "REVIEW_JOURNAL_WRITE_FAILED",
                f"Could not inspect the Review journal: {exc}",
                "Restore writable local state storage and retry.",
                http_status=507,
            ) from exc
        if old_size + len(frame) > MAX_JOURNAL_BYTES:
            raise ReviewContractError(
                "REVIEW_JOURNAL_FULL",
                f"Review journal would exceed {MAX_JOURNAL_BYTES} bytes.",
                "Compact or end the review session before accepting more feedback.",
                http_status=507,
            )
        if _session_tree_size(self.path.parent, stop_after=MAX_SESSION_STORAGE_BYTES) + len(frame) > MAX_SESSION_STORAGE_BYTES:
            raise ReviewContractError(
                "REVIEW_STORAGE_LIMIT_EXCEEDED",
                "Review session cannot reserve this journal frame within its 256 MiB budget.",
                "Purge old retained revisions or verification evidence before accepting more feedback.",
                http_status=507,
                cli_exit=2,
            )
        descriptor: int | None = None
        try:
            descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND)
            _write_all(descriptor, frame)
            os.fsync(descriptor)
        except OSError as exc:
            if descriptor is not None:
                try:
                    os.ftruncate(descriptor, old_size)
                    os.fsync(descriptor)
                except OSError as rollback_exc:
                    raise ReviewContractError(
                        "REVIEW_JOURNAL_INVALID",
                        f"Journal write and rollback both failed: {rollback_exc}",
                        "Stop using this session and inspect its private journal.",
                        http_status=500,
                    ) from exc
            raise ReviewContractError(
                "REVIEW_JOURNAL_WRITE_FAILED",
                f"Could not durably append the Review journal: {exc}",
                "Restore writable local state storage and retry.",
                http_status=507,
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
        self._records = (*self._records, normalized)

    @property
    def size(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError as exc:
            raise make_review_error("REVIEW_COMPACTION_FAILED", f"Could not inspect the active journal: {exc}") from exc

    def compact(self, records: tuple[dict[str, Any], ...], *, timeout_seconds: float = COMPACTION_TIMEOUT_SECONDS) -> None:
        """Atomically replace the journal only after a complete bounded rewrite is durable."""
        started = time.monotonic()
        frames: list[bytes] = []
        total = 0
        try:
            for record in records:
                frame = _journal_frame(record)
                total += len(frame)
                if total > MAX_JOURNAL_BYTES:
                    raise make_review_error("REVIEW_COMPACTION_FAILED", "Compacted journal still exceeds 16 MiB.")
                frames.append(frame)
                _assert_compaction_deadline(started, timeout_seconds)
        except ReviewContractError as exc:
            if exc.code == "REVIEW_COMPACTION_FAILED":
                raise
            raise make_review_error("REVIEW_COMPACTION_FAILED", f"Could not encode compacted journal: {exc.message}") from exc
        if _session_tree_size(self.path.parent, stop_after=MAX_SESSION_STORAGE_BYTES) + total > MAX_SESSION_STORAGE_BYTES:
            raise make_review_error(
                "REVIEW_COMPACTION_FAILED",
                "Review session cannot reserve the compacted journal within 256 MiB.",
            )

        temporary: Path | None = None
        descriptor: int | None = None
        try:
            for _ in range(3):
                candidate = self.path.with_name(f".{self.path.name}.compact-{secrets.token_hex(8)}")
                try:
                    descriptor = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except FileExistsError:
                    continue
                temporary = candidate
                break
            if descriptor is None or temporary is None:
                raise make_review_error("REVIEW_COMPACTION_FAILED", "Could not allocate a unique compaction file.")
            for frame in frames:
                _write_all(descriptor, frame)
                _assert_compaction_deadline(started, timeout_seconds)
            os.fsync(descriptor)
            _assert_compaction_deadline(started, timeout_seconds)
            os.close(descriptor)
            descriptor = None
            os.replace(temporary, self.path)
            temporary = None
            _fsync_directory(self.path.parent)
        except ReviewContractError:
            raise
        except OSError as exc:
            raise make_review_error("REVIEW_COMPACTION_FAILED", f"Could not durably compact the Review journal: {exc}") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self._records = records

    def _prepare_file(self) -> None:
        if self.path.exists():
            value = self.path.lstat()
            owner = os.geteuid() if hasattr(os, "geteuid") else value.st_uid
            if (
                stat.S_ISLNK(value.st_mode)
                or not stat.S_ISREG(value.st_mode)
                or value.st_uid != owner
                or value.st_nlink != 1
            ):
                raise ReviewContractError(
                    "REVIEW_FILESYSTEM_UNSAFE",
                    "Review journal must be a regular non-symlink file.",
                    "Choose a private local Review state directory.",
                )
            try:
                os.chmod(self.path, 0o600)
            except OSError as exc:
                raise ReviewContractError(
                    "REVIEW_FILESYSTEM_UNSAFE",
                    f"Could not secure the Review journal: {exc}",
                    "Choose a state directory owned by the current user.",
                ) from exc
            return
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            _fsync_directory(self.path.parent)
        except OSError as exc:
            raise ReviewContractError(
                "REVIEW_JOURNAL_WRITE_FAILED",
                f"Could not create the Review journal: {exc}",
                "Choose a writable private local Review state directory.",
                http_status=507,
            ) from exc

    def _read_records(self) -> tuple[dict[str, Any], ...]:
        try:
            size = self.path.stat().st_size
            if size > MAX_JOURNAL_BYTES:
                raise ReviewContractError(
                    "REVIEW_JOURNAL_INVALID",
                    f"Review journal exceeds {MAX_JOURNAL_BYTES} bytes.",
                    "Treat the session as corrupt; V0 does not salvage oversized journals.",
                    http_status=500,
                )
            content = self.path.read_bytes()
        except ReviewContractError:
            raise
        except OSError as exc:
            raise ReviewContractError(
                "REVIEW_JOURNAL_INVALID",
                f"Could not read the Review journal: {exc}",
                "Treat the session as corrupt and create a new review.",
                http_status=500,
            ) from exc
        records: list[dict[str, Any]] = []
        offset = 0
        while offset < len(content):
            if len(content) - offset < _FRAME_LENGTH.size:
                raise _journal_invalid("Journal ends inside a frame length.")
            (length,) = _FRAME_LENGTH.unpack_from(content, offset)
            offset += _FRAME_LENGTH.size
            if length > MAX_FRAME_BYTES:
                raise _journal_invalid(f"Journal frame declares {length} bytes.")
            frame_end = offset + length
            digest_end = frame_end + _FRAME_DIGEST_BYTES
            if digest_end > len(content):
                raise _journal_invalid("Journal ends inside a frame payload or digest.")
            payload = content[offset:frame_end]
            digest = content[frame_end:digest_end]
            if not hmac.compare_digest(hashlib.sha256(payload).digest(), digest):
                raise _journal_invalid("Journal frame SHA-256 does not match its payload.")
            try:
                record = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise _journal_invalid(f"Journal frame is not canonical UTF-8 JSON: {exc}") from exc
            if not isinstance(record, dict) or canonical_json_bytes(record) != payload:
                raise _journal_invalid("Journal frame is not a canonical JSON object.")
            records.append(record)
            offset = digest_end
        return tuple(records)


class ReviewSession:
    """One durable V0 session with at-least-once delivery to one consumer cursor."""

    def __init__(self, state_dir: str | Path, *, revision: ReviewRevision) -> None:
        if not isinstance(revision, ReviewRevision):
            raise TypeError("revision must be a ReviewRevision")
        self.state_dir = Path(state_dir)
        self._prepare_state_dir()
        self.journal = ReviewJournal(self.state_dir / "events.vrj")
        self.revision = revision
        self.review_id = ""
        self._events: list[ReviewEvent] = []
        self._idempotency: dict[str, tuple[str, ReviewEvent]] = {}
        self._cursor = 0
        self._outstanding_batch: ReviewBatch | None = None
        self._acknowledgements: dict[str, tuple[str, str | None, int]] = {}
        self._agent_replies: list[str] = []
        self._ended_by: str | None = None
        self._end_identity: tuple[str | None, str] | None = None
        self._acknowledged_since_compaction = 0
        self._compaction_failure: ReviewContractError | None = None
        self._pending_compaction_failure: ReviewContractError | None = None
        records = self.journal.records()
        if not records:
            self.review_id = new_review_id()
            self.journal.append(
                {
                    "type": "session_initialized",
                    "review_id": self.review_id,
                    "revision": self.revision.to_json(),
                }
            )
        else:
            self._recover(records)

    @property
    def events(self) -> tuple[ReviewEvent, ...]:
        return tuple(self._events)

    @property
    def cursor(self) -> int:
        return self._cursor

    @property
    def outstanding_batch(self) -> ReviewBatch | None:
        return self._outstanding_batch

    @property
    def agent_replies(self) -> tuple[str, ...]:
        return tuple(self._agent_replies)

    @property
    def ended_by(self) -> str | None:
        return self._ended_by

    @property
    def compaction_failure(self) -> dict[str, object] | None:
        return self._compaction_failure.to_json() if self._compaction_failure is not None else None

    def submit_event(
        self,
        *,
        idempotency_key: str,
        kind: str,
        body: str,
        target: ReviewTarget,
        context: ReviewContext,
    ) -> ReviewEvent:
        validate_idempotency_key(idempotency_key)
        request = {
            "kind": kind,
            "body": body,
            "target": target.to_json() if isinstance(target, ReviewTarget) else target,
            "context": context.to_json() if isinstance(context, ReviewContext) else context,
            "revision": self.revision.to_json(),
        }
        request_hash = canonical_json_sha256(request)
        previous = self._idempotency.get(idempotency_key)
        if previous is not None:
            if previous[0] == request_hash:
                return previous[1]
            raise ReviewContractError(
                "REVIEW_IDEMPOTENCY_CONFLICT",
                "Idempotency key was already used with different event bytes.",
                "Retry the original request unchanged or generate a new key for a new event.",
                http_status=409,
            )
        if self._ended_by is not None:
            raise ReviewContractError(
                "REVIEW_SESSION_ENDED",
                "The Review session has ended and cannot accept feedback.",
                "Explicitly reopen the review before submitting new feedback.",
                http_status=409,
            )
        if len(self._events) >= MAX_SESSION_EVENTS or len(self._events) - self._cursor >= MAX_UNACKNOWLEDGED_EVENTS:
            raise ReviewContractError(
                "REVIEW_EVENT_LIMIT_EXCEEDED",
                "The Review session event limit has been reached.",
                "Acknowledge pending feedback or end the bounded review session.",
                http_status=429,
            )
        event = ReviewEvent(
            event_id=_unique_generated_id({event.event_id for event in self._events}, new_event_id, "event"),
            sequence=len(self._events) + 1,
            actor="human",
            kind=kind,
            body=body,
            revision=self.revision,
            target=target,
            context=context,
        )
        record = {
            "type": "event",
            "idempotency_key": idempotency_key,
            "request_sha256": request_hash,
            "event": event.to_json(),
        }
        try:
            self.journal.append(record)
        except ReviewContractError as exc:
            if exc.code == "REVIEW_EVENT_TOO_LARGE":
                raise ReviewContractError(
                    "REVIEW_EVENT_TOO_LARGE",
                    "Persisted Review event exceeds the 24 KiB frame limit.",
                    "Reduce feedback or context before submitting the event.",
                    http_status=413,
                ) from exc
            raise
        self._events.append(event)
        self._idempotency[idempotency_key] = (request_hash, event)
        self._maybe_compact()
        return event

    def submit_event_and_end(
        self,
        *,
        idempotency_key: str,
        kind: str,
        body: str,
        target: ReviewTarget,
        context: ReviewContext,
    ) -> ReviewEvent:
        """Commit the human's final event and end marker in one idempotent journal frame."""
        validate_idempotency_key(idempotency_key)
        request = {
            "kind": kind,
            "body": body,
            "target": target.to_json() if isinstance(target, ReviewTarget) else target,
            "context": context.to_json() if isinstance(context, ReviewContext) else context,
            "revision": self.revision.to_json(),
            "end_actor": "human",
        }
        request_hash = canonical_json_sha256(request)
        previous = self._idempotency.get(idempotency_key)
        if previous is not None:
            if previous[0] == request_hash and self._end_identity == (idempotency_key, request_hash):
                return previous[1]
            raise ReviewContractError(
                "REVIEW_IDEMPOTENCY_CONFLICT",
                "Idempotency key was already used with different final-event bytes.",
                "Retry the identical Send & End transaction or use a new key before ending.",
                http_status=409,
            )
        if self._ended_by is not None:
            raise ReviewContractError(
                "REVIEW_SESSION_ENDED",
                "The Review session has already ended.",
                "Inspect the durable end attribution instead of submitting another final event.",
                http_status=409,
            )
        if len(self._events) >= MAX_SESSION_EVENTS or len(self._events) - self._cursor >= MAX_UNACKNOWLEDGED_EVENTS:
            raise ReviewContractError(
                "REVIEW_EVENT_LIMIT_EXCEEDED",
                "The Review session event limit has been reached.",
                "Acknowledge pending feedback before the reviewer sends and ends.",
                http_status=429,
            )
        event = ReviewEvent(
            event_id=_unique_generated_id({event.event_id for event in self._events}, new_event_id, "event"),
            sequence=len(self._events) + 1,
            actor="human",
            kind=kind,
            body=body,
            revision=self.revision,
            target=target,
            context=context,
        )
        record = {
            "type": "event_and_end",
            "actor": "human",
            "idempotency_key": idempotency_key,
            "request_sha256": request_hash,
            "event": event.to_json(),
        }
        try:
            self.journal.append(record)
        except ReviewContractError as exc:
            if exc.code == "REVIEW_EVENT_TOO_LARGE":
                raise ReviewContractError(
                    "REVIEW_EVENT_TOO_LARGE",
                    "Final Review event transaction exceeds the 24 KiB frame limit.",
                    "Reduce final feedback or context before Send & End.",
                    http_status=413,
                ) from exc
            raise
        self._events.append(event)
        self._idempotency[idempotency_key] = (request_hash, event)
        self._ended_by = "human"
        self._end_identity = (idempotency_key, request_hash)
        self._maybe_compact()
        return event

    def promote_revision(self, revision: ReviewRevision) -> None:
        """Advance the durable current head without altering identities on older events."""
        if not isinstance(revision, ReviewRevision):
            raise TypeError("revision must be a ReviewRevision")
        if self._ended_by is not None:
            raise ReviewContractError(
                "REVIEW_SESSION_ENDED",
                "The Review session has ended and cannot promote another revision.",
                "Explicitly reopen the review before rebuilding semantic source.",
                http_status=409,
            )
        self._assert_next_revision(revision)
        self.journal.append({"type": "revision_promoted", "revision": revision.to_json()})
        self.revision = revision
        self._maybe_compact()

    def poll(self, *, ack_batch_id: str | None = None, agent_reply: str | None = None) -> ReviewBatch | None:
        if self._pending_compaction_failure is not None:
            failure = self._pending_compaction_failure
            self._pending_compaction_failure = None
            raise failure
        if ack_batch_id is not None:
            self._acknowledge(ack_batch_id, agent_reply)
        elif agent_reply is not None:
            raise ReviewContractError(
                "REVIEW_ACK_REQUIRED",
                "An agent reply requires the exact outstanding batch acknowledgement.",
                "Pass the reply with --ack for the outstanding batch.",
                http_status=409,
            )
        if self._outstanding_batch is not None:
            return self._outstanding_batch
        available = tuple(event for event in self._events if event.sequence > self._cursor)
        if not available:
            return None
        revision = available[0].revision
        pending = tuple(event for event in available if event.revision == revision)[:MAX_BATCH_EVENTS]
        issued_ids = set(self._acknowledgements)
        if self._outstanding_batch is not None:
            issued_ids.add(self._outstanding_batch.batch_id)
        batch = ReviewBatch.create(
            pending,
            review_id=self.review_id,
            batch_id=_unique_generated_id(issued_ids, new_batch_id, "batch"),
        )
        self.journal.append({"type": "batch_issued", "batch": batch.to_json()})
        self._outstanding_batch = batch
        self._maybe_compact()
        return batch

    def end(self, *, actor: str, idempotency_key: str | None = None) -> None:
        if actor not in {"human", "agent"}:
            raise ReviewContractError(
                "REVIEW_EVENT_INVALID", "Review end actor must be human or agent.", "Use a supported ending actor.", http_status=422
            )
        if actor == "human":
            if idempotency_key is None:
                validate_idempotency_key("")
            validate_idempotency_key(idempotency_key)
        elif idempotency_key is not None:
            validate_idempotency_key(idempotency_key)
        request_hash = canonical_json_sha256({"actor": actor})
        if self._ended_by is not None:
            if self._end_identity == (idempotency_key, request_hash):
                return
            raise ReviewContractError(
                "REVIEW_SESSION_ENDED",
                "The Review session already ended with a different transaction.",
                "Inspect the durable end attribution before reopening.",
                http_status=409,
            )
        record = {
            "type": "end",
            "actor": actor,
            "idempotency_key": idempotency_key,
            "request_sha256": request_hash,
        }
        self.journal.append(record)
        self._ended_by = actor
        self._end_identity = (idempotency_key, request_hash)
        self._maybe_compact()

    def reopen(self) -> None:
        if self._ended_by is None:
            return
        previous_actor = self._ended_by
        self.journal.append(
            {
                "type": "session_reopened",
                "previous_actor": previous_actor,
                "final_sequence": len(self._events),
            }
        )
        self._ended_by = None
        self._end_identity = None
        self._maybe_compact()

    def _acknowledge(self, batch_id: str, reply: str | None) -> None:
        if reply is not None:
            if not isinstance(reply, str) or len(reply.encode("utf-8")) > MAX_BODY_BYTES:
                raise ReviewContractError(
                    "REVIEW_EVENT_TOO_LARGE",
                    f"Agent reply exceeds {MAX_BODY_BYTES} UTF-8 bytes.",
                    "Shorten the agent reply before acknowledging.",
                    http_status=413,
                )
        reply_hash = hashlib.sha256((reply or "").encode("utf-8")).hexdigest()
        prior = self._acknowledgements.get(batch_id)
        if prior is not None:
            if prior[0] != reply_hash or prior[1] != reply:
                raise ReviewContractError(
                    "REVIEW_ACK_INVALID",
                    "Acknowledged batch was retried with different reply bytes.",
                    "Retry the identical acknowledgement and reply transaction.",
                    http_status=409,
                )
            return
        if self._outstanding_batch is None:
            raise ReviewContractError(
                "REVIEW_ACK_OUT_OF_ORDER",
                "There is no outstanding Review batch to acknowledge.",
                "Poll for feedback and acknowledge only the returned batch.",
                http_status=409,
            )
        if batch_id != self._outstanding_batch.batch_id:
            raise ReviewContractError(
                "REVIEW_ACK_INVALID",
                "Acknowledgement does not name the exact outstanding Review batch.",
                "Use the batch_id returned by the current pending poll.",
                http_status=409,
            )
        cursor = self._outstanding_batch.last_sequence
        acknowledged_count = cursor - self._cursor
        record = {
            "type": "acknowledgement",
            "batch_id": batch_id,
            "reply": reply,
            "reply_sha256": reply_hash,
            "cursor": cursor,
        }
        self.journal.append(record)
        self._cursor = cursor
        if reply is not None:
            self._agent_replies.append(reply)
        self._acknowledgements[batch_id] = (reply_hash, reply, cursor)
        self._outstanding_batch = None
        self._acknowledged_since_compaction += acknowledged_count
        self._maybe_compact()

    def _recover(self, records: tuple[dict[str, Any], ...]) -> None:
        expected_revision = self.revision
        first = records[0]
        if first.get("type") != "session_initialized":
            raise _journal_invalid("First Review journal record is not session_initialized.")
        try:
            stored_revision = ReviewRevision.from_json(first.get("revision"))
            review_id = first.get("review_id")
            if not isinstance(review_id, str):
                raise ValueError("missing review id")
        except (ReviewContractError, ValueError, TypeError) as exc:
            raise _journal_invalid(f"Invalid session initialization: {exc}") from exc
        self.revision = stored_revision
        self.review_id = review_id
        outstanding: ReviewBatch | None = None
        for record in records[1:]:
            record_type = record.get("type")
            if record_type == "event":
                event = ReviewEvent.from_json(record.get("event"))
                key = record.get("idempotency_key")
                request_hash = record.get("request_sha256")
                if not isinstance(key, str) or not isinstance(request_hash, str):
                    raise _journal_invalid("Event record lacks idempotency identity.")
                if event.sequence != len(self._events) + 1 or key in self._idempotency or event.revision != self.revision:
                    raise _journal_invalid("Event sequence or idempotency identity is duplicated.")
                self._events.append(event)
                self._idempotency[key] = (request_hash, event)
            elif record_type == "event_and_end":
                event = ReviewEvent.from_json(record.get("event"))
                key = record.get("idempotency_key")
                request_hash = record.get("request_sha256")
                if (
                    record.get("actor") != "human"
                    or not isinstance(key, str)
                    or not isinstance(request_hash, str)
                    or self._ended_by is not None
                    or event.sequence != len(self._events) + 1
                    or key in self._idempotency
                    or event.revision != self.revision
                ):
                    raise _journal_invalid("Atomic final-event/end record is invalid.")
                self._events.append(event)
                self._idempotency[key] = (request_hash, event)
                self._ended_by = "human"
                self._end_identity = (key, request_hash)
            elif record_type == "revision_promoted":
                revision = ReviewRevision.from_json(record.get("revision"))
                self._assert_next_revision(revision, recovery=True)
                self.revision = revision
            elif record_type == "batch_issued":
                batch = ReviewBatch.from_json(record.get("batch"))
                if batch.review_id != self.review_id or batch.first_sequence != self._cursor + 1:
                    raise _journal_invalid("Issued batch does not match the Review delivery cursor.")
                outstanding = batch
            elif record_type == "acknowledgement":
                batch_id = record.get("batch_id")
                reply = record.get("reply")
                reply_hash = record.get("reply_sha256")
                cursor = record.get("cursor")
                if (
                    outstanding is None
                    or batch_id != outstanding.batch_id
                    or cursor != outstanding.last_sequence
                    or not isinstance(reply_hash, str)
                    or (reply is not None and not isinstance(reply, str))
                ):
                    raise _journal_invalid("Acknowledgement does not match the issued batch.")
                if hashlib.sha256((reply or "").encode("utf-8")).hexdigest() != reply_hash:
                    raise _journal_invalid("Acknowledgement reply SHA-256 does not match.")
                acknowledged_count = outstanding.last_sequence - self._cursor
                self._cursor = cursor
                if reply is not None:
                    self._agent_replies.append(reply)
                self._acknowledgements[batch_id] = (reply_hash, reply, cursor)
                outstanding = None
                self._acknowledged_since_compaction += acknowledged_count
            elif record_type == "acknowledgement_checkpoint":
                batch_id = record.get("batch_id")
                reply = record.get("reply")
                reply_hash = record.get("reply_sha256")
                cursor = record.get("cursor")
                if (
                    not isinstance(batch_id, str)
                    or batch_id in self._acknowledgements
                    or type(cursor) is not int
                    or not self._cursor < cursor <= len(self._events)
                    or not isinstance(reply_hash, str)
                    or (reply is not None and not isinstance(reply, str))
                    or hashlib.sha256((reply or "").encode("utf-8")).hexdigest() != reply_hash
                ):
                    raise _journal_invalid("Compacted acknowledgement checkpoint is invalid.")
                self._cursor = cursor
                if reply is not None:
                    self._agent_replies.append(reply)
                self._acknowledgements[batch_id] = (reply_hash, reply, cursor)
            elif record_type == "end":
                actor = record.get("actor")
                key = record.get("idempotency_key")
                request_hash = record.get("request_sha256")
                if actor not in {"human", "agent"} or not isinstance(request_hash, str) or self._ended_by is not None:
                    raise _journal_invalid("End record is malformed or duplicated.")
                self._ended_by = actor
                self._end_identity = (key if isinstance(key, str) else None, request_hash)
            elif record_type == "session_reopened":
                if (
                    self._ended_by is None
                    or record.get("previous_actor") != self._ended_by
                    or record.get("final_sequence") != len(self._events)
                ):
                    raise _journal_invalid("Session reopen does not match the durable end state.")
                self._ended_by = None
                self._end_identity = None
            elif record_type == "compaction_marker":
                if record != {
                    "type": "compaction_marker",
                    "cursor": self._cursor,
                    "event_count": len(self._events),
                }:
                    raise _journal_invalid("Review compaction marker does not match recovered state.")
                self._acknowledged_since_compaction = 0
            else:
                raise _journal_invalid(f"Unknown Review journal record type: {record_type!r}")
        self._outstanding_batch = outstanding
        if self.revision != expected_revision:
            raise ReviewContractError(
                "REVIEW_SESSION_CONFIGURATION_CONFLICT",
                "Stored Review current revision does not match the requested session head.",
                "Resume with the exact durable current revision or create an explicit new session.",
                http_status=409,
            )

    def _maybe_compact(self) -> None:
        if (
            self._acknowledged_since_compaction < COMPACTION_ACKNOWLEDGEMENTS
            and self.journal.size < COMPACTION_JOURNAL_BYTES
        ):
            return
        records = _compacted_journal_records(
            self.journal.records(),
            cursor=self._cursor,
            event_count=len(self._events),
        )
        try:
            self.journal.compact(records)
        except ReviewContractError as exc:
            failure = (
                exc
                if exc.code == "REVIEW_COMPACTION_FAILED"
                else make_review_error("REVIEW_COMPACTION_FAILED", f"Review journal compaction failed: {exc.message}")
            )
            self._compaction_failure = failure
            self._pending_compaction_failure = failure
            return
        self._acknowledged_since_compaction = 0
        self._compaction_failure = None
        self._pending_compaction_failure = None

    def _assert_next_revision(self, revision: ReviewRevision, *, recovery: bool = False) -> None:
        compatible = (
            revision.number == self.revision.number + 1
            and revision.source_kind == self.revision.source_kind
            and revision.target == self.revision.target
            and revision.root_manifest_kind == self.revision.root_manifest_kind
            and revision.compiler_version == self.revision.compiler_version
            and revision.contract_profile == self.revision.contract_profile
        )
        if compatible:
            return
        if recovery:
            raise _journal_invalid("Promoted revision is nonconsecutive or changes the session configuration.")
        raise ReviewContractError(
            "REVIEW_REVISION_IDENTITY_MISMATCH",
            "Promoted revision must be the next number under the same Review configuration.",
            "Build one consecutive revision without changing source kind, target, compiler, or contract profile.",
            http_status=500,
            cli_exit=1,
        )

    def _prepare_state_dir(self) -> None:
        if self.state_dir.exists():
            value = self.state_dir.lstat()
            owner = os.geteuid() if hasattr(os, "geteuid") else value.st_uid
            if stat.S_ISLNK(value.st_mode) or not stat.S_ISDIR(value.st_mode) or value.st_uid != owner:
                raise ReviewContractError(
                    "REVIEW_FILESYSTEM_UNSAFE",
                    "Review session state must be a private owner-controlled non-symlink directory.",
                    "Choose a private local Review state directory.",
                )
        try:
            self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self.state_dir, 0o700)
        except OSError as exc:
            raise ReviewContractError(
                "REVIEW_FILESYSTEM_UNSAFE",
                f"Could not secure the Review session directory: {exc}",
                "Choose a state directory owned by the current user.",
            ) from exc


def _journal_invalid(message: str) -> ReviewContractError:
    return ReviewContractError(
        "REVIEW_JOURNAL_INVALID",
        message,
        "Treat the Review session as corrupt; V0 does not truncate or salvage malformed journals.",
        http_status=500,
    )


def _session_tree_size(root: Path, *, stop_after: int) -> int:
    total = 0
    for path in root.rglob("*"):
        value = path.lstat()
        if stat.S_ISLNK(value.st_mode):
            raise ReviewContractError(
                "REVIEW_FILESYSTEM_UNSAFE",
                "Review session storage contains a symbolic link.",
                "Use only owner-controlled regular files and directories.",
                cli_exit=2,
            )
        if stat.S_ISREG(value.st_mode):
            total += value.st_size
            if total > stop_after:
                return total
    return total


def _journal_frame(record: Mapping[str, Any]) -> bytes:
    payload = canonical_json_bytes(dict(record))
    if len(payload) > MAX_FRAME_BYTES:
        raise ReviewContractError(
            "REVIEW_EVENT_TOO_LARGE",
            f"Journal record exceeds {MAX_FRAME_BYTES} bytes.",
            "Reduce the event or transaction before writing it.",
            http_status=413,
        )
    return _FRAME_LENGTH.pack(len(payload)) + payload + hashlib.sha256(payload).digest()


def _assert_compaction_deadline(started: float, timeout_seconds: float) -> None:
    if time.monotonic() - started > timeout_seconds:
        raise make_review_error(
            "REVIEW_COMPACTION_FAILED",
            f"Review journal compaction exceeded its {timeout_seconds:g}-second deadline.",
        )


def _compacted_journal_records(
    records: tuple[dict[str, Any], ...],
    *,
    cursor: int,
    event_count: int,
) -> tuple[dict[str, Any], ...]:
    acknowledged_batch_ids = {
        record.get("batch_id")
        for record in records
        if record.get("type") == "acknowledgement" and isinstance(record.get("batch_id"), str)
    }
    compacted: list[dict[str, Any]] = []
    for record in records:
        record_type = record.get("type")
        if record_type == "compaction_marker":
            continue
        if record_type == "batch_issued":
            batch = record.get("batch")
            batch_id = batch.get("batch_id") if isinstance(batch, dict) else None
            if batch_id in acknowledged_batch_ids:
                continue
        if record_type == "acknowledgement":
            compacted.append(
                {
                    "type": "acknowledgement_checkpoint",
                    "batch_id": record.get("batch_id"),
                    "reply": record.get("reply"),
                    "reply_sha256": record.get("reply_sha256"),
                    "cursor": record.get("cursor"),
                }
            )
            continue
        compacted.append(dict(record))
    compacted.append({"type": "compaction_marker", "cursor": cursor, "event_count": event_count})
    return tuple(compacted)


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(descriptor, content[offset:])
        if written <= 0:
            raise OSError("journal write made no progress")
        offset += written


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _unique_generated_id(existing: set[str], generator: Any, kind: str) -> str:
    for _ in range(3):
        candidate = generator()
        if candidate not in existing:
            return candidate
    raise ReviewContractError(
        "REVIEW_ENTROPY_UNAVAILABLE",
        f"Could not allocate a unique Review {kind} id after 3 attempts.",
        "Stop the request without emitting a predictable identifier and retry with healthy entropy.",
        http_status=500,
        cli_exit=1,
    )


__all__ = [
    "MAX_FRAME_BYTES",
    "MAX_JOURNAL_BYTES",
    "ReviewJournal",
    "ReviewSession",
    "ReviewStateLock",
    "read_current_revision",
]
