"""Shared local tool helpers for CLI and native agent integrations."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from viewspec._version import __version__
from viewspec.design_md import DesignSystemContext, DesignSystemError, load_design_system
from viewspec.emitters.html_tailwind import ACTION_EVENT_SCRIPT
from viewspec.raw_html import (
    MANIFEST_SCHEMA_VERSION,
    RAW_HTML_POLICY_VERSION,
    HtmlInputError,
    compile_html,
    diff_html,
    lift_html,
    write_html_compile_result,
)


MCP_RESULT_SCHEMA_VERSION = 1
INTENT_BUNDLE_POLICY_VERSION = "viewspec-intent-bundle@1"
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
CANONICAL_CONTENT_REF_RE = re.compile(r"^node:[A-Za-z0-9_.-]+(?:#(?:attr|slot|edge):[A-Za-z0-9_.-]+(?:\[[0-9]+\])?)?$")
VIEWSPEC_INTENT_REF_RE = re.compile(r"^viewspec:(view|region|binding|group|motif|style|action):[A-Za-z0-9_.-]+$")
ACTION_TARGET_REF_RE = re.compile(r"^(region|binding|motif|view):[A-Za-z0-9_.-]+$")
ABSOLUTE_PATH_ARG_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/]{1,2})")
DIAGNOSTIC_SEVERITIES = {"error", "info", "warning"}
MCP_RESERVED_RESULT_KEYS = {
    "diagnostics",
    "errors",
    "external_refs",
    "metadata",
    "next_actions",
    "ok",
    "paths",
    "schema_version",
    "summary",
}
EXTERNAL_REF_POLICIES = {
    ("image", "src", "inert_placeholder"),
    ("link", "href", "user_click"),
}
KNOWN_EMITTERS = {"html_tailwind", "react_tsx"}
EMITTER_ARTIFACT_FILES = {
    "html_tailwind": "index.html",
    "react_tsx": "ViewSpecView.tsx",
}
REACT_TSX_REQUIRED_MARKERS = {
    '"use client";': "ViewSpecView.tsx missing client component directive",
    'source: "viewspec-react-tsx"': "ViewSpecView.tsx missing React action source marker",
    "export function ViewSpecView": "ViewSpecView.tsx missing ViewSpecView export",
    "const collectPayloadValues = (payloadBindings: string[]): Record<string, unknown> =>": (
        "ViewSpecView.tsx missing action payload collection"
    ),
}
REACT_TSX_ACTION_REQUIRED_MARKERS = {
    "payloadValues: collectPayloadValues": "ViewSpecView.tsx missing action payload dispatch",
}
REACT_TSX_FORBIDDEN_SURFACES = (
    (re.compile(r"\bdangerouslySetInnerHTML\b"), "ViewSpecView.tsx contains dangerouslySetInnerHTML"),
    (re.compile(r"\bfetch\s*\("), "ViewSpecView.tsx contains fetch()"),
    (re.compile(r"\bXMLHttpRequest\b"), "ViewSpecView.tsx contains XMLHttpRequest"),
    (re.compile(r"\bWebSocket\b"), "ViewSpecView.tsx contains WebSocket"),
    (re.compile(r"\bEventSource\b"), "ViewSpecView.tsx contains EventSource"),
    (re.compile(r"\bnavigator\.sendBeacon\b"), "ViewSpecView.tsx contains navigator.sendBeacon"),
    (re.compile(r"\bimport\s*\("), "ViewSpecView.tsx contains dynamic import"),
    (re.compile(r"\beval\s*\("), "ViewSpecView.tsx contains eval()"),
    (re.compile(r"\bnew\s+Function\s*\("), "ViewSpecView.tsx contains new Function()"),
    (re.compile(r"(?i)<script\b"), "ViewSpecView.tsx contains a script tag"),
)
REMOTE_AUTOFETCH_ATTRS = {"action", "background", "formaction", "manifest", "poster", "src", "srcset"}
REMOTE_HREF_AUTOFETCH_TAGS = {"image", "use"}
ACTIVE_OR_AUTOFETCH_TAGS = {"embed", "iframe", "link", "object"}
ACTIVE_STRUCTURAL_TAGS = {"form"}
VOID_HTML_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
TEXT_PROP_PRIMITIVES = {"badge", "button", "label", "text", "value"}
EXPECTED_MANIFEST_ENVELOPES = {
    "raw_html_compile": {
        "command": "compile_html",
        "policy_version": RAW_HTML_POLICY_VERSION,
        "decompilation": "not_claimed",
    },
    "intent_bundle_compile": {
        "command": "compile",
        "policy_version": INTENT_BUNDLE_POLICY_VERSION,
        "decompilation": "not_applicable",
    },
}
STARTER_DESIGN = """---
name: Agent Output
colors:
  primary: "#111827"
  secondary: "#4B5563"
  surface: "#FFFFFF"
  background: "#F8FAFC"
  accent: "#0F766E"
typography:
  body:
    fontFamily: Inter
    fontSize: 16px
    lineHeight: 1.6
  heading:
    fontFamily: Inter
    fontWeight: 760
    letterSpacing: -0.02em
spacing:
  md: 16px
rounded:
  md: 10px
---
"""


class LocalToolError(ValueError):
    """Agent-readable local tool failure."""

    def __init__(self, code: str, message: str, fix: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.fix = fix

    def to_json(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "fix": self.fix}


def init_design_file(path: str | Path = "DESIGN.md", *, force: bool = False) -> Path:
    output = Path(path)
    if output.exists() and not force:
        raise ValueError(f"{output} already exists; pass --force to overwrite")
    atomic_write(output, STARTER_DESIGN)
    return output


def check_artifact_dir(path: str | Path) -> dict[str, Any]:
    artifact_path = Path(path)
    errors: list[str] = []
    warnings: list[str] = []
    manifest_path = artifact_path / "provenance_manifest.json"
    html_path = artifact_path / "index.html"
    diagnostics_path = artifact_path / "diagnostics.json"
    manifest: dict[str, Any] = {}

    if not manifest_path.exists():
        errors.append("missing provenance_manifest.json")
    else:
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"invalid provenance_manifest.json: {exc}")
        else:
            if isinstance(loaded, dict):
                manifest = loaded
            else:
                errors.append("provenance_manifest.json must be an object")

    required = {
        "version",
        "manifest_schema_version",
        "kind",
        "sdk_version",
        "source_name",
        "raw_source_hash",
        "source_hash",
        "design_hash",
        "artifact_hash",
        "command",
        "command_args",
        "policy_version",
        "guarantees",
        "nodes",
        "diagnostics",
        "external_refs",
    }
    for key in sorted(required - set(manifest)):
        errors.append(f"manifest missing {key}")
    if manifest.get("version") != 1:
        errors.append("manifest version must be 1")
    if manifest.get("manifest_schema_version") != MANIFEST_SCHEMA_VERSION:
        errors.append(f"manifest_schema_version must be {MANIFEST_SCHEMA_VERSION}")
    _validate_manifest_envelope(manifest, errors)
    for key in ("raw_source_hash", "source_hash", "artifact_hash"):
        value = manifest.get(key)
        if value is not None and (not isinstance(value, str) or not HASH_RE.match(value)):
            errors.append(f"manifest {key} must be a sha256 hex string")
    design_hash = manifest.get("design_hash")
    if design_hash is not None and (not isinstance(design_hash, str) or not HASH_RE.match(design_hash)):
        errors.append("manifest design_hash must be null or a sha256 hex string")
    if not isinstance(manifest.get("command_args"), list) or not all(isinstance(item, str) for item in manifest.get("command_args", [])):
        errors.append("manifest command_args must be a list of strings")
    else:
        absolute_args = [item for item in manifest["command_args"] if looks_absolute_path_arg(item)]
        if absolute_args:
            errors.append(f"manifest command_args must not contain absolute paths: {absolute_args}")

    guarantees = manifest.get("guarantees")
    if isinstance(guarantees, dict):
        for key in ("sdk_network_calls", "artifact_autofetch_network", "network_calls"):
            if guarantees.get(key) != "none":
                errors.append(f"manifest guarantees.{key} must be 'none'")
    else:
        errors.append("manifest guarantees must be an object")

    _validate_manifest_diagnostics(manifest.get("diagnostics"), errors)
    _validate_manifest_external_refs(manifest.get("external_refs"), errors)
    if "design" in manifest:
        _validate_manifest_design(manifest.get("design"), manifest.get("design_hash"), errors)
    _validate_manifest_nodes(manifest, errors)
    artifact_file = _validate_manifest_artifact_file(manifest, errors)

    if artifact_file == "ViewSpecView.tsx":
        if html_path.exists():
            errors.append("react_tsx artifact directory must not contain index.html")
        react_path = artifact_path / artifact_file
        if react_path.exists():
            tsx = react_path.read_text(encoding="utf-8")
            artifact_hash = file_hash(react_path)
            if manifest.get("artifact_hash") and manifest.get("artifact_hash") != artifact_hash:
                errors.append("artifact_hash does not match ViewSpecView.tsx")
            errors.extend(_validate_react_tsx_source(tsx, has_action_nodes=_manifest_has_action_nodes(manifest)))
        else:
            errors.append("missing ViewSpecView.tsx")
    elif html_path.exists():
        html = html_path.read_text(encoding="utf-8")
        artifact_hash = file_hash(html_path)
        if manifest.get("artifact_hash") and manifest.get("artifact_hash") != artifact_hash:
            errors.append("artifact_hash does not match index.html")
        lowered = html.lower()
        errors.extend(_validate_no_autofetch_surfaces(html))
        scripts = re.findall(r"<script\b[^>]*>[\s\S]*?</script>", html, flags=re.IGNORECASE)
        if manifest.get("kind") == "raw_html_compile" and scripts:
            errors.append("index.html contains an active raw-HTML surface")
        elif scripts and [script.strip() for script in scripts] != [ACTION_EVENT_SCRIPT]:
            errors.append("index.html contains an unknown inline script")
        elif scripts and not _manifest_has_action_nodes(manifest):
            errors.append("index.html contains an action runtime script without action nodes")
        if any(marker in lowered for marker in ("@import", "url(")):
            errors.append("index.html contains an active or auto-fetching surface")
        errors.extend(_validate_manifest_dom_links(manifest, html))
    else:
        errors.append("missing index.html")

    if diagnostics_path.exists():
        try:
            diagnostics_file = json.loads(diagnostics_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"invalid diagnostics.json: {exc}")
        else:
            if manifest.get("diagnostics") != diagnostics_file:
                warnings.append("diagnostics.json differs from manifest diagnostics")
    else:
        errors.append("missing diagnostics.json")

    return {"ok": not errors, "errors": errors, "warnings": warnings}


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
            metadata={**path_policy_metadata(root, allow_outside_cwd), "warnings": result["warnings"]},
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
        changed = [item for item in result["files"] if item["action"] != "unchanged"]
        return tool_response(
            True,
            "Exported local agent contract assets." if not dry_run else "Planned local agent contract asset export.",
            paths=paths,
            data={"assets": result},
            next_actions=[
                "Verify .viewspec/agent-assets.json when reusing exported assets.",
                "Point schema-aware editors or agents at .viewspec/agent-intent-bundle.schema.json.",
                "Use .viewspec/agent-system-prompt.txt as the local ViewSpec agent contract prompt.",
                "Use .viewspec/agent-intent-example.dashboard.json as a valid wire-shape example.",
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


def resolve_cwd(cwd: str | Path | None = None) -> Path:
    root = Path.cwd() if cwd is None else Path(cwd)
    try:
        return root.resolve(strict=True)
    except OSError as exc:
        raise LocalToolError("INVALID_PATH", f"Invalid cwd: {root}", "Choose an existing local working directory.") from exc


def resolve_local_path(
    value: str | Path,
    *,
    cwd: Path,
    allow_outside_cwd: bool = False,
    must_exist: bool = False,
) -> Path:
    raw = str(value)
    if not raw or "\x00" in raw:
        raise LocalToolError("INVALID_PATH", "Path is empty or contains a null byte.", "Pass a normal local filesystem path.")
    if raw.lower().startswith("file:") or "://" in raw:
        raise LocalToolError("INVALID_PATH", f"URLs are not local paths: {raw}", "Pass a local path under the MCP cwd.")
    candidate = Path(raw)
    if os.name == "nt" and candidate.drive and not candidate.is_absolute():
        raise LocalToolError(
            "INVALID_PATH",
            f"Windows drive-relative paths are not allowed: {raw}",
            "Pass a normal relative path under the MCP cwd or a fully qualified absolute path.",
        )
    if os.name == "nt" and candidate.root and not candidate.is_absolute():
        raise LocalToolError(
            "INVALID_PATH",
            f"Windows rooted paths without a drive are not allowed: {raw}",
            "Pass a normal relative path under the MCP cwd or a fully qualified absolute path.",
        )
    path = candidate if candidate.is_absolute() else cwd / candidate
    try:
        resolved = path.resolve(strict=must_exist)
    except FileNotFoundError as exc:
        raise LocalToolError("INVALID_PATH", f"Path does not exist: {raw}", "Pass an existing local file or directory.") from exc
    except OSError as exc:
        raise LocalToolError("INVALID_PATH", f"Invalid path: {raw}", "Pass a valid local filesystem path.") from exc
    if not allow_outside_cwd and not _is_relative_to(resolved, cwd):
        raise LocalToolError("PATH_OUTSIDE_CWD", f"Path resolves outside MCP cwd: {raw}", "Move the file under the cwd or restart with --allow-outside-cwd.")
    return resolved


def ensure_no_input_overwrite(input_path: Path | None, out_dir: Path, output_names: tuple[str, ...]) -> None:
    if input_path is None:
        return
    input_resolved = input_path.resolve()
    out_resolved = out_dir.resolve()
    for name in output_names:
        if input_resolved == (out_resolved / name).resolve():
            raise ValueError(f"Refusing to overwrite input file with output {name}")


def source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def bytes_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_hash(path: Path) -> str:
    return bytes_hash(path.read_bytes())


def looks_absolute_path_arg(value: str) -> bool:
    candidate = value.split("=", 1)[1] if value.startswith("--") and "=" in value else value
    return Path(candidate).is_absolute() or bool(ABSOLUTE_PATH_ARG_RE.match(candidate))


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(text)
        Path(temp_name).replace(path)
    except Exception:
        if temp_name:
            Path(temp_name).unlink(missing_ok=True)
        raise


def _load_optional_design(
    design_path: str | Path | None,
    *,
    cwd: Path,
    allow_outside_cwd: bool,
) -> DesignSystemContext | None:
    if design_path is None:
        return None
    resolved = resolve_local_path(design_path, cwd=cwd, allow_outside_cwd=allow_outside_cwd, must_exist=True)
    return load_design_system(path=resolved)


def path_policy_metadata(cwd: Path | None, allow_outside_cwd: bool) -> dict[str, Any]:
    return {
        "cwd": str(cwd) if cwd is not None else None,
        "allow_outside_cwd": allow_outside_cwd,
        "sdk_version": __version__,
        "network_calls": "none",
    }


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _validate_manifest_envelope(manifest: dict[str, Any], errors: list[str]) -> None:
    kind = manifest.get("kind")
    if not isinstance(kind, str):
        errors.append("manifest kind must be a string")
        return
    expected = EXPECTED_MANIFEST_ENVELOPES.get(kind)
    if expected is None:
        allowed = ", ".join(sorted(EXPECTED_MANIFEST_ENVELOPES))
        errors.append(f"manifest kind must be one of: {allowed}")
        return
    for field in ("command", "policy_version"):
        if manifest.get(field) != expected[field]:
            errors.append(f"manifest {field} must be {expected[field]} for {kind}")
    guarantees = manifest.get("guarantees")
    if isinstance(guarantees, dict) and guarantees.get("decompilation") != expected["decompilation"]:
        errors.append(f"manifest guarantees.decompilation must be {expected['decompilation']} for {kind}")


def _validate_manifest_artifact_file(manifest: dict[str, Any], errors: list[str]) -> str:
    kind = manifest.get("kind")
    emitter = manifest.get("emitter")
    artifact_file = manifest.get("artifact_file")

    if emitter is not None:
        if not isinstance(emitter, str):
            errors.append("manifest emitter must be a string")
            emitter = None
        elif emitter not in KNOWN_EMITTERS:
            allowed = ", ".join(sorted(KNOWN_EMITTERS))
            errors.append(f"manifest emitter must be one of: {allowed}")
            emitter = None
    if artifact_file is not None:
        if not isinstance(artifact_file, str) or not artifact_file:
            errors.append("manifest artifact_file must be a non-empty string")
            artifact_file = None
        elif artifact_file in {".", ".."} or "/" in artifact_file or "\\" in artifact_file or ":" in artifact_file:
            errors.append("manifest artifact_file must be a simple file name")
            artifact_file = None

    if kind == "raw_html_compile":
        if emitter is not None:
            errors.append("raw_html_compile manifest must not declare an emitter")
        if artifact_file is not None and artifact_file != "index.html":
            errors.append("manifest artifact_file must be index.html for raw_html_compile")
        return "index.html"

    if kind == "intent_bundle_compile":
        resolved_emitter = emitter or "html_tailwind"
        expected = EMITTER_ARTIFACT_FILES.get(resolved_emitter, "index.html")
        if artifact_file is None and resolved_emitter == "react_tsx":
            errors.append("manifest artifact_file must be ViewSpecView.tsx for react_tsx emitter")
        elif artifact_file is not None and artifact_file != expected:
            errors.append(f"manifest artifact_file must be {expected} for {resolved_emitter} emitter")
        return expected

    return "index.html"


def _validate_react_tsx_source(tsx: str, *, has_action_nodes: bool = False) -> list[str]:
    errors: list[str] = []
    for marker, message in REACT_TSX_REQUIRED_MARKERS.items():
        if marker not in tsx:
            errors.append(message)
    if has_action_nodes:
        for marker, message in REACT_TSX_ACTION_REQUIRED_MARKERS.items():
            if marker not in tsx:
                errors.append(message)
    code = _strip_tsx_literals_and_comments(tsx)
    for pattern, message in REACT_TSX_FORBIDDEN_SURFACES:
        if pattern.search(code):
            errors.append(message)
    return errors


def _strip_tsx_literals_and_comments(source: str) -> str:
    output: list[str] = []
    index = 0
    state: str | None = None
    escaped = False
    while index < len(source):
        char = source[index]
        peek = source[index : index + 2]
        if state in {"'", '"', "`"}:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == state:
                state = None
            output.append("\n" if char == "\n" else " ")
            index += 1
            continue
        if state == "//":
            if char == "\n":
                state = None
                output.append("\n")
            else:
                output.append(" ")
            index += 1
            continue
        if state == "/*":
            if peek == "*/":
                state = None
                output.append("  ")
                index += 2
            else:
                output.append("\n" if char == "\n" else " ")
                index += 1
            continue
        if peek in {"//", "/*"}:
            state = peek
            output.append("  ")
            index += 2
            continue
        if char in {"'", '"', "`"}:
            state = char
            output.append(" ")
            index += 1
            continue
        output.append(char)
        index += 1
    return "".join(output)


def _validate_manifest_diagnostics(value: Any, errors: list[str], *, label: str = "manifest diagnostics") -> None:
    if not isinstance(value, list):
        errors.append(f"{label} must be a list")
        return
    for index, item in enumerate(value):
        if not isinstance(item, dict) or not {"severity", "code", "message"}.issubset(item):
            errors.append(f"{label}[{index}] must contain severity, code, and message")
            continue
        if item["severity"] not in DIAGNOSTIC_SEVERITIES:
            errors.append(f"{label}[{index}].severity must be one of: error, info, warning")
        for key in ("code", "message"):
            if not isinstance(item[key], str) or not item[key]:
                errors.append(f"{label}[{index}].{key} must be a non-empty string")
        for key in ("node_id", "path"):
            if key in item and not isinstance(item[key], str):
                errors.append(f"{label}[{index}].{key} must be a string")


def _validate_manifest_external_refs(value: Any, errors: list[str]) -> None:
    if not isinstance(value, list):
        errors.append("manifest external_refs must be a list")
        return
    for index, item in enumerate(value):
        if not isinstance(item, dict) or not {"kind", "attr", "url", "behavior"}.issubset(item):
            errors.append(f"manifest external_refs[{index}] must contain kind, attr, url, and behavior")
            continue
        kind = item["kind"]
        attr = item["attr"]
        behavior = item["behavior"]
        if (kind, attr, behavior) not in EXTERNAL_REF_POLICIES:
            errors.append(f"manifest external_refs[{index}] must use an allowed inert external-ref policy")
        url = item["url"]
        if not isinstance(url, str) or not _is_remote_http_url(url):
            errors.append(f"manifest external_refs[{index}].url must be an http(s) URL")


def _validate_manifest_design(value: Any, design_hash: Any, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append("manifest design must be an object")
        return
    required = {
        "name",
        "design_hash",
        "lint_summary",
        "findings",
        "applied_tokens",
        "ignored_tokens",
        "dropped_tokens",
        "mode_defaults",
    }
    for key in sorted(required - set(value)):
        errors.append(f"manifest design missing {key}")
    if not isinstance(value.get("name"), str):
        errors.append("manifest design.name must be a string")
    nested_hash = value.get("design_hash")
    if not isinstance(nested_hash, str) or not HASH_RE.match(nested_hash):
        errors.append("manifest design.design_hash must be a sha256 hex string")
    elif design_hash != nested_hash:
        errors.append("manifest design.design_hash must match manifest design_hash")
    summary = value.get("lint_summary")
    if not isinstance(summary, dict):
        errors.append("manifest design.lint_summary must be an object")
    else:
        for key in ("errors", "warnings", "info"):
            if not isinstance(summary.get(key), int) or isinstance(summary.get(key), bool) or summary.get(key) < 0:
                errors.append(f"manifest design.lint_summary.{key} must be a non-negative integer")
    findings = value.get("findings")
    if not isinstance(findings, list):
        errors.append("manifest design.findings must be a list")
    else:
        _validate_manifest_diagnostics(findings, errors, label="manifest design.findings")
    applied_tokens = value.get("applied_tokens")
    if not isinstance(applied_tokens, dict) or not all(isinstance(key, str) and _is_string_list(item) for key, item in applied_tokens.items()):
        errors.append("manifest design.applied_tokens must be an object of string arrays")
    for key in ("ignored_tokens", "dropped_tokens", "mode_defaults"):
        if not _is_string_list(value.get(key)):
            errors.append(f"manifest design.{key} must be a list of strings")


def _validate_manifest_nodes(manifest: dict[str, Any], errors: list[str]) -> None:
    nodes = manifest.get("nodes")
    if not isinstance(nodes, dict):
        errors.append("manifest nodes must be an object")
        return
    kind = manifest.get("kind")
    for node_id, entry in sorted(nodes.items()):
        if not node_id or not SAFE_ID_RE.match(node_id):
            errors.append(f"manifest nodes.{node_id} key must be a safe id")
        if not isinstance(entry, dict):
            errors.append(f"manifest nodes.{node_id} must be an object")
            continue
        if kind == "intent_bundle_compile":
            _validate_intent_manifest_node(node_id, entry, errors)
        elif kind == "raw_html_compile":
            _validate_raw_html_manifest_node(node_id, entry, errors)


def _validate_intent_manifest_node(node_id: str, entry: dict[str, Any], errors: list[str]) -> None:
    ir_id = entry.get("ir_id")
    if not isinstance(ir_id, str) or not ir_id:
        errors.append(f"manifest nodes.{node_id}.ir_id must be a non-empty string")
    elif not SAFE_ID_RE.match(ir_id):
        errors.append(f"manifest nodes.{node_id}.ir_id must be a safe id")
    primitive = entry.get("primitive")
    if not isinstance(primitive, str) or not primitive:
        errors.append(f"manifest nodes.{node_id}.primitive must be a non-empty string")
    for key in ("content_refs", "intent_refs", "style_tokens"):
        if not _is_string_list(entry.get(key)):
            errors.append(f"manifest nodes.{node_id}.{key} must be a list of strings")
    content_refs = entry.get("content_refs")
    if _is_string_list(content_refs):
        if any(not CANONICAL_CONTENT_REF_RE.match(item) for item in content_refs):
            errors.append(f"manifest nodes.{node_id}.content_refs must contain only canonical content refs")
    intent_refs = entry.get("intent_refs")
    if _is_string_list(intent_refs):
        if not intent_refs:
            errors.append(f"manifest nodes.{node_id}.intent_refs must not be empty")
        if any(not VIEWSPEC_INTENT_REF_RE.match(item) for item in intent_refs):
            errors.append(f"manifest nodes.{node_id}.intent_refs must contain only ViewSpec intent refs")
    if not isinstance(entry.get("props"), dict):
        errors.append(f"manifest nodes.{node_id}.props must be an object")
        return
    props = entry["props"]
    binding_id = props.get("binding_id")
    if binding_id is not None:
        if not isinstance(binding_id, str) or not binding_id:
            errors.append(f"manifest nodes.{node_id}.props.binding_id must be a non-empty string")
        elif not SAFE_ID_RE.match(binding_id):
            errors.append(f"manifest nodes.{node_id}.props.binding_id must be a safe id")
        else:
            if _is_string_list(intent_refs) and f"viewspec:binding:{binding_id}" not in intent_refs:
                errors.append(f"manifest nodes.{node_id}.intent_refs must include viewspec:binding:{binding_id}")
            if _is_string_list(content_refs) and not content_refs:
                errors.append(f"manifest nodes.{node_id}.content_refs must not be empty for binding_id {binding_id}")
    action_id = props.get("action_id")
    if entry.get("primitive") == "button" or action_id is not None:
        if not isinstance(action_id, str) or not action_id:
            errors.append(f"manifest nodes.{node_id}.props.action_id must be a non-empty string")
        elif not SAFE_ID_RE.match(action_id):
            errors.append(f"manifest nodes.{node_id}.props.action_id must be a safe id")
        elif _is_string_list(intent_refs) and f"viewspec:action:{action_id}" not in intent_refs:
            errors.append(f"manifest nodes.{node_id}.intent_refs must include viewspec:action:{action_id}")
        action_kind = props.get("action_kind")
        if not isinstance(action_kind, str) or not action_kind:
            errors.append(f"manifest nodes.{node_id}.props.action_kind must be a non-empty string")
        payload_bindings = props.get("payload_bindings")
        if not _is_string_list(payload_bindings):
            errors.append(f"manifest nodes.{node_id}.props.payload_bindings must be a list of strings")
        elif any(not SAFE_ID_RE.match(item) for item in payload_bindings):
            errors.append(f"manifest nodes.{node_id}.props.payload_bindings must contain only safe ids")
        target_ref = props.get("target_ref")
        if target_ref not in (None, "") and (not isinstance(target_ref, str) or not ACTION_TARGET_REF_RE.match(target_ref)):
            errors.append(f"manifest nodes.{node_id}.props.target_ref must be region:id, binding:id, motif:id, or view:id")
    detail_role = props.get("detail_role")
    if detail_role is not None and detail_role not in {"term", "description"}:
        errors.append(f"manifest nodes.{node_id}.props.detail_role must be term or description")
    empty_state_role = props.get("empty_state_role")
    if empty_state_role is not None and empty_state_role not in {"title", "description", "detail"}:
        errors.append(f"manifest nodes.{node_id}.props.empty_state_role must be title, description, or detail")
    hero_role = props.get("hero_role")
    if hero_role is not None and hero_role not in {"eyebrow", "title", "description", "detail"}:
        errors.append(f"manifest nodes.{node_id}.props.hero_role must be eyebrow, title, description, or detail")


def _validate_raw_html_manifest_node(node_id: str, entry: dict[str, Any], errors: list[str]) -> None:
    if not isinstance(entry.get("tag"), str) or not entry.get("tag"):
        errors.append(f"manifest nodes.{node_id}.tag must be a non-empty string")
    attrs = entry.get("attrs")
    if not isinstance(attrs, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in attrs.items()):
        errors.append(f"manifest nodes.{node_id}.attrs must be an object of strings")
    if not isinstance(entry.get("text"), str):
        errors.append(f"manifest nodes.{node_id}.text must be a string")


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _is_remote_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def _contains_remote_http_reference(value: str) -> bool:
    return bool(re.search(r"(?i)(?:https?:)?//", value))


def _validate_no_autofetch_surfaces(html: str) -> list[str]:
    probe = _AutofetchSurfaceProbe()
    probe.feed(html)
    return probe.errors


class _AutofetchSurfaceProbe(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.errors: list[str] = []

    def _append_once(self, message: str) -> None:
        if message not in self.errors:
            self.errors.append(message)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_name = tag.lower()
        attr_map = {name.lower(): value or "" for name, value in attrs}
        if tag_name in ACTIVE_OR_AUTOFETCH_TAGS:
            self._append_once("index.html contains an active or auto-fetching surface")
        if tag_name in ACTIVE_STRUCTURAL_TAGS:
            self._append_once("index.html contains an active form surface")
        if tag_name == "meta" and attr_map.get("http-equiv", "").lower() == "refresh":
            self._append_once("index.html contains an active or auto-fetching surface")
        for attr_name, attr_value in attr_map.items():
            if attr_name in REMOTE_AUTOFETCH_ATTRS and _contains_remote_http_reference(attr_value):
                self._append_once("index.html contains an auto-fetching remote URL attribute")
            if tag_name in REMOTE_HREF_AUTOFETCH_TAGS and attr_name in {"href", "xlink:href"} and _contains_remote_http_reference(attr_value):
                self._append_once("index.html contains an auto-fetching remote URL attribute")


class _ArtifactDomProbe(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: dict[str, dict[str, str]] = {}
        self.node_ids: dict[str, dict[str, str]] = {}
        self.binding_ids: dict[str, str] = {}
        self.action_ids: dict[str, str] = {}
        self.data_ir_by_dom_id: dict[str, str] = {}
        self.data_ir_without_dom_id: list[str] = []
        self.text_by_node_id: dict[str, list[str]] = {}
        self._node_stack: list[str | None] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_name = tag.lower()
        attr_map = {name.lower(): value or "" for name, value in attrs}
        attr_map["__tag__"] = tag_name
        dom_id = attr_map.get("id")
        node_id = attr_map.get("data-viewspec-node-id")
        ir_id = attr_map.get("data-ir-id")
        binding_id = attr_map.get("data-binding-id")
        action_id = attr_map.get("data-action-id")

        if dom_id:
            if dom_id in self.ids:
                self.errors.append(f"index.html contains duplicate id {dom_id}")
            else:
                self.ids[dom_id] = attr_map
        if node_id:
            if node_id in self.node_ids:
                self.errors.append(f"index.html contains duplicate data-viewspec-node-id {node_id}")
            else:
                self.node_ids[node_id] = attr_map
        if ir_id:
            if dom_id:
                self.data_ir_by_dom_id[dom_id] = ir_id
            else:
                self.data_ir_without_dom_id.append(ir_id)
        if binding_id:
            if binding_id in self.binding_ids:
                self.errors.append(f"index.html contains duplicate data-binding-id {binding_id}")
            else:
                self.binding_ids[binding_id] = dom_id or ""
        if action_id:
            if action_id in self.action_ids:
                self.errors.append(f"index.html contains duplicate data-action-id {action_id}")
            else:
                self.action_ids[action_id] = dom_id or ""
        if tag_name not in VOID_HTML_TAGS:
            self._node_stack.append(node_id or dom_id or None)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        tag_name = tag.lower()
        if tag_name not in VOID_HTML_TAGS and self._node_stack:
            self._node_stack.pop()

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() not in VOID_HTML_TAGS and self._node_stack:
            self._node_stack.pop()

    def handle_data(self, data: str) -> None:
        if not self._node_stack:
            return
        node_id = self._node_stack[-1]
        if node_id is not None:
            self.text_by_node_id.setdefault(node_id, []).append(data)


def _validate_manifest_dom_links(manifest: dict[str, Any], html: str) -> list[str]:
    nodes = manifest.get("nodes")
    if not isinstance(nodes, dict):
        return []
    probe = _ArtifactDomProbe()
    probe.feed(html)
    probe.close()

    errors = list(probe.errors)
    if manifest.get("kind") == "intent_bundle_compile":
        errors.extend(_validate_intent_dom_links(nodes, probe))
    elif manifest.get("kind") == "raw_html_compile":
        errors.extend(_validate_raw_html_dom_links(nodes, probe))
    return errors


def _manifest_has_action_nodes(manifest: dict[str, Any]) -> bool:
    nodes = manifest.get("nodes")
    if not isinstance(nodes, dict):
        return False
    for entry in nodes.values():
        if not isinstance(entry, dict):
            continue
        props = entry.get("props")
        if entry.get("primitive") == "button" and isinstance(props, dict) and props.get("action_id"):
            return True
    return False


def _validate_intent_dom_links(nodes: dict[str, Any], probe: _ArtifactDomProbe) -> list[str]:
    errors: list[str] = []
    manifest_binding_ids: dict[str, str] = {}
    manifest_action_ids: dict[str, str] = {}
    for ir_id in probe.data_ir_without_dom_id:
        errors.append(f"DOM element with data-ir-id {ir_id} is missing an id")
    for dom_id, entry in sorted(nodes.items()):
        if not isinstance(entry, dict):
            errors.append(f"manifest nodes.{dom_id} must be an object")
            continue
        props = entry.get("props") if isinstance(entry.get("props"), dict) else {}
        binding_id = props.get("binding_id")
        if isinstance(binding_id, str) and binding_id:
            previous = manifest_binding_ids.setdefault(binding_id, dom_id)
            if previous != dom_id:
                errors.append(f"manifest nodes contain duplicate binding_id {binding_id}: {previous}, {dom_id}")
        action_id = props.get("action_id")
        if isinstance(action_id, str) and action_id:
            previous = manifest_action_ids.setdefault(action_id, dom_id)
            if previous != dom_id:
                errors.append(f"manifest nodes contain duplicate action_id {action_id}: {previous}, {dom_id}")
        attrs = probe.ids.get(dom_id)
        if attrs is None:
            errors.append(f"manifest node {dom_id} has no matching DOM id")
            continue
        ir_id = entry.get("ir_id")
        if isinstance(ir_id, str) and ir_id and attrs.get("data-ir-id") != ir_id:
            errors.append(f"manifest node {dom_id} ir_id does not match DOM data-ir-id")
        _compare_json_list_attr(errors, attrs, entry, dom_id, "data-content-refs", "content_refs")
        _compare_json_list_attr(errors, attrs, entry, dom_id, "data-intent-refs", "intent_refs")
        _compare_json_list_attr(errors, attrs, entry, dom_id, "data-style-tokens", "style_tokens")
        _validate_intent_semantic_attrs(errors, attrs, entry, dom_id, probe)
    for dom_id in sorted(probe.data_ir_by_dom_id):
        if dom_id not in nodes:
            errors.append(f"DOM element {dom_id} with data-ir-id is missing from manifest nodes")
    return errors


def _validate_raw_html_dom_links(nodes: dict[str, Any], probe: _ArtifactDomProbe) -> list[str]:
    errors: list[str] = []
    for node_id, entry in sorted(nodes.items()):
        if not isinstance(entry, dict):
            errors.append(f"manifest nodes.{node_id} must be an object")
            continue
        attrs = probe.node_ids.get(node_id)
        if attrs is None:
            errors.append(f"manifest node {node_id} has no matching data-viewspec-node-id")
            continue
        _validate_raw_html_dom_node(errors, node_id, entry, attrs, probe)
    for node_id in sorted(probe.node_ids):
        if node_id not in nodes:
            errors.append(f"DOM element {node_id} with data-viewspec-node-id is missing from manifest nodes")
    return errors


def _validate_raw_html_dom_node(
    errors: list[str],
    node_id: str,
    entry: dict[str, Any],
    attrs: dict[str, str],
    probe: _ArtifactDomProbe,
) -> None:
    manifest_tag = entry.get("tag")
    manifest_attrs = entry.get("attrs")
    if not isinstance(manifest_tag, str) or not isinstance(manifest_attrs, dict):
        return
    external_image_url = manifest_attrs.get("data-viewspec-external-src")
    expected_tag = "a" if manifest_tag == "img" and isinstance(external_image_url, str) else manifest_tag
    if attrs.get("__tag__") != expected_tag:
        errors.append(f"manifest node {node_id} tag does not match DOM tag")
    if isinstance(external_image_url, str):
        if attrs.get("href") != external_image_url:
            errors.append(f"manifest node {node_id} external image href does not match manifest attrs")
        return
    for attr_name, attr_value in sorted(manifest_attrs.items()):
        if not isinstance(attr_name, str) or not isinstance(attr_value, str):
            continue
        if attrs.get(attr_name) != attr_value:
            errors.append(f"manifest node {node_id} attr {attr_name} does not match DOM")
    manifest_text = entry.get("text")
    if isinstance(manifest_text, str):
        dom_text = _collapse_artifact_text(" ".join(probe.text_by_node_id.get(node_id, [])))[:160]
        if dom_text != manifest_text:
            errors.append(f"manifest node {node_id} text does not match DOM text")


def _compare_json_list_attr(
    errors: list[str],
    attrs: dict[str, str],
    entry: dict[str, Any],
    dom_id: str,
    attr_name: str,
    manifest_key: str,
) -> None:
    attr_value = attrs.get(attr_name)
    if attr_value is None:
        errors.append(f"DOM element {dom_id} missing {attr_name}")
        return
    try:
        parsed = json.loads(attr_value)
    except json.JSONDecodeError:
        errors.append(f"DOM element {dom_id} has invalid {attr_name} JSON")
        return
    manifest_value = entry.get(manifest_key)
    if parsed != manifest_value:
        errors.append(f"manifest node {dom_id} {manifest_key} does not match DOM {attr_name}")


def _collapse_artifact_text(value: str) -> str:
    return " ".join(value.split())


def _validate_intent_semantic_attrs(
    errors: list[str],
    attrs: dict[str, str],
    entry: dict[str, Any],
    dom_id: str,
    probe: _ArtifactDomProbe,
) -> None:
    primitive = entry.get("primitive")
    props = entry.get("props") if isinstance(entry.get("props"), dict) else {}
    tag = attrs.get("__tag__")
    _validate_intent_visible_text(errors, primitive, props, dom_id, probe)
    if props.get("binding_id") is not None and attrs.get("data-binding-id") != str(props["binding_id"]):
        errors.append(f"DOM element {dom_id} data-binding-id does not match manifest props")
    if primitive == "button":
        if tag != "button":
            errors.append(f"manifest node {dom_id} button primitive must render as <button>")
        if attrs.get("type") != "button":
            errors.append(f"DOM element {dom_id} button missing type=\"button\"")
        if props.get("action_id") and attrs.get("data-action-id") != str(props["action_id"]):
            errors.append(f"DOM element {dom_id} data-action-id does not match manifest props")
        if props.get("action_kind") and attrs.get("data-action-kind") != str(props["action_kind"]):
            errors.append(f"DOM element {dom_id} data-action-kind does not match manifest props")
        if attrs.get("data-action-target-ref", "") != str(props.get("target_ref", "")):
            errors.append(f"DOM element {dom_id} data-action-target-ref does not match manifest props")
        _compare_json_attr(errors, attrs, props, dom_id, "data-payload-bindings", "payload_bindings")
    elif primitive == "input":
        if tag != "input":
            errors.append(f"manifest node {dom_id} input primitive must render as <input>")
        if attrs.get("type") != "text":
            errors.append(f"DOM element {dom_id} input missing type=\"text\"")
        if attrs.get("value") != str(props.get("value", "")):
            errors.append(f"DOM element {dom_id} input value does not match manifest props")
        expected_label = str(props.get("aria_label", props.get("binding_id", "input")))
        if attrs.get("aria-label") != expected_label:
            errors.append(f"DOM element {dom_id} input aria-label does not match manifest props")
    elif primitive == "image_slot":
        _validate_image_like_attrs(errors, attrs, props, dom_id, "image_slot", "alt")
    elif primitive == "svg":
        _validate_image_like_attrs(errors, attrs, props, dom_id, "svg", "label")
    elif primitive == "error_boundary" and attrs.get("role") != "alert":
        errors.append(f"DOM element {dom_id} error_boundary missing role=\"alert\"")

    if props.get("motif_kind") == "list" and primitive == "stack" and tag != "ul":
        errors.append(f"manifest node {dom_id} list stack must render as <ul>")
    if props.get("motif_kind") == "list" and primitive == "surface" and tag != "li":
        errors.append(f"manifest node {dom_id} list surface must render as <li>")
    if props.get("motif_kind") == "form" and primitive == "stack":
        if tag != "section":
            errors.append(f"manifest node {dom_id} form stack must render as <section>")
        if attrs.get("role") != "form":
            errors.append(f"DOM element {dom_id} form stack missing role=\"form\"")
    if props.get("motif_kind") == "form" and primitive == "surface":
        if attrs.get("role") != "group":
            errors.append(f"DOM element {dom_id} form field missing role=\"group\"")
    if props.get("motif_kind") == "detail" and primitive == "stack" and tag != "dl":
        errors.append(f"manifest node {dom_id} detail stack must render as <dl>")
    if props.get("motif_kind") == "detail" and primitive == "cluster" and tag != "div":
        errors.append(f"manifest node {dom_id} detail row must render as <div>")
    detail_role = props.get("detail_role")
    if detail_role == "term":
        if tag != "dt":
            errors.append(f"manifest node {dom_id} detail term must render as <dt>")
    elif detail_role == "description":
        if tag != "dd":
            errors.append(f"manifest node {dom_id} detail description must render as <dd>")
    elif detail_role is not None:
        errors.append(f"manifest node {dom_id} detail_role must be term or description")
    if props.get("motif_kind") == "empty_state" and primitive == "surface":
        if tag != "section":
            errors.append(f"manifest node {dom_id} empty_state surface must render as <section>")
        if attrs.get("aria-label") != str(props.get("aria_label", "Empty state")):
            errors.append(f"DOM element {dom_id} empty_state surface aria-label does not match manifest props")
    empty_state_role = props.get("empty_state_role")
    if empty_state_role == "title":
        if tag != "h2":
            errors.append(f"manifest node {dom_id} empty_state title must render as <h2>")
    elif empty_state_role == "description":
        if tag != "p":
            errors.append(f"manifest node {dom_id} empty_state description must render as <p>")
    elif empty_state_role is not None and empty_state_role != "detail":
        errors.append(f"manifest node {dom_id} empty_state_role must be title, description, or detail")
    if props.get("motif_kind") == "hero" and primitive == "surface":
        if tag != "header":
            errors.append(f"manifest node {dom_id} hero surface must render as <header>")
        if attrs.get("aria-label") != str(props.get("aria_label", "Hero")):
            errors.append(f"DOM element {dom_id} hero surface aria-label does not match manifest props")
    hero_role = props.get("hero_role")
    if hero_role == "title":
        if tag != "h1":
            errors.append(f"manifest node {dom_id} hero title must render as <h1>")
    elif hero_role in {"description", "eyebrow"}:
        if tag != "p":
            errors.append(f"manifest node {dom_id} hero {hero_role} must render as <p>")
    elif hero_role is not None and hero_role != "detail":
        errors.append(f"manifest node {dom_id} hero_role must be eyebrow, title, description, or detail")
    if props.get("motif_kind") == "table" and primitive == "stack" and tag != "table":
        errors.append(f"manifest node {dom_id} table stack must render as <table>")
    if props.get("motif_kind") == "table" and primitive == "cluster" and tag != "tr":
        errors.append(f"manifest node {dom_id} table row must render as <tr>")
    table_cell_role = props.get("table_cell_role")
    if table_cell_role == "row_header":
        if tag != "th":
            errors.append(f"manifest node {dom_id} table row_header must render as <th>")
        if attrs.get("scope") != "row":
            errors.append(f"DOM element {dom_id} table row_header missing scope=\"row\"")
    elif table_cell_role == "cell":
        if tag != "td":
            errors.append(f"manifest node {dom_id} table cell must render as <td>")
    elif table_cell_role is not None:
        errors.append(f"manifest node {dom_id} table_cell_role must be row_header or cell")


def _validate_intent_visible_text(
    errors: list[str],
    primitive: Any,
    props: dict[str, Any],
    dom_id: str,
    probe: _ArtifactDomProbe,
) -> None:
    dom_text = _collapse_artifact_text(" ".join(probe.text_by_node_id.get(dom_id, [])))
    if primitive in TEXT_PROP_PRIMITIVES and "text" in props:
        if dom_text != str(props["text"]):
            errors.append(f"DOM element {dom_id} text does not match manifest props")
    elif primitive == "button" and "label" in props:
        if dom_text != str(props["label"]):
            errors.append(f"DOM element {dom_id} text does not match manifest props")


def _validate_image_like_attrs(
    errors: list[str],
    attrs: dict[str, str],
    props: dict[str, Any],
    dom_id: str,
    primitive: str,
    label_key: str,
) -> None:
    if attrs.get("role") != "img":
        errors.append(f"DOM element {dom_id} {primitive} missing role=\"img\"")
    label = str(props.get(label_key, primitive.replace("_", " ")))
    if attrs.get("aria-label") != label:
        errors.append(f"DOM element {dom_id} {primitive} aria-label does not match manifest props")


def _compare_json_attr(
    errors: list[str],
    attrs: dict[str, str],
    props: dict[str, Any],
    dom_id: str,
    attr_name: str,
    props_key: str,
) -> None:
    attr_value = attrs.get(attr_name)
    if attr_value is None:
        errors.append(f"DOM element {dom_id} missing {attr_name}")
        return
    try:
        parsed = json.loads(attr_value)
    except json.JSONDecodeError:
        errors.append(f"DOM element {dom_id} has invalid {attr_name} JSON")
        return
    if parsed != props.get(props_key, []):
        errors.append(f"DOM element {dom_id} {attr_name} does not match manifest props")


__all__ = [
    "ABSOLUTE_PATH_ARG_RE",
    "HASH_RE",
    "INTENT_BUNDLE_POLICY_VERSION",
    "MCP_RESULT_SCHEMA_VERSION",
    "STARTER_DESIGN",
    "LocalToolError",
    "atomic_write",
    "check_artifact_dir",
    "check_artifact_tool",
    "check_agent_assets_tool",
    "compile_html_file_tool",
    "diff_html_files_tool",
    "ensure_no_input_overwrite",
    "exception_response",
    "export_agent_assets_tool",
    "init_design_file",
    "init_design_tool",
    "lift_html_file_tool",
    "looks_absolute_path_arg",
    "path_policy_metadata",
    "resolve_cwd",
    "resolve_local_path",
    "source_hash",
    "bytes_hash",
    "file_hash",
    "tool_error_response",
    "tool_response",
]
