"""Local intent/artifact tools. Facade re-exporting the split submodules."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from viewspec.raw_html import compile_html
from viewspec.raw_html import diff_html
from viewspec.raw_html import lift_html
from viewspec.raw_html import write_html_compile_result
import json
from viewspec.local_tools_constants import (
    ABSOLUTE_PATH_ARG_RE,
    ACTION_TARGET_REF_RE,
    ACTIVE_OR_AUTOFETCH_TAGS,
    ACTIVE_STRUCTURAL_TAGS,
    CANONICAL_CONTENT_REF_RE,
    DIAGNOSTIC_SEVERITIES,
    EMITTER_ARTIFACT_FILES,
    EXPECTED_MANIFEST_ENVELOPES,
    EXTERNAL_REF_POLICIES,
    HASH_RE,
    INTENT_BUNDLE_POLICY_VERSION,
    KNOWN_EMITTERS,
    MCP_RESERVED_RESULT_KEYS,
    MCP_RESULT_SCHEMA_VERSION,
    REACT_TSX_ACTION_REQUIRED_MARKERS,
    REACT_TSX_FORBIDDEN_SURFACES,
    REACT_TSX_REQUIRED_MARKERS,
    REACT_TSX_REQUIRED_MARKERS_BY_EMITTER,
    REMOTE_AUTOFETCH_ATTRS,
    REMOTE_HREF_AUTOFETCH_TAGS,
    SAFE_ID_RE,
    SEMANTIC_ACTION_KEYS,
    SEMANTIC_DIGEST_KEYS,
    SEMANTIC_DIGEST_MAX_PROJECTION_BYTES,
    SEMANTIC_DIGEST_VERSION,
    SEMANTIC_NODE_KEYS,
    SEMANTIC_PROJECTION_KEYS,
    STARTER_DESIGN,
    STATEFUL_COLLECTION_ACTION_KINDS,
    TEXT_PROP_PRIMITIVES,
    VIEWSPEC_INTENT_REF_RE,
    VOID_HTML_TAGS,
)
from viewspec.local_tools_response import (
    LocalToolError,
    exception_response,
    tool_error_response,
    tool_response,
)
from viewspec.local_tools_io import (
    _is_relative_to,
    _load_optional_design,
    atomic_write,
    ensure_no_input_overwrite,
    looks_absolute_path_arg,
    path_policy_metadata,
    resolve_cwd,
    resolve_local_path,
)
from viewspec.local_tools_hash import (
    bytes_hash,
    file_hash,
    source_hash,
)
from viewspec.local_tools_validators import (
    _ArtifactDomProbe,
    _AutofetchSurfaceProbe,
    _SemanticHtmlParentParser,
    _SemanticHtmlProjectionParser,
    _assert_semantic_projection_size,
    _collapse_artifact_text,
    _compare_json_attr,
    _compare_json_list_attr,
    _contains_remote_http_reference,
    _contains_semantic_digest_key,
    _diagnostic_codes,
    _html_semantic_parent_map,
    _is_remote_http_url,
    _is_string_list,
    _manifest_has_action_nodes,
    _normalize_visible_text,
    _semantic_accessibility_label,
    _semantic_action_from_attrs,
    _semantic_html_source_nodes,
    _semantic_manifest_node,
    _semantic_manifest_projection,
    _semantic_projection_uses_stateful_collections,
    _semantic_source_projection,
    _semantic_tag_for_manifest_node,
    _semantic_tsx_action,
    _semantic_tsx_attrs,
    _semantic_tsx_inner_text,
    _semantic_tsx_source_nodes,
    _semantic_visible_text_from_props,
    _stable_semantic_json,
    _strip_tsx_literals_and_comments,
    _tailwind_allowed_class_tokens,
    _tailwind_source_class_tokens,
    _tsx_render_block_lines,
    _tsx_semantic_parent_map,
    _tsx_text_marker,
    _validate_aesthetic_layout_manifest_node,
    _validate_aesthetic_profile_manifest_node,
    _validate_image_like_attrs,
    _validate_intent_dom_links,
    _validate_intent_manifest_node,
    _validate_intent_semantic_attrs,
    _validate_intent_semantic_digest,
    _validate_intent_visible_text,
    _validate_manifest_artifact_file,
    _validate_manifest_design,
    _validate_manifest_diagnostics,
    _validate_manifest_dom_links,
    _validate_manifest_envelope,
    _validate_manifest_external_refs,
    _validate_manifest_nodes,
    _validate_no_autofetch_surfaces,
    _validate_no_tailwind_scope_leak,
    _validate_raw_html_dom_links,
    _validate_raw_html_dom_node,
    _validate_raw_html_manifest_node,
    _validate_react_tailwind_class_inventory,
    _validate_react_tailwind_limits,
    _validate_react_tailwind_manifest,
    _validate_react_tailwind_semantic_markers,
    _validate_react_tailwind_static_source,
    _validate_react_tailwind_tsx_artifact,
    _validate_react_tsx_manifest_links,
    _validate_react_tsx_source,
    _validate_semantic_digest_shape,
    _validate_semantic_projection_shape,
    _validate_stateful_collection_artifact,
    _validate_tailwind_generic_fallback,
    build_intent_semantic_digest,
    check_artifact_dir,
)

def init_design_file(path: str | Path = "DESIGN.md", *, force: bool = False) -> Path:
    output = Path(path)
    if output.exists() and not force:
        raise ValueError(f"{output} already exists; pass --force to overwrite")
    atomic_write(output, STARTER_DESIGN)
    return output

def compile_html_file_tool(
    input_path: str | Path,
    out_dir: str | Path,
    *,
    design_path: str | Path | None = None,
    title: str | None = None,
    include_lift: bool = False,
    cwd: str | Path | None = None,
    allow_outside_cwd: bool = False,
) -> dict[str, Any]:
    root: Path | None = None
    try:
        root = resolve_cwd(cwd)
        source = resolve_local_path(input_path, cwd=root, allow_outside_cwd=allow_outside_cwd, must_exist=True)
        output = resolve_local_path(out_dir, cwd=root, allow_outside_cwd=allow_outside_cwd)
        ensure_no_input_overwrite(source, output, ("index.html", "provenance_manifest.json", "diagnostics.json", "lift.json"))
        design = _load_optional_design(design_path, cwd=root, allow_outside_cwd=allow_outside_cwd)
        html = source.read_text(encoding="utf-8")
        result = compile_html(
            html,
            design=design,
            title=title,
            source_name=source.name,
            command_args=["viewspec", "mcp", "compile_html_file", source.name, "--out", "<out>"],
        )
        paths = write_html_compile_result(result, output, include_lift=include_lift)
        checked = check_artifact_dir(output)
        external_refs = result.manifest.get("external_refs", [])
        if not checked["ok"]:
            return tool_error_response(
                "CHECK_FAILED",
                "Compiled artifact failed viewspec check.",
                "Inspect the check errors and re-run compile after fixing the source HTML or DESIGN.md.",
                diagnostics=result.diagnostics,
                external_refs=external_refs,
                paths=paths,
                errors=[{"code": "CHECK_FAILED", "message": item, "fix": "Re-run viewspec compile after fixing the reported artifact issue."} for item in checked["errors"]],
                metadata=path_policy_metadata(root, allow_outside_cwd),
            )
        return tool_response(
            True,
            "Compiled and checked local HTML artifact.",
            diagnostics=result.diagnostics,
            external_refs=external_refs,
            paths=paths,
            next_actions=["Open the compiled index.html for review.", "Use diff_html_files before summarizing revisions."],
            metadata=path_policy_metadata(root, allow_outside_cwd),
        )
    except Exception as exc:
        return exception_response(
            exc,
            "COMPILE_FAILED",
            "Fix the HTML, DESIGN.md, or path issue and retry compile_html_file.",
            metadata=path_policy_metadata(root, allow_outside_cwd),
        )

def check_artifact_tool(
    artifact_dir: str | Path,
    *,
    cwd: str | Path | None = None,
    allow_outside_cwd: bool = False,
) -> dict[str, Any]:
    root: Path | None = None
    try:
        root = resolve_cwd(cwd)
        artifact = resolve_local_path(artifact_dir, cwd=root, allow_outside_cwd=allow_outside_cwd)
        result = check_artifact_dir(artifact)
        errors = [
            {"code": "CHECK_FAILED", "message": item, "fix": "Re-run viewspec compile from the source HTML, or fix the tampered artifact."}
            for item in result["errors"]
        ]
        return tool_response(
            result["ok"],
            "Artifact check passed." if result["ok"] else "Artifact check failed.",
            paths={"artifact_dir": str(artifact)},
            errors=errors,
            next_actions=[] if result["ok"] else ["Fix the reported issue and re-run viewspec check."],
            metadata={
                **path_policy_metadata(root, allow_outside_cwd),
                "warnings": result["warnings"],
                "manifest_summary": result.get("manifest_summary"),
            },
        )
    except Exception as exc:
        return exception_response(
            exc,
            "CHECK_FAILED",
            "Fix the artifact directory path and retry check_artifact.",
            metadata=path_policy_metadata(root, allow_outside_cwd),
        )

def diff_html_files_tool(
    left_path: str | Path,
    right_path: str | Path,
    *,
    cwd: str | Path | None = None,
    allow_outside_cwd: bool = False,
) -> dict[str, Any]:
    root: Path | None = None
    try:
        root = resolve_cwd(cwd)
        left = resolve_local_path(left_path, cwd=root, allow_outside_cwd=allow_outside_cwd, must_exist=True)
        right = resolve_local_path(right_path, cwd=root, allow_outside_cwd=allow_outside_cwd, must_exist=True)
        result = diff_html(
            left.read_text(encoding="utf-8"),
            right.read_text(encoding="utf-8"),
            left_name=left.name,
            right_name=right.name,
        )
        return tool_response(
            True,
            "Computed local semantic HTML diff.",
            diagnostics=result.diagnostics,
            paths={"left": str(left), "right": str(right)},
            data={"diff": result.to_json()},
            next_actions=["Use diagnostics as unsupported-content notes, not silent success."],
            metadata=path_policy_metadata(root, allow_outside_cwd),
        )
    except Exception as exc:
        return exception_response(
            exc,
            "DIFF_FAILED",
            "Fix the compared HTML paths and retry diff_html_files.",
            metadata=path_policy_metadata(root, allow_outside_cwd),
        )

def lift_html_file_tool(
    input_path: str | Path,
    out_path: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    allow_outside_cwd: bool = False,
) -> dict[str, Any]:
    root: Path | None = None
    try:
        root = resolve_cwd(cwd)
        source = resolve_local_path(input_path, cwd=root, allow_outside_cwd=allow_outside_cwd, must_exist=True)
        output = resolve_local_path(out_path, cwd=root, allow_outside_cwd=allow_outside_cwd) if out_path is not None else None
        if output is not None and source == output:
            raise LocalToolError("INVALID_PATH", "Refusing to overwrite input file.", "Choose a distinct output path for lift_json.")
        result = lift_html(source.read_text(encoding="utf-8"), source_name=source.name)
        paths: dict[str, str] = {"input": str(source)}
        if output is not None:
            atomic_write(output, json.dumps(result.to_json(), indent=2, sort_keys=True))
            paths["lift"] = str(output)
        return tool_response(
            True,
            "Lifted local HTML into semantic signals.",
            diagnostics=result.diagnostics,
            paths=paths,
            data={"lift": result.to_json()},
            metadata=path_policy_metadata(root, allow_outside_cwd),
        )
    except Exception as exc:
        return exception_response(
            exc,
            "COMPILE_FAILED",
            "Fix the HTML path and retry lift_html_file.",
            metadata=path_policy_metadata(root, allow_outside_cwd),
        )

def init_design_tool(
    out: str | Path = "DESIGN.md",
    *,
    force: bool = False,
    cwd: str | Path | None = None,
    allow_outside_cwd: bool = False,
) -> dict[str, Any]:
    root: Path | None = None
    try:
        root = resolve_cwd(cwd)
        output = resolve_local_path(out, cwd=root, allow_outside_cwd=allow_outside_cwd)
        init_design_file(output, force=force)
        return tool_response(
            True,
            "Wrote starter DESIGN.md.",
            paths={"design": str(output)},
            next_actions=["Compile with --design DESIGN.md."],
            metadata=path_policy_metadata(root, allow_outside_cwd),
        )
    except Exception as exc:
        return exception_response(
            exc,
            "IO_ERROR",
            "Choose a writable path, or pass force=True to replace an existing DESIGN.md.",
            metadata=path_policy_metadata(root, allow_outside_cwd),
        )

def export_agent_assets_tool(
    out: str | Path = ".viewspec",
    *,
    force: bool = False,
    dry_run: bool = False,
    cwd: str | Path | None = None,
    allow_outside_cwd: bool = False,
) -> dict[str, Any]:
    from viewspec.agent_assets import export_agent_assets

    root: Path | None = None
    try:
        root = resolve_cwd(cwd)
        output = resolve_local_path(out, cwd=root, allow_outside_cwd=allow_outside_cwd)
        result = export_agent_assets(output, force=force, dry_run=dry_run)
        paths = {"out": str(output)}
        for item in result["files"]:
            filename = item["path"]
            if filename == "agent-assets.json":
                paths["manifest"] = str(output / filename)
            elif filename == "agent-system-prompt.txt":
                paths["prompt"] = str(output / filename)
            elif filename == "agent-intent-bundle.schema.json":
                paths["schema"] = str(output / filename)
            elif filename == "agent-intent-example.dashboard.json":
                paths["example"] = str(output / filename)
            elif filename == "agent-app-bundle.schema.json":
                paths["app_schema"] = str(output / filename)
            elif filename == "agent-app-example.internal-tool.json":
                paths["app_example"] = str(output / filename)
            elif filename == "intent-patch.schema.json":
                paths["patch_schema"] = str(output / filename)
            elif filename == "intent-patch-example.dashboard.json":
                paths["patch_example"] = str(output / filename)
        changed = [item for item in result["files"] if item["action"] != "unchanged"]
        return tool_response(
            True,
            "Exported local agent contract assets." if not dry_run else "Planned local agent contract asset export.",
            paths=paths,
            data={"assets": result},
            next_actions=[
                "Verify .viewspec/agent-assets.json when reusing exported assets.",
                "Point schema-aware editors or agents at .viewspec/agent-intent-bundle.schema.json.",
                "Point app-aware agents at .viewspec/agent-app-bundle.schema.json for multi-screen AppBundle V1/V2/V3.",
                "Use .viewspec/agent-system-prompt.txt as the local ViewSpec agent contract prompt.",
                "Use .viewspec/agent-intent-example.dashboard.json as a valid wire-shape example.",
                "Use .viewspec/agent-app-example.internal-tool.json as a valid AppBundle wire-shape example.",
                "Use .viewspec/intent-patch.schema.json and its example for bounded source revisions.",
            ],
            metadata={**path_policy_metadata(root, allow_outside_cwd), "dry_run": dry_run, "changes": len(changed)},
        )
    except Exception as exc:
        return exception_response(
            exc,
            "IO_ERROR",
            "Choose a writable asset output directory, or pass force=True to replace existing generated assets.",
            metadata=path_policy_metadata(root, allow_outside_cwd),
        )

def check_agent_assets_tool(
    asset_dir: str | Path = ".viewspec",
    *,
    cwd: str | Path | None = None,
    allow_outside_cwd: bool = False,
) -> dict[str, Any]:
    from viewspec.agent_assets import check_agent_assets

    root: Path | None = None
    try:
        root = resolve_cwd(cwd)
        asset_path = resolve_local_path(asset_dir, cwd=root, allow_outside_cwd=allow_outside_cwd, must_exist=True)
        result = check_agent_assets(asset_path)
        return tool_response(
            bool(result["ok"]),
            "Agent contract assets match the current SDK." if result["ok"] else "Agent contract assets failed verification.",
            paths={"assets": str(asset_path), "manifest": str(asset_path / "agent-assets.json")},
            errors=[
                {
                    "code": "AGENT_ASSET_CHECK_FAILED",
                    "message": message,
                    "fix": "Re-run viewspec export-agent-assets --out .viewspec --force.",
                }
                for message in result["errors"]
            ],
            data={"assets": result},
            metadata=path_policy_metadata(root, allow_outside_cwd),
        )
    except Exception as exc:
        return exception_response(
            exc,
            "IO_ERROR",
            "Choose a readable agent asset directory and retry check_agent_assets.",
            metadata=path_policy_metadata(root, allow_outside_cwd),
        )

__all__ = [
    "ABSOLUTE_PATH_ARG_RE",
    "ACTION_TARGET_REF_RE",
    "ACTIVE_OR_AUTOFETCH_TAGS",
    "ACTIVE_STRUCTURAL_TAGS",
    "CANONICAL_CONTENT_REF_RE",
    "DIAGNOSTIC_SEVERITIES",
    "EMITTER_ARTIFACT_FILES",
    "EXPECTED_MANIFEST_ENVELOPES",
    "EXTERNAL_REF_POLICIES",
    "HASH_RE",
    "INTENT_BUNDLE_POLICY_VERSION",
    "KNOWN_EMITTERS",
    "LocalToolError",
    "MCP_RESERVED_RESULT_KEYS",
    "MCP_RESULT_SCHEMA_VERSION",
    "REACT_TSX_ACTION_REQUIRED_MARKERS",
    "REACT_TSX_FORBIDDEN_SURFACES",
    "REACT_TSX_REQUIRED_MARKERS",
    "REACT_TSX_REQUIRED_MARKERS_BY_EMITTER",
    "REMOTE_AUTOFETCH_ATTRS",
    "REMOTE_HREF_AUTOFETCH_TAGS",
    "SAFE_ID_RE",
    "SEMANTIC_ACTION_KEYS",
    "SEMANTIC_DIGEST_KEYS",
    "SEMANTIC_DIGEST_MAX_PROJECTION_BYTES",
    "SEMANTIC_DIGEST_VERSION",
    "SEMANTIC_NODE_KEYS",
    "SEMANTIC_PROJECTION_KEYS",
    "STARTER_DESIGN",
    "STATEFUL_COLLECTION_ACTION_KINDS",
    "TEXT_PROP_PRIMITIVES",
    "VIEWSPEC_INTENT_REF_RE",
    "VOID_HTML_TAGS",
    "_ArtifactDomProbe",
    "_AutofetchSurfaceProbe",
    "_SemanticHtmlParentParser",
    "_SemanticHtmlProjectionParser",
    "_assert_semantic_projection_size",
    "_collapse_artifact_text",
    "_compare_json_attr",
    "_compare_json_list_attr",
    "_contains_remote_http_reference",
    "_contains_semantic_digest_key",
    "_diagnostic_codes",
    "_html_semantic_parent_map",
    "_is_relative_to",
    "_is_remote_http_url",
    "_is_string_list",
    "_load_optional_design",
    "_manifest_has_action_nodes",
    "_normalize_visible_text",
    "_semantic_accessibility_label",
    "_semantic_action_from_attrs",
    "_semantic_html_source_nodes",
    "_semantic_manifest_node",
    "_semantic_manifest_projection",
    "_semantic_projection_uses_stateful_collections",
    "_semantic_source_projection",
    "_semantic_tag_for_manifest_node",
    "_semantic_tsx_action",
    "_semantic_tsx_attrs",
    "_semantic_tsx_inner_text",
    "_semantic_tsx_source_nodes",
    "_semantic_visible_text_from_props",
    "_stable_semantic_json",
    "_strip_tsx_literals_and_comments",
    "_tailwind_allowed_class_tokens",
    "_tailwind_source_class_tokens",
    "_tsx_render_block_lines",
    "_tsx_semantic_parent_map",
    "_tsx_text_marker",
    "_validate_aesthetic_layout_manifest_node",
    "_validate_aesthetic_profile_manifest_node",
    "_validate_image_like_attrs",
    "_validate_intent_dom_links",
    "_validate_intent_manifest_node",
    "_validate_intent_semantic_attrs",
    "_validate_intent_semantic_digest",
    "_validate_intent_visible_text",
    "_validate_manifest_artifact_file",
    "_validate_manifest_design",
    "_validate_manifest_diagnostics",
    "_validate_manifest_dom_links",
    "_validate_manifest_envelope",
    "_validate_manifest_external_refs",
    "_validate_manifest_nodes",
    "_validate_no_autofetch_surfaces",
    "_validate_no_tailwind_scope_leak",
    "_validate_raw_html_dom_links",
    "_validate_raw_html_dom_node",
    "_validate_raw_html_manifest_node",
    "_validate_react_tailwind_class_inventory",
    "_validate_react_tailwind_limits",
    "_validate_react_tailwind_manifest",
    "_validate_react_tailwind_semantic_markers",
    "_validate_react_tailwind_static_source",
    "_validate_react_tailwind_tsx_artifact",
    "_validate_react_tsx_manifest_links",
    "_validate_react_tsx_source",
    "_validate_semantic_digest_shape",
    "_validate_semantic_projection_shape",
    "_validate_stateful_collection_artifact",
    "_validate_tailwind_generic_fallback",
    "atomic_write",
    "build_intent_semantic_digest",
    "bytes_hash",
    "check_agent_assets_tool",
    "check_artifact_dir",
    "check_artifact_tool",
    "compile_html_file_tool",
    "diff_html_files_tool",
    "ensure_no_input_overwrite",
    "exception_response",
    "export_agent_assets_tool",
    "file_hash",
    "init_design_file",
    "init_design_tool",
    "lift_html_file_tool",
    "looks_absolute_path_arg",
    "path_policy_metadata",
    "resolve_cwd",
    "resolve_local_path",
    "source_hash",
    "tool_error_response",
    "tool_response",
]
