"""Optional stdio MCP server for native agent integrations."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from viewspec.local_tools import (
    check_artifact_tool,
    compile_html_file_tool,
    diff_html_files_tool,
    init_design_tool,
    lift_html_file_tool,
)


MCP_INSTALL_HINT = 'Install with: python -m pip install "viewspec[agents]"'


class MissingMCPDependency(RuntimeError):
    """Raised when the optional MCP dependency is unavailable."""


def mcp_dependency_available() -> bool:
    return importlib.util.find_spec("mcp") is not None


def run_mcp_server(*, cwd: str | Path | None = None, allow_outside_cwd: bool = False) -> None:
    if not mcp_dependency_available():
        raise MissingMCPDependency(MCP_INSTALL_HINT)
    try:
        from mcp.server.fastmcp import FastMCP  # type: ignore[import-not-found]
    except ImportError as exc:
        raise MissingMCPDependency(MCP_INSTALL_HINT) from exc

    root = Path.cwd() if cwd is None else Path(cwd)
    app = FastMCP("viewspec")

    @app.tool(
        description=(
            "Compile agent-authored local HTML into a sanitized, themed, checked ViewSpec artifact. "
            "Use after creating or editing human-facing HTML."
        )
    )
    def compile_html_file(
        input_path: str,
        out_dir: str,
        design_path: str | None = None,
        title: str | None = None,
        include_lift: bool = False,
    ) -> dict[str, Any]:
        return compile_html_file_tool(
            input_path,
            out_dir,
            design_path=design_path,
            title=title,
            include_lift=include_lift,
            cwd=root,
            allow_outside_cwd=allow_outside_cwd,
        )

    @app.tool(description="Validate a compiled ViewSpec artifact directory and manifest hashes.")
    def check_artifact(artifact_dir: str) -> dict[str, Any]:
        return check_artifact_tool(artifact_dir, cwd=root, allow_outside_cwd=allow_outside_cwd)

    @app.tool(description="Diff two local HTML files using ViewSpec lift_v1 semantic signals.")
    def diff_html_files(left_path: str, right_path: str) -> dict[str, Any]:
        return diff_html_files_tool(left_path, right_path, cwd=root, allow_outside_cwd=allow_outside_cwd)

    @app.tool(description="Lift a local HTML file into ViewSpec semantic signals without compiling it.")
    def lift_html_file(input_path: str, out_path: str | None = None) -> dict[str, Any]:
        return lift_html_file_tool(input_path, out_path, cwd=root, allow_outside_cwd=allow_outside_cwd)

    @app.tool(description="Write a strict starter DESIGN.md file for local ViewSpec theming.")
    def init_design(out: str = "DESIGN.md", force: bool = False) -> dict[str, Any]:
        return init_design_tool(out, force=force, cwd=root, allow_outside_cwd=allow_outside_cwd)

    app.run()


__all__ = [
    "MCP_INSTALL_HINT",
    "MissingMCPDependency",
    "mcp_dependency_available",
    "run_mcp_server",
]
