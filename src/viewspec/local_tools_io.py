from __future__ import annotations

from pathlib import Path
from typing import Any
from viewspec._version import __version__
from viewspec.design_md import DesignSystemContext
from viewspec.design_md import load_design_system
import os
import tempfile
from viewspec.local_tools_constants import (ABSOLUTE_PATH_ARG_RE)
from viewspec.local_tools_response import (LocalToolError)

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
    strict: bool = False,
) -> DesignSystemContext | None:
    if design_path is None:
        return None
    resolved = resolve_local_path(design_path, cwd=cwd, allow_outside_cwd=allow_outside_cwd, must_exist=True)
    return load_design_system(path=resolved, strict=strict)

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
