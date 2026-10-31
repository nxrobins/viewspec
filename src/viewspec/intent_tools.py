"""Shared IntentBundle tools for agent-native UI workflows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from viewspec._canonical import canonical_json_utf8
from viewspec._version import __version__
from viewspec.agent import (
    SUPPORTED_AGENT_MOTIFS,
    AgentValidationIssue,
    AgentValidationResult,
    agent_correction_prompt,
    agent_repair_checklist,
    validate_agent_intent_bundle,
)
from viewspec.compiler import compile
from viewspec.design_md import DesignSystemContext, load_design_system
from viewspec.emitters.html_tailwind import HtmlTailwindEmitter
from viewspec.emitters.react_tailwind_tsx import (
    TAILWIND_RECIPE_REGISTRY_VERSION,
    ReactTailwindTsxEmitter,
    tailwind_recipe_registry_digest,
)
from viewspec.emitters.react_tsx import ReactTsxEmitter
from viewspec.local_tools import (
    INTENT_BUNDLE_POLICY_VERSION,
    atomic_write,
    build_intent_semantic_digest,
    check_artifact_dir,
    check_issue_entry,
    ensure_no_input_overwrite,
    exception_response,
    file_hash,
    path_policy_metadata,
    resolve_cwd,
    resolve_local_path,
    source_hash,
    tool_error_response,
    tool_response,
)
from viewspec.manifest_summary import summarize_intent_manifest
from viewspec.raw_html import MANIFEST_SCHEMA_VERSION
from viewspec.sdk.builder import ViewSpecBuilder
from viewspec.types import INTENT_BUNDLE_SCHEMA_VERSION, IntentBundle

BUNDLE_POLICY_VERSION = INTENT_BUNDLE_POLICY_VERSION
INTENT_RESULT_SCHEMA_VERSION = 1
INTENT_COMPILE_TARGETS = ("html-tailwind", "react-tsx", "react-tailwind-tsx")
STARTER_INTENT_KINDS = tuple(SUPPORTED_AGENT_MOTIFS)


def starter_intent_bundle(kind: str = "dashboard") -> IntentBundle:
    if kind not in STARTER_INTENT_KINDS:
        raise ValueError(f"Unknown starter intent kind: {kind}")
    builder = ViewSpecBuilder(
        f"starter_{kind}",
        root_attrs={"title": f"Starter {kind.replace('_', ' ').title()}"},
    )
    if kind == "table":
        table = builder.add_table("items", region="main", group_id="rows")
        table.add_row(label="Alpha", value="Ready", id="alpha")
        table.add_row(label="Beta", value="Next", id="beta")
    elif kind == "dashboard":
        dashboard = builder.add_dashboard("metrics", region="main", group_id="cards")
        dashboard.add_card(label="Revenue", value="$12.4K", id="revenue")
        dashboard.add_card(label="Users", value="1,284", id="users")
        dashboard.add_card(label="Weekly trend", value="Revenue +12%", id="weekly_trend")
        dashboard.add_card(
            label="Priority",
            value="Activation -4%",
            id="priority",
            value_present_as="badge",
        )
    elif kind == "outline":
        outline = builder.add_outline("plan", region="main", group_id="steps")
        outline.add_branch(label="Define intent", id="define")
        outline.add_branch(label="Compile artifact", id="compile")
    elif kind == "comparison":
        comparison = builder.add_comparison("options", region="main", group_id="choices")
        comparison.add_item(label="Standard", value="Core workflow", id="standard")
        comparison.add_item(label="Pro", value="Advanced workflow", id="pro")
    elif kind == "list":
        items = builder.add_list("next_steps", region="main", group_id="steps")
        items.add_item(label="Capture user intent", description="Describe the UI without writing DOM.", id="intent")
        items.add_item(
            label="Compile artifact", description="Let ViewSpec produce checked renderer output.", id="compile"
        )
    elif kind == "form":
        form = builder.add_form("contact", region="main", group_id="fields")
        form.add_field(label="Name", value="", id="name")
        form.add_field(label="Email", value="", id="email")
        builder.add_action(
            "submit_contact",
            "submit",
            "Submit",
            target_region="main",
            target_ref="motif:contact",
            payload_bindings=["name_value", "email_value"],
        )
    elif kind == "detail":
        detail = builder.add_detail("profile", region="main", group_id="fields")
        detail.add_field(label="Owner", value="Ada Lovelace", id="owner")
        detail.add_field(label="Status", value="Ready", id="status")
        detail.add_field(label="Next step", value="Compile the checked artifact", id="next_step")
    elif kind == "empty_state":
        builder.add_empty_state(
            "no_results",
            title="No results yet",
            description="Adjust filters or create the first item.",
            region="main",
            group_id="message",
        )
    elif kind == "loading_state":
        builder.add_loading_state(
            "loading_results",
            title="Loading results",
            description="The collection is being prepared.",
            region="main",
            group_id="message",
        )
    elif kind == "error_state":
        builder.add_error_state(
            "collection_error",
            title="Unable to load results",
            description="Retry after checking the source data.",
            region="main",
            group_id="message",
        )
    elif kind == "hero":
        builder.add_hero(
            "intro",
            eyebrow="Agent-native UI",
            title="Describe intent, not DOM",
            description="ViewSpec compiles semantic UI intent into checked renderer artifacts.",
            region="main",
            group_id="message",
        )
    return builder.build_bundle()


def starter_intent_payload(kind: str = "dashboard") -> dict[str, Any]:
    """Starter IntentBundle JSON payload carrying the self-describing schema_version field."""
    return {"schema_version": INTENT_BUNDLE_SCHEMA_VERSION, **starter_intent_bundle(kind).to_json()}


def init_intent_file(
    path: str | Path = "viewspec.intent.json", *, kind: str = "dashboard", force: bool = False
) -> Path:
    output = Path(path)
    if output.exists() and not force:
        raise ValueError(f"{output} already exists; pass --force to overwrite")
    payload = starter_intent_payload(kind)
    atomic_write(output, json.dumps(payload, indent=2, sort_keys=True))
    return output


def validate_intent_text(text: str, *, compile_check: bool = True) -> dict[str, Any]:
    result = validate_agent_intent_bundle(text, require_reference_compiler_support=compile_check)
    return intent_validation_payload(result, compile_check=compile_check)


def validate_intent_file(path: str | Path, *, compile_check: bool = True) -> dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8")
    return validate_intent_text(text, compile_check=compile_check)


def intent_validation_payload(result: AgentValidationResult, *, compile_check: bool) -> dict[str, Any]:
    if not compile_check:
        compile_status = "skipped"
    elif result.valid:
        compile_status = "passed"
    else:
        compile_status = "failed"
    return {
        "schema_version": INTENT_RESULT_SCHEMA_VERSION,
        "ok": result.valid,
        "compile_check": compile_status,
        "issues": [issue.to_json() for issue in result.issues],
        "repair_checklist": [] if result.valid else agent_repair_checklist(result),
        "correction_prompt": None if result.valid else agent_correction_prompt(result),
    }


def intent_error_payload(
    code: str,
    message: str,
    suggestion: str,
    *,
    compile_check: bool,
    path: str = "$",
) -> dict[str, Any]:
    issue = AgentValidationIssue("error", code, path, message, suggestion)
    return intent_validation_payload(
        AgentValidationResult(valid=False, bundle=None, issues=[issue]),
        compile_check=compile_check,
    )


def _apply_ir_props_overlay(root_node: Any, overlay: dict[str, dict[str, Any]]) -> list[str]:
    """Merge bounded props onto IR nodes by node id, between compile() and emit.

    The overlay is a closed data structure used by AppBundle compilation. Only visibility,
    bounded state-text, resource identity markers, and the compiler-owned semantic embedding
    context are permitted, so this seam
    cannot become a generic style/content side channel.
    Returns the overlay node ids that did not resolve to an IR node (fail-closed at the caller).
    """
    allowed_keys = {
        "record_id",
        "resource_field",
        "resource_id",
        "resource_view_id",
        "semantic_context",
        "state_text_id",
        "state_text_initial",
        "visibility_rule_id",
        "visibility_hidden_initial",
    }
    for node_id, props in overlay.items():
        unexpected = set(props) - allowed_keys
        if unexpected:
            raise ValueError(f"ir_props_overlay contains unsupported keys {sorted(unexpected)} for {node_id}.")
        semantic_context = props.get("semantic_context")
        if semantic_context is not None and semantic_context != "embedded_screen":
            raise ValueError(
                f"ir_props_overlay semantic_context must be 'embedded_screen'; got {semantic_context!r} for {node_id}."
            )
        for key in ("record_id", "resource_field", "resource_id", "resource_view_id"):
            if key in props and (not isinstance(props[key], str) or not props[key]):
                raise ValueError(f"ir_props_overlay {key} must be a non-empty string for {node_id}.")
        state_text_id = props.get("state_text_id")
        if state_text_id is not None and (not isinstance(state_text_id, str) or not state_text_id):
            raise ValueError(f"ir_props_overlay state_text_id must be a non-empty string for {node_id}.")
        if "state_text_initial" in props and not isinstance(props.get("state_text_initial"), str):
            raise ValueError(f"ir_props_overlay state_text_initial must be a string for {node_id}.")
    remaining = dict(overlay)
    stack = [root_node]
    while stack and remaining:
        node = stack.pop()
        props = remaining.pop(node.id, None)
        if props is not None:
            node.props.update(props)
        stack.extend(getattr(node, "children", []) or [])
    return sorted(remaining)


def compile_intent_bundle_file_tool(
    input_path: str | Path,
    out_dir: str | Path,
    *,
    design_path: str | Path | None = None,
    strict_design: bool = False,
    target: str = "html-tailwind",
    cwd: str | Path | None = None,
    allow_outside_cwd: bool = False,
    ir_props_overlay: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    root: Path | None = None
    try:
        root = resolve_cwd(cwd)
        if target not in INTENT_COMPILE_TARGETS:
            return tool_error_response(
                "COMPILE_FAILED",
                f"Unsupported IntentBundle compile target: {target}",
                "Use target='html-tailwind', target='react-tsx', or target='react-tailwind-tsx'.",
                metadata={
                    "cwd": str(root),
                    "allow_outside_cwd": allow_outside_cwd,
                    "sdk_version": __version__,
                    "network_calls": "none",
                    "target": target,
                },
            )
        source = resolve_local_path(input_path, cwd=root, allow_outside_cwd=allow_outside_cwd, must_exist=True)
        output = resolve_local_path(out_dir, cwd=root, allow_outside_cwd=allow_outside_cwd)
        if target in {"react-tsx", "react-tailwind-tsx"}:
            ensure_no_input_overwrite(
                source, output, ("ViewSpecView.tsx", "provenance_manifest.json", "diagnostics.json")
            )
        else:
            ensure_no_input_overwrite(source, output, ("index.html", "provenance_manifest.json", "diagnostics.json"))
        text = source.read_text(encoding="utf-8")
        validation = validate_intent_text(text, compile_check=True)
        if not validation["ok"]:
            return tool_error_response(
                "COMPILE_FAILED",
                "IntentBundle validation failed.",
                "Regenerate viewspec.intent.json using correction_prompt, then retry compile.",
                errors=[
                    {
                        "code": issue["code"],
                        "message": issue["message"],
                        "fix": issue.get("suggestion") or "Regenerate the full IntentBundle JSON.",
                    }
                    for issue in validation["issues"]
                ],
                data={"validation": validation, "correction_prompt": validation["correction_prompt"]},
                metadata={
                    "cwd": str(root),
                    "allow_outside_cwd": allow_outside_cwd,
                    "sdk_version": __version__,
                    "network_calls": "none",
                },
            )
        design = _load_optional_design(design_path, cwd=root, allow_outside_cwd=allow_outside_cwd, strict=strict_design)
        bundle = IntentBundle.from_json(json.loads(text))
        ast = compile(bundle, design=design, strict_design=strict_design)
        if ir_props_overlay:
            unresolved = _apply_ir_props_overlay(ast.result.root.root, ir_props_overlay)
            if unresolved:
                unresolved_errors = []
                for node_id in unresolved:
                    props = ir_props_overlay.get(node_id, {})
                    if isinstance(props.get("state_text_id"), str):
                        unresolved_errors.append(
                            {
                                "code": "APP_STATE_TEXT_TARGET_UNRESOLVED",
                                "message": f"State text target node {node_id} was not found in the compiled IR.",
                                "fix": "Point the state_text rule at an exactly-once text binding declared by this screen.",
                            }
                        )
                    else:
                        unresolved_errors.append(
                            {
                                "code": "APP_VISIBILITY_TARGET_UNRESOLVED",
                                "message": f"Visibility target node {node_id} was not found in the compiled IR.",
                                "fix": "Point the visibility rule at a declared region, binding, or motif id.",
                            }
                        )
                return tool_error_response(
                    "COMPILE_FAILED",
                    f"App compiler target(s) did not resolve to compiled IR nodes: {', '.join(unresolved)}.",
                    "Verify every compiler-owned target id is declared in this screen's IntentBundle.",
                    errors=unresolved_errors,
                    metadata={
                        "cwd": str(root),
                        "allow_outside_cwd": allow_outside_cwd,
                        "sdk_version": __version__,
                        "network_calls": "none",
                    },
                )
        if target == "react-tsx":
            paths = ReactTsxEmitter().emit(ast, output)
            artifact_path = Path(paths["tsx"])
            emitter = "react_tsx"
        elif target == "react-tailwind-tsx":
            paths = ReactTailwindTsxEmitter().emit(ast, output)
            artifact_path = Path(paths["tsx"])
            emitter = "react_tailwind_tsx"
        else:
            paths = HtmlTailwindEmitter().emit(ast, output)
            artifact_path = Path(paths["html"])
            emitter = "html_tailwind"
        wrap_intent_bundle_manifest(
            Path(paths["manifest"]),
            source_name=source.name,
            raw_source_hash=source_hash(text),
            design=design,
            command_args=_compile_command_args(
                source.name, design_path=design_path, strict_design=strict_design, target=target
            ),
            artifact_path=artifact_path,
            emitter=emitter,
        )
        manifest_summary = summarize_intent_manifest(Path(paths["manifest"]))
        metadata = {
            "cwd": str(root),
            "allow_outside_cwd": allow_outside_cwd,
            "sdk_version": __version__,
            "network_calls": "none",
            "target": target,
            "emitter": emitter,
            "manifest_summary": manifest_summary,
        }
        checked = check_artifact_dir(output)
        if not checked["ok"]:
            return tool_error_response(
                "CHECK_FAILED",
                "Compiled IntentBundle artifact failed viewspec check.",
                "Fix the reported artifact issue and retry compile.",
                paths=paths,
                errors=[
                    check_issue_entry(
                        item, default_fix="Re-run viewspec compile after fixing the reported artifact issue."
                    )
                    for item in checked["errors"]
                ],
                metadata=metadata,
            )
        return tool_response(
            True,
            "Compiled and checked IntentBundle artifact.",
            paths=paths,
            next_actions=[
                "Review ViewSpecView.tsx and provenance_manifest.json."
                if target in {"react-tsx", "react-tailwind-tsx"}
                else "Review dist/index.html and provenance_manifest.json."
            ],
            metadata={**metadata, "artifact_check": "passed"},
        )
    except Exception as exc:
        return exception_response(
            exc,
            "COMPILE_FAILED",
            "Fix the IntentBundle, DESIGN.md, or path issue and retry.",
            metadata=path_policy_metadata(root, allow_outside_cwd),
        )


def wrap_intent_bundle_manifest(
    manifest_path: Path,
    *,
    source_name: str | None,
    raw_source_hash: str,
    design: DesignSystemContext | None,
    command_args: list[str],
    artifact_path: Path | None = None,
    emitter: str | None = None,
) -> None:
    from viewspec.local_tools import atomic_write

    existing = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact = artifact_path or manifest_path.with_name("index.html")
    artifact_hash = file_hash(artifact) if artifact.exists() else None
    wrapped: dict[str, Any] = {
        "version": 1,
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "kind": "intent_bundle_compile",
        "sdk_version": __version__,
        "source_name": source_name,
        "raw_source_hash": raw_source_hash,
        "source_hash": raw_source_hash,
        "design_hash": design.design_hash if design else None,
        "artifact_hash": artifact_hash,
        "command": "compile",
        "command_args": command_args,
        "policy_version": BUNDLE_POLICY_VERSION,
        "guarantees": {
            "sdk_network_calls": "none",
            "artifact_autofetch_network": "none",
            "network_calls": "none",
            "decompilation": "not_applicable",
        },
        "nodes": existing,
        "diagnostics": json.loads(manifest_path.with_name("diagnostics.json").read_text(encoding="utf-8")),
        "external_refs": [],
    }
    if emitter is not None:
        wrapped["emitter"] = emitter
        wrapped["artifact_file"] = artifact.name
    if emitter == "react_tailwind_tsx":
        wrapped["tailwind_recipe_inventory"] = _tailwind_recipe_inventory(existing)
    if design is not None:
        wrapped["design"] = design.to_meta()
    wrapped["semantic_digest"] = build_intent_semantic_digest(
        wrapped,
        artifact.read_text(encoding="utf-8") if artifact.exists() else "",
        emitter=emitter or "html_tailwind",
        diagnostics=wrapped["diagnostics"],
    )
    atomic_write(manifest_path, json.dumps(wrapped, indent=2))


def _tailwind_recipe_inventory(nodes: dict[str, Any]) -> dict[str, Any]:
    recipe_keys: set[str] = set()
    class_tokens: set[str] = set()
    app_roles: set[str] = set()
    app_role_sources: set[str] = set()
    aesthetic_profile: str | None = None
    for entry in nodes.values():
        if not isinstance(entry, dict):
            continue
        props = entry.get("props") if isinstance(entry.get("props"), dict) else {}
        if entry.get("primitive") == "root" and isinstance(props.get("aesthetic_profile"), str):
            aesthetic_profile = props["aesthetic_profile"]
        recipe_key = entry.get("recipe_key")
        if isinstance(recipe_key, str) and recipe_key:
            recipe_keys.add(recipe_key)
        app_role = entry.get("app_role")
        if isinstance(app_role, str) and app_role:
            app_roles.add(app_role)
        app_role_source = entry.get("app_role_source")
        if isinstance(app_role_source, str) and app_role_source:
            app_role_sources.add(app_role_source)
        classes = entry.get("classes")
        if isinstance(classes, list):
            class_tokens.update(item for item in classes if isinstance(item, str) and item)
    return {
        "recipe_pack": "tailwind_app_v1",
        "registry_version": TAILWIND_RECIPE_REGISTRY_VERSION,
        "recipe_registry_digest": tailwind_recipe_registry_digest(),
        "aesthetic_profile": aesthetic_profile,
        "recipe_count": len(recipe_keys),
        "recipes": sorted(recipe_keys),
        "class_count": len(class_tokens),
        "class_tokens": sorted(class_tokens),
        "app_roles": sorted(app_roles),
        "app_role_sources": sorted(app_role_sources),
    }


def _compile_command_args(
    source_name: str,
    *,
    design_path: str | Path | None,
    strict_design: bool,
    target: str = "html-tailwind",
) -> list[str]:
    command = ["viewspec", "compile", source_name]
    if design_path is not None:
        command.extend(["--design", Path(design_path).name])
    if strict_design:
        command.append("--strict-design")
    if target != "html-tailwind":
        command.extend(["--target", target])
    command.extend(["--out", "<out>"])
    return command


def _load_optional_design(
    design_path: str | Path | None,
    *,
    cwd: Path,
    allow_outside_cwd: bool,
    strict: bool,
) -> DesignSystemContext | None:
    if design_path is None:
        return None
    resolved = resolve_local_path(design_path, cwd=cwd, allow_outside_cwd=allow_outside_cwd, must_exist=True)
    return load_design_system(path=resolved, strict=strict)


# UTF-8 canonical form, shared through viewspec._canonical (see its docstring).
_stable_json = canonical_json_utf8


__all__ = [
    "BUNDLE_POLICY_VERSION",
    "INTENT_RESULT_SCHEMA_VERSION",
    "STARTER_INTENT_KINDS",
    "compile_intent_bundle_file_tool",
    "init_intent_file",
    "intent_error_payload",
    "intent_validation_payload",
    "starter_intent_bundle",
    "summarize_intent_manifest",
    "validate_intent_file",
    "validate_intent_text",
    "wrap_intent_bundle_manifest",
]
