"""Shared local tool helpers for CLI and native agent integrations."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from pathlib import Path
from typing import Any

from viewspec._version import __version__
from viewspec.design_md import DesignSystemContext, DesignSystemError, load_design_system
from viewspec.raw_html import (
    MANIFEST_SCHEMA_VERSION,
    HtmlInputError,
    compile_html,
    diff_html,
    lift_html,
    write_html_compile_result,
)


MCP_RESULT_SCHEMA_VERSION = 1
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
ABSOLUTE_PATH_ARG_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/]{1,2})")
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

    diagnostics = manifest.get("diagnostics")
    if isinstance(diagnostics, list):
        for index, item in enumerate(diagnostics):
            if not isinstance(item, dict) or not {"severity", "code", "message"}.issubset(item):
                errors.append(f"manifest diagnostics[{index}] must contain severity, code, and message")
    else:
        errors.append("manifest diagnostics must be a list")

    external_refs = manifest.get("external_refs")
    if isinstance(external_refs, list):
        for index, item in enumerate(external_refs):
            if not isinstance(item, dict) or not {"kind", "attr", "url", "behavior"}.issubset(item):
                errors.append(f"manifest external_refs[{index}] must contain kind, attr, url, and behavior")
    else:
        errors.append("manifest external_refs must be a list")

    if html_path.exists():
        html = html_path.read_text(encoding="utf-8")
        artifact_hash = source_hash(html)
        if manifest.get("artifact_hash") and manifest.get("artifact_hash") != artifact_hash:
            errors.append("artifact_hash does not match index.html")
        lowered = html.lower()
        if re.search(r'\s(?:src|poster|background|action)=["\']https?://', lowered):
            errors.append("index.html contains an auto-fetching remote URL attribute")
        if any(marker in lowered for marker in ("<script", "<iframe", "<embed", "<object", "<link", "@import", "url(")):
            errors.append("index.html contains an active or auto-fetching surface")
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
    try:
        root = resolve_cwd(cwd)
        source = resolve_local_path(input_path, cwd=root, allow_outside_cwd=allow_outside_cwd, must_exist=True)
        output = resolve_local_path(out_dir, cwd=root, allow_outside_cwd=allow_outside_cwd)
        design = _load_optional_design(design_path, cwd=root, allow_outside_cwd=allow_outside_cwd)
        ensure_no_input_overwrite(source, output, ("index.html", "provenance_manifest.json", "diagnostics.json", "lift.json"))
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
                metadata=_path_policy_metadata(root, allow_outside_cwd),
            )
        return tool_response(
            True,
            "Compiled and checked local HTML artifact.",
            diagnostics=result.diagnostics,
            external_refs=external_refs,
            paths=paths,
            next_actions=["Open the compiled index.html for review.", "Use diff_html_files before summarizing revisions."],
            metadata=_path_policy_metadata(root, allow_outside_cwd),
        )
    except Exception as exc:
        return exception_response(exc, "COMPILE_FAILED", "Fix the HTML, DESIGN.md, or path issue and retry compile_html_file.")


def check_artifact_tool(
    artifact_dir: str | Path,
    *,
    cwd: str | Path | None = None,
    allow_outside_cwd: bool = False,
) -> dict[str, Any]:
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
            metadata={**_path_policy_metadata(root, allow_outside_cwd), "warnings": result["warnings"]},
        )
    except Exception as exc:
        return exception_response(exc, "CHECK_FAILED", "Fix the artifact directory path and retry check_artifact.")


def diff_html_files_tool(
    left_path: str | Path,
    right_path: str | Path,
    *,
    cwd: str | Path | None = None,
    allow_outside_cwd: bool = False,
) -> dict[str, Any]:
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
            metadata=_path_policy_metadata(root, allow_outside_cwd),
        )
    except Exception as exc:
        return exception_response(exc, "DIFF_FAILED", "Fix the compared HTML paths and retry diff_html_files.")


def lift_html_file_tool(
    input_path: str | Path,
    out_path: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    allow_outside_cwd: bool = False,
) -> dict[str, Any]:
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
            metadata=_path_policy_metadata(root, allow_outside_cwd),
        )
    except Exception as exc:
        return exception_response(exc, "COMPILE_FAILED", "Fix the HTML path and retry lift_html_file.")


def init_design_tool(
    out: str | Path = "DESIGN.md",
    *,
    force: bool = False,
    cwd: str | Path | None = None,
    allow_outside_cwd: bool = False,
) -> dict[str, Any]:
    try:
        root = resolve_cwd(cwd)
        output = resolve_local_path(out, cwd=root, allow_outside_cwd=allow_outside_cwd)
        init_design_file(output, force=force)
        return tool_response(
            True,
            "Wrote starter DESIGN.md.",
            paths={"design": str(output)},
            next_actions=["Compile with --design DESIGN.md."],
            metadata=_path_policy_metadata(root, allow_outside_cwd),
        )
    except Exception as exc:
        return exception_response(exc, "IO_ERROR", "Choose a writable path, or pass force=True to replace an existing DESIGN.md.")


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


def exception_response(exc: Exception, fallback_code: str, fallback_fix: str) -> dict[str, Any]:
    if isinstance(exc, LocalToolError):
        return tool_error_response(exc.code, exc.message, exc.fix)
    if isinstance(exc, HtmlInputError):
        return tool_error_response(exc.code, str(exc), fallback_fix)
    if isinstance(exc, DesignSystemError):
        return tool_error_response("COMPILE_FAILED", str(exc), "Fix DESIGN.md and retry.")
    return tool_error_response(fallback_code, str(exc), fallback_fix)


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


def _path_policy_metadata(cwd: Path, allow_outside_cwd: bool) -> dict[str, Any]:
    return {
        "cwd": str(cwd),
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


__all__ = [
    "ABSOLUTE_PATH_ARG_RE",
    "HASH_RE",
    "MCP_RESULT_SCHEMA_VERSION",
    "STARTER_DESIGN",
    "LocalToolError",
    "atomic_write",
    "check_artifact_dir",
    "check_artifact_tool",
    "compile_html_file_tool",
    "diff_html_files_tool",
    "ensure_no_input_overwrite",
    "exception_response",
    "init_design_file",
    "init_design_tool",
    "lift_html_file_tool",
    "looks_absolute_path_arg",
    "resolve_cwd",
    "resolve_local_path",
    "source_hash",
    "tool_error_response",
    "tool_response",
]
