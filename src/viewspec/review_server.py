"""Capability-scoped loopback HTTP server for local ViewSpec Review V0."""

from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
import hmac
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import stat
import threading
import time
from typing import Callable, Protocol
from urllib.parse import unquote_to_bytes, urlsplit

from viewspec.converge_sessions import (
    ConvergeError,
    ConvergenceSession,
    approve_convergence_preview,
    get_convergence_status,
    reject_convergence_preview,
)
from viewspec.review_contract import ReviewContext, ReviewContractError, canonical_json_bytes
from viewspec.review_compile import STUDIO_COMPARE_TARGET
from viewspec.review_errors import make_review_error
from viewspec.review_runtime import ReviewRuntime


MAX_REQUEST_BODY_BYTES = 256 * 1024
MAX_JSON_RESPONSE_BYTES = 256 * 1024
MAX_REQUEST_URI_BYTES = 2 * 1024
MAX_REQUEST_HEADERS = 64
MAX_REQUEST_HEADER_BYTES = 16 * 1024
MAX_SINGLE_HEADER_BYTES = 8 * 1024
STREAM_CHUNK_BYTES = 64 * 1024
BOOTSTRAP_LIFETIME_SECONDS = 60
COOKIE_MAX_AGE_SECONDS = 8 * 60 * 60
COOKIE_IDLE_SECONDS = 30 * 60
FRAME_TICKET_LIFETIME_SECONDS = 5 * 60
FRAME_HANDSHAKE_SECONDS = 5
AUTO_EXIT_GRACE_SECONDS = 5
SESSION_IDLE_SECONDS = 30 * 60
MAX_POLL_TIMEOUT_MS = 55_000
_COOKIE_NAME = "viewspec_review"
_SAFE_FRAME_SEGMENT = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True, slots=True)
class _ArtifactEntry:
    path: Path
    size: int
    sha256: str
    content_type: str


@dataclass(frozen=True, slots=True)
class _Response:
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes


class _StudioSharePublisher(Protocol):
    def status(self) -> dict[str, object]: ...

    def prepare(self) -> dict[str, object]: ...

    def publish(
        self,
        *,
        package_id: str,
        disclosure_accepted: bool,
        expires_in_seconds: int,
    ) -> dict[str, object]: ...


class ReviewServer:
    """One bounded HTTP server bound only to the literal IPv4 loopback address."""

    def __init__(
        self,
        runtime: ReviewRuntime,
        *,
        host: str = "127.0.0.1",
        port: int = 4388,
        clock: Callable[[], float] = time.monotonic,
        share_publisher: _StudioSharePublisher | None = None,
    ) -> None:
        if host != "127.0.0.1":
            raise ReviewContractError(
                "REVIEW_NON_LOOPBACK_FORBIDDEN",
                "Review V0 binds only to the literal address 127.0.0.1.",
                "Use the default IPv4 loopback address.",
                cli_exit=2,
            )
        if not isinstance(runtime, ReviewRuntime):
            raise TypeError("runtime must be a ReviewRuntime")
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ReviewContractError(
                "REVIEW_PORT_UNAVAILABLE",
                "Review port must be an integer from 1024 through 65535.",
                "Use the default port 4388 or one explicit unprivileged local port.",
                cli_exit=2,
            )
        self.runtime = runtime
        self.host = host
        self._clock = clock
        self._share_publisher = share_publisher
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._poll_lock = threading.Lock()
        self._connection_slots = threading.BoundedSemaphore(32)
        browser_slots = 4 if runtime.built.revision.target == STUDIO_COMPARE_TARGET else 2
        self._browser_connection_slots = threading.BoundedSemaphore(browser_slots)
        self._active_mutations = 0
        self._active_polls = 0
        self._last_authenticated_activity = clock()
        self.last_response_error: str | None = None
        self._thread: threading.Thread | None = None
        self.agent_token = _token()
        self._agent_digest = _digest_token(self.agent_token)
        self._bootstrap_token = _token()
        self._bootstrap_digest = _digest_token(self._bootstrap_token)
        self._bootstrap_expires = clock() + BOOTSTRAP_LIFETIME_SECONDS
        self._bootstrap_consumed = False
        self._cookie_digest: bytes | None = None
        self._ended_cookie_digest: bytes | None = None
        self._cookie_expires = 0.0
        self._cookie_last_activity = 0.0
        self._capability_revision = 0
        self._frame_ticket = ""
        self._frame_ticket_digest = b""
        self._frame_ticket_expires = 0.0
        self.frame_nonce = ""
        self._frame_first_served_at: float | None = None
        self._handshake_revision: int | None = None
        self._allowlist: dict[str, _ArtifactEntry] = {}
        try:
            self._httpd = ThreadingHTTPServer((host, port), self._handler_type())
        except OSError as exc:
            raise ReviewContractError(
                "REVIEW_PORT_UNAVAILABLE",
                f"Could not bind the local Review port: {exc}",
                "Choose one available unprivileged local port.",
                cli_exit=2,
            ) from exc
        self._httpd.daemon_threads = True
        self.port = int(self._httpd.server_address[1])
        self.origin = f"http://127.0.0.1:{self.port}"
        self._rotate_revision_capabilities()

    @property
    def bootstrap_path(self) -> str:
        return f"/open/{self._bootstrap_token}"

    @property
    def bootstrap_url(self) -> str:
        return f"{self.origin}{self.bootstrap_path}"

    def frame_path(self, relative_path: str) -> str:
        self._ensure_revision_capabilities()
        return f"/frame/{self._frame_ticket}/{self.runtime.built.revision.number}/{relative_path}"

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="viewspec-review", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            self._httpd.server_close()
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)
        self._thread = None

    def serve_forever(self) -> None:
        try:
            self._httpd.serve_forever()
        finally:
            self._httpd.server_close()

    def notify_state_changed(self) -> None:
        with self._condition:
            self._condition.notify_all()

    def status(self) -> dict[str, object]:
        """Return the bounded agent projection used for daemon readiness."""

        return self._agent_status()

    @property
    def should_auto_exit(self) -> bool:
        with self._lock:
            return (
                self.runtime.session.ended_by is not None
                and self._active_mutations == 0
                and self._active_polls == 0
                and self._clock() - self._last_authenticated_activity >= AUTO_EXIT_GRACE_SECONDS
            )

    @property
    def should_suspend(self) -> bool:
        with self._lock:
            return (
                self.runtime.session.ended_by is None
                and self._active_mutations == 0
                and self._active_polls == 0
                and self._clock() - self._last_authenticated_activity >= SESSION_IDLE_SECONDS
            )

    @property
    def browser_ready(self) -> bool:
        with self._lock:
            return self._handshake_revision == self.runtime.built.revision.number

    def reset_bootstrap(self) -> str:
        with self._lock:
            self._bootstrap_token = _token()
            self._bootstrap_digest = _digest_token(self._bootstrap_token)
            self._bootstrap_expires = self._clock() + BOOTSTRAP_LIFETIME_SECONDS
            self._bootstrap_consumed = False
            return self.bootstrap_url

    def _handler_type(self) -> type[BaseHTTPRequestHandler]:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self) -> None:
                super().setup()
                self.connection.settimeout(5)

            def do_GET(self) -> None:  # noqa: N802
                outer._dispatch(self, mutation=False)

            def do_POST(self) -> None:  # noqa: N802
                outer._dispatch(self, mutation=True)

            def log_message(self, format: str, *args: object) -> None:
                del format, args

        return Handler

    def _dispatch(self, handler: BaseHTTPRequestHandler, *, mutation: bool) -> None:
        if not self._connection_slots.acquire(blocking=False):
            self._send(handler, _busy_response("Review server already has 32 active connections."))
            return
        browser_slot = False
        try:
            if not handler.path.startswith("/internal/v1/"):
                if not self._browser_connection_slots.acquire(blocking=False):
                    self._send(handler, _busy_response("Review session already has 2 active browser requests."))
                    return
                browser_slot = True
            try:
                response = self._handle(handler, mutation=mutation)
            except ReviewContractError as exc:
                response = _error_response(exc)
            except Exception:
                response = _error_response(
                    ReviewContractError(
                        "REVIEW_SERVER_START_FAILED",
                        "Review server failed while handling the bounded local request.",
                        "Retry the request or restart the local review server.",
                        http_status=500,
                        cli_exit=1,
                    )
                )
            self._send(handler, response)
        finally:
            if browser_slot:
                self._browser_connection_slots.release()
            self._connection_slots.release()

    def _handle(self, handler: BaseHTTPRequestHandler, *, mutation: bool) -> _Response:
        if len(handler.path.encode("utf-8", errors="replace")) > MAX_REQUEST_URI_BYTES:
            raise _http_error(414, "REVIEW_REQUEST_URI_TOO_LONG", "Review request URI exceeds 2 KiB.")
        self._validate_headers(handler)
        if handler.headers.get("Host") != f"127.0.0.1:{self.port}":
            raise _forbidden("Request Host does not match the bound loopback origin.")
        split = urlsplit(handler.path)
        if split.scheme or split.netloc:
            raise _forbidden("Absolute-form request targets are unsupported.")
        path = split.path
        if mutation:
            if path.startswith("/internal/v1/"):
                self._authorize_agent(handler)
            else:
                self._authorize_browser_mutation(handler, allow_ended_retry=path.endswith("/api/v1/end"))
            with self._lock:
                self._active_mutations += 1
                self._last_authenticated_activity = self._clock()
            try:
                body = self._read_body(handler)
                if path.startswith("/internal/v1/"):
                    return self._handle_agent_post(path, body)
                return self._handle_post(path, body, handler)
            finally:
                with self._lock:
                    self._active_mutations -= 1
                    self._last_authenticated_activity = self._clock()
        return self._handle_get(path, handler)

    def _handle_get(self, path: str, handler: BaseHTTPRequestHandler) -> _Response:
        if path == "/internal/v1/status":
            self._authorize_agent(handler)
            return _json_response(200, {"schema_version": 1, "ok": True, "review": self._agent_status()})
        if path.startswith("/open/"):
            return self._consume_bootstrap(path.removeprefix("/open/"))
        if path.startswith("/frame/"):
            return self._serve_frame(path)
        review_root = f"/r/{self.runtime.session.review_id}/"
        if path == review_root:
            self._authorize_cookie(handler)
            return self._chrome_response()
        if path == f"{review_root}api/v1/session":
            self._authorize_cookie(handler)
            return _json_response(200, {"schema_version": 1, "ok": True, "review": self._browser_status()})
        if path == f"{review_root}api/v1/inspection":
            self._authorize_cookie(handler)
            if self.runtime.inspection is None:
                raise _artifact_not_found()
            return _json_response(
                200,
                {"schema_version": 1, "ok": True, "inspection": self.runtime.inspection},
            )
        if path == f"{review_root}api/v1/events":
            self._authorize_cookie(handler)
            events = [event.to_json() for event in self.runtime.session.events]
            return _json_response(200, {"schema_version": 1, "ok": True, "events": events})
        raise _artifact_not_found()

    def _handle_post(self, path: str, body: bytes, handler: BaseHTTPRequestHandler) -> _Response:
        review_root = f"/r/{self.runtime.session.review_id}/"
        if path == f"{review_root}api/v1/handshake":
            payload = _json_object(body)
            if set(payload) != {"targets"}:
                raise _http_error(
                    400,
                    "REVIEW_REQUEST_INVALID",
                    "Browser handshake must contain exact target observations.",
                )
            self._complete_frame_handshake(payload["targets"])
            return _json_response(
                200,
                {
                    "schema_version": 1,
                    "ok": True,
                    "revision": self.runtime.built.revision.number,
                },
            )
        if path == f"{review_root}api/v1/share/prepare":
            self._require_frame_handshake()
            payload = _json_object(body)
            if payload:
                raise _http_error(400, "REVIEW_REQUEST_INVALID", "Studio Share preparation requires an empty object.")
            publisher = self._require_share_publisher()
            return _json_response(
                200,
                {"schema_version": 1, "ok": True, "share": publisher.prepare()},
            )
        if path == f"{review_root}api/v1/share/publish":
            self._require_frame_handshake()
            payload = _json_object(body)
            if set(payload) != {"package_id", "disclosure_accepted", "expires_in_seconds"}:
                raise _http_error(
                    400,
                    "REVIEW_REQUEST_INVALID",
                    "Studio Share creation requires the exact confirmed package, disclosure, and expiry.",
                )
            publisher = self._require_share_publisher()
            result = publisher.publish(
                package_id=payload["package_id"],
                disclosure_accepted=payload["disclosure_accepted"],
                expires_in_seconds=payload["expires_in_seconds"],
            )
            return _json_response(201, {"schema_version": 1, "ok": True, "share": result})
        if path == f"{review_root}api/v1/events":
            self._require_frame_handshake()
            payload = _json_object(body)
            allowed = {
                "kind",
                "body",
                "screen_id",
                "dom_ancestors",
                "page_level",
                "context",
                "client_provenance",
            }
            if set(payload) - allowed:
                raise _http_error(400, "REVIEW_REQUEST_INVALID", "Review event request contains unknown fields.")
            ancestors = payload.get("dom_ancestors")
            if (
                not isinstance(ancestors, list)
                or len(ancestors) > 32
                or not all(isinstance(item, str) and len(item.encode("utf-8")) <= 256 for item in ancestors)
                or type(payload.get("page_level")) is not bool
            ):
                raise _http_error(400, "REVIEW_REQUEST_INVALID", "Review event target hint is invalid or oversized.")
            with self._condition:
                event = self.runtime.submit_browser_event(
                    idempotency_key=handler.headers.get("Idempotency-Key", ""),
                    kind=payload.get("kind"),
                    body=payload.get("body"),
                    screen_id=payload.get("screen_id"),
                    dom_ancestors=tuple(ancestors),
                    page_level=payload["page_level"],
                    context=ReviewContext.from_json(payload.get("context")),
                    client_provenance=payload.get("client_provenance")
                    if isinstance(payload.get("client_provenance"), dict)
                    else None,
                )
                self._condition.notify_all()
            return _json_response(201, {"schema_version": 1, "ok": True, "event": event.to_json()})
        if path == f"{review_root}api/v1/convergence/approve":
            self._require_frame_handshake()
            preview_id = _exact_preview_request(body)
            try:
                current = get_convergence_status(
                    self.runtime.configuration.source_path,
                    state_root=self.runtime.configuration.convergence_state_root,
                )
                pending = current.pending_preview
                if pending is None or pending.preview_id != preview_id:
                    raise ConvergeError(
                        "CONVERGE_PREVIEW_INVALID",
                        "The approved preview is no longer the exact pending proposal.",
                        "Reload Review and inspect the current convergence proposal.",
                    )
                session = approve_convergence_preview(
                    self.runtime.configuration.source_path,
                    pending.approval_token,
                    state_root=self.runtime.configuration.convergence_state_root,
                )
            except ConvergeError as exc:
                raise _convergence_review_error(exc) from exc
            return _json_response(
                200,
                {
                    "schema_version": 1,
                    "ok": True,
                    "convergence": _browser_convergence_projection(session),
                },
            )
        if path == f"{review_root}api/v1/convergence/reject":
            self._require_frame_handshake()
            preview_id = _exact_preview_request(body)
            try:
                session = reject_convergence_preview(
                    self.runtime.configuration.source_path,
                    preview_id,
                    state_root=self.runtime.configuration.convergence_state_root,
                )
            except ConvergeError as exc:
                raise _convergence_review_error(exc) from exc
            return _json_response(
                200,
                {
                    "schema_version": 1,
                    "ok": True,
                    "convergence": _browser_convergence_projection(session),
                },
            )
        if path == f"{review_root}api/v1/end":
            self._require_frame_handshake()
            payload = _json_object(body)
            allowed = {
                "actor",
                "kind",
                "body",
                "screen_id",
                "dom_ancestors",
                "page_level",
                "context",
                "client_provenance",
            }
            ancestors = payload.get("dom_ancestors")
            if (
                set(payload) != allowed
                or payload.get("actor") != "human"
                or not isinstance(ancestors, list)
                or len(ancestors) > 32
                or not all(isinstance(item, str) and len(item.encode("utf-8")) <= 256 for item in ancestors)
                or type(payload.get("page_level")) is not bool
            ):
                raise _http_error(400, "REVIEW_REQUEST_INVALID", "Browser Send & End request is invalid or incomplete.")
            with self._condition:
                event = self.runtime.submit_browser_event_and_end(
                    idempotency_key=handler.headers.get("Idempotency-Key", ""),
                    kind=payload.get("kind"),
                    body=payload.get("body"),
                    screen_id=payload.get("screen_id"),
                    dom_ancestors=tuple(ancestors),
                    page_level=payload["page_level"],
                    context=ReviewContext.from_json(payload.get("context")),
                    client_provenance=payload.get("client_provenance")
                    if isinstance(payload.get("client_provenance"), dict)
                    else None,
                )
                with self._lock:
                    self._ended_cookie_digest = self._cookie_digest
                    self._cookie_digest = None
                    self._frame_ticket_digest = b""
                    self._frame_ticket_expires = 0.0
                self._condition.notify_all()
            return _json_response(
                200,
                {"schema_version": 1, "ok": True, "ended_by": "human", "event": event.to_json()},
            )
        raise _artifact_not_found()

    def _handle_agent_post(self, path: str, body: bytes) -> _Response:
        payload = _json_object(body)
        if path == "/internal/v1/poll":
            allowed = {"ack_batch_id", "agent_reply", "timeout_ms"}
            if set(payload) != allowed:
                raise _http_error(400, "REVIEW_REQUEST_INVALID", "Agent poll requires the exact V0 request fields.")
            timeout_ms = payload.get("timeout_ms")
            if type(timeout_ms) is not int or not 1 <= timeout_ms <= MAX_POLL_TIMEOUT_MS:
                raise _http_error(400, "REVIEW_REQUEST_INVALID", "Poll timeout_ms must be from 1 through 55000.")
            ack = payload.get("ack_batch_id")
            reply = payload.get("agent_reply")
            if ack is not None and not isinstance(ack, str):
                raise _http_error(400, "REVIEW_REQUEST_INVALID", "Poll acknowledgement must be a batch id or null.")
            if reply is not None and not isinstance(reply, str):
                raise _http_error(400, "REVIEW_REQUEST_INVALID", "Agent reply must be text or null.")
            return self._agent_poll(ack_batch_id=ack, agent_reply=reply, timeout_ms=timeout_ms)
        if path == "/internal/v1/end":
            if payload:
                raise _http_error(400, "REVIEW_REQUEST_INVALID", "Agent end request body must be an empty object.")
            with self._condition:
                self.runtime.session.end(actor="agent")
                self._condition.notify_all()
            return _json_response(200, {"schema_version": 1, "ok": True, "status": "ended", "ended_by": "agent"})
        if path == "/internal/v1/bootstrap":
            if payload:
                raise _http_error(400, "REVIEW_REQUEST_INVALID", "Bootstrap refresh body must be an empty object.")
            return _json_response(
                200,
                {"schema_version": 1, "ok": True, "bootstrap_url": self.reset_bootstrap()},
            )
        if path == "/internal/v1/reopen":
            if set(payload) != {"allow_human"} or type(payload.get("allow_human")) is not bool:
                raise _http_error(400, "REVIEW_REQUEST_INVALID", "Reopen request requires one allow_human boolean.")
            actor = self.runtime.session.ended_by
            if actor == "human" and payload["allow_human"] is not True:
                raise ReviewContractError(
                    "REVIEW_SESSION_ENDED_BY_HUMAN",
                    "The reviewer ended this Review session.",
                    "Reopen only after the user explicitly requests it.",
                    http_status=409,
                )
            with self._condition:
                self.runtime.session.reopen()
                self._condition.notify_all()
            return _json_response(200, {"schema_version": 1, "ok": True, "status": "active"})
        raise _artifact_not_found()

    def _agent_poll(self, *, ack_batch_id: str | None, agent_reply: str | None, timeout_ms: int) -> _Response:
        if not self._poll_lock.acquire(blocking=False):
            raise ReviewContractError(
                "REVIEW_POLL_CONFLICT",
                "Another agent poll already owns this Review session delivery lease.",
                "Wait for the active poll to return before polling again.",
                http_status=409,
            )
        try:
            with self._lock:
                self._active_polls += 1
                self._last_authenticated_activity = self._clock()
            deadline = self._clock() + (timeout_ms / 1000)
            first = True
            with self._condition:
                while True:
                    batch = self.runtime.session.poll(
                        ack_batch_id=ack_batch_id if first else None,
                        agent_reply=agent_reply if first else None,
                    )
                    first = False
                    if batch is not None:
                        return _json_response(200, self._bounded_feedback_payload(batch.to_json()))
                    if self.runtime.last_source_failure is not None:
                        return _json_response(
                            200,
                            {
                                "schema_version": 1,
                                "ok": True,
                                "status": "source_failed",
                                "batch": None,
                                "source_failure": self.runtime.last_source_failure,
                            },
                        )
                    if self.runtime.session.ended_by is not None:
                        return _json_response(
                            200,
                            {
                                "schema_version": 1,
                                "ok": True,
                                "status": "ended",
                                "batch": None,
                                "end": {
                                    "actor": self.runtime.session.ended_by,
                                    "final_sequence": len(self.runtime.session.events),
                                    "acknowledgement_required": False,
                                },
                            },
                        )
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        return _json_response(
                            200,
                            {"schema_version": 1, "ok": True, "status": "timeout", "batch": None},
                        )
                    self._condition.wait(timeout=remaining)
        finally:
            with self._lock:
                self._active_polls -= 1
                self._last_authenticated_activity = self._clock()
            self._poll_lock.release()

    def _bounded_feedback_payload(self, batch: dict[str, object]) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": 1,
            "ok": True,
            "status": "feedback",
            "batch": batch,
        }
        projections = (
            ("semantic_diff", self.runtime.semantic_diff),
            ("verification", self.runtime.verification),
            ("source_failure", self.runtime.last_source_failure),
        )
        for name, projection in projections:
            if projection is None:
                continue
            candidate = {**payload, name: projection}
            if len(canonical_json_bytes(candidate)) <= MAX_JSON_RESPONSE_BYTES:
                payload = candidate
            else:
                payload[name] = {"status": "deferred"}
        return payload

    def _consume_bootstrap(self, token: str) -> _Response:
        now = self._clock()
        with self._lock:
            valid = (
                not self._bootstrap_consumed
                and now <= self._bootstrap_expires
                and _compare_token(token, self._bootstrap_digest)
            )
            if not valid:
                raise ReviewContractError(
                    "REVIEW_CAPABILITY_INVALID",
                    "Review bootstrap capability is unknown, expired, or already consumed.",
                    "Run viewspec review again to obtain a fresh single-use URL.",
                    http_status=403,
                )
            self._bootstrap_consumed = True
            cookie = _token()
            self._cookie_digest = _digest_token(cookie)
            self._ended_cookie_digest = None
            self._cookie_expires = now + COOKIE_MAX_AGE_SECONDS
            self._cookie_last_activity = now
            self._last_authenticated_activity = now
        path = f"/r/{self.runtime.session.review_id}/"
        return _Response(
            303,
            (
                ("Location", path),
                (
                    "Set-Cookie",
                    f"{_COOKIE_NAME}={cookie}; Path={path}; HttpOnly; SameSite=Strict; Max-Age={COOKIE_MAX_AGE_SECONDS}",
                ),
            ),
            b"",
        )

    def _authorize_cookie(self, handler: BaseHTTPRequestHandler, *, allow_ended_retry: bool = False) -> None:
        values = _cookie_values(handler.headers.get("Cookie", ""), _COOKIE_NAME)
        now = self._clock()
        with self._lock:
            digest = self._cookie_digest
            if digest is None and allow_ended_retry:
                digest = self._ended_cookie_digest
            valid = (
                len(values) == 1
                and digest is not None
                and now <= self._cookie_expires
                and now - self._cookie_last_activity <= COOKIE_IDLE_SECONDS
                and _compare_token(values[0], digest)
            )
            if not valid:
                raise ReviewContractError(
                    "REVIEW_CAPABILITY_INVALID",
                    "Review browser capability is missing, malformed, or expired.",
                    "Open a fresh bootstrap URL from viewspec review.",
                    http_status=403,
                )
            self._cookie_last_activity = now

    def _authorize_browser_mutation(self, handler: BaseHTTPRequestHandler, *, allow_ended_retry: bool = False) -> None:
        self._authorize_cookie(handler, allow_ended_retry=allow_ended_retry)
        self._ensure_revision_capabilities()
        if handler.headers.get("Origin") != self.origin or handler.headers.get("Sec-Fetch-Site") != "same-origin":
            raise _forbidden("Browser mutation authorization does not match the current Review frame.")
        if handler.headers.get("X-ViewSpec-Frame-Nonce") != self.frame_nonce:
            raise ReviewContractError(
                "REVIEW_REVISION_MISMATCH",
                "Browser mutation carries a stale revision/frame nonce.",
                "Reload the current checked revision before submitting feedback.",
                http_status=409,
            )

    def _authorize_agent(self, handler: BaseHTTPRequestHandler) -> None:
        if not _compare_token(handler.headers.get("X-ViewSpec-Agent-Capability", ""), self._agent_digest):
            raise ReviewContractError(
                "REVIEW_CAPABILITY_INVALID",
                "Agent Review capability is missing or invalid.",
                "Resolve the active private session again before polling.",
                http_status=403,
            )
        with self._lock:
            self._last_authenticated_activity = self._clock()

    def _validate_headers(self, handler: BaseHTTPRequestHandler) -> None:
        pairs = list(handler.headers.items())
        if len(pairs) > MAX_REQUEST_HEADERS:
            raise _http_error(431, "REVIEW_REQUEST_HEADERS_TOO_LARGE", "Review request has more than 64 headers.")
        aggregate = 0
        for key, value in pairs:
            size = len(key.encode("latin-1", errors="replace")) + len(value.encode("latin-1", errors="replace")) + 4
            if size > MAX_SINGLE_HEADER_BYTES:
                raise _http_error(431, "REVIEW_REQUEST_HEADERS_TOO_LARGE", "One Review request header exceeds 8 KiB.")
            aggregate += size
        if aggregate > MAX_REQUEST_HEADER_BYTES:
            raise _http_error(431, "REVIEW_REQUEST_HEADERS_TOO_LARGE", "Review request headers exceed 16 KiB.")

    def _read_body(self, handler: BaseHTTPRequestHandler) -> bytes:
        lengths = handler.headers.get_all("Content-Length", failobj=[])
        if len(lengths) != 1 or not lengths[0].isdigit():
            raise _http_error(411, "REVIEW_REQUEST_LENGTH_REQUIRED", "Mutation requires one decimal Content-Length.")
        length = int(lengths[0])
        if length > MAX_REQUEST_BODY_BYTES:
            handler.close_connection = True
            raise _http_error(413, "REVIEW_REQUEST_TOO_LARGE", "Review request body exceeds 256 KiB.")
        encoding = handler.headers.get("Content-Encoding")
        if encoding not in {None, "identity"}:
            raise _http_error(415, "REVIEW_REQUEST_INVALID", "Compressed Review mutations are unsupported.")
        if handler.headers.get("Content-Type") != "application/json":
            raise _http_error(415, "REVIEW_REQUEST_INVALID", "Review mutations require application/json.")
        handler.connection.settimeout(5)
        content = handler.rfile.read(length)
        if len(content) != length:
            raise _http_error(408, "REVIEW_REQUEST_TIMEOUT", "Review request body ended before Content-Length.")
        return content

    def _chrome_response(self) -> _Response:
        self._ensure_revision_capabilities()
        comparison = self.runtime.built.revision.target == STUDIO_COMPARE_TARGET
        frame = self.frame_path("static/index.html" if comparison else "index.html")
        react_frame = self.frame_path("react/index.html") if comparison else None
        endpoint = f"/r/{self.runtime.session.review_id}/api/v1/events"
        end_endpoint = f"/r/{self.runtime.session.review_id}/api/v1/end"
        handshake_endpoint = f"/r/{self.runtime.session.review_id}/api/v1/handshake"
        session_endpoint = f"/r/{self.runtime.session.review_id}/api/v1/session"
        inspection_endpoint = (
            f"/r/{self.runtime.session.review_id}/api/v1/inspection"
            if self.runtime.inspection is not None
            else ""
        )
        convergence_approve_endpoint = f"/r/{self.runtime.session.review_id}/api/v1/convergence/approve"
        convergence_reject_endpoint = f"/r/{self.runtime.session.review_id}/api/v1/convergence/reject"
        share_available = comparison and self._share_status().get("status") == "available"
        share_prepare_endpoint = (
            f"/r/{self.runtime.session.review_id}/api/v1/share/prepare" if share_available else ""
        )
        share_publish_endpoint = (
            f"/r/{self.runtime.session.review_id}/api/v1/share/publish" if share_available else ""
        )
        share_button_markup = (
            "<button id=share-toggle class=secondary type=button aria-controls=studio-panel>Share</button>"
            if share_available
            else ""
        )
        share_panel_markup = (
            "<section id=share-panel class=panel-section hidden><h2>Create a private review</h2>"
            "<p id=share-summary>Preparing the exact checked revision locally…</p>"
            "<div id=share-disclosure hidden><p class=share-package id=share-package></p>"
            "<h3>What will leave this machine</h3><ul id=share-leaving class=share-list></ul>"
            "<h3>What stays here</h3><ul id=share-staying class=share-list></ul>"
            "<label for=share-expiry>Link expires<select id=share-expiry></select></label>"
            "<label class=share-confirm><input id=share-confirm type=checkbox> I reviewed this exact inventory and approve its upload.</label>"
            "<button id=share-create class=primary type=button disabled>Create private link</button></div>"
            "<div id=share-result class=share-result hidden><strong>Private reviewer link ready</strong>"
            "<label for=share-reviewer-link>Reviewer link<input id=share-reviewer-link readonly></label>"
            "<div class=action-row><button id=share-copy class=primary type=button>Copy reviewer link</button>"
            "<a id=share-owner class=secondary target=_blank rel='noopener noreferrer'>Open owner review</a></div>"
            "<small>The capability lives only after # in each link and is not sent in ordinary HTTP requests.</small></div>"
            "</section>"
            if share_available
            else ""
        )
        verification_status = str(self.runtime.verification.get("status", "not_run"))
        if comparison:
            verification_label = "Static + React checked"
            verification_detail = (
                "Both targets were built from the exact same AppBundle and passed artifact and semantic-identity checks. "
                "Visual parity is available for inspection, not claimed as proved."
            )
        else:
            verification_label = "3 viewports verified" if verification_status == "conformant" else "Artifact checked"
            verification_detail = (
                "Canonical mobile, tablet, and desktop verification is conformant for this exact revision."
                if verification_status == "conformant"
                else "This exact revision passed semantic compilation and artifact integrity checks. Viewport verification was not run."
            )
        if comparison:
            assert react_frame is not None
            frame_markup = (
                "<div id=fit-shell class=fit-shell><div id=fit-stage class=compare-stage data-studio-comparison=true>"
                f"<div class=target-frame data-studio-surface='html-tailwind-app' data-studio-surface-active=false aria-hidden=true inert><span>Static</span><iframe id=artifact tabindex=-1 data-studio-frame data-target='html-tailwind-app' "
                f"title='Checked static ViewSpec interface' sandbox='allow-scripts allow-forms' data-src='{frame}'></iframe></div>"
                f"<div class=target-frame data-studio-surface='react-tailwind-app' data-studio-surface-active=true aria-hidden=false><span>Live</span><iframe id=artifact-react data-studio-frame data-target='react-tailwind-app' "
                f"title='Checked React ViewSpec interface' sandbox='allow-scripts allow-forms' data-src='{react_frame}'></iframe></div></div></div>"
            )
        else:
            frame_markup = (
                "<div id=fit-shell class=fit-shell><div id=fit-stage class=single-stage>"
                f"<iframe id=artifact data-studio-frame data-target='{self.runtime.built.revision.target}' "
                f"title='Checked ViewSpec interface' sandbox='allow-scripts allow-forms' data-src='{frame}'></iframe></div></div>"
            )
        initial_agent_presence = str(self._agent_presence()["status"])
        initial_agent_label = {
            "ready": "Agent ready",
            "working": "Agent working",
            "not_connected": "Agent not connected",
        }[initial_agent_presence]
        initial_queued_events = self.runtime.status()["queued_events"]
        style = (
            ":root{color-scheme:dark;--ink:#080a0d;--panel:#10141a;--panel2:#151b23;--line:#29313d;"
            "--text:#f7f8fa;--muted:#98a3b3;--amber:#ffbd4a;--mint:#5fe0ad;--blue:#80aaff}"
            "*{box-sizing:border-box}html,body{height:100%}body{margin:0;overflow:hidden;font:14px/1.5 "
            "Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--ink);color:var(--text)}"
            "button,select,textarea,input{font:inherit}.skip{position:fixed;left:12px;top:-60px;z-index:10;padding:10px 14px;"
            "background:var(--amber);color:#211400}.skip:focus{top:12px}.toolbar{min-height:72px;display:flex;"
            "align-items:center;gap:18px;padding:12px 18px;border-bottom:1px solid var(--line);background:rgba(8,10,13,.96)}"
            ".brand{display:flex;align-items:center;gap:10px;margin-right:auto}.mark{display:grid;place-items:center;width:34px;"
            "height:34px;border-radius:10px;background:var(--amber);color:#211400;font-weight:900}.brand strong{display:block;"
            "font-size:14px}.brand small{display:block;color:var(--muted);font-size:10px;letter-spacing:.08em;text-transform:uppercase}"
            ".confidence{position:relative}.confidence summary{list-style:none;display:flex;align-items:center;gap:8px;cursor:pointer;"
            "padding:8px 11px;border:1px solid var(--line);border-radius:999px;color:#dbe5ee;font-size:11px}.confidence summary::-webkit-details-marker{display:none}"
            ".confidence summary:before{content:'';width:7px;height:7px;border-radius:50%;background:var(--mint);"
            "box-shadow:0 0 0 4px rgba(95,224,173,.12)}.confidence div{position:absolute;right:0;top:44px;z-index:5;width:320px;"
            "padding:15px;border:1px solid var(--line);border-radius:12px;background:var(--panel2);box-shadow:0 24px 70px #000}"
            ".confidence p{margin:0 0 8px;color:#dbe5ee}.confidence small{color:var(--muted)}.controls{display:flex;"
            "align-items:center;gap:8px}.controls label{color:var(--muted);font-size:11px}.controls select{min-height:38px;"
            "padding:0 30px 0 10px;border:1px solid var(--line);border-radius:9px;background:var(--panel);color:var(--text)}"
            ".primary,.secondary{min-height:40px;padding:0 14px;border-radius:9px;cursor:pointer;font-weight:750}.primary{border:0;"
            "background:var(--amber);color:#211400}.secondary{border:1px solid var(--line);background:transparent;color:var(--text)}"
            "button:focus-visible,select:focus-visible,textarea:focus-visible,summary:focus-visible{outline:2px solid var(--amber);outline-offset:2px}"
            ".statusbar{height:36px;display:flex;align-items:center;justify-content:space-between;gap:12px;padding:0 18px;"
            "border-bottom:1px solid var(--line);background:#0d1117;color:var(--muted);font-size:11px}.statusbar strong{color:var(--text)}"
            ".status-meta{display:flex;align-items:center;gap:14px}.agent-presence{color:var(--muted);font-weight:650}.agent-presence[data-status=ready]{color:var(--mint)}"
            ".agent-presence[data-status=working]{color:var(--amber)}"
            ".layout{position:relative;height:calc(100vh - 108px)}.canvas{height:100%;min-width:0;"
            "overflow:auto;overflow-anchor:none;padding:24px;background:#0b0e13;background-image:linear-gradient(rgba(255,255,255,.025) 1px,transparent 1px),"
            "linear-gradient(90deg,rgba(255,255,255,.025) 1px,transparent 1px);background-size:28px 28px}.stage{width:100%;"
            "min-width:0;display:block}.stage:before{content:'CHECKED INTERFACE';display:block;position:sticky;left:24px;"
            "margin:0 0 10px;color:var(--muted);font:700 9px/1 ui-monospace,monospace;letter-spacing:.12em}iframe{display:block;box-sizing:content-box;"
            "width:1440px;height:1000px;margin-inline:auto;border:1px solid #344050;border-radius:10px;background:white;box-shadow:0 35px 100px -45px #000}"
            "html[data-studio-viewport=mobile] iframe{width:390px;height:844px}html[data-studio-viewport=tablet] iframe{width:768px;height:1024px}"
            "html[data-studio-viewport=desktop] iframe{width:1440px;height:1000px}"
            ".fit-shell{position:relative;margin-inline:auto}.single-stage,.compare-stage{position:absolute;inset:0 auto auto 0;transform-origin:top left}"
            ".single-stage{width:max-content}.compare-stage{display:flex;align-items:flex-start;gap:18px;width:max-content}"
            "html[data-studio-viewport=mobile] .compare-stage{flex-direction:row}html[data-studio-viewport=tablet] .compare-stage,"
            "html[data-studio-viewport=desktop] .compare-stage{display:grid}html[data-studio-viewport=tablet] .target-frame,"
            "html[data-studio-viewport=desktop] .target-frame{grid-area:1/1}html[data-studio-viewport=tablet] .target-frame[data-studio-surface-active=false],"
            "html[data-studio-viewport=desktop] .target-frame[data-studio-surface-active=false]{opacity:0;pointer-events:none}.target-frame{display:grid;gap:8px}.target-frame>span{position:sticky;left:0;"
            "width:max-content;padding:5px 8px;border:1px solid var(--line);border-radius:999px;background:var(--panel2);color:#dbe5ee;"
            "font:700 9px/1 ui-monospace,monospace;letter-spacing:.1em;text-transform:uppercase}"
            ".panel{position:fixed;z-index:6;top:108px;right:0;bottom:0;width:min(380px,calc(100vw - 24px));min-width:0;padding:20px;"
            "border-left:1px solid var(--line);background:var(--panel);overflow:auto;visibility:hidden;transform:translateX(105%);"
            "transition:transform .2s ease,visibility 0s linear .2s;box-shadow:-24px 0 70px rgba(0,0,0,.38)}"
            ".panel.is-open{visibility:visible;transform:translateX(0);transition:transform .2s ease}.panel-head{display:grid;grid-template-columns:1fr auto;gap:12px;align-items:start}"
            ".panel-close{min-height:36px;padding:0 10px}.panel-head .eyebrow{"
            "color:var(--amber);font:700 9px/1 ui-monospace,monospace;letter-spacing:.13em;text-transform:uppercase}.panel h1{"
            "margin:10px 0 0;font:750 24px/1.02 ui-monospace,monospace;letter-spacing:-.04em}.panel-intro{margin:10px 0 18px}.panel h2{margin:0 0 8px;"
            "font:700 15px/1.2 ui-monospace,monospace}.panel h3{font-size:12px}.panel p{color:var(--muted)}.panel-section{padding:18px 0;border-top:1px solid var(--line)}"
            ".panel label{display:block;margin-top:12px;color:#dbe5ee;font-weight:650}.panel textarea,.panel select,.panel input[readonly]{width:100%;"
            "margin-top:5px;border:1px solid var(--line);border-radius:9px;background:#0b0e13;color:var(--text)}.panel select{min-height:38px;"
            "padding:0 9px}.panel textarea{min-height:120px;padding:11px;resize:vertical}.trace{white-space:pre-wrap;overflow-wrap:anywhere;"
            "border:1px solid var(--line);border-radius:9px;background:#0b0e13;padding:10px;color:#bac5d3;font:10px/1.6 ui-monospace,monospace}"
            ".action-row{display:flex;gap:8px;margin-top:12px}.action-row button{flex:1}.conversation:empty:after{content:'Your agent’s replies will appear here.';"
            "display:block;color:var(--muted);font-size:12px}.conversation{padding-left:20px;color:#dbe5ee}.change-list{display:grid;gap:8px}"
            ".change-row{display:grid;grid-template-columns:1fr auto 1fr;gap:8px;align-items:center}.change-value{min-width:0;padding:10px;"
            "border:1px solid var(--line);border-radius:8px;background:#0b0e13}.change-value span{display:block;color:var(--muted);"
            "font-size:9px;text-transform:uppercase}.change-value strong{display:block;margin-top:5px;overflow-wrap:anywhere;font:600 11px/1.4 ui-monospace,monospace}"
            ".change-arrow{color:var(--amber)}.change-target{grid-column:1/-1;color:var(--muted);font:10px/1.4 ui-monospace,monospace}"
            ".inspection-summary{margin:0 0 10px}.inspection-card{padding:11px;border:1px solid var(--line);border-radius:9px;background:#0b0e13}"
            ".inspection-card strong,.inspection-card small{display:block}.inspection-card small{margin-top:5px;color:var(--muted);font:10px/1.45 ui-monospace,monospace}"
            ".inspection-path{margin:8px 0 0;color:var(--blue);font:650 11px/1.5 ui-monospace,monospace;overflow-wrap:anywhere}"
            ".inspection-value{margin-top:7px;color:var(--text);font:750 15px/1.2 ui-monospace,monospace}.replay-row{display:grid;grid-template-columns:1fr auto;gap:8px;align-items:end}"
            ".surface-tools{padding:0 0 18px}.surface-tools label{margin-top:0}.surface-tools p{margin:7px 0 0;font-size:10px}html[data-studio-viewport=mobile] .surface-tools{display:none}"
            ".coherence-card{display:grid;gap:6px;padding:12px;border:1px solid var(--line);border-radius:10px;background:#0b0e13}"
            ".coherence-card[data-status=aligned]{border-color:rgba(95,224,173,.3);background:rgba(95,224,173,.05)}"
            ".coherence-card[data-status=mismatch]{border-color:rgba(255,189,74,.38);background:rgba(255,189,74,.06)}"
            ".coherence-card strong{font-size:13px}.coherence-card p{margin:0;font-size:11px}.coherence-actions{display:flex;gap:8px;margin-top:5px}"
            ".coherence-actions button{min-height:34px;flex:1}.coherence-scope{display:block;margin-top:8px;color:var(--muted);font-size:10px;line-height:1.45}"
            ".selection-card{display:grid;gap:4px;margin:12px 0;padding:12px;border:1px solid rgba(255,189,74,.32);border-radius:10px;background:rgba(255,189,74,.06)}"
            ".selection-card span{color:var(--muted);font-size:11px}.selection-card .selection-kicker{color:var(--amber);font:700 9px/1 ui-monospace,monospace;letter-spacing:.11em;text-transform:uppercase}"
            ".selection-card strong{font-size:15px;line-height:1.3}.selection-details summary{color:var(--muted);cursor:pointer;font-size:11px}.selection-details .trace{margin-top:8px}"
            ".replay-row label{margin-top:0}.replay-row button{min-height:38px}.proof-note{margin:8px 0 0!important;font-size:11px}"
            ".proof-result{padding:10px;border:1px solid rgba(95,224,173,.25);border-radius:9px;background:rgba(95,224,173,.06);"
            "color:var(--mint);font-size:11px}.advanced summary{margin-top:12px;color:var(--muted);cursor:pointer;font-size:11px}"
            ".share-package{padding:10px;border:1px solid var(--line);border-radius:9px;background:#0b0e13;color:#dbe5ee!important;font:10px/1.5 ui-monospace,monospace;overflow-wrap:anywhere}"
            ".share-list{margin:8px 0 14px;padding-left:20px;color:#dbe5ee;font-size:12px}.share-list li+li{margin-top:6px}"
            ".share-confirm{display:grid!important;grid-template-columns:auto 1fr;gap:9px;align-items:start;font-size:12px;font-weight:500!important}"
            ".share-confirm input{margin-top:4px}.share-result{display:grid;gap:8px;padding:12px;border:1px solid rgba(95,224,173,.3);border-radius:10px;background:rgba(95,224,173,.05)}"
            ".share-result label{margin-top:0}.share-result input{margin-top:5px;padding:9px;border:1px solid var(--line);border-radius:8px;background:#0b0e13;color:var(--text)}"
            ".share-result a{display:grid;place-items:center;text-decoration:none}.share-result small{color:var(--muted)}"
            "[hidden]{display:none!important}.notice{min-height:18px}.revision{color:var(--muted);font-size:11px}"
            "@media(prefers-reduced-motion:reduce){.panel{transition:none}.panel.is-open{transition:none}}"
            "@media(max-width:860px){body{overflow:auto}.toolbar{align-items:flex-start;flex-wrap:wrap}.brand{width:100%}.confidence{margin-right:auto}"
            ".statusbar{height:auto;min-height:44px;align-items:flex-start;flex-direction:column;padding-block:7px}.status-meta{width:100%;justify-content:space-between}"
            ".layout{height:auto}.canvas{height:58vh;padding:14px}.panel{top:0;width:100%;max-width:none;border-left:0}"
            ".confidence div{left:0;right:auto;width:min(320px,calc(100vw - 36px))}}"
        )
        share_script = (
            "const shareToggle=document.getElementById('share-toggle'),sharePanel=document.getElementById('share-panel'),"
            "shareSummary=document.getElementById('share-summary'),shareDisclosure=document.getElementById('share-disclosure'),"
            "sharePackage=document.getElementById('share-package'),shareLeaving=document.getElementById('share-leaving'),"
            "shareStaying=document.getElementById('share-staying'),shareExpiry=document.getElementById('share-expiry'),"
            "shareConfirm=document.getElementById('share-confirm'),shareCreate=document.getElementById('share-create'),"
            "shareResult=document.getElementById('share-result'),shareReviewerLink=document.getElementById('share-reviewer-link'),"
            "shareCopy=document.getElementById('share-copy'),shareOwner=document.getElementById('share-owner');let preparedShare=null;"
            "const sharePost=async(url,payload)=>{const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-ViewSpec-Frame-Nonce':nonce},body:JSON.stringify(payload)});"
            "const result=await response.json();if(!response.ok)throw new Error(result.error?.code||'Private review request failed');return result.share;};"
            "const shareMetric=item=>Object.entries(item).filter(([key])=>key!=='category').map(([key,value])=>{"
            "if(key==='bytes')return new Intl.NumberFormat().format(value)+' bytes';if(key==='file_count')return value+' files';"
            "if(key==='field_count')return value+' fields';if(key==='current_count')return value+' existing';return key+': '+value;}).join(' · ');"
            "const shareItems=(target,items)=>target.replaceChildren(...items.map(item=>{const row=document.createElement('li');"
            "row.textContent=typeof item==='string'?item:item.category+(shareMetric(item)?' · '+shareMetric(item):'');return row;}));"
            "const renderShare=value=>{if(value?.status!=='awaiting_confirmation'||value.upload_performed!==false)throw new Error('STUDIO_SHARE_REMOTE_INVALID');"
            "preparedShare=value;sharePackage.textContent='Checked revision '+value.revision+' · '+value.file_count+' files · '+new Intl.NumberFormat().format(value.bytes)+' bytes · package '+value.package_id;"
            "shareItems(shareLeaving,value.disclosure?.will_leave_machine||[]);shareItems(shareStaying,value.disclosure?.will_not_leave_machine||[]);"
            "shareExpiry.replaceChildren(...value.expiry_options.map(seconds=>{const option=document.createElement('option');option.value=String(seconds);"
            "option.textContent=seconds===3600?'1 hour':seconds===86400?'24 hours':'7 days';return option;}));"
            "shareConfirm.checked=false;shareCreate.disabled=true;shareDisclosure.hidden=false;shareResult.hidden=true;"
            "shareSummary.textContent='Nothing has been uploaded. Review the exact inventory, choose an expiry, then confirm.';};"
            "const prepareShare=async()=>{if(!handshakeConfirmed){status.textContent='Wait for the checked target pair before sharing';return;}"
            "shareToggle.disabled=true;sharePanel.hidden=false;openPanel(sharePanel);shareSummary.textContent='Preparing the exact checked revision locally…';shareDisclosure.hidden=true;shareResult.hidden=true;"
            "try{renderShare(await sharePost('"
            + share_prepare_endpoint
            + "',{}));status.textContent='Private review package prepared locally · nothing uploaded';}catch(error){shareSummary.textContent='Private sharing is unavailable · '+error.message;status.textContent=error.message;}finally{shareToggle.disabled=false;}};"
            "shareToggle.addEventListener('click',prepareShare);shareConfirm.addEventListener('change',()=>{shareCreate.disabled=!shareConfirm.checked||!preparedShare;});"
            "shareCreate.addEventListener('click',async()=>{if(!preparedShare||!shareConfirm.checked)return;shareCreate.disabled=true;shareSummary.textContent='Creating one private link…';"
            "try{const value=await sharePost('"
            + share_publish_endpoint
            + "',{package_id:preparedShare.package_id,disclosure_accepted:true,expires_in_seconds:Number(shareExpiry.value)});"
            "if(value?.status!=='active'||value.package_id!==preparedShare.package_id||value.upload_performed!==true)throw new Error('STUDIO_SHARE_REMOTE_INVALID');"
            "shareReviewerLink.value=value.reviewer_url;shareOwner.href=value.owner_url;shareDisclosure.hidden=true;shareResult.hidden=false;"
            "shareSummary.textContent='This exact checked revision is available by private link until its expiry.';status.textContent='Private reviewer link ready';"
            "}catch(error){shareSummary.textContent='Private link was not created · '+error.message;status.textContent=error.message;shareCreate.disabled=false;}});"
            "shareCopy.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(shareReviewerLink.value);shareCopy.textContent='Copied';status.textContent='Reviewer link copied';}"
            "catch{shareReviewerLink.focus();shareReviewerLink.select();status.textContent='Select and copy the reviewer link';}});"
            if share_available
            else ""
        )
        script = (
            "(()=>{const nonce='"
            + self.frame_nonce
            + "',endpoint='"
            + endpoint
            + "',endEndpoint='"
            + end_endpoint
            + "',handshakeEndpoint='"
            + handshake_endpoint
            + "',sessionEndpoint='"
            + session_endpoint
            + "',inspectionEndpoint='"
            + inspection_endpoint
            + "',convergenceApproveEndpoint='"
            + convergence_approve_endpoint
            + "',convergenceRejectEndpoint='"
            + convergence_reject_endpoint
            + "',requiresSemanticScreen="
            + ("true" if self.runtime.built.revision.source_kind == "app_bundle" else "false")
            + ",revision="
            + str(self.runtime.built.revision.number)
            + ",frames=Array.from(document.querySelectorAll('[data-studio-frame]')),frame=frames[0],canvas=document.querySelector('.canvas'),fitShell=document.getElementById('fit-shell'),fitStage=document.getElementById('fit-stage'),mode=document.getElementById('mode'),"
            "panel=document.getElementById('studio-panel'),panelToggle=document.getElementById('panel-toggle'),panelClose=document.getElementById('panel-close'),"
            "surfaceSelect=document.getElementById('surface-preview'),surfaceTools=document.getElementById('surface-tools'),"
            "status=document.getElementById('status'),agentPresence=document.getElementById('agent-presence'),queuedCount=document.getElementById('queued'),"
            "composer=document.getElementById('composer'),trace=document.getElementById('trace'),"
            "conversation=document.getElementById('conversation'),convergence=document.getElementById('convergence'),"
            "inspectionPanel=document.getElementById('inspection'),inspectionSummary=document.getElementById('inspection-summary'),"
            "replayTools=document.getElementById('replay-tools'),replaySelect=document.getElementById('replay-checkpoint'),runReplay=document.getElementById('run-replay'),"
            "replayProof=document.getElementById('replay-proof'),resourceCard=document.getElementById('resource-card'),"
            "coherencePanel=document.getElementById('coherence'),coherenceCard=document.getElementById('coherence-card'),coherenceSummary=document.getElementById('coherence-summary'),"
            "coherenceDetail=document.getElementById('coherence-detail'),coherenceReview=document.getElementById('coherence-review'),coherenceRecheck=document.getElementById('coherence-recheck'),"
            "selectionKicker=document.getElementById('selection-kicker'),selectionTitle=document.getElementById('selection-title'),selectionDetail=document.getElementById('selection-detail'),"
            "convergenceSummary=document.getElementById('convergence-summary'),convergenceDiff=document.getElementById('convergence-diff'),"
            "convergenceProof=document.getElementById('convergence-proof'),approveConvergence=document.getElementById('approve-convergence'),"
            "rejectConvergence=document.getElementById('reject-convergence');let pendingConvergence=null,panelOpen=false,panelSection=null;"
            "let annotate=false,selection=null,retainedContext=null,restoreContext=null,readyTargets=new Map(),targetContexts={},handshakeStarted=false,handshakeConfirmed=false;"
            "let inspection=null,replayChoices=new Map(),desiredReplay=null,replayDispatched=false,replayReloading=false,replaySettled=false,activeReplayRef=null,replayResults=new Map(),replayGeneration=0;"
            "let coherenceProbe=0,coherenceResults=new Map(),coherenceMismatch=null,coherenceTimer=null,resetAfterCoherence=true,canvasOriginLocked=true;"
            "const resetCanvas=()=>{canvas.scrollLeft=0;canvas.scrollTop=0;};"
            "const lockCanvasOrigin=()=>{canvasOriginLocked=true;resetCanvas();};canvas.addEventListener('scroll',()=>{if(canvasOriginLocked&&(canvas.scrollLeft||canvas.scrollTop))requestAnimationFrame(resetCanvas);},{passive:true});"
            "canvas.addEventListener('wheel',()=>{canvasOriginLocked=false;},{passive:true});canvas.addEventListener('pointerdown',()=>{canvasOriginLocked=false;},{passive:true});"
            "const fitCanvas=()=>{if(!fitShell||!fitStage)return;fitStage.style.transform='none';const width=fitStage.offsetWidth,height=fitStage.offsetHeight,style=getComputedStyle(canvas);"
            "const available=Math.max(1,canvas.clientWidth-(parseFloat(style.paddingLeft)||0)-(parseFloat(style.paddingRight)||0));const scale=Math.min(1,available/Math.max(1,width));"
            "fitStage.style.transform='scale('+scale+')';fitShell.style.width=Math.ceil(width*scale)+'px';fitShell.style.height=Math.ceil(height*scale)+'px';fitShell.dataset.fitScale=scale.toFixed(3);};"
            "const openPanel=section=>{const shouldReveal=!panelOpen||(section&&panelSection!==section);panelOpen=true;panel.classList.add('is-open');panel.removeAttribute('inert');"
            "panel.setAttribute('aria-hidden','false');panelToggle.setAttribute('aria-expanded','true');if(section)panelSection=section;if(shouldReveal&&section)requestAnimationFrame(()=>section.scrollIntoView({block:'start'}));};"
            "const closePanel=(restoreFocus=true)=>{panelOpen=false;panelSection=null;panel.classList.remove('is-open');panel.setAttribute('aria-hidden','true');panel.setAttribute('inert','');"
            "panelToggle.setAttribute('aria-expanded','false');panel.scrollTop=0;if(restoreFocus)panelToggle.focus();};"
            "const showSurface=surface=>{if(!surfaceSelect)return;const mobile=document.documentElement.dataset.studioViewport==='mobile';surfaceSelect.value=surface;frames.forEach(item=>{"
            "const wrapper=item.closest('.target-frame'),active=mobile||item.dataset.target===surface;wrapper.dataset.studioSurfaceActive=active?'true':'false';wrapper.setAttribute('aria-hidden',active?'false':'true');"
            "wrapper.toggleAttribute('inert',!active);if(active)item.removeAttribute('tabindex');else item.setAttribute('tabindex','-1');});requestAnimationFrame(fitCanvas);};"
            "panelToggle.addEventListener('click',()=>openPanel(null));panelClose.addEventListener('click',()=>closePanel());surfaceSelect?.addEventListener('change',e=>showSurface(e.target.value));"
            "try{restoreContext=JSON.parse(sessionStorage.getItem('viewspec-context-restore')||'null');}catch{}"
            "sessionStorage.removeItem('viewspec-context-restore');const reset=sessionStorage.getItem('viewspec-context-reset');"
            "if(reset){sessionStorage.removeItem('viewspec-context-reset');status.textContent='REVIEW_CONTEXT_RESET';}"
            "const hex=b=>Array.from(b,x=>x.toString(16).padStart(2,'0')).join('');"
            "const showResource=e=>{if(!inspection||inspection.resources?.status!=='ready'){resourceCard.hidden=true;return null;}"
            "const ancestors=new Set(e.dom_ancestors||[]),matches=inspection.resources.views.filter(view=>view.screen_id===e.screen_id).flatMap(view=>view.assertions||[]).filter(item=>ancestors.has(item.matched_dom_id));"
            "const identities=[...new Set(matches.map(item=>item.canonical_identity))];if(identities.length!==1){resourceCard.hidden=true;return null;}"
            "const item=matches.find(value=>value.canonical_identity===identities[0]);resourceCard.hidden=false;resourceCard.replaceChildren();"
            "const title=document.createElement('strong'),path=document.createElement('div'),value=document.createElement('div'),meta=document.createElement('small');"
            "title.textContent='Checked fixture field';path.className='inspection-path';path.textContent=item.resource_id+' → '+item.record_id+' → '+item.field;"
            "value.className='inspection-value';value.textContent=String(item.expected);meta.textContent='Source value · '+item.matched_binding_id+' · current '+e.surface_target+' text: '+(e.rendered_text||'');"
            "resourceCard.append(title,path,value,meta);return item;};"
            "const humanize=value=>String(value||'').replace(/[_-]+/g,' ').replace(/\\b\\w/g,letter=>letter.toUpperCase());"
            "const coherenceLabel=item=>(String(item?.text||'').trim().slice(0,52)||humanize(item?.dom_id||'semantic element'));"
            "const validCoherenceItem=item=>item&&typeof item.dom_id==='string'&&item.dom_id.length<=256&&typeof item.ir_id==='string'&&item.ir_id.length<=256&&"
            "item.leaf===true&&item.rect&&['x','y','width','height'].every(key=>Number.isFinite(item.rect[key]))&&Number.isFinite(item.font_size)&&Number.isFinite(item.font_weight);"
            "const renderCoherence=()=>{if(coherenceResults.size!==frames.length)return;const staticResult=coherenceResults.get('html-tailwind-app'),reactResult=coherenceResults.get('react-tailwind-app');"
            "if(!staticResult||!reactResult)return;const left=new Map((staticResult.items||[]).filter(validCoherenceItem).map(item=>[item.dom_id,item])),right=new Map((reactResult.items||[]).filter(validCoherenceItem).map(item=>[item.dom_id,item]));"
            "const ids=[...new Set([...left.keys(),...right.keys()])].sort(),thresholds=inspection?.coherence?.thresholds||{position_px:3,size_px:3,font_size_px:.5,font_weight:50},candidates=[];let aligned=0;"
            "for(const id of ids){const a=left.get(id),b=right.get(id),item=a||b,label=coherenceLabel(item);if(!a||!b){candidates.push({score:10000,dom_id:id,surface_target:a?'html-tailwind-app':'react-tailwind-app',screen_id:item?.screen_id||null,"
            "detail:label+' is visible only in '+(a?'Static':'React')+'.'});continue;}const aText=String(a.text||''),bText=String(b.text||'');if(aText!==bText){candidates.push({score:9000,dom_id:id,surface_target:'react-tailwind-app',screen_id:b.screen_id||a.screen_id||null,"
            "detail:label+' shows different text: Static “'+aText.slice(0,48)+'” · React “'+bText.slice(0,48)+'”.'});continue;}"
            "const metrics=[['x',b.rect.x-a.rect.x,Number(thresholds.position_px)||3],['y',b.rect.y-a.rect.y,Number(thresholds.position_px)||3],"
            "['width',b.rect.width-a.rect.width,Number(thresholds.size_px)||3],['height',b.rect.height-a.rect.height,Number(thresholds.size_px)||3],"
            "['font_size',b.font_size-a.font_size,Number(thresholds.font_size_px)||.5],['font_weight',b.font_weight-a.font_weight,Number(thresholds.font_weight)||50]];"
            "const changed=metrics.filter(([,delta,limit])=>Math.abs(delta)>limit).sort((first,second)=>(Math.abs(second[1])-second[2])-(Math.abs(first[1])-first[2]))[0];"
            "if(changed){const [metric,delta,limit]=changed,amount=Math.round(Math.abs(delta)*10)/10;let phrase='';if(metric==='x')phrase='sits '+amount+' px farther '+(delta>0?'right':'left')+' in React';"
            "else if(metric==='y')phrase='sits '+amount+' px '+(delta>0?'lower':'higher')+' in React';else if(metric==='width')phrase='is '+amount+' px '+(delta>0?'wider':'narrower')+' in React';"
            "else if(metric==='height')phrase='is '+amount+' px '+(delta>0?'taller':'shorter')+' in React';else if(metric==='font_size')phrase='uses type '+amount+' px '+(delta>0?'larger':'smaller')+' in React';"
            "else phrase='uses '+(delta>0?'heavier':'lighter')+' type in React';candidates.push({score:100+Math.abs(delta)-limit,dom_id:id,surface_target:'react-tailwind-app',screen_id:b.screen_id||a.screen_id||null,detail:label+' '+phrase+'.'});continue;}"
            "if(a.color!==b.color){candidates.push({score:10,dom_id:id,surface_target:'react-tailwind-app',screen_id:b.screen_id||a.screen_id||null,detail:label+' uses a different text color in React.'});continue;}aligned++;}"
            "coherenceMismatch=candidates.sort((a,b)=>b.score-a.score||a.dom_id.localeCompare(b.dom_id))[0]||null;coherencePanel.hidden=false;coherenceCard.dataset.status=coherenceMismatch?'mismatch':'aligned';"
            "const viewport=humanize(document.documentElement.dataset.studioViewport||'current');if(coherenceMismatch){coherenceSummary.textContent='Targets differ at '+viewport;coherenceDetail.textContent=coherenceMismatch.detail;coherenceReview.hidden=false;openPanel(coherencePanel);}"
            "else{coherenceSummary.textContent='Static + React align at '+viewport;coherenceDetail.textContent=aligned+' visible semantic elements agree within the checked geometry thresholds.';coherenceReview.hidden=true;}"
            "if(resetAfterCoherence){resetCanvas();resetAfterCoherence=false;}};"
            "const requestCoherence=()=>{if(frames.length!==2||!handshakeConfirmed)return;clearTimeout(coherenceTimer);coherenceProbe++;const probeId='coherence-'+coherenceProbe;"
            "coherenceResults.clear();coherenceMismatch=null;coherencePanel.hidden=false;coherenceCard.dataset.status='checking';coherenceSummary.textContent='Checking Static + React…';"
            "coherenceDetail.textContent='Comparing visible semantic geometry at this canvas size.';coherenceReview.hidden=true;coherenceTimer=setTimeout(()=>{"
            "frames.forEach(item=>item.contentWindow.postMessage({type:'viewspec-studio-coherence-measure',nonce,probe_id:probeId},'*'));},120);};"
            "const expectedSummary=checkpoint=>{const expected=checkpoint?.expected;if(!expected)return '';const parts=[...(expected.text||[]).map(item=>String(item.value)),...(expected.state||[]).map(item=>humanize(item.id)+(item.kind==='scalar'?' = '+JSON.stringify(item.value):' checked')),...(expected.selectors||[]).map(item=>humanize(item.id)+' checked'),...(expected.visibility||[]).map(item=>humanize(item.id)+' '+(item.visible?'visible':'hidden'))];return parts.slice(0,3).join(' · ');};"
            "const showSelection=(e,item)=>{const surface=e.surface_target==='react-tailwind-app'?'React':e.surface_target==='html-tailwind-app'?'Static':'Preview';"
            "selectionKicker.textContent='Selected in '+surface;selectionTitle.textContent=item?(item.record_id+' · '+humanize(item.field)):((e.rendered_text||e.dom_ancestors?.[0]||'Whole page').trim().slice(0,96));"
            "selectionDetail.textContent=(frames.length>1?'Matched across Static + React · ':'')+(item?'Checked fixture field · ':'Checked semantic element · ')+humanize(e.screen_id||'standalone');};"
            "const loadInspection=async()=>{if(!inspectionEndpoint)return;try{const response=await fetch(inspectionEndpoint),result=await response.json();"
            "if(!response.ok||!result.inspection)return;inspection=result.inspection;inspectionPanel.hidden=false;const state=inspection.state,resources=inspection.resources;"
            "inspectionSummary.textContent=[state.status==='ready'?(state.replays.length+' checked replay'+(state.replays.length===1?'':'s')):'No declared replay',"
            "resources.status==='ready'?(resources.assertion_count+' checked fixture fields'):'No declared resource binding'].join(' · ');"
            "replayChoices.clear();replaySelect.replaceChildren();if(state.status==='ready'&&state.replays.length){state.replays.forEach(replay=>replay.checkpoints.forEach(checkpoint=>{"
            "const key=replay.id+':'+checkpoint.index,option=document.createElement('option');option.value=key;option.textContent=replay.id+' · '+checkpoint.label;"
            "replayChoices.set(key,{replay,checkpoint});replaySelect.append(option);}));replayTools.hidden=false;const update=()=>{const choice=replayChoices.get(replaySelect.value);"
            "runReplay.disabled=!choice||choice.replay.browser_status!=='replayable';const expected=expectedSummary(choice?.checkpoint);replayProof.textContent=choice?.replay.browser_status==='replayable'"
            "?(expected?'Proved result · '+expected:'Reducer proof passed · exact declared actions'):'Proof passed · browser replay unavailable for an ambiguous action trigger';};replaySelect.addEventListener('change',update);update();}"
            "}catch{inspectionPanel.hidden=true;}};"
            "runReplay.addEventListener('click',()=>{const choice=replayChoices.get(replaySelect.value);if(!choice||choice.replay.browser_status!=='replayable')return;"
            "const count=choice.checkpoint.index;desiredReplay={evidence_ref:choice.checkpoint.evidence_ref,events:choice.replay.checkpoints.slice(1,count+1).map(item=>item.event)};"
            "activeReplayRef=null;replayDispatched=false;replaySettled=false;replayResults.clear();readyTargets.clear();status.textContent='Resetting both targets…';"
            "replayGeneration++;replayReloading=true;const loaded=frames.map(item=>new Promise(resolve=>item.addEventListener('load',resolve,{once:true})));"
            "frames.forEach(item=>{const next=new URL(item.dataset.src||'',location.href);next.searchParams.set('viewspec_replay',String(replayGeneration));item.src=next.href;});"
            "Promise.all(loaded).then(()=>{replayReloading=false;dispatchReplay();});});"
            "const dispatchReplay=()=>{if(!desiredReplay||replayDispatched)return;replayDispatched=true;status.textContent='Applying proved checkpoint to both targets…';"
            "frames.forEach(item=>item.contentWindow.postMessage({type:'viewspec-studio-replay-apply',nonce,...desiredReplay},'*'));};"
            "document.getElementById('viewport').addEventListener('change',e=>{const d={mobile:[390,844],tablet:[768,1024],desktop:[1440,1000]}[e.target.value];"
            "if(!d)return;document.documentElement.dataset.studioViewport=e.target.value;showSurface(surfaceSelect?.value||'react-tailwind-app');resetAfterCoherence=true;lockCanvasOrigin();requestAnimationFrame(fitCanvas);status.textContent='Viewport '+e.target.value+' · all targets';requestCoherence();});"
            "const toggle=()=>{annotate=!annotate;mode.textContent=annotate?'Preview':'Comment';status.textContent=annotate?'Comment mode · choose something to change':'Preview mode · interactions are live';"
            "if(!annotate){selection=null;composer.hidden=true;resourceCard.hidden=true;closePanel(false);}frames.forEach(item=>item.contentWindow.postMessage({type:'viewspec-review-mode',nonce,annotate},'*'));};mode.addEventListener('click',toggle);"
            "coherenceRecheck?.addEventListener('click',requestCoherence);coherenceReview?.addEventListener('click',()=>{if(!coherenceMismatch)return;if(!annotate)toggle();"
            "showSurface(coherenceMismatch.surface_target);const target=frames.find(item=>item.dataset.target===coherenceMismatch.surface_target)||frame;target.contentWindow.postMessage({type:'viewspec-studio-coherence-choose',nonce,dom_id:coherenceMismatch.dom_id},'*');"
            "const feedback=document.getElementById('feedback');if(!feedback.value)feedback.value='Make this consistent across Static and React. '+coherenceMismatch.detail;});"
            "addEventListener('keydown',e=>{if(e.key==='Escape'&&panelOpen){e.preventDefault();closePanel();return;}if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='i'){e.preventDefault();toggle();}});"
            "addEventListener('message',e=>{const sourceFrame=frames.find(item=>e.source===item.contentWindow);if(!sourceFrame||!e.data||e.data.nonce!==nonce)return;"
            "const surfaceTarget=sourceFrame.dataset.target;if(e.data.type==='viewspec-review-render-failed'){status.textContent='Target failed to render · '+surfaceTarget;return;}"
            "if(e.data.type==='viewspec-review-ready'){readyTargets.set(surfaceTarget,{target:surfaceTarget,route:e.data.route,screen_id:e.data.screen_id});"
            "if(replaySettled)return;"
            "const observations=Array.from(readyTargets.values()),complete=readyTargets.size===frames.length&&(!requiresSemanticScreen||observations.every(item=>typeof item.route==='string'&&typeof item.screen_id==='string'));"
            "if(!complete){status.textContent='Waiting for every target to render…';return;}if(handshakeConfirmed){if(desiredReplay&&!replayReloading)dispatchReplay();else if(!desiredReplay&&!replaySettled)status.textContent=frames.length>1?'Checked target pair ready':'Checked frame ready';return;}"
            "status.textContent='Checking exact target pair…';if(handshakeStarted)return;handshakeStarted=true;"
            "fetch(handshakeEndpoint,{method:'POST',"
            "headers:{'Content-Type':'application/json','X-ViewSpec-Frame-Nonce':nonce},body:JSON.stringify({targets:observations})}).then(async response=>{"
            "const result=await response.json();if(response.ok)handshakeConfirmed=true;status.textContent=response.ok?(reset?'REVIEW_CONTEXT_RESET':(frames.length>1?'Checked target pair ready':'Checked frame ready')):(result.error?.code||'Frame handshake failed');"
            "if(response.ok&&desiredReplay)dispatchReplay();"
            "else if(response.ok&&restoreContext)frames.forEach(item=>item.contentWindow.postMessage({type:'viewspec-review-restore',nonce,...restoreContext},'*'));"
            "if(response.ok&&!desiredReplay)requestCoherence();})"
            ".catch(()=>{status.textContent='REVIEW_BROWSER_HANDSHAKE_TIMEOUT';});return;}"
            "if(e.data.type==='viewspec-review-viewport-mismatch'){status.textContent='REVIEW_VIEWPORT_MISMATCH';return;}"
            "if(e.data.type==='viewspec-studio-coherence-result'){const expected='coherence-'+coherenceProbe;if(e.data.probe_id!==expected||!e.data.viewport||e.data.viewport.name!==document.documentElement.dataset.studioViewport||!Array.isArray(e.data.items)||e.data.items.length>512)return;"
            "coherenceResults.set(surfaceTarget,{items:e.data.items,viewport:e.data.viewport});renderCoherence();return;}"
            "if(e.data.type==='viewspec-studio-replay-result'){replayResults.set(surfaceTarget,e.data);if(replayResults.size===frames.length){"
            "const passed=Array.from(replayResults.values()).every(item=>item.ok===true);activeReplayRef=passed?desiredReplay?.evidence_ref:null;"
            "status.textContent=passed?'Replay checkpoint · both targets applied':'Replay unavailable · '+(Array.from(replayResults.values()).find(item=>!item.ok)?.reason||'target mismatch');replaySettled=true;"
            "replayProof.textContent=passed?('Checkpoint active · '+(expectedSummary(replayChoices.get(replaySelect.value)?.checkpoint)||'proved state retained')):'No checkpoint claim was attached';desiredReplay=null;replayDispatched=false;requestCoherence();}return;}"
            "if(e.data.type==='viewspec-review-context'){retainedContext={route:e.data.route,scroll_x:e.data.scroll_x,scroll_y:e.data.scroll_y};"
            "targetContexts[surfaceTarget]=retainedContext;if(frames.length>1&&e.data.route&&e.data.sync_cause==='navigation'){frames.filter(item=>item!==sourceFrame).forEach(item=>{"
            "if(targetContexts[item.dataset.target]?.route!==e.data.route)item.contentWindow.postMessage({type:'viewspec-review-restore',nonce,route:e.data.route,scroll_x:0,scroll_y:0},'*');});}return;}"
            "if(e.data.type==='viewspec-review-toggle'){toggle();return;}if(e.data.type!=='viewspec-review-selected')return;"
            "selection={...e.data,surface_target:surfaceTarget};frames.forEach(item=>item.contentWindow.postMessage({type:'viewspec-review-selection',nonce,dom_id:e.data.dom_ancestors[0]||null},'*'));"
            "const selectedResource=showResource(selection);showSelection(selection,selectedResource);composer.hidden=false;openPanel(composer);trace.textContent=['Target: '+surfaceTarget,'Selected: '+(e.data.dom_ancestors[0]||'whole page'),"
            "'Screen: '+(e.data.screen_id||'standalone'),'Revision: "
            + str(self.runtime.built.revision.number)
            + "'].join('\\n');document.getElementById('feedback').focus();});"
            "document.getElementById('page-target').addEventListener('click',()=>{if(!selection)return;selection={...selection,page_level:true,dom_ancestors:[]};"
            "selectionKicker.textContent='Selected scope';selectionTitle.textContent='Whole page';selectionDetail.textContent='Explicit page-level request';"
            "trace.textContent='Page-level annotation (explicit fallback)';status.textContent='Page target selected';});"
            "const submit=async end=>{if(!selection)return;const feedback=document.getElementById('feedback'),body=feedback.value;"
            "if(!body){status.textContent='Feedback text is required';return;}let selected_text=null;if(selection.selected_text){const q=selection.selected_text.quote;"
            "const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(q));selected_text={...selection.selected_text,sha256:hex(new Uint8Array(digest))};}"
            "let payload={kind:document.getElementById('kind').value,body,screen_id:selection.screen_id,dom_ancestors:selection.dom_ancestors,"
            "page_level:selection.page_level,client_provenance:{studio_surface_target:selection.surface_target},context:{route:selection.route,screen_id:selection.screen_id,viewport:selection.viewport,"
            "selected_text,control_values:{},visibility:selection.visibility,evidence_refs:activeReplayRef?[activeReplayRef]:[]}};const key=hex(crypto.getRandomValues(new Uint8Array(16)));"
            "if(end)payload={actor:'human',...payload};const response=await fetch(end?endEndpoint:endpoint,{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':key,'X-ViewSpec-Frame-Nonce':nonce},"
            "body:JSON.stringify(payload)});const result=await response.json();if(!response.ok){status.textContent=result.error?.code||'Submission failed';return;}"
            "feedback.value='';const session=await refreshSession().catch(()=>null),agentState=session?.agent_presence?.status;"
            "status.textContent=end?'Feedback sent; review ended':agentState==='working'?'Request delivered · agent working':agentState==='ready'?'Request sent to agent':'Request saved locally · waiting for agent';"
            "if(end){document.getElementById('send').disabled=true;document.getElementById('send-end').disabled=true;}};"
            "document.getElementById('send').addEventListener('click',()=>submit(false));document.getElementById('send-end').addEventListener('click',()=>submit(true));"
            "const showValue=value=>typeof value==='string'?value:JSON.stringify(value);const renderDiff=diff=>{const direct=Array.isArray(diff?.changed_fields)?diff.changed_fields:[];"
            "const nested=Object.entries(diff?.screen_intent_diffs||{}).flatMap(([screen,value])=>(Array.isArray(value?.changed_fields)?value.changed_fields:[]).map(field=>({...field,screen_id:screen})));"
            "const fields=[...direct,...nested];"
            "convergenceDiff.replaceChildren(...fields.slice(0,16).map(field=>{const row=document.createElement('div');row.className='change-row';"
            "const before=document.createElement('div'),after=document.createElement('div'),arrow=document.createElement('span'),target=document.createElement('div');"
            "before.className=after.className='change-value';arrow.className='change-arrow';target.className='change-target';"
            "const beforeLabel=document.createElement('span'),afterLabel=document.createElement('span'),left=document.createElement('strong'),right=document.createElement('strong');"
            "beforeLabel.textContent='Before';afterLabel.textContent='After';left.textContent=showValue(field.left);right.textContent=showValue(field.right);"
            "before.append(beforeLabel,left);after.append(afterLabel,right);arrow.textContent='→';"
            "target.textContent=[field.screen_id,field.section,field.id,field.field].filter(Boolean).join(' · ');row.append(before,arrow,after,target);return row;}));"
            "if(!fields.length)convergenceDiff.textContent='One bounded semantic change is ready for review.';};"
            "const renderConvergence=value=>{pendingConvergence=value?.status==='awaiting_approval'?value:null;convergence.hidden=!pendingConvergence;"
            "if(!pendingConvergence)return;openPanel(convergence);const preview=pendingConvergence.pending_preview,proof=preview.progress_certificate;"
            "convergenceSummary.textContent='Your agent proposes one checked change to the current semantic source.';renderDiff(preview.semantic_diff);"
            "const fixed=proof.fixed_obligations?.length||0,remaining=proof.remaining_obligations?.length||0,introduced=proof.introduced_obligations?.length||0;"
            "convergenceProof.textContent=proof.accepted?'Checked change · '+fixed+' fixed · '+remaining+' remaining · '+introduced+' introduced':'Change needs another pass · '+(proof.reason||'proof incomplete');"
            "approveConvergence.disabled=false;rejectConvergence.disabled=false;};"
            "const decideConvergence=async action=>{if(!pendingConvergence)return;approveConvergence.disabled=true;rejectConvergence.disabled=true;"
            "const previewId=pendingConvergence.pending_preview.preview_id;try{const response=await fetch(action==='approve'?convergenceApproveEndpoint:convergenceRejectEndpoint,{"
            "method:'POST',headers:{'Content-Type':'application/json','X-ViewSpec-Frame-Nonce':nonce},body:JSON.stringify({preview_id:previewId})});"
            "const result=await response.json();status.textContent=response.ok?(action==='approve'?'Change approved':'Change rejected'):(result.error?.code||'Decision failed');"
            "if(response.ok)renderConvergence(result.convergence);}catch{status.textContent='Convergence decision failed';}finally{if(pendingConvergence){"
            "approveConvergence.disabled=false;rejectConvergence.disabled=false;}}};approveConvergence.addEventListener('click',()=>decideConvergence('approve'));"
            "rejectConvergence.addEventListener('click',()=>decideConvergence('reject'));"
            "const renderAgentPresence=review=>{const value=review?.agent_presence?.status,state=['ready','working'].includes(value)?value:'not_connected';"
            "agentPresence.dataset.status=state;agentPresence.textContent=state==='ready'?'Agent ready':state==='working'?'Agent working':'Agent not connected';"
            "const count=Number.isInteger(review?.queued_events)&&review.queued_events>=0?review.queued_events:0;queuedCount.textContent=String(count);};"
            "const refreshSession=async()=>{const response=await fetch(sessionEndpoint);if(!response.ok)throw new Error('Studio session unavailable');const result=await response.json(),review=result.review;"
            "renderAgentPresence(review);const replies=review?.agent_replies||[];conversation.replaceChildren(...replies.map(reply=>{const item=document.createElement('li');"
            "item.textContent='Agent: '+reply;return item;}));renderConvergence(review?.convergence);"
            "if(review?.revision!==revision){if(retainedContext?.route&&review?.routes?.includes(retainedContext.route)){"
            "sessionStorage.setItem('viewspec-context-restore',JSON.stringify(retainedContext));}else{sessionStorage.setItem('viewspec-context-reset','1');}"
            "location.reload();}return review;};setInterval(()=>{refreshSession().catch(()=>{});},500);refreshSession().catch(()=>{});history.scrollRestoration='manual';addEventListener('pageshow',lockCanvasOrigin);loadInspection();lockCanvasOrigin();fitCanvas();if('ResizeObserver'in window)new ResizeObserver(fitCanvas).observe(canvas);else addEventListener('resize',fitCanvas);"
            "frames.forEach(item=>item.addEventListener('load',fitCanvas));frames.forEach(item=>{item.src=item.dataset.src||'';});"
            + share_script
            + "})();"
        )
        html = (
            f"<!doctype html><html lang=en data-studio-viewport={'mobile' if comparison else 'desktop'}><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>ViewSpec Studio</title>"
            f"<style>{style}</style></head><body><a class=skip href=#studio-main>Skip to canvas</a>"
            "<header class=toolbar><div class=brand><span class=mark>V</span><div><strong>ViewSpec Studio</strong>"
            f"<small>revision {self.runtime.built.revision.number} · local creation session</small></div></div>"
            f"<details class=confidence data-studio-proof='{verification_status}' data-studio-comparison='{str(comparison).lower()}'><summary>Checked · Local · {verification_label}</summary>"
            f"<div><p>{verification_detail}</p><small>Generated output remains immutable. Comments resolve to semantic source identity.</small></div></details>"
            "<div class=controls><label for=viewport>Canvas</label><select id=viewport>"
            + (
                "<option value=mobile selected>Mobile · 390</option>"
                if comparison
                else "<option value=mobile>Mobile · 390</option>"
            )
            + "<option value=tablet>Tablet · 768</option>"
            + (
                "<option value=desktop>Desktop · 1440</option>"
                if comparison
                else "<option value=desktop selected>Desktop · 1440</option>"
            )
            + "</select>"
            "<button id=panel-toggle class=secondary type=button aria-controls=studio-panel aria-expanded=false>Details</button>"
            + share_button_markup
            + "<button id=mode class=primary type=button>Comment</button></div></header>"
            "<div class=statusbar><span id=status class=notice aria-live=polite>Preview mode · interactions are live</span>"
            f"<div class=status-meta><span id=agent-presence class=agent-presence data-status='{initial_agent_presence}' aria-live=polite>{initial_agent_label}</span>"
            f"<span class=revision>Requests: <strong id=queued>{initial_queued_events}</strong></span></div></div>"
            f"<main class=layout id=studio-main><section class=canvas aria-label='Checked interface canvas'><div class=stage>{frame_markup}</div></section>"
            "<aside id=studio-panel class=panel aria-hidden=true inert><header class=panel-head><div><span class=eyebrow>One continuous loop</span>"
            "<h1>Point. Ask. Approve.</h1></div><button id=panel-close class='secondary panel-close' type=button>Close</button></header>"
            "<p class=panel-intro>Proof, requests, and decisions appear here when they matter.</p>"
            + (
                "<div id=surface-tools class=surface-tools><label for=surface-preview>Preview surface<select id=surface-preview>"
                "<option value=react-tailwind-app selected>Live</option><option value=html-tailwind-app>Static</option></select></label>"
                "<p>Both targets remain mounted and checked; this chooses the one you inspect.</p></div>"
                if comparison
                else ""
            )
            + "<section id=coherence class=panel-section hidden><h2>Target coherence</h2><div id=coherence-card class=coherence-card data-status=checking>"
            "<strong id=coherence-summary>Checking Static + React…</strong><p id=coherence-detail>Comparing visible semantic geometry at this canvas size.</p>"
            "<div class=coherence-actions><button id=coherence-review class=primary type=button hidden>Review this</button>"
            "<button id=coherence-recheck class=secondary type=button>Recheck targets</button></div></div>"
            "<small class=coherence-scope>Observed in this browser from exact semantic pairs. This is geometry and typography coherence, not pixel-perfect parity.</small></section>"
            "<section id=inspection class=panel-section hidden><h2>State &amp; data</h2><p id=inspection-summary class=inspection-summary></p>"
            "<div id=replay-tools hidden><div class=replay-row><label for=replay-checkpoint>Declared checkpoint<select id=replay-checkpoint></select></label>"
            "<button id=run-replay class=secondary type=button>Show</button></div><p id=replay-proof class=proof-note></p></div>"
            "<div id=resource-card class=inspection-card hidden aria-live=polite></div></section>"
            + share_panel_markup
            + "<section class=panel-section><h2>Conversation</h2><ol id=conversation class=conversation aria-live=polite></ol></section>"
            "<section id=convergence class=panel-section hidden><h2>Ready for your decision</h2><p id=convergence-summary></p>"
            "<h3>What changes</h3><div id=convergence-diff class=change-list></div><h3>Why it is safe to review</h3>"
            "<p id=convergence-proof class=proof-result></p><div class=action-row>"
            "<button id=approve-convergence class=primary type=button>Approve change</button>"
            "<button id=reject-convergence class=secondary type=button>Reject</button></div></section>"
            "<section id=composer class=panel-section hidden><h2>Ask for one change</h2>"
            "<p>Describe the outcome you want. Your agent receives this selection’s exact semantic identity.</p>"
            "<div class=selection-card aria-live=polite><span id=selection-kicker class=selection-kicker></span><strong id=selection-title></strong><span id=selection-detail></span></div>"
            "<details class=selection-details><summary>Exact selection details</summary><pre id=trace class=trace></pre></details>"
            "<button id=page-target class=secondary type=button>Comment on the whole page</button>"
            "<label for=feedback>What should be different?</label><textarea id=feedback maxlength=8192 "
            "placeholder='Make the escalation action unmistakable on mobile.'></textarea>"
            "<details class=advanced><summary>Comment type</summary><label for=kind>Type</label>"
            "<select id=kind><option value=change_request>Change request</option><option value=question>Question</option>"
            "<option value=approval>Approval</option><option value=note>Note</option></select></details>"
            "<div class=action-row><button id=send class=primary type=button>Send to agent</button>"
            "<button id=send-end class=secondary type=button>Send &amp; end</button></div></section></aside></main>"
            f"<script>{script}</script></body></html>"
        ).encode("utf-8")
        csp = (
            "default-src 'none'; "
            f"script-src {_csp_hash(script.encode())}; style-src {_csp_hash(style.encode())}; "
            "img-src 'self' data:; connect-src 'self'; frame-src 'self'; base-uri 'none'; "
            "form-action 'none'; object-src 'none'"
        )
        return _Response(200, (("Content-Type", "text/html; charset=utf-8"), ("Content-Security-Policy", csp)), html)

    def _browser_status(self) -> dict[str, object]:
        status = self.runtime.status()
        comparison = self.runtime.built.revision.target == STUDIO_COMPARE_TARGET
        try:
            convergence: dict[str, object] | None = _browser_convergence_projection(
                get_convergence_status(
                    self.runtime.configuration.source_path,
                    state_root=self.runtime.configuration.convergence_state_root,
                )
            )
        except ConvergeError as exc:
            convergence = None if exc.code == "CONVERGE_SESSION_NOT_FOUND" else {
                "status": "unavailable",
                "error_code": exc.code,
            }
        status.update(
            {
                "frame_path": self.frame_path("static/index.html" if comparison else "index.html"),
                "comparison_frame_path": self.frame_path("react/index.html") if comparison else None,
                "frame_nonce": self.frame_nonce,
                "routes": list(self.runtime.routes),
                "inspection": _inspection_summary(self.runtime.inspection),
                "agent_presence": self._agent_presence(),
                "agent_replies": list(self.runtime.session.agent_replies[-4:]),
                "convergence": convergence,
                "share": self._share_status(),
            }
        )
        return status

    def _agent_presence(self) -> dict[str, object]:
        """Project only delivery-lease truth into the browser session."""

        with self._lock:
            if self.runtime.session.outstanding_batch is not None:
                status = "working"
            elif self._active_polls > 0:
                status = "ready"
            else:
                status = "not_connected"
        return {"status": status}

    def _agent_status(self) -> dict[str, object]:
        return {
            **self.runtime.status(),
            "browser_ready": self.browser_ready,
            "response_error": self.last_response_error,
            "share": self._share_status(),
        }

    def _share_status(self) -> dict[str, object]:
        if self._share_publisher is None:
            return {"status": "unavailable", "reason": "production_canary_required"}
        if self.runtime.built.revision.target != STUDIO_COMPARE_TARGET:
            return {"status": "unavailable", "reason": "checked_comparison_required"}
        try:
            value = self._share_publisher.status()
        except ReviewContractError as exc:
            return {"status": "unavailable", "reason": "production_canary_required", "error_code": exc.code}
        if not isinstance(value, dict) or value.get("status") != "available":
            return {"status": "unavailable", "reason": "production_canary_required"}
        return value

    def _require_share_publisher(self) -> _StudioSharePublisher:
        if self._share_status().get("status") != "available" or self._share_publisher is None:
            raise ReviewContractError(
                "STUDIO_SHARE_UNAVAILABLE",
                "Private Share is unavailable until the canonical production canary authorizes it.",
                "Restart Studio with --share after the production private-review release is healthy.",
                http_status=404,
            )
        return self._share_publisher

    def _serve_frame(self, path: str) -> _Response:
        self._ensure_revision_capabilities()
        prefix = "/frame/"
        components = path[len(prefix) :].split("/", 2)
        if len(components) != 3:
            raise _artifact_not_found()
        ticket, revision_text, raw_relative = components
        if not _compare_token(ticket, self._frame_ticket_digest):
            raise ReviewContractError(
                "REVIEW_CAPABILITY_INVALID",
                "Frame capability is invalid or expired.",
                "Reload the current Review page.",
                http_status=403,
            )
        if self._clock() > self._frame_ticket_expires:
            raise ReviewContractError(
                "REVIEW_CAPABILITY_INVALID",
                "Frame capability is invalid or expired.",
                "Reload the current Review page.",
                http_status=403,
            )
        if revision_text != str(self.runtime.built.revision.number):
            raise ReviewContractError(
                "REVIEW_REVISION_MISMATCH",
                "Frame capability does not name the current Review revision.",
                "Reload the current Review page and use its new frame ticket.",
                http_status=409,
            )
        relative = _canonical_frame_path(raw_relative)
        entry = self._allowlist.get(relative)
        if entry is None:
            raise _artifact_not_found()
        content = _read_exact_artifact(entry)
        headers: list[tuple[str, str]] = [("Content-Type", entry.content_type)]
        if entry.content_type.startswith("text/html"):
            with self._lock:
                if self._frame_first_served_at is None:
                    self._frame_first_served_at = self._clock()
            if self.runtime.built.revision.target == STUDIO_COMPARE_TARGET:
                surface_target = "react-tailwind-app" if relative.startswith("react/") else "html-tailwind-app"
            else:
                surface_target = self.runtime.built.revision.target
            if surface_target == "react-tailwind-app":
                initial_route = self.runtime.initial_route
                first_script = content.find(b"<script")
                if initial_route is None or first_script < 0:
                    raise ReviewContractError(
                        "REVIEW_REVISION_IDENTITY_MISMATCH",
                        "Checked React artifact cannot receive its declared initial route before mounting.",
                        "Rebuild the complete checked AppBundle before serving this revision.",
                        http_status=500,
                    )
                initial_route_script = (
                    b'<script id="viewspec-initial-route">window.__viewspecInitialPath='
                    + json.dumps(initial_route).encode("utf-8")
                    + b";</script>"
                )
                content = content[:first_script] + initial_route_script + content[first_script:]
            annotation_style = _frame_annotation_style()
            style_marker = b"</head>"
            style_injection = b'<style id="viewspec-review-style">' + annotation_style + b"</style>"
            content = (
                content.replace(style_marker, style_injection + style_marker, 1)
                if style_marker in content
                else style_injection + content
            )
            sdk = _frame_sdk(self.frame_nonce, surface_target=surface_target)
            marker = b"</body>"
            injection = b'<script id="viewspec-review-sdk">' + sdk + b"</script>"
            content = content.replace(marker, injection + marker, 1) if marker in content else content + injection
            script_hashes = [_csp_hash(sdk), *_inline_hashes(content, b"script")]
            style_element_hashes = _inline_hashes(content, b"style")
            style_attribute_hashes = _inline_style_attribute_hashes(content)
            style_attribute_policy = (
                "'unsafe-hashes' " + " ".join(sorted(set(style_attribute_hashes)))
                if style_attribute_hashes
                else "'none'"
            )
            csp = (
                "default-src 'none'; script-src "
                + " ".join(sorted(set(script_hashes)))
                + "; style-src "
                + " ".join(sorted(set(style_element_hashes)))
                + "; style-src-elem "
                + " ".join(sorted(set(style_element_hashes)))
                + "; style-src-attr "
                + style_attribute_policy
                + "; img-src 'self' data:; font-src 'self'; connect-src 'none'; base-uri 'none'; "
                "form-action 'none'; object-src 'none'"
            )
            headers.append(("Content-Security-Policy", csp))
        return _Response(200, tuple(headers), content)

    def _ensure_revision_capabilities(self) -> None:
        with self._lock:
            if self._capability_revision != self.runtime.built.revision.number:
                self._rotate_revision_capabilities()

    def _rotate_revision_capabilities(self) -> None:
        self._capability_revision = self.runtime.built.revision.number
        self._frame_ticket = _token()
        self._frame_ticket_digest = _digest_token(self._frame_ticket)
        self._frame_ticket_expires = self._clock() + FRAME_TICKET_LIFETIME_SECONDS
        self.frame_nonce = _token()
        self._frame_first_served_at = None
        self._handshake_revision = None
        self._allowlist = _artifact_allowlist(self.runtime.built.artifact_dir)

    def _complete_frame_handshake(self, raw_targets: object) -> None:
        expected_targets = (
            {"html-tailwind-app", "react-tailwind-app"}
            if self.runtime.built.revision.target == STUDIO_COMPARE_TARGET
            else {self.runtime.built.revision.target}
        )
        if not isinstance(raw_targets, list) or len(raw_targets) != len(expected_targets):
            raise _http_error(400, "REVIEW_REQUEST_INVALID", "Browser handshake target count is invalid.")
        observed_targets: set[str] = set()
        for raw_target in raw_targets:
            if not isinstance(raw_target, dict) or set(raw_target) != {"target", "route", "screen_id"}:
                raise _http_error(400, "REVIEW_REQUEST_INVALID", "Browser handshake target shape is invalid.")
            target = raw_target.get("target")
            route = raw_target.get("route")
            screen_id = raw_target.get("screen_id")
            if (
                not isinstance(target, str)
                or len(target) > 64
                or target in observed_targets
                or (route is not None and (not isinstance(route, str) or len(route.encode("utf-8")) > 512))
                or (
                    screen_id is not None
                    and (not isinstance(screen_id, str) or len(screen_id.encode("utf-8")) > 256)
                )
            ):
                raise _http_error(400, "REVIEW_REQUEST_INVALID", "Browser handshake target value is invalid.")
            observed_targets.add(target)
            if self.runtime.built.revision.source_kind == "app_bundle":
                if not isinstance(route, str) or not isinstance(screen_id, str):
                    raise _http_error(
                        422,
                        "REVIEW_CONTEXT_FORBIDDEN",
                        "Checked AppBundle target did not render one declared route and semantic screen.",
                    )
                if self.runtime.screen_for_route(route) != screen_id:
                    raise _http_error(
                        422,
                        "REVIEW_CONTEXT_FORBIDDEN",
                        "Browser target route does not match its checked semantic screen.",
                    )
        if observed_targets != expected_targets:
            raise _http_error(400, "REVIEW_REQUEST_INVALID", "Browser handshake targets are incomplete or unexpected.")
        now = self._clock()
        with self._lock:
            if self._frame_first_served_at is None or now - self._frame_first_served_at > FRAME_HANDSHAKE_SECONDS:
                raise make_review_error(
                    "REVIEW_BROWSER_HANDSHAKE_TIMEOUT",
                    "The current checked frame did not handshake within 5 seconds of its first HTML response.",
                )
            self._handshake_revision = self.runtime.built.revision.number

    def _require_frame_handshake(self) -> None:
        with self._lock:
            if self._handshake_revision != self.runtime.built.revision.number:
                raise make_review_error(
                    "REVIEW_BROWSER_HANDSHAKE_TIMEOUT",
                    "The current checked frame has not completed its revision-scoped handshake.",
                )

    def _send(self, handler: BaseHTTPRequestHandler, response: _Response) -> None:
        started = self._clock()
        try:
            handler.connection.settimeout(5)
            handler.send_response(response.status)
            headers = list(response.headers)
            headers.extend(
                (
                    ("Cache-Control", "no-store"),
                    ("Referrer-Policy", "no-referrer"),
                    ("X-Content-Type-Options", "nosniff"),
                    ("Connection", "close"),
                )
            )
            for key, value in headers:
                handler.send_header(key, value)
            handler.send_header("Content-Length", str(len(response.body)))
            handler.end_headers()
            for offset in range(0, len(response.body), STREAM_CHUNK_BYTES):
                if self._clock() - started > 30:
                    raise TimeoutError("Review response exceeded its 30-second total deadline")
                handler.wfile.write(response.body[offset : offset + STREAM_CHUNK_BYTES])
            handler.close_connection = True
        except (OSError, TimeoutError):
            self.last_response_error = "REVIEW_RESPONSE_TIMEOUT"
            handler.close_connection = True


def _artifact_allowlist(root: Path) -> dict[str, _ArtifactEntry]:
    result: dict[str, _ArtifactEntry] = {}
    for path in root.rglob("*"):
        value = path.lstat()
        if stat.S_ISDIR(value.st_mode) and not stat.S_ISLNK(value.st_mode):
            continue
        if not stat.S_ISREG(value.st_mode) or stat.S_ISLNK(value.st_mode):
            raise ReviewContractError(
                "REVIEW_ARTIFACT_NOT_FOUND",
                "Promoted artifact allowlist contains a non-regular entry.",
                "Rebuild the checked revision before serving it.",
                http_status=404,
            )
        relative = path.relative_to(root).as_posix()
        content_type = mimetypes.guess_type(relative)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/json", "application/javascript"}:
            content_type += "; charset=utf-8"
        result[relative] = _ArtifactEntry(path, value.st_size, _sha256_path(path), content_type)
    return result


def _read_exact_artifact(entry: _ArtifactEntry) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O" + "_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(entry.path, flags)
        value = os.fstat(descriptor)
        if not stat.S_ISREG(value.st_mode) or value.st_size != entry.size:
            raise _artifact_not_found()
        digest = hashlib.sha256()
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, STREAM_CHUNK_BYTES):
            chunks.append(chunk)
            digest.update(chunk)
        if not hmac.compare_digest(digest.hexdigest(), entry.sha256):
            raise _artifact_not_found()
        return b"".join(chunks)
    except ReviewContractError:
        raise
    except OSError as exc:
        raise _artifact_not_found() from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _canonical_frame_path(raw: str) -> str:
    if not raw or "\\" in raw or "\x00" in raw:
        raise _artifact_not_found()
    try:
        decoded = unquote_to_bytes(raw).decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise _artifact_not_found() from exc
    if decoded.startswith("/") or "\\" in decoded or "\x00" in decoded:
        raise _artifact_not_found()
    parts = decoded.split("/")
    if any(not part or part in {".", ".."} or _SAFE_FRAME_SEGMENT.fullmatch(part) is None for part in parts):
        raise _artifact_not_found()
    return "/".join(parts)


def _browser_convergence_projection(session: ConvergenceSession) -> dict[str, object]:
    pending: dict[str, object] | None = None
    if session.pending_preview is not None:
        preview = session.pending_preview
        pending = {
            "schema_version": 1,
            "preview_id": preview.preview_id,
            "attempt": preview.attempt,
            "base_source_sha256": preview.base_source_sha256,
            "candidate_source_sha256": preview.candidate_source_sha256,
            "semantic_diff": preview.semantic_diff,
            "compile_check": preview.compile_check,
            "progress_certificate": preview.progress_certificate.to_json(),
        }
    return {
        "schema_version": session.schema_version,
        "session_id": session.session_id,
        "status": session.status,
        "mode": session.mode,
        "attempt_count": session.attempt_count,
        "expires_at": session.expires_at,
        "terminal_reason": session.terminal_reason,
        "pending_preview": pending,
    }


def _convergence_review_error(error: ConvergeError) -> ReviewContractError:
    if error.code == "CONVERGE_SESSION_NOT_FOUND":
        status = 404
    elif error.code in {"CONVERGE_STATE_CORRUPT", "CONVERGE_STATE_IO"}:
        status = 500
    elif error.code in {
        "CONVERGE_APPROVAL_INVALID",
        "CONVERGE_PREVIEW_INVALID",
        "CONVERGE_SESSION_EXPIRED",
        "CONVERGE_SESSION_STATUS",
        "CONVERGE_SOURCE_CHANGED",
    }:
        status = 409
    else:
        status = 422
    return ReviewContractError(
        error.code,
        error.message,
        error.fix,
        http_status=status,
        cli_exit=error.cli_exit,
    )


def _exact_preview_request(content: bytes) -> str:
    payload = _json_object(content)
    preview_id = payload.get("preview_id")
    if set(payload) != {"preview_id"} or not isinstance(preview_id, str):
        raise _http_error(
            400,
            "REVIEW_REQUEST_INVALID",
            "Convergence decision requires exactly one preview_id string.",
        )
    return preview_id


def _json_object(content: bytes) -> dict[str, object]:
    try:
        value = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_constant,
            parse_float=_reject_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError, MemoryError) as exc:
        raise _http_error(400, "REVIEW_REQUEST_INVALID", f"Review request is not strict UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise _http_error(400, "REVIEW_REQUEST_INVALID", "Review request JSON root must be an object.")
    _validate_json_shape(value)
    return value


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value}")


def _reject_float(value: str) -> None:
    raise ValueError(f"floating-point JSON number {value}")


def _validate_json_shape(root: dict[str, object]) -> None:
    stack: list[tuple[object, int]] = [(root, 1)]
    values = 0
    while stack:
        value, depth = stack.pop()
        values += 1
        if values > 4096:
            raise _http_error(400, "REVIEW_REQUEST_INVALID", "Review request JSON contains more than 4096 values.")
        if isinstance(value, dict):
            if depth > 16:
                raise _http_error(400, "REVIEW_REQUEST_INVALID", "Review request JSON exceeds 16 container levels.")
            for key, child in value.items():
                if len(key.encode("utf-8")) > 128:
                    raise _http_error(400, "REVIEW_REQUEST_INVALID", "Review request JSON key exceeds 128 bytes.")
                stack.append((child, depth + 1 if isinstance(child, (dict, list)) else depth))
        elif isinstance(value, list):
            if depth > 16 or len(value) > 256:
                raise _http_error(400, "REVIEW_REQUEST_INVALID", "Review request JSON array or depth exceeds V0 bounds.")
            stack.extend((child, depth + 1 if isinstance(child, (dict, list)) else depth) for child in value)
        elif isinstance(value, str):
            if len(value.encode("utf-8")) > 8 * 1024:
                raise _http_error(400, "REVIEW_REQUEST_INVALID", "Review request JSON string exceeds 8 KiB.")
        elif type(value) is int and not -(2**63) <= value <= (2**63 - 1):
            raise _http_error(400, "REVIEW_REQUEST_INVALID", "Review request JSON integer is outside signed 64-bit range.")


def _json_response(status: int, payload: dict[str, object]) -> _Response:
    body = canonical_json_bytes(payload)
    if len(body) > MAX_JSON_RESPONSE_BYTES:
        raise ReviewContractError(
            "REVIEW_RESPONSE_TOO_LARGE",
            "Review JSON response exceeds 256 KiB.",
            "Request a smaller bounded Review projection.",
            http_status=500,
            cli_exit=1,
        )
    return _Response(status, (("Content-Type", "application/json; charset=utf-8"),), body)


def _error_response(error: ReviewContractError) -> _Response:
    return _json_response(
        error.http_status or 400,
        {"schema_version": 1, "ok": False, "error": error.to_json()},
    )


def _inspection_summary(inspection: dict[str, object] | None) -> dict[str, object] | None:
    if not isinstance(inspection, dict):
        return None
    state = inspection.get("state") if isinstance(inspection.get("state"), dict) else {}
    resources = inspection.get("resources") if isinstance(inspection.get("resources"), dict) else {}
    replays = state.get("replays") if isinstance(state.get("replays"), list) else []
    return {
        "status": "ready",
        "state_status": state.get("status"),
        "replay_count": len(replays),
        "resource_status": resources.get("status"),
        "resource_assertion_count": resources.get("assertion_count", 0),
        "production_data": "not_claimed",
    }


def _busy_response(message: str) -> _Response:
    return _Response(
        503,
        (("Content-Type", "application/json; charset=utf-8"), ("Retry-After", "1")),
        canonical_json_bytes(
            {
                "schema_version": 1,
                "ok": False,
                "error": {
                    "code": "REVIEW_SERVER_BUSY",
                    "message": message,
                    "fix": "Retry after one second.",
                },
            }
        ),
    )


def _http_error(status: int, code: str, message: str) -> ReviewContractError:
    return ReviewContractError(code, message, "Correct the bounded local request and retry.", http_status=status)


def _forbidden(message: str) -> ReviewContractError:
    return ReviewContractError(
        "REVIEW_REQUEST_FORBIDDEN",
        message,
        "Use the exact current loopback Review page and capability context.",
        http_status=403,
    )


def _artifact_not_found() -> ReviewContractError:
    return ReviewContractError(
        "REVIEW_ARTIFACT_NOT_FOUND",
        "Requested Review artifact is absent, changed, or outside the promoted allowlist.",
        "Reload the exact current checked revision.",
        http_status=404,
    )


def _configuration_token_bytes(value: str) -> bytes:
    try:
        if len(value) != 32:
            return b""
        return bytes.fromhex(value)
    except ValueError:
        return b""


def _token() -> str:
    try:
        return secrets.token_hex(16)
    except Exception as exc:
        raise ReviewContractError(
            "REVIEW_ENTROPY_UNAVAILABLE",
            "Operating-system cryptographic entropy is unavailable for a Review capability.",
            "Abort without emitting a capability and retry only after OS entropy is healthy.",
            http_status=500,
            cli_exit=1,
        ) from exc


def _digest_token(value: str) -> bytes:
    return hashlib.sha256(_configuration_token_bytes(value)).digest()


def _compare_token(value: str, digest: bytes) -> bool:
    candidate = _configuration_token_bytes(value)
    return bool(candidate) and hmac.compare_digest(hashlib.sha256(candidate).digest(), digest)


def _cookie_values(raw: str, name: str) -> list[str]:
    values: list[str] = []
    for item in raw.split(";"):
        key, separator, value = item.strip().partition("=")
        if separator and key == name:
            values.append(value)
    return values


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(STREAM_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _frame_sdk(nonce: str, *, surface_target: str) -> bytes:
    if surface_target not in {"html-tailwind", "html-tailwind-app", "react-tailwind-app"}:
        raise ValueError("unsupported Review frame surface target")
    return (
        "(()=>{const n='"
        + nonce
        + "',surface='"
        + surface_target
        + "';let annotate=false,cursor=-1,selectedElement=null;const parent=window.parent,ids=()=>Array.from(document.querySelectorAll('[id]'))"
        ".filter(e=>e.id&&e.id.length<=256);const viewport=()=>{const w=innerWidth,h=innerHeight;"
        "if(Math.abs(w-390)<=1&&Math.abs(h-844)<=1)return{name:'mobile',width:390,height:844};"
        "if(Math.abs(w-768)<=1&&Math.abs(h-1024)<=1)return{name:'tablet',width:768,height:1024};"
        "if(Math.abs(w-1440)<=1&&Math.abs(h-1000)<=1)return{name:'desktop',width:1440,height:1000};return null;};"
        "const rounded=value=>Math.round(value*10)/10;const compactText=value=>String(value||'').replace(/\\s+/g,' ').trim().slice(0,96);"
        "const measureCoherence=probeId=>{const measured=viewport();if(!measured){parent.postMessage({type:'viewspec-review-viewport-mismatch',nonce:n},'*');return;}"
        "const items=Array.from(document.querySelectorAll('[id][data-ir-id]')).filter(element=>{const box=element.getBoundingClientRect(),style=getComputedStyle(element);"
        "return element.id.length<=256&&element.dataset.irId&&element.dataset.irId.length<=256&&style.display!=='none'&&style.visibility!=='hidden'&&box.width>0&&box.height>0;}).slice(0,512).map(element=>{"
        "const box=element.getBoundingClientRect(),style=getComputedStyle(element),screen=element.closest('[data-viewspec-app-screen]');return{dom_id:element.id,ir_id:element.dataset.irId,"
        "screen_id:screen?.dataset.viewspecAppScreen||null,tag:element.tagName.toLowerCase(),leaf:!element.querySelector('[id][data-ir-id]'),text:compactText(element.textContent),"
        "rect:{x:rounded(box.x),y:rounded(box.y),width:rounded(box.width),height:rounded(box.height)},font_size:rounded(Number.parseFloat(style.fontSize)||0),"
        "font_weight:Number.parseInt(style.fontWeight,10)||400,color:String(style.color||'').slice(0,64)};});"
        "parent.postMessage({type:'viewspec-studio-coherence-result',nonce:n,surface_target:surface,probe_id:probeId,viewport:measured,items},'*');};"
        "const setSelected=element=>{selectedElement?.removeAttribute('data-viewspec-review-selected');selectedElement=element;selectedElement?.setAttribute('data-viewspec-review-selected','true');};"
        "const choose=element=>{const measured=viewport();if(!measured){parent.postMessage({type:'viewspec-review-viewport-mismatch',nonce:n},'*');return;}"
        "setSelected(element);"
        "const chain=[];let p=element;"
        "while(p&&p!==document.documentElement&&chain.length<32){if(p.id)chain.push(p.id);p=p.parentElement;}"
        "const screen=element.closest('[data-viewspec-app-screen]'),selection=getSelection(),text=element.textContent||'',quote=selection?selection.toString():'';"
        "let selected_text=null;if(quote&&selection&&element.contains(selection.anchorNode)&&element.contains(selection.focusNode)){const at=text.indexOf(quote);"
        "if(at>=0)selected_text={quote,prefix:text.slice(Math.max(0,at-512),at),suffix:text.slice(at+quote.length,at+quote.length+512)};}"
        "parent.postMessage({type:'viewspec-review-selected',nonce:n,surface_target:surface,dom_ancestors:chain,page_level:chain.length===0,"
        "screen_id:screen?screen.dataset.viewspecAppScreen:null,route:screen?(screen.dataset.routePath||location.pathname):null,viewport:measured,selected_text,"
        "rendered_text:text.trim().slice(0,2048),visibility:element.getClientRects().length?'visible':'hidden'},'*');};"
        "let restoringHash=null,restoringHistory=false;const postContext=(cause='passive',routeOverride=null)=>{const screen=document.querySelector('[data-viewspec-app-screen]:not([hidden])')||document.querySelector('[data-viewspec-app-screen]');"
        "parent.postMessage({type:'viewspec-review-context',nonce:n,surface_target:surface,sync_cause:cause,route:routeOverride??(screen?(screen.dataset.routePath||location.pathname):null),"
        "scroll_x:Math.max(0,Math.min(1000000,Math.trunc(scrollX))),scroll_y:Math.max(0,Math.min(1000000,Math.trunc(scrollY)))},'*');};"
        "const nativePush=history.pushState.bind(history),nativeReplace=history.replaceState.bind(history);"
        "const nextFrame=()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));"
        "const applyReplay=async data=>{try{for(const event of data.events||[]){if(!event||typeof event.route!=='string'||typeof event.action_id!=='string')throw new Error('invalid declared event');"
        "if(surface==='html-tailwind-app'){restoringHash=event.route;if(location.hash.slice(1)!==event.route)location.hash=event.route;else restoringHash=null;}"
        "else{restoringHistory=true;nativeReplace({},'',event.route);dispatchEvent(new PopStateEvent('popstate'));restoringHistory=false;}await nextFrame();"
        "for(const [binding,value] of Object.entries(event.payload_values||{})){const node=document.querySelector('[data-binding-id=\"'+CSS.escape(binding)+'\"]');"
        "const expected=value===null?'':(typeof value==='string'?value:JSON.stringify(value));if(!node||node.textContent.trim()!==expected)throw new Error('payload '+binding+' does not match the rendered binding');}"
        "const action=document.querySelector('[data-action-id=\"'+CSS.escape(event.action_id)+'\"]');if(!action)throw new Error('declared action '+event.action_id+' is not rendered');"
        "action.click();await nextFrame();}parent.postMessage({type:'viewspec-studio-replay-result',nonce:n,surface_target:surface,ok:true,evidence_ref:data.evidence_ref},'*');}"
        "catch(error){parent.postMessage({type:'viewspec-studio-replay-result',nonce:n,surface_target:surface,ok:false,reason:String(error?.message||error).slice(0,256)},'*');}};"
        "history.pushState=(...args)=>{nativePush(...args);requestAnimationFrame(()=>postContext('navigation',location.pathname));};"
        "history.replaceState=(...args)=>{nativeReplace(...args);requestAnimationFrame(()=>postContext(restoringHistory?'restore':'navigation',location.pathname));};"
        "addEventListener('message',e=>{if(e.source!==parent||!e.data||e.data.nonce!==n)return;"
        "if(e.data.type==='viewspec-studio-coherence-measure'&&typeof e.data.probe_id==='string'&&e.data.probe_id.length<=64){measureCoherence(e.data.probe_id);return;}"
        "if(e.data.type==='viewspec-studio-coherence-choose'){const id=e.data.dom_id;const element=typeof id==='string'&&id.length<=256?document.getElementById(id):null;if(element)choose(element);return;}"
        "if(e.data.type==='viewspec-review-mode'){annotate=!!e.data.annotate;document.documentElement.dataset.viewspecReviewMode=annotate?'annotate':'explore';"
        "if(!annotate)setSelected(null);return;}"
        "if(e.data.type==='viewspec-review-selection'){const id=e.data.dom_id;setSelected(typeof id==='string'&&id.length<=256?document.getElementById(id):null);return;}"
        "if(e.data.type==='viewspec-studio-replay-reset'){location.reload();return;}if(e.data.type==='viewspec-studio-replay-apply'){applyReplay(e.data);return;}"
        "if(e.data.type==='viewspec-review-restore'&&typeof e.data.route==='string'&&Number.isInteger(e.data.scroll_x)&&Number.isInteger(e.data.scroll_y)){"
        "if(surface==='html-tailwind-app'){restoringHash=e.data.route;if(location.hash.slice(1)!==e.data.route)location.hash=e.data.route;else restoringHash=null;}else{restoringHistory=true;nativeReplace({},'',e.data.route);dispatchEvent(new PopStateEvent('popstate'));}"
        "requestAnimationFrame(()=>{scrollTo(e.data.scroll_x,e.data.scroll_y);postContext('restore');restoringHistory=false;});}});"
        "addEventListener('popstate',()=>requestAnimationFrame(()=>postContext(restoringHistory?'restore':'navigation')));"
        "addEventListener('hashchange',()=>requestAnimationFrame(()=>{const route=location.hash.slice(1)||'/';const cause=restoringHash===route?'restore':'navigation';restoringHash=null;postContext(cause);}));"
        "addEventListener('scroll',()=>postContext('passive'),{passive:true});"
        "addEventListener('click',e=>{if(!annotate)return;e.preventDefault();e.stopImmediatePropagation();choose(e.target);},true);"
        "addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='i'){e.preventDefault();"
        "parent.postMessage({type:'viewspec-review-toggle',nonce:n,surface_target:surface},'*');return;}if(!annotate)return;const list=ids();"
        "if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();cursor=(cursor+(e.key==='ArrowDown'?1:-1)+list.length)%list.length;"
        "list[cursor]?.focus();}else if(e.key==='Enter'&&document.activeElement?.id){e.preventDefault();choose(document.activeElement);}});"
        "let readyAnnounced=false,readyCheckPending=false;const announceReady=async()=>{if(readyAnnounced||readyCheckPending)return readyAnnounced;readyCheckPending=true;await nextFrame();"
        "const readyScreen=document.querySelector('[data-viewspec-app-screen]:not([hidden])')||document.querySelector('[data-viewspec-app-screen]');readyCheckPending=false;"
        "if(surface!=='html-tailwind'&&!readyScreen)return false;readyAnnounced=true;"
        "const readyRoute=readyScreen?.dataset.routePath||(surface==='html-tailwind-app'?(location.hash.slice(1)||'/'):(window.__viewspecInitialPath||location.pathname));"
        "document.documentElement.dataset.viewspecReviewReady=surface;document.documentElement.dataset.viewspecReviewScreen=readyScreen?.dataset.viewspecAppScreen||'';"
        "postContext('initial');parent.postMessage({type:'viewspec-review-ready',nonce:n,surface_target:surface,screen_id:readyScreen?.dataset.viewspecAppScreen||null,route:readyRoute||null},'*');return true;};"
        "const readyObserver=new MutationObserver(()=>{announceReady().then(done=>{if(done)readyObserver.disconnect();});});readyObserver.observe(document.documentElement,{childList:true,subtree:true});"
        "if(document.readyState==='loading')addEventListener('DOMContentLoaded',()=>announceReady(),{once:true});else announceReady();"
        "setTimeout(()=>{if(!readyAnnounced){readyObserver.disconnect();parent.postMessage({type:'viewspec-review-render-failed',nonce:n,surface_target:surface},'*');}},4000);})();"
    ).encode("utf-8")


def _csp_hash(content: bytes) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(content).digest()).decode("ascii") + "'"


def _frame_annotation_style() -> bytes:
    return (
        b"html[data-viewspec-review-mode=annotate] [id]{cursor:crosshair!important}"
        b"[data-viewspec-review-selected=true]{outline:3px solid #ffbd4a!important;"
        b"outline-offset:3px!important;box-shadow:0 0 0 6px rgba(255,189,74,.24)!important}"
    )


def _inline_hashes(content: bytes, tag: bytes) -> list[str]:
    expression = re.compile(rb"<" + tag + rb"\b[^>]*>([\s\S]*?)</" + tag + rb">", re.IGNORECASE)
    return [_csp_hash(match.group(1)) for match in expression.finditer(content)]


class _InlineStyleAttributeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.values: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del tag
        self.values.extend(value for name, value in attrs if name.lower() == "style" and value is not None)

    handle_startendtag = handle_starttag


def _inline_style_attribute_hashes(content: bytes) -> list[str]:
    parser = _InlineStyleAttributeParser()
    parser.feed(content.decode("utf-8"))
    parser.close()
    return [_csp_hash(value.encode("utf-8")) for value in parser.values]


__all__ = [
    "BOOTSTRAP_LIFETIME_SECONDS",
    "AUTO_EXIT_GRACE_SECONDS",
    "COOKIE_IDLE_SECONDS",
    "COOKIE_MAX_AGE_SECONDS",
    "FRAME_TICKET_LIFETIME_SECONDS",
    "FRAME_HANDSHAKE_SECONDS",
    "MAX_REQUEST_BODY_BYTES",
    "MAX_POLL_TIMEOUT_MS",
    "SESSION_IDLE_SECONDS",
    "ReviewServer",
]
