"""Durable, provider-independent trust core for private Studio review.

This module deliberately does not open a socket, upload a package, or construct a
public URL.  It implements the state machine an authorized HTTPS adapter must use:
verified immutable session creation, one-time capability exchange, bounded reads,
source-bound reviewer comments, owner-only approval, and reversible lifecycle.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import sqlite3
import stat
import tempfile
import time

from viewspec.review_contract import canonical_json_bytes
from viewspec.studio_review_staging import plan_staging, prepare_staging, remove_staging
from viewspec.studio_share import (
    STUDIO_SHARE_ARCHIVE_MAX_BYTES,
    STUDIO_SHARE_MAX_BYTES,
    STUDIO_SHARE_MAX_FILES,
    StudioShareError,
    load_studio_share_package,
    materialize_studio_share_archive,
)


STUDIO_REVIEW_SERVICE_SCHEMA_VERSION = 1
STUDIO_REVIEW_VIEWPORTS = (390, 768, 1440)
STUDIO_REVIEW_COMMENT_MAX_BYTES = 4 * 1024
STUDIO_REVIEW_MAX_EXPIRY_SECONDS = 30 * 24 * 60 * 60
STUDIO_REVIEW_MIN_EXPIRY_SECONDS = 5 * 60
STUDIO_REVIEW_COOKIE_TTL_SECONDS = 12 * 60 * 60
STUDIO_REVIEW_MAX_CPU_SECONDS = 30
STUDIO_REVIEW_MAX_MEMORY_BYTES = 512 * 1024 * 1024
STUDIO_REVIEW_MAX_WALL_SECONDS = 120

_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$")
_SESSION_ID_RE = re.compile(r"^vsr_[A-Za-z0-9_-]{24}$")
_SAFE_ARTIFACT_RE = re.compile(r"^[A-Za-z0-9._/-]{1,512}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMENT_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

VerificationRunner = Callable[[Path, dict[str, object]], dict[str, object]]
Clock = Callable[[], float]
TokenBytes = Callable[[int], bytes]


class StudioReviewServiceError(ValueError):
    """Stable fail-closed service-boundary error."""

    def __init__(
        self,
        code: str,
        message: str,
        fix: str,
        *,
        http_status: int = 422,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.fix = fix
        self.http_status = http_status

    def to_json(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "fix": self.fix,
            "http_status": self.http_status,
        }


@dataclass(frozen=True, slots=True)
class HostedReviewArtifact:
    """One exact allowlisted checked artifact response."""

    path: str
    media_type: str
    sha256: str
    content: bytes


class StudioReviewService:
    """Durable private-review domain service with no transport assumptions."""

    def __init__(
        self,
        root: str | Path,
        *,
        signing_key: bytes,
        verifier: VerificationRunner,
        key_id: str = "studio-review-v1",
        receipt_signing_key: bytes | None = None,
        receipt_verification_keys: Mapping[str, bytes] | None = None,
        reconcile_on_startup: bool = True,
        clock: Clock = time.time,
        token_bytes: TokenBytes = secrets.token_bytes,
    ) -> None:
        if not isinstance(signing_key, bytes) or len(signing_key) < 32:
            raise ValueError("Studio review signing_key must contain at least 32 bytes.")
        if not callable(verifier):
            raise ValueError("Studio review verifier must be callable.")
        if not isinstance(key_id, str) or not 1 <= len(key_id) <= 64:
            raise ValueError("Studio review key_id must contain 1 through 64 characters.")
        if type(reconcile_on_startup) is not bool:
            raise ValueError("Studio review reconcile_on_startup must be a boolean.")
        active_receipt_key = signing_key if receipt_signing_key is None else receipt_signing_key
        if not isinstance(active_receipt_key, bytes) or len(active_receipt_key) < 32:
            raise ValueError("Studio review receipt_signing_key must contain at least 32 bytes.")
        receipt_keys: dict[str, bytes] = {}
        if receipt_verification_keys is not None:
            if not isinstance(receipt_verification_keys, Mapping):
                raise ValueError("Studio review receipt_verification_keys must be a mapping.")
            for receipt_key_id, receipt_key in receipt_verification_keys.items():
                if not isinstance(receipt_key_id, str) or not 1 <= len(receipt_key_id) <= 64:
                    raise ValueError("Studio review receipt key ids must contain 1 through 64 characters.")
                if not isinstance(receipt_key, bytes) or len(receipt_key) < 32:
                    raise ValueError("Studio review receipt verification keys must contain at least 32 bytes.")
                receipt_keys[receipt_key_id] = receipt_key
        existing_active = receipt_keys.get(key_id)
        if existing_active is not None and not hmac.compare_digest(existing_active, active_receipt_key):
            raise ValueError("Studio review active receipt key conflicts with the verification keyring.")
        receipt_keys[key_id] = active_receipt_key
        self.root = Path(root).resolve()
        self.objects = self.root / "objects"
        self.ingress = self.root / "ingress"
        self.database = self.root / "service.sqlite3"
        self._signing_key = signing_key
        self._receipt_signing_key = active_receipt_key
        self._receipt_verification_keys = receipt_keys
        self._verifier = verifier
        self._key_id = key_id
        self._clock = clock
        self._token_bytes = token_bytes
        self._prepare_storage(reconcile_on_startup=reconcile_on_startup)

    def create_session_from_archive(
        self,
        archive_path: str | Path,
        *,
        disclosure_accepted: bool,
        expires_in_seconds: int,
        idempotency_key: str,
    ) -> dict[str, object]:
        """Strictly materialize one remote ingress body, then create its session."""

        try:
            archive_sha256, archive_bytes = _bounded_file_sha256(
                Path(archive_path),
                maximum=STUDIO_SHARE_ARCHIVE_MAX_BYTES,
            )
            with tempfile.TemporaryDirectory(prefix=".ingress-", dir=self.ingress) as directory:
                package = materialize_studio_share_archive(archive_path, directory)
                created = self.create_session(
                    package,
                    disclosure_accepted=disclosure_accepted,
                    expires_in_seconds=expires_in_seconds,
                    idempotency_key=idempotency_key,
                )
                return {
                    **created,
                    "ingress": {
                        "archive_sha256": archive_sha256,
                        "archive_bytes": archive_bytes,
                    },
                }
        except StudioShareError as exc:
            raise StudioReviewServiceError(
                "STUDIO_REVIEW_PACKAGE_INVALID",
                "The private-review upload body failed strict ingress validation.",
                "Rebuild the deterministic .vsreview archive from the checked Studio revision.",
            ) from exc

    def create_session(
        self,
        package_dir: str | Path,
        *,
        disclosure_accepted: bool,
        expires_in_seconds: int,
        idempotency_key: str,
    ) -> dict[str, object]:
        """Create one private session only after exact package and sandbox verification."""

        key = _idempotency_key(idempotency_key)
        if disclosure_accepted is not True:
            raise StudioReviewServiceError(
                "STUDIO_REVIEW_DISCLOSURE_REQUIRED",
                "Private review creation requires explicit acceptance of the prepared disclosure.",
                "Show the exact share-disclosure.md and obtain a deliberate confirmation.",
            )
        if type(expires_in_seconds) is not int or not (
            STUDIO_REVIEW_MIN_EXPIRY_SECONDS <= expires_in_seconds <= STUDIO_REVIEW_MAX_EXPIRY_SECONDS
        ):
            raise _expiry_invalid()
        try:
            envelope = load_studio_share_package(package_dir)
        except (OSError, ValueError) as exc:
            raise StudioReviewServiceError(
                "STUDIO_REVIEW_PACKAGE_INVALID",
                "The prepared private-review package failed exact revalidation.",
                "Prepare a fresh package from the checked Studio comparison.",
            ) from exc
        package = Path(package_dir).resolve()
        package_id = str(envelope["package_id"])
        request_sha256 = _canonical_sha256(
            {
                "package_id": package_id,
                "disclosure_accepted": True,
                "expires_in_seconds": expires_in_seconds,
            }
        )
        replay = self._idempotency_lookup("global", "create", key, request_sha256)
        if replay is not None:
            return self._create_response_with_capabilities(replay)

        session_id = self._new_session_id()
        candidate_outer = self.objects / f".candidate-{session_id}"
        candidate_package = candidate_outer / package_id
        final_outer = self.objects / session_id
        try:
            shutil.copytree(package, candidate_package, symlinks=True)
            copied = load_studio_share_package(candidate_package)
            if copied != envelope:
                raise StudioReviewServiceError(
                    "STUDIO_REVIEW_PACKAGE_INVALID",
                    "The package changed while the service copied it.",
                    "Stop concurrent edits and prepare a fresh package.",
                )
            _seal_private_tree(candidate_outer)
            try:
                verification = self._verifier(candidate_package, copied)
            except Exception as exc:
                raise _verification_failed() from exc
            verification = _validated_verification(verification, envelope=copied)
            projection = _build_projection(candidate_package, copied)
            now = self._now()
            expires_at = now + expires_in_seconds
            owner_nonce = self._new_nonce()
            reviewer_nonce = self._new_nonce()
            revision_sha256 = _canonical_sha256(copied["revision"])
            receipt = self._signed_receipt(
                "session_created",
                session_id,
                {
                    "package_id": package_id,
                    "revision_identity_sha256": revision_sha256,
                    "verification_sha256": _canonical_sha256(verification),
                    "expires_at": expires_at,
                },
                issued_at=now,
            )
            safe_response = {
                "schema_version": STUDIO_REVIEW_SERVICE_SCHEMA_VERSION,
                "status": "active",
                "session": {
                    "id": session_id,
                    "package_id": package_id,
                    "revision_identity_sha256": revision_sha256,
                    "created_at": now,
                    "expires_at": expires_at,
                    "private": True,
                },
                "verification": verification,
                "receipt": receipt,
                "response_policy": _response_policy(),
            }
            os.rename(candidate_outer, final_outer)
            try:
                with self._connect() as database:
                    database.execute("BEGIN IMMEDIATE")
                    existing = self._idempotency_lookup_db(database, "global", "create", key, request_sha256)
                    if existing is not None:
                        database.rollback()
                        shutil.rmtree(final_outer)
                        return self._create_response_with_capabilities(existing)
                    database.execute(
                        """
                        INSERT INTO sessions (
                            session_id, package_id, object_relpath, revision_sha256, revision_json,
                            projection_json, verification_json, receipt_json, status, created_at,
                            expires_at, reviewer_generation, owner_nonce, reviewer_nonce
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, 1, ?, ?)
                        """,
                        (
                            session_id,
                            package_id,
                            f"objects/{session_id}/{package_id}",
                            revision_sha256,
                            _json_text(copied["revision"]),
                            _json_text(projection),
                            _json_text(verification),
                            _json_text(receipt),
                            now,
                            expires_at,
                            owner_nonce,
                            reviewer_nonce,
                        ),
                    )
                    self._insert_capability(
                        database,
                        session_id=session_id,
                        role="owner",
                        generation=0,
                        nonce=owner_nonce,
                        created_at=now,
                        expires_at=expires_at,
                    )
                    self._insert_capability(
                        database,
                        session_id=session_id,
                        role="reviewer",
                        generation=1,
                        nonce=reviewer_nonce,
                        created_at=now,
                        expires_at=expires_at,
                    )
                    self._idempotency_store_db(
                        database,
                        "global",
                        "create",
                        key,
                        request_sha256,
                        safe_response,
                        now,
                    )
                    self._audit(database, session_id, "verification_passed", {}, now)
                    self._audit(database, session_id, "session_created", {"receipt_id": receipt["receipt_id"]}, now)
                    database.commit()
            except Exception:
                if final_outer.exists():
                    shutil.rmtree(final_outer)
                raise
            return self._create_response_with_capabilities(safe_response)
        except StudioReviewServiceError:
            if candidate_outer.exists():
                shutil.rmtree(candidate_outer)
            raise
        except Exception as exc:
            if candidate_outer.exists():
                shutil.rmtree(candidate_outer)
            raise StudioReviewServiceError(
                "STUDIO_REVIEW_STORAGE_FAILED",
                "Private review creation did not commit a complete session.",
                "Retry with the same idempotency key; no partial session is usable.",
                http_status=500,
            ) from exc

    def exchange_capability(self, session_id: str, capability: str) -> dict[str, object]:
        """Consume one fragment capability and mint one scoped browser-session cookie."""

        if not isinstance(session_id, str) or _SESSION_ID_RE.fullmatch(session_id) is None:
            raise _access_denied()
        capability_hash = _token_sha256(capability)
        now = self._now()
        cookie = self._new_token("vss_", 32)
        cookie_hash = _token_sha256(cookie)
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute(
                """
                SELECT c.session_id, c.role, c.generation, c.consumed_at, c.revoked_at,
                       c.expires_at AS capability_expires_at, s.status, s.expires_at,
                       s.reviewer_generation
                FROM capabilities c JOIN sessions s ON s.session_id = c.session_id
                WHERE c.capability_hash = ? AND c.session_id = ?
                """,
                (capability_hash, session_id),
            ).fetchone()
            if (
                row is None
                or row["consumed_at"] is not None
                or row["revoked_at"] is not None
                or row["status"] != "active"
                or int(row["capability_expires_at"]) <= now
                or int(row["expires_at"]) <= now
                or (row["role"] == "reviewer" and int(row["generation"]) != int(row["reviewer_generation"]))
            ):
                database.rollback()
                raise _access_denied()
            changed = database.execute(
                "UPDATE capabilities SET consumed_at = ? WHERE capability_hash = ? AND consumed_at IS NULL",
                (now, capability_hash),
            ).rowcount
            if changed != 1:
                database.rollback()
                raise _access_denied()
            cookie_expires_at = min(int(row["expires_at"]), now + STUDIO_REVIEW_COOKIE_TTL_SECONDS)
            database.execute(
                """
                INSERT INTO browser_sessions
                    (cookie_hash, session_id, role, created_at, expires_at, revoked_at)
                VALUES (?, ?, ?, ?, ?, NULL)
                """,
                (cookie_hash, session_id, row["role"], now, cookie_expires_at),
            )
            self._audit(database, session_id, "capability_exchanged", {"role": row["role"]}, now)
            database.commit()
        return {
            "schema_version": STUDIO_REVIEW_SERVICE_SCHEMA_VERSION,
            "status": "exchanged",
            "role": row["role"],
            "cookie_value": cookie,
            "cookie_policy": {
                "secure": True,
                "http_only": True,
                "same_site": "Strict",
                "path": f"/review/{session_id}/",
                "expires_at": cookie_expires_at,
                "max_age": cookie_expires_at - now,
            },
            "history_action": "remove_fragment_immediately",
        }

    def read_revision(self, browser_session: str) -> dict[str, object]:
        """Read the immutable review projection and acknowledged comments."""

        now = self._now()
        with self._connect() as database:
            auth = self._authorize(database, browser_session, required_role=None, now=now)
            comments = [
                json.loads(row["comment_json"])
                for row in database.execute(
                    "SELECT comment_json FROM comments WHERE session_id = ? ORDER BY created_at, comment_id",
                    (auth["session_id"],),
                ).fetchall()
            ]
            approval = database.execute(
                "SELECT approval_json FROM approvals WHERE session_id = ? ORDER BY created_at LIMIT 1",
                (auth["session_id"],),
            ).fetchone()
        projection = json.loads(auth["projection_json"])
        return {
            "schema_version": STUDIO_REVIEW_SERVICE_SCHEMA_VERSION,
            "status": "active",
            "role": auth["role"],
            "session": {
                "id": auth["session_id"],
                "revision_identity_sha256": auth["revision_sha256"],
                "expires_at": auth["session_expires_at"],
                "private": True,
            },
            "revision": projection["revision"],
            "routes": projection["routes"],
            "screens": projection["screens"],
            "inspection": projection["inspection"],
            "artifacts": projection["artifacts"],
            "comments": comments,
            "approval": json.loads(approval["approval_json"]) if approval is not None else None,
            "response_policy": _response_policy(),
        }

    def read_artifact(self, browser_session: str, artifact_path: str) -> HostedReviewArtifact:
        """Read one exact checked artifact; source, design, and reference files are never served."""

        safe_path = _artifact_path(artifact_path)
        with self._connect() as database:
            auth = self._authorize(database, browser_session, required_role=None, now=self._now())
        projection = json.loads(auth["projection_json"])
        metadata = projection["artifact_index"].get(safe_path)
        if not isinstance(metadata, dict):
            raise StudioReviewServiceError(
                "STUDIO_REVIEW_ARTIFACT_FORBIDDEN",
                "The requested path is not an allowlisted checked review artifact.",
                "Request a path from the current revision artifact inventory.",
                http_status=404,
            )
        package = self.root / auth["object_relpath"]
        path = package / "payload" / safe_path
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise _storage_failed() from exc
        if len(content) != metadata["bytes"] or hashlib.sha256(content).hexdigest() != metadata["sha256"]:
            raise _storage_failed()
        return HostedReviewArtifact(
            path=safe_path,
            media_type=str(metadata["media_type"]),
            sha256=str(metadata["sha256"]),
            content=content,
        )

    def append_comment(
        self,
        browser_session: str,
        *,
        body: str,
        context: Mapping[str, object],
        idempotency_key: str,
    ) -> dict[str, object]:
        """Append one bounded, server-revalidated reviewer comment."""

        key = _idempotency_key(idempotency_key)
        normalized_body = _comment_body(body)
        now = self._now()
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            auth = self._authorize(database, browser_session, required_role="reviewer", now=now)
            projection = json.loads(auth["projection_json"])
            checked_context = _checked_comment_context(
                context,
                projection=projection,
                revision_sha256=auth["revision_sha256"],
            )
            request_sha256 = _canonical_sha256({"body": normalized_body, "context": checked_context})
            replay = self._idempotency_lookup_db(
                database,
                auth["session_id"],
                "comment",
                key,
                request_sha256,
            )
            if replay is not None:
                database.rollback()
                return replay
            comment_id = "vcm_" + self._stable_token(
                b"comment\x00" + auth["session_id"].encode() + b"\x00" + key.encode() + b"\x00" + request_sha256.encode(),
                length=18,
            )
            comment = {
                "id": comment_id,
                "revision_identity_sha256": auth["revision_sha256"],
                "body": normalized_body,
                "context": checked_context,
                "created_at": now,
                "acknowledged": True,
            }
            response = {
                "schema_version": STUDIO_REVIEW_SERVICE_SCHEMA_VERSION,
                "status": "acknowledged",
                "comment": comment,
            }
            database.execute(
                """
                INSERT INTO comments
                    (comment_id, session_id, idempotency_key, request_sha256, comment_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (comment_id, auth["session_id"], key, request_sha256, _json_text(comment), now),
            )
            self._idempotency_store_db(
                database,
                auth["session_id"],
                "comment",
                key,
                request_sha256,
                response,
                now,
            )
            self._audit(
                database,
                auth["session_id"],
                "comment_acknowledged",
                {"comment_id": comment_id, "context_sha256": _canonical_sha256(checked_context)},
                now,
            )
            database.commit()
        return response

    def approve_revision(
        self,
        browser_session: str,
        *,
        revision_identity_sha256: str,
        idempotency_key: str,
    ) -> dict[str, object]:
        """Record owner approval of the exact immutable revision; never mutate source."""

        key = _idempotency_key(idempotency_key)
        now = self._now()
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            auth = self._authorize(database, browser_session, required_role="owner", now=now)
            if revision_identity_sha256 != auth["revision_sha256"]:
                database.rollback()
                raise StudioReviewServiceError(
                    "STUDIO_REVIEW_REVISION_MISMATCH",
                    "Owner approval does not name the exact current immutable review revision.",
                    "Reload the session and approve its exact revision identity.",
                    http_status=409,
                )
            request_sha256 = _canonical_sha256({"revision_identity_sha256": revision_identity_sha256})
            replay = self._idempotency_lookup_db(
                database,
                auth["session_id"],
                "approve",
                key,
                request_sha256,
            )
            if replay is not None:
                database.rollback()
                return replay
            prior = database.execute(
                "SELECT approval_id FROM approvals WHERE session_id = ?",
                (auth["session_id"],),
            ).fetchone()
            if prior is not None:
                database.rollback()
                raise StudioReviewServiceError(
                    "STUDIO_REVIEW_ALREADY_APPROVED",
                    "This immutable review revision already has an owner approval.",
                    "Use the existing approval receipt or create a new checked revision.",
                    http_status=409,
                )
            approval_id = "vap_" + self._stable_token(
                b"approval\x00" + auth["session_id"].encode() + b"\x00" + key.encode(),
                length=18,
            )
            receipt = self._signed_receipt(
                "revision_approved",
                auth["session_id"],
                {"approval_id": approval_id, "revision_identity_sha256": revision_identity_sha256},
                issued_at=now,
            )
            approval = {
                "id": approval_id,
                "revision_identity_sha256": revision_identity_sha256,
                "approved_at": now,
                "role": "owner",
                "receipt": receipt,
            }
            response = {
                "schema_version": STUDIO_REVIEW_SERVICE_SCHEMA_VERSION,
                "status": "approved",
                "approval": approval,
            }
            database.execute(
                """
                INSERT INTO approvals
                    (approval_id, session_id, idempotency_key, request_sha256, approval_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (approval_id, auth["session_id"], key, request_sha256, _json_text(approval), now),
            )
            self._idempotency_store_db(
                database,
                auth["session_id"],
                "approve",
                key,
                request_sha256,
                response,
                now,
            )
            self._audit(
                database,
                auth["session_id"],
                "revision_approved",
                {"approval_id": approval_id, "receipt_id": receipt["receipt_id"]},
                now,
            )
            database.commit()
        return response

    def rotate_reviewer(self, browser_session: str, *, idempotency_key: str) -> dict[str, object]:
        """Invalidate reviewer access and return one new fragment capability."""

        key = _idempotency_key(idempotency_key)
        request_sha256 = _canonical_sha256({"action": "rotate_reviewer"})
        now = self._now()
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            auth = self._authorize(database, browser_session, required_role="owner", now=now)
            replay = self._idempotency_lookup_db(
                database,
                auth["session_id"],
                "rotate_reviewer",
                key,
                request_sha256,
            )
            if replay is not None:
                database.rollback()
                return self._rotation_response_with_capability(replay)
            generation = int(auth["reviewer_generation"]) + 1
            nonce = self._new_nonce()
            database.execute(
                "UPDATE capabilities SET revoked_at = ? WHERE session_id = ? AND role = 'reviewer' AND revoked_at IS NULL",
                (now, auth["session_id"]),
            )
            database.execute(
                "UPDATE browser_sessions SET revoked_at = ? WHERE session_id = ? AND role = 'reviewer' AND revoked_at IS NULL",
                (now, auth["session_id"]),
            )
            database.execute(
                "UPDATE sessions SET reviewer_generation = ?, reviewer_nonce = ? WHERE session_id = ?",
                (generation, nonce, auth["session_id"]),
            )
            self._insert_capability(
                database,
                session_id=auth["session_id"],
                role="reviewer",
                generation=generation,
                nonce=nonce,
                created_at=now,
                expires_at=int(auth["session_expires_at"]),
            )
            safe_response = {
                "schema_version": STUDIO_REVIEW_SERVICE_SCHEMA_VERSION,
                "status": "rotated",
                "session_id": auth["session_id"],
                "reviewer_generation": generation,
                "expires_at": int(auth["session_expires_at"]),
            }
            self._idempotency_store_db(
                database,
                auth["session_id"],
                "rotate_reviewer",
                key,
                request_sha256,
                safe_response,
                now,
            )
            self._audit(database, auth["session_id"], "reviewer_rotated", {"generation": generation}, now)
            database.commit()
        return self._rotation_response_with_capability(safe_response)

    def shorten_expiry(
        self,
        browser_session: str,
        *,
        expires_at: int,
        idempotency_key: str,
    ) -> dict[str, object]:
        """Shorten, but never extend, the private session lifetime."""

        key = _idempotency_key(idempotency_key)
        now = self._now()
        if type(expires_at) is not int or expires_at < now + 60:
            raise _expiry_invalid()
        request_sha256 = _canonical_sha256({"expires_at": expires_at})
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            auth = self._authorize(database, browser_session, required_role="owner", now=now)
            replay = self._idempotency_lookup_db(
                database,
                auth["session_id"],
                "shorten_expiry",
                key,
                request_sha256,
            )
            if replay is not None:
                database.rollback()
                return replay
            if expires_at >= int(auth["session_expires_at"]):
                database.rollback()
                raise _expiry_invalid()
            database.execute("UPDATE sessions SET expires_at = ? WHERE session_id = ?", (expires_at, auth["session_id"]))
            database.execute(
                "UPDATE capabilities SET expires_at = MIN(expires_at, ?) WHERE session_id = ?",
                (expires_at, auth["session_id"]),
            )
            database.execute(
                "UPDATE browser_sessions SET expires_at = MIN(expires_at, ?) WHERE session_id = ?",
                (expires_at, auth["session_id"]),
            )
            receipt = self._signed_receipt(
                "expiry_shortened",
                auth["session_id"],
                {"expires_at": expires_at},
                issued_at=now,
            )
            response = {
                "schema_version": STUDIO_REVIEW_SERVICE_SCHEMA_VERSION,
                "status": "active",
                "expires_at": expires_at,
                "receipt": receipt,
            }
            self._idempotency_store_db(
                database,
                auth["session_id"],
                "shorten_expiry",
                key,
                request_sha256,
                response,
                now,
            )
            self._audit(database, auth["session_id"], "expiry_shortened", {"expires_at": expires_at}, now)
            database.commit()
        return response

    def revoke(self, browser_session: str, *, idempotency_key: str) -> dict[str, object]:
        """Immediately revoke every capability and browser session."""

        key = _idempotency_key(idempotency_key)
        request_sha256 = _canonical_sha256({"action": "revoke"})
        now = self._now()
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            replay = self._terminal_replay(database, browser_session, "revoke", key, request_sha256)
            if replay is not None:
                database.rollback()
                return replay
            auth = self._authorize(database, browser_session, required_role="owner", now=now)
            receipt = self._signed_receipt("session_revoked", auth["session_id"], {}, issued_at=now)
            response = {
                "schema_version": STUDIO_REVIEW_SERVICE_SCHEMA_VERSION,
                "status": "revoked",
                "receipt": receipt,
            }
            self._idempotency_store_db(
                database,
                auth["session_id"],
                "revoke",
                key,
                request_sha256,
                response,
                now,
            )
            self._audit(database, auth["session_id"], "session_revoked", {"receipt_id": receipt["receipt_id"]}, now)
            database.execute("UPDATE sessions SET status = 'revoked' WHERE session_id = ?", (auth["session_id"],))
            database.execute(
                "UPDATE capabilities SET revoked_at = ? WHERE session_id = ? AND revoked_at IS NULL",
                (now, auth["session_id"]),
            )
            database.execute(
                "UPDATE browser_sessions SET revoked_at = ? WHERE session_id = ? AND revoked_at IS NULL",
                (now, auth["session_id"]),
            )
            database.commit()
        return response

    def delete(self, browser_session: str, *, idempotency_key: str) -> dict[str, object]:
        """Delete the retained package and make every access path fail closed."""

        key = _idempotency_key(idempotency_key)
        request_sha256 = _canonical_sha256({"action": "delete"})
        now = self._now()
        tombstone: Path | None = None
        original: Path | None = None
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            replay = self._terminal_replay(database, browser_session, "delete", key, request_sha256)
            if replay is not None:
                database.rollback()
                self._finish_delete_cleanup(str(replay["session_id"]))
                return replay
            auth = self._authorize(database, browser_session, required_role="owner", now=now)
            session_id = str(auth["session_id"])
            original = self.objects / session_id
            tombstone = self.objects / f".deleted-{session_id}"
            if tombstone.exists() or not original.is_dir():
                database.rollback()
                raise _storage_failed()
            os.rename(original, tombstone)
            receipt = self._signed_receipt("session_deleted", session_id, {}, issued_at=now)
            response = {
                "schema_version": STUDIO_REVIEW_SERVICE_SCHEMA_VERSION,
                "status": "deleted",
                "session_id": session_id,
                "receipt": receipt,
            }
            try:
                self._idempotency_store_db(
                    database,
                    session_id,
                    "delete",
                    key,
                    request_sha256,
                    response,
                    now,
                )
                self._audit(database, session_id, "session_deleted", {"receipt_id": receipt["receipt_id"]}, now)
                database.execute("UPDATE sessions SET status = 'deleted' WHERE session_id = ?", (session_id,))
                database.execute(
                    "UPDATE capabilities SET revoked_at = ? WHERE session_id = ? AND revoked_at IS NULL",
                    (now, session_id),
                )
                database.execute(
                    "UPDATE browser_sessions SET revoked_at = ? WHERE session_id = ? AND revoked_at IS NULL",
                    (now, session_id),
                )
                database.commit()
            except Exception:
                database.rollback()
                if tombstone.exists() and original is not None:
                    os.rename(tombstone, original)
                raise
        try:
            shutil.rmtree(tombstone)
        except OSError as exc:
            raise _storage_failed() from exc
        return response

    def reconcile_storage(self, *, dry_run: bool, limit: int = 100) -> dict[str, object]:
        """Repair only mechanically provable interrupted filesystem transitions."""

        if type(dry_run) is not bool:
            raise ValueError("Studio review reconciliation dry_run must be a boolean.")
        batch_limit = _maintenance_limit(limit)
        now = self._now()
        try:
            staging = plan_staging(self.root)
        except (OSError, ValueError) as exc:
            raise _storage_failed() from exc
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            sessions = {
                str(row["session_id"]): str(row["status"])
                for row in database.execute("SELECT session_id, status FROM sessions").fetchall()
            }
            try:
                entries = sorted(self.objects.iterdir(), key=lambda path: path.name)
            except OSError as exc:
                database.rollback()
                raise _storage_failed() from exc
            entry_names = {path.name for path in entries}
            planned: list[tuple[str, Path, Path | None]] = []
            for path in entries:
                try:
                    value = path.lstat()
                except OSError as exc:
                    database.rollback()
                    raise _storage_failed() from exc
                if path.is_symlink() or not stat.S_ISDIR(value.st_mode):
                    database.rollback()
                    raise _storage_failed()
                name = path.name
                if name.startswith(".candidate-"):
                    session_id = name.removeprefix(".candidate-")
                    if _SESSION_ID_RE.fullmatch(session_id) is None:
                        database.rollback()
                        raise _storage_failed()
                    planned.append(("staging_removed", path, None))
                    continue
                if name.startswith(".deleted-"):
                    session_id = name.removeprefix(".deleted-")
                    if _SESSION_ID_RE.fullmatch(session_id) is None:
                        database.rollback()
                        raise _storage_failed()
                    original = self.objects / session_id
                    status = sessions.get(session_id)
                    if status in {"active", "revoked"}:
                        if original.exists():
                            database.rollback()
                            raise _storage_failed()
                        planned.append(("deletion_rollback_completed", path, original))
                    else:
                        planned.append(("deletion_cleanup_completed", path, None))
                    continue
                if _SESSION_ID_RE.fullmatch(name) is None:
                    database.rollback()
                    raise _storage_failed()
                status = sessions.get(name)
                if status is None:
                    planned.append(("orphan_objects_removed", path, None))
                elif status == "deleted":
                    planned.append(("deletion_cleanup_completed", path, None))
            for session_id, status in sessions.items():
                if status in {"active", "revoked"} and (
                    session_id not in entry_names and f".deleted-{session_id}" not in entry_names
                ):
                    database.rollback()
                    raise _storage_failed()
            planned.extend(("staging_removed", path, None) for path, _ in staging)
            selected = planned[:batch_limit]
            counts = {
                "scanned_entries": len(entries) + len(staging),
                "planned_actions": len(planned),
                "staging_removed": 0,
                "orphan_objects_removed": 0,
                "deletion_cleanup_completed": 0,
                "deletion_rollback_completed": 0,
            }
            for action, _path, _destination in selected:
                counts[action] += 1
            if not dry_run:
                try:
                    staging_paths = dict(staging)
                    remove_staging(self.root, [(path, staging_paths[path]) for _, path, _ in selected if path in staging_paths])
                    for action, path, destination in selected:
                        if path in staging_paths:
                            continue
                        if action == "deletion_rollback_completed":
                            assert destination is not None
                            os.rename(path, destination)
                        else:
                            shutil.rmtree(path)
                except (OSError, ValueError) as exc:
                    database.rollback()
                    raise _storage_failed() from exc
            run_sequence = self._record_maintenance(
                database,
                operation="reconcile_storage",
                dry_run=dry_run,
                counts=counts,
                created_at=now,
            )
            database.commit()
        return {
            "schema_version": STUDIO_REVIEW_SERVICE_SCHEMA_VERSION,
            "status": "dry_run" if dry_run else "completed",
            "operation": "reconcile_storage",
            "run_sequence": run_sequence,
            "limit": batch_limit,
            "has_more": len(planned) > len(selected),
            "counts": counts,
        }

    def run_retention(self, *, dry_run: bool, limit: int = 100) -> dict[str, object]:
        """Expire and remove a bounded batch without emitting session-level telemetry."""

        if type(dry_run) is not bool:
            raise ValueError("Studio review retention dry_run must be a boolean.")
        batch_limit = _maintenance_limit(limit)
        now = self._now()
        renamed: list[tuple[Path, Path]] = []
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            rows = database.execute(
                "SELECT session_id FROM sessions "
                "WHERE status IN ('active', 'revoked') AND expires_at <= ? "
                "ORDER BY expires_at, session_id LIMIT ?",
                (now, batch_limit),
            ).fetchall()
            eligible = len(rows)
            has_more = (
                database.execute(
                    "SELECT 1 FROM sessions "
                    "WHERE status IN ('active', 'revoked') AND expires_at <= ? "
                    "ORDER BY expires_at, session_id LIMIT 1 OFFSET ?",
                    (now, eligible),
                ).fetchone()
                is not None
            )
            counts = {
                "eligible": eligible,
                "would_expire": eligible if dry_run else 0,
                "sessions_expired": 0 if dry_run else eligible,
                "objects_deleted": 0 if dry_run else eligible,
            }
            if not dry_run:
                try:
                    for row in rows:
                        session_id = str(row["session_id"])
                        original = self.objects / session_id
                        tombstone = self.objects / f".deleted-{session_id}"
                        if tombstone.exists() or not original.is_dir() or original.is_symlink():
                            raise _storage_failed()
                        os.rename(original, tombstone)
                        renamed.append((tombstone, original))
                        database.execute("UPDATE sessions SET status = 'deleted' WHERE session_id = ?", (session_id,))
                        database.execute(
                            "UPDATE capabilities SET revoked_at = ? "
                            "WHERE session_id = ? AND revoked_at IS NULL",
                            (now, session_id),
                        )
                        database.execute(
                            "UPDATE browser_sessions SET revoked_at = ? "
                            "WHERE session_id = ? AND revoked_at IS NULL",
                            (now, session_id),
                        )
                        self._audit(database, session_id, "session_expired", {"reason": "retention"}, now)
                except Exception:
                    database.rollback()
                    for tombstone, original in reversed(renamed):
                        if tombstone.exists() and not original.exists():
                            os.rename(tombstone, original)
                    raise
            run_sequence = self._record_maintenance(
                database,
                operation="retention",
                dry_run=dry_run,
                counts=counts,
                created_at=now,
            )
            database.commit()
        if not dry_run:
            try:
                for tombstone, _original in renamed:
                    shutil.rmtree(tombstone)
            except OSError as exc:
                raise _storage_failed() from exc
        return {
            "schema_version": STUDIO_REVIEW_SERVICE_SCHEMA_VERSION,
            "status": "dry_run" if dry_run else "completed",
            "operation": "retention",
            "run_sequence": run_sequence,
            "limit": batch_limit,
            "has_more": has_more,
            "counts": counts,
        }

    def verify_storage(self) -> dict[str, object]:
        """Verify a restored consistency unit without exposing retained review content."""

        try:
            if plan_staging(self.root):
                raise _storage_failed()
        except (OSError, ValueError) as exc:
            raise _storage_failed() from exc
        object_facts: list[dict[str, object]] = []
        receipts: dict[str, dict[str, object]] = {}
        with self._connect() as database:
            integrity = [str(row[0]) for row in database.execute("PRAGMA integrity_check").fetchall()]
            if integrity != ["ok"] or database.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise _storage_failed()
            rows = database.execute(
                "SELECT session_id, package_id, object_relpath, revision_sha256, revision_json, "
                "receipt_json, status FROM sessions ORDER BY session_id"
            ).fetchall()
            for row in rows:
                session_id = str(row["session_id"])
                package_id = str(row["package_id"])
                expected_relpath = f"objects/{session_id}/{package_id}"
                if row["object_relpath"] != expected_relpath:
                    raise _storage_failed()
                original = self.objects / session_id
                tombstone = self.objects / f".deleted-{session_id}"
                status = str(row["status"])
                if status == "deleted":
                    if original.exists() or tombstone.exists():
                        raise _storage_failed()
                else:
                    package = self.root / expected_relpath
                    if tombstone.exists() or not original.is_dir() or original.is_symlink():
                        raise _storage_failed()
                    try:
                        envelope = load_studio_share_package(package)
                    except (OSError, ValueError) as exc:
                        raise _storage_failed() from exc
                    revision = envelope.get("revision")
                    if (
                        envelope.get("package_id") != package_id
                        or not isinstance(revision, dict)
                        or revision != json.loads(str(row["revision_json"]))
                        or _canonical_sha256(revision) != row["revision_sha256"]
                    ):
                        raise _storage_failed()
                    object_facts.append(
                        {
                            "package_id": package_id,
                            "revision_identity_sha256": row["revision_sha256"],
                            "root_manifest_sha256": revision.get("root_manifest_sha256"),
                            "inspection_sha256": revision.get("inspection_sha256"),
                            "files_sha256": _canonical_sha256(envelope.get("files")),
                        }
                    )
                stored_receipt = json.loads(str(row["receipt_json"]))
                if not isinstance(stored_receipt, dict):
                    raise _storage_failed()
                _collect_receipts(stored_receipt, receipts)
            for row in database.execute("SELECT approval_json FROM approvals ORDER BY approval_id").fetchall():
                _collect_receipts(json.loads(str(row["approval_json"])), receipts)
            for row in database.execute("SELECT response_json FROM idempotency ORDER BY scope, operation, key").fetchall():
                _collect_receipts(json.loads(str(row["response_json"])), receipts)
            status_counts = {
                str(row["status"]): int(row["count"])
                for row in database.execute(
                    "SELECT status, COUNT(*) AS count FROM sessions GROUP BY status ORDER BY status"
                ).fetchall()
            }
        for receipt in receipts.values():
            if not self.verify_receipt(receipt):
                raise _storage_failed()
        expected_entries = {
            str(row["session_id"])
            for row in rows
            if str(row["status"]) != "deleted"
        }
        try:
            actual_entries = {path.name for path in self.objects.iterdir()}
        except OSError as exc:
            raise _storage_failed() from exc
        if actual_entries != expected_entries:
            raise _storage_failed()
        return {
            "schema_version": STUDIO_REVIEW_SERVICE_SCHEMA_VERSION,
            "status": "passed",
            "database_integrity": "ok",
            "session_count": len(rows),
            "status_counts": status_counts,
            "object_count": len(object_facts),
            "receipt_count": len(receipts),
            "receipt_key_count": len({receipt["key_id"] for receipt in receipts.values()}),
            "object_set_sha256": _canonical_sha256(object_facts),
        }

    def aggregate_telemetry(self, *, since: int = 0, until: int | None = None) -> dict[str, object]:
        """Return lifecycle and maintenance counts with no review-level identifiers or content."""

        now = self._now()
        end = now if until is None else until
        if type(since) is not int or type(end) is not int or since < 0 or end < since:
            raise ValueError("Studio review telemetry bounds must be ordered non-negative integers.")
        with self._connect() as database:
            event_counts = {
                str(row["event"]): int(row["count"])
                for row in database.execute(
                    "SELECT event, COUNT(*) AS count FROM audit_events "
                    "WHERE created_at >= ? AND created_at <= ? GROUP BY event ORDER BY event",
                    (since, end),
                ).fetchall()
            }
            maintenance_counts: dict[str, dict[str, int]] = {}
            for row in database.execute(
                "SELECT operation, dry_run, counts_json FROM maintenance_runs "
                "WHERE created_at >= ? AND created_at <= ? ORDER BY sequence",
                (since, end),
            ).fetchall():
                label = f"{row['operation']}:{'dry_run' if int(row['dry_run']) else 'applied'}"
                target = maintenance_counts.setdefault(label, {})
                value = json.loads(str(row["counts_json"]))
                if not isinstance(value, dict) or any(type(count) is not int for count in value.values()):
                    raise _storage_failed()
                for name, count in value.items():
                    target[str(name)] = target.get(str(name), 0) + int(count)
            status_counts = {
                str(row["status"]): int(row["count"])
                for row in database.execute(
                    "SELECT status, COUNT(*) AS count FROM sessions GROUP BY status ORDER BY status"
                ).fetchall()
            }
        return {
            "schema_version": STUDIO_REVIEW_SERVICE_SCHEMA_VERSION,
            "status": "ok",
            "window": {"since": since, "until": end},
            "event_counts": event_counts,
            "maintenance_counts": maintenance_counts,
            "session_status_counts": status_counts,
            "privacy": "aggregate_only",
        }

    def audit_events(self, browser_session: str) -> list[dict[str, object]]:
        """Return the owner-visible bounded audit trail without secrets or comment bodies."""

        with self._connect() as database:
            auth = self._authorize(database, browser_session, required_role="owner", now=self._now())
            rows = database.execute(
                "SELECT sequence, event, details_json, created_at FROM audit_events "
                "WHERE session_id = ? ORDER BY sequence",
                (auth["session_id"],),
            ).fetchall()
        return [
            {
                "sequence": row["sequence"],
                "event": row["event"],
                "details": json.loads(row["details_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def _record_maintenance(
        self,
        database: sqlite3.Connection,
        *,
        operation: str,
        dry_run: bool,
        counts: dict[str, int],
        created_at: int,
    ) -> int:
        cursor = database.execute(
            "INSERT INTO maintenance_runs (operation, dry_run, counts_json, created_at) VALUES (?, ?, ?, ?)",
            (operation, int(dry_run), _json_text(counts), created_at),
        )
        if cursor.lastrowid is None:
            raise _storage_failed()
        return int(cursor.lastrowid)

    def verify_receipt(self, receipt: Mapping[str, object]) -> bool:
        """Verify one service receipt against this service key and strict shape."""

        if not isinstance(receipt, Mapping):
            return False
        value = dict(receipt)
        signature = value.pop("signature", None)
        receipt_id = value.pop("receipt_id", None)
        if set(value) != {"schema_version", "kind", "key_id", "session_id", "issued_at", "payload"}:
            return False
        if value.get("schema_version") != STUDIO_REVIEW_SERVICE_SCHEMA_VERSION:
            return False
        receipt_key_id = value.get("key_id")
        if not isinstance(receipt_key_id, str):
            return False
        receipt_key = self._receipt_verification_keys.get(receipt_key_id)
        if receipt_key is None:
            return False
        if not isinstance(signature, str) or not signature.startswith("hmac-sha256:"):
            return False
        expected_signature = hmac.new(receipt_key, canonical_json_bytes(value), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature.removeprefix("hmac-sha256:"), expected_signature):
            return False
        expected_id = "vrr_" + hashlib.sha256(
            canonical_json_bytes({**value, "signature": signature})
        ).hexdigest()[:32]
        return isinstance(receipt_id, str) and hmac.compare_digest(receipt_id, expected_id)

    def _prepare_storage(self, *, reconcile_on_startup: bool) -> None:
        if self.root.exists():
            value = self.root.lstat()
            if self.root.is_symlink() or not stat.S_ISDIR(value.st_mode):
                raise ValueError("Studio review service root must be a normal directory.")
        else:
            self.root.mkdir(mode=0o700, parents=True)
        self.root.chmod(0o700)
        self.objects.mkdir(mode=0o700, exist_ok=True)
        self.objects.chmod(0o700)
        prepare_staging(self.root)
        with self._connect() as database:
            database.executescript(_SCHEMA)
            database.commit()
        self.database.chmod(0o600)
        if reconcile_on_startup:
            self.reconcile_storage(dry_run=False, limit=1000)

    def _connect(self) -> sqlite3.Connection:
        database = sqlite3.connect(self.database, timeout=10)
        database.row_factory = sqlite3.Row
        database.execute("PRAGMA foreign_keys = ON")
        database.execute("PRAGMA busy_timeout = 10000")
        return database

    def _now(self) -> int:
        value = self._clock()
        if not isinstance(value, (int, float)) or value < 0:
            raise ValueError("Studio review clock returned an invalid time.")
        return int(value)

    def _new_session_id(self) -> str:
        for _ in range(8):
            candidate = self._new_token("vsr_", 18)
            with self._connect() as database:
                exists = database.execute(
                    "SELECT 1 FROM sessions WHERE session_id = ?",
                    (candidate,),
                ).fetchone()
            if exists is None and not (self.objects / candidate).exists():
                return candidate
        raise _storage_failed()

    def _new_nonce(self) -> str:
        return _b64url(self._random_bytes(16))

    def _new_token(self, prefix: str, byte_count: int) -> str:
        return prefix + _b64url(self._random_bytes(byte_count))

    def _random_bytes(self, byte_count: int) -> bytes:
        value = self._token_bytes(byte_count)
        if not isinstance(value, bytes) or len(value) != byte_count:
            raise ValueError("Studio review token source returned invalid entropy.")
        return value

    def _stable_token(self, material: bytes, *, length: int) -> str:
        return _b64url(hmac.new(self._signing_key, material, hashlib.sha256).digest())[:length]

    def _capability(self, session_id: str, role: str, generation: int, nonce: str) -> str:
        material = f"capability\0{session_id}\0{role}\0{generation}\0{nonce}".encode()
        return "vsc_" + _b64url(hmac.new(self._signing_key, material, hashlib.sha256).digest())

    def _insert_capability(
        self,
        database: sqlite3.Connection,
        *,
        session_id: str,
        role: str,
        generation: int,
        nonce: str,
        created_at: int,
        expires_at: int,
    ) -> None:
        capability_hash = _token_sha256(self._capability(session_id, role, generation, nonce))
        database.execute(
            """
            INSERT INTO capabilities
                (capability_hash, session_id, role, generation, created_at, expires_at, consumed_at, revoked_at)
            VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)
            """,
            (capability_hash, session_id, role, generation, created_at, expires_at),
        )

    def _create_response_with_capabilities(self, safe_response: dict[str, object]) -> dict[str, object]:
        session = safe_response["session"]
        assert isinstance(session, dict)
        session_id = str(session["id"])
        with self._connect() as database:
            row = database.execute(
                "SELECT owner_nonce, reviewer_nonce, reviewer_generation FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            raise _storage_failed()
        owner = self._capability(session_id, "owner", 0, str(row["owner_nonce"]))
        reviewer = self._capability(
            session_id,
            "reviewer",
            int(row["reviewer_generation"]),
            str(row["reviewer_nonce"]),
        )
        return {
            **safe_response,
            "fragment_capabilities": {
                "owner": f"#cap={owner}",
                "reviewer": f"#cap={reviewer}",
                "transport": "url_fragment_one_time_exchange",
            },
        }

    def _rotation_response_with_capability(self, safe_response: dict[str, object]) -> dict[str, object]:
        session_id = str(safe_response["session_id"])
        generation = int(safe_response["reviewer_generation"])
        with self._connect() as database:
            row = database.execute(
                "SELECT reviewer_nonce, reviewer_generation FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None or int(row["reviewer_generation"]) != generation:
            raise _storage_failed()
        capability = self._capability(session_id, "reviewer", generation, str(row["reviewer_nonce"]))
        return {
            **safe_response,
            "reviewer_fragment": f"#cap={capability}",
            "transport": "url_fragment_one_time_exchange",
        }

    def _authorize(
        self,
        database: sqlite3.Connection,
        browser_session: str,
        *,
        required_role: str | None,
        now: int,
    ) -> sqlite3.Row:
        cookie_hash = _token_sha256(browser_session)
        row = database.execute(
            """
            SELECT b.session_id, b.role, b.expires_at AS cookie_expires_at, b.revoked_at AS cookie_revoked_at,
                   s.package_id, s.object_relpath, s.revision_sha256, s.revision_json,
                   s.projection_json, s.verification_json, s.receipt_json, s.status,
                   s.created_at AS session_created_at, s.expires_at AS session_expires_at,
                   s.reviewer_generation
            FROM browser_sessions b JOIN sessions s ON s.session_id = b.session_id
            WHERE b.cookie_hash = ?
            """,
            (cookie_hash,),
        ).fetchone()
        if (
            row is None
            or row["cookie_revoked_at"] is not None
            or row["status"] != "active"
            or int(row["cookie_expires_at"]) <= now
            or int(row["session_expires_at"]) <= now
        ):
            raise _access_denied()
        object_root = self.objects / str(row["session_id"])
        if not object_root.is_dir() or object_root.is_symlink():
            raise _storage_failed()
        if required_role is not None and row["role"] != required_role:
            raise StudioReviewServiceError(
                "STUDIO_REVIEW_ROLE_FORBIDDEN",
                "This private review role cannot perform the requested operation.",
                "Use the owner session for approval and lifecycle actions; use the reviewer session for comments.",
                http_status=403,
            )
        return row

    def _idempotency_lookup(
        self,
        scope: str,
        operation: str,
        key: str,
        request_sha256: str,
    ) -> dict[str, object] | None:
        with self._connect() as database:
            return self._idempotency_lookup_db(database, scope, operation, key, request_sha256)

    def _idempotency_lookup_db(
        self,
        database: sqlite3.Connection,
        scope: str,
        operation: str,
        key: str,
        request_sha256: str,
    ) -> dict[str, object] | None:
        row = database.execute(
            "SELECT request_sha256, response_json FROM idempotency WHERE scope = ? AND operation = ? AND key = ?",
            (scope, operation, key),
        ).fetchone()
        if row is None:
            return None
        if not hmac.compare_digest(str(row["request_sha256"]), request_sha256):
            raise StudioReviewServiceError(
                "STUDIO_REVIEW_IDEMPOTENCY_CONFLICT",
                "The idempotency key was already used for a different request.",
                "Retry the original request exactly or choose a fresh idempotency key.",
                http_status=409,
            )
        value = json.loads(row["response_json"])
        if not isinstance(value, dict):
            raise _storage_failed()
        return value

    def _idempotency_store_db(
        self,
        database: sqlite3.Connection,
        scope: str,
        operation: str,
        key: str,
        request_sha256: str,
        response: dict[str, object],
        created_at: int,
    ) -> None:
        database.execute(
            """
            INSERT INTO idempotency (scope, operation, key, request_sha256, response_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (scope, operation, key, request_sha256, _json_text(response), created_at),
        )

    def _terminal_replay(
        self,
        database: sqlite3.Connection,
        browser_session: str,
        operation: str,
        key: str,
        request_sha256: str,
    ) -> dict[str, object] | None:
        row = database.execute(
            "SELECT session_id, role FROM browser_sessions WHERE cookie_hash = ?",
            (_token_sha256(browser_session),),
        ).fetchone()
        if row is None or row["role"] != "owner":
            return None
        return self._idempotency_lookup_db(database, str(row["session_id"]), operation, key, request_sha256)

    def _finish_delete_cleanup(self, session_id: str) -> None:
        tombstone = self.objects / f".deleted-{session_id}"
        original = self.objects / session_id
        if original.exists():
            raise _storage_failed()
        if tombstone.exists():
            try:
                shutil.rmtree(tombstone)
            except OSError as exc:
                raise _storage_failed() from exc

    def _audit(
        self,
        database: sqlite3.Connection,
        session_id: str,
        event: str,
        details: dict[str, object],
        created_at: int,
    ) -> None:
        database.execute(
            "INSERT INTO audit_events (session_id, event, details_json, created_at) VALUES (?, ?, ?, ?)",
            (session_id, event, _json_text(details), created_at),
        )

    def _signed_receipt(
        self,
        kind: str,
        session_id: str,
        payload: dict[str, object],
        *,
        issued_at: int,
    ) -> dict[str, object]:
        body = {
            "schema_version": STUDIO_REVIEW_SERVICE_SCHEMA_VERSION,
            "kind": kind,
            "key_id": self._key_id,
            "session_id": session_id,
            "issued_at": issued_at,
            "payload": payload,
        }
        signature = "hmac-sha256:" + hmac.new(
            self._receipt_signing_key,
            canonical_json_bytes(body),
            hashlib.sha256,
        ).hexdigest()
        receipt_id = "vrr_" + hashlib.sha256(
            canonical_json_bytes({**body, "signature": signature})
        ).hexdigest()[:32]
        return {**body, "signature": signature, "receipt_id": receipt_id}


def _build_projection(package: Path, envelope: dict[str, object]) -> dict[str, object]:
    payload = package / "payload"
    source = _read_json(payload / "source/viewspec.app.json")
    comparison = _read_json(payload / "artifacts/studio_comparison_manifest.json")
    inspection_entry = comparison.get("inspection")
    if not isinstance(inspection_entry, dict) or not isinstance(inspection_entry.get("path"), str):
        raise _package_invalid()
    inspection = _read_json(payload / "artifacts" / str(inspection_entry["path"]))
    revision = envelope["revision"]
    assert isinstance(revision, dict)

    source_screens = source.get("screens")
    if not isinstance(source_screens, list):
        raise _package_invalid()
    semantic_by_screen = {
        str(item["screen_id"]): str(item["semantic_identity_sha256"])
        for item in revision["screens"]
        if isinstance(item, dict)
    }
    resource_refs = _inspection_resource_refs(inspection)
    screens: list[dict[str, object]] = []
    for screen in source_screens:
        if not isinstance(screen, dict) or not isinstance(screen.get("id"), str):
            raise _package_invalid()
        screen_id = str(screen["id"])
        intent = screen.get("intent_bundle")
        if screen_id not in semantic_by_screen or not isinstance(intent, dict):
            raise _package_invalid()
        substrate = intent.get("substrate")
        view_spec = intent.get("view_spec")
        if not isinstance(substrate, dict) or not isinstance(view_spec, dict):
            raise _package_invalid()
        nodes = substrate.get("nodes")
        bindings = view_spec.get("bindings", [])
        actions = view_spec.get("actions", [])
        if not isinstance(nodes, dict) or not isinstance(bindings, list) or not isinstance(actions, list):
            raise _package_invalid()
        node_ids = sorted(str(key) for key in nodes if isinstance(key, str))
        authored_binding_ids = [
            str(item["id"])
            for item in bindings
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        ]
        binding_ids = set(authored_binding_ids)
        binding_ids.update(
            key.split("\0", 1)[1]
            for key in resource_refs
            if key.startswith(f"{screen_id}\0")
        )
        action_ids = sorted(
            str(item["id"])
            for item in actions
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        )
        if (
            len(node_ids) != len(nodes)
            or len(authored_binding_ids) != len(bindings)
            or len(set(authored_binding_ids)) != len(authored_binding_ids)
            or len(action_ids) != len(actions)
        ):
            raise _package_invalid()
        screens.append(
            {
                "id": screen_id,
                "title": screen.get("title"),
                "semantic_identity_sha256": semantic_by_screen[screen_id],
                "targets": {
                    "page": [screen_id],
                    "node": node_ids,
                    "binding": sorted(binding_ids),
                    "action": action_ids,
                },
            }
        )
    routes = revision.get("routes")
    if not isinstance(routes, list):
        raise _package_invalid()
    normalized_routes = []
    for route in routes:
        if not isinstance(route, dict) or set(route) != {"id", "path", "screenId"}:
            raise _package_invalid()
        normalized_routes.append(
            {"id": route["id"], "path": route["path"], "screen_id": route["screenId"]}
        )
    replay_refs = sorted(_inspection_replay_refs(inspection))
    artifacts = [
        {
            "path": item["path"],
            "bytes": item["bytes"],
            "sha256": item["sha256"],
            "media_type": item["media_type"],
        }
        for item in envelope["files"]
        if isinstance(item, dict) and item.get("role") == "checked_artifact"
    ]
    artifact_index = {str(item["path"]): item for item in artifacts}
    return {
        "revision": revision,
        "routes": normalized_routes,
        "screens": screens,
        "inspection": inspection,
        "replay_evidence_refs": replay_refs,
        "resource_evidence_refs": resource_refs,
        "artifacts": artifacts,
        "artifact_index": artifact_index,
        "policy": {
            "production_data": "not_claimed",
            "visual_parity": "not_proven",
            "source_editing": "forbidden",
        },
    }


def _checked_comment_context(
    value: Mapping[str, object],
    *,
    projection: dict[str, object],
    revision_sha256: str,
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise _context_invalid()
    context = dict(value)
    expected = {
        "revision_identity_sha256",
        "route",
        "screen_id",
        "semantic_identity_sha256",
        "viewport_width",
        "target",
        "replay_evidence_ref",
    }
    if set(context) != expected or context.get("revision_identity_sha256") != revision_sha256:
        raise _context_invalid()
    route = context.get("route")
    screen_id = context.get("screen_id")
    viewport = context.get("viewport_width")
    if not isinstance(route, str) or not isinstance(screen_id, str) or viewport not in STUDIO_REVIEW_VIEWPORTS:
        raise _context_invalid()
    route_matches = [
        item
        for item in projection["routes"]
        if isinstance(item, dict) and item.get("path") == route and item.get("screen_id") == screen_id
    ]
    screens = [item for item in projection["screens"] if isinstance(item, dict) and item.get("id") == screen_id]
    if len(route_matches) != 1 or len(screens) != 1:
        raise _context_invalid()
    screen = screens[0]
    if context.get("semantic_identity_sha256") != screen.get("semantic_identity_sha256"):
        raise _context_invalid()
    target = context.get("target")
    if not isinstance(target, dict) or set(target) != {"kind", "id"}:
        raise _context_invalid()
    kind = target.get("kind")
    target_id = target.get("id")
    targets = screen.get("targets")
    if (
        not isinstance(kind, str)
        or not isinstance(target_id, str)
        or not isinstance(targets, dict)
        or kind not in {"page", "node", "binding", "action"}
        or target_id not in targets.get(kind, [])
    ):
        raise _context_invalid()
    replay_ref = context.get("replay_evidence_ref")
    if replay_ref is not None and (
        not isinstance(replay_ref, str) or replay_ref not in projection["replay_evidence_refs"]
    ):
        raise _context_invalid()
    evidence_refs = [] if replay_ref is None else [replay_ref]
    if kind == "binding":
        resource_ref = projection["resource_evidence_refs"].get(f"{screen_id}\0{target_id}")
        if isinstance(resource_ref, str):
            evidence_refs.append(resource_ref)
    return {
        "revision_identity_sha256": revision_sha256,
        "route": route,
        "screen_id": screen_id,
        "semantic_identity_sha256": screen["semantic_identity_sha256"],
        "viewport_width": viewport,
        "target": {"kind": kind, "id": target_id},
        "evidence_refs": evidence_refs,
    }


def _inspection_replay_refs(inspection: dict[str, object]) -> set[str]:
    state = inspection.get("state")
    if not isinstance(state, dict) or not isinstance(state.get("replays"), list):
        return set()
    return {
        str(checkpoint["evidence_ref"])
        for replay in state["replays"]
        if isinstance(replay, dict) and isinstance(replay.get("checkpoints"), list)
        for checkpoint in replay["checkpoints"]
        if isinstance(checkpoint, dict) and isinstance(checkpoint.get("evidence_ref"), str)
    }


def _inspection_resource_refs(inspection: dict[str, object]) -> dict[str, str]:
    resources = inspection.get("resources")
    if not isinstance(resources, dict) or not isinstance(resources.get("views"), list):
        return {}
    candidates: dict[str, set[str]] = {}
    for view in resources["views"]:
        if not isinstance(view, dict) or not isinstance(view.get("screen_id"), str):
            continue
        assertions = view.get("assertions")
        if not isinstance(assertions, list):
            continue
        for assertion in assertions:
            if (
                isinstance(assertion, dict)
                and isinstance(assertion.get("matched_binding_id"), str)
                and isinstance(assertion.get("canonical_identity"), str)
            ):
                key = f"{view['screen_id']}\0{assertion['matched_binding_id']}"
                candidates.setdefault(key, set()).add(
                    f"studio-inspection/resources/{assertion['canonical_identity']}"
                )
    return {key: next(iter(refs)) for key, refs in candidates.items() if len(refs) == 1}


def _validated_verification(value: object, *, envelope: dict[str, object]) -> dict[str, object]:
    if not isinstance(value, dict):
        raise _verification_failed()
    expected = {
        "schema_version",
        "status",
        "verifier_id",
        "package_id",
        "source_sha256",
        "artifact_set_sha256",
        "root_manifest_sha256",
        "inspection_sha256",
        "target_artifact_sets",
        "rebuild",
        "sandbox",
    }
    revision = envelope["revision"]
    assert isinstance(revision, dict)
    if (
        set(value) != expected
        or value.get("schema_version") != STUDIO_REVIEW_SERVICE_SCHEMA_VERSION
        or value.get("status") != "passed"
        or not isinstance(value.get("verifier_id"), str)
        or not 1 <= len(str(value["verifier_id"])) <= 128
        or value.get("package_id") != envelope.get("package_id")
        or value.get("source_sha256") != revision.get("source_sha256")
        or value.get("artifact_set_sha256") != revision.get("artifact_set_sha256")
        or value.get("root_manifest_sha256") != revision.get("root_manifest_sha256")
        or value.get("inspection_sha256") != revision.get("inspection_sha256")
        or value.get("target_artifact_sets") != revision.get("target_artifact_sets")
    ):
        raise _verification_failed()
    rebuild = value.get("rebuild")
    if not isinstance(rebuild, dict) or set(rebuild) != {
        "evidence_sha256",
        "expected_inventory_sha256",
        "observed_inventory_sha256",
        "source_only_request",
        "install_used",
        "lifecycle_hooks_disabled",
        "uploaded_artifacts_executed",
    }:
        raise _verification_failed()
    expected_inventory = revision.get("artifact_set_sha256")
    if (
        not isinstance(rebuild.get("evidence_sha256"), str)
        or _HASH_RE.fullmatch(str(rebuild["evidence_sha256"])) is None
        or rebuild.get("expected_inventory_sha256") != expected_inventory
        or rebuild.get("observed_inventory_sha256") != expected_inventory
        or rebuild.get("source_only_request") is not True
        or rebuild.get("install_used") is not False
        or rebuild.get("lifecycle_hooks_disabled") is not True
        or rebuild.get("uploaded_artifacts_executed") is not False
    ):
        raise _verification_failed()
    sandbox = value.get("sandbox")
    if not isinstance(sandbox, dict) or set(sandbox) != {
        "network",
        "lifecycle_hooks",
        "arbitrary_commands",
        "limits",
    }:
        raise _verification_failed()
    if (
        sandbox.get("network") != "denied"
        or sandbox.get("lifecycle_hooks") != "disabled"
        or sandbox.get("arbitrary_commands") != "disabled"
    ):
        raise _verification_failed()
    limits = sandbox.get("limits")
    if not isinstance(limits, dict) or set(limits) != {
        "cpu_seconds",
        "memory_bytes",
        "wall_seconds",
        "file_count",
        "byte_count",
    }:
        raise _verification_failed()
    bounds = {
        "cpu_seconds": STUDIO_REVIEW_MAX_CPU_SECONDS,
        "memory_bytes": STUDIO_REVIEW_MAX_MEMORY_BYTES,
        "wall_seconds": STUDIO_REVIEW_MAX_WALL_SECONDS,
        "file_count": STUDIO_SHARE_MAX_FILES,
        "byte_count": STUDIO_SHARE_MAX_BYTES,
    }
    if any(type(limits.get(key)) is not int or not 1 <= int(limits[key]) <= maximum for key, maximum in bounds.items()):
        raise _verification_failed()
    totals = envelope.get("totals")
    if (
        not isinstance(totals, dict)
        or int(limits["file_count"]) < int(totals.get("file_count", STUDIO_SHARE_MAX_FILES + 1))
        or int(limits["byte_count"]) < int(totals.get("bytes", STUDIO_SHARE_MAX_BYTES + 1))
    ):
        raise _verification_failed()
    encoded = canonical_json_bytes(value)
    if len(encoded) > 64 * 1024:
        raise _verification_failed()
    return json.loads(encoded)


def _response_policy() -> dict[str, object]:
    return {
        "visibility": "unlisted_private",
        "robots": "noindex, noarchive",
        "cache_control": "private, no-store",
        "referrer_policy": "no-referrer",
        "capabilities_in_artifact_urls": False,
        "analytics": "disabled",
    }


def _comment_body(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or _COMMENT_CONTROL_RE.search(value):
        raise StudioReviewServiceError(
            "STUDIO_REVIEW_COMMENT_INVALID",
            "Review comments must be non-empty plain text without control characters.",
            "Submit a concise plain-text comment.",
        )
    if len(value.encode("utf-8")) > STUDIO_REVIEW_COMMENT_MAX_BYTES:
        raise StudioReviewServiceError(
            "STUDIO_REVIEW_COMMENT_INVALID",
            f"Review comments may not exceed {STUDIO_REVIEW_COMMENT_MAX_BYTES} UTF-8 bytes.",
            "Shorten the comment and retry.",
        )
    return value


def _artifact_path(value: object) -> str:
    if not isinstance(value, str) or _SAFE_ARTIFACT_RE.fullmatch(value) is None or "\\" in value:
        raise _artifact_forbidden()
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts) or path.parts[0] != "artifacts":
        raise _artifact_forbidden()
    return path.as_posix()


def _idempotency_key(value: object) -> str:
    if not isinstance(value, str) or _IDEMPOTENCY_RE.fullmatch(value) is None:
        raise StudioReviewServiceError(
            "STUDIO_REVIEW_IDEMPOTENCY_CONFLICT",
            "Idempotency keys must contain 16 through 128 safe characters.",
            "Generate one stable random key per logical operation.",
        )
    return value


def _maintenance_limit(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 1000:
        raise ValueError("Studio review maintenance limit must be between 1 and 1000.")
    return value


def _collect_receipts(value: object, target: dict[str, dict[str, object]]) -> None:
    if isinstance(value, dict):
        if {
            "schema_version",
            "kind",
            "key_id",
            "session_id",
            "issued_at",
            "payload",
            "signature",
            "receipt_id",
        } == set(value):
            receipt_id = value.get("receipt_id")
            if not isinstance(receipt_id, str) or receipt_id in target and target[receipt_id] != value:
                raise _storage_failed()
            target[receipt_id] = value
            return
        for child in value.values():
            _collect_receipts(child, target)
    elif isinstance(value, list):
        for child in value:
            _collect_receipts(child, target)


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _package_invalid() from exc
    if not isinstance(value, dict):
        raise _package_invalid()
    return value


def _seal_private_tree(root: Path) -> None:
    for path in (root, *root.rglob("*")):
        value = path.lstat()
        if path.is_symlink():
            raise _package_invalid()
        if stat.S_ISDIR(value.st_mode):
            path.chmod(0o700)
        elif stat.S_ISREG(value.st_mode):
            path.chmod(0o600)
        else:
            raise _package_invalid()


def _json_text(value: object) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _bounded_file_sha256(path: Path, *, maximum: int) -> tuple[str, int]:
    try:
        metadata = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or not 1 <= metadata.st_size <= maximum:
            raise OSError("file is outside its regular bounded identity")
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                size += len(chunk)
                if size > maximum:
                    raise OSError("file changed beyond its byte boundary")
                digest.update(chunk)
        if size != metadata.st_size:
            raise OSError("file size changed during hashing")
    except OSError as exc:
        raise StudioShareError(
            "STUDIO_SHARE_PACKAGE_INVALID",
            "Review archive is not one stable bounded regular file.",
            "Prepare a fresh deterministic private-review archive.",
            cli_exit=1,
        ) from exc
    return digest.hexdigest(), size


def _token_sha256(value: object) -> str:
    if not isinstance(value, str) or not 16 <= len(value) <= 256:
        return hashlib.sha256(b"invalid-token").hexdigest()
    return hashlib.sha256(value.encode()).hexdigest()


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _access_denied() -> StudioReviewServiceError:
    return StudioReviewServiceError(
        "STUDIO_REVIEW_ACCESS_DENIED",
        "Private review access is unavailable.",
        "Use a current unexpired capability or ask the owner for a rotated link.",
        http_status=404,
    )


def _artifact_forbidden() -> StudioReviewServiceError:
    return StudioReviewServiceError(
        "STUDIO_REVIEW_ARTIFACT_FORBIDDEN",
        "The requested path is not an allowlisted checked review artifact.",
        "Request a path from the current revision artifact inventory.",
        http_status=404,
    )


def _context_invalid() -> StudioReviewServiceError:
    return StudioReviewServiceError(
        "STUDIO_REVIEW_CONTEXT_INVALID",
        "Review comment context does not resolve uniquely in the exact immutable revision.",
        "Reload the current route and semantic target before submitting the comment.",
    )


def _expiry_invalid() -> StudioReviewServiceError:
    return StudioReviewServiceError(
        "STUDIO_REVIEW_EXPIRY_INVALID",
        "Private review expiry is outside the allowed range or would extend the session.",
        "Choose a future time that only shortens the current maximum lifetime.",
    )


def _package_invalid() -> StudioReviewServiceError:
    return StudioReviewServiceError(
        "STUDIO_REVIEW_PACKAGE_INVALID",
        "The prepared private-review package cannot produce a strict service projection.",
        "Prepare a fresh package from the exact checked Studio comparison.",
    )


def _storage_failed() -> StudioReviewServiceError:
    return StudioReviewServiceError(
        "STUDIO_REVIEW_STORAGE_FAILED",
        "The private review service could not preserve its durable state invariant.",
        "Keep the session unavailable and retry after repairing storage.",
        http_status=500,
    )


def _verification_failed() -> StudioReviewServiceError:
    return StudioReviewServiceError(
        "STUDIO_REVIEW_VERIFICATION_FAILED",
        "Bounded hosted verification did not prove the exact prepared revision and sandbox policy.",
        "Do not create a link; repair the verifier or package and retry.",
    )


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    package_id TEXT NOT NULL,
    object_relpath TEXT NOT NULL UNIQUE,
    revision_sha256 TEXT NOT NULL,
    revision_json TEXT NOT NULL,
    projection_json TEXT NOT NULL,
    verification_json TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'revoked', 'deleted')),
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    reviewer_generation INTEGER NOT NULL,
    owner_nonce TEXT NOT NULL,
    reviewer_nonce TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS capabilities (
    capability_hash TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    role TEXT NOT NULL CHECK (role IN ('owner', 'reviewer')),
    generation INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    consumed_at INTEGER,
    revoked_at INTEGER
);
CREATE TABLE IF NOT EXISTS browser_sessions (
    cookie_hash TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    role TEXT NOT NULL CHECK (role IN ('owner', 'reviewer')),
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    revoked_at INTEGER
);
CREATE TABLE IF NOT EXISTS comments (
    comment_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    idempotency_key TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    comment_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE (session_id, idempotency_key)
);
CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL UNIQUE REFERENCES sessions(session_id),
    idempotency_key TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    approval_json TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS idempotency (
    scope TEXT NOT NULL,
    operation TEXT NOT NULL,
    key TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (scope, operation, key)
);
CREATE TABLE IF NOT EXISTS audit_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    event TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS maintenance_runs (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    dry_run INTEGER NOT NULL CHECK (dry_run IN (0, 1)),
    counts_json TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS capabilities_session_idx ON capabilities(session_id);
CREATE INDEX IF NOT EXISTS browser_sessions_session_idx ON browser_sessions(session_id);
CREATE INDEX IF NOT EXISTS comments_session_idx ON comments(session_id);
CREATE INDEX IF NOT EXISTS audit_events_session_idx ON audit_events(session_id);
CREATE INDEX IF NOT EXISTS maintenance_runs_created_idx ON maintenance_runs(created_at);
"""


__all__ = [
    "HostedReviewArtifact",
    "STUDIO_REVIEW_COMMENT_MAX_BYTES",
    "STUDIO_REVIEW_COOKIE_TTL_SECONDS",
    "STUDIO_REVIEW_MAX_CPU_SECONDS",
    "STUDIO_REVIEW_MAX_EXPIRY_SECONDS",
    "STUDIO_REVIEW_MAX_MEMORY_BYTES",
    "STUDIO_REVIEW_MAX_WALL_SECONDS",
    "STUDIO_REVIEW_MIN_EXPIRY_SECONDS",
    "STUDIO_REVIEW_SERVICE_SCHEMA_VERSION",
    "STUDIO_REVIEW_VIEWPORTS",
    "StudioReviewService",
    "StudioReviewServiceError",
    "VerificationRunner",
]
