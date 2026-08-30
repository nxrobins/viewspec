from __future__ import annotations

import asyncio
from collections.abc import Iterable
import threading

from viewspec.studio_review_asgi import StudioReviewASGIApp
from viewspec.studio_review_http import (
    ReviewHTTPRequest,
    ReviewHTTPResponse,
    STUDIO_REVIEW_HTTP_MAX_JSON_BYTES,
    StudioReviewHTTPAdapter,
)
from viewspec.studio_review_internal import (
    STUDIO_REVIEW_INTERNAL_AUTHENTICATED_HEADER,
    STUDIO_REVIEW_INTERNAL_INGRESS_PATH,
    STUDIO_REVIEW_INTERNAL_NONCE_HEADER,
    StudioReviewInternalAuth,
    StudioReviewInternalNonceStore,
)


class RecordingAdapter(StudioReviewHTTPAdapter):
    def __init__(self) -> None:
        self.requests: list[ReviewHTTPRequest] = []

    def handle(self, request: ReviewHTTPRequest) -> ReviewHTTPResponse:
        self.requests.append(request)
        if request.method == "INVALID":
            return ReviewHTTPResponse(
                status=400,
                headers=(("Content-Type", "application/json"),),
                body=b'{"error":{"code":"STUDIO_REVIEW_HTTP_INVALID"}}',
            )
        return ReviewHTTPResponse(
            status=201,
            headers=(
                ("Content-Type", "application/json"),
                ("Set-Cookie", "first=one"),
                ("Set-Cookie", "second=two"),
            ),
            body=request.body or b"ok",
        )


def _invoke(
    app: StudioReviewASGIApp,
    *,
    path: str = "/v1/reviews",
    raw_path: bytes | None = None,
    method: str = "POST",
    scheme: str = "https",
    headers: Iterable[tuple[bytes, bytes]] = (),
    messages: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    pending = list(messages or [{"type": "http.request", "body": b"", "more_body": False}])
    sent: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        return pending.pop(0)

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    scope: dict[str, object] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": scheme,
        "path": path,
        "raw_path": raw_path if raw_path is not None else path.encode("ascii"),
        "query_string": b"",
        "headers": list(headers),
    }
    asyncio.run(app(scope, receive, send))
    return sent


def test_asgi_bridge_streams_one_bounded_review_request_and_preserves_response_headers() -> None:
    adapter = RecordingAdapter()
    app = StudioReviewASGIApp(adapter)
    sent = _invoke(
        app,
        headers=((b"content-type", b"application/vnd.viewspec.review+zip"), (b"content-length", b"6")),
        messages=[
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"def", "more_body": False},
        ],
    )

    assert len(adapter.requests) == 1
    assert adapter.requests[0] == ReviewHTTPRequest(
        method="POST",
        path="/v1/reviews",
        headers={"content-type": "application/vnd.viewspec.review+zip", "content-length": "6"},
        body=b"abcdef",
        scheme="https",
    )
    assert sent[0] == {
        "type": "http.response.start",
        "status": 201,
        "headers": [
            (b"content-type", b"application/json"),
            (b"set-cookie", b"first=one"),
            (b"set-cookie", b"second=two"),
        ],
    }
    assert sent[1] == {"type": "http.response.body", "body": b"abcdef", "more_body": False}


def test_asgi_bridge_uses_raw_path_and_scope_scheme_without_trusting_forwarded_headers() -> None:
    adapter = RecordingAdapter()
    app = StudioReviewASGIApp(adapter)
    _invoke(
        app,
        path="/review/vsr_AAAAAAAAAAAAAAAAAAAAAAAA/artifact/static/a/b",
        raw_path=b"/review/vsr_AAAAAAAAAAAAAAAAAAAAAAAA/artifact/static/a%2Fb",
        method="GET",
        scheme="http",
        headers=((b"x-forwarded-proto", b"https"),),
    )

    request = adapter.requests[0]
    assert request.path.endswith("a%2Fb")
    assert request.scheme == "http"
    assert request.headers == {"x-forwarded-proto": "https"}


def test_asgi_bridge_strips_internal_transport_headers_before_external_dispatch() -> None:
    adapter = RecordingAdapter()
    app = StudioReviewASGIApp(adapter)
    _invoke(
        app,
        headers=(
            (b"content-type", b"text/plain"),
            (b"x-viewspec-internal-authenticated", b"true"),
            (b"x-viewspec-internal-signature", b"hmac-sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
        ),
    )

    assert adapter.requests[0].headers == {"content-type": "text/plain"}


def test_asgi_bridge_rejects_duplicate_or_oversized_inputs_before_dispatch() -> None:
    for headers, messages in (
        (
            ((b"content-type", b"application/json"), (b"Content-Type", b"application/json")),
            [{"type": "http.request", "body": b"{}", "more_body": False}],
        ),
        (
            ((b"content-length", str(STUDIO_REVIEW_HTTP_MAX_JSON_BYTES + 1).encode()),),
            [{"type": "http.request", "body": b"", "more_body": False}],
        ),
        (
            (),
            [
                {
                    "type": "http.request",
                    "body": b"x" * (STUDIO_REVIEW_HTTP_MAX_JSON_BYTES + 1),
                    "more_body": False,
                }
            ],
        ),
    ):
        adapter = RecordingAdapter()
        app = StudioReviewASGIApp(adapter)
        sent = _invoke(
            app,
            path="/review/vsr_AAAAAAAAAAAAAAAAAAAAAAAA/comments",
            headers=headers,
            messages=messages,
        )
        assert [request.method for request in adapter.requests] == ["INVALID"]
        assert sent[0]["status"] == 400


def test_asgi_bridge_delegates_unrelated_paths_and_non_http_scopes() -> None:
    adapter = RecordingAdapter()
    delegated: list[str] = []

    async def downstream(scope, receive, send) -> None:
        delegated.append(str(scope["type"]))
        if scope["type"] == "http":
            await receive()
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b"", "more_body": False})

    app = StudioReviewASGIApp(adapter, downstream=downstream)
    sent = _invoke(app, path="/v1/compile")
    assert sent[0]["status"] == 204

    async def receive() -> dict[str, object]:
        return {"type": "lifespan.shutdown"}

    async def send(_message: dict[str, object]) -> None:
        return None

    asyncio.run(app({"type": "lifespan"}, receive, send))
    assert delegated == ["http", "lifespan"]
    assert adapter.requests == []


def test_standalone_asgi_bridge_handles_disconnect_and_lifespan_cleanly() -> None:
    adapter = RecordingAdapter()
    app = StudioReviewASGIApp(adapter)
    assert _invoke(app, messages=[{"type": "http.disconnect"}]) == []
    assert adapter.requests == []

    pending = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
    sent: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        return pending.pop(0)

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    asyncio.run(app({"type": "lifespan"}, receive, send))
    assert sent == [{"type": "lifespan.startup.complete"}, {"type": "lifespan.shutdown.complete"}]


def test_asgi_bridge_rejects_non_ascii_raw_paths() -> None:
    adapter = RecordingAdapter()
    sent = _invoke(StudioReviewASGIApp(adapter), raw_path=b"/review/\xff")
    assert [request.method for request in adapter.requests] == ["INVALID"]
    assert sent[0]["status"] == 400


def test_asgi_bridge_runs_blocking_review_service_outside_the_event_loop_thread() -> None:
    main_thread = threading.get_ident()
    observed: list[int] = []

    class ThreadRecordingAdapter(RecordingAdapter):
        def handle(self, request: ReviewHTTPRequest) -> ReviewHTTPResponse:
            observed.append(threading.get_ident())
            return super().handle(request)

    sent = _invoke(StudioReviewASGIApp(ThreadRecordingAdapter()))
    assert sent[0]["status"] == 201
    assert len(observed) == 1
    assert observed[0] != main_thread


def test_internal_ingress_binds_request_and_response_while_direct_create_stays_closed(tmp_path) -> None:
    now = 1_800_000_000
    secret = b"api-to-review-asgi-test-secret-0001"
    request_nonce = "1" * 32
    response_nonce = "2" * 32
    client = StudioReviewInternalAuth(
        secret,
        nonce_store=StudioReviewInternalNonceStore(tmp_path / "client-nonces.sqlite3"),
        clock=lambda: now,
        nonce_factory=lambda: request_nonce,
    )
    service = StudioReviewInternalAuth(
        secret,
        nonce_store=StudioReviewInternalNonceStore(tmp_path / "service-nonces.sqlite3"),
        clock=lambda: now,
        nonce_factory=lambda: response_nonce,
    )
    body = b"abcdef"
    request_headers = client.sign_request(
        method="POST",
        path=STUDIO_REVIEW_INTERNAL_INGRESS_PATH,
        headers={
            "content-type": "application/vnd.viewspec.review+zip",
            "idempotency-key": "internal-asgi-create-0001",
            "x-viewspec-disclosure-accepted": "true",
            "x-viewspec-expiry-seconds": "3600",
        },
        body=body,
    )
    adapter = RecordingAdapter()
    delegated: list[str] = []

    async def downstream(scope, receive, send) -> None:
        delegated.append(str(scope["path"]))
        await receive()
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    app = StudioReviewASGIApp(
        adapter,
        downstream=downstream,
        internal_auth=service,
        allow_direct_create=False,
    )
    sent = _invoke(
        app,
        path=STUDIO_REVIEW_INTERNAL_INGRESS_PATH,
        scheme="http",
        headers=tuple((name.encode(), value.encode()) for name, value in request_headers.items()),
        messages=[{"type": "http.request", "body": body, "more_body": False}],
    )

    assert len(adapter.requests) == 1
    assert adapter.requests[0] == ReviewHTTPRequest(
        method="POST",
        path="/v1/reviews",
        headers={
            "content-type": "application/vnd.viewspec.review+zip",
            "idempotency-key": "internal-asgi-create-0001",
            "x-viewspec-disclosure-accepted": "true",
            "x-viewspec-expiry-seconds": "3600",
            STUDIO_REVIEW_INTERNAL_AUTHENTICATED_HEADER: "true",
        },
        body=body,
        scheme="https",
    )
    assert sent[0]["status"] == 201
    response_headers = {
        name.decode(): value.decode()
        for name, value in sent[0]["headers"]
        if name.decode() != "set-cookie"
    }
    client.verify_response(
        status=201,
        path=STUDIO_REVIEW_INTERNAL_INGRESS_PATH,
        headers=response_headers,
        body=body,
        request_nonce=request_headers[STUDIO_REVIEW_INTERNAL_NONCE_HEADER],
    )

    direct = _invoke(
        app,
        path="/v1/reviews",
        headers=((b"content-type", b"application/vnd.viewspec.review+zip"),),
        messages=[{"type": "http.request", "body": body, "more_body": False}],
    )
    assert direct[0]["status"] == 404
    assert len(adapter.requests) == 1
    assert delegated == []


def test_internal_ingress_is_not_delegated_without_internal_auth() -> None:
    adapter = RecordingAdapter()
    delegated: list[str] = []

    async def downstream(scope, receive, send) -> None:
        delegated.append(str(scope["path"]))
        await receive()
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    sent = _invoke(
        StudioReviewASGIApp(adapter, downstream=downstream),
        path=STUDIO_REVIEW_INTERNAL_INGRESS_PATH,
    )

    assert sent[0]["status"] == 404
    assert adapter.requests == []
    assert delegated == []


def test_internal_ingress_rejects_tampering_before_adapter_dispatch(tmp_path) -> None:
    now = 1_800_000_000
    secret = b"api-to-review-asgi-test-secret-0002"
    client = StudioReviewInternalAuth(
        secret,
        nonce_store=StudioReviewInternalNonceStore(tmp_path / "client-nonces.sqlite3"),
        clock=lambda: now,
        nonce_factory=lambda: "3" * 32,
    )
    service = StudioReviewInternalAuth(
        secret,
        nonce_store=StudioReviewInternalNonceStore(tmp_path / "service-nonces.sqlite3"),
        clock=lambda: now,
    )
    signed = client.sign_request(
        method="POST",
        path=STUDIO_REVIEW_INTERNAL_INGRESS_PATH,
        headers={
            "content-type": "application/vnd.viewspec.review+zip",
            "idempotency-key": "internal-asgi-create-0002",
            "x-viewspec-disclosure-accepted": "true",
            "x-viewspec-expiry-seconds": "3600",
        },
        body=b"original",
    )
    adapter = RecordingAdapter()
    app = StudioReviewASGIApp(adapter, internal_auth=service, allow_direct_create=False)
    sent = _invoke(
        app,
        path=STUDIO_REVIEW_INTERNAL_INGRESS_PATH,
        headers=tuple((name.encode(), value.encode()) for name, value in signed.items()),
        messages=[{"type": "http.request", "body": b"tampered", "more_body": False}],
    )

    assert sent[0]["status"] == 401
    assert b"STUDIO_REVIEW_UPLOAD_UNAUTHORIZED" in sent[1]["body"]
    assert adapter.requests == []
