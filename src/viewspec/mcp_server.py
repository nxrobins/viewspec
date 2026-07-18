"""Optional stdio MCP server for native agent integrations."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from viewspec.app_bundle import compile_app_tool, diff_app_files_tool, init_app_tool, prove_app_tool, validate_app_file_tool
from viewspec.converge_tools import (
    approve_convergence_preview_tool,
    convergence_status_tool,
    reject_convergence_preview_tool,
    start_convergence_session_tool,
    submit_convergence_patch_tool,
)
from viewspec.intent_tools import (
    agent_correction_prompt_file_tool,
    compile_intent_bundle_file_tool,
    diff_intent_bundle_files_tool,
    init_intent_tool,
    validate_intent_bundle_file_tool,
)
from viewspec.intent_patch_tools import (
    apply_intent_patch_file_tool,
    intent_patch_context_tool,
    preview_intent_patch_file_tool,
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
    resolve_local_path,
)
from viewspec.prove import prove_tool
from viewspec.review_cli import end_review as end_review_cli
from viewspec.review_cli import open_review as open_review_cli
from viewspec.review_cli import poll_review as poll_review_cli
from viewspec.review_cli import review_status as review_status_cli


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
            "Write a valid starter AppBundle JSON file for a two-screen internal tool. "
            "Use template='react-app' for the runnable AppBundle V4 golden path, or use the contract template "
            "with resource_binding='unbound_v0' or 'fixture_readonly_v0'."
        )
    )
    def init_app(
        out: str = "viewspec.app.json",
        kind: str = "internal_tool",
        resource_binding: str = "unbound_v0",
        template: str = "contract",
        force: bool = False,
    ) -> dict[str, Any]:
        return init_app_tool(
            out,
            kind=kind,
            resource_binding=resource_binding,
            template=template,
            force=force,
            cwd=root,
            allow_outside_cwd=allow_outside_cwd,
        )

    @app.tool(
        description=(
            "Validate a local AppBundle JSON file. Checks V1 unbound and V2 fixture-readonly binding contracts, "
            "static routes, bounded fixture resources, unknown-field rejection, no-network app fields, and embedded local V1 IntentBundles."
        )
    )
    def validate_app_file(path: str, compile_check: bool = True) -> dict[str, Any]:
        return validate_app_file_tool(
            path,
            compile_check=compile_check,
            cwd=root,
            allow_outside_cwd=allow_outside_cwd,
        )

    @app.tool(
        description=(
            "Diff two local AppBundle JSON files. Reports app route/screen/resource/resource-view/metadata changes "
            "and per-screen diff-intent semantic summaries for changed embedded intents."
        )
    )
    def diff_app_files(left_path: str, right_path: str, compile_check: bool = True) -> dict[str, Any]:
        return diff_app_files_tool(
            left_path,
            right_path,
            compile_check=compile_check,
            cwd=root,
            allow_outside_cwd=allow_outside_cwd,
        )

    @app.tool(
        description=(
            "Compile a local AppBundle JSON file into a Static Shell V0 artifact or a runnable Vite React/Tailwind app. "
            "Use target='react-tailwind-app' for the generated host bridge."
        )
    )
    def compile_app(
        app_path: str,
        out_dir: str = "app-dist",
        design_path: str | None = None,
        strict_design: bool = False,
        force: bool = False,
        target: str = "html-tailwind-app",
    ) -> dict[str, Any]:
        return compile_app_tool(
            app_path,
            out_dir,
            design_path=design_path,
            strict_design=strict_design,
            force=force,
            target=target,
            cwd=root,
            allow_outside_cwd=allow_outside_cwd,
        )

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
            "React Tailwind host proof. Metadata includes proof_identity hashes for the written proof artifacts."
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

    @app.tool(
        description=(
            "Run the local AppBundle proof workflow. Use target='react-tailwind-app' with install=True to generate the exact Vite app, "
            "build it, and prove routing, history, mutation, data rebinding, selectors, and visibility in Chromium."
        )
    )
    def prove_app(
        app_path: str,
        out_dir: str = ".viewspec-app-proof",
        design_path: str | None = None,
        strict_design: bool = False,
        force: bool = False,
        report_out: str | None = None,
        with_shell: bool = False,
        target: str = "html-tailwind",
        install: bool = False,
    ) -> dict[str, Any]:
        return prove_app_tool(
            app_path=app_path,
            out_dir=out_dir,
            design_path=design_path,
            strict_design=strict_design,
            force=force,
            report_out=report_out,
            with_shell=with_shell,
            target=target,
            install=install,
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
            "Export the local ViewSpec agent system prompt, IntentBundle JSON schema, valid starter IntentBundle example, and asset manifest without network calls. "
            "The export also includes AppBundle, IntentPatch, "
            "and Convergence Authoring Task schemas and examples."
        )
    )
    def export_agent_assets(out: str = ".viewspec", force: bool = False, dry_run: bool = False) -> dict[str, Any]:
        return export_agent_assets_tool(out, force=force, dry_run=dry_run, cwd=root, allow_outside_cwd=allow_outside_cwd)

    @app.tool(description="Open or resume a checked local ViewSpec Review session without launching a browser automatically.")
    def open_review(
        source: str,
        design_path: str | None = None,
        target: str | None = None,
        port: int = 4388,
        state_dir: str = ".viewspec-review",
        reopen: bool = False,
        verify: bool = False,
        install: bool = False,
    ) -> dict[str, Any]:
        resolved_source = resolve_local_path(source, cwd=root, allow_outside_cwd=allow_outside_cwd, must_exist=True)
        resolved_design = (
            resolve_local_path(design_path, cwd=root, allow_outside_cwd=allow_outside_cwd, must_exist=True)
            if design_path is not None
            else None
        )
        resolved_state = resolve_local_path(state_dir, cwd=root, allow_outside_cwd=allow_outside_cwd)
        return open_review_cli(
            resolved_source,
            design=resolved_design,
            target=target,
            port=port,
            state_root=resolved_state,
            reopen=reopen,
            no_open=True,
            verify=verify,
            install=install,
        )

    @app.tool(description="Long-poll one active Review session with at-least-once batch acknowledgement semantics.")
    def poll_review(
        source: str,
        ack: str | None = None,
        agent_reply: str | None = None,
        timeout_ms: int = 55_000,
        state_dir: str = ".viewspec-review",
    ) -> dict[str, Any]:
        resolved_source = resolve_local_path(source, cwd=root, allow_outside_cwd=allow_outside_cwd)
        resolved_state = resolve_local_path(state_dir, cwd=root, allow_outside_cwd=allow_outside_cwd)
        return poll_review_cli(
            resolved_source,
            ack=ack,
            agent_reply=agent_reply,
            timeout_ms=timeout_ms,
            state_root=resolved_state,
        )

    @app.tool(description="End an active local ViewSpec Review session with agent attribution.")
    def end_review(source: str, state_dir: str = ".viewspec-review") -> dict[str, Any]:
        resolved_source = resolve_local_path(source, cwd=root, allow_outside_cwd=allow_outside_cwd)
        resolved_state = resolve_local_path(state_dir, cwd=root, allow_outside_cwd=allow_outside_cwd)
        return end_review_cli(resolved_source, state_root=resolved_state)

    @app.tool(description="Get bounded Review status without capability values, source paths, or feedback bodies.")
    def get_review_status(source: str | None = None, state_dir: str = ".viewspec-review") -> dict[str, Any]:
        resolved_source = (
            resolve_local_path(source, cwd=root, allow_outside_cwd=allow_outside_cwd) if source is not None else None
        )
        resolved_state = resolve_local_path(state_dir, cwd=root, allow_outside_cwd=allow_outside_cwd)
        return review_status_cli(resolved_source, state_root=resolved_state)

    @app.tool(
        description=(
            "Validate and compile-check an exact source-bound IntentPatch, return its semantic diff and approval token, "
            "and optionally write the candidate without mutating the source."
        )
    )
    def preview_intent_patch(
        source: str,
        patch: str,
        candidate_out: str | None = None,
        verify: bool = False,
        install: bool = False,
    ) -> dict[str, Any]:
        return preview_intent_patch_file_tool(
            source,
            patch,
            candidate_out=candidate_out,
            verify=verify,
            install=install,
            cwd=root,
            allow_outside_cwd=allow_outside_cwd,
        )

    @app.tool(
        description=(
            "Atomically apply an IntentPatch only when given the exact approval token from the current preview; "
            "write a durable receipt containing the inverse patch."
        )
    )
    def apply_intent_patch(
        source: str,
        patch: str,
        approval_token: str,
        verify: bool = False,
        install: bool = False,
    ) -> dict[str, Any]:
        return apply_intent_patch_file_tool(
            source,
            patch,
            approval_token=approval_token,
            verify=verify,
            install=install,
            cwd=root,
            allow_outside_cwd=allow_outside_cwd,
        )

    @app.tool(
        description=(
            "Convert exactly one validated Review batch or verification repair plan into bounded, source-bound "
            "IntentPatch proposal context. This tool grants no approval and performs no source mutation."
        )
    )
    def build_intent_patch_context(
        review_batch: dict[str, Any] | None = None,
        repair_plan: dict[str, Any] | None = None,
        source_kind: str | None = None,
        base_source_sha256: str | None = None,
    ) -> dict[str, Any]:
        return intent_patch_context_tool(
            review_batch=review_batch,
            repair_plan=repair_plan,
            source_kind=source_kind,
            base_source_sha256=base_source_sha256,
        )

    @app.tool(
        description=(
            "Automatically start a durable, bounded Converge Session from exact Review or verifier context. "
            "This proposal-only tool grants no source-write authority; use it after build_intent_patch_context."
        )
    )
    def start_convergence(
        source: str,
        context: dict[str, Any],
        baseline_result: dict[str, Any] | None = None,
        state_dir: str | None = None,
    ) -> dict[str, Any]:
        return start_convergence_session_tool(
            source,
            context,
            baseline_result=baseline_result,
            state_dir=state_dir,
            cwd=root,
            allow_outside_cwd=allow_outside_cwd,
        )

    @app.tool(
        description=(
            "Submit one IntentPatch using only the active convergence task's legal operations and exact evidence. "
            "The response withholds every source-write token; ask the human to decide in ViewSpec Review."
        )
    )
    def submit_convergence_patch(
        source: str,
        patch: dict[str, Any],
        state_dir: str | None = None,
    ) -> dict[str, Any]:
        return submit_convergence_patch_tool(
            source,
            patch,
            state_dir=state_dir,
            cwd=root,
            allow_outside_cwd=allow_outside_cwd,
        )

    @app.tool(
        description=(
            "Get bounded Converge Session status and the next automatic workflow action. "
            "Approval authority is always withheld from this agent-facing response."
        )
    )
    def get_convergence_status(
        source: str,
        state_dir: str | None = None,
    ) -> dict[str, Any]:
        return convergence_status_tool(
            source,
            state_dir=state_dir,
            cwd=root,
            allow_outside_cwd=allow_outside_cwd,
        )

    @app.tool(
        description=(
            "Apply a convergence preview only when a human operator explicitly supplies its outer approval token. "
            "Never discover, infer, retain, or self-authorize this value; normal human approval happens in Review."
        )
    )
    def approve_convergence(
        source: str,
        approval_token: str,
        state_dir: str | None = None,
    ) -> dict[str, Any]:
        return approve_convergence_preview_tool(
            source,
            approval_token,
            state_dir=state_dir,
            cwd=root,
            allow_outside_cwd=allow_outside_cwd,
        )

    @app.tool(
        description=(
            "Reject the exact pending convergence preview by id without changing source. "
            "Use only after the operator explicitly rejects the proposal."
        )
    )
    def reject_convergence(
        source: str,
        preview_id: str,
        state_dir: str | None = None,
    ) -> dict[str, Any]:
        return reject_convergence_preview_tool(
            source,
            preview_id,
            state_dir=state_dir,
            cwd=root,
            allow_outside_cwd=allow_outside_cwd,
        )

    app.run()


__all__ = [
    "MCP_INSTALL_HINT",
    "MissingMCPDependency",
    "mcp_dependency_available",
    "run_mcp_server",
]
