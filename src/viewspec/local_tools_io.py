from __future__ import annotations

import copy
import os
import tempfile
from collections.abc import Callable, Mapping
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
        raise LocalToolError(
            "INVALID_PATH", f"URLs are not local paths: {raw}", "Pass a local path under the working directory."
        )
    candidate = Path(raw)
    if os.name == "nt" and candidate.drive and not candidate.is_absolute():
        raise LocalToolError(
            "INVALID_PATH",
            f"Windows drive-relative paths are not allowed: {raw}",
            "Pass a normal relative path under the working directory or a fully qualified absolute path.",
        )
    if os.name == "nt" and candidate.root and not candidate.is_absolute():
        raise LocalToolError(
            "INVALID_PATH",
            f"Windows rooted paths without a drive are not allowed: {raw}",
            "Pass a normal relative path under the working directory or a fully qualified absolute path.",
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
            f"Path resolves outside the working directory: {raw}",
            "Move the file under the cwd or restart with --allow-outside-cwd.",
        )
    return resolved


# Ownership sentinel that viewspec prove writes into their output directory as soon as they
# create it, before any other file.  A run that stops before its report is written (an interrupt,
# a failed report write) still leaves a directory that ``force`` recognises as ViewSpec's.
# AppBundle compile output is a deployable site and does not get one.
VIEWSPEC_OUTPUT_SENTINEL = ".viewspec-output"
_VIEWSPEC_OUTPUT_SENTINEL_TEXT = '{"kind": "viewspec_proof_output", "schema_version": 1}\n'

# Top-level files that viewspec prove and compile write into their
# output directories. ``force`` may replace an existing directory only when it is
# empty or carries one of these markers, so a stray ``force=True`` cannot wipe an
# unrelated directory such as ``src/``.
VIEWSPEC_OUTPUT_MARKERS: frozenset[str] = frozenset(
    {
        VIEWSPEC_OUTPUT_SENTINEL,
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


def input_inside_output(output_dir: Path, root: Path, *inputs: str | Path | None) -> str | Path | None:
    """Return the first input that replacing ``output_dir`` would delete; relative inputs start at ``root``."""
    replaced = output_dir.resolve()
    for item in inputs:
        if item is not None and replaced in resolve_local_path(item, cwd=root, allow_outside_cwd=True).parents:
            return item
    return None


def write_output_sentinel(output_dir: Path) -> None:
    """Mark the freshly created ``output_dir`` as a ViewSpec proof directory.

    The sentinel is created exclusively, so it is always a new regular file and never follows a
    link that appeared in the directory.
    """
    with (output_dir / VIEWSPEC_OUTPUT_SENTINEL).open("x", encoding="utf-8", newline="") as handle:
        handle.write(_VIEWSPEC_OUTPUT_SENTINEL_TEXT)


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


# Report fields outside a ``paths`` mapping that record a path: ``(section, key)`` at the top level
# of a report.  The portable and absolute conversions below rewrite exactly these and the values of
# every ``paths`` mapping, so each is the inverse of the other.
REPORT_PATH_FIELDS: tuple[tuple[str, str], ...] = (("host_report", "artifact_dir"),)


def portable_path(value: str, base: str | Path) -> str:
    """Return ``value`` relative to ``base`` with POSIX separators.

    Only an absolute ``value`` is rewritten: ``base`` itself becomes ``"."`` and a path outside it
    climbs out with ``..``, computed lexically.  A relative ``value`` is already portable and keeps
    its spelling apart from the separators, so with an absolute ``base`` the result never depends on
    the current working directory.  A path on another Windows drive has no relative form and keeps
    its absolute spelling in POSIX form.  The empty string is returned unchanged.
    """
    if not value:
        return value
    if not os.path.isabs(value):
        return Path(value).as_posix()
    try:
        relative = os.path.relpath(value, str(base))
    except ValueError:
        return Path(value).as_posix()
    return Path(relative).as_posix()


def report_directory(report: Mapping[str, Any]) -> str | None:
    """Return the directory that holds the report file named by ``report["paths"]["report"]``.

    ``proof_report.json`` and ``app_proof_report.json`` record every path relative to this
    directory.  With the default report location it is the proof directory.  A report that names no
    report file falls back to ``paths.proof_dir``; ``None`` when it names neither.
    """
    paths = report.get("paths")
    if not isinstance(paths, Mapping):
        return None
    report_path = paths.get("report")
    if isinstance(report_path, str) and report_path:
        return os.path.dirname(report_path)
    proof_dir = paths.get("proof_dir")
    return proof_dir if isinstance(proof_dir, str) and proof_dir else None


def portable_report_paths(report: Mapping[str, Any], base: str | Path) -> dict[str, Any]:
    """Deep-copy ``report`` with its recorded paths rewritten relative to the absolute ``base``.

    Proof reports record where their files were written.  Written as absolute paths, two proofs of
    the same input in different directories, or one proof after a move, would differ and so would
    every hash over them.  The copy rewrites, through :func:`portable_path`, each string value under
    a key named ``paths`` (at any depth) and each field in :data:`REPORT_PATH_FIELDS`; ``None`` and
    non-string values are kept.  A relative ``base`` (such as the ``"."`` an on-disk report records
    as its ``proof_dir``) means the report is portable already: the copy is returned unchanged, so a
    document read back from disk converts to itself from any working directory.
    """
    if not os.path.isabs(str(base)):
        return copy.deepcopy(dict(report))
    return _rewrite_report_paths(report, lambda value: portable_path(value, base))


def absolute_path(value: str, base: str | Path) -> str:
    """Return ``value`` joined onto the resolved ``base`` when it is relative; an absolute ``value`` is kept.

    This is the inverse of :func:`portable_path` for the paths a proof report records: ``"."``
    becomes ``base`` itself and ``../x`` climbs out of it.  ``base`` is resolved first, as the
    paths were when the report was written, so a proof directory reached through a symlink gives the
    same absolute paths as its real location.  The empty string is returned unchanged.
    """
    return _join_resolved(value, os.path.realpath(str(base)))


def absolute_report_paths(report: Mapping[str, Any], base: str | Path) -> dict[str, Any]:
    """Deep-copy ``report`` with its recorded paths resolved against ``base``.

    ``proof_report.json`` and ``app_proof_report.json`` record their paths relative to the directory
    that holds the report file (see :func:`report_directory`); with the default report location that
    is the proof directory.  A reader that loads one of those files passes that directory as
    ``base`` and gets back the absolute paths the in-memory report carried, so the result can be
    used exactly like the dict ``prove()`` or ``prove_app()`` returned.  It rewrites exactly the
    fields :func:`portable_report_paths` rewrites.
    """
    resolved = os.path.realpath(str(base))
    return _rewrite_report_paths(report, lambda value: _join_resolved(value, resolved))


def _join_resolved(value: str, resolved_base: str) -> str:
    if not value or os.path.isabs(value):
        return value
    return os.path.normpath(os.path.join(resolved_base, value))


def _rewrite_report_paths(report: Mapping[str, Any], rewrite: Callable[[str], str]) -> dict[str, Any]:
    # Every dict and list is rebuilt, so the fields below are set on the copy, never on ``report``.
    rewritten = _rewrite_paths_mappings(dict(report), rewrite)
    for section, key in REPORT_PATH_FIELDS:
        holder = rewritten.get(section)
        if isinstance(holder, dict) and isinstance(holder.get(key), str):
            holder[key] = rewrite(holder[key])
    return rewritten


def _rewrite_paths_mappings(value: Any, rewrite: Callable[[str], str]) -> Any:
    if isinstance(value, dict):
        return {
            key: {name: rewrite(item) if isinstance(item, str) else item for name, item in entry.items()}
            if key == "paths" and isinstance(entry, dict)
            else _rewrite_paths_mappings(entry, rewrite)
            for key, entry in value.items()
        }
    if isinstance(value, list):
        return [_rewrite_paths_mappings(item, rewrite) for item in value]
    return value


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
