"""Local Node dependency layouts for network-free verification runtimes."""

from __future__ import annotations

from pathlib import Path


_WRITABLE_CACHE_DIRS = frozenset({".cache", ".vite", ".vite-temp"})


def materialize_prebuilt_node_modules(destination: Path, seed: Path) -> None:
    """Create a writable node_modules shell backed by an immutable dependency seed."""
    destination.mkdir()
    for source in sorted(seed.iterdir(), key=lambda path: path.name):
        target = destination / source.name
        if source.name in _WRITABLE_CACHE_DIRS:
            target.mkdir()
        elif source.name.startswith("@") and source.is_dir():
            target.mkdir()
            for package in sorted(source.iterdir(), key=lambda path: path.name):
                _link_dependency(package, target / package.name)
        else:
            _link_dependency(source, target)


def _link_dependency(source: Path, target: Path) -> None:
    target.symlink_to(source.resolve(), target_is_directory=source.is_dir())
