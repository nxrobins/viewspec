"""Shared IntentBundle tools for agent-native UI workflows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from viewspec._version import __version__
from viewspec.aesthetics import AestheticProfileError, profile_layout_props, profile_style_facts
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
    atomic_write,
    build_intent_semantic_digest,
    check_artifact_dir,
    ensure_no_input_overwrite,
    exception_response,
    file_hash,
    INTENT_BUNDLE_POLICY_VERSION,
    resolve_cwd,
    resolve_local_path,
    path_policy_metadata,
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
INTENT_DIFF_VERSION = 1
INTENT_DIFF_BASIS = "intent_bundle_v1"
STARTER_INTENT_KINDS = tuple(SUPPORTED_AGENT_MOTIFS)
_AESTHETIC_PROFILE_STYLE_IMPACT_KEYS = ("changed_token_count", "category_count", "declaration_count")
_AESTHETIC_PROFILE_STYLE_IMPACT_LABELS = {
    "changed_token_count": "tokens",
    "category_count": "categories",
    "declaration_count": "declarations",
}


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
        items.add_item(label="Compile artifact", description="Let ViewSpec produce checked renderer output.", id="compile")
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


def init_intent_file(path: str | Path = "viewspec.intent.json", *, kind: str = "dashboard", force: bool = False) -> Path:
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


def diff_intent_text(left_text: str, right_text: str, *, compile_check: bool = True) -> dict[str, Any]:
    left_validation = validate_intent_text(left_text, compile_check=compile_check)
    right_validation = validate_intent_text(right_text, compile_check=compile_check)
    if not left_validation["ok"] or not right_validation["ok"]:
        errors = []
        for side, validation in (("left", left_validation), ("right", right_validation)):
            if validation["ok"]:
                continue
            errors.extend(
                {
                    "side": side,
                    "code": issue["code"],
                    "path": issue["path"],
                    "message": issue["message"],
                    "fix": issue.get("suggestion") or "Regenerate the full IntentBundle JSON.",
                }
                for issue in validation["issues"]
            )
        return _intent_diff_payload(
            ok=False,
            compile_check=_combined_compile_check(left_validation, right_validation),
            validation={"left": left_validation, "right": right_validation},
            changes=_empty_intent_changes(),
            changed_fields=[],
            semantic_changes=_empty_intent_semantic_changes(),
            counts=_empty_intent_counts(),
            topology_similarity=0.0,
            errors=errors,
        )

    left_payload = json.loads(left_text)
    right_payload = json.loads(right_text)
    left_sections = _intent_sections(left_payload)
    right_sections = _intent_sections(right_payload)
    changes = _diff_intent_sections(left_sections, right_sections)
    changed_fields = _intent_changed_fields(left_sections, right_sections)
    semantic_changes = _intent_semantic_changes(left_sections, right_sections)
    return _intent_diff_payload(
        ok=True,
        compile_check=_combined_compile_check(left_validation, right_validation),
        validation={"left": _validation_summary(left_validation), "right": _validation_summary(right_validation)},
        changes=changes,
        changed_fields=changed_fields,
        semantic_changes=semantic_changes,
        counts=_intent_counts(left_sections, right_sections),
        topology_similarity=_intent_topology_similarity(left_sections, right_sections, changes),
        errors=[],
    )


def diff_intent_files(left_path: str | Path, right_path: str | Path, *, compile_check: bool = True) -> dict[str, Any]:
    left_text = Path(left_path).read_text(encoding="utf-8")
    right_text = Path(right_path).read_text(encoding="utf-8")
    return diff_intent_text(left_text, right_text, compile_check=compile_check)


def intent_validation_payload(result: AgentValidationResult, *, compile_check: bool) -> dict[str, Any]:
    if not compile_check:
        compile_status = "skipped"
    else:
        compile_status = "passed" if result.valid else "failed"
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


def intent_diff_error_payload(code: str, message: str, fix: str) -> dict[str, Any]:
    return _intent_diff_payload(
        ok=False,
        compile_check="failed",
        validation={"left": None, "right": None},
        changes=_empty_intent_changes(),
        changed_fields=[],
        semantic_changes=_empty_intent_semantic_changes(),
        counts=_empty_intent_counts(),
        topology_similarity=0.0,
        errors=[{"code": code, "message": message, "fix": fix}],
    )


def validate_intent_bundle_file_tool(
    path: str | Path,
    *,
    compile_check: bool = True,
    cwd: str | Path | None = None,
    allow_outside_cwd: bool = False,
) -> dict[str, Any]:
    root: Path | None = None
    try:
        root = resolve_cwd(cwd)
        source = resolve_local_path(path, cwd=root, allow_outside_cwd=allow_outside_cwd, must_exist=True)
        validation = validate_intent_file(source, compile_check=compile_check)
        errors = [
            {
                "code": issue["code"],
                "message": issue["message"],
                "fix": issue.get("suggestion") or "Regenerate the full IntentBundle JSON.",
            }
            for issue in validation["issues"]
        ]
        return tool_response(
            validation["ok"],
            "IntentBundle is valid." if validation["ok"] else "IntentBundle validation failed.",
            paths={"intent": str(source)},
            errors=errors,
            next_actions=[] if validation["ok"] else ["Regenerate viewspec.intent.json using correction_prompt."],
            data={"validation": validation, "correction_prompt": validation["correction_prompt"]},
            metadata={
                "cwd": str(root),
                "allow_outside_cwd": allow_outside_cwd,
                "sdk_version": __version__,
                "network_calls": "none",
                "compile_check": validation["compile_check"],
            },
        )
    except Exception as exc:
        return exception_response(
            exc,
            "INVALID_PATH",
            "Fix the intent file path and retry validate_intent_bundle_file.",
            metadata=path_policy_metadata(root, allow_outside_cwd),
        )


def diff_intent_bundle_files_tool(
    left_path: str | Path,
    right_path: str | Path,
    *,
    compile_check: bool = True,
    cwd: str | Path | None = None,
    allow_outside_cwd: bool = False,
) -> dict[str, Any]:
    root: Path | None = None
    try:
        root = resolve_cwd(cwd)
        left = resolve_local_path(left_path, cwd=root, allow_outside_cwd=allow_outside_cwd, must_exist=True)
        right = resolve_local_path(right_path, cwd=root, allow_outside_cwd=allow_outside_cwd, must_exist=True)
        diff = diff_intent_files(left, right, compile_check=compile_check)
        semantic_summary = intent_semantic_change_lines(diff.get("semantic_changes"))
        return tool_response(
            diff["ok"],
            "Computed IntentBundle semantic diff." if diff["ok"] else "IntentBundle diff failed validation.",
            paths={"left": str(left), "right": str(right)},
            errors=[
                {
                    "code": error["code"],
                    "message": error["message"],
                    "fix": error.get("fix") or "Regenerate the invalid IntentBundle.",
                }
                for error in diff["errors"]
            ],
            data={"diff": diff, "semantic_summary": semantic_summary},
            next_actions=[] if diff["ok"] else ["Regenerate invalid IntentBundle files before comparing them."],
            metadata={
                "cwd": str(root),
                "allow_outside_cwd": allow_outside_cwd,
                "sdk_version": __version__,
                "network_calls": "none",
                "compile_check": diff["compile_check"],
                "semantic_change_count": len(semantic_summary),
                "semantic_change_sections": _intent_semantic_change_sections(diff.get("semantic_changes")),
                "topology_similarity": diff["topology_similarity"],
            },
        )
    except Exception as exc:
        return exception_response(
            exc,
            "DIFF_FAILED",
            "Fix the compared IntentBundle paths and retry diff_intent_bundle_files.",
            metadata=path_policy_metadata(root, allow_outside_cwd),
        )


def agent_correction_prompt_file_tool(
    path: str | Path,
    *,
    cwd: str | Path | None = None,
    allow_outside_cwd: bool = False,
) -> dict[str, Any]:
    root: Path | None = None
    try:
        root = resolve_cwd(cwd)
        source = resolve_local_path(path, cwd=root, allow_outside_cwd=allow_outside_cwd, must_exist=True)
        validation = validate_intent_file(source, compile_check=True)
        errors = [
            {
                "code": issue["code"],
                "message": issue["message"],
                "fix": issue.get("suggestion") or "Regenerate the full IntentBundle JSON.",
            }
            for issue in validation["issues"]
        ]
        return tool_response(
            not validation["correction_prompt"],
            "No correction prompt needed." if validation["ok"] else "Generated IntentBundle correction prompt.",
            paths={"intent": str(source)},
            errors=errors,
            data={"correction_prompt": validation["correction_prompt"], "validation": validation},
            next_actions=[] if validation["ok"] else ["Regenerate the full IntentBundle JSON from correction_prompt."],
            metadata={"cwd": str(root), "allow_outside_cwd": allow_outside_cwd, "sdk_version": __version__, "network_calls": "none"},
        )
    except Exception as exc:
        return exception_response(
            exc,
            "INVALID_PATH",
            "Fix the intent file path and retry agent_correction_prompt_file.",
            metadata=path_policy_metadata(root, allow_outside_cwd),
        )


def init_intent_tool(
    out: str | Path = "viewspec.intent.json",
    *,
    kind: str = "dashboard",
    force: bool = False,
    cwd: str | Path | None = None,
    allow_outside_cwd: bool = False,
) -> dict[str, Any]:
    root: Path | None = None
    try:
        root = resolve_cwd(cwd)
        output = resolve_local_path(out, cwd=root, allow_outside_cwd=allow_outside_cwd)
        init_intent_file(output, kind=kind, force=force)
        validation = validate_intent_file(output)
        return tool_response(
            validation["ok"],
            "Wrote starter IntentBundle." if validation["ok"] else "Wrote starter IntentBundle, but validation failed.",
            paths={"intent": str(output)},
            data={"validation": validation},
            next_actions=[
                "Replace sample labels and values with real user intent.",
                "Create DESIGN.md with viewspec init-design --out DESIGN.md if the repo does not already have one.",
                "Run viewspec compile viewspec.intent.json --design DESIGN.md --out dist/.",
            ],
            metadata={
                "cwd": str(root),
                "allow_outside_cwd": allow_outside_cwd,
                "sdk_version": __version__,
                "network_calls": "none",
                "kind": kind,
            },
        )
    except Exception as exc:
        return exception_response(
            exc,
            "IO_ERROR",
            "Choose a writable intent path, valid kind, or pass force=True.",
            metadata=path_policy_metadata(root, allow_outside_cwd),
        )


def _apply_ir_props_overlay(root_node: Any, overlay: dict[str, dict[str, Any]]) -> list[str]:
    """Merge bounded props onto IR nodes by node id, between compile() and emit.

    The overlay is a closed data structure (AppBundle V4 visibility bake): only the visibility
    marker keys are permitted, so this seam cannot become a generic style/content side channel.
    Returns the overlay node ids that did not resolve to an IR node (fail-closed at the caller).
    """
    allowed_keys = {"visibility_rule_id", "visibility_hidden_initial"}
    for node_id, props in overlay.items():
        unexpected = set(props) - allowed_keys
        if unexpected:
            raise ValueError(f"ir_props_overlay only supports visibility marker keys; got {sorted(unexpected)} for {node_id}.")
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
            ensure_no_input_overwrite(source, output, ("ViewSpecView.tsx", "provenance_manifest.json", "diagnostics.json"))
        else:
            ensure_no_input_overwrite(source, output, ("index.html", "provenance_manifest.json", "diagnostics.json"))
        text = source.read_text(encoding="utf-8")
        validation = validate_intent_text(text, compile_check=True)
        if not validation["ok"]:
            return tool_error_response(
                "COMPILE_FAILED",
                "IntentBundle validation failed.",
                "Regenerate viewspec.intent.json using correction_prompt, then retry compile_intent_bundle_file.",
                errors=[
                    {
                        "code": issue["code"],
                        "message": issue["message"],
                        "fix": issue.get("suggestion") or "Regenerate the full IntentBundle JSON.",
                    }
                    for issue in validation["issues"]
                ],
                data={"validation": validation, "correction_prompt": validation["correction_prompt"]},
                metadata={"cwd": str(root), "allow_outside_cwd": allow_outside_cwd, "sdk_version": __version__, "network_calls": "none"},
            )
        design = _load_optional_design(design_path, cwd=root, allow_outside_cwd=allow_outside_cwd, strict=strict_design)
        bundle = IntentBundle.from_json(json.loads(text))
        ast = compile(bundle, design=design, strict_design=strict_design)
        if ir_props_overlay:
            unresolved = _apply_ir_props_overlay(ast.result.root.root, ir_props_overlay)
            if unresolved:
                return tool_error_response(
                    "COMPILE_FAILED",
                    f"Visibility target(s) did not resolve to compiled IR nodes: {', '.join(unresolved)}.",
                    "Verify the visibility target_ref ids are declared in this screen's IntentBundle.",
                    errors=[
                        {
                            "code": "APP_VISIBILITY_TARGET_UNRESOLVED",
                            "message": f"Visibility target node {node_id} was not found in the compiled IR.",
                            "fix": "Point the visibility rule at a declared region, binding, or motif id.",
                        }
                        for node_id in unresolved
                    ],
                    metadata={"cwd": str(root), "allow_outside_cwd": allow_outside_cwd, "sdk_version": __version__, "network_calls": "none"},
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
            command_args=_compile_command_args(source.name, design_path=design_path, strict_design=strict_design, target=target),
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
                "Fix the reported artifact issue and retry compile_intent_bundle_file.",
                paths=paths,
                errors=[
                    {
                        "code": "CHECK_FAILED",
                        "message": item,
                        "fix": "Re-run viewspec compile after fixing the reported artifact issue.",
                    }
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


def _intent_diff_payload(
    *,
    ok: bool,
    compile_check: str,
    validation: dict[str, Any],
    changes: dict[str, dict[str, list[str]]],
    changed_fields: list[dict[str, Any]],
    semantic_changes: dict[str, list[dict[str, Any]]],
    counts: dict[str, dict[str, int]],
    topology_similarity: float,
    errors: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": INTENT_RESULT_SCHEMA_VERSION,
        "ok": ok,
        "diff_version": INTENT_DIFF_VERSION,
        "basis": INTENT_DIFF_BASIS,
        "compile_check": compile_check,
        "topology_similarity": topology_similarity,
        "counts": counts,
        "changes": changes,
        "changed_fields": changed_fields,
        "semantic_changes": semantic_changes,
        "diagnostics": [],
        "errors": errors,
        "validation": validation,
    }


def _validation_summary(validation: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": validation["ok"],
        "compile_check": validation["compile_check"],
        "issue_count": len(validation["issues"]),
    }


def _combined_compile_check(left_validation: dict[str, Any], right_validation: dict[str, Any]) -> str:
    states = {left_validation["compile_check"], right_validation["compile_check"]}
    if "failed" in states:
        return "failed"
    if states == {"skipped"}:
        return "skipped"
    if "skipped" in states:
        return "mixed"
    return "passed"


def _intent_sections(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    view_spec = payload["view_spec"]
    return {
        "bundle_metadata": _intent_metadata(payload),
        "substrate_nodes": dict(payload["substrate"]["nodes"]),
        "regions": _list_section(view_spec["regions"]),
        "bindings": _list_section(view_spec["bindings"]),
        "groups": _list_section(view_spec["groups"]),
        "motifs": _list_section(view_spec["motifs"]),
        "styles": _list_section(view_spec["styles"]),
        "actions": _list_section(view_spec["actions"]),
    }


def _intent_metadata(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    substrate = payload["substrate"]
    view_spec = payload["view_spec"]
    return {
        "substrate.id": {"value": substrate.get("id")},
        "substrate.root_id": {"value": substrate.get("root_id")},
        "view_spec.id": {"value": view_spec.get("id")},
        "view_spec.substrate_id": {"value": view_spec.get("substrate_id")},
        "view_spec.complexity_tier": {"value": view_spec.get("complexity_tier")},
        "view_spec.root_region": {"value": view_spec.get("root_region")},
    }


def _list_section(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {item["id"]: item for item in items}


def _diff_intent_sections(
    left_sections: dict[str, dict[str, Any]],
    right_sections: dict[str, dict[str, Any]],
) -> dict[str, dict[str, list[str]]]:
    return {
        section: _diff_id_map(left_items, right_sections[section])
        for section, left_items in left_sections.items()
    }


def _diff_id_map(left: dict[str, Any], right: dict[str, Any]) -> dict[str, list[str]]:
    left_ids = set(left)
    right_ids = set(right)
    common = left_ids & right_ids
    return {
        "added": sorted(right_ids - left_ids),
        "removed": sorted(left_ids - right_ids),
        "changed": sorted(item_id for item_id in common if _stable_json(left[item_id]) != _stable_json(right[item_id])),
    }


def _intent_changed_fields(
    left_sections: dict[str, dict[str, Any]],
    right_sections: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    tracked_fields = {
        "bundle_metadata": ("value",),
        "substrate_nodes": ("kind", "attrs", "slots", "edges"),
        "regions": ("parent_region", "role", "layout", "min_children", "max_children"),
        "bindings": ("address", "target_region", "present_as", "cardinality"),
        "groups": ("kind", "members", "target_region"),
        "motifs": ("kind", "region", "members"),
        "styles": ("target", "token"),
        "actions": ("kind", "label", "target_region", "target_ref", "payload_bindings"),
    }
    changes: list[dict[str, Any]] = []
    for section, fields in tracked_fields.items():
        left_items = left_sections[section]
        right_items = right_sections[section]
        for item_id in sorted(set(left_items) & set(right_items)):
            left_item = left_items[item_id]
            right_item = right_items[item_id]
            for field in fields:
                if _stable_json(left_item.get(field)) == _stable_json(right_item.get(field)):
                    continue
                if section == "bundle_metadata":
                    changes.append(
                        {
                            "section": section,
                            "id": "$",
                            "field": item_id,
                            "left": left_item.get(field),
                            "right": right_item.get(field),
                        }
                    )
                    continue
                changes.append(
                    {
                        "section": section,
                        "id": item_id,
                        "field": field,
                        "left": left_item.get(field),
                        "right": right_item.get(field),
                    }
                )
    return changes


def _intent_semantic_changes(
    left_sections: dict[str, dict[str, Any]],
    right_sections: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    return {
        "regions": _semantic_region_changes(left_sections["regions"], right_sections["regions"]),
        "groups": _semantic_group_changes(left_sections["groups"], right_sections["groups"]),
        "motifs": _semantic_motif_changes(left_sections["motifs"], right_sections["motifs"]),
        "aesthetic_profiles": _semantic_aesthetic_profile_changes(left_sections["styles"], right_sections["styles"]),
        "styles": _semantic_style_changes(left_sections["styles"], right_sections["styles"]),
        "actions": _semantic_action_changes(left_sections["actions"], right_sections["actions"]),
        "bindings": _semantic_binding_changes(left_sections["bindings"], right_sections["bindings"]),
    }


def _semantic_region_changes(left: dict[str, Any], right: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for region_id in sorted(set(right) - set(left)):
        item = right[region_id]
        changes.append(
            {
                "id": region_id,
                "change": "added",
                "parent_region": item.get("parent_region"),
                "role": item.get("role"),
                "layout": item.get("layout"),
            }
        )
    for region_id in sorted(set(left) - set(right)):
        item = left[region_id]
        changes.append(
            {
                "id": region_id,
                "change": "removed",
                "parent_region": item.get("parent_region"),
                "role": item.get("role"),
                "layout": item.get("layout"),
            }
        )
    for region_id in sorted(set(left) & set(right)):
        left_item = left[region_id]
        right_item = right[region_id]
        for field, change_name in (
            ("parent_region", "parent_changed"),
            ("role", "role_changed"),
            ("layout", "layout_changed"),
            ("min_children", "min_children_changed"),
            ("max_children", "max_children_changed"),
        ):
            if _stable_json(left_item.get(field)) != _stable_json(right_item.get(field)):
                changes.append(
                    {
                        "id": region_id,
                        "change": change_name,
                        "left": left_item.get(field),
                        "right": right_item.get(field),
                    }
                )
    return changes


def _semantic_group_changes(left: dict[str, Any], right: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for group_id in sorted(set(right) - set(left)):
        item = right[group_id]
        changes.append(
            {
                "id": group_id,
                "change": "added",
                "kind": item.get("kind"),
                "target_region": item.get("target_region"),
                "members": list(item.get("members") or []),
            }
        )
    for group_id in sorted(set(left) - set(right)):
        item = left[group_id]
        changes.append(
            {
                "id": group_id,
                "change": "removed",
                "kind": item.get("kind"),
                "target_region": item.get("target_region"),
                "members": list(item.get("members") or []),
            }
        )
    for group_id in sorted(set(left) & set(right)):
        left_item = left[group_id]
        right_item = right[group_id]
        for field, change_name in (("kind", "kind_changed"), ("target_region", "target_region_changed")):
            if _stable_json(left_item.get(field)) != _stable_json(right_item.get(field)):
                changes.append(
                    {
                        "id": group_id,
                        "change": change_name,
                        "left": left_item.get(field),
                        "right": right_item.get(field),
                    }
                )
        member_delta = _ordered_list_delta(left_item.get("members"), right_item.get("members"))
        if member_delta["added"] or member_delta["removed"] or member_delta["order_changed"]:
            changes.append({"id": group_id, "change": "members_changed", **member_delta})
    return changes


def _semantic_motif_changes(left: dict[str, Any], right: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for motif_id in sorted(set(right) - set(left)):
        item = right[motif_id]
        changes.append(
            {
                "id": motif_id,
                "change": "added",
                "kind": item.get("kind"),
                "region": item.get("region"),
                "members": list(item.get("members") or []),
            }
        )
    for motif_id in sorted(set(left) - set(right)):
        item = left[motif_id]
        changes.append(
            {
                "id": motif_id,
                "change": "removed",
                "kind": item.get("kind"),
                "region": item.get("region"),
                "members": list(item.get("members") or []),
            }
        )
    for motif_id in sorted(set(left) & set(right)):
        left_item = left[motif_id]
        right_item = right[motif_id]
        for field, change_name in (("kind", "kind_changed"), ("region", "region_changed")):
            if _stable_json(left_item.get(field)) != _stable_json(right_item.get(field)):
                changes.append(
                    {
                        "id": motif_id,
                        "change": change_name,
                        "left": left_item.get(field),
                        "right": right_item.get(field),
                    }
                )
        member_delta = _ordered_list_delta(left_item.get("members"), right_item.get("members"))
        if member_delta["added"] or member_delta["removed"] or member_delta["order_changed"]:
            changes.append({"id": motif_id, "change": "members_changed", **member_delta})
    return changes


def _semantic_style_changes(left: dict[str, Any], right: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for style_id in sorted(set(right) - set(left)):
        item = right[style_id]
        changes.append(
            {
                "id": style_id,
                "change": "added",
                "target": item.get("target"),
                "token": item.get("token"),
            }
        )
    for style_id in sorted(set(left) - set(right)):
        item = left[style_id]
        changes.append(
            {
                "id": style_id,
                "change": "removed",
                "target": item.get("target"),
                "token": item.get("token"),
            }
        )
    for style_id in sorted(set(left) & set(right)):
        left_item = left[style_id]
        right_item = right[style_id]
        for field, change_name in (("target", "target_changed"), ("token", "token_changed")):
            if _stable_json(left_item.get(field)) != _stable_json(right_item.get(field)):
                changes.append(
                    {
                        "id": style_id,
                        "change": change_name,
                        "left": left_item.get(field),
                        "right": right_item.get(field),
                    }
                )
    return changes


def _semantic_aesthetic_profile_changes(left: dict[str, Any], right: dict[str, Any]) -> list[dict[str, Any]]:
    left_profile = _aesthetic_profile_style(left)
    right_profile = _aesthetic_profile_style(right)
    if left_profile is None and right_profile is None:
        return []
    if left_profile == right_profile:
        return []
    if left_profile is None and right_profile is not None:
        return [
            {
                "change": "added",
                "profile": right_profile["token"],
                "style_id": right_profile["id"],
                "target": right_profile["target"],
                "impact": _aesthetic_profile_impact(right_profile["token"]),
            }
        ]
    if left_profile is not None and right_profile is None:
        return [
            {
                "change": "removed",
                "profile": left_profile["token"],
                "style_id": left_profile["id"],
                "target": left_profile["target"],
                "impact": _aesthetic_profile_impact(left_profile["token"]),
            }
        ]
    assert left_profile is not None and right_profile is not None
    left_impact = _aesthetic_profile_impact(left_profile["token"])
    right_impact = _aesthetic_profile_impact(right_profile["token"])
    return [
        {
            "change": "profile_changed",
            "left": left_profile["token"],
            "right": right_profile["token"],
            "left_style_id": left_profile["id"],
            "right_style_id": right_profile["id"],
            "left_target": left_profile["target"],
            "right_target": right_profile["target"],
            "left_impact": left_impact,
            "right_impact": right_impact,
            "impact_delta": _aesthetic_profile_impact_delta(left_impact, right_impact),
        }
    ]


def _aesthetic_profile_style(styles: dict[str, Any]) -> dict[str, Any] | None:
    for style_id in sorted(styles):
        item = styles[style_id]
        if not isinstance(item, dict):
            continue
        token = item.get("token")
        if isinstance(token, str) and token.startswith("aesthetic."):
            return {
                "id": style_id,
                "target": item.get("target"),
                "token": token,
            }
    return None


def _aesthetic_profile_impact(profile: object) -> dict[str, Any]:
    if not isinstance(profile, str) or not profile.startswith("aesthetic."):
        return {}
    try:
        style_facts = profile_style_facts(profile)
        layout_props = profile_layout_props(profile)
    except AestheticProfileError:
        return {}
    return {
        "style": {
            key: style_facts[key]
            for key in _AESTHETIC_PROFILE_STYLE_IMPACT_KEYS
            if isinstance(style_facts.get(key), int)
        },
        "layout": {role: dict(props) for role, props in sorted(layout_props.items())},
    }


def _aesthetic_profile_impact_delta(left_impact: dict[str, Any], right_impact: dict[str, Any]) -> dict[str, Any]:
    left_style = left_impact.get("style") if isinstance(left_impact.get("style"), dict) else {}
    right_style = right_impact.get("style") if isinstance(right_impact.get("style"), dict) else {}
    style_delta = {
        key: {"left": left_style.get(key), "right": right_style.get(key)}
        for key in _AESTHETIC_PROFILE_STYLE_IMPACT_KEYS
        if left_style.get(key) != right_style.get(key)
    }
    left_layout = left_impact.get("layout") if isinstance(left_impact.get("layout"), dict) else {}
    right_layout = right_impact.get("layout") if isinstance(right_impact.get("layout"), dict) else {}
    layout_delta: list[dict[str, Any]] = []
    for role in sorted(set(left_layout) | set(right_layout)):
        left_props = left_layout.get(role)
        right_props = right_layout.get(role)
        if _stable_json(left_props) == _stable_json(right_props):
            continue
        if left_props is None:
            layout_delta.append({"role": role, "change": "added", "right": right_props})
        elif right_props is None:
            layout_delta.append({"role": role, "change": "removed", "left": left_props})
        else:
            layout_delta.append({"role": role, "change": "props_changed", "left": left_props, "right": right_props})
    return {"style": style_delta, "layout": layout_delta}


def _semantic_action_changes(left: dict[str, Any], right: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for action_id in sorted(set(right) - set(left)):
        item = right[action_id]
        changes.append(
            {
                "id": action_id,
                "change": "added",
                "kind": item.get("kind"),
                "target_ref": item.get("target_ref"),
                "payload_bindings": list(item.get("payload_bindings") or []),
            }
        )
    for action_id in sorted(set(left) - set(right)):
        item = left[action_id]
        changes.append(
            {
                "id": action_id,
                "change": "removed",
                "kind": item.get("kind"),
                "target_ref": item.get("target_ref"),
                "payload_bindings": list(item.get("payload_bindings") or []),
            }
        )
    for action_id in sorted(set(left) & set(right)):
        left_item = left[action_id]
        right_item = right[action_id]
        for field, change_name in (
            ("kind", "kind_changed"),
            ("label", "label_changed"),
            ("target_region", "target_region_changed"),
            ("target_ref", "target_ref_changed"),
        ):
            if _stable_json(left_item.get(field)) != _stable_json(right_item.get(field)):
                changes.append(
                    {
                        "id": action_id,
                        "change": change_name,
                        "left": left_item.get(field),
                        "right": right_item.get(field),
                    }
                )
        payload_delta = _ordered_list_delta(left_item.get("payload_bindings"), right_item.get("payload_bindings"))
        if payload_delta["added"] or payload_delta["removed"] or payload_delta["order_changed"]:
            changes.append({"id": action_id, "change": "payload_bindings_changed", **payload_delta})
    return changes


def _semantic_binding_changes(left: dict[str, Any], right: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for binding_id in sorted(set(right) - set(left)):
        item = right[binding_id]
        changes.append(
            {
                "id": binding_id,
                "change": "added",
                "address": item.get("address"),
                "target_region": item.get("target_region"),
                "present_as": item.get("present_as"),
            }
        )
    for binding_id in sorted(set(left) - set(right)):
        item = left[binding_id]
        changes.append(
            {
                "id": binding_id,
                "change": "removed",
                "address": item.get("address"),
                "target_region": item.get("target_region"),
                "present_as": item.get("present_as"),
            }
        )
    for binding_id in sorted(set(left) & set(right)):
        left_item = left[binding_id]
        right_item = right[binding_id]
        for field, change_name in (
            ("address", "source_changed"),
            ("target_region", "target_region_changed"),
            ("present_as", "presentation_changed"),
            ("cardinality", "cardinality_changed"),
        ):
            if _stable_json(left_item.get(field)) != _stable_json(right_item.get(field)):
                changes.append(
                    {
                        "id": binding_id,
                        "change": change_name,
                        "left": left_item.get(field),
                        "right": right_item.get(field),
                    }
                )
    return changes


def _ordered_list_delta(left_value: Any, right_value: Any) -> dict[str, Any]:
    left = list(left_value or []) if isinstance(left_value, list) else []
    right = list(right_value or []) if isinstance(right_value, list) else []
    left_set = set(left)
    right_set = set(right)
    return {
        "added": sorted(right_set - left_set),
        "removed": sorted(left_set - right_set),
        "order_changed": left != right and left_set == right_set,
        "left_order": left,
        "right_order": right,
    }


def _intent_counts(
    left_sections: dict[str, dict[str, Any]],
    right_sections: dict[str, dict[str, Any]],
) -> dict[str, dict[str, int]]:
    return {
        section: {"left": len(left_items), "right": len(right_sections[section])}
        for section, left_items in left_sections.items()
    }


def _empty_intent_counts() -> dict[str, dict[str, int]]:
    return {section: {"left": 0, "right": 0} for section in _intent_section_names()}


def _empty_intent_changes() -> dict[str, dict[str, list[str]]]:
    return {section: {"added": [], "removed": [], "changed": []} for section in _intent_section_names()}


def _empty_intent_semantic_changes() -> dict[str, list[dict[str, Any]]]:
    return {"regions": [], "groups": [], "motifs": [], "aesthetic_profiles": [], "styles": [], "actions": [], "bindings": []}


def intent_semantic_change_lines(semantic_changes: object) -> list[str]:
    if not isinstance(semantic_changes, dict):
        return []
    lines: list[str] = []
    for section in ("regions", "groups", "motifs", "aesthetic_profiles", "styles", "bindings", "actions"):
        changes = semantic_changes.get(section)
        if not isinstance(changes, list):
            continue
        for change in changes:
            if not isinstance(change, dict):
                continue
            line = _intent_semantic_change_line(section, change)
            if line:
                lines.append(line)
    return lines


def _intent_semantic_change_sections(semantic_changes: object) -> list[str]:
    if not isinstance(semantic_changes, dict):
        return []
    sections = []
    for section in ("regions", "groups", "motifs", "aesthetic_profiles", "styles", "bindings", "actions"):
        changes = semantic_changes.get(section)
        if isinstance(changes, list) and changes:
            sections.append(section)
    return sections


def _intent_semantic_change_line(section: str, change: dict[str, object]) -> str:
    change_name = str(change.get("change") or "changed")
    if section == "aesthetic_profiles":
        if change_name == "profile_changed":
            base = (
                "aesthetic_profiles: profile_changed "
                f"{_intent_diff_value(change.get('left'))} -> {_intent_diff_value(change.get('right'))} "
                f"target={_intent_diff_value(change.get('right_target'))}"
            )
            impact_summary = _aesthetic_profile_change_impact_summary(change)
            return f"{base} {impact_summary}" if impact_summary else base
        if change_name in {"added", "removed"}:
            base = (
                f"aesthetic_profiles: {change_name} "
                f"{_intent_diff_value(change.get('profile'))} "
                f"target={_intent_diff_value(change.get('target'))}"
            )
            impact_summary = _aesthetic_profile_impact_summary(change.get("impact"))
            return f"{base} impact={impact_summary}" if impact_summary else base
    item_id = change.get("id")
    prefix = f"{section}.{item_id}" if isinstance(item_id, str) and item_id else section
    parts = [f"{prefix}: {change_name}"]
    if "left" in change and "right" in change:
        parts.append(f"{_intent_diff_value(change.get('left'))} -> {_intent_diff_value(change.get('right'))}")
    for field in (
        "kind",
        "target",
        "target_region",
        "target_ref",
        "role",
        "layout",
        "present_as",
        "parent_region",
        "profile",
        "token",
        "payload_bindings",
        "added",
        "removed",
        "order_changed",
    ):
        if field in change:
            parts.append(f"{field}={_intent_diff_value(change.get(field))}")
    return " ".join(parts)


def _aesthetic_profile_change_impact_summary(change: dict[str, object]) -> str:
    impact_delta = change.get("impact_delta")
    if not isinstance(impact_delta, dict):
        return ""
    parts: list[str] = []
    style_summary = _aesthetic_profile_style_delta_summary(impact_delta.get("style"))
    if style_summary:
        parts.append(f"style_delta={style_summary}")
    layout_summary = _aesthetic_profile_layout_delta_summary(impact_delta.get("layout"))
    if layout_summary:
        parts.append(f"layout_delta={layout_summary}")
    return " ".join(parts)


def _aesthetic_profile_style_delta_summary(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    parts: list[str] = []
    for key in _AESTHETIC_PROFILE_STYLE_IMPACT_KEYS:
        delta = value.get(key)
        if not isinstance(delta, dict):
            continue
        label = _AESTHETIC_PROFILE_STYLE_IMPACT_LABELS[key]
        parts.append(f"{label} {_intent_diff_value(delta.get('left'))} -> {_intent_diff_value(delta.get('right'))}")
    return "; ".join(parts)


def _aesthetic_profile_layout_delta_summary(value: object) -> str:
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        change = item.get("change")
        if not isinstance(role, str) or not isinstance(change, str):
            continue
        if change == "added":
            parts.append(f"{role} added {_aesthetic_profile_layout_props_summary(item.get('right'))}")
        elif change == "removed":
            parts.append(f"{role} removed {_aesthetic_profile_layout_props_summary(item.get('left'))}")
        elif change == "props_changed":
            left = _aesthetic_profile_layout_props_summary(item.get("left"))
            right = _aesthetic_profile_layout_props_summary(item.get("right"))
            parts.append(f"{role} {left} -> {right}")
    return "; ".join(part for part in parts if part.strip())


def _aesthetic_profile_impact_summary(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    style = value.get("style")
    layout = value.get("layout")
    parts: list[str] = []
    if isinstance(style, dict):
        tokens = style.get("changed_token_count")
        categories = style.get("category_count")
        declarations = style.get("declaration_count")
        if tokens is not None and categories is not None and declarations is not None:
            parts.append(f"style {tokens} tokens/{categories} categories/{declarations} declarations")
    layout_summary = _aesthetic_profile_layout_summary(layout)
    if layout_summary:
        parts.append(f"layout {layout_summary}")
    return "; ".join(parts)


def _aesthetic_profile_layout_summary(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    parts: list[str] = []
    for role in sorted(value):
        props = _aesthetic_profile_layout_props_summary(value.get(role))
        if props:
            parts.append(f"{role} {props}")
    return "; ".join(parts)


def _aesthetic_profile_layout_props_summary(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    return " ".join(f"{key}={_intent_diff_value(value[key])}" for key in sorted(value))


def _intent_diff_value(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def _intent_section_names() -> tuple[str, ...]:
    return ("bundle_metadata", "substrate_nodes", "regions", "bindings", "groups", "motifs", "styles", "actions")


def _intent_topology_similarity(
    left_sections: dict[str, dict[str, Any]],
    right_sections: dict[str, dict[str, Any]],
    changes: dict[str, dict[str, list[str]]],
) -> float:
    changed = 0
    total = 0
    for section_name, section_changes in changes.items():
        changed += len(section_changes["added"]) + len(section_changes["removed"]) + len(section_changes["changed"])
        total += len(set(left_sections[section_name]) | set(right_sections[section_name]))
    if total == 0:
        return 1.0
    return round(max(0.0, 1.0 - (changed / total)), 4)


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


__all__ = [
    "BUNDLE_POLICY_VERSION",
    "INTENT_DIFF_BASIS",
    "INTENT_DIFF_VERSION",
    "INTENT_RESULT_SCHEMA_VERSION",
    "STARTER_INTENT_KINDS",
    "agent_correction_prompt_file_tool",
    "compile_intent_bundle_file_tool",
    "diff_intent_bundle_files_tool",
    "diff_intent_files",
    "diff_intent_text",
    "intent_semantic_change_lines",
    "init_intent_file",
    "init_intent_tool",
    "intent_diff_error_payload",
    "intent_error_payload",
    "intent_validation_payload",
    "starter_intent_bundle",
    "summarize_intent_manifest",
    "validate_intent_bundle_file_tool",
    "validate_intent_file",
    "validate_intent_text",
    "wrap_intent_bundle_manifest",
]
