"""Replay-safe authentication for private Studio review service hops.

The public API and dedicated review service do not share paid API credentials.
Instead, the API forwards one bounded review archive with a dedicated HMAC
identity.  Signatures bind the exact method, path, forwarded creation headers,
body hash, timestamp, nonce, direction, and protocol version.  The review
service persists admitted request nonces so process restarts cannot reopen the
replay window.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import hmac
from pathlib import Path
import re
import secrets
import sqlite3
import time

from viewspec.review_contract import canonical_json_bytes


STUDIO_REVIEW_INTERNAL_SCHEMA_VERSION = 1
STUDIO_REVIEW_INTERNAL_PROTOCOL = "viewspec-studio-review-internal-v1"
STUDIO_REVIEW_INTERNAL_INGRESS_PATH = "/internal/v1/reviews"
STUDIO_REVIEW_INTERNAL_MAX_CLOCK_SKEW_SECONDS = 60
STUDIO_REVIEW_INTERNAL_MAX_ACTIVE_NONCES = 4096
STUDIO_REVIEW_INTERNAL_AUTHENTICATED_HEADER = "x-viewspec-internal-authenticated"

STUDIO_REVIEW_INTERNAL_PROTOCOL_HEADER = "x-viewspec-internal-protocol"
STUDIO_REVIEW_INTERNAL_TIMESTAMP_HEADER = "x-viewspec-internal-timestamp"
STUDIO_REVIEW_INTERNAL_NONCE_HEADER = "x-viewspec-internal-nonce"
STUDIO_REVIEW_INTERNAL_REQUEST_NONCE_HEADER = "x-viewspec-internal-request-nonce"
STUDIO_REVIEW_INTERNAL_BODY_SHA256_HEADER = "x-viewspec-internal-body-sha256"
STUDIO_REVIEW_INTERNAL_SIGNATURE_HEADER = "x-viewspec-internal-signature"

STUDIO_REVIEW_INTERNAL_FORWARDED_HEADERS = frozenset(
    {
        "content-type",
        "idempotency-key",
        "x-viewspec-disclosure-accepted",
        "x-viewspec-expiry-seconds",
    }
)

_NONCE_RE = re.compile(r"^[0-9a-f]{32}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SIGNATURE_RE = re.compile(r"^hmac-sha256:([0-9a-f]{64})$")
_HEADER_NAME_RE = re.compile(r"^[a-z0-9-]{1,64}$")
_HEADER_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_METHOD_RE = re.compile(r"^[A-Z]{1,16}$")


class StudioReviewInternalAuthError(ValueError):
    """One internal transport message failed closed authentication."""

    code = "STUDIO_REVIEW_INTERNAL_AUTH_FAILED"

    def __init__(self, message: str = "Private review internal authentication failed.") -> None:
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class VerifiedStudioReviewInternalRequest:
    """The only request material allowed to cross into the review adapter."""

    request_nonce: str
    forwarded_headers: Mapping[str, str]


class StudioReviewInternalNonceStore:
    """Durably reject replayed, still-live internal transport nonces."""

    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        if self.database.exists() and (self.database.is_symlink() or not self.database.is_file()):
            raise StudioReviewInternalAuthError("Private review nonce storage is not a regular file.")
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS studio_review_internal_nonces (
                    direction TEXT NOT NULL,
                    nonce TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    PRIMARY KEY (direction, nonce)
                )
                """
            )
        try:
            self.database.chmod(0o600)
        except OSError as exc:
            raise StudioReviewInternalAuthError("Private review nonce storage permissions could not be secured.") from exc

    def admit(self, *, direction: str, nonce: str, expires_at: int, now: int) -> bool:
        if direction not in {"request", "response"} or _NONCE_RE.fullmatch(nonce) is None:
            raise StudioReviewInternalAuthError()
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "DELETE FROM studio_review_internal_nonces WHERE expires_at < ?",
                    (now,),
                )
                active = connection.execute(
                    "SELECT COUNT(*) FROM studio_review_internal_nonces"
                ).fetchone()
                if active is None or int(active[0]) >= STUDIO_REVIEW_INTERNAL_MAX_ACTIVE_NONCES:
                    raise StudioReviewInternalAuthError("Private review nonce storage reached its bounded capacity.")
                try:
                    connection.execute(
                        "INSERT INTO studio_review_internal_nonces(direction, nonce, expires_at) VALUES (?, ?, ?)",
                        (direction, nonce, expires_at),
                    )
                except sqlite3.IntegrityError:
                    return False
        except StudioReviewInternalAuthError:
            raise
        except sqlite3.Error as exc:
            raise StudioReviewInternalAuthError("Private review nonce storage is unavailable.") from exc
        return True

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


class StudioReviewInternalAuth:
    """Sign and verify the API-to-review request and its bound response."""

    def __init__(
        self,
        secret: bytes,
        *,
        nonce_store: StudioReviewInternalNonceStore,
        clock: Callable[[], float] = time.time,
        nonce_factory: Callable[[], str] | None = None,
        max_clock_skew_seconds: int = STUDIO_REVIEW_INTERNAL_MAX_CLOCK_SKEW_SECONDS,
    ) -> None:
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise ValueError("Studio review internal HMAC secret must contain at least 32 bytes.")
        if not isinstance(nonce_store, StudioReviewInternalNonceStore):
            raise TypeError("Studio review internal authentication requires durable nonce storage.")
        if not callable(clock) or (nonce_factory is not None and not callable(nonce_factory)):
            raise TypeError("Studio review internal clock and nonce factory must be callable.")
        if not 1 <= max_clock_skew_seconds <= 300:
            raise ValueError("Studio review internal clock skew must be between 1 and 300 seconds.")
        self._secret = secret
        self._nonce_store = nonce_store
        self._clock = clock
        self._nonce_factory = nonce_factory or (lambda: secrets.token_hex(16))
        self._max_clock_skew_seconds = max_clock_skew_seconds

    def sign_request(
        self,
        *,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes,
    ) -> dict[str, str]:
        method, path, normalized, body = _message_inputs(method, path, headers, body)
        forwarded = _forwarded_headers(normalized)
        timestamp = int(self._clock())
        nonce = self._new_nonce()
        body_sha256 = hashlib.sha256(body).hexdigest()
        payload = _request_payload(
            method=method,
            path=path,
            forwarded_headers=forwarded,
            body_sha256=body_sha256,
            timestamp=timestamp,
            nonce=nonce,
        )
        return {
            **dict(forwarded),
            STUDIO_REVIEW_INTERNAL_PROTOCOL_HEADER: STUDIO_REVIEW_INTERNAL_PROTOCOL,
            STUDIO_REVIEW_INTERNAL_TIMESTAMP_HEADER: str(timestamp),
            STUDIO_REVIEW_INTERNAL_NONCE_HEADER: nonce,
            STUDIO_REVIEW_INTERNAL_BODY_SHA256_HEADER: body_sha256,
            STUDIO_REVIEW_INTERNAL_SIGNATURE_HEADER: self._signature(payload),
        }

    def verify_request(
        self,
        *,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes,
    ) -> VerifiedStudioReviewInternalRequest:
        method, path, normalized, body = _message_inputs(method, path, headers, body)
        forwarded = _forwarded_headers(normalized)
        timestamp, nonce, body_sha256, signature = self._authentication_values(normalized)
        if body_sha256 != hashlib.sha256(body).hexdigest():
            raise StudioReviewInternalAuthError()
        payload = _request_payload(
            method=method,
            path=path,
            forwarded_headers=forwarded,
            body_sha256=body_sha256,
            timestamp=timestamp,
            nonce=nonce,
        )
        self._verify_signature(payload, signature)
        now = self._verify_time(timestamp)
        if not self._nonce_store.admit(
            direction="request",
            nonce=nonce,
            expires_at=timestamp + self._max_clock_skew_seconds,
            now=now,
        ):
            raise StudioReviewInternalAuthError()
        return VerifiedStudioReviewInternalRequest(request_nonce=nonce, forwarded_headers=forwarded)

    def sign_response(
        self,
        *,
        status: int,
        path: str,
        content_type: str,
        body: bytes,
        request_nonce: str,
    ) -> dict[str, str]:
        status, path, content_type, body, request_nonce = _response_inputs(
            status,
            path,
            content_type,
            body,
            request_nonce,
        )
        timestamp = int(self._clock())
        nonce = self._new_nonce()
        body_sha256 = hashlib.sha256(body).hexdigest()
        payload = _response_payload(
            status=status,
            path=path,
            content_type=content_type,
            body_sha256=body_sha256,
            timestamp=timestamp,
            nonce=nonce,
            request_nonce=request_nonce,
        )
        return {
            STUDIO_REVIEW_INTERNAL_PROTOCOL_HEADER: STUDIO_REVIEW_INTERNAL_PROTOCOL,
            STUDIO_REVIEW_INTERNAL_TIMESTAMP_HEADER: str(timestamp),
            STUDIO_REVIEW_INTERNAL_NONCE_HEADER: nonce,
            STUDIO_REVIEW_INTERNAL_REQUEST_NONCE_HEADER: request_nonce,
            STUDIO_REVIEW_INTERNAL_BODY_SHA256_HEADER: body_sha256,
            STUDIO_REVIEW_INTERNAL_SIGNATURE_HEADER: self._signature(payload),
        }

    def verify_response(
        self,
        *,
        status: int,
        path: str,
        headers: Mapping[str, str],
        body: bytes,
        request_nonce: str,
    ) -> None:
        normalized = _normalize_headers(headers)
        content_type = normalized.get("content-type", "")
        status, path, content_type, body, request_nonce = _response_inputs(
            status,
            path,
            content_type,
            body,
            request_nonce,
        )
        timestamp, nonce, body_sha256, signature = self._authentication_values(normalized)
        if normalized.get(STUDIO_REVIEW_INTERNAL_REQUEST_NONCE_HEADER) != request_nonce:
            raise StudioReviewInternalAuthError()
        if body_sha256 != hashlib.sha256(body).hexdigest():
            raise StudioReviewInternalAuthError()
        payload = _response_payload(
            status=status,
            path=path,
            content_type=content_type,
            body_sha256=body_sha256,
            timestamp=timestamp,
            nonce=nonce,
            request_nonce=request_nonce,
        )
        self._verify_signature(payload, signature)
        now = self._verify_time(timestamp)
        if not self._nonce_store.admit(
            direction="response",
            nonce=nonce,
            expires_at=timestamp + self._max_clock_skew_seconds,
            now=now,
        ):
            raise StudioReviewInternalAuthError()

    def _authentication_values(self, headers: Mapping[str, str]) -> tuple[int, str, str, str]:
        if headers.get(STUDIO_REVIEW_INTERNAL_PROTOCOL_HEADER) != STUDIO_REVIEW_INTERNAL_PROTOCOL:
            raise StudioReviewInternalAuthError()
        raw_timestamp = headers.get(STUDIO_REVIEW_INTERNAL_TIMESTAMP_HEADER, "")
        nonce = headers.get(STUDIO_REVIEW_INTERNAL_NONCE_HEADER, "")
        body_sha256 = headers.get(STUDIO_REVIEW_INTERNAL_BODY_SHA256_HEADER, "")
        signature = headers.get(STUDIO_REVIEW_INTERNAL_SIGNATURE_HEADER, "")
        if (
            not raw_timestamp.isdigit()
            or len(raw_timestamp) > 16
            or _NONCE_RE.fullmatch(nonce) is None
            or _SHA256_RE.fullmatch(body_sha256) is None
            or _SIGNATURE_RE.fullmatch(signature) is None
        ):
            raise StudioReviewInternalAuthError()
        return int(raw_timestamp), nonce, body_sha256, signature

    def _new_nonce(self) -> str:
        nonce = self._nonce_factory()
        if not isinstance(nonce, str) or _NONCE_RE.fullmatch(nonce) is None:
            raise StudioReviewInternalAuthError("Private review internal nonce generation failed.")
        return nonce

    def _verify_time(self, timestamp: int) -> int:
        now = int(self._clock())
        if abs(now - timestamp) > self._max_clock_skew_seconds:
            raise StudioReviewInternalAuthError()
        return now

    def _signature(self, payload: Mapping[str, object]) -> str:
        digest = hmac.new(self._secret, canonical_json_bytes(payload), hashlib.sha256).hexdigest()
        return f"hmac-sha256:{digest}"

    def _verify_signature(self, payload: Mapping[str, object], signature: str) -> None:
        matched = _SIGNATURE_RE.fullmatch(signature)
        if matched is None:
            raise StudioReviewInternalAuthError()
        expected = hmac.new(self._secret, canonical_json_bytes(payload), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(matched.group(1), expected):
            raise StudioReviewInternalAuthError()


def authorize_internal_studio_review_upload(headers: Mapping[str, str]) -> bool:
    """Authorize only the marker added after successful internal verification.

    Use this behind a transport that strips reserved ``x-viewspec-internal-*``
    headers from every external request before adapter dispatch.
    """

    return headers.get(STUDIO_REVIEW_INTERNAL_AUTHENTICATED_HEADER) == "true"


def _message_inputs(
    method: object,
    path: object,
    headers: object,
    body: object,
) -> tuple[str, str, dict[str, str], bytes]:
    if not isinstance(method, str) or _METHOD_RE.fullmatch(method) is None:
        raise StudioReviewInternalAuthError()
    if not isinstance(path, str) or not path.startswith("/") or len(path.encode("utf-8")) > 2048:
        raise StudioReviewInternalAuthError()
    if not isinstance(headers, Mapping) or not isinstance(body, bytes):
        raise StudioReviewInternalAuthError()
    return method, path, _normalize_headers(headers), body


def _response_inputs(
    status: object,
    path: object,
    content_type: object,
    body: object,
    request_nonce: object,
) -> tuple[int, str, str, bytes, str]:
    if not isinstance(status, int) or not 100 <= status <= 599:
        raise StudioReviewInternalAuthError()
    if not isinstance(path, str) or not path.startswith("/") or len(path.encode("utf-8")) > 2048:
        raise StudioReviewInternalAuthError()
    if (
        not isinstance(content_type, str)
        or not content_type
        or len(content_type.encode("latin-1")) > 256
        or _HEADER_CONTROL_RE.search(content_type) is not None
    ):
        raise StudioReviewInternalAuthError()
    if not isinstance(body, bytes) or not isinstance(request_nonce, str) or _NONCE_RE.fullmatch(request_nonce) is None:
        raise StudioReviewInternalAuthError()
    return status, path, content_type, body, request_nonce


def _normalize_headers(headers: Mapping[object, object]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    total = 0
    for raw_name, raw_value in headers.items():
        if not isinstance(raw_name, str) or not isinstance(raw_value, str):
            raise StudioReviewInternalAuthError()
        name = raw_name.lower()
        if _HEADER_NAME_RE.fullmatch(name) is None or name in normalized:
            raise StudioReviewInternalAuthError()
        if len(raw_value.encode("latin-1")) > 8192 or _HEADER_CONTROL_RE.search(raw_value) is not None:
            raise StudioReviewInternalAuthError()
        normalized[name] = raw_value
        total += len(name) + len(raw_value)
        if total > 16 * 1024:
            raise StudioReviewInternalAuthError()
    return normalized


def _forwarded_headers(headers: Mapping[str, str]) -> dict[str, str]:
    if not STUDIO_REVIEW_INTERNAL_FORWARDED_HEADERS.issubset(headers):
        raise StudioReviewInternalAuthError()
    return {name: headers[name] for name in sorted(STUDIO_REVIEW_INTERNAL_FORWARDED_HEADERS)}


def _request_payload(
    *,
    method: str,
    path: str,
    forwarded_headers: Mapping[str, str],
    body_sha256: str,
    timestamp: int,
    nonce: str,
) -> dict[str, object]:
    return {
        "schema_version": STUDIO_REVIEW_INTERNAL_SCHEMA_VERSION,
        "protocol": STUDIO_REVIEW_INTERNAL_PROTOCOL,
        "direction": "api_to_review_request",
        "method": method,
        "path": path,
        "headers": dict(forwarded_headers),
        "body_sha256": body_sha256,
        "timestamp": timestamp,
        "nonce": nonce,
    }


def _response_payload(
    *,
    status: int,
    path: str,
    content_type: str,
    body_sha256: str,
    timestamp: int,
    nonce: str,
    request_nonce: str,
) -> dict[str, object]:
    return {
        "schema_version": STUDIO_REVIEW_INTERNAL_SCHEMA_VERSION,
        "protocol": STUDIO_REVIEW_INTERNAL_PROTOCOL,
        "direction": "review_to_api_response",
        "status": status,
        "path": path,
        "content_type": content_type,
        "body_sha256": body_sha256,
        "timestamp": timestamp,
        "nonce": nonce,
        "request_nonce": request_nonce,
    }


__all__ = [
    "STUDIO_REVIEW_INTERNAL_AUTHENTICATED_HEADER",
    "STUDIO_REVIEW_INTERNAL_FORWARDED_HEADERS",
    "STUDIO_REVIEW_INTERNAL_INGRESS_PATH",
    "STUDIO_REVIEW_INTERNAL_PROTOCOL",
    "StudioReviewInternalAuth",
    "StudioReviewInternalAuthError",
    "StudioReviewInternalNonceStore",
    "VerifiedStudioReviewInternalRequest",
    "authorize_internal_studio_review_upload",
]
