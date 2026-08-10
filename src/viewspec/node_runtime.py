"""Local Node dependency layouts for network-free verification runtimes."""

from __future__ import annotations

from pathlib import Path
import shutil


_WRITABLE_CACHE_DIRS = frozenset({".cache", ".vite", ".vite-temp"})


def materialize_prebuilt_node_modules(
    destination: Path,
    seed: Path,
    *,
    copy_packages: frozenset[str] = frozenset(),
) -> None:
    """Create a writable node_modules shell backed by an immutable dependency seed."""
    destination.mkdir()
    for source in sorted(seed.iterdir(), key=lambda path: path.name):
        target = destination / source.name
        if source.name in _WRITABLE_CACHE_DIRS:
            target.mkdir()
        elif source.name.startswith("@") and source.is_dir():
            target.mkdir()
            for package in sorted(source.iterdir(), key=lambda path: path.name):
                _materialize_dependency(
                    package,
                    target / package.name,
                    qualified_name=f"{source.name}/{package.name}",
                    copy_packages=copy_packages,
                )
        else:
            _materialize_dependency(
                source,
                target,
                qualified_name=source.name,
                copy_packages=copy_packages,
            )


def _materialize_dependency(
    source: Path,
    target: Path,
    *,
    qualified_name: str,
    copy_packages: frozenset[str],
) -> None:
    if qualified_name in copy_packages:
        if not source.is_dir() or source.is_symlink():
            raise ValueError(
                f"integrity-sensitive dependency seed must be a regular directory: {qualified_name}"
            )
        shutil.copytree(source, target, symlinks=False)
    else:
        _link_dependency(source, target)


def _link_dependency(source: Path, target: Path) -> None:
    target.symlink_to(source.resolve(), target_is_directory=source.is_dir())
