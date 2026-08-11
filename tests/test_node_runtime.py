from __future__ import annotations

import tempfile
from pathlib import Path

from hypothesis import given, strategies as st

from viewspec.node_runtime import materialize_prebuilt_node_modules


@given(st.permutations(("playwright", "react", "vite")))
def test_prebuilt_node_modules_are_order_independent_and_keep_caches_writable(order) -> None:
    with tempfile.TemporaryDirectory(prefix="viewspec-node-runtime-") as temp_name:
        root = Path(temp_name)
        seed = root / "seed"
        seed.mkdir()
        for name in order:
            package = seed / name
            package.mkdir()
            package.joinpath("package.json").write_text("{}\n", encoding="utf-8")
        scoped = seed / "@viewspec" / "runtime"
        scoped.mkdir(parents=True)
        scoped.joinpath("package.json").write_text("{}\n", encoding="utf-8")
        for name in (".cache", ".vite", ".vite-temp"):
            seed.joinpath(name).mkdir()

        destination = root / "node_modules"
        materialize_prebuilt_node_modules(destination, seed)

        for name in order:
            assert destination.joinpath(name).is_symlink()
            assert destination.joinpath(name).resolve() == seed.joinpath(name).resolve()
        assert destination.joinpath("@viewspec").is_dir()
        assert not destination.joinpath("@viewspec").is_symlink()
        assert destination.joinpath("@viewspec", "runtime").is_symlink()
        for name in (".cache", ".vite", ".vite-temp"):
            cache = destination / name
            assert cache.is_dir()
            assert not cache.is_symlink()
            cache.joinpath("write-proof").write_text("ok\n", encoding="utf-8")


def test_integrity_sensitive_packages_are_copied_as_regular_trees(tmp_path) -> None:
    seed = tmp_path / "seed"
    pretext = seed / "@chenglou" / "pretext"
    freerange = seed / "@chenglou" / "freerange"
    typescript = seed / "typescript"
    vite = seed / "vite"
    for package in (pretext, freerange, typescript, vite):
        package.mkdir(parents=True)
        package.joinpath("package.json").write_text("{}\n", encoding="utf-8")

    destination = tmp_path / "node_modules"
    materialize_prebuilt_node_modules(
        destination,
        seed,
        copy_packages=frozenset(
            {"@chenglou/pretext", "@chenglou/freerange", "typescript"}
        ),
    )

    assert not destination.joinpath("@chenglou/pretext").is_symlink()
    assert not destination.joinpath("@chenglou/freerange").is_symlink()
    assert not destination.joinpath("typescript").is_symlink()
    assert destination.joinpath("vite").is_symlink()
