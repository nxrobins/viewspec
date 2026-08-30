"""Production-gated publisher for one private ViewSpec Studio revision.

The local Studio product stays network-free unless a person explicitly starts it
with private sharing enabled.  Even then, Share is available only after the
canonical API returns a short-lived, Ed25519-signed release receipt proving that
the production private-review canary passed for the active deployment.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

from viewspec.hosted_receipts import ReceiptPublicKey, verify_signed_receipt
from viewspec.review_contract import ReviewContractError, canonical_json_bytes
from viewspec.studio_review_http import STUDIO_REVIEW_MEDIA_TYPE
from viewspec.studio_share import StudioShareError, load_studio_share_package, prepare_studio_share


STUDIO_SHARE_API_ORIGIN = "https://api.viewspec.dev"
STUDIO_SHARE_REVIEW_ORIGIN = "https://review.viewspec.dev"
STUDIO_SHARE_READINESS_PATH = "/v1/studio-share-readiness"
STUDIO_SHARE_CREATE_PATH = "/v1/reviews"
STUDIO_SHARE_RELEASE_SCHEMA_VERSION = 1
STUDIO_SHARE_RELEASE_KIND = "studio_share_release"
STUDIO_SHARE_RELEASE_MAX_LIFETIME_SECONDS = 60 * 60
STUDIO_SHARE_RELEASE_CLOCK_SKEW_SECONDS = 60
STUDIO_SHARE_HTTP_MAX_RESPONSE_BYTES = 256 * 1024
STUDIO_SHARE_EXPIRY_OPTIONS = (60 * 60, 24 * 60 * 60, 7 * 24 * 60 * 60)
STUDIO_SHARE_RELEASE_CHECKS = {
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

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID_RE = re.compile(r"^vsrcan_[0-9a-f]{32}$")
_SESSION_ID_RE = re.compile(r"^vsr_[A-Za-z0-9_-]{24}$")
_CAPABILITY_RE = re.compile(r"^vsc_[A-Za-z0-9_-]{16,256}$")
_RELEASE_FIELDS = {
    "schema_version",
    "kind",
    "status",
    "api_origin",
    "review_origin",
    "deployment_sha256",
    "verifier_id",
    "run_id",
    "report_sha256",
    "checks",
    "issued_at_epoch_s",
    "expires_at_epoch_s",
}

HTTPTransport = Callable[
    [str, str, Mapping[str, str], bytes | None, float],
    tuple[int, Mapping[str, str], bytes],
]


def _share_error(code: str, message: str, fix: str, *, http_status: int = 422) -> ReviewContractError:
    return ReviewContractError(code, message, fix, http_status=http_status, cli_exit=2)


def _canonical_origin(value: object, noun: str) -> str:
    if not isinstance(value, str):
        raise _share_error("STUDIO_SHARE_RELEASE_INVALID", f"{noun} is missing.", "Retry after the production gate is healthy.")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise _share_error(
            "STUDIO_SHARE_RELEASE_INVALID",
            f"{noun} is not one canonical HTTPS origin.",
            "Retry after the production gate is healthy.",
        )
    return f"https://{parsed.netloc}"


@dataclass(frozen=True, slots=True)
class StudioShareRelease:
    """One short-lived signed authorization to expose Studio's Share action."""

    api_origin: str
    review_origin: str
    deployment_sha256: str
    verifier_id: str
    run_id: str
    report_sha256: str
    issued_at_epoch_s: int
    expires_at_epoch_s: int
    receipt: Mapping[str, object]

    @classmethod
    def from_signed_receipt(
        cls,
        receipt: object,
        public_key: ReceiptPublicKey | Mapping[str, Any],
        *,
        expected_api_origin: str = STUDIO_SHARE_API_ORIGIN,
        expected_review_origin: str = STUDIO_SHARE_REVIEW_ORIGIN,
        now_epoch_s: int | None = None,
    ) -> StudioShareRelease:
        try:
            verified = verify_signed_receipt(receipt, public_key)
        except ImportError as exc:
            raise _share_error(
                "STUDIO_SHARE_REMOTE_DEPENDENCY_MISSING",
                "Private sharing requires the ViewSpec remote verification dependency.",
                "Install viewspec[remote], then restart Studio with --share.",
            ) from exc
        if not verified:
            raise _share_error(
                "STUDIO_SHARE_RELEASE_INVALID",
                "The Studio Share release receipt is unsigned or invalid.",
                "Wait for a valid production canary release receipt.",
            )
        if not isinstance(receipt, Mapping) or not isinstance(receipt.get("payload"), dict):
            raise _share_error(
                "STUDIO_SHARE_RELEASE_INVALID",
                "The Studio Share release receipt is malformed.",
                "Wait for a valid production canary release receipt.",
            )
        payload = receipt["payload"]
        assert isinstance(payload, dict)
        if set(payload) != _RELEASE_FIELDS:
            raise _share_error(
                "STUDIO_SHARE_RELEASE_INVALID",
                "The Studio Share release contract is incomplete or contains unknown fields.",
                "Update ViewSpec or wait for a compatible production release.",
            )
        api_origin = _canonical_origin(payload.get("api_origin"), "Studio Share API origin")
        review_origin = _canonical_origin(payload.get("review_origin"), "Studio Share review origin")
        if api_origin != _canonical_origin(expected_api_origin, "Expected Studio Share API origin"):
            raise _share_error(
                "STUDIO_SHARE_RELEASE_INVALID",
                "The Studio Share release names a different API origin.",
                "Use the canonical production API that issued the release.",
            )
        if review_origin != _canonical_origin(expected_review_origin, "Expected Studio Share review origin"):
            raise _share_error(
                "STUDIO_SHARE_RELEASE_INVALID",
                "The Studio Share release names a different review origin.",
                "Use the canonical private-review deployment that passed the canary.",
            )
        checks = payload.get("checks")
        if not isinstance(checks, dict) or set(checks) != STUDIO_SHARE_RELEASE_CHECKS or not all(
            value is True for value in checks.values()
        ):
            raise _share_error(
                "STUDIO_SHARE_RELEASE_INVALID",
                "The Studio Share release does not prove every production canary gate.",
                "Keep Share unavailable until the complete production canary passes.",
            )
        if (
            payload.get("schema_version") != STUDIO_SHARE_RELEASE_SCHEMA_VERSION
            or payload.get("kind") != STUDIO_SHARE_RELEASE_KIND
            or payload.get("status") != "active"
        ):
            raise _share_error(
                "STUDIO_SHARE_RELEASE_INVALID",
                "The Studio Share release identity or status is unsupported.",
                "Wait for an active compatible production release.",
            )
        deployment_sha256 = payload.get("deployment_sha256")
        report_sha256 = payload.get("report_sha256")
        verifier_id = payload.get("verifier_id")
        run_id = payload.get("run_id")
        if (
            not isinstance(deployment_sha256, str)
            or _SHA256_RE.fullmatch(deployment_sha256) is None
            or not isinstance(report_sha256, str)
            or _SHA256_RE.fullmatch(report_sha256) is None
            or verifier_id != "viewspec-studio-production-canary-v1"
            or not isinstance(run_id, str)
            or _RUN_ID_RE.fullmatch(run_id) is None
        ):
            raise _share_error(
                "STUDIO_SHARE_RELEASE_INVALID",
                "The Studio Share release is not bound to one verified production canary.",
                "Run and sign the complete canonical production canary.",
            )
        issued = payload.get("issued_at_epoch_s")
        expires = payload.get("expires_at_epoch_s")
        now = int(time.time()) if now_epoch_s is None else now_epoch_s
        if (
            type(issued) is not int
            or type(expires) is not int
            or type(now) is not int
            or issued < 0
            or expires <= issued
            or expires - issued > STUDIO_SHARE_RELEASE_MAX_LIFETIME_SECONDS
            or issued > now + STUDIO_SHARE_RELEASE_CLOCK_SKEW_SECONDS
            or expires <= now
        ):
            raise _share_error(
                "STUDIO_SHARE_RELEASE_EXPIRED",
                "The Studio Share production authorization is expired or outside its bounded lifetime.",
                "Refresh Studio after the production API publishes a current signed release.",
                http_status=409,
            )
        return cls(
            api_origin=api_origin,
            review_origin=review_origin,
            deployment_sha256=deployment_sha256,
            verifier_id=str(verifier_id),
            run_id=run_id,
            report_sha256=report_sha256,
            issued_at_epoch_s=issued,
            expires_at_epoch_s=expires,
            receipt=receipt,
        )

    def require_current(self, *, now_epoch_s: int | None = None) -> None:
        now = int(time.time()) if now_epoch_s is None else now_epoch_s
        if now >= self.expires_at_epoch_s:
            raise _share_error(
                "STUDIO_SHARE_RELEASE_EXPIRED",
                "The Studio Share production authorization expired before upload.",
                "Restart Studio with sharing enabled to obtain a current signed release.",
                http_status=409,
            )

    def browser_projection(self) -> dict[str, object]:
        return {
            "status": "available",
            "review_origin": self.review_origin,
            "deployment_sha256": self.deployment_sha256,
            "run_id": self.run_id,
            "report_sha256": self.report_sha256,
            "expires_at_epoch_s": self.expires_at_epoch_s,
        }


def fetch_studio_share_release(
    *,
    api_key: str,
    api_origin: str = STUDIO_SHARE_API_ORIGIN,
    review_origin: str = STUDIO_SHARE_REVIEW_ORIGIN,
    public_key: ReceiptPublicKey | Mapping[str, Any] | None = None,
    now_epoch_s: int | None = None,
    transport: HTTPTransport | None = None,
) -> StudioShareRelease:
    """Check this account's eligibility and verify its active production release."""

    _require_api_key(api_key)
    api = _canonical_origin(api_origin, "Studio Share API origin")
    request = transport or _http_transport
    if public_key is None:
        key_payload = _json_get(request, f"{api}/v1/receipt-key", noun="Studio receipt key")
        try:
            public_key = ReceiptPublicKey.from_json(key_payload)
        except ValueError as exc:
            raise _share_error(
                "STUDIO_SHARE_RELEASE_INVALID",
                "The production receipt key is invalid.",
                "Retry after the canonical API publishes a valid receipt key.",
                http_status=502,
            ) from exc
    readiness = _json_get(request, f"{api}{STUDIO_SHARE_READINESS_PATH}", noun="Studio Share readiness",
                          headers={"Authorization": f"Bearer {api_key}"})
    if set(readiness) != {"schema_version", "release"} or readiness.get("schema_version") != 1:
        raise _share_error(
            "STUDIO_SHARE_RELEASE_INVALID",
            "The production Share readiness response is incompatible.",
            "Keep Share unavailable until the canonical readiness contract is healthy.",
            http_status=502,
        )
    return StudioShareRelease.from_signed_receipt(
        readiness["release"],
        public_key,
        expected_api_origin=api,
        expected_review_origin=review_origin,
        now_epoch_s=now_epoch_s,
    )


class StudioSharePublisher:
    """Prepare locally, disclose exactly, then upload one immutable revision."""

    def __init__(
        self,
        *,
        release: StudioShareRelease,
        api_key: str,
        source: str | Path,
        state_root: str | Path,
        cwd: str | Path,
        reference: str | Path | None = None,
        transport: HTTPTransport | None = None,
        now_epoch_s: Callable[[], int] | None = None,
    ) -> None:
        if not isinstance(release, StudioShareRelease):
            raise TypeError("Studio Share publisher requires a verified release")
        _require_api_key(api_key)
        self.release = release
        self._api_key = api_key
        self._source = Path(source)
        self._state_root = Path(state_root)
        self._cwd = Path(cwd)
        self._reference = Path(reference) if reference is not None else None
        self._transport = transport or _http_transport
        self._now = now_epoch_s or (lambda: int(time.time()))
        # Publishing refreshes the exact package under the same critical section
        # before it uploads, so the lock must permit that deliberate re-entry.
        self._lock = threading.RLock()
        self._prepared: dict[str, object] | None = None

    def status(self) -> dict[str, object]:
        self.release.require_current(now_epoch_s=self._now())
        return self.release.browser_projection()

    def prepare(self) -> dict[str, object]:
        """Prepare and project the exact disclosure without sending network bytes."""

        with self._lock:
            self.release.require_current(now_epoch_s=self._now())
            try:
                prepared = prepare_studio_share(
                    self._source,
                    reference=self._reference,
                    state_root=self._state_root,
                    cwd=self._cwd,
                )
            except StudioShareError as exc:
                raise ReviewContractError(
                    exc.code,
                    exc.message,
                    exc.fix,
                    http_status=409,
                    cli_exit=exc.cli_exit,
                ) from exc
            package = Path(str(prepared["paths"]["package"]))
            envelope = load_studio_share_package(package)
            self._prepared = {"result": prepared, "envelope": envelope}
            disclosure = envelope["disclosure"]
            assert isinstance(disclosure, dict)
            return {
                "schema_version": 1,
                "status": "awaiting_confirmation",
                "package_id": envelope["package_id"],
                "revision": envelope["revision"]["number"],
                "file_count": envelope["totals"]["file_count"],
                "bytes": envelope["totals"]["bytes"],
                "disclosure": disclosure,
                "expiry_options": list(STUDIO_SHARE_EXPIRY_OPTIONS),
                "release": self.release.browser_projection(),
                "upload_performed": False,
            }

    def publish(
        self,
        *,
        package_id: str,
        disclosure_accepted: bool,
        expires_in_seconds: int,
    ) -> dict[str, object]:
        """Upload only after exact package confirmation and return bounded private links."""

        with self._lock:
            self.release.require_current(now_epoch_s=self._now())
            if disclosure_accepted is not True:
                raise _share_error(
                    "STUDIO_SHARE_DISCLOSURE_REQUIRED",
                    "Private review creation requires deliberate disclosure acceptance.",
                    "Read the exact inventory and confirm before creating a private link.",
                )
            if type(expires_in_seconds) is not int or expires_in_seconds not in STUDIO_SHARE_EXPIRY_OPTIONS:
                raise _share_error(
                    "STUDIO_SHARE_EXPIRY_INVALID",
                    "Private review expiry is outside the supported bound.",
                    "Choose one of the expiry options shown by Studio.",
                )
            fresh = self.prepare()
            if not isinstance(package_id, str) or package_id != fresh["package_id"]:
                raise _share_error(
                    "STUDIO_SHARE_REVISION_STALE",
                    "The confirmed private-review package is no longer the exact current Studio revision.",
                    "Review the refreshed disclosure before creating a link.",
                    http_status=409,
                )
            assert self._prepared is not None
            prepared = self._prepared["result"]
            envelope = self._prepared["envelope"]
            assert isinstance(prepared, dict) and isinstance(envelope, dict)
            archive = Path(str(prepared["paths"]["upload_archive"]))
            archive_bytes = archive.read_bytes()
            archive_sha256 = hashlib.sha256(archive_bytes).hexdigest()
            request_identity = {
                "package_id": package_id,
                "expires_in_seconds": expires_in_seconds,
                "deployment_sha256": self.release.deployment_sha256,
            }
            idempotency_key = "vss_" + hashlib.sha256(canonical_json_bytes(request_identity)).hexdigest()[:40]
            status, _headers, body = self._transport(
                "POST",
                f"{self.release.api_origin}{STUDIO_SHARE_CREATE_PATH}",
                {
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": STUDIO_REVIEW_MEDIA_TYPE,
                    "Idempotency-Key": idempotency_key,
                    "X-ViewSpec-Disclosure-Accepted": "true",
                    "X-ViewSpec-Expiry-Seconds": str(expires_in_seconds),
                },
                archive_bytes,
                150.0,
            )
            response = _json_response(status, body, noun="Studio private-review creation")
            if status != 201:
                raise _remote_error(response, status=status)
            return _validate_creation_response(
                response,
                envelope=envelope,
                archive_sha256=archive_sha256,
                archive_bytes=len(archive_bytes),
                release=self.release,
                requested_expiry_seconds=expires_in_seconds,
                now_epoch_s=self._now(),
            )


def _require_api_key(api_key: object) -> None:
    if not isinstance(api_key, str) or re.fullmatch(r"[\x21-\x7e]{1,512}", api_key) is None:
        raise _share_error(
            "STUDIO_SHARE_AUTH_REQUIRED",
            "Private sharing requires one bounded API credential.",
            "Set VIEWSPEC_STUDIO_API_KEY before starting Studio with --share.",
            http_status=401,
        )


def _json_get(request: HTTPTransport, url: str, *, noun: str, headers: Mapping[str, str] | None = None) -> dict[str, object]:
    status, _headers, body = request("GET", url, headers or {}, None, 15.0)
    payload = _json_response(status, body, noun=noun)
    if status != 200:
        if noun == "Studio Share readiness" and status in {401, 403}:
            raise _share_error(
                "STUDIO_SHARE_AUTH_REQUIRED" if status == 401 else "STUDIO_SHARE_NOT_ELIGIBLE",
                "Private sharing is not available for this account.",
                "Use an active paid API credential admitted to the private sharing beta, or continue locally without --share.",
                http_status=status,
            )
        raise _remote_error(payload, status=status)
    return payload


def _json_response(status: int, body: bytes, *, noun: str) -> dict[str, object]:
    if type(status) is not int or not 100 <= status <= 599 or not 1 <= len(body) <= STUDIO_SHARE_HTTP_MAX_RESPONSE_BYTES:
        raise _share_error(
            "STUDIO_SHARE_REMOTE_INVALID",
            f"{noun} returned an invalid bounded response.",
            "Retry after the production private-review service is healthy.",
            http_status=502,
        )
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _share_error(
            "STUDIO_SHARE_REMOTE_INVALID",
            f"{noun} did not return strict JSON.",
            "Retry after the production private-review service is healthy.",
            http_status=502,
        ) from exc
    if not isinstance(value, dict):
        raise _share_error(
            "STUDIO_SHARE_REMOTE_INVALID",
            f"{noun} did not return one JSON object.",
            "Retry after the production private-review service is healthy.",
            http_status=502,
        )
    return value


def _remote_error(payload: Mapping[str, object], *, status: int) -> ReviewContractError:
    error = payload.get("error")
    code = error.get("code") if isinstance(error, Mapping) else None
    return _share_error(
        "STUDIO_SHARE_REMOTE_REJECTED",
        f"The private-review service rejected link creation ({code or status}).",
        "Keep the local package and retry after checking private-review service health.",
        http_status=502 if status >= 500 else status,
    )


def _validate_creation_response(
    payload: Mapping[str, object],
    *,
    envelope: Mapping[str, object],
    archive_sha256: str,
    archive_bytes: int,
    release: StudioShareRelease,
    requested_expiry_seconds: int,
    now_epoch_s: int,
) -> dict[str, object]:
    session = payload.get("session")
    verification = payload.get("verification")
    ingress = payload.get("ingress")
    links = payload.get("links")
    revision = envelope.get("revision")
    if not all(isinstance(item, dict) for item in (session, verification, ingress, links, revision)):
        raise _share_error(
            "STUDIO_SHARE_REMOTE_INVALID",
            "Private-review creation omitted its exact session, verification, ingress, or links.",
            "Retry after the production private-review service is healthy.",
            http_status=502,
        )
    assert isinstance(session, dict)
    assert isinstance(verification, dict)
    assert isinstance(ingress, dict)
    assert isinstance(links, dict)
    assert isinstance(revision, dict)
    session_id = session.get("id")
    expires_at = session.get("expires_at")
    if (
        not isinstance(session_id, str)
        or _SESSION_ID_RE.fullmatch(session_id) is None
        or session.get("package_id") != envelope.get("package_id")
        or session.get("private") is not True
        or payload.get("status") != "active"
        or ingress.get("archive_sha256") != archive_sha256
        or ingress.get("archive_bytes") != archive_bytes
        or type(expires_at) is not int
        or not now_epoch_s < expires_at <= now_epoch_s + requested_expiry_seconds + STUDIO_SHARE_RELEASE_CLOCK_SKEW_SECONDS
    ):
        raise _share_error(
            "STUDIO_SHARE_REMOTE_INVALID",
            "Private-review creation does not match the uploaded immutable package.",
            "Do not use the returned links; retry after the production service is healthy.",
            http_status=502,
        )
    expected_verification = {
        "status": "passed",
        "package_id": envelope.get("package_id"),
        "source_sha256": revision.get("source_sha256"),
        "artifact_set_sha256": revision.get("artifact_set_sha256"),
        "root_manifest_sha256": revision.get("root_manifest_sha256"),
        "inspection_sha256": revision.get("inspection_sha256"),
        "target_artifact_sets": revision.get("target_artifact_sets"),
    }
    if any(verification.get(key) != value for key, value in expected_verification.items()):
        raise _share_error(
            "STUDIO_SHARE_REMOTE_INVALID",
            "Private-review verification does not reproduce the local checked revision.",
            "Do not use the returned links; investigate the remote rebuild mismatch.",
            http_status=502,
        )
    if links.get("transport") != "url_fragment_one_time_exchange" or set(links) != {
        "owner",
        "reviewer",
        "transport",
    }:
        raise _share_error(
            "STUDIO_SHARE_REMOTE_INVALID",
            "Private-review creation returned an unsupported capability transport.",
            "Do not use the returned links.",
            http_status=502,
        )
    owner = _private_link(links["owner"], role="owner", session_id=session_id, release=release)
    reviewer = _private_link(links["reviewer"], role="reviewer", session_id=session_id, release=release)
    return {
        "schema_version": 1,
        "status": "active",
        "session_id": session_id,
        "package_id": envelope["package_id"],
        "expires_at": expires_at,
        "owner_url": owner,
        "reviewer_url": reviewer,
        "review_origin": release.review_origin,
        "deployment_sha256": release.deployment_sha256,
        "upload_performed": True,
        "private": True,
    }


def _private_link(value: object, *, role: str, session_id: str, release: StudioShareRelease) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise _share_error(
            "STUDIO_SHARE_REMOTE_INVALID",
            f"Private-review {role} link is missing or oversized.",
            "Do not use the returned links.",
            http_status=502,
        )
    parsed = urlsplit(value)
    expected = urlsplit(release.review_origin)
    try:
        fragment = parse_qs(parsed.fragment, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise _share_error(
            "STUDIO_SHARE_REMOTE_INVALID",
            f"Private-review {role} link has an invalid capability fragment.",
            "Do not use the returned links.",
            http_status=502,
        ) from exc
    capabilities = fragment.get("cap")
    if (
        parsed.scheme != "https"
        or parsed.netloc != expected.netloc
        or parsed.path != f"/review/{session_id}/"
        or parsed.query
        or parsed.username is not None
        or parsed.password is not None
        or set(fragment) != {"cap"}
        or not isinstance(capabilities, list)
        or len(capabilities) != 1
        or _CAPABILITY_RE.fullmatch(capabilities[0]) is None
    ):
        raise _share_error(
            "STUDIO_SHARE_REMOTE_INVALID",
            f"Private-review {role} link is not bound to the verified private origin and session.",
            "Do not use the returned links.",
            http_status=502,
        )
    return value


def _http_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout: float,
) -> tuple[int, Mapping[str, str], bytes]:
    try:
        import httpx
    except ImportError:
        raise _share_error(
            "STUDIO_SHARE_REMOTE_DEPENDENCY_MISSING",
            "Private sharing requires the ViewSpec remote client dependency.",
            "Install viewspec[remote], then restart Studio with --share.",
        ) from None
    try:
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            with client.stream(method, url, headers={**headers, "Accept-Encoding": "identity"}, content=body, timeout=timeout) as response:
                content = bytearray()
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise _share_error("STUDIO_SHARE_REMOTE_INVALID", "Encoded private-review responses are not accepted.",
                                       "Retry after the production service is healthy.", http_status=502)
                for chunk in response.iter_raw():
                    if len(content) + len(chunk) > STUDIO_SHARE_HTTP_MAX_RESPONSE_BYTES:
                        raise _share_error(
                            "STUDIO_SHARE_REMOTE_INVALID",
                            "The production private-review response exceeds its bounded size.",
                            "Keep the local package and retry when the service is healthy.",
                            http_status=502,
                        )
                    content.extend(chunk)
                return int(response.status_code), dict(response.headers), bytes(content)
    except httpx.HTTPError as exc:
        raise _share_error(
            "STUDIO_SHARE_REMOTE_UNAVAILABLE",
            "The production private-review service could not be reached.",
            "Keep the local package and retry when the service is healthy.",
            http_status=502,
        ) from exc


__all__ = [
    "STUDIO_SHARE_API_ORIGIN",
    "STUDIO_SHARE_EXPIRY_OPTIONS",
    "STUDIO_SHARE_RELEASE_CHECKS",
    "STUDIO_SHARE_REVIEW_ORIGIN",
    "StudioSharePublisher",
    "StudioShareRelease",
    "fetch_studio_share_release",
]
