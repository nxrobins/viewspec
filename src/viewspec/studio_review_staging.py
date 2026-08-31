"""Owned upload scratch space, never an arbitrary filesystem cleanup policy.

Call only while no creation is active (the deployment must hold its exclusive
storage lease during startup/reconciliation). The private ingress directory is
reserved entirely for SDK-created temporary uploads and extraction directories.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import stat

from viewspec.studio_share import STUDIO_SHARE_ARCHIVE_MAX_BYTES, STUDIO_SHARE_MAX_FILES

MAX_STAGING_ENTRIES = 64
MAX_STAGING_NODES = (STUDIO_SHARE_MAX_FILES + 2) * 4
MAX_STAGING_BYTES = STUDIO_SHARE_ARCHIVE_MAX_BYTES * 4
_UPLOAD = re.compile(r"\.upload-[a-z0-9_]{8}\.vsreview")
_DIRECTORY = re.compile(r"\.ingress-[a-z0-9_]{8}")


def _check(value: bool) -> None:
    if not value:
        raise ValueError("Private review ingress staging is ambiguous or outside its bound.")


def _private(path: Path, *, directory: bool) -> os.stat_result:
    value = path.lstat()
    _check((stat.S_ISDIR(value.st_mode) if directory else stat.S_ISREG(value.st_mode))
           and value.st_uid == os.getuid() and not value.st_mode & 0o077
           and (directory or value.st_nlink == 1))
    return value


def _identity(value: os.stat_result) -> tuple[int, ...]:
    return value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def prepare_staging(root: Path) -> Path:
    _check(root.is_absolute() and root.resolve() == root)
    _private(root, directory=True)
    staging = root / "ingress"
    if not staging.exists() and not staging.is_symlink():
        staging.mkdir(mode=0o700)
    _private(staging, directory=True)
    return staging


def plan_staging(root: Path) -> list[tuple[Path, tuple[int, ...]]]:
    """Preflight every remnant before allowing the caller to remove any of them."""
    staging = root / "ingress"
    _private(root, directory=True)
    _private(staging, directory=True)
    # Old releases wrote these outside the owned namespace. Never adopt or delete
    # them merely because their names resemble SDK scratch; require inspection.
    with os.scandir(root) as entries:
        for entry in entries:
            _check(not entry.name.startswith((".ingress-", ".upload-")))
    planned: list[tuple[Path, tuple[int, ...]]] = []
    pending: list[Path] = []
    total_bytes, nodes = 0, 0
    with os.scandir(staging) as entries:
        for entry in entries:
            _check(len(planned) < MAX_STAGING_ENTRIES)
            path = Path(entry.path)
            directory = _DIRECTORY.fullmatch(entry.name) is not None
            _check(directory or _UPLOAD.fullmatch(entry.name) is not None)
            value = _private(path, directory=directory)
            planned.append((path, _identity(value)))
            if directory:
                pending.append(path)
            else:
                _check(value.st_size <= STUDIO_SHARE_ARCHIVE_MAX_BYTES)
                total_bytes += value.st_size
            nodes += 1
    while pending:
        directory = pending.pop()
        _private(directory, directory=True)
        with os.scandir(directory) as entries:
            for entry in entries:
                nodes += 1
                _check(nodes <= MAX_STAGING_NODES)
                path = Path(entry.path)
                is_directory = entry.is_dir(follow_symlinks=False)
                value = _private(path, directory=is_directory)
                if is_directory:
                    pending.append(path)
                else:
                    total_bytes += value.st_size
                    _check(total_bytes <= MAX_STAGING_BYTES)
    _check(total_bytes <= MAX_STAGING_BYTES and nodes <= MAX_STAGING_NODES)
    return sorted(planned, key=lambda item: item[0].name)


def remove_staging(root: Path, selected: list[tuple[Path, tuple[int, ...]]]) -> None:
    # Recheck the whole tree before deletion; no partial cleanup of ambiguous
    # state. Directory removal must use the platform's symlink-resistant walker.
    current = dict(plan_staging(root))
    _check(all(current.get(path) == identity for path, identity in selected))
    _check(shutil.rmtree.avoids_symlink_attacks)
    for path, identity in selected:
        directory = stat.S_ISDIR(identity[2])
        _check(_identity(_private(path, directory=directory)) == identity)
        if directory:
            shutil.rmtree(path)
        else:
            path.unlink()
