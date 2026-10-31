"""Public-surface regression tests for exports and layering."""

from __future__ import annotations

import hashlib
import json

import viewspec
from viewspec import ViewSpecBuilder, compiler_refs, sdk
from viewspec import compiler as compiler_module
from viewspec.emitters import html_tailwind, react_tailwind_tsx
from viewspec.emitters.react_tailwind_tsx import recipes as tailwind_recipes
from viewspec.sdk import DetailBuilder, FormBuilder, ListBuilder


def test_public_surface_is_the_small_documented_one():
    assert sorted(viewspec.__all__) == sorted(
        [
            "__version__",
            "ASTBundle",
            "AppBundle",
            "AppBundleError",
            "CompilerInputError",
            "DesignSystemError",
            "IntentBundle",
            "UnsupportedMotifError",
            "ViewSpecBuilder",
            "check",
            "compile",
            "prove",
        ]
    )
    for name in viewspec.__all__:
        assert hasattr(viewspec, name)


def test_sdk_exports_every_documented_motif_builder():
    for name in ("ListBuilder", "FormBuilder", "DetailBuilder"):
        assert name in sdk.__all__

    builder = ViewSpecBuilder("exports")
    assert isinstance(builder.add_list("tasks", region="main"), ListBuilder)
    assert isinstance(builder.add_form("signup", region="main"), FormBuilder)
    assert isinstance(builder.add_detail("profile", region="main"), DetailBuilder)


def test_product_surface_roles_owned_by_compiler_refs():
    assert compiler_module.PRODUCT_SURFACE_PLANNER_V1_ROLES is compiler_refs.PRODUCT_SURFACE_PLANNER_V1_ROLES
    assert set(html_tailwind.PRODUCT_ROLE_CLASS_BY_ROLE) == set(compiler_refs.PRODUCT_SURFACE_PLANNER_V1_ROLES)


def test_derive_style_tokens_is_public_and_aliased():
    assert compiler_module._derive_style_tokens is compiler_module.derive_style_tokens
    bundle = ViewSpecBuilder("tokens").build_bundle()
    tokens = compiler_module.derive_style_tokens(bundle.substrate, bundle.view_spec)
    assert tokens["emphasis.high"].startswith("font-weight:")


def test_tailwind_recipe_registry_digest_has_one_definition():
    assert not hasattr(tailwind_recipes, "tailwind_recipe_registry_digest")
    assert not hasattr(tailwind_recipes, "tailwind_recipe_registry_projection")
    projection = react_tailwind_tsx.tailwind_recipe_registry_projection()
    assert "semantic_parity_classes" in projection
    encoded = json.dumps(projection, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    assert react_tailwind_tsx.tailwind_recipe_registry_digest() == hashlib.sha256(encoded).hexdigest()
