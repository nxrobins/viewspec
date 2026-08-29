"""Bounded ASGI bridge for the private ViewSpec Studio review protocol.

The bridge contains no framework dependency. It can wrap an existing ASGI app
and intercept only ``/v1/reviews`` plus ``/review/...``, or run standalone. TLS
identity comes only from the ASGI scope; untrusted forwarding headers are never
used to manufacture HTTPS.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from typing import Any

from viewspec.studio_review_http import (
    ReviewHTTPRequest,
    ReviewHTTPResponse,
    STUDIO_REVIEW_HTTP_MAX_HEADER_BYTES,
    STUDIO_REVIEW_HTTP_MAX_JSON_BYTES,
    STUDIO_REVIEW_HTTP_MAX_PATH_BYTES,
    StudioReviewHTTPAdapter,
)
from viewspec.studio_share import STUDIO_SHARE_ARCHIVE_MAX_BYTES


ASGIScope = MutableMapping[str, Any]
ASGIMessage = MutableMapping[str, Any]
ASGIReceive = Callable[[], Awaitable[ASGIMessage]]
ASGISend = Callable[[ASGIMessage], Awaitable[None]]
ASGIApp = Callable[[ASGIScope, ASGIReceive, ASGISend], Awaitable[None]]


class StudioReviewASGIApp:
    """Map bounded ASGI HTTP messages into :class:`StudioReviewHTTPAdapter`.

    When ``downstream`` is supplied, unrelated paths and non-HTTP scopes are
    delegated unchanged. This lets a hosted compiler API add private review
    without moving or duplicating its existing routes.
    """

    def __init__(
        self,
        adapter: StudioReviewHTTPAdapter,
        *,
        downstream: ASGIApp | None = None,
    ) -> None:
        if not isinstance(adapter, StudioReviewHTTPAdapter):
            raise TypeError("StudioReviewASGIApp requires a StudioReviewHTTPAdapter.")
        if downstream is not None and not callable(downstream):
            raise TypeError("StudioReviewASGIApp downstream must be callable.")
        self.adapter = adapter
        self.downstream = downstream

    async def __call__(self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        if scope.get("type") != "http":
            if self.downstream is not None:
                await self.downstream(scope, receive, send)
                return
            await _unsupported_scope(scope, receive, send)
            return

        path = _scope_path(scope)
        if path is None:
            await _send_response(send, _invalid_request(self.adapter))
            return
        if not _is_review_path(path):
            if self.downstream is not None:
                await self.downstream(scope, receive, send)
                return
            await _send_response(
                send,
                self.adapter.handle(
                    ReviewHTTPRequest(method="GET", path=path, headers={}, scheme="https")
                ),
            )
            return

        headers = _scope_headers(scope)
        if headers is None:
            await _send_response(send, _invalid_request(self.adapter))
            return
        body_limit = STUDIO_SHARE_ARCHIVE_MAX_BYTES if path == "/v1/reviews" else STUDIO_REVIEW_HTTP_MAX_JSON_BYTES
        content_length = headers.get("content-length")
        if content_length is not None and (not content_length.isdigit() or int(content_length) > body_limit):
            await _send_response(send, _invalid_request(self.adapter))
            return
        body = await _receive_body(receive, maximum=body_limit)
        if body is None:
            return
        if body is _BODY_INVALID:
            await _send_response(send, _invalid_request(self.adapter))
            return
        response = await asyncio.to_thread(
            self.adapter.handle,
            ReviewHTTPRequest(
                method=str(scope.get("method", "")),
                path=path,
                headers=headers,
                body=body,
                scheme=str(scope.get("scheme", "")),
            ),
        )
        await _send_response(send, response)


_BODY_INVALID = object()


def _scope_path(scope: Mapping[str, Any]) -> str | None:
    raw_path = scope.get("raw_path")
    if isinstance(raw_path, bytes):
        try:
            path = raw_path.decode("ascii")
        except UnicodeDecodeError:
            return None
    else:
        path = scope.get("path")
    if not isinstance(path, str) or len(path.encode("utf-8")) > STUDIO_REVIEW_HTTP_MAX_PATH_BYTES:
        return None
    return path


def _scope_headers(scope: Mapping[str, Any]) -> dict[str, str] | None:
    raw_headers = scope.get("headers")
    if not isinstance(raw_headers, list):
        return None
    headers: dict[str, str] = {}
    total = 0
    for item in raw_headers:
        if not isinstance(item, tuple) or len(item) != 2 or not all(isinstance(value, bytes) for value in item):
            return None
        raw_name, raw_value = item
        try:
            name = raw_name.decode("ascii")
            value = raw_value.decode("latin-1")
        except UnicodeDecodeError:
            return None
        lowered = name.lower()
        if lowered in headers:
            return None
        headers[lowered] = value
        total += len(raw_name) + len(raw_value)
        if total > STUDIO_REVIEW_HTTP_MAX_HEADER_BYTES:
            return None
    return headers


async def _receive_body(receive: ASGIReceive, *, maximum: int) -> bytes | object | None:
    body = bytearray()
    while True:
        message = await receive()
        message_type = message.get("type")
        if message_type == "http.disconnect":
            return None
        if message_type != "http.request":
            return _BODY_INVALID
        chunk = message.get("body", b"")
        if not isinstance(chunk, bytes):
            return _BODY_INVALID
        body.extend(chunk)
        if len(body) > maximum:
            return _BODY_INVALID
        if not message.get("more_body", False):
            return bytes(body)


def _is_review_path(path: str) -> bool:
    return path == "/v1/reviews" or path == "/review" or path.startswith("/review/")


def _invalid_request(adapter: StudioReviewHTTPAdapter) -> ReviewHTTPResponse:
    return adapter.handle(ReviewHTTPRequest(method="INVALID", path="/", headers={}, scheme="https"))


async def _send_response(send: ASGISend, response: ReviewHTTPResponse) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": response.status,
            "headers": [(name.lower().encode("ascii"), value.encode("latin-1")) for name, value in response.headers],
        }
    )
    await send({"type": "http.response.body", "body": response.body, "more_body": False})


async def _unsupported_scope(scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
    if scope.get("type") == "lifespan":
        while True:
            message = await receive()
            if message.get("type") == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message.get("type") == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
            else:
                return
    elif scope.get("type") == "websocket":
        await send({"type": "websocket.close", "code": 1008})


__all__ = ["ASGIApp", "StudioReviewASGIApp"]
