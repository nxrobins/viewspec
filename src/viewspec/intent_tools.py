"""Shared IntentBundle tools for agent-native UI workflows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from viewspec._version import __version__
from viewspec.agent import (
    SUPPORTED_AGENT_MOTIFS,
    AgentValidationIssue,
    AgentValidationResult,
    agent_correction_prompt,
    validate_agent_intent_bundle,
)
from viewspec.compiler import compile
from viewspec.design_md import DesignSystemContext, load_design_system
from viewspec.emitters.html_tailwind import HtmlTailwindEmitter
from viewspec.emitters.react_tsx import ReactTsxEmitter
from viewspec.local_tools import (
    atomic_write,
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
from viewspec.raw_html import MANIFEST_SCHEMA_VERSION
from viewspec.sdk.builder import ViewSpecBuilder
from viewspec.types import IntentBundle


BUNDLE_POLICY_VERSION = INTENT_BUNDLE_POLICY_VERSION
INTENT_RESULT_SCHEMA_VERSION = 1
INTENT_COMPILE_TARGETS = ("html-tailwind", "react-tsx")
INTENT_DIFF_VERSION = 1
INTENT_DIFF_BASIS = "intent_bundle_v1"
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


def init_intent_file(path: str | Path = "viewspec.intent.json", *, kind: str = "dashboard", force: bool = False) -> Path:
    output = Path(path)
    if output.exists() and not force:
        raise ValueError(f"{output} already exists; pass --force to overwrite")
    payload = starter_intent_bundle(kind).to_json()
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
            data={"diff": diff},
            next_actions=[] if diff["ok"] else ["Regenerate invalid IntentBundle files before comparing them."],
            metadata={
                "cwd": str(root),
                "allow_outside_cwd": allow_outside_cwd,
                "sdk_version": __version__,
                "network_calls": "none",
                "compile_check": diff["compile_check"],
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


def compile_intent_bundle_file_tool(
    input_path: str | Path,
    out_dir: str | Path,
    *,
    design_path: str | Path | None = None,
    strict_design: bool = False,
    target: str = "html-tailwind",
    cwd: str | Path | None = None,
    allow_outside_cwd: bool = False,
) -> dict[str, Any]:
    root: Path | None = None
    try:
        root = resolve_cwd(cwd)
        if target not in INTENT_COMPILE_TARGETS:
            return tool_error_response(
                "COMPILE_FAILED",
                f"Unsupported IntentBundle compile target: {target}",
                "Use target='html-tailwind' or target='react-tsx'.",
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
        if target == "react-tsx":
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
        if target == "react-tsx":
            paths = ReactTsxEmitter().emit(ast, output)
            artifact_path = Path(paths["tsx"])
            emitter = "react_tsx"
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
        metadata = {
            "cwd": str(root),
            "allow_outside_cwd": allow_outside_cwd,
            "sdk_version": __version__,
            "network_calls": "none",
            "target": target,
            "emitter": emitter,
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
                if target == "react-tsx"
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
    if design is not None:
        wrapped["design"] = design.to_meta()
    atomic_write(manifest_path, json.dumps(wrapped, indent=2, sort_keys=True))


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
    return {"regions": [], "groups": [], "motifs": [], "styles": [], "actions": [], "bindings": []}


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
    "init_intent_file",
    "init_intent_tool",
    "intent_diff_error_payload",
    "intent_error_payload",
    "intent_validation_payload",
    "starter_intent_bundle",
    "validate_intent_bundle_file_tool",
    "validate_intent_file",
    "validate_intent_text",
    "wrap_intent_bundle_manifest",
]
