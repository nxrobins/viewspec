"""`viewspec mcp`: the CLI's five verbs as MCP tools for a coding agent, over stdio."""

from __future__ import annotations

import functools
import importlib
import os
import signal
from collections.abc import Callable
from pathlib import Path
from types import FrameType
from typing import Any, Literal

from viewspec import api
from viewspec._version import __version__
from viewspec.app_bundle import APP_SHELL_TARGET, compile_app, init_app_file
from viewspec.intent_tools import compile_intent_bundle_file_tool, init_intent_file
from viewspec.local_tools import (
    exception_response,
    init_design_file,
    resolve_cwd,
    resolve_local_path,
    tool_error_response,
    tool_response,
)
from viewspec.studio import Studio, StudioError, resolve_studio_source

MCP_INSTALL_HINT = 'Install with: python -m pip install "viewspec[agents]"'
# The module run_mcp_server imports. MCP SDK 2.x removed it, which is why the agents extra
# caps mcp below 2.
_MCP_SERVER_MODULE = "mcp.server.fastmcp"
_SERVER_INSTRUCTIONS = (
    "ViewSpec compiles semantic JSON into checked UI. Edit viewspec.intent.json or viewspec.app.json, never "
    "the generated output. The loop is init, edit the source, check, compile, then prove. Every result "
    "carries ok; on failure, follow its errors, issues, or correction_prompt and retry."
)


class MissingMCPDependency(RuntimeError):
    """Raised when the optional MCP dependency is unavailable."""


def mcp_dependency_available() -> bool:
    """Report whether the MCP SDK module the server imports actually imports.

    Finding the top-level ``mcp`` package is not enough: MCP 2.x keeps a
    ``mcp.server.fastmcp`` stub that raises ModuleNotFoundError on import, so the
    server could not start although a spec lookup would succeed.

    Any exception the import raises counts as "not available", not only ImportError: a
    broken or partly installed SDK can fail at import time with, for example, a TypeError
    or a pydantic error.
    """
    try:
        module = importlib.import_module(_MCP_SERVER_MODULE)
    except Exception:
        return False
    return hasattr(module, "FastMCP")


def _metadata() -> dict[str, str]:
    return {"sdk_version": __version__, "network_calls": "none"}


def _reported(code: str, fix: str) -> Callable[[Callable[..., dict[str, Any]]], Callable[..., dict[str, Any]]]:
    """Return a raised failure as a report, so an agent gets a code and a fix, not a traceback."""

    def decorate(tool: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
        @functools.wraps(tool)
        def run(*args: Any, **kwargs: Any) -> dict[str, Any]:
            try:
                return tool(*args, **kwargs)
            except StudioError as exc:
                return tool_error_response(exc.code, exc.message, exc.fix, metadata=_metadata())
            except Exception as exc:
                return exception_response(exc, code, fix, metadata=_metadata())

        return run

    return decorate


class ViewSpecTools:
    """The tool bodies. Every path stays inside ``cwd`` unless ``allow_outside_cwd`` is set."""

    def __init__(self, cwd: str | Path | None = None, *, allow_outside_cwd: bool = False) -> None:
        self.root = resolve_cwd(cwd)
        self.allow_outside_cwd = allow_outside_cwd
        self._preview: Studio | None = None

    def _path(self, value: str | Path, *, must_exist: bool = False) -> Path:
        return resolve_local_path(value, cwd=self.root, allow_outside_cwd=self.allow_outside_cwd, must_exist=must_exist)

    def _source(self, value: str | None) -> Path | None:
        return self._path(value, must_exist=True) if value is not None else None

    @_reported("IO_ERROR", "Choose a writable path under the working directory, or pass force=True.")
    def init(
        self,
        what: Literal["intent", "app", "design"] = "intent",
        out: str | None = None,
        kind: str | None = None,
        template: Literal["contract", "react-app"] = "contract",
        force: bool = False,
    ) -> dict[str, Any]:
        """Write a starter viewspec.intent.json, viewspec.app.json, or DESIGN.md.

        Replace the sample content with the user's intent. kind picks a starter motif for intent
        (default dashboard) or an app kind for app (default internal_tool). template="react-app"
        writes the runnable AppBundle golden path.
        """
        path = self._path(out or api.INIT_DEFAULT_OUT[what])
        if what == "app":
            written = init_app_file(path, kind=kind or "internal_tool", force=force, template=template)
        elif what == "design":
            written = init_design_file(path, force=force)
        else:
            written = init_intent_file(path, kind=kind or "dashboard", force=force)
        return tool_response(
            True,
            f"Wrote {written.name}.",
            paths={what: str(written)},
            next_actions=[f"Edit {written.name}, then run the check tool on it."],
            metadata=_metadata(),
        )

    @_reported("COMPILE_FAILED", "Fix the input, DESIGN.md, or path issue and retry.")
    def compile(
        self,
        source: str,
        out: str,
        target: str | None = None,
        design: str | None = None,
        strict_design: bool = False,
        force: bool = False,
        uri: str | None = None,
        action_tools: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Compile an IntentBundle or AppBundle JSON file into a directory; the kind is detected.

        IntentBundle targets: html-tailwind (default), react-tsx, react-tailwind-tsx, mcp-app.
        AppBundle targets: html-tailwind-app (default), react-tailwind-app. design is a DESIGN.md
        path. force replaces an existing AppBundle output directory after safety checks. uri and
        action_tools apply to mcp-app: the ui:// resource URI, and a map of action id to MCP tool.
        """
        input_path = self._path(source, must_exist=True)
        output = self._path(out)
        design_path = self._source(design)
        if api.source_kind(input_path) == "app_bundle":
            return compile_app(
                input_path,
                out_dir=output,
                design_path=design_path,
                strict_design=strict_design,
                force=force,
                target=target or APP_SHELL_TARGET,
                cwd=self.root,
            )
        return compile_intent_bundle_file_tool(
            input_path,
            output,
            design_path=design_path,
            strict_design=strict_design,
            target=target or "html-tailwind",
            cwd=self.root,
            allow_outside_cwd=self.allow_outside_cwd,
            uri=uri,
            action_tools=action_tools,
        )

    @_reported("CHECK_FAILED", "Fix the reported path and retry.")
    def check(self, path: str, compile_check: bool = True) -> dict[str, Any]:
        """Run the fast local checks on a source file or a compiled artifact directory.

        A source file gets contract checks, and a failure includes a correction_prompt to act on.
        A directory gets provenance, tamper, and active-content checks.
        """
        return api.check(self._path(path), compile_check=compile_check)

    @_reported("PROVE_INVALID_REQUEST", "Fix the reported option and retry.")
    def prove(
        self,
        source: str | None = None,
        out: str | None = None,
        target: str | None = None,
        design: str | None = None,
        strict_design: bool = False,
        kind: str = "dashboard",
        install: bool = False,
        force: bool = False,
        with_shell: bool = False,
        freerange: bool = False,
        pretext: bool = False,
    ) -> dict[str, Any]:
        """Compile and verify a source, or browser-verify a compiled artifact directory.

        With no source, proves a starter of kind. Writes a proof bundle and returns its report.
        Use install=True only when the user explicitly permits npm ci --ignore-scripts; browser
        proofs need it, with target react-tailwind-tsx or react-tailwind-app. with_shell, freerange
        (needs Bun), and pretext apply to AppBundles. force replaces an earlier proof directory.
        """
        source_path = self._source(source)
        return api.prove(
            source_path,
            out_dir=self._path(out or api.default_proof_dir(source_path)),
            design_path=self._source(design),
            strict_design=strict_design,
            target=target,
            kind=kind,
            install=install,
            force=force,
            with_shell=with_shell,
            freerange=freerange,
            pretext=pretext,
            cwd=self.root,
        )

    @_reported("STUDIO_START_FAILED", "Fix the reported problem and retry.")
    def studio(
        self,
        source: str | None = None,
        design: str | None = None,
        port: int = 0,
        stop: bool = False,
    ) -> dict[str, Any]:
        """Start a live-reloading preview of a source on 127.0.0.1 and return its URL.

        The preview runs in the background and rebuilds when the source or DESIGN.md changes. One
        runs at a time: starting another replaces it, and stop=True ends it. With no source, uses
        the only viewspec.app.json or viewspec.intent.json in the working directory. port=0 picks
        a free port. A failed first build is reported in build_errors while the preview keeps watching.
        """
        if stop:
            self.close()
            return tool_response(True, "Stopped the preview.", metadata=_metadata())
        # A bad request must leave the running preview alone, so resolve everything and bind the new
        # port before stopping it. Only asking for the running preview's own port has to stop it first.
        source_path = self._path(resolve_studio_source(self._source(source), cwd=self.root), must_exist=True)
        design_path = self._source(design)
        if self._preview is not None and port == self._preview.port:
            self.close()
        preview = Studio(source_path, cwd=self.root, design=design_path, port=port)
        self.close()
        preview.start()
        self._preview = preview
        return tool_response(
            True,
            f"Serving a preview at {preview.url}.",
            paths={"source": str(preview.source)},
            next_actions=["Open the URL. Edit the source and the page reloads."],
            metadata=_metadata(),
            data={"url": preview.url, "build_errors": preview.errors},
        )

    def close(self) -> None:
        """Stop the running preview, if any."""
        preview, self._preview = self._preview, None
        if preview is not None:
            preview.close()


def run_mcp_server(
    *,
    cwd: str | Path | None = None,
    allow_outside_cwd: bool = False,
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] | None = None,
) -> None:
    """Serve the ViewSpec tools over stdio until the client disconnects.

    ``log_level`` is passed to FastMCP, which logs to stderr; ``None`` keeps the SDK's default,
    ``INFO``, which logs a line for every request. ``viewspec mcp --json`` passes ``"ERROR"`` so
    that those lines do not mix with its one-line JSON failure report on stderr.
    """
    if not mcp_dependency_available():
        raise MissingMCPDependency(MCP_INSTALL_HINT)
    from mcp.server.fastmcp import FastMCP  # type: ignore[import-not-found]

    tools = ViewSpecTools(cwd, allow_outside_cwd=allow_outside_cwd)
    app = FastMCP("viewspec", instructions=_SERVER_INSTRUCTIONS, log_level=log_level or "INFO")
    for tool in (tools.init, tools.compile, tools.check, tools.prove, tools.studio):
        app.add_tool(tool)

    def stop(signum: int, _frame: FrameType | None) -> None:
        # The SDK's blocking stdin read outlives KeyboardInterrupt, so a signal could never end the
        # server. Clean up here and let the default action end the process.
        tools.close()
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        app.run()
    finally:
        tools.close()


__all__ = [
    "MCP_INSTALL_HINT",
    "MissingMCPDependency",
    "ViewSpecTools",
    "mcp_dependency_available",
    "run_mcp_server",
]
