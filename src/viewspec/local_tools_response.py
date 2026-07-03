from __future__ import annotations

from typing import Any
from viewspec.design_md import DesignSystemError
from viewspec.emitters.react_tailwind_tsx import CompilerConstraintError
from viewspec.raw_html import HtmlInputError
from viewspec.local_tools_constants import (MCP_RESERVED_RESULT_KEYS, MCP_RESULT_SCHEMA_VERSION)

class LocalToolError(ValueError):
    """Agent-readable local tool failure."""

    def __init__(self, code: str, message: str, fix: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.fix = fix

    def to_json(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "fix": self.fix}

def tool_response(
    ok: bool,
    summary: str,
    *,
    diagnostics: Any = (),
    external_refs: Any = (),
    paths: dict[str, str] | None = None,
    next_actions: list[str] | tuple[str, ...] = (),
    errors: list[dict[str, str]] | tuple[dict[str, str], ...] = (),
    metadata: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": MCP_RESULT_SCHEMA_VERSION,
        "ok": ok,
        "summary": summary,
        "diagnostics": [dict(item) for item in diagnostics],
        "external_refs": [dict(item) for item in external_refs],
        "paths": dict(paths or {}),
        "next_actions": list(next_actions),
        "errors": [dict(item) for item in errors],
    }
    if metadata:
        payload["metadata"] = metadata
    if data:
        conflicts = sorted(MCP_RESERVED_RESULT_KEYS & set(data))
        if conflicts:
            raise LocalToolError(
                "MCP_RESPONSE_SCHEMA_CONFLICT",
                f"Tool data attempted to overwrite reserved MCP result keys: {conflicts}",
                "Rename extension data fields so they do not collide with the MCP result envelope.",
            )
        payload.update(data)
    return payload

def tool_error_response(
    code: str,
    message: str,
    fix: str,
    **kwargs: Any,
) -> dict[str, Any]:
    errors = list(kwargs.pop("errors", ())) or [{"code": code, "message": message, "fix": fix}]
    return tool_response(False, message, errors=errors, next_actions=[fix], **kwargs)

def exception_response(
    exc: Exception,
    fallback_code: str,
    fallback_fix: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if isinstance(exc, LocalToolError):
        return tool_error_response(exc.code, exc.message, exc.fix, metadata=metadata)
    if isinstance(exc, CompilerConstraintError):
        return tool_error_response(exc.code, str(exc), "Fix the Tailwind emitter constraint violation and retry.", metadata=metadata)
    if isinstance(exc, HtmlInputError):
        return tool_error_response(exc.code, str(exc), fallback_fix, metadata=metadata)
    if isinstance(exc, DesignSystemError):
        return tool_error_response("COMPILE_FAILED", str(exc), "Fix DESIGN.md and retry.", metadata=metadata)
    if hasattr(exc, "code") and str(getattr(exc, "code")) in {
        "AGENT_ASSET_CONFLICT",
        "AGENT_ASSET_OUTPUT_NOT_DIRECTORY",
    }:
        return tool_error_response(
            "IO_ERROR",
            str(exc),
            "Choose a writable asset output directory, or pass force=True to replace existing generated assets.",
            metadata=metadata,
        )
    return tool_error_response(fallback_code, str(exc), fallback_fix, metadata=metadata)
