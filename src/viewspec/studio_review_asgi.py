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
from viewspec.studio_review_internal import (
    STUDIO_REVIEW_INTERNAL_AUTHENTICATED_HEADER,
    STUDIO_REVIEW_INTERNAL_INGRESS_PATH,
    StudioReviewInternalAuth,
    StudioReviewInternalAuthError,
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
        internal_auth: StudioReviewInternalAuth | None = None,
        allow_direct_create: bool = True,
    ) -> None:
        if not isinstance(adapter, StudioReviewHTTPAdapter):
            raise TypeError("StudioReviewASGIApp requires a StudioReviewHTTPAdapter.")
        if downstream is not None and not callable(downstream):
            raise TypeError("StudioReviewASGIApp downstream must be callable.")
        if internal_auth is not None and not isinstance(internal_auth, StudioReviewInternalAuth):
            raise TypeError("StudioReviewASGIApp internal_auth must use the exact internal authentication contract.")
        if not isinstance(allow_direct_create, bool):
            raise TypeError("StudioReviewASGIApp allow_direct_create must be a boolean.")
        self.adapter = adapter
        self.downstream = downstream
        self.internal_auth = internal_auth
        self.allow_direct_create = allow_direct_create

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
        if path == "/v1/reviews" and not self.allow_direct_create:
            await _send_response(send, _route_not_found())
            return
        if path == STUDIO_REVIEW_INTERNAL_INGRESS_PATH and self.internal_auth is None:
            await _send_response(send, _route_not_found())
            return
        if not _is_review_path(path):
            await self._delegate_or_not_found(scope, receive, send)
            return

        headers = _scope_headers(scope)
        if headers is None:
            await _send_response(send, _invalid_request(self.adapter))
            return
        body_limit = (
            STUDIO_SHARE_ARCHIVE_MAX_BYTES
            if path in {"/v1/reviews", STUDIO_REVIEW_INTERNAL_INGRESS_PATH}
            else STUDIO_REVIEW_HTTP_MAX_JSON_BYTES
        )
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
        if path == STUDIO_REVIEW_INTERNAL_INGRESS_PATH:
            response = await self._handle_internal_ingress(scope, headers, body)
        else:
            external_headers = _external_review_headers(headers)
            response = await asyncio.to_thread(
                self.adapter.handle,
                ReviewHTTPRequest(
                    method=str(scope.get("method", "")),
                    path=path,
                    headers=external_headers,
                    body=body,
                    scheme=str(scope.get("scheme", "")),
                ),
            )
        await _send_response(send, response)

    async def _delegate_or_not_found(
        self,
        scope: ASGIScope,
        receive: ASGIReceive,
        send: ASGISend,
    ) -> None:
        if self.downstream is not None:
            await self.downstream(scope, receive, send)
            return
        await _send_response(send, _route_not_found())

    async def _handle_internal_ingress(
        self,
        scope: ASGIScope,
        headers: Mapping[str, str],
        body: bytes,
    ) -> ReviewHTTPResponse:
        assert self.internal_auth is not None
        method = str(scope.get("method", ""))
        try:
            verified = self.internal_auth.verify_request(
                method=method,
                path=STUDIO_REVIEW_INTERNAL_INGRESS_PATH,
                headers=headers,
                body=body,
            )
        except StudioReviewInternalAuthError:
            return _internal_authentication_failed()
        forwarded = {
            **dict(verified.forwarded_headers),
            STUDIO_REVIEW_INTERNAL_AUTHENTICATED_HEADER: "true",
        }
        response = await asyncio.to_thread(
            self.adapter.handle,
            ReviewHTTPRequest(
                method="POST",
                path="/v1/reviews",
                headers=forwarded,
                body=body,
                scheme="https",
            ),
        )
        try:
            content_type = _response_content_type(response)
            authentication = self.internal_auth.sign_response(
                status=response.status,
                path=STUDIO_REVIEW_INTERNAL_INGRESS_PATH,
                content_type=content_type,
                body=response.body,
                request_nonce=verified.request_nonce,
            )
        except StudioReviewInternalAuthError:
            return _internal_response_failed()
        return ReviewHTTPResponse(
            status=response.status,
            headers=(*response.headers, *tuple(authentication.items())),
            body=response.body,
        )


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


def _external_review_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {name: value for name, value in headers.items() if not name.startswith("x-viewspec-internal-")}


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
    return (
        path in {"/v1/reviews", STUDIO_REVIEW_INTERNAL_INGRESS_PATH, "/review"}
        or path.startswith("/review/")
    )


def _invalid_request(adapter: StudioReviewHTTPAdapter) -> ReviewHTTPResponse:
    return adapter.handle(ReviewHTTPRequest(method="INVALID", path="/", headers={}, scheme="https"))


def _response_content_type(response: ReviewHTTPResponse) -> str:
    values = [value for name, value in response.headers if name.lower() == "content-type"]
    if len(values) != 1:
        raise StudioReviewInternalAuthError("Private review internal response has no unique content type.")
    return values[0]


def _internal_authentication_failed() -> ReviewHTTPResponse:
    return ReviewHTTPResponse(
        status=401,
        headers=(("Content-Type", "application/json"),),
        body=(
            b'{"error":{"code":"STUDIO_REVIEW_UPLOAD_UNAUTHORIZED",'
            b'"fix":"Retry through the authenticated private ingress.",'
            b'"message":"Private review internal authentication failed."}}'
        ),
    )


def _internal_response_failed() -> ReviewHTTPResponse:
    return ReviewHTTPResponse(
        status=500,
        headers=(("Content-Type", "application/json"),),
        body=(
            b'{"error":{"code":"STUDIO_REVIEW_HTTP_INVALID",'
            b'"fix":"Retry the same idempotent private review creation request.",'
            b'"message":"Private review response authentication failed."}}'
        ),
    )


def _route_not_found() -> ReviewHTTPResponse:
    return ReviewHTTPResponse(
        status=404,
        headers=(("Content-Type", "application/json"),),
        body=(
            b'{"error":{"code":"STUDIO_REVIEW_ACCESS_DENIED",'
            b'"fix":"Use a current unexpired capability or the authenticated private ingress.",'
            b'"message":"Private review access is unavailable."}}'
        ),
    )


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
