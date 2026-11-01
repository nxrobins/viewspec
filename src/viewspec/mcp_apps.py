"""Compile an IntentBundle into an MCP Apps ``ui://`` resource and register it on a server.

An MCP App is a self-contained HTML document that a host renders in a sandboxed iframe. The document
here is the ``html-tailwind`` output plus a pinned bridge script, so ``check`` still governs it.
Clicking an action calls the MCP tool named for it in ``action_tools``. An unmapped action becomes a
chat message, or a model-context update for ``select``. A tool result updates the view in place:
``structuredContent.viewspecBindings`` maps binding ids to new text, and
``structuredContent.viewspecRoot`` replaces the compiled root, which is how a result changes the
number of rows. :meth:`MCPAppArtifact.render` builds the second from a new bundle, so every render
still goes through the compiler and ``check``.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from viewspec.aesthetics import AESTHETIC_PROFILE_TOKENS, is_aesthetic_profile_token
from viewspec.compiler import compile as compile_intent
from viewspec.compiler_errors import LocalToolContractError
from viewspec.design_md import DesignSystemContext
from viewspec.emitters.html_tailwind import HtmlTailwindEmitter
from viewspec.intent_tools import wrap_intent_bundle_manifest
from viewspec.local_tools import MCP_APP_TARGET, atomic_write, check_artifact_dir, source_hash
from viewspec.types import ASTBundle, IntentBundle, IRNode, StyleSpec

if TYPE_CHECKING:
    from mcp.types import CallToolResult

MCP_APP_MIME_TYPE = "text/html;profile=mcp-app"
MCP_APP_METADATA_FILE = "mcp_app.json"

_UI_URI_RE = re.compile(r"ui://[A-Za-z0-9._~-]+(?:/[A-Za-z0-9._~%-]+)*/?")
_TOOL_NAME_RE = re.compile(r"[A-Za-z0-9_.-]{1,128}")


@dataclass(frozen=True)
class MCPAppArtifact:
    """A checked MCP Apps document and the metadata that publishes it."""

    uri: str
    html: str
    mime_type: str = MCP_APP_MIME_TYPE
    render_options: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def csp_meta(self) -> dict[str, Any]:
        """The ``_meta`` for the resource: the document loads nothing from any other domain."""
        return {"ui": {"csp": {"connectDomains": [], "resourceDomains": []}}}

    @property
    def tool_meta(self) -> dict[str, Any]:
        """The ``_meta`` for a tool that opens this view, under the current and the legacy key."""
        return {"ui": {"resourceUri": self.uri}, "ui/resourceUri": self.uri}

    @property
    def size_bytes(self) -> int:
        return len(self.html.encode("utf-8"))

    @property
    def root(self) -> str:
        """The compiled ``.vs-root`` element, which a tool result can swap into the open view."""
        return self.html.partition("<body>\n")[2].partition("\n<script>")[0]

    def render(self, bundle: IntentBundle, *, text: str | None = None) -> CallToolResult:
        """Compile ``bundle`` into a result for a tool to return, replacing the open view's root.

        The bundle takes this app's profile and design, and is held to the same checks and ceiling.
        The tool map stays in the document, so a render can only reach tools the app already names.
        ``text`` is all the model sees: the root stays in ``structuredContent``, which it does not read.
        """
        from mcp.types import CallToolResult, TextContent

        root = compile_mcp_app(bundle, uri=self.uri, **self.render_options).root
        summary = TextContent(type="text", text=text or f"Rendered the {bundle.view_spec.id} view.")
        return CallToolResult(content=[summary], structuredContent={"viewspecRoot": root})

    def metadata(self) -> dict[str, Any]:
        """Everything but the document itself, for writing beside ``index.html``."""
        return {
            "uri": self.uri,
            "mime_type": self.mime_type,
            "csp_meta": self.csp_meta,
            "tool_meta": self.tool_meta,
            "size_bytes": self.size_bytes,
        }


def compile_mcp_app(
    bundle: IntentBundle,
    *,
    uri: str,
    profile: str | None = None,
    design: DesignSystemContext | str | os.PathLike[str] | None = None,
    strict_design: bool = False,
    action_tools: Mapping[str, str] | None = None,
) -> MCPAppArtifact:
    """Compile ``bundle`` into a checked MCP Apps document served at ``uri``.

    ``profile`` overrides the bundle's aesthetic profile. ``action_tools`` maps action ids to the
    MCP tools they call. ``design`` is read as documented on :func:`viewspec.compile`.
    """
    source = _with_profile(bundle, profile) if profile is not None else bundle
    ast = compile_intent(source, design, strict_design=strict_design)
    render_options = {"profile": profile, "design": design, "strict_design": strict_design}
    with tempfile.TemporaryDirectory(prefix="viewspec-mcp-app-") as scratch:
        paths = emit_mcp_app(ast, scratch, uri=uri, action_tools=action_tools)
        wrap_intent_bundle_manifest(
            Path(paths["manifest"]),
            source_name=None,
            raw_source_hash=source_hash(json.dumps(source.to_json(), sort_keys=True)),
            design=None,
            command_args=["compile_mcp_app"],
            artifact_path=Path(paths["html"]),
            emitter="html_tailwind",
            target=MCP_APP_TARGET,
        )
        return replace(read_mcp_app(scratch, uri), render_options=render_options)


def emit_mcp_app(
    ast: ASTBundle,
    out_dir: str | Path,
    *,
    uri: str,
    action_tools: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Write the MCP Apps document for ``ast``, refusing a bad ``uri`` or ``action_tools`` entry."""
    if not _UI_URI_RE.fullmatch(uri):
        raise LocalToolContractError("MCP_APP_INVALID_URI", f"{uri!r} is not a ui:// resource URI.")
    tools = dict(action_tools or {})
    actions = _action_ids(ast.result.root.root)
    for action_id, tool in sorted(tools.items()):
        if action_id not in actions:
            known = ", ".join(sorted(actions)) or "none"
            raise LocalToolContractError(
                "MCP_APP_UNKNOWN_ACTION", f"Action {action_id!r} is not in this view. Actions: {known}."
            )
        if not isinstance(tool, str) or not _TOOL_NAME_RE.fullmatch(tool):
            raise LocalToolContractError("MCP_APP_INVALID_TOOL", f"{tool!r} is not a valid MCP tool name.")
    return HtmlTailwindEmitter(mcp_tools=tools).emit(ast, out_dir)


def read_mcp_app(out_dir: str | Path, uri: str) -> MCPAppArtifact:
    """Return the document in ``out_dir`` once ``check`` has passed it."""
    checked = check_artifact_dir(out_dir)
    if not checked["ok"]:
        issue = checked["issues"][0]
        raise LocalToolContractError(issue["code"], issue["message"])
    return MCPAppArtifact(uri=uri, html=(Path(out_dir) / "index.html").read_text(encoding="utf-8"))


def write_mcp_app_metadata(out_dir: str | Path, uri: str) -> Path:
    """Check the document in ``out_dir`` and write its publishing metadata beside it."""
    path = Path(out_dir) / MCP_APP_METADATA_FILE
    atomic_write(path, json.dumps(read_mcp_app(out_dir, uri).metadata(), indent=2, sort_keys=True))
    return path


def default_mcp_app_uri(bundle: IntentBundle) -> str:
    return f"ui://viewspec/{bundle.view_spec.id}"


def register_mcp_app(server: Any, app: MCPAppArtifact, *, name: str | None = None) -> Callable[..., Any]:
    """Publish ``app`` on a FastMCP ``server`` and return a decorator for its tools.

    The decorator takes FastMCP's ``tool()`` options plus ``visibility``. Decorate the tool that opens
    the view with ``tool()``, and a tool only the view may call with ``tool(visibility=["app"])``.
    """

    def read_view() -> str:
        return app.html

    server.resource(app.uri, name=name or app.uri, mime_type=app.mime_type, meta=app.csp_meta)(read_view)

    def tool(*, visibility: Sequence[str] | None = None, **options: Any) -> Callable[..., Any]:
        meta = app.tool_meta
        if visibility is not None:
            meta["ui"]["visibility"] = list(visibility)
        return server.tool(meta=meta, **options)

    return tool


def _with_profile(bundle: IntentBundle, profile: str) -> IntentBundle:
    if profile not in AESTHETIC_PROFILE_TOKENS:
        allowed = ", ".join(AESTHETIC_PROFILE_TOKENS)
        raise LocalToolContractError("MCP_APP_INVALID_PROFILE", f"profile must be one of: {allowed}.")
    view_spec = bundle.view_spec
    styles = [
        style
        for style in view_spec.styles
        if not is_aesthetic_profile_token(style.token) and style.id != "aesthetic_profile"
    ]
    styles.append(StyleSpec(id="aesthetic_profile", target=f"view:{view_spec.id}", token=profile))
    return replace(bundle, view_spec=replace(view_spec, styles=styles))


def _action_ids(node: IRNode) -> set[str]:
    found = {node.props["action_id"]} if node.primitive == "button" and node.props.get("action_id") else set()
    for child in node.children:
        found |= _action_ids(child)
    return found
