from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from viewspec._version import __version__
from viewspec.design_md import DesignSystemContext, load_design_system
from viewspec.local_tools_constants import ABSOLUTE_PATH_ARG_RE
from viewspec.local_tools_response import LocalToolError


def resolve_cwd(cwd: str | Path | None = None) -> Path:
    root = Path.cwd() if cwd is None else Path(cwd)
    try:
        return root.resolve(strict=True)
    except OSError as exc:
        raise LocalToolError(
            "INVALID_PATH", f"Invalid cwd: {root}", "Choose an existing local working directory."
        ) from exc


def resolve_local_path(
    value: str | Path,
    *,
    cwd: Path,
    allow_outside_cwd: bool = False,
    must_exist: bool = False,
) -> Path:
    raw = str(value)
    if not raw or "\x00" in raw:
        raise LocalToolError(
            "INVALID_PATH", "Path is empty or contains a null byte.", "Pass a normal local filesystem path."
        )
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
        raise LocalToolError(
            "INVALID_PATH", f"Path does not exist: {raw}", "Pass an existing local file or directory."
        ) from exc
    except OSError as exc:
        raise LocalToolError("INVALID_PATH", f"Invalid path: {raw}", "Pass a valid local filesystem path.") from exc
    if not allow_outside_cwd and not _is_relative_to(resolved, cwd):
        raise LocalToolError(
            "PATH_OUTSIDE_CWD",
            f"Path resolves outside MCP cwd: {raw}",
            "Move the file under the cwd or restart with --allow-outside-cwd.",
        )
    return resolved


# Top-level files that prove, prove-app, compile, and compile-app write into their
# output directories. ``force`` may replace an existing directory only when it is
# empty or carries one of these markers, so a stray ``force=True`` cannot wipe an
# unrelated directory such as ``src/``.
VIEWSPEC_OUTPUT_MARKERS: frozenset[str] = frozenset(
    {
        "proof_report.json",
        "PROOF.md",
        "support_bundle.json",
        "app_proof_report.json",
        "APP_PROOF.md",
        "app_support_bundle.json",
        "shell_manifest.json",
        "provenance_manifest.json",
        "viewspec_app_manifest.json",
    }
)


def is_replaceable_output_dir(output_dir: Path) -> bool:
    """Return True when ``output_dir`` is empty or holds a file ViewSpec itself writes."""
    try:
        entries = list(output_dir.iterdir())
    except OSError:
        return False
    if not entries:
        return True
    return any(
        entry.name in VIEWSPEC_OUTPUT_MARKERS and entry.is_file() and not entry.is_symlink() for entry in entries
    )


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


def portable_path(value: str, base: str | Path) -> str:
    """Return ``value`` relative to ``base`` with POSIX separators.

    ``base`` itself becomes ``"."``.  A path on another Windows drive has no relative form and keeps
    its absolute spelling in POSIX form.  The empty string is returned unchanged.
    """
    if not value:
        return value
    try:
        relative = os.path.relpath(value, str(base))
    except ValueError:
        return Path(value).as_posix()
    return Path(relative).as_posix()


def portable_report_paths(report: Mapping[str, Any], base: str | Path) -> dict[str, Any]:
    """Deep-copy ``report`` with every ``paths`` mapping rewritten relative to ``base``.

    Proof reports record where their files were written.  Written as absolute paths, two proofs of
    the same input in different directories, or one proof after a move, would differ and so would
    every hash over them.  The copy rewrites each string value under a key named ``paths`` (at any
    depth) through :func:`portable_path`; ``None`` and non-string values are kept.  Applied to an
    already portable report with ``base`` ``"."`` it is the identity, so a document read back from
    disk hashes the same as the in-memory report it was written from.
    """
    return _portable_value(dict(report), str(base))


def _portable_value(value: Any, base: str) -> Any:
    if isinstance(value, dict):
        return {
            key: _portable_paths_mapping(item, base)
            if key == "paths" and isinstance(item, dict)
            else _portable_value(item, base)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_portable_value(item, base) for item in value]
    return value


def _portable_paths_mapping(paths: Mapping[str, Any], base: str) -> dict[str, Any]:
    return {key: portable_path(item, base) if isinstance(item, str) else item for key, item in paths.items()}


def absolute_path(value: str, base: str | Path) -> str:
    """Return ``value`` joined onto ``base`` when it is relative; an absolute ``value`` is kept.

    This is the inverse of :func:`portable_path` for the paths a proof report records: ``"."``
    becomes ``base`` itself and ``../x`` climbs out of it.  The empty string is returned unchanged.
    """
    if not value or os.path.isabs(value):
        return value
    return os.path.normpath(os.path.join(str(base), value))


def absolute_report_paths(report: Mapping[str, Any], base: str | Path) -> dict[str, Any]:
    """Deep-copy ``report`` with every ``paths`` mapping resolved against ``base``.

    ``proof_report.json`` and ``app_proof_report.json`` record their paths relative to the proof
    directory (see :func:`portable_report_paths`).  A reader that loads one of those files passes the
    proof directory as ``base`` and gets back the absolute paths the in-memory report carried, so
    the result can be used exactly like the dict ``prove()`` or ``prove_app()`` returned.
    """
    return _absolute_value(dict(report), str(base))


def _absolute_value(value: Any, base: str) -> Any:
    if isinstance(value, dict):
        return {
            key: _absolute_paths_mapping(item, base)
            if key == "paths" and isinstance(item, dict)
            else _absolute_value(item, base)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_absolute_value(item, base) for item in value]
    return value


def _absolute_paths_mapping(paths: Mapping[str, Any], base: str) -> dict[str, Any]:
    return {key: absolute_path(item, base) if isinstance(item, str) else item for key, item in paths.items()}


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
