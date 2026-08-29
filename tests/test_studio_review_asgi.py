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
