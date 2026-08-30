"""Capability-scoped local Studio room for one task-bound first creation."""

from __future__ import annotations

from collections.abc import Callable
import base64
from dataclasses import dataclass
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import secrets
import stat
import threading
import time
from urllib.parse import urlsplit

from viewspec.review_contract import ReviewContractError, canonical_json_bytes
from viewspec.studio_creation import (
    STUDIO_CREATION_TASK_DEFAULT,
    StudioCreationError,
    accept_studio_creation,
    inspect_accepted_studio_creation,
    inspect_studio_creation,
)


CREATION_ROOM_SCHEMA_VERSION = 1
CREATION_ROOM_BOOTSTRAP_SECONDS = 60
CREATION_ROOM_COOKIE_SECONDS = 30 * 60
CREATION_ROOM_IDLE_SECONDS = 30 * 60
CREATION_ROOM_HANDOFF_GRACE_SECONDS = 5
CREATION_ROOM_QUIET_SECONDS = 0.250
CREATION_ROOM_CHECKED_MINIMUM_SECONDS = 0.350
CREATION_ROOM_COOKIE = "viewspec_creation"
CREATION_ROOM_MAX_URI_BYTES = 2 * 1024
CREATION_ROOM_MAX_RESPONSE_BYTES = 64 * 1024


def creation_room_configuration_sha256(
    *,
    task_id: str,
    cwd: str | Path,
    design: str | Path | None,
    target: str | None,
    port: int,
    state_root: str | Path,
    convergence_state_root: str | Path | None,
    verify: bool,
    install: bool,
) -> str:
    """Bind active-room reuse to the exact eventual Studio configuration."""

    root = Path(os.path.abspath(Path(cwd).expanduser()))

    def absolute(value: str | Path | None) -> str | None:
        if value is None:
            return None
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = root / path
        return str(Path(os.path.abspath(path)))

    identity = {
        "schema_version": CREATION_ROOM_SCHEMA_VERSION,
        "task_id": task_id,
        "cwd": str(root),
        "design": absolute(design),
        "target": target,
        "port": port,
        "state_root": absolute(state_root),
        "convergence_state_root": absolute(convergence_state_root),
        "verify": verify,
        "install": install,
    }
    return hashlib.sha256(b"viewspec.studio.creation-room-configuration.v1\x00" + canonical_json_bytes(identity)).hexdigest()


@dataclass(frozen=True, slots=True)
class StudioCreationHandoff:
    """Normal Studio readiness returned after a checked first creation."""

    url: str
    source_sha256: str
    review: dict[str, object]


class StudioCreationRoomController:
    """Deterministic Waiting → Checking → Checked coordinator."""

    def __init__(
        self,
        task_path: str | Path = STUDIO_CREATION_TASK_DEFAULT,
        *,
        cwd: str | Path | None = None,
        handoff: Callable[[Path, dict[str, object]], StudioCreationHandoff],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        inspected = inspect_studio_creation(task_path, cwd=cwd)
        self.root = inspected["root"]
        self.task_path = inspected["task_path"]
        self.candidate_path = inspected["candidate_path"]
        self.source_path = inspected["source_path"]
        self.proof_path = inspected["proof_path"]
        self.task = inspected["task"]
        assert isinstance(self.root, Path)
        assert isinstance(self.task_path, Path)
        assert isinstance(self.candidate_path, Path)
        assert isinstance(self.source_path, Path)
        assert isinstance(self.proof_path, Path)
        assert isinstance(self.task, dict)
        self._handoff = handoff
        self._clock = clock
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._started_at = clock()
        self._last_activity = self._started_at
        self._stage = "waiting"
        self._detail = "Studio preserved the exact local brief. Your agent can author its task-bound candidate."
        self._error: dict[str, str] | None = None
        self._candidate_validation = "pending"
        self._artifact_check = "pending"
        self._history = ["waiting"]
        self._candidate_signature: tuple[int, int, int] | None = None
        self._candidate_pending_since: float | None = None
        self._checked_not_before: float | None = None
        self._accepted: dict[str, object] | None = None
        self._handoff_result: StudioCreationHandoff | None = None
        self._handoff_consumed_at: float | None = None

    def status(self) -> dict[str, object]:
        with self._lock:
            reference = self.task.get("reference")
            reference_projection: dict[str, object] | None = None
            if isinstance(reference, dict):
                reference_projection = {
                    "name": Path(str(reference["path"])).name,
                    "media_type": reference["media_type"],
                    "bytes": reference["bytes"],
                    "width": reference["width"],
                    "height": reference["height"],
                }
            projection: dict[str, object] = {
                "schema_version": CREATION_ROOM_SCHEMA_VERSION,
                "ok": True,
                "stage": self._stage,
                "headline": _headline(self._stage),
                "detail": self._detail,
                "brief": self.task["brief"],
                "source_kind": "product" if self.task["source_kind"] == "app_bundle" else "view",
                "reference": reference_projection,
                "checks": {
                    "candidate_validation": self._candidate_validation,
                    "artifact_check": self._artifact_check,
                },
                "stage_history": list(self._history),
                "handoff_ready": self._handoff_result is not None,
                "elapsed_ms": max(0, round((self._clock() - self._started_at) * 1000)),
                "network_calls": "none",
            }
            if self._error is not None:
                projection["error"] = self._browser_error(self._error)
            return projection

    def touch(self) -> None:
        with self._lock:
            self._last_activity = self._clock()

    def poll_once(self) -> None:
        """Advance from filesystem evidence at most once; safe to call repeatedly."""

        with self._lock:
            if self._handoff_result is not None:
                return
            if self._accepted is not None:
                not_before = self._checked_not_before
                if not_before is None or self._clock() >= not_before:
                    pass
                else:
                    return
        if self._accepted is not None:
            self._start_handoff()
            return
        if self.source_path.exists() or self.source_path.is_symlink():
            self._finish_existing_acceptance()
            return
        signature = _regular_signature(self.candidate_path)
        now = self._clock()
        with self._lock:
            if signature is None:
                if self._candidate_signature is not None:
                    self._candidate_signature = None
                    self._candidate_pending_since = None
                    self._accepted = None
                    self._candidate_validation = "pending"
                    self._artifact_check = "pending"
                    self._set_stage(
                        "waiting",
                        "The candidate was removed. Studio is still holding the exact local brief for your agent.",
                    )
                return
            if signature != self._candidate_signature:
                self._candidate_signature = signature
                self._candidate_pending_since = now
                self._accepted = None
                self._candidate_validation = "pending"
                self._artifact_check = "pending"
                self._set_stage(
                    "waiting",
                    "Candidate received. Studio is waiting for the local file to settle before checking it.",
                )
                return
            pending_since = self._candidate_pending_since
            if self._accepted is None and pending_since is not None and now - pending_since < CREATION_ROOM_QUIET_SECONDS:
                return
            if self._accepted is None:
                self._set_stage(
                    "checking",
                    "Validating semantic source and checking the generated artifact before anything becomes canonical.",
                )
        if self._accepted is None:
            try:
                accepted = accept_studio_creation(self.task_path, cwd=self.root)
            except StudioCreationError as exc:
                with self._lock:
                    self._candidate_pending_since = None
                    self._candidate_validation = "failed"
                    self._artifact_check = "not_run"
                    self._error = exc.to_json()
                    self._set_stage("needs_fix", "The candidate did not pass. Canonical semantic source was not published.")
                return
            with self._lock:
                self._accepted = accepted
                creation = accepted.get("creation")
                if isinstance(creation, dict):
                    self._candidate_validation = str(creation.get("candidate_validation", "passed"))
                    self._artifact_check = str(creation.get("artifact_check", "passed"))
                else:
                    self._candidate_validation = "passed"
                    self._artifact_check = "passed"
                self._checked_not_before = self._clock() + CREATION_ROOM_CHECKED_MINIMUM_SECONDS
        with self._lock:
            not_before = self._checked_not_before
            if not_before is not None and self._clock() < not_before:
                return
        self._start_handoff()

    def consume_handoff(self) -> str | None:
        with self._lock:
            if self._handoff_result is None or self._handoff_consumed_at is not None:
                return None
            self._handoff_consumed_at = self._clock()
            return self._handoff_result.url

    @property
    def should_exit(self) -> bool:
        with self._lock:
            now = self._clock()
            if self._handoff_consumed_at is not None:
                return now - self._handoff_consumed_at >= CREATION_ROOM_HANDOFF_GRACE_SECONDS
            return now - self._last_activity >= CREATION_ROOM_IDLE_SECONDS

    @property
    def candidate_to_checked_ms(self) -> int | None:
        with self._lock:
            if self._candidate_pending_since is None:
                return None
            return max(0, round((self._clock() - self._candidate_pending_since) * 1000))

    def wait(self, timeout: float) -> None:
        with self._condition:
            self._condition.wait(timeout)

    def _finish_existing_acceptance(self) -> None:
        try:
            inspected = inspect_accepted_studio_creation(self.task_path, cwd=self.root)
        except StudioCreationError as exc:
            with self._lock:
                self._candidate_validation = "failed"
                self._artifact_check = "failed"
                self._error = exc.to_json()
                self._set_stage(
                    "needs_fix",
                    "A canonical source exists, but it is not the exact retained checked candidate. Studio refused to open it.",
                )
            return
        with self._lock:
            self._accepted = {
                "creation": {
                    "status": "source_ready",
                    "source_sha256": inspected["source_sha256"],
                    "candidate_validation": "passed",
                    "artifact_check": "passed",
                }
            }
            self._candidate_validation = "passed"
            self._artifact_check = "passed"
            self._checked_not_before = self._clock()
        self._start_handoff()

    def _start_handoff(self) -> None:
        with self._lock:
            if self._handoff_result is not None or self._accepted is None:
                return
            accepted = self._accepted
            self._set_stage("checking", "Candidate passed. Opening the normal checked Studio product in this tab.")
        try:
            result = self._handoff(self.source_path, accepted)
            _validate_handoff(result)
        except (ReviewContractError, StudioCreationError) as exc:
            error = exc.to_json()
            with self._lock:
                self._error = {key: str(value) for key, value in error.items() if key in {"code", "message", "fix"}}
                self._set_stage("open_failed", "The semantic source is checked, but the Studio product could not open yet.")
            return
        with self._lock:
            self._handoff_result = result
            self._error = None
            self._set_stage("checked", "Candidate validation and artifact check passed. Continuing to the product now.")

    def _set_stage(self, stage: str, detail: str) -> None:
        changed = stage != self._stage
        self._stage = stage
        self._detail = detail
        if stage not in {"needs_fix", "open_failed"}:
            self._error = None
        if changed:
            self._history.append(stage)
        self._condition.notify_all()

    def _browser_error(self, error: dict[str, str]) -> dict[str, str]:
        replacements = {
            str(self.root): "this workspace",
            str(self.task_path): "the creation task",
            str(self.candidate_path): "the task-bound candidate",
            str(self.source_path): "canonical semantic source",
            str(self.proof_path): "the retained local proof",
            str(self.task["candidate_path"]): "the task-bound candidate",
            str(self.task["source_path"]): "canonical semantic source",
            str(self.task["proof_path"]): "the retained local proof",
        }
        reference = self.task.get("reference")
        if isinstance(reference, dict):
            replacements[str(reference["path"])] = "the local reference"

        def redact(value: str) -> str:
            result = value
            for path, label in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
                result = result.replace(path, label)
            return result[:2048]

        code = redact(str(error.get("code", "STUDIO_CREATION_ROOM_FAILED")))
        human_copy = {
            "STUDIO_CREATION_CANDIDATE_INVALID": (
                "Studio could not check this candidate as the required product source.",
                "Ask the agent to update the task-bound candidate and save it again. Studio will retry automatically.",
            ),
            "STUDIO_CREATION_PROOF_FAILED": (
                "The candidate did not produce a healthy checked interface.",
                "Ask the agent to fix the task-bound candidate and save it again. Studio will retry automatically.",
            ),
            "STUDIO_CREATION_STARTER_FORBIDDEN": (
                "The candidate still contains ViewSpec starter content instead of the requested product.",
                "Ask the agent to replace the sample content with the brief, then save the candidate again.",
            ),
            "STUDIO_CREATION_REFERENCE_CHANGED": (
                "The local reference changed after this creation room was prepared.",
                "Restore the exact reference or start a fresh room from the new reference.",
            ),
            "STUDIO_CREATION_TASK_INVALID": (
                "The local creation task changed after Studio prepared it.",
                "Restore the exact task or start a fresh room from the brief.",
            ),
        }
        message, fix = human_copy.get(
            code,
            (
                redact(str(error.get("message", "The local candidate did not pass."))),
                redact(str(error.get("fix", "Update the task-bound candidate and save it again."))),
            ),
        )
        return {"code": code, "message": message, "fix": fix}


class StudioCreationRoomServer:
    """One bounded browser server with a one-time bootstrap and no source-serving route."""

    def __init__(
        self,
        controller: StudioCreationRoomController,
        *,
        host: str = "127.0.0.1",
        port: int = 4388,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if host != "127.0.0.1":
            raise ReviewContractError(
                "STUDIO_CREATION_NON_LOOPBACK_FORBIDDEN",
                "Studio creation binds only to the literal IPv4 loopback address.",
                "Use the default local Studio address.",
                cli_exit=2,
            )
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ReviewContractError(
                "REVIEW_PORT_UNAVAILABLE",
                "Studio creation port must be an integer from 1024 through 65535.",
                "Use the default port 4388 or one explicit unprivileged local port.",
                cli_exit=2,
            )
        self.controller = controller
        self.host = host
        self._clock = clock
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self.agent_token = _token()
        self._agent_digest = _digest(self.agent_token)
        self._cookie_token = _token()
        self._cookie_digest = _digest(self._cookie_token)
        self._bootstrap_token = ""
        self._bootstrap_digest = b""
        self._bootstrap_expires = 0.0
        self._bootstrap_consumed = True
        self.reset_bootstrap()
        try:
            self._httpd = ThreadingHTTPServer((host, port), self._handler_type())
        except OSError as exc:
            raise ReviewContractError(
                "REVIEW_PORT_UNAVAILABLE",
                f"Could not bind the local Studio creation port: {exc}",
                "Choose one available unprivileged local port.",
                cli_exit=2,
            ) from exc
        self._httpd.daemon_threads = True
        self.port = int(self._httpd.server_address[1])
        self.origin = f"http://127.0.0.1:{self.port}"

    @property
    def bootstrap_url(self) -> str:
        return f"{self.origin}/open/{self._bootstrap_token}"

    def reset_bootstrap(self) -> str:
        with self._lock:
            self._bootstrap_token = _token()
            self._bootstrap_digest = _digest(self._bootstrap_token)
            self._bootstrap_expires = self._clock() + CREATION_ROOM_BOOTSTRAP_SECONDS
            self._bootstrap_consumed = False
            if hasattr(self, "origin"):
                return self.bootstrap_url
            return ""

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="viewspec-studio-creation", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            self._httpd.server_close()
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)
        self._thread = None

    def _handler_type(self) -> type[BaseHTTPRequestHandler]:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802
                outer._dispatch(self)

            def do_POST(self) -> None:  # noqa: N802
                outer._dispatch(self)

            def do_HEAD(self) -> None:  # noqa: N802
                outer._dispatch(self)

            def do_OPTIONS(self) -> None:  # noqa: N802
                outer._dispatch(self)

            def do_PUT(self) -> None:  # noqa: N802
                outer._dispatch(self)

            def do_DELETE(self) -> None:  # noqa: N802
                outer._dispatch(self)

            def log_message(self, format: str, *args: object) -> None:
                del format, args

        return Handler

    def _dispatch(self, handler: BaseHTTPRequestHandler) -> None:
        try:
            response = self._handle(handler)
        except Exception:
            response = _json_response(
                500,
                {
                    "schema_version": 1,
                    "ok": False,
                    "error": {
                        "code": "STUDIO_CREATION_ROOM_FAILED",
                        "message": "The local creation room could not handle this request safely.",
                        "fix": "Reload the exact local Studio creation URL.",
                    },
                },
            )
        _send(handler, response)

    def _handle(self, handler: BaseHTTPRequestHandler) -> "_RoomResponse":
        if handler.headers.get("Host") != f"127.0.0.1:{self.port}" or not _headers_are_bounded(handler):
            handler.close_connection = True
            return _not_found()
        if len(handler.path.encode("utf-8", errors="replace")) > CREATION_ROOM_MAX_URI_BYTES:
            return _not_found()
        parsed = urlsplit(handler.path)
        if parsed.query or parsed.fragment or parsed.scheme or parsed.netloc:
            return _not_found()
        path = parsed.path
        method = handler.command
        if path.startswith("/internal/v1/"):
            if not self._agent_authorized(handler):
                return _not_found()
            if method == "GET" and path == "/internal/v1/status":
                return _json_response(200, self.controller.status())
            if method == "POST" and path == "/internal/v1/bootstrap":
                if handler.headers.get("Content-Length") not in {None, "0"}:
                    return _not_found()
                return _json_response(200, {"schema_version": 1, "ok": True, "bootstrap_url": self.reset_bootstrap()})
            return _not_found()
        if method != "GET":
            handler.close_connection = True
            return _not_found()
        if path.startswith("/open/"):
            token = path.removeprefix("/open/")
            with self._lock:
                valid = (
                    not self._bootstrap_consumed
                    and self._clock() <= self._bootstrap_expires
                    and hmac.compare_digest(_digest(token), self._bootstrap_digest)
                )
                if valid:
                    self._bootstrap_consumed = True
            if not valid:
                return _not_found()
            self.controller.touch()
            return _redirect(
                "/",
                cookie=(
                    f"{CREATION_ROOM_COOKIE}={self._cookie_token}; Path=/; Max-Age={CREATION_ROOM_COOKIE_SECONDS}; "
                    "HttpOnly; SameSite=Strict"
                ),
            )
        if not self._browser_authorized(handler):
            return _not_found()
        self.controller.touch()
        if path == "/":
            return _html_response(_ROOM_HTML)
        if path == "/v1/status":
            return _json_response(200, self.controller.status())
        if path == "/continue":
            handoff = self.controller.consume_handoff()
            return _redirect(handoff) if handoff is not None else _not_found()
        return _not_found()

    def _browser_authorized(self, handler: BaseHTTPRequestHandler) -> bool:
        raw = handler.headers.get("Cookie", "")
        matches = []
        for part in raw.split(";"):
            name, separator, value = part.strip().partition("=")
            if separator and name == CREATION_ROOM_COOKIE:
                matches.append(value)
        return len(matches) == 1 and hmac.compare_digest(_digest(matches[0]), self._cookie_digest)

    def _agent_authorized(self, handler: BaseHTTPRequestHandler) -> bool:
        value = handler.headers.get("X-ViewSpec-Agent-Capability", "")
        return hmac.compare_digest(_digest(value), self._agent_digest)


@dataclass(frozen=True, slots=True)
class _RoomResponse:
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes


def _headline(stage: str) -> str:
    return {
        "waiting": "Waiting for agent",
        "checking": "Checking candidate",
        "needs_fix": "Candidate needs one fix",
        "checked": "Checked product ready",
        "open_failed": "Checked product could not open",
    }.get(stage, "Preparing Studio")


def _regular_signature(path: Path) -> tuple[int, int, int] | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        return None
    if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        return (info.st_ino, info.st_size, info.st_mtime_ns)
    return (info.st_ino, info.st_size, info.st_mtime_ns)


def _headers_are_bounded(handler: BaseHTTPRequestHandler) -> bool:
    items = list(handler.headers.items())
    if len(items) > 64:
        return False
    total = 0
    for name, value in items:
        if len(name) > 256 or len(value) > 8192 or "\x00" in name or "\x00" in value:
            return False
        total += len(name) + len(value)
    return total <= 16 * 1024


def _validate_handoff(value: StudioCreationHandoff) -> None:
    if not isinstance(value, StudioCreationHandoff):
        raise StudioCreationError(
            "STUDIO_CREATION_HANDOFF_INVALID",
            "The checked Studio handoff is incomplete.",
            "Restart the exact local Studio creation task.",
            cli_exit=1,
        )
    parsed = urlsplit(value.url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise StudioCreationError(
            "STUDIO_CREATION_HANDOFF_INVALID",
            "The checked Studio handoff has an invalid local port.",
            "Restart the exact local Studio creation task.",
            cli_exit=1,
        ) from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or port is None
        or not 1024 <= port <= 65535
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.path.startswith("/open/")
        or parsed.query
        or parsed.fragment
        or len(value.source_sha256) != 64
        or any(character not in "0123456789abcdef" for character in value.source_sha256)
        or not isinstance(value.review, dict)
    ):
        raise StudioCreationError(
            "STUDIO_CREATION_HANDOFF_INVALID",
            "The checked Studio handoff is not one exact local Review bootstrap.",
            "Restart the exact local Studio creation task.",
            cli_exit=1,
        )


def _token() -> str:
    return secrets.token_urlsafe(32)


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("ascii", errors="ignore")).digest()


def _send(handler: BaseHTTPRequestHandler, response: _RoomResponse) -> None:
    handler.send_response(response.status)
    for name, value in response.headers:
        handler.send_header(name, value)
    handler.end_headers()
    if response.body:
        handler.wfile.write(response.body)


def _security_headers(*, content_type: str) -> tuple[tuple[str, str], ...]:
    return (
        ("Content-Type", content_type),
        ("Cache-Control", "no-store"),
        ("Referrer-Policy", "no-referrer"),
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
        ("Cross-Origin-Opener-Policy", "same-origin"),
        ("Cross-Origin-Resource-Policy", "same-origin"),
        ("Content-Security-Policy", _ROOM_CSP),
    )


def _json_response(status: int, payload: dict[str, object]) -> _RoomResponse:
    body = canonical_json_bytes(payload)
    if len(body) > CREATION_ROOM_MAX_RESPONSE_BYTES:
        return _not_found()
    return _RoomResponse(
        status,
        _security_headers(content_type="application/json") + (("Content-Length", str(len(body))),),
        body,
    )


def _html_response(value: str) -> _RoomResponse:
    body = value.encode("utf-8")
    return _RoomResponse(
        200,
        _security_headers(content_type="text/html; charset=utf-8") + (("Content-Length", str(len(body))),),
        body,
    )


def _redirect(location: str, *, cookie: str | None = None) -> _RoomResponse:
    headers = _security_headers(content_type="text/plain; charset=utf-8") + (
        ("Location", location),
        ("Content-Length", "0"),
    )
    if cookie is not None:
        headers += (("Set-Cookie", cookie),)
    return _RoomResponse(303, headers, b"")


def _not_found() -> _RoomResponse:
    body = b"Studio creation access unavailable."
    return _RoomResponse(
        404,
        _security_headers(content_type="text/plain; charset=utf-8") + (("Content-Length", str(len(body))),),
        body,
    )


_ROOM_STYLE = """
:root{color-scheme:dark;--ink:#080a0d;--panel:#10141a;--panel2:#151b23;--line:#29313d;--text:#f7f8fa;--muted:#98a3b3;--amber:#ffbd4a;--mint:#5fe0ad;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:var(--ink);color:var(--text)}*{box-sizing:border-box}body{margin:0;min-height:100vh;background:radial-gradient(circle at 50% -20%,#242730 0,#111318 34%,var(--ink) 68%);color:var(--text)}.toolbar{min-height:72px;padding:12px 18px;border-bottom:1px solid var(--line);display:flex;align-items:center;background:rgba(8,10,13,.96)}.brand{display:flex;align-items:center;gap:10px}.mark{display:grid;place-items:center;width:34px;height:34px;border-radius:10px;background:var(--amber);color:#211400;font-weight:900}.brand strong{display:block;font-size:14px;letter-spacing:-.01em}.brand small{display:block;color:var(--muted);font-size:10px;letter-spacing:.08em;text-transform:uppercase}.shell{min-height:calc(100vh - 72px);display:grid;place-items:center;padding:48px 24px}.room{width:min(760px,100%);border:1px solid var(--line);border-radius:20px;background:rgba(16,20,26,.96);box-shadow:0 28px 80px rgba(0,0,0,.34);overflow:hidden}.room-main{padding:42px 42px 34px}.eyebrow{display:block;margin-bottom:12px;color:var(--amber);font-size:11px;font-weight:700;letter-spacing:.13em;text-transform:uppercase}h1{margin:0;font-size:clamp(30px,5vw,48px);line-height:1.02;letter-spacing:-.045em}#detail{max-width:620px;margin:18px 0 0;color:#b9bcc4;font-size:15px;line-height:1.6}.steps{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:30px 0}.step{height:4px;border-radius:99px;background:#30333b}.step.active{background:var(--amber)}.step.done{background:#777d89}.brief{margin:0;padding:22px;border:1px solid var(--line);border-radius:14px;background:#0d0f13}.brief-label{display:block;margin-bottom:9px;color:var(--muted);font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase}#brief{margin:0;white-space:pre-wrap;font-family:inherit;font-size:15px;line-height:1.55;color:#edede9}.reference{display:flex;align-items:center;gap:8px;margin-top:12px;color:#aeb1ba;font-size:12px}.reference[hidden]{display:none}.reference strong{color:#e4e4df}.error{margin-top:16px;padding:16px;border:1px solid #5d3738;border-radius:12px;background:#251617}.error[hidden]{display:none}.error strong{display:block;color:#f0c0bc;font-size:13px}.error p{margin:7px 0 0;color:#d7b5b2;font-size:13px;line-height:1.45}.error details{margin-top:11px;color:#aa8582;font-size:11px}.error summary{cursor:pointer}.error code{display:block;margin-top:6px;overflow-wrap:anywhere}.room-foot{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:17px 42px;border-top:1px solid var(--line);background:#0d1117;color:var(--muted);font-size:11px}.pulse{width:7px;height:7px;border-radius:50%;background:#8d929d;box-shadow:0 0 0 5px rgba(141,146,157,.1)}.state{display:flex;align-items:center;gap:9px}.room[data-stage=checking] .pulse{background:var(--amber);animation:pulse 1.2s ease-in-out infinite}.room[data-stage=checked] .pulse{background:var(--mint)}.room[data-stage=needs_fix] .pulse,.room[data-stage=open_failed] .pulse{background:#d47770}@keyframes pulse{50%{opacity:.35;transform:scale(.78)}}@media(max-width:640px){.toolbar{padding:12px 16px}.shell{padding:20px 12px}.room-main{padding:30px 22px 26px}.room-foot{padding:15px 22px;align-items:flex-start;flex-direction:column}.steps{margin:24px 0}}
""".strip()

_ROOM_SCRIPT = """
(()=>{const room=document.querySelector('.room'),headline=document.getElementById('headline'),detail=document.getElementById('detail'),brief=document.getElementById('brief'),reference=document.getElementById('reference'),referenceName=document.getElementById('reference-name'),referenceMeta=document.getElementById('reference-meta'),error=document.getElementById('error'),errorCode=document.getElementById('error-code'),errorMessage=document.getElementById('error-message'),errorFix=document.getElementById('error-fix'),stateLabel=document.getElementById('state-label'),steps=Array.from(document.querySelectorAll('.step'));let continuing=false;const render=value=>{room.dataset.stage=value.stage;headline.textContent=value.headline;detail.textContent=value.detail;brief.textContent=value.brief;stateLabel.textContent=value.headline;const order={waiting:0,checking:1,needs_fix:1,open_failed:2,checked:2},active=order[value.stage]??0;steps.forEach((step,index)=>{step.classList.toggle('active',index===active);step.classList.toggle('done',index<active)});if(value.reference){reference.hidden=false;referenceName.textContent=value.reference.name;referenceMeta.textContent=`${value.reference.width}×${value.reference.height} · ${Math.max(1,Math.round(value.reference.bytes/1024))} KB · stays local`}else reference.hidden=true;if(value.error){error.hidden=false;errorCode.textContent=value.error.code;errorMessage.textContent=value.error.message;errorFix.textContent=value.error.fix}else error.hidden=true;if(value.handoff_ready&&!continuing){continuing=true;setTimeout(()=>location.replace('/continue'),250)}};const refresh=async()=>{try{const response=await fetch('/v1/status',{cache:'no-store'});if(response.ok)render(await response.json())}catch{}if(!continuing)setTimeout(refresh,250)};refresh()})();
""".strip()


def _csp_hash(value: str) -> str:
    digest = base64.b64encode(hashlib.sha256(value.encode("utf-8")).digest()).decode("ascii")
    return f"'sha256-{digest}'"


_ROOM_CSP = (
    "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; "
    f"style-src {_csp_hash(_ROOM_STYLE)}; script-src {_csp_hash(_ROOM_SCRIPT)}; "
    "connect-src 'self'; img-src 'none'; font-src 'none'; object-src 'none'"
)

_ROOM_HTML = (
    "<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
    "<title>ViewSpec Studio · First creation</title><style>"
    + _ROOM_STYLE
    + "</style></head><body><header class=toolbar><div class=brand><span class=mark>V</span><div><strong>ViewSpec Studio</strong>"
    "<small>First creation · local</small></div></div></header><main class=shell><section class=room data-stage=waiting aria-labelledby=headline>"
    "<div class=room-main><span class=eyebrow>First creation</span><h1 id=headline>Waiting for agent</h1>"
    "<p id=detail>Studio preserved the exact local brief. Your agent can author its task-bound candidate.</p>"
    "<div class=steps aria-hidden=true><span class='step active'></span><span class=step></span><span class=step></span></div>"
    "<div class=brief><span class=brief-label>Your brief</span><pre id=brief></pre></div>"
    "<div id=reference class=reference hidden><strong id=reference-name></strong><span id=reference-meta></span></div>"
    "<div id=error class=error hidden role=alert><strong>Candidate check did not pass</strong><p id=error-message></p><p id=error-fix></p>"
    "<details><summary>Exact error</summary><code id=error-code></code></details></div></div>"
    "<footer class=room-foot><span class=state><i class=pulse></i><span id=state-label>Waiting for agent</span></span>"
    "<span>Brief and reference stay in this workspace · nothing uploaded</span></footer></section></main><script>"
    + _ROOM_SCRIPT
    + "</script></body></html>"
)


__all__ = [
    "CREATION_ROOM_SCHEMA_VERSION",
    "StudioCreationHandoff",
    "StudioCreationRoomController",
    "StudioCreationRoomServer",
    "creation_room_configuration_sha256",
]
