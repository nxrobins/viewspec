"""Public-surface regression tests for export and layering fixes (audit Q-12, Q-2, Q-5, C-8)."""

from __future__ import annotations

import hashlib
import json

import viewspec
from viewspec import DetailBuilder, FormBuilder, ListBuilder, ViewSpecBuilder, sdk
from viewspec import compiler as compiler_module
from viewspec import compiler_refs, hosted_compile
from viewspec.emitters import html_tailwind
from viewspec.emitters import react_tailwind_tsx
from viewspec.emitters.react_tailwind_tsx import recipes as tailwind_recipes


def test_sdk_exports_every_documented_motif_builder():
    for name in ("ListBuilder", "FormBuilder", "DetailBuilder"):
        assert name in viewspec.__all__
        assert name in sdk.__all__
        assert getattr(viewspec, name) is getattr(sdk, name)

    builder = ViewSpecBuilder("exports")
    assert isinstance(builder.add_list("tasks", region="main"), ListBuilder)
    assert isinstance(builder.add_form("signup", region="main"), FormBuilder)
    assert isinstance(builder.add_detail("profile", region="main"), DetailBuilder)


def test_hosted_client_names_stay_importable_from_compiler():
    from viewspec.compiler import (
        VIEWSPEC_API_URL,
        CompilerAPIError,
        compile_auto,
        compile_remote,
        compile_remote_response,
    )

    assert CompilerAPIError is hosted_compile.CompilerAPIError
    assert compile_auto is hosted_compile.compile_auto
    assert compile_remote is hosted_compile.compile_remote
    assert compile_remote_response is hosted_compile.compile_remote_response
    assert VIEWSPEC_API_URL == hosted_compile.VIEWSPEC_API_URL == "https://api.viewspec.dev"
    for name in ("compile_auto", "compile_remote", "compile_remote_response", "CompilerAPIError"):
        assert name in viewspec.__all__
        assert getattr(viewspec, name) is getattr(hosted_compile, name)
    # Private helpers the hosted_* siblings consume are still reachable through the old path.
    assert compiler_module._compiler_api_error is hosted_compile._compiler_api_error


def test_compile_auto_resolves_local_compiler_at_call_time(monkeypatch):
    calls: list[str] = []
    bundle = ViewSpecBuilder("patched").build_bundle()
    expected = compiler_module.compile(bundle)

    def fake_compile(request, *, design=None):
        calls.append(request.view_spec.id)
        return expected

    monkeypatch.setattr(compiler_module, "compile", fake_compile)

    assert hosted_compile.compile_auto(bundle) is expected
    assert calls == ["patched"]


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
