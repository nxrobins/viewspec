"""AppBundle output path safety helpers."""

from __future__ import annotations

import shutil
from pathlib import Path

from viewspec.app_errors import AppBundleProofFailure


def _prepare_app_output_dir(output_dir: Path, *, root: Path, force: bool, raw_out: str | Path) -> None:
    _assert_safe_app_output(output_dir, root=root, raw_out=raw_out)
    if output_dir.exists():
        if not force:
            raise AppBundleProofFailure(
                "APP_PROOF_OUTPUT_EXISTS",
                f"App proof output already exists: {output_dir}",
                "Pass --force or choose a new --out directory.",
            )
        if not output_dir.is_dir():
            raise AppBundleProofFailure(
                "APP_PROOF_OUTPUT_UNSAFE",
                f"App proof output is not a directory: {output_dir}",
                "Choose a dedicated app proof output directory.",
            )
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)


def _assert_safe_app_output(output_dir: Path, *, root: Path, raw_out: str | Path) -> None:
    raw_parts = [str(part) for part in Path(raw_out).parts]
    if ".." in raw_parts:
        raise AppBundleProofFailure(
            "APP_PROOF_OUTPUT_UNSAFE",
            "App proof output path must not contain parent traversal.",
            "Use a direct child output path.",
        )
    resolved = output_dir.resolve()
    home = Path.home().resolve()
    repo_root = _repo_root(root)
    drive_root = Path(resolved.anchor).resolve() if resolved.anchor else resolved
    blocked = {root.resolve(), repo_root, home, drive_root}
    if resolved in blocked:
        raise AppBundleProofFailure(
            "APP_PROOF_OUTPUT_UNSAFE",
            f"Refusing unsafe app proof output directory: {resolved}",
            "Use a dedicated app proof output directory such as .viewspec-app-proof.",
        )
    for parent in (root.resolve(), repo_root, home):
        if _is_parent(resolved, parent):
            raise AppBundleProofFailure(
                "APP_PROOF_OUTPUT_UNSAFE",
                f"Refusing app proof output that is a parent of a protected directory: {resolved}",
                "Use a dedicated child output directory.",
            )


def _assert_report_under_output(report_path: Path, output_dir: Path) -> None:
    if not _is_relative_to(report_path.resolve(), output_dir.resolve()):
        raise AppBundleProofFailure(
            "APP_PROOF_REPORT_PATH_UNSAFE",
            f"App proof report path must stay under proof root: {report_path}",
            "Write the proof report under the --out directory or omit --report-out.",
        )


def _should_write_app_proof_failure(output_dir: Path, code: str) -> bool:
    no_write_codes = {
        "APP_PROOF_OUTPUT_EXISTS",
        "APP_PROOF_OUTPUT_UNSAFE",
        "APP_PROOF_REPORT_PATH_UNSAFE",
    }
    return output_dir.exists() and code not in no_write_codes


def _prepare_app_shell_output_dir(output_dir: Path, *, root: Path, force: bool, raw_out: str | Path) -> None:
    _assert_safe_app_shell_output(output_dir, root=root, raw_out=raw_out)
    if output_dir.exists():
        if not force:
            raise AppBundleProofFailure(
                "APP_SHELL_OUTPUT_EXISTS",
                f"Static shell output already exists: {output_dir}",
                "Pass --force or choose a new --out directory.",
            )
        if not output_dir.is_dir():
            raise AppBundleProofFailure(
                "APP_SHELL_OUTPUT_PATH_UNSAFE",
                f"Static shell output is not a directory: {output_dir}",
                "Choose a dedicated app shell output directory.",
            )
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)


def _assert_safe_app_shell_output(output_dir: Path, *, root: Path, raw_out: str | Path) -> None:
    raw_parts = [str(part) for part in Path(raw_out).parts]
    if ".." in raw_parts:
        raise AppBundleProofFailure(
            "APP_SHELL_OUTPUT_PATH_UNSAFE",
            "Static shell output path must not contain parent traversal.",
            "Use a direct child output path.",
        )
    resolved = output_dir.resolve()
    home = Path.home().resolve()
    repo_root = _repo_root(root)
    drive_root = Path(resolved.anchor).resolve() if resolved.anchor else resolved
    blocked = {root.resolve(), repo_root, home, drive_root}
    if resolved in blocked:
        raise AppBundleProofFailure(
            "APP_SHELL_OUTPUT_PATH_UNSAFE",
            f"Refusing unsafe static shell output directory: {resolved}",
            "Use a dedicated app shell output directory such as app-dist.",
        )
    for parent in (root.resolve(), repo_root, home):
        if _is_parent(resolved, parent):
            raise AppBundleProofFailure(
                "APP_SHELL_OUTPUT_PATH_UNSAFE",
                f"Refusing static shell output that is a parent of a protected directory: {resolved}",
                "Use a dedicated child output directory.",
            )


def _assert_under_proof_root(path: Path, proof_root: Path) -> None:
    resolved = path.resolve()
    root = proof_root.resolve()
    if not _is_relative_to(resolved, root):
        raise AppBundleProofFailure(
            "APP_PROOF_OUTPUT_UNSAFE",
            f"Resolved proof path escaped proof root: {resolved}",
            "Use safe AppBundle ids and a dedicated proof output directory.",
        )


def _repo_root(root: Path) -> Path:
    current = root.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return current


def _is_parent(path: Path, child: Path) -> bool:
    try:
        child.relative_to(path)
    except ValueError:
        return False
    return path != child


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


__all__ = [
    "_assert_report_under_output",
    "_assert_under_proof_root",
    "_prepare_app_output_dir",
    "_prepare_app_shell_output_dir",
    "_should_write_app_proof_failure",
]
