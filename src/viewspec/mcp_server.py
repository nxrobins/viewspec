"""Optional stdio MCP server for native agent integrations."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from viewspec.intent_tools import (
    agent_correction_prompt_file_tool,
    compile_intent_bundle_file_tool,
    diff_intent_bundle_files_tool,
    init_intent_tool,
    validate_intent_bundle_file_tool,
)
from viewspec.host_verify import verify_host_tool
from viewspec.local_tools import (
    check_artifact_tool,
    check_agent_assets_tool,
    compile_html_file_tool,
    diff_html_files_tool,
    export_agent_assets_tool,
    init_design_tool,
    lift_html_file_tool,
)
from viewspec.prove import prove_tool


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
            "Validate a ViewSpec IntentBundle JSON file. Use for new UI before compiling; "
            "agents should write intent, not DOM."
        )
    )
    def validate_intent_bundle_file(path: str, compile_check: bool = True) -> dict[str, Any]:
        return validate_intent_bundle_file_tool(
            path,
            compile_check=compile_check,
            cwd=root,
            allow_outside_cwd=allow_outside_cwd,
        )

    @app.tool(
        description=(
            "Compile a ViewSpec IntentBundle JSON file into a local compiler artifact. "
            "Use target='html-tailwind' for checked standalone HTML, target='react-tsx' for checked React source, "
            "or target='react-tailwind-tsx' for checked React source with closed Tailwind recipes. "
            "Use for new UI; HTML, CSS, DOM, React, SwiftUI, Flutter, and CompositionIR are compiler outputs."
        )
    )
    def compile_intent_bundle_file(
        input_path: str,
        out_dir: str,
        design_path: str | None = None,
        strict_design: bool = False,
        target: str = "html-tailwind",
    ) -> dict[str, Any]:
        return compile_intent_bundle_file_tool(
            input_path,
            out_dir,
            design_path=design_path,
            strict_design=strict_design,
            target=target,
            cwd=root,
            allow_outside_cwd=allow_outside_cwd,
        )

    @app.tool(description="Generate a deterministic correction prompt for invalid ViewSpec IntentBundle JSON.")
    def agent_correction_prompt_file(path: str) -> dict[str, Any]:
        return agent_correction_prompt_file_tool(path, cwd=root, allow_outside_cwd=allow_outside_cwd)

    @app.tool(description="Diff two ViewSpec IntentBundle JSON files using intent_bundle_v1 semantic signals.")
    def diff_intent_bundle_files(left_path: str, right_path: str, compile_check: bool = True) -> dict[str, Any]:
        return diff_intent_bundle_files_tool(
            left_path,
            right_path,
            compile_check=compile_check,
            cwd=root,
            allow_outside_cwd=allow_outside_cwd,
        )

    @app.tool(
        description=(
            "Write a valid starter ViewSpec IntentBundle JSON file. Use only as a scaffold; "
            "replace sample content with real user intent before compiling."
        )
    )
    def init_intent(out: str = "viewspec.intent.json", kind: str = "dashboard", force: bool = False) -> dict[str, Any]:
        return init_intent_tool(out, kind=kind, force=force, cwd=root, allow_outside_cwd=allow_outside_cwd)

    @app.tool(
        description=(
            "Use only when importing existing HTML; do not use for new UI. "
            "Compile local HTML into a sanitized, themed, checked ViewSpec artifact."
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

    @app.tool(
        description=(
            "Verify a checked react-tailwind-tsx artifact in ViewSpec's bounded React/Vite/Tailwind host. "
            "Use install=True only when the user explicitly permits npm ci --ignore-scripts."
        )
    )
    def verify_host(
        artifact_dir: str | None = None,
        intent_path: str | None = None,
        out_dir: str | None = None,
        design_path: str | None = None,
        strict_design: bool = False,
        target: str = "react-tailwind-tsx",
        install: bool = False,
        report_out: str | None = None,
    ) -> dict[str, Any]:
        return verify_host_tool(
            artifact_dir,
            intent_path=intent_path,
            out_dir=out_dir,
            design_path=design_path,
            strict_design=strict_design,
            target=target,
            install=install,
            report_out=report_out,
            cwd=root,
            allow_outside_cwd=allow_outside_cwd,
        )

    @app.tool(
        description=(
            "Run ViewSpec's first proof workflow: generate or use an IntentBundle, compile through the public path, "
            "check the artifact, write PROOF.md/proof_report.json/support_bundle.json, and optionally run the bounded "
            "React Tailwind host proof."
        )
    )
    def prove(
        intent_path: str | None = None,
        out_dir: str = ".viewspec-proof",
        design_path: str | None = None,
        strict_design: bool = False,
        target: str = "html-tailwind",
        kind: str = "dashboard",
        install: bool = False,
        force: bool = False,
        report_out: str | None = None,
    ) -> dict[str, Any]:
        return prove_tool(
            intent_path=intent_path,
            out_dir=out_dir,
            design_path=design_path,
            strict_design=strict_design,
            target=target,
            kind=kind,
            install=install,
            force=force,
            report_out=report_out,
            cwd=root,
            allow_outside_cwd=allow_outside_cwd,
        )

    @app.tool(description="Verify exported local ViewSpec agent contract assets against the current SDK.")
    def check_agent_assets(asset_dir: str = ".viewspec") -> dict[str, Any]:
        return check_agent_assets_tool(asset_dir, cwd=root, allow_outside_cwd=allow_outside_cwd)

    @app.tool(
        description=(
            "Use only when importing existing HTML; do not use for new UI. "
            "Diff two local HTML files using ViewSpec lift_v1 semantic signals."
        )
    )
    def diff_html_files(left_path: str, right_path: str) -> dict[str, Any]:
        return diff_html_files_tool(left_path, right_path, cwd=root, allow_outside_cwd=allow_outside_cwd)

    @app.tool(
        description=(
            "Use only when importing existing HTML; do not use for new UI. "
            "Lift a local HTML file into ViewSpec semantic signals without compiling it."
        )
    )
    def lift_html_file(input_path: str, out_path: str | None = None) -> dict[str, Any]:
        return lift_html_file_tool(input_path, out_path, cwd=root, allow_outside_cwd=allow_outside_cwd)

    @app.tool(description="Write a strict starter DESIGN.md file for local ViewSpec theming.")
    def init_design(out: str = "DESIGN.md", force: bool = False) -> dict[str, Any]:
        return init_design_tool(out, force=force, cwd=root, allow_outside_cwd=allow_outside_cwd)

    @app.tool(
        description=(
            "Export the local ViewSpec agent system prompt, IntentBundle JSON schema, "
            "valid starter IntentBundle example, and asset manifest without network calls."
        )
    )
    def export_agent_assets(out: str = ".viewspec", force: bool = False, dry_run: bool = False) -> dict[str, Any]:
        return export_agent_assets_tool(out, force=force, dry_run=dry_run, cwd=root, allow_outside_cwd=allow_outside_cwd)

    app.run()


__all__ = [
    "MCP_INSTALL_HINT",
    "MissingMCPDependency",
    "mcp_dependency_available",
    "run_mcp_server",
]
