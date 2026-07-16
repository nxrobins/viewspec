"""Capability-scoped loopback HTTP server for local ViewSpec Review V0."""

from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
import hmac
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
from typing import Callable
from urllib.parse import unquote_to_bytes, urlsplit

from viewspec.review_contract import ReviewContext, ReviewContractError, canonical_json_bytes
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


class ReviewServer:
    """One bounded HTTP server bound only to the literal IPv4 loopback address."""

    def __init__(
        self,
        runtime: ReviewRuntime,
        *,
        host: str = "127.0.0.1",
        port: int = 4388,
        clock: Callable[[], float] = time.monotonic,
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
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._poll_lock = threading.Lock()
        self._connection_slots = threading.BoundedSemaphore(32)
        self._browser_connection_slots = threading.BoundedSemaphore(2)
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
        if path == f"{review_root}api/v1/events":
            self._authorize_cookie(handler)
            events = [event.to_json() for event in self.runtime.session.events]
            return _json_response(200, {"schema_version": 1, "ok": True, "events": events})
        raise _artifact_not_found()

    def _handle_post(self, path: str, body: bytes, handler: BaseHTTPRequestHandler) -> _Response:
        review_root = f"/r/{self.runtime.session.review_id}/"
        if path == f"{review_root}api/v1/handshake":
            payload = _json_object(body)
            if payload:
                raise _http_error(400, "REVIEW_REQUEST_INVALID", "Browser handshake body must be an empty object.")
            self._complete_frame_handshake()
            return _json_response(
                200,
                {
                    "schema_version": 1,
                    "ok": True,
                    "revision": self.runtime.built.revision.number,
                },
            )
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
        frame = self.frame_path("index.html")
        endpoint = f"/r/{self.runtime.session.review_id}/api/v1/events"
        end_endpoint = f"/r/{self.runtime.session.review_id}/api/v1/end"
        handshake_endpoint = f"/r/{self.runtime.session.review_id}/api/v1/handshake"
        session_endpoint = f"/r/{self.runtime.session.review_id}/api/v1/session"
        style = (
            "body{margin:0;overflow-x:hidden;font:14px system-ui;background:#f6f7f9;color:#111827}"
            ".toolbar{display:flex;flex-wrap:wrap;align-items:center;gap:16px;padding:10px 16px;background:#111827;color:white}"
            ".toolbar button{padding:6px 10px}.layout{display:grid;grid-template-columns:minmax(0,1fr) 340px;height:calc(100vh - 44px)}"
            ".canvas{overflow:auto;padding:16px;background:#dfe3e8}iframe{display:block;width:1440px;height:1000px;border:0;background:white}"
            ".panel{padding:16px;border-left:1px solid #d1d5db;background:white;overflow:auto}"
            ".panel label{display:block;margin-top:12px;font-weight:600}.panel textarea,.panel select{box-sizing:border-box;width:100%;margin-top:4px}"
            ".panel textarea{min-height:110px}.trace{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f4f6;padding:10px}"
            "[hidden]{display:none!important}.notice{min-height:20px;color:#374151}"
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
            + "',revision="
            + str(self.runtime.built.revision.number)
            + ",frame=document.getElementById('artifact'),mode=document.getElementById('mode'),"
            "status=document.getElementById('status'),composer=document.getElementById('composer'),trace=document.getElementById('trace'),"
            "conversation=document.getElementById('conversation');"
            "let annotate=false,selection=null,queued=0,retainedContext=null,restoreContext=null;"
            "try{restoreContext=JSON.parse(sessionStorage.getItem('viewspec-context-restore')||'null');}catch{}"
            "sessionStorage.removeItem('viewspec-context-restore');const reset=sessionStorage.getItem('viewspec-context-reset');"
            "if(reset){sessionStorage.removeItem('viewspec-context-reset');status.textContent='REVIEW_CONTEXT_RESET';}"
            "const hex=b=>Array.from(b,x=>x.toString(16).padStart(2,'0')).join('');"
            "document.getElementById('viewport').addEventListener('change',e=>{const d={mobile:[390,844],tablet:[768,1024],desktop:[1440,1000]}[e.target.value];"
            "frame.style.width=d[0]+'px';frame.style.height=d[1]+'px';status.textContent='Viewport '+e.target.value;});"
            "const toggle=()=>{annotate=!annotate;mode.textContent=annotate?'Explore':'Annotate';status.textContent=annotate?'Annotate mode':'Explore mode';"
            "frame.contentWindow.postMessage({type:'viewspec-review-mode',nonce,annotate},'*');};mode.addEventListener('click',toggle);"
            "addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='i'){e.preventDefault();toggle();}});"
            "addEventListener('message',e=>{if(e.source!==frame.contentWindow||!e.data||e.data.nonce!==nonce)return;"
            "if(e.data.type==='viewspec-review-ready'){status.textContent='Checking frame…';fetch(handshakeEndpoint,{method:'POST',"
            "headers:{'Content-Type':'application/json','X-ViewSpec-Frame-Nonce':nonce},body:'{}'}).then(async response=>{"
            "const result=await response.json();status.textContent=response.ok?(reset?'REVIEW_CONTEXT_RESET':'Checked frame ready'):(result.error?.code||'Frame handshake failed');"
            "if(response.ok&&restoreContext)frame.contentWindow.postMessage({type:'viewspec-review-restore',nonce,...restoreContext},'*');})"
            ".catch(()=>{status.textContent='REVIEW_BROWSER_HANDSHAKE_TIMEOUT';});return;}"
            "if(e.data.type==='viewspec-review-viewport-mismatch'){status.textContent='REVIEW_VIEWPORT_MISMATCH';return;}"
            "if(e.data.type==='viewspec-review-context'){retainedContext={route:e.data.route,scroll_x:e.data.scroll_x,scroll_y:e.data.scroll_y};return;}"
            "if(e.data.type==='viewspec-review-toggle'){toggle();return;}if(e.data.type!=='viewspec-review-selected')return;"
            "selection=e.data;composer.hidden=false;trace.textContent=['DOM: '+(e.data.dom_ancestors[0]||'page'),"
            "'Screen: '+(e.data.screen_id||'standalone'),'Revision: "
            + str(self.runtime.built.revision.number)
            + "'].join('\\n');document.getElementById('feedback').focus();});"
            "document.getElementById('page-target').addEventListener('click',()=>{if(!selection)return;selection={...selection,page_level:true,dom_ancestors:[]};"
            "trace.textContent='Page-level annotation (explicit fallback)';status.textContent='Page target selected';});"
            "const submit=async end=>{if(!selection)return;const feedback=document.getElementById('feedback'),body=feedback.value;"
            "if(!body){status.textContent='Feedback text is required';return;}let selected_text=null;if(selection.selected_text){const q=selection.selected_text.quote;"
            "const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(q));selected_text={...selection.selected_text,sha256:hex(new Uint8Array(digest))};}"
            "let payload={kind:document.getElementById('kind').value,body,screen_id:selection.screen_id,dom_ancestors:selection.dom_ancestors,"
            "page_level:selection.page_level,client_provenance:{},context:{route:selection.route,screen_id:selection.screen_id,viewport:selection.viewport,"
            "selected_text,control_values:{},visibility:selection.visibility,evidence_refs:[]}};const key=hex(crypto.getRandomValues(new Uint8Array(16)));"
            "if(end)payload={actor:'human',...payload};const response=await fetch(end?endEndpoint:endpoint,{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':key,'X-ViewSpec-Frame-Nonce':nonce},"
            "body:JSON.stringify(payload)});const result=await response.json();if(!response.ok){status.textContent=result.error?.code||'Submission failed';return;}"
            "queued++;document.getElementById('queued').textContent=String(queued);feedback.value='';status.textContent=end?'Feedback sent; review ended':'Feedback queued';"
            "if(end){document.getElementById('send').disabled=true;document.getElementById('send-end').disabled=true;}};"
            "document.getElementById('send').addEventListener('click',()=>submit(false));document.getElementById('send-end').addEventListener('click',()=>submit(true));"
            "setInterval(async()=>{try{const response=await fetch(sessionEndpoint);if(!response.ok)return;const result=await response.json();"
            "const replies=result.review?.agent_replies||[];conversation.replaceChildren(...replies.map(reply=>{const item=document.createElement('li');"
            "item.textContent='Agent: '+reply;return item;}));"
            "if(result.review?.revision!==revision){if(retainedContext?.route&&result.review?.routes?.includes(retainedContext.route)){"
            "sessionStorage.setItem('viewspec-context-restore',JSON.stringify(retainedContext));}else{sessionStorage.setItem('viewspec-context-reset','1');}"
            "location.reload();}}catch{}},500);})();"
        )
        html = (
            "<!doctype html><html><head><meta charset=utf-8><title>ViewSpec Review</title>"
            f"<style>{style}</style></head><body>"
            f"<div class=toolbar><strong>ViewSpec Review</strong><span>Revision {self.runtime.built.revision.number}</span>"
            "<label for=viewport>Viewport</label><select id=viewport><option value=mobile>Mobile</option>"
            "<option value=tablet>Tablet</option><option value=desktop selected>Desktop</option></select>"
            "<button id=mode type=button>Annotate</button><span id=status class=notice aria-live=polite>Explore mode</span>"
            "<span>Queued: <strong id=queued>0</strong></span></div>"
            f"<main class=layout><section class=canvas><iframe id=artifact title='Checked ViewSpec artifact' "
            f"sandbox='allow-scripts allow-forms' src='{frame}'></iframe></section>"
            "<aside class=panel><h1>Review panel</h1><p>Select a compiler-owned element in Annotate mode. Cmd/Ctrl+I toggles modes.</p>"
            "<h2>Conversation</h2><ol id=conversation aria-live=polite></ol>"
            "<section id=composer hidden><h2>Annotation</h2><pre id=trace class=trace></pre>"
            "<button id=page-target type=button>Use page-level target</button>"
            "<label for=kind>Kind</label><select id=kind><option value=change_request>Change request</option>"
            "<option value=question>Question</option><option value=approval>Approval</option><option value=note>Note</option></select>"
            "<label for=feedback>Feedback</label><textarea id=feedback maxlength=8192></textarea>"
            "<button id=send type=button>Send feedback</button><button id=send-end type=button>Send &amp; End</button></section></aside></main>"
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
        status.update(
            {
                "frame_path": self.frame_path("index.html"),
                "frame_nonce": self.frame_nonce,
                "routes": sorted(self._route_screens),
                "agent_replies": list(self.runtime.session.agent_replies[-4:]),
            }
        )
        return status

    def _agent_status(self) -> dict[str, object]:
        return {
            **self.runtime.status(),
            "browser_ready": self.browser_ready,
            "response_error": self.last_response_error,
        }

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
            sdk = _frame_sdk(self.frame_nonce)
            marker = b"</body>"
            injection = b'<script id="viewspec-review-sdk">' + sdk + b"</script>"
            content = content.replace(marker, injection + marker, 1) if marker in content else content + injection
            hashes = [_csp_hash(sdk)]
            hashes.extend(_inline_hashes(content, b"script"))
            hashes.extend(_inline_hashes(content, b"style"))
            csp = (
                "default-src 'none'; script-src "
                + " ".join(sorted(set(hashes)))
                + "; style-src "
                + " ".join(sorted(set(_inline_hashes(content, b"style"))))
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

    def _complete_frame_handshake(self) -> None:
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


def _frame_sdk(nonce: str) -> bytes:
    return (
        "(()=>{const n='"
        + nonce
        + "';let annotate=false,cursor=-1;const parent=window.parent,ids=()=>Array.from(document.querySelectorAll('[id]'))"
        ".filter(e=>e.id&&e.id.length<=256);const viewport=()=>{const w=innerWidth,h=innerHeight;"
        "if(Math.abs(w-390)<=1&&Math.abs(h-844)<=1)return{name:'mobile',width:390,height:844};"
        "if(Math.abs(w-768)<=1&&Math.abs(h-1024)<=1)return{name:'tablet',width:768,height:1024};"
        "if(Math.abs(w-1440)<=1&&Math.abs(h-1000)<=1)return{name:'desktop',width:1440,height:1000};return null;};"
        "const choose=element=>{const measured=viewport();if(!measured){parent.postMessage({type:'viewspec-review-viewport-mismatch',nonce:n},'*');return;}"
        "const chain=[];let p=element;"
        "while(p&&p!==document.documentElement&&chain.length<32){if(p.id)chain.push(p.id);p=p.parentElement;}"
        "const screen=element.closest('[data-viewspec-app-screen]'),selection=getSelection(),text=element.textContent||'',quote=selection?selection.toString():'';"
        "let selected_text=null;if(quote&&selection&&element.contains(selection.anchorNode)&&element.contains(selection.focusNode)){const at=text.indexOf(quote);"
        "if(at>=0)selected_text={quote,prefix:text.slice(Math.max(0,at-512),at),suffix:text.slice(at+quote.length,at+quote.length+512)};}"
        "parent.postMessage({type:'viewspec-review-selected',nonce:n,dom_ancestors:chain,page_level:chain.length===0,"
        "screen_id:screen?screen.dataset.viewspecAppScreen:null,route:screen?(screen.dataset.routePath||location.pathname):null,viewport:measured,selected_text,"
        "visibility:element.getClientRects().length?'visible':'hidden'},'*');};"
        "const postContext=()=>{const screen=document.querySelector('[data-viewspec-app-screen]:not([hidden])')||document.querySelector('[data-viewspec-app-screen]');"
        "parent.postMessage({type:'viewspec-review-context',nonce:n,route:screen?(screen.dataset.routePath||location.pathname):null,"
        "scroll_x:Math.max(0,Math.min(1000000,Math.trunc(scrollX))),scroll_y:Math.max(0,Math.min(1000000,Math.trunc(scrollY)))},'*');};"
        "addEventListener('message',e=>{if(e.source!==parent||!e.data||e.data.nonce!==n)return;"
        "if(e.data.type==='viewspec-review-mode'){annotate=!!e.data.annotate;document.documentElement.dataset.viewspecReviewMode=annotate?'annotate':'explore';return;}"
        "if(e.data.type==='viewspec-review-restore'&&typeof e.data.route==='string'&&Number.isInteger(e.data.scroll_x)&&Number.isInteger(e.data.scroll_y)){"
        "history.replaceState({},'',e.data.route);dispatchEvent(new PopStateEvent('popstate'));requestAnimationFrame(()=>{scrollTo(e.data.scroll_x,e.data.scroll_y);postContext();});}});"
        "addEventListener('popstate',postContext);addEventListener('scroll',postContext,{passive:true});"
        "addEventListener('click',e=>{if(!annotate)return;e.preventDefault();e.stopImmediatePropagation();choose(e.target);},true);"
        "addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='i'){e.preventDefault();"
        "parent.postMessage({type:'viewspec-review-toggle',nonce:n},'*');return;}if(!annotate)return;const list=ids();"
        "if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();cursor=(cursor+(e.key==='ArrowDown'?1:-1)+list.length)%list.length;"
        "list[cursor]?.focus();}else if(e.key==='Enter'&&document.activeElement?.id){e.preventDefault();choose(document.activeElement);}});"
        "postContext();parent.postMessage({type:'viewspec-review-ready',nonce:n},'*');})();"
    ).encode("utf-8")


def _csp_hash(content: bytes) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(content).digest()).decode("ascii") + "'"


def _inline_hashes(content: bytes, tag: bytes) -> list[str]:
    expression = re.compile(rb"<" + tag + rb"\b[^>]*>([\s\S]*?)</" + tag + rb">", re.IGNORECASE)
    return [_csp_hash(match.group(1)) for match in expression.finditer(content)]


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
