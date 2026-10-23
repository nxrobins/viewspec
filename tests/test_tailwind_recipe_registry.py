"""The Tailwind React emitter reads its recipes from an explicit, read-only registry (audit Q-4).

Before this, every emit reassigned seven attributes on ``recipes.py`` from the package's names, so
that tests could swap recipes by monkeypatching the package. That shared state made concurrent
emits race, made the registry digest depend on what ran before it, and left wrappers with the same
names in two modules. These tests pin the replacement: a frozen registry passed down the call
chain, module attributes that emitting never writes, a digest that is a pure function of the
registry and covers every field of it, fixed grid classes that no registry can replace, and emits
with different registries that can run at the same time. ``viewspec check`` and ``prove`` read the
same import-time snapshot as a default emit, so the two paths cannot disagree.
"""

from __future__ import annotations

import copy
import dataclasses
import inspect
import json
import os
import pickle
import subprocess
import sys
import threading
import types
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

import viewspec.emitters.react_tailwind_tsx as react_tailwind
from viewspec import ViewSpecBuilder, compile
from viewspec.emitters.react_tailwind_tsx import (
    DEFAULT_TAILWIND_RECIPE_REGISTRY,
    CompilerConstraintError,
    ReactTailwindTsxEmitter,
    TailwindRecipeRegistry,
    emit_compiler_result,
    resolve_manifest_recipe_metadata,
    tailwind_recipe_registry_digest,
    tailwind_recipe_registry_projection,
)
from viewspec.emitters.react_tailwind_tsx import a11y as tailwind_a11y
from viewspec.emitters.react_tailwind_tsx import recipes as tailwind_recipes
from viewspec.intent_tools import compile_intent_bundle_file_tool, wrap_intent_bundle_manifest
from viewspec.local_tools import check_artifact_dir
from viewspec.local_tools_validators import _tailwind_allowed_class_tokens
from viewspec.prove import _a11y_report
from viewspec.types import IRNode

SRC = Path(__file__).resolve().parents[1] / "src"

# tailwind_recipe_registry_digest() on main before Q-4 (64e77eb). The default registry is built from
# the same checked-in tables, so the value must not move; a deliberate recipe change updates it here.
MAIN_REGISTRY_DIGEST = "a1916b7868b484feaa9e14a409416fb41dd2faf507614f4581dbd623a0bae83d"

# The tables the registry is built from: these are what emitting must leave alone.
_MODULE_TABLES = (
    "RECIPE_BY_KEY",
    "TAILWIND_AESTHETIC_RECIPE_OVERLAYS",
    "LAYOUT_EMPHASIS_CLASS_BY_VALUE",
    "GRID_CLASS_BY_COLUMNS",
    "GRID_SPAN_CLASS_BY_COLUMNS",
    "TAILWIND_APP_V1_APP_ROLE_CONTRACTS",
    "TAILWIND_PRODUCT_APP_ROLE_RULE_IDS",
    "TAILWIND_STRUCTURAL_APP_ROLE_RULE_IDS",
    "TAILWIND_APP_ROLE_RULE_PRECEDENCE",
)


def _ast():
    builder = ViewSpecBuilder("recipe_registry_probe")
    builder.set_aesthetic_profile("aesthetic.calm_ops")
    dashboard = builder.add_dashboard("metrics", region="main", group_id="cards")
    dashboard.add_card(label="Revenue", value="$2.4M", id="revenue")
    dashboard.add_card(label="Open risks", value="4", id="open_risks")
    return compile(builder.build_bundle())


def _grid_ast():
    """A dashboard whose profile lays the cards out on a two-column grid, the first spanning both."""
    builder = ViewSpecBuilder("grid_registry_probe")
    builder.set_aesthetic_profile("aesthetic.premium_saas")
    dashboard = builder.add_dashboard("metrics", region="main", group_id="cards")
    dashboard.add_card(label="Revenue", value="$2.4M", id="revenue")
    dashboard.add_card(label="Open risks", value="4", id="open_risks")
    return compile(builder.build_bundle())


def _marked_registry(marker: str) -> TailwindRecipeRegistry:
    """A valid registry whose every base recipe also carries ``marker``, a closed class token."""
    recipes = {key: f"{classes} {marker}" for key, classes in DEFAULT_TAILWIND_RECIPE_REGISTRY.recipes.items()}
    return dataclasses.replace(DEFAULT_TAILWIND_RECIPE_REGISTRY, recipes=recipes)


def _varied(value: object) -> object:
    """``value`` with one entry changed, for every kind of table a registry field holds."""
    if isinstance(value, str):
        return f"{value} accent-rose-500"
    if isinstance(value, tuple):
        return (*value, "accent-rose-500")
    if isinstance(value, Mapping):
        first = next(iter(value))
        return {**value, first: _varied(value[first])}
    raise AssertionError(f"no variant for a registry table of type {type(value).__name__}")


def _read_artifact(output: Path) -> tuple[str, str]:
    return (
        output.joinpath("ViewSpecView.tsx").read_text(encoding="utf-8"),
        output.joinpath("provenance_manifest.json").read_text(encoding="utf-8"),
    )


def _rich_builder() -> ViewSpecBuilder:
    """An executive_review page whose emit reaches every registry field.

    A hero (role recipes), a three-column dashboard whose first card is featured and spans two
    columns (product roles, layout emphasis, the grid classes and the profile overlay) and a table
    (structural roles and more role recipes). The profile's contrast facts differ from the base
    recipes' (minimum 7.58 against 4.76), so a proof that dropped the profile would show.
    """
    builder = ViewSpecBuilder("rich_registry_probe")
    builder.set_aesthetic_profile("aesthetic.executive_review")
    builder.add_hero("intro", title="Operations", eyebrow="Today", description="Deploy health")
    dashboard = builder.add_dashboard("metrics", region="main", group_id="cards")
    dashboard.add_card(label="Revenue", value="$2.4M", id="revenue")
    dashboard.add_card(label="Open risks", value="4", id="open_risks")
    table = builder.add_table("deploys", region="main")
    table.add_row(label="payments", value="failed", id="payments")
    table.add_row(label="search", value="ok", id="search")
    return builder


def _rich_ast():
    return compile(_rich_builder().build_bundle())


def _variant(**fields: object) -> TailwindRecipeRegistry:
    return dataclasses.replace(DEFAULT_TAILWIND_RECIPE_REGISTRY, **fields)


@contextmanager
def _record_module_writes(*modules: types.ModuleType):
    """Record every attribute assignment or deletion on ``modules`` while the block runs."""
    writes: list[tuple[str, str]] = []

    class RecordingModule(types.ModuleType):
        def __setattr__(self, name: str, value: object) -> None:
            writes.append((self.__name__, name))
            super().__setattr__(name, value)

        def __delattr__(self, name: str) -> None:
            writes.append((self.__name__, name))
            super().__delattr__(name)

    original_classes = [(module, type(module)) for module in modules]
    for module in modules:
        object.__setattr__(module, "__class__", RecordingModule)
    try:
        yield writes
    finally:
        for module, original in original_classes:
            object.__setattr__(module, "__class__", original)


def _module_snapshot() -> tuple[dict[str, dict[str, int]], dict[str, object]]:
    identities = {
        module.__name__: {name: id(value) for name, value in vars(module).items()}
        for module in (tailwind_recipes, react_tailwind, tailwind_a11y)
    }
    tables = {name: copy.deepcopy(getattr(tailwind_recipes, name)) for name in _MODULE_TABLES}
    return identities, tables


def _assert_modules_unchanged(before: tuple[dict[str, dict[str, int]], dict[str, object]]) -> None:
    identities, tables = before
    after_identities, after_tables = _module_snapshot()
    assert after_identities == identities
    assert after_tables == tables


def test_default_registry_digest_matches_main_and_the_module_tables():
    assert tailwind_recipe_registry_digest() == MAIN_REGISTRY_DIGEST
    assert tailwind_recipe_registry_digest(recipe_registry=DEFAULT_TAILWIND_RECIPE_REGISTRY) == MAIN_REGISTRY_DIGEST
    registry = DEFAULT_TAILWIND_RECIPE_REGISTRY
    assert dict(registry.recipes) == tailwind_recipes.RECIPE_BY_KEY
    assert {profile: dict(overlay) for profile, overlay in registry.aesthetic_overlays.items()} == (
        tailwind_recipes.TAILWIND_AESTHETIC_RECIPE_OVERLAYS
    )
    assert dict(registry.layout_emphasis_classes) == tailwind_recipes.LAYOUT_EMPHASIS_CLASS_BY_VALUE
    # The grid tables are fixed rather than registry fields; the resolver reads copies of them.
    assert dict(tailwind_recipes._GRID_CLASSES) == tailwind_recipes.GRID_CLASS_BY_COLUMNS
    assert dict(tailwind_recipes._GRID_SPAN_CLASSES) == tailwind_recipes.GRID_SPAN_CLASS_BY_COLUMNS
    assert {role: dict(contract) for role, contract in registry.app_role_contracts.items()} == (
        tailwind_recipes.TAILWIND_APP_V1_APP_ROLE_CONTRACTS
    )
    assert dict(registry.product_rule_ids) == tailwind_recipes.TAILWIND_PRODUCT_APP_ROLE_RULE_IDS
    assert dict(registry.structural_rule_ids) == tailwind_recipes.TAILWIND_STRUCTURAL_APP_ROLE_RULE_IDS
    assert registry.rule_precedence == tailwind_recipes.TAILWIND_APP_ROLE_RULE_PRECEDENCE


def test_registry_is_read_only_and_detached_from_the_tables_it_was_built_from():
    registry = DEFAULT_TAILWIND_RECIPE_REGISTRY
    with pytest.raises(dataclasses.FrozenInstanceError):
        registry.recipes = {}  # type: ignore[misc]
    with pytest.raises(TypeError):
        registry.recipes["primitive:text"] = "text-red-700"  # type: ignore[index]
    with pytest.raises(TypeError):
        registry.aesthetic_overlays["aesthetic.calm_ops"]["primitive:text"] = "text-red-700"  # type: ignore[index]
    with pytest.raises(TypeError):
        registry.app_role_contracts["app_shell"]["primitives"] = ("stack",)  # type: ignore[index]
    assert registry.app_role_contracts["app_shell"]["primitives"] == ("root",)
    assert registry.recipes is not tailwind_recipes.RECIPE_BY_KEY

    source_recipes = dict(registry.recipes)
    source_overlays = {profile: dict(overlay) for profile, overlay in registry.aesthetic_overlays.items()}
    variant = dataclasses.replace(registry, recipes=source_recipes, aesthetic_overlays=source_overlays)
    source_recipes["primitive:text"] = "text-red-700"
    source_overlays["aesthetic.calm_ops"]["primitive:text"] = "text-red-700"
    assert variant.recipes["primitive:text"] == registry.recipes["primitive:text"]
    calm_ops = "aesthetic.calm_ops"
    assert (
        variant.aesthetic_overlays[calm_ops]["primitive:text"]
        == registry.aesthetic_overlays[calm_ops]["primitive:text"]
    )
    with pytest.raises(TypeError):
        variant.recipes["primitive:text"] = "text-red-700"  # type: ignore[index]


def test_registry_freezes_contract_values_given_as_sets():
    # Review of Q-4: a contract value given as a set was kept by reference, so a caller who changed
    # the set afterwards changed a built registry, its digest and the roles it derives. A set is now
    # frozen into a sorted tuple, so a registry built from sets equals one built from the same tuples.
    contracts = {role: dict(contract) for role, contract in DEFAULT_TAILWIND_RECIPE_REGISTRY.app_role_contracts.items()}
    primitives = {"root"}
    contracts["app_shell"]["primitives"] = primitives
    contracts["app_header"]["primitives"] = frozenset({"surface", "stack"})
    contracts["tab_panel"]["motif_kinds"] = {"form", "detail"}
    registry = dataclasses.replace(DEFAULT_TAILWIND_RECIPE_REGISTRY, app_role_contracts=contracts)
    primitives.add("stack")

    assert registry.app_role_contracts["app_shell"]["primitives"] == ("root",)
    assert registry.app_role_contracts["app_header"]["primitives"] == ("stack", "surface")
    assert registry == DEFAULT_TAILWIND_RECIPE_REGISTRY
    assert tailwind_recipe_registry_digest(recipe_registry=registry) == MAIN_REGISTRY_DIGEST
    stack = IRNode(id="shell", primitive="stack", props={"product_role": "app_shell"})
    assert not tailwind_recipes._role_contract_matches(stack, "app_shell", registry)

    # Members that do not sort are still copied into a tuple of their own.
    mixed: set[object] = {"root", 1}
    contracts["app_shell"]["primitives"] = mixed
    registry = dataclasses.replace(DEFAULT_TAILWIND_RECIPE_REGISTRY, app_role_contracts=contracts)
    mixed.add("stack")
    frozen = registry.app_role_contracts["app_shell"]["primitives"]
    assert isinstance(frozen, tuple)
    assert set(frozen) == {"root", 1}


_SET_CONTRACTS_DIGEST_PROBE = r"""
import dataclasses
from viewspec.emitters.react_tailwind_tsx import DEFAULT_TAILWIND_RECIPE_REGISTRY, tailwind_recipe_registry_digest
contracts = {
    role: {key: set(values) for key, values in contract.items()}
    for role, contract in DEFAULT_TAILWIND_RECIPE_REGISTRY.app_role_contracts.items()
}
registry = dataclasses.replace(DEFAULT_TAILWIND_RECIPE_REGISTRY, app_role_contracts=contracts)
print(tailwind_recipe_registry_digest(recipe_registry=registry))
"""


def test_registry_built_from_set_contracts_has_one_digest_in_every_process():
    # A set of strings iterates in an order that depends on PYTHONHASHSEED. Frozen in sorted order,
    # contracts given as sets give the same digest in every process: the default one, since every
    # checked-in contract lists its members in sorted order.
    digests = set()
    for seed in ("0", "1", "2", "3"):
        completed = subprocess.run(
            [sys.executable, "-c", _SET_CONTRACTS_DIGEST_PROBE],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
            env={"PATH": os.defpath, "PYTHONPATH": str(SRC), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": seed},
        )
        assert completed.returncode == 0, completed.stderr
        digests.add(completed.stdout.strip())
    assert digests == {MAIN_REGISTRY_DIGEST}


def test_registries_and_emitters_holding_one_survive_pickle_and_copy(tmp_path):
    # The emitter held no state before; holding a registry must not stop it from being pickled
    # (for a process pool) or copied, and a restored registry is as read-only as the original.
    ast = _ast()
    marked = _marked_registry("accent-rose-500")
    for registry in (DEFAULT_TAILWIND_RECIPE_REGISTRY, marked):
        for restored in (pickle.loads(pickle.dumps(registry)), copy.copy(registry), copy.deepcopy(registry)):
            assert restored == registry
            assert tailwind_recipe_registry_digest(recipe_registry=restored) == tailwind_recipe_registry_digest(
                recipe_registry=registry
            )
            with pytest.raises(TypeError):
                restored.aesthetic_overlays["aesthetic.calm_ops"]["primitive:text"] = "text-red-700"  # type: ignore[index]
    for index, emitter in enumerate((ReactTailwindTsxEmitter(), ReactTailwindTsxEmitter(recipe_registry=marked))):
        emitter.emit(ast, tmp_path / f"original-{index}")
        for kind, clone in (("pickle", pickle.loads(pickle.dumps(emitter))), ("deepcopy", copy.deepcopy(emitter))):
            clone.emit(ast, tmp_path / f"{kind}-{index}")
            assert _read_artifact(tmp_path / f"{kind}-{index}") == _read_artifact(tmp_path / f"original-{index}")


def test_emitters_reject_a_recipe_registry_of_the_wrong_type(tmp_path):
    ast = _ast()
    with pytest.raises(TypeError, match="TailwindRecipeRegistry"):
        ReactTailwindTsxEmitter(recipe_registry=dict(DEFAULT_TAILWIND_RECIPE_REGISTRY.recipes))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="TailwindRecipeRegistry"):
        emit_compiler_result(
            ast.result,
            dict(ast.style_values),
            output_dir=tmp_path / "out",
            recipe_registry=tailwind_recipes,  # type: ignore[arg-type]
        )
    assert not (tmp_path / "out").exists()


def test_injected_registry_reaches_emitted_classes_and_the_manifest_check(tmp_path):
    ast = _ast()
    marked = _marked_registry("accent-rose-500")
    ReactTailwindTsxEmitter(recipe_registry=marked).emit(ast, tmp_path / "marked")
    ReactTailwindTsxEmitter().emit(ast, tmp_path / "default")
    marked_tsx, marked_manifest = _read_artifact(tmp_path / "marked")
    default_tsx, default_manifest = _read_artifact(tmp_path / "default")

    assert "accent-rose-500" in marked_tsx
    assert "accent-rose-500" not in default_tsx
    marked_nodes = json.loads(marked_manifest)
    default_nodes = json.loads(default_manifest)
    assert marked_nodes.keys() == default_nodes.keys()
    parent_ir_id: dict[str, str | None] = {}

    def walk(node, parent_id):
        parent_ir_id[node.id] = parent_id
        for child in node.children:
            walk(child, node.id)

    walk(ast.result.root.root, None)
    # The manifest check recomputes each node's emitted classes from the registry it is given.
    for dom_id, entry in marked_nodes.items():
        assert "accent-rose-500" in entry["classes"], dom_id
        parent_id = parent_ir_id[entry["ir_id"]]
        parent = marked_nodes[f"dom-{parent_id}"] if parent_id is not None else None
        recomputed = resolve_manifest_recipe_metadata(
            entry, parent, aesthetic_profile="aesthetic.calm_ops", recipe_registry=marked
        )
        assert recomputed["classes"] == entry["classes"], dom_id
        assert recomputed["recipe_key"] == entry["recipe_key"] == default_nodes[dom_id]["recipe_key"], dom_id
        default_recomputed = resolve_manifest_recipe_metadata(entry, parent, aesthetic_profile="aesthetic.calm_ops")
        assert default_recomputed["classes"] == default_nodes[dom_id]["classes"], dom_id
    assert tailwind_recipe_registry_digest(recipe_registry=marked) != MAIN_REGISTRY_DIGEST
    assert tailwind_recipe_registry_projection(recipe_registry=marked)["recipes"]["primitive:label"].endswith(
        " accent-rose-500"
    )


def test_emitting_with_the_default_registry_assigns_no_module_attributes(tmp_path):
    ast = _ast()
    before = _module_snapshot()
    with _record_module_writes(tailwind_recipes, react_tailwind, tailwind_a11y) as writes:
        ReactTailwindTsxEmitter().emit(ast, tmp_path / "plugin")
        emit_compiler_result(ast.result, dict(ast.style_values), output_dir=tmp_path / "function", title=ast.title)
        tailwind_recipe_registry_digest()
        tailwind_recipe_registry_projection()
        nodes = json.loads(tmp_path.joinpath("plugin", "provenance_manifest.json").read_text(encoding="utf-8"))
        for entry in nodes.values():
            resolve_manifest_recipe_metadata(entry, None, aesthetic_profile="aesthetic.calm_ops")
            tailwind_recipes.resolve_manifest_recipe_metadata(entry, None, aesthetic_profile="aesthetic.calm_ops")
        tailwind_recipes._resolve_recipes(ast.result.root.root)
        tailwind_recipes._validate_recipe_registry()
        tailwind_a11y.react_contrast_report("aesthetic.calm_ops")
    assert writes == []
    _assert_modules_unchanged(before)


def test_emitting_with_injected_registries_assigns_no_module_attributes(tmp_path):
    ast = _ast()
    marked = _marked_registry("accent-sky-500")
    broken = dataclasses.replace(DEFAULT_TAILWIND_RECIPE_REGISTRY, aesthetic_overlays={})
    before = _module_snapshot()
    with _record_module_writes(tailwind_recipes, react_tailwind, tailwind_a11y) as writes:
        ReactTailwindTsxEmitter(recipe_registry=marked).emit(ast, tmp_path / "marked")
        emit_compiler_result(
            ast.result, dict(ast.style_values), output_dir=tmp_path / "function", recipe_registry=marked
        )
        with pytest.raises(CompilerConstraintError) as exc_info:
            ReactTailwindTsxEmitter(recipe_registry=broken).emit(ast, tmp_path / "broken")
        tailwind_recipe_registry_digest(recipe_registry=marked)
        tailwind_a11y.react_contrast_report("aesthetic.calm_ops", recipe_registry=marked)
    assert exc_info.value.code == "TAILWIND_AESTHETIC_RECIPE_MISSING"
    assert not (tmp_path / "broken").exists()
    assert writes == []
    _assert_modules_unchanged(before)


def test_reassigned_package_tables_neither_reach_recipes_nor_change_the_output(tmp_path, monkeypatch):
    # Reassigning the package's re-exported names was the old test hook. It is no longer one: the
    # emitter reads its registry, and recipes.py keeps its own objects.
    ast = _ast()
    ReactTailwindTsxEmitter().emit(ast, tmp_path / "before")
    original_recipes = tailwind_recipes.RECIPE_BY_KEY
    original_overlays = tailwind_recipes.TAILWIND_AESTHETIC_RECIPE_OVERLAYS
    monkeypatch.setattr(react_tailwind, "TAILWIND_AESTHETIC_RECIPE_OVERLAYS", {})
    monkeypatch.setattr(
        react_tailwind,
        "RECIPE_BY_KEY",
        {key: f"{classes} accent-lime-500" for key, classes in original_recipes.items()},
    )

    ReactTailwindTsxEmitter().emit(ast, tmp_path / "after")

    assert tailwind_recipes.RECIPE_BY_KEY is original_recipes
    assert tailwind_recipes.TAILWIND_AESTHETIC_RECIPE_OVERLAYS is original_overlays
    assert _read_artifact(tmp_path / "after") == _read_artifact(tmp_path / "before")
    assert tailwind_recipe_registry_digest() == MAIN_REGISTRY_DIGEST


_FRESH_DIGEST_PROBE = r"""
from viewspec.emitters.react_tailwind_tsx import tailwind_recipe_registry_digest
print(tailwind_recipe_registry_digest())
"""


def test_tailwind_recipe_registry_digest_does_not_depend_on_call_order(tmp_path):
    # A fresh interpreter that computes the digest before anything else ran.
    fresh = subprocess.run(
        [sys.executable, "-c", _FRESH_DIGEST_PROBE],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
        env={"PATH": os.defpath, "PYTHONPATH": str(SRC), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert fresh.returncode == 0, fresh.stderr
    assert fresh.stdout.strip() == MAIN_REGISTRY_DIGEST

    ast = _ast()
    first = tailwind_recipe_registry_digest()
    assert first == MAIN_REGISTRY_DIGEST
    marked_recipes = {key: f"{classes} accent-amber-500" for key, classes in tailwind_recipes.RECIPE_BY_KEY.items()}
    # Names that other code left reassigned (a monkeypatch, a plugin) do not move it, before or
    # after an emit, and nothing of theirs is left behind once they are put back.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(react_tailwind, "RECIPE_BY_KEY", marked_recipes)
        patch.setattr(react_tailwind, "LAYOUT_EMPHASIS_CLASS_BY_VALUE", {"featured": "ring-4"})
        patch.setattr(tailwind_recipes, "TAILWIND_APP_ROLE_RULE_PRECEDENCE", ())
        assert tailwind_recipe_registry_digest() == first
        ReactTailwindTsxEmitter().emit(ast, tmp_path / "while-reassigned")
        assert tailwind_recipe_registry_digest() == first
    assert tailwind_recipe_registry_digest() == first
    # Neither do emits with another registry, whose own digest is a pure function of it.
    marked = dataclasses.replace(DEFAULT_TAILWIND_RECIPE_REGISTRY, recipes=marked_recipes)
    marked_digest = tailwind_recipe_registry_digest(recipe_registry=marked)
    ReactTailwindTsxEmitter(recipe_registry=marked).emit(ast, tmp_path / "marked")
    assert tailwind_recipe_registry_digest() == first
    assert marked_digest != first
    assert tailwind_recipe_registry_digest(recipe_registry=marked) == marked_digest
    assert tailwind_recipe_registry_digest() == MAIN_REGISTRY_DIGEST


def test_concurrent_emits_with_different_registries_do_not_interfere(tmp_path):
    ast = _ast()
    markers = ["accent-rose-500", "accent-sky-500", "accent-lime-500", "accent-amber-500"]
    registries: list[TailwindRecipeRegistry | None] = [None, *(_marked_registry(marker) for marker in markers)]

    expected = []
    for index, registry in enumerate(registries):
        ReactTailwindTsxEmitter(recipe_registry=registry).emit(ast, tmp_path / f"expected-{index}")
        expected.append(_read_artifact(tmp_path / f"expected-{index}"))
    for index, (tsx, _manifest) in enumerate(expected):
        own = markers[index - 1] if index else None
        assert all((marker in tsx) == (marker == own) for marker in markers), index

    rounds = 8
    barrier = threading.Barrier(len(registries))
    mismatches: list[tuple[int, int]] = []
    errors: list[BaseException] = []

    def worker(index: int) -> None:
        registry = registries[index]
        try:
            for round_index in range(rounds):
                output = tmp_path / f"worker-{index}-round-{round_index}"
                barrier.wait(timeout=60)
                if (index + round_index) % 2:
                    ReactTailwindTsxEmitter(recipe_registry=registry).emit(ast, output)
                else:
                    emit_compiler_result(
                        ast.result,
                        dict(ast.style_values),
                        output_dir=output,
                        title=ast.title,
                        recipe_registry=registry,
                    )
                if _read_artifact(output) != expected[index]:
                    mismatches.append((index, round_index))
        except BaseException as exc:  # reported below; a dead worker would otherwise hang the barrier
            errors.append(exc)
            barrier.abort()

    previous_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)  # switch threads as often as possible to expose any shared state
    try:
        threads = [threading.Thread(target=worker, args=(index,)) for index in range(len(registries))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=300)
    finally:
        sys.setswitchinterval(previous_interval)

    assert not errors, errors
    assert all(not thread.is_alive() for thread in threads)
    assert mismatches == []
    assert tailwind_recipe_registry_digest() == MAIN_REGISTRY_DIGEST


def test_package_keeps_no_same_named_wrappers_of_recipe_functions():
    # The package re-exports the recipes module's checks instead of wrapping them; the one function
    # defined in both is resolve_manifest_recipe_metadata, whose package version also appends the
    # node's semantic vs-* classes (both are public, with those two behaviours).
    shadowed = {
        name
        for name, value in vars(react_tailwind).items()
        if inspect.isfunction(value)
        and value.__module__ == react_tailwind.__name__
        and inspect.isfunction(getattr(tailwind_recipes, name, None))
    }
    assert shadowed == {"resolve_manifest_recipe_metadata"}
    assert react_tailwind._validate_recipe_registry is tailwind_recipes._validate_recipe_registry
    assert react_tailwind._validate_tailwind_contract is tailwind_recipes._validate_tailwind_contract
    assert react_tailwind.TailwindRecipeRegistry is tailwind_recipes.TailwindRecipeRegistry
    assert react_tailwind.DEFAULT_TAILWIND_RECIPE_REGISTRY is tailwind_recipes.DEFAULT_TAILWIND_RECIPE_REGISTRY


@pytest.mark.parametrize(
    ("table_name", "table"),
    [
        ("grid_classes", {**tailwind_recipes.GRID_CLASS_BY_COLUMNS, 2: "grid-cols-1 sm:grid-cols-2 [color:red]"}),
        ("grid_classes", {**tailwind_recipes.GRID_CLASS_BY_COLUMNS, 2: "grid-cols-1 bg-[var(--brand)]"}),
        ("grid_classes", {1: "grid-cols-1"}),
        ("grid_span_classes", {**tailwind_recipes.GRID_SPAN_CLASS_BY_COLUMNS, 2: "sm:col-span-2 [color:red]"}),
    ],
    ids=["grid-arbitrary-value", "grid-css-variable", "grid-incomplete", "span-arbitrary-value"],
)
def test_grid_classes_cannot_be_injected_through_a_registry(tmp_path, table_name, table):
    # Review of Q-4: the registry briefly had grid_classes and grid_span_classes fields that no
    # registry check read. A variant put host-config classes such as [color:red] on a grid or on a
    # node spanning grid columns, an incomplete grid table failed with a bare KeyError, and either
    # kind of variant kept the default digest. The grid classes are fixed again, as they were before
    # the registry, so no registry can carry them.
    with pytest.raises(TypeError, match=table_name):
        variant = dataclasses.replace(DEFAULT_TAILWIND_RECIPE_REGISTRY, **{table_name: table})
        ReactTailwindTsxEmitter(recipe_registry=variant).emit(_grid_ast(), tmp_path / "variant")
    assert not (tmp_path / "variant").exists()


def test_every_registry_emits_the_grid_classes_checked_in_at_import(tmp_path, monkeypatch):
    # Every registry puts the checked-in grid classes on grids and on spanning nodes. They are read
    # from copies taken at import, so, as with the tables the default registry was built from,
    # reassigning or mutating the module tables afterwards reaches no emit and no digest.
    ast = _grid_ast()
    nodes = tailwind_recipes._walk(ast.result.root.root)
    grid_id = next(node.id for node in nodes if node.primitive == "grid" and node.props.get("columns") == 2)
    span_id = next(node.id for node in nodes if node.props.get("span_columns") == 2)
    grid_classes = set(tailwind_recipes.GRID_CLASS_BY_COLUMNS[2].split())
    span_classes = set(tailwind_recipes.GRID_SPAN_CLASS_BY_COLUMNS[2].split())
    registries = (None, _marked_registry("accent-rose-500"))
    for index, registry in enumerate(registries):
        ReactTailwindTsxEmitter(recipe_registry=registry).emit(ast, tmp_path / f"before-{index}")

    monkeypatch.setitem(tailwind_recipes.GRID_CLASS_BY_COLUMNS, 2, "grid-cols-1 sm:grid-cols-2 [color:red]")
    monkeypatch.setattr(tailwind_recipes, "GRID_SPAN_CLASS_BY_COLUMNS", {2: "[color:red]", 3: "[color:red]"})
    monkeypatch.setattr(react_tailwind, "GRID_CLASS_BY_COLUMNS", dict.fromkeys((1, 2, 3), "[color:red]"))

    for index, registry in enumerate(registries):
        output = tmp_path / f"after-{index}"
        ReactTailwindTsxEmitter(recipe_registry=registry).emit(ast, output)
        tsx, manifest = _read_artifact(output)
        assert (tsx, manifest) == _read_artifact(tmp_path / f"before-{index}")
        assert "[color:red]" not in tsx
        entries = {entry["ir_id"]: entry for entry in json.loads(manifest).values()}
        assert grid_classes <= set(entries[grid_id]["classes"])
        assert span_classes <= set(entries[span_id]["classes"])
    assert tailwind_recipe_registry_digest() == MAIN_REGISTRY_DIGEST


def test_fixed_grid_tables_are_complete_closed_literal_classes():
    # No registry check reads the fixed grid tables, so this test is what keeps them closed.
    assert set(tailwind_recipes.GRID_CLASS_BY_COLUMNS) == tailwind_recipes.TAILWIND_GRID_COLUMNS
    assert set(tailwind_recipes.GRID_SPAN_CLASS_BY_COLUMNS) == {2, 3}
    for table in (tailwind_recipes.GRID_CLASS_BY_COLUMNS, tailwind_recipes.GRID_SPAN_CLASS_BY_COLUMNS):
        for classes in table.values():
            tailwind_recipes._validate_class_string(classes, code="TAILWIND_HOST_CONFIG_DEPENDENCY")


def test_every_registry_field_feeds_the_digest():
    # Review of Q-4: two registries that differed only in their grid tables emitted different
    # classes under the same digest, the default one, because the projection left those tables out.
    # Changing any entry of any field a registry has must give it a digest of its own.
    seen = {MAIN_REGISTRY_DIGEST}
    shared_digest: list[tuple[str, object]] = []
    for field in dataclasses.fields(TailwindRecipeRegistry):
        table = getattr(DEFAULT_TAILWIND_RECIPE_REGISTRY, field.name)
        if isinstance(table, Mapping):
            variants = [(key, {**table, key: _varied(table[key])}) for key in table]
        else:
            variants = [(None, _varied(table))]
        for key, varied in variants:
            variant = dataclasses.replace(DEFAULT_TAILWIND_RECIPE_REGISTRY, **{field.name: varied})
            digest = tailwind_recipe_registry_digest(recipe_registry=variant)
            if digest in seen:
                shared_digest.append((field.name, key))
            seen.add(digest)
    assert shared_digest == []
    assert len(seen) > 100  # every recipe, overlay, contract and rule id was varied


# pickle.dumps(ReactTailwindTsxEmitter()) on main (64e77eb), when the emitter held no state.
_MAIN_PICKLED_EMITTER = (
    b"\x80\x04\x95G\x00\x00\x00\x00\x00\x00\x00\x8c$viewspec.emitters.react_tailwind_tsx\x94"
    b"\x8c\x17ReactTailwindTsxEmitter\x94\x93\x94)\x81\x94."
)


def test_in_place_edits_of_the_module_tables_reach_neither_the_emitter_nor_the_check(tmp_path, monkeypatch):
    # Review of Q-4: the emitter read the import-time snapshot while viewspec check and prove read
    # the module tables, so editing RECIPE_BY_KEY in place made compile reject the emitter's own
    # output (TAILWIND_UNSAFE_CLASS_SOURCE). On main both moved together; now neither moves.
    bundle = _rich_builder().export_json(tmp_path / "probe.intent.json")

    def compile_and_check(name: str) -> tuple[tuple[str, str], dict[str, Any]]:
        response = compile_intent_bundle_file_tool(bundle, tmp_path / name, target="react-tailwind-tsx", cwd=tmp_path)
        assert response["ok"], response.get("errors")
        assert response["metadata"]["artifact_check"] == "passed"
        return _read_artifact(tmp_path / name), _a11y_report(tmp_path / name / "provenance_manifest.json")

    before = compile_and_check("before")
    for key in list(tailwind_recipes.RECIPE_BY_KEY):
        monkeypatch.setitem(tailwind_recipes.RECIPE_BY_KEY, key, "hidden")
    monkeypatch.delitem(tailwind_recipes.RECIPE_BY_KEY, "primitive:value")
    for overlay in tailwind_recipes.TAILWIND_AESTHETIC_RECIPE_OVERLAYS.values():
        for key in list(overlay):
            monkeypatch.setitem(overlay, key, "hidden")
    monkeypatch.delitem(tailwind_recipes.TAILWIND_AESTHETIC_RECIPE_OVERLAYS, "aesthetic.executive_review")
    for table in (
        tailwind_recipes.LAYOUT_EMPHASIS_CLASS_BY_VALUE,
        tailwind_recipes.GRID_CLASS_BY_COLUMNS,
        tailwind_recipes.GRID_SPAN_CLASS_BY_COLUMNS,
    ):
        for key in list(table):
            monkeypatch.setitem(table, key, "hidden")
    for contract in tailwind_recipes.TAILWIND_APP_V1_APP_ROLE_CONTRACTS.values():
        monkeypatch.setitem(contract, "primitives", ("rule",))
    monkeypatch.delitem(tailwind_recipes.TAILWIND_APP_V1_APP_ROLE_CONTRACTS, "metric_card")
    for rule_ids in (
        tailwind_recipes.TAILWIND_PRODUCT_APP_ROLE_RULE_IDS,
        tailwind_recipes.TAILWIND_STRUCTURAL_APP_ROLE_RULE_IDS,
    ):
        for role in list(rule_ids):
            monkeypatch.setitem(rule_ids, role, "edited")

    # The same TSX and manifest (whose inventory records the default digest), still checked, and the
    # same proof contrast facts.
    assert compile_and_check("after") == before
    assert tailwind_recipe_registry_digest() == MAIN_REGISTRY_DIGEST


def test_check_allows_every_class_the_fixed_grid_tables_emit():
    # The check reads the fixed grid copies the resolver reads, span classes included, so it accepts
    # a node spanning three columns (lg:col-span-3), which no recipe table holds.
    allowed = _tailwind_allowed_class_tokens()
    for table in (tailwind_recipes._GRID_CLASSES, tailwind_recipes._GRID_SPAN_CLASSES):
        for classes in table.values():
            assert set(classes.split()) <= allowed, classes


def test_artifacts_emitted_with_another_registry_do_not_pass_viewspec_check(tmp_path):
    # viewspec check verifies against the default registry. A registry that recolors a table cell
    # with a class the default registry also owns still fails it, on the recomputed classes.
    ast = _rich_ast()
    cell = DEFAULT_TAILWIND_RECIPE_REGISTRY.recipes["role:table:cell"]
    recolored = _variant(
        recipes={**DEFAULT_TAILWIND_RECIPE_REGISTRY.recipes, "role:table:cell": cell.replace("-700", "-600")}
    )
    results = {}
    for name, registry in (("default", None), ("recolored", recolored)):
        paths = ReactTailwindTsxEmitter(recipe_registry=registry).emit(ast, tmp_path / name)
        wrap_intent_bundle_manifest(
            Path(paths["manifest"]),
            source_name="probe.intent.json",
            raw_source_hash="0" * 64,
            design=None,
            command_args=["viewspec", "compile", "probe.intent.json", "--target", "react-tailwind-tsx"],
            artifact_path=Path(paths["tsx"]),
            emitter="react_tailwind_tsx",
        )
        results[name] = check_artifact_dir(tmp_path / name)
    assert results["default"]["ok"], results["default"]["errors"]
    assert not results["recolored"]["ok"]
    assert {error.split(":")[0] for error in results["recolored"]["errors"]} == {"TAILWIND_INVENTORY_MISMATCH"}


def test_emitters_that_never_ran_init_emit_with_the_default_registry(tmp_path):
    # Review of Q-4: the registry lived only on the instance, so a subclass whose own __init__ does
    # not call super().__init__() (fine on main, where the emitter had no __init__), an instance made
    # with object.__new__ and an emitter pickled by main raised AttributeError: _recipe_registry.
    class LabelledEmitter(ReactTailwindTsxEmitter):
        def __init__(self, label: str) -> None:
            self.label = label

    ast = _ast()
    ReactTailwindTsxEmitter().emit(ast, tmp_path / "default")
    expected = _read_artifact(tmp_path / "default")
    emitters = {
        "subclass": LabelledEmitter("audit"),
        "new": object.__new__(ReactTailwindTsxEmitter),
        "main-pickle": pickle.loads(_MAIN_PICKLED_EMITTER),
    }
    for name, emitter in emitters.items():
        assert emitter.recipe_registry is DEFAULT_TAILWIND_RECIPE_REGISTRY, name
        emitter.emit(ast, tmp_path / name)
        assert _read_artifact(tmp_path / name) == expected, name
