"""Hosted compiler HTTP client: compile IntentBundles through api.viewspec.dev.

This module owns the remote client (``compile_remote``, ``compile_remote_response``,
``compile_auto`` and ``CompilerAPIError``) that previously lived in
:mod:`viewspec.compiler`. The compiler module re-exports every public name here,
so ``from viewspec.compiler import compile_remote`` keeps working. ``httpx`` is
imported lazily so the base install stays dependency-free.
"""

from __future__ import annotations

from typing import Any

from viewspec.design_md import DesignSystemError
from viewspec.types import ASTBundle, CompileRequestPayload, CompileResponse, IntentBundle

VIEWSPEC_API_URL = "https://api.viewspec.dev"


class CompilerAPIError(Exception):
    """Structured failure returned by the hosted compiler API."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "remote_error",
        path: str | None = None,
        status_code: int | None = None,
        request_id: str | None = None,
        retry_after: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.path = path
        self.status_code = status_code
        self.request_id = request_id
        self.retry_after = retry_after


def _response_header(response: Any, name: str) -> str | None:
    headers = getattr(response, "headers", {})
    value = headers.get(name) if hasattr(headers, "get") else None
    if value is None and hasattr(headers, "items"):
        value = next((item for key, item in headers.items() if str(key).lower() == name.lower()), None)
    return None if value is None else str(value)


def _compiler_api_error(response: Any, data: Any = None) -> CompilerAPIError:
    status_code = int(getattr(response, "status_code", 0)) or None
    if data is None:
        try:
            data = response.json()
        except ValueError:
            data = None
    error = data.get("error") if isinstance(data, dict) else None
    structured = error if isinstance(error, dict) else {}
    message = str(structured.get("message") or "")
    code = str(structured.get("code") or "remote_error")
    path = structured.get("path") if isinstance(structured.get("path"), str) else None
    if not message and isinstance(data, dict) and isinstance(data.get("message"), str):
        message = data["message"]
    if status_code == 401 and not structured:
        message = "Invalid API key"
        code = "invalid_api_key"
    elif status_code == 429 and not structured:
        message = f"Rate limit exceeded: {message or 'Upgrade at viewspec.dev'}"
        code = "rate_limit_exceeded"
    elif not message:
        message = f"Compilation failed (HTTP {status_code})"
    request_id = data.get("request_id") if isinstance(data, dict) and isinstance(data.get("request_id"), str) else None
    request_id = request_id or _response_header(response, "X-Request-ID")
    return CompilerAPIError(
        message,
        code=code,
        path=path,
        status_code=status_code,
        request_id=request_id,
        retry_after=_response_header(response, "Retry-After"),
    )


def _compile_request_payload(request: IntentBundle | CompileRequestPayload) -> CompileRequestPayload:
    if isinstance(request, CompileRequestPayload):
        return request
    if isinstance(request, IntentBundle):
        return CompileRequestPayload(bundle=request)
    raise TypeError("Expected IntentBundle or CompileRequestPayload")


def compile_remote_response(
    request: IntentBundle | CompileRequestPayload,
    *,
    api_url: str = VIEWSPEC_API_URL,
    api_key: str | None = None,
) -> CompileResponse:
    """
    Compile an IntentBundle or CompileRequestPayload using the hosted ViewSpec compiler API.

    Requires the `httpx` package (`pip install viewspec[remote]`).

    Args:
        request: The IntentBundle or hosted compiler request to compile.
        api_url: Base URL of the ViewSpec API (default: https://api.viewspec.dev).
        api_key: Optional API key for authenticated access (higher rate limits).

    Returns:
        CompileResponse with the compiled AST and response metadata.

    Raises:
        CompilerAPIError: If the API returns an error.
        ImportError: If httpx is not installed.
    """
    try:
        import httpx
    except ImportError:
        raise ImportError(
            "httpx is required for remote compilation. Install it: pip install viewspec[remote]"
        ) from None

    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = _compile_request_payload(request)

    try:
        response = httpx.post(
            f"{api_url.rstrip('/')}/v1/compile",
            json=payload.to_json(),
            headers=headers,
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        raise CompilerAPIError(
            f"Remote compilation request failed: {exc}", code="network_error"
        ) from exc

    def _response_json() -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise CompilerAPIError("Compilation failed: response was not valid JSON") from exc
        if not isinstance(data, dict):
            raise CompilerAPIError("Compilation failed: response JSON was not an object")
        return data

    if response.status_code != 200:
        raise _compiler_api_error(response, _response_json())

    data = _response_json()
    if "ast" not in data:
        raise CompilerAPIError("Compilation failed: response missing ast")
    try:
        return CompileResponse.from_json(data)
    except Exception as exc:
        raise CompilerAPIError("Compilation failed: response ast was invalid") from exc


def compile_remote(
    request: IntentBundle | CompileRequestPayload,
    *,
    api_url: str = VIEWSPEC_API_URL,
    api_key: str | None = None,
) -> ASTBundle:
    """
    Compile an IntentBundle or CompileRequestPayload using the hosted compiler API.

    Returns only the ASTBundle for backward compatibility. Use
    compile_remote_response() to access response metadata such as meta.design.
    """
    return compile_remote_response(request, api_url=api_url, api_key=api_key).ast


def compile_auto(
    request: IntentBundle | CompileRequestPayload,
    *,
    api_url: str = VIEWSPEC_API_URL,
    api_key: str | None = None,
    prefer_local: bool = True,
) -> ASTBundle:
    """
    Compile with automatic local→remote fallback.

    Tries the local reference compiler first. If the ViewSpec contains
    unsupported motif types, falls back to the hosted API (if api_key
    is provided or the free tier is available).

    Args:
        request: The IntentBundle or hosted compiler request to compile.
        api_url: Base URL of the ViewSpec API.
        api_key: Optional API key for higher rate limits.
        prefer_local: If True (default), try local compilation first.

    Returns:
        ASTBundle with the compiled result.
    """
    # Imported lazily: viewspec.compiler re-exports this module, so a top-level
    # import would be circular. Resolving the attribute at call time also keeps
    # ``viewspec.compiler.compile`` patchable from tests, as it was before the move.
    from viewspec.compiler import UnsupportedMotifError, compile as compile_local

    payload = _compile_request_payload(request)
    if prefer_local:
        try:
            return compile_local(payload.bundle, design=payload.design)
        except (UnsupportedMotifError, DesignSystemError):
            return compile_remote(payload, api_url=api_url, api_key=api_key)
    return compile_remote(payload, api_url=api_url, api_key=api_key)


__all__ = [
    "VIEWSPEC_API_URL",
    "CompilerAPIError",
    "compile_auto",
    "compile_remote",
    "compile_remote_response",
]
