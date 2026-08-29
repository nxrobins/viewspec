"""Transport adapter and calm browser surface for private Studio review.

The adapter is deliberately server-framework neutral: a deployment maps one HTTPS
request into :class:`ReviewHTTPRequest` and writes the returned response.  It never
logs requests, accepts capabilities in paths, or serves semantic source files.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import unquote_to_bytes, urlsplit

from viewspec.review_contract import canonical_json_bytes
from viewspec.studio_review_service import StudioReviewService, StudioReviewServiceError
from viewspec.studio_share import STUDIO_SHARE_ARCHIVE_MAX_BYTES


STUDIO_REVIEW_MEDIA_TYPE = "application/vnd.viewspec.review+zip"
STUDIO_REVIEW_COOKIE_NAME = "__Secure-viewspec_review"
STUDIO_REVIEW_HTTP_MAX_JSON_BYTES = 16 * 1024
STUDIO_REVIEW_HTTP_MAX_PATH_BYTES = 2 * 1024
STUDIO_REVIEW_HTTP_MAX_HEADER_BYTES = 16 * 1024

_SESSION_PATH_RE = re.compile(r"^/review/(vsr_[A-Za-z0-9_-]{24})(?:/(.*))?$")
_HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_HEADER_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

UploadAuthorizer = Callable[[Mapping[str, str]], bool]


@dataclass(frozen=True, slots=True)
class ReviewHTTPRequest:
    method: str
    path: str
    headers: Mapping[str, str]
    body: bytes = b""
    scheme: str = "https"


@dataclass(frozen=True, slots=True)
class ReviewHTTPResponse:
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes

    def json(self) -> dict[str, object]:
        value = json.loads(self.body)
        if not isinstance(value, dict):
            raise ValueError("response body is not a JSON object")
        return value


class StudioReviewHTTPAdapter:
    """Strict HTTPS request adapter for one :class:`StudioReviewService`."""

    def __init__(
        self,
        service: StudioReviewService,
        *,
        public_origin: str,
        authorize_upload: UploadAuthorizer,
    ) -> None:
        parsed = urlsplit(public_origin)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("Studio review public_origin must be one canonical HTTPS origin.")
        if not isinstance(service, StudioReviewService) or not callable(authorize_upload):
            raise TypeError("Studio review HTTP adapter requires a service and upload authorizer.")
        self.service = service
        self.public_origin = f"https://{parsed.netloc}"
        self._authorize_upload = authorize_upload

    def handle(self, request: ReviewHTTPRequest) -> ReviewHTTPResponse:
        """Handle one already-bounded deployment request without emitting logs."""

        try:
            method, path, headers = _request_head(request)
            if request.scheme != "https":
                raise StudioReviewServiceError(
                    "STUDIO_REVIEW_HTTPS_REQUIRED",
                    "Private review transport requires HTTPS.",
                    "Terminate TLS before invoking the private review adapter.",
                    http_status=400,
                )
            if method == "POST" and path == "/v1/reviews":
                return self._create(headers, request.body)
            matched = _SESSION_PATH_RE.fullmatch(path)
            if matched is None:
                return _not_found()
            session_id, tail = matched.group(1), matched.group(2) or ""
            if method == "GET" and tail == "":
                if not path.endswith("/"):
                    return _redirect(f"/review/{session_id}/")
                return _asset_response("text/html; charset=utf-8", _REVIEW_HTML.encode())
            if method == "GET" and tail == "client.js":
                return _asset_response("text/javascript; charset=utf-8", _REVIEW_CLIENT.encode())
            if method == "GET" and tail == "client.css":
                return _asset_response("text/css; charset=utf-8", _REVIEW_CSS.encode())
            if method == "POST" and tail == "exchange":
                return self._exchange(session_id, headers, request.body)
            if method == "GET" and tail == "revision":
                return self._revision(headers)
            if method == "GET" and tail.startswith("artifact/"):
                return self._artifact(session_id, headers, tail.removeprefix("artifact/"))
            if method == "POST" and tail == "comments":
                return self._comment(headers, request.body)
            if method == "POST" and tail == "approval":
                return self._approval(headers, request.body)
            if method == "POST" and tail == "lifecycle":
                return self._lifecycle(session_id, headers, request.body)
            return _not_found()
        except StudioReviewServiceError as exc:
            return _json_response(exc.http_status, {"error": exc.to_json()})
        except Exception:
            error = StudioReviewServiceError(
                "STUDIO_REVIEW_HTTP_INVALID",
                "The private review request could not be handled safely.",
                "Retry a bounded request against the documented HTTPS contract.",
                http_status=500,
            )
            return _json_response(500, {"error": error.to_json()})

    def _create(self, headers: dict[str, str], body: bytes) -> ReviewHTTPResponse:
        if not self._authorize_upload(headers):
            raise StudioReviewServiceError(
                "STUDIO_REVIEW_UPLOAD_UNAUTHORIZED",
                "Private review upload authorization is unavailable.",
                "Authenticate with the configured deployment identity and retry.",
                http_status=401,
            )
        if headers.get("content-type") != STUDIO_REVIEW_MEDIA_TYPE or not 1 <= len(body) <= STUDIO_SHARE_ARCHIVE_MAX_BYTES:
            raise _http_invalid("Create requires one bounded deterministic .vsreview request body.")
        if headers.get("x-viewspec-disclosure-accepted") != "true":
            raise StudioReviewServiceError(
                "STUDIO_REVIEW_DISCLOSURE_REQUIRED",
                "Private review creation requires explicit disclosure acceptance.",
                "Show the prepared disclosure and send its deliberate acceptance.",
            )
        try:
            expires_in = int(headers.get("x-viewspec-expiry-seconds", ""))
        except ValueError as exc:
            raise _http_invalid("Create requires an integer expiry header.") from exc
        key = _required_header(headers, "idempotency-key")
        descriptor, temporary_name = tempfile.mkstemp(prefix=".upload-", suffix=".vsreview", dir=self.service.root)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.chmod(0o600)
            created = self.service.create_session_from_archive(
                temporary,
                disclosure_accepted=True,
                expires_in_seconds=expires_in,
                idempotency_key=key,
            )
        finally:
            if temporary.exists():
                temporary.unlink()
        capabilities = created.pop("fragment_capabilities")
        assert isinstance(capabilities, dict)
        session = created["session"]
        assert isinstance(session, dict)
        session_path = f"/review/{session['id']}/"
        payload = {
            **created,
            "ingress": {
                "archive_sha256": hashlib.sha256(body).hexdigest(),
                "archive_bytes": len(body),
            },
            "links": {
                "owner": f"{self.public_origin}{session_path}{capabilities['owner']}",
                "reviewer": f"{self.public_origin}{session_path}{capabilities['reviewer']}",
                "transport": capabilities["transport"],
            },
        }
        return _json_response(201, payload)

    def _exchange(self, session_id: str, headers: dict[str, str], body: bytes) -> ReviewHTTPResponse:
        _require_origin(headers, self.public_origin)
        payload = _json_body(headers, body, fields={"capability"})
        exchanged = self.service.exchange_capability(session_id, payload["capability"])
        cookie_value = exchanged.pop("cookie_value")
        policy = exchanged["cookie_policy"]
        assert isinstance(policy, dict)
        max_age = int(policy["max_age"])
        cookie = (
            f"{STUDIO_REVIEW_COOKIE_NAME}={cookie_value}; Path={policy['path']}; Max-Age={max_age}; "
            "Secure; HttpOnly; SameSite=Strict"
        )
        return _json_response(200, exchanged, extra_headers=(("Set-Cookie", cookie),))

    def _revision(self, headers: dict[str, str]) -> ReviewHTTPResponse:
        return _json_response(200, self.service.read_revision(_browser_cookie(headers)))

    def _artifact(self, session_id: str, headers: dict[str, str], encoded_path: str) -> ReviewHTTPResponse:
        if "%2f" in encoded_path.lower() or "%5c" in encoded_path.lower():
            raise _http_invalid("Encoded path separators are forbidden.")
        try:
            decoded = unquote_to_bytes(encoded_path).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _http_invalid("Artifact path is not strict UTF-8.") from exc
        artifact = self.service.read_artifact(_browser_cookie(headers), f"artifacts/{decoded}")
        content = artifact.content
        response_sha256 = artifact.sha256
        extra_headers: tuple[tuple[str, str], ...] = ()
        if artifact.media_type.startswith("text/html"):
            surface = "react-tailwind-app" if decoded.startswith("react/") else "html-tailwind-app"
            base_directory = decoded.rsplit("/", 1)[0]
            head_injection = (
                f'<base href="/review/{session_id}/artifact/{base_directory}/">'.encode("ascii")
                + b'<script id="viewspec-hosted-review-bootstrap" data-surface="'
                + surface.encode("ascii")
                + b'">'
                + _HOSTED_FRAME_BOOTSTRAP.encode("utf-8")
                + b"</script>"
            )
            body_injection = (
                b'<script id="viewspec-hosted-review-sdk" data-surface="'
                + surface.encode("ascii")
                + b'">'
                + _HOSTED_FRAME_CLIENT.encode("utf-8")
                + b"</script>"
            )
            head_marker = b"<head>"
            content = (
                content.replace(head_marker, head_marker + head_injection, 1)
                if head_marker in content
                else head_injection + content
            )
            marker = b"</body>"
            content = (
                content.replace(marker, body_injection + marker, 1)
                if marker in content
                else content + body_injection
            )
            response_sha256 = hashlib.sha256(content).hexdigest()
            extra_headers = (
                ("X-ViewSpec-Source-Artifact-SHA256", artifact.sha256),
                ("X-ViewSpec-Presentation-Derivation", "hosted-review-frame-v1"),
            )
        return ReviewHTTPResponse(
            status=200,
            headers=_security_headers(shell=False)
            + (
                ("Content-Type", artifact.media_type),
                ("Content-Length", str(len(content))),
                ("ETag", f'"sha256-{response_sha256}"'),
            )
            + extra_headers,
            body=content,
        )

    def _comment(self, headers: dict[str, str], body: bytes) -> ReviewHTTPResponse:
        _require_origin(headers, self.public_origin)
        payload = _json_body(headers, body, fields={"body", "context"})
        result = self.service.append_comment(
            _browser_cookie(headers),
            body=payload["body"],
            context=payload["context"],
            idempotency_key=_required_header(headers, "idempotency-key"),
        )
        return _json_response(201, result)

    def _approval(self, headers: dict[str, str], body: bytes) -> ReviewHTTPResponse:
        _require_origin(headers, self.public_origin)
        payload = _json_body(headers, body, fields={"revision_identity_sha256"})
        result = self.service.approve_revision(
            _browser_cookie(headers),
            revision_identity_sha256=payload["revision_identity_sha256"],
            idempotency_key=_required_header(headers, "idempotency-key"),
        )
        return _json_response(201, result)

    def _lifecycle(self, session_id: str, headers: dict[str, str], body: bytes) -> ReviewHTTPResponse:
        _require_origin(headers, self.public_origin)
        payload = _json_body(headers, body)
        action = payload.get("action")
        cookie = _browser_cookie(headers)
        key = _required_header(headers, "idempotency-key")
        if action == "rotate_reviewer" and set(payload) == {"action"}:
            result = self.service.rotate_reviewer(cookie, idempotency_key=key)
            fragment = result.pop("reviewer_fragment")
            result["reviewer_url"] = f"{self.public_origin}/review/{session_id}/{fragment}"
        elif action == "shorten_expiry" and set(payload) == {"action", "expires_at"}:
            result = self.service.shorten_expiry(
                cookie,
                expires_at=payload["expires_at"],
                idempotency_key=key,
            )
        elif action == "revoke" and set(payload) == {"action"}:
            result = self.service.revoke(cookie, idempotency_key=key)
        elif action == "delete" and set(payload) == {"action"}:
            result = self.service.delete(cookie, idempotency_key=key)
        else:
            raise _http_invalid("Lifecycle request names an unsupported action or field set.")
        return _json_response(200, result)


def _request_head(request: ReviewHTTPRequest) -> tuple[str, str, dict[str, str]]:
    if not isinstance(request.method, str) or request.method.upper() not in {"GET", "POST"}:
        raise _http_invalid("Private review supports only GET and POST.")
    if (
        not isinstance(request.path, str)
        or not request.path.startswith("/")
        or "?" in request.path
        or "#" in request.path
        or len(request.path.encode("utf-8")) > STUDIO_REVIEW_HTTP_MAX_PATH_BYTES
    ):
        raise _http_invalid("Private review request path is invalid.")
    if not isinstance(request.body, bytes):
        raise _http_invalid("Private review request body must be bytes.")
    headers: dict[str, str] = {}
    total = 0
    for raw_name, raw_value in request.headers.items():
        if not isinstance(raw_name, str) or not isinstance(raw_value, str) or _HEADER_NAME_RE.fullmatch(raw_name) is None:
            raise _http_invalid("Private review request headers are invalid.")
        name = raw_name.lower()
        if name in headers or _HEADER_CONTROL_RE.search(raw_value) is not None or len(raw_value) > 8192:
            raise _http_invalid("Private review request headers are ambiguous.")
        headers[name] = raw_value
        total += len(name) + len(raw_value)
    if total > STUDIO_REVIEW_HTTP_MAX_HEADER_BYTES:
        raise _http_invalid("Private review request headers exceed their bound.")
    return request.method.upper(), request.path, headers


def _json_body(
    headers: dict[str, str],
    body: bytes,
    *,
    fields: set[str] | None = None,
) -> dict[str, object]:
    if headers.get("content-type") != "application/json" or not 2 <= len(body) <= STUDIO_REVIEW_HTTP_MAX_JSON_BYTES:
        raise _http_invalid("Private review mutation requires one bounded JSON object.")
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _http_invalid("Private review mutation JSON is invalid.") from exc
    if not isinstance(value, dict) or (fields is not None and set(value) != fields):
        raise _http_invalid("Private review mutation fields are invalid.")
    return value


def _browser_cookie(headers: dict[str, str]) -> str:
    raw = headers.get("cookie", "")
    if not raw or len(raw) > 4096:
        return "missing-browser-session"
    matches = []
    for item in raw.split(";"):
        name, separator, value = item.strip().partition("=")
        if separator and name == STUDIO_REVIEW_COOKIE_NAME:
            matches.append(value)
    return matches[0] if len(matches) == 1 else "ambiguous-browser-session"


def _required_header(headers: dict[str, str], name: str) -> str:
    value = headers.get(name)
    if not isinstance(value, str) or not value:
        raise _http_invalid(f"Private review requires the {name} header.")
    return value


def _require_origin(headers: dict[str, str], origin: str) -> None:
    if headers.get("origin") != origin:
        raise StudioReviewServiceError(
            "STUDIO_REVIEW_ORIGIN_FORBIDDEN",
            "Private review mutation origin is not the configured HTTPS origin.",
            "Reload the exact private review page before retrying the mutation.",
            http_status=403,
        )


def _json_response(
    status: int,
    payload: dict[str, object],
    *,
    extra_headers: tuple[tuple[str, str], ...] = (),
) -> ReviewHTTPResponse:
    body = canonical_json_bytes(payload)
    return ReviewHTTPResponse(
        status=status,
        headers=_security_headers(shell=True)
        + (("Content-Type", "application/json"), ("Content-Length", str(len(body))))
        + extra_headers,
        body=body,
    )


def _asset_response(content_type: str, body: bytes) -> ReviewHTTPResponse:
    return ReviewHTTPResponse(
        status=200,
        headers=_security_headers(shell=True)
        + (("Content-Type", content_type), ("Content-Length", str(len(body)))),
        body=body,
    )


def _not_found() -> ReviewHTTPResponse:
    error = StudioReviewServiceError(
        "STUDIO_REVIEW_ACCESS_DENIED",
        "Private review access is unavailable.",
        "Use a current unexpired capability or ask the owner for a rotated link.",
        http_status=404,
    )
    return _json_response(404, {"error": error.to_json()})


def _redirect(location: str) -> ReviewHTTPResponse:
    return ReviewHTTPResponse(
        status=308,
        headers=_security_headers(shell=True) + (("Location", location), ("Content-Length", "0")),
        body=b"",
    )


def _security_headers(*, shell: bool) -> tuple[tuple[str, str], ...]:
    csp = (
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "frame-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
        if shell
        else "default-src 'self' data: blob:; script-src 'self' 'unsafe-inline' blob:; "
        "style-src 'self' 'unsafe-inline'; connect-src 'none'; frame-ancestors 'self'; "
        "base-uri 'self'; form-action 'none'; object-src 'none'; sandbox allow-scripts"
    )
    return (
        ("Cache-Control", "private, no-store"),
        ("Content-Security-Policy", csp),
        ("Cross-Origin-Opener-Policy", "same-origin"),
        ("Cross-Origin-Resource-Policy", "same-origin"),
        ("Permissions-Policy", "camera=(), geolocation=(), microphone=(), payment=(), usb=()"),
        ("Referrer-Policy", "no-referrer"),
        ("Strict-Transport-Security", "max-age=31536000; includeSubDomains"),
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY" if shell else "SAMEORIGIN"),
        ("X-Robots-Tag", "noindex, noarchive"),
    )

def _http_invalid(message: str) -> StudioReviewServiceError:
    return StudioReviewServiceError(
        "STUDIO_REVIEW_HTTP_INVALID",
        message,
        "Retry a bounded request against the documented HTTPS contract.",
        http_status=400,
    )


_HOSTED_FRAME_BOOTSTRAP = r"""(() => {
  'use strict'
  const surface = document.currentScript?.dataset.surface || 'unknown'
  const fragment = new URLSearchParams(location.hash.slice(1))
  const rawChannel = fragment.get('channel') || ''
  const rawRoute = fragment.get('route') || '/'
  const channel = /^vsf_[A-Za-z0-9_-]{16,96}$/.test(rawChannel) ? rawChannel : ''
  const route = rawRoute.startsWith('/') && rawRoute.length <= 512 ? rawRoute : '/'
  window.__viewspecHostedReviewTransportV1 = {channel, surface}
  window.__viewspecInitialPath = route
  if (surface === 'html-tailwind-app') history.replaceState({}, '', route === '/' ? location.pathname : `${location.pathname}#${route}`)
})()"""


_HOSTED_FRAME_CLIENT = r"""(() => {
  'use strict'
  const ownScript = document.currentScript
  const surface = ownScript?.dataset.surface || 'unknown'
  const channel = window.__viewspecHostedReviewTransportV1?.channel || ''
  const send = (value) => parent.postMessage({channel, surface, ...value}, '*')
  const currentScreen = () => document.querySelector('[data-viewspec-app-screen]:not([hidden])') || document.querySelector('[data-viewspec-app-screen]')
  const currentRoute = () => {
    const screen = currentScreen()
    return screen?.dataset.routePath || (surface === 'html-tailwind-app' ? (location.hash.slice(1) || '/') : location.pathname)
  }
  const sendContext = (cause = 'passive', routeOverride = null) => send({type: 'viewspec-hosted-context', cause, route: routeOverride || currentRoute(), screen_id: currentScreen()?.dataset.viewspecAppScreen || null})
  const nextFrame = () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))
  const restoreRoute = (route) => {
    if (surface === 'html-tailwind-app') {
      if (location.hash.slice(1) !== route) location.hash = route
    } else {
      dispatchEvent(new CustomEvent('viewspec-app-restore', {detail: {path: route}}))
    }
    requestAnimationFrame(() => sendContext('restore'))
  }
  const applyReplay = async (message) => {
    try {
      for (const event of message.events || []) {
        if (!event || typeof event.route !== 'string' || typeof event.action_id !== 'string') throw new Error('invalid declared event')
        restoreRoute(event.route)
        await nextFrame()
        for (const [binding, value] of Object.entries(event.payload_values || {})) {
          const node = document.querySelector(`[data-binding-id="${CSS.escape(binding)}"]`)
          const expected = value === null ? '' : (typeof value === 'string' ? value : JSON.stringify(value))
          if (!node || node.textContent.trim() !== expected) throw new Error(`payload ${binding} does not match`)
        }
        const action = document.querySelector(`[data-action-id="${CSS.escape(event.action_id)}"]`)
        if (!action) throw new Error(`declared action ${event.action_id} is unavailable`)
        action.click()
        await nextFrame()
      }
      send({type: 'viewspec-hosted-replay-result', ok: true, evidence_ref: message.evidence_ref})
    } catch (error) {
      send({type: 'viewspec-hosted-replay-result', ok: false, reason: String(error?.message || error).slice(0, 256)})
    }
  }
  let commentMode = false
  addEventListener('message', (event) => {
    if (event.source !== parent || !event.data || event.data.channel !== channel) return
    if (event.data.type === 'viewspec-hosted-comment-mode') commentMode = event.data.enabled === true
    if (event.data.type === 'viewspec-hosted-restore' && typeof event.data.route === 'string') restoreRoute(event.data.route)
    if (event.data.type === 'viewspec-hosted-replay') applyReplay(event.data)
  })
  addEventListener('click', (event) => {
    if (!commentMode) return
    event.preventDefault()
    event.stopImmediatePropagation()
    const element = event.target instanceof Element ? event.target : null
    if (!element) return
    const ancestors = []
    for (let node = element; node && node !== document.documentElement && ancestors.length < 32; node = node.parentElement) if (node.id) ancestors.push(node.id)
    const screen = element.closest('[data-viewspec-app-screen]') || currentScreen()
    const binding = element.closest('[data-binding-id]')
    const action = element.closest('[data-action-id]')
    send({
      type: 'viewspec-hosted-selected',
      route: screen?.dataset.routePath || currentRoute(),
      screen_id: screen?.dataset.viewspecAppScreen || null,
      node_id: ancestors[0] || null,
      binding_id: binding?.dataset.bindingId || null,
      action_id: action?.dataset.actionId || null,
      rendered_text: (element.textContent || '').trim().slice(0, 2048),
      visible: element.getClientRects().length > 0,
    })
  }, true)
  addEventListener('hashchange', () => sendContext('navigation'))
  addEventListener('popstate', () => sendContext('navigation'))
  addEventListener('viewspec-app-route', (event) => {
    const route = event instanceof CustomEvent ? event.detail?.path : null
    if (typeof route === 'string') requestAnimationFrame(() => sendContext('navigation', route))
  })
  const nativePush = history.pushState.bind(history)
  history.pushState = (...args) => { nativePush(...args); requestAnimationFrame(() => sendContext('navigation')) }
  addEventListener('load', () => requestAnimationFrame(() => {
    sendContext('initial')
    send({type: 'viewspec-hosted-ready'})
  }))
})()"""


_REVIEW_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,noarchive"><title>Private ViewSpec review</title>
<link rel="stylesheet" href="./client.css"></head>
<body><main id="app"><p class="eyebrow">Private ViewSpec review</p><h1>Opening the exact product…</h1>
<p id="status">Exchanging private access.</p></main><script src="./client.js" defer></script></body></html>
"""


_REVIEW_CLIENT = r"""(() => {
  'use strict'
  const app = document.getElementById('app')
  const jsonHeaders = {'Content-Type': 'application/json'}
  const key = () => crypto.randomUUID().replaceAll('-', '')
  const channel = `vsf_${key()}`
  const fail = () => {
    app.replaceChildren()
    const eyebrow = document.createElement('p'); eyebrow.className = 'eyebrow'; eyebrow.textContent = 'Private ViewSpec review'
    const heading = document.createElement('h1'); heading.textContent = 'Access unavailable'
    const detail = document.createElement('p'); detail.textContent = 'This link expired, was revoked, or is not valid.'
    app.append(eyebrow, heading, detail)
  }
  const request = async (path, options = {}) => {
    const response = await fetch(path, {cache: 'no-store', referrerPolicy: 'no-referrer', ...options})
    const result = await response.json()
    if (!response.ok) throw new Error(result.error?.code || 'request failed')
    return result
  }
  const exchange = async (capability) => {
    history.replaceState(null, '', location.pathname)
    await request('./exchange', {method: 'POST', headers: jsonHeaders, body: JSON.stringify({capability})})
    location.replace(location.pathname)
  }
  const render = (review) => {
    const routes = review.routes
    const routeByPath = new Map(routes.map((item) => [item.path, item]))
    const screens = new Map(review.screens.map((screen) => [screen.id, screen]))
    let route = routes[0]
    let width = 390
    let commentMode = false
    let activeReplayRef = null
    let desiredReplay = null
    let replayDispatched = false
    let selectedSurface = null
    const replayResults = new Map()
    const readySurfaces = new Set()
    const frameRoutes = new Map()
    app.innerHTML = `<header><div><p class="eyebrow" id="role"></p><h1>Review the real product</h1><p>Exact revision <code id="revision"></code></p></div><div class="header-actions"><span class="status">Checked</span><button id="mode" type="button">Comment on product</button></div></header>
      <div class="toolbar"><nav class="routes" aria-label="Product routes"></nav><div class="viewports" aria-label="Viewport"><button data-width="390">Mobile</button><button data-width="768">Tablet</button><button data-width="1440">Desktop</button></div></div>
      <p id="surface-status" class="surface-status" aria-live="polite">Opening the checked target pair…</p>
      <section class="compare"><article><h2>Static</h2><div class="frame"><iframe title="Static product" sandbox="allow-scripts allow-forms" data-surface="html-tailwind-app"></iframe></div></article><article><h2>React</h2><div class="frame"><iframe title="React product" sandbox="allow-scripts allow-forms" data-surface="react-tailwind-app"></iframe></div></article></section>
      <section class="decision"><div class="evidence"><p class="eyebrow">Evidence at the point of use</p><h2 id="evidence"></h2><p>Production data is not claimed. Static/React visual parity is not inferred.</p><div id="replay-tools" hidden><label>Declared checkpoint<select name="replay"></select></label><button id="show-replay" type="button">Show on both targets</button><p id="replay-status"></p></div><div id="resource" class="resource" hidden></div></div><form id="comment"><label>Semantic target<select name="target"></select></label><p id="selection">Choose Comment on product, then point at the interface—or select a semantic target here.</p><label>Comment<textarea name="body" maxlength="4000" required placeholder="Point at the outcome you want changed."></textarea></label><button>Send comment</button></form><div id="owner" hidden><button id="approve" type="button">Approve this exact revision</button><button id="rotate" type="button">Rotate reviewer link</button></div><div class="comments"><p class="eyebrow">Review comments</p><div id="comments"></div></div><p id="result" aria-live="polite"></p></section>`
    app.querySelector('#role').textContent = `Private · ${review.role}`
    app.querySelector('#revision').textContent = review.session.revision_identity_sha256.slice(0, 12)
    const stateLabel = review.inspection.state.status === 'ready' ? 'Replay checked' : 'State not declared'
    const resourceLabel = review.inspection.resources.status === 'ready' ? 'Fixture identity checked' : 'Resources not declared'
    app.querySelector('#evidence').textContent = `${stateLabel} · ${resourceLabel}`
    const comments = app.querySelector('#comments')
    if (!review.comments.length) comments.textContent = 'No comments yet.'
    for (const item of review.comments) {
      const card = document.createElement('article')
      const body = document.createElement('p'); body.textContent = item.body
      const target = document.createElement('code'); target.textContent = `${item.context.target.kind} · ${item.context.target.id}`
      card.append(body, target); comments.append(card)
    }
    const frames = [...app.querySelectorAll('iframe')]
    const post = (frame, message) => frame.contentWindow?.postMessage({channel, ...message}, '*')
    const frameSource = (frame) => `./artifact/${frame.dataset.surface === 'react-tailwind-app' ? 'react' : 'static'}/index.html#channel=${encodeURIComponent(channel)}&route=${encodeURIComponent(route.path)}`
    const setStatus = (message) => { app.querySelector('#surface-status').textContent = message }
    const setTargetOptions = (preferred = null) => {
      const screen = screens.get(route.screen_id)
      const select = app.querySelector('[name=target]')
      const options = Object.entries(screen.targets).flatMap(([kind, ids]) => ids.map((id) => new Option(`${kind} · ${id}`, `${kind}\0${id}`)))
      select.replaceChildren(...options)
      if (preferred && [...select.options].some((item) => item.value === preferred)) select.value = preferred
    }
    const selectRoute = (next, sourceFrame = null) => {
      route = next
      setTargetOptions()
      for (const frame of frames) if (frame !== sourceFrame) post(frame, {type: 'viewspec-hosted-restore', route: next.path})
    }
    const setViewport = (next) => {
      width = next
      app.dataset.viewport = String(next)
      setStatus(`Viewport ${next} · both targets`)
    }
    const routeNav = app.querySelector('.routes')
    routes.forEach((item) => {
      const button = document.createElement('button')
      button.type = 'button'
      button.textContent = item.id
      button.onclick = () => selectRoute(item)
      routeNav.append(button)
    })
    app.querySelectorAll('[data-width]').forEach((button) => { button.onclick = () => setViewport(Number(button.dataset.width)) })
    const mode = app.querySelector('#mode')
    mode.onclick = () => {
      commentMode = !commentMode
      mode.textContent = commentMode ? 'Return to preview' : 'Comment on product'
      setStatus(commentMode ? 'Comment mode · choose something to change' : 'Preview mode · interactions are live')
      frames.forEach((frame) => post(frame, {type: 'viewspec-hosted-comment-mode', enabled: commentMode}))
    }
    const replayChoices = new Map()
    const replaySelect = app.querySelector('[name=replay]')
    const replayTools = app.querySelector('#replay-tools')
    for (const replay of review.inspection.state.replays || []) for (const checkpoint of replay.checkpoints || []) {
      const option = new Option(`${replay.id} · ${checkpoint.label}`, checkpoint.evidence_ref)
      replaySelect.append(option)
      replayChoices.set(checkpoint.evidence_ref, {replay, checkpoint})
    }
    if (replayChoices.size) replayTools.hidden = false
    const updateReplayAvailability = () => {
      const choice = replayChoices.get(replaySelect.value)
      const available = choice?.replay.browser_status === 'replayable'
      app.querySelector('#show-replay').disabled = !available
      const expected = choice?.checkpoint.expected
      const humanize = (value) => String(value || '').replace(/[_-]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
      const parts = expected ? [...(expected.text || []).map((item) => String(item.value)), ...(expected.state || []).map((item) => `${humanize(item.id)}${item.kind === 'scalar' ? ` = ${JSON.stringify(item.value)}` : ' checked'}`), ...(expected.selectors || []).map((item) => `${humanize(item.id)} checked`), ...(expected.visibility || []).map((item) => `${humanize(item.id)} ${item.visible ? 'visible' : 'hidden'}`)] : []
      app.querySelector('#replay-status').textContent = available ? (parts.length ? `Proved result · ${parts.slice(0, 3).join(' · ')}` : 'Exact declared actions can be replayed.') : 'Reducer proof passed; browser replay is unavailable for this checkpoint.'
    }
    replaySelect.onchange = updateReplayAvailability
    updateReplayAvailability()
    app.querySelector('#show-replay').onclick = () => {
      const choice = replayChoices.get(replaySelect.value)
      if (!choice || choice.replay.browser_status !== 'replayable') return
      desiredReplay = {evidence_ref: choice.checkpoint.evidence_ref, events: choice.replay.checkpoints.slice(1, choice.checkpoint.index + 1).map((item) => item.event)}
      activeReplayRef = null
      replayDispatched = false
      replayResults.clear()
      readySurfaces.clear()
      setStatus('Resetting both checked targets…')
      const blanked = frames.map((frame) => new Promise((resolve) => {
        frame.addEventListener('load', resolve, {once: true})
        frame.src = 'about:blank'
      }))
      Promise.all(blanked).then(() => frames.forEach((frame) => { frame.src = frameSource(frame) }))
    }
    const showResource = (message) => {
      const card = app.querySelector('#resource')
      const resources = review.inspection.resources
      if (resources?.status !== 'ready' || !message.binding_id) { card.hidden = true; return }
      const matches = (resources.views || []).filter((view) => view.screen_id === message.screen_id).flatMap((view) => view.assertions || []).filter((item) => item.matched_binding_id === message.binding_id)
      const identities = new Set(matches.map((item) => item.canonical_identity))
      if (identities.size !== 1) { card.hidden = true; return }
      const item = matches[0]
      card.replaceChildren()
      const title = document.createElement('strong'); title.textContent = 'Checked fixture field'
      const path = document.createElement('p'); path.textContent = `${item.resource_id} → ${item.record_id} → ${item.field}`
      const value = document.createElement('code'); value.textContent = String(item.expected)
      const current = document.createElement('p'); current.textContent = `Current ${message.surface} text: ${message.rendered_text || ''}`
      card.append(title, path, value, current); card.hidden = false
    }
    addEventListener('message', (event) => {
      const sourceFrame = frames.find((frame) => event.source === frame.contentWindow)
      if (!sourceFrame || !event.data || event.data.channel !== channel || event.data.surface !== sourceFrame.dataset.surface) return
      const message = event.data
      if (message.type === 'viewspec-hosted-ready') {
        readySurfaces.add(message.surface)
        if (readySurfaces.size === frames.length) {
          setStatus('Checked target pair ready')
          frames.forEach((frame) => post(frame, {type: 'viewspec-hosted-comment-mode', enabled: commentMode}))
          if (desiredReplay && !replayDispatched) {
            replayDispatched = true
            frames.forEach((frame) => post(frame, {type: 'viewspec-hosted-replay', ...desiredReplay}))
          }
        }
        return
      }
      if (message.type === 'viewspec-hosted-context') {
        frameRoutes.set(message.surface, message.route)
        const next = routeByPath.get(message.route)
        if (message.cause === 'navigation' && next && next.path !== route.path) selectRoute(next, sourceFrame)
        return
      }
      if (message.type === 'viewspec-hosted-replay-result') {
        replayResults.set(message.surface, message)
        if (replayResults.size === frames.length) {
          const passed = [...replayResults.values()].every((item) => item.ok === true)
          activeReplayRef = passed ? desiredReplay?.evidence_ref || null : null
          app.querySelector('#replay-status').textContent = passed ? 'Checkpoint active on both targets; comments retain this evidence.' : 'Replay was not attached because both targets did not match.'
          setStatus(passed ? 'Declared checkpoint · both targets applied' : 'Replay unavailable')
          desiredReplay = null
        }
        return
      }
      if (message.type !== 'viewspec-hosted-selected') return
      const next = routeByPath.get(message.route)
      if (!next || next.screen_id !== message.screen_id || message.visible !== true) return
      route = next
      const screen = screens.get(next.screen_id)
      const candidates = [['binding', message.binding_id], ['action', message.action_id], ['node', message.node_id]].filter((entry) => entry[1] && screen.targets[entry[0]].includes(entry[1]))
      if (!candidates.length) return
      const [kind, id] = candidates[0]
      setTargetOptions(`${kind}\0${id}`)
      selectedSurface = message.surface
      app.querySelector('#selection').textContent = `Selected ${kind} · ${id} on ${message.surface}`
      showResource(message)
      app.querySelector('[name=body]').focus()
    })
    const form = app.querySelector('#comment')
    if (review.role !== 'reviewer') form.hidden = true
    form.onsubmit = async (event) => {
      event.preventDefault()
      const data = new FormData(form)
      const [kind, id] = data.get('target').split('\0')
      const screen = screens.get(route.screen_id)
      const context = {revision_identity_sha256: review.session.revision_identity_sha256, route: route.path, screen_id: screen.id, semantic_identity_sha256: screen.semantic_identity_sha256, viewport_width: width, target: {kind, id}, replay_evidence_ref: activeReplayRef}
      try {
        const result = await request('./comments', {method: 'POST', headers: {...jsonHeaders, 'Idempotency-Key': key()}, body: JSON.stringify({body: data.get('body'), context})})
        app.querySelector('#result').textContent = `Comment ${result.status}${selectedSurface ? ` from ${selectedSurface}` : ''}.`
        data.set('body', '')
        form.reset()
        setTargetOptions()
      } catch (error) { app.querySelector('#result').textContent = `Comment was not sent · ${error.message}` }
    }
    const owner = app.querySelector('#owner')
    if (review.role === 'owner') owner.hidden = false
    app.querySelector('#approve').onclick = async () => {
      try {
        const result = await request('./approval', {method: 'POST', headers: {...jsonHeaders, 'Idempotency-Key': key()}, body: JSON.stringify({revision_identity_sha256: review.session.revision_identity_sha256})})
        app.querySelector('#result').textContent = `Revision ${result.status}.`
      } catch (error) { app.querySelector('#result').textContent = `Approval was not recorded · ${error.message}` }
    }
    app.querySelector('#rotate').onclick = async () => {
      try {
        const result = await request('./lifecycle', {method: 'POST', headers: {...jsonHeaders, 'Idempotency-Key': key()}, body: JSON.stringify({action: 'rotate_reviewer'})})
        app.querySelector('#result').textContent = `New reviewer link: ${result.reviewer_url}`
      } catch (error) { app.querySelector('#result').textContent = `Reviewer link was not rotated · ${error.message}` }
    }
    setTargetOptions()
    setViewport(390)
    frames.forEach((frame) => { frame.src = frameSource(frame) })
  }
  const capability = new URLSearchParams(location.hash.slice(1)).get('cap')
  if (capability) exchange(capability).catch(fail)
  else request('./revision').then(render).catch(fail)
})()
"""


_REVIEW_CSS = """*{box-sizing:border-box}
body{margin:0;background:#f4f1e9;color:#171714;font:15px/1.5 ui-sans-serif,system-ui,sans-serif}
main{max-width:1600px;margin:auto;padding:28px}.eyebrow{text-transform:uppercase;letter-spacing:.12em;font-size:12px;font-weight:800;color:#087f70}
h1,h2,p{margin:0}header{display:flex;justify-content:space-between;align-items:end;margin-bottom:16px}.header-actions,.toolbar,.routes,.viewports{display:flex;gap:8px;align-items:center}.toolbar{justify-content:space-between;flex-wrap:wrap;margin-bottom:10px}
.status{padding:7px 12px;border:1px solid #8cb7ad;border-radius:999px;background:#e6f5f0;font-weight:700}.surface-status{margin:8px 0;color:#57554d}
button,select,textarea{font:inherit}button{border:1px solid #aaa697;background:#fff;padding:8px 12px;border-radius:9px;font-weight:700;cursor:pointer}button:disabled{cursor:not-allowed;opacity:.5}#mode{background:#171714;color:#fff;border-color:#171714}
.compare{display:grid;grid-template-columns:1fr 1fr;gap:14px}.compare article,.decision{background:#fff;border:1px solid #d5d0c2;border-radius:16px;padding:14px;overflow:auto}.compare h2{font-size:14px;margin-bottom:8px}.frame{width:390px;margin:auto;transition:width .18s}.frame iframe{display:block;box-sizing:content-box;width:100%;height:844px;border:1px solid #ddd8ca;border-radius:10px;background:#fff}
#app[data-viewport="768"] .frame{width:768px}#app[data-viewport="768"] .frame iframe{height:1024px}#app[data-viewport="1440"] .frame{width:1440px}#app[data-viewport="1440"] .frame iframe{height:1000px}
.decision{display:grid;grid-template-columns:minmax(240px,.8fr) minmax(300px,1.2fr);gap:20px;margin-top:14px}.decision form,.evidence,#owner{display:grid;gap:10px}.decision label{display:grid;gap:4px;font-weight:700}.decision select,.decision textarea{width:100%;border:1px solid #aaa697;border-radius:8px;padding:8px;background:#fff}.decision textarea{min-height:90px;resize:vertical}
.resource,.comments{grid-column:1/-1;border-top:1px solid #e5e1d7;padding-top:12px}.resource code{display:block;margin:6px 0}.comments article{display:flex;justify-content:space-between;gap:16px;padding:10px 0;border-bottom:1px solid #eeeae0}.comments article:last-child{border-bottom:0}.comments code,code{font-size:12px}
#result{grid-column:1/-1;font-weight:700;color:#087f70;overflow-wrap:anywhere}@media(max-width:900px){main{padding:16px}.compare,.decision{grid-template-columns:1fr}.header-actions{align-items:end;flex-direction:column}.frame iframe{height:620px}}"""


__all__ = [
    "ReviewHTTPRequest",
    "ReviewHTTPResponse",
    "STUDIO_REVIEW_COOKIE_NAME",
    "STUDIO_REVIEW_HTTP_MAX_HEADER_BYTES",
    "STUDIO_REVIEW_HTTP_MAX_JSON_BYTES",
    "STUDIO_REVIEW_HTTP_MAX_PATH_BYTES",
    "STUDIO_REVIEW_MEDIA_TYPE",
    "StudioReviewHTTPAdapter",
    "UploadAuthorizer",
]
