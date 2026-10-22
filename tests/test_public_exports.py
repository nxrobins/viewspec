"""Public-surface regression tests for export and layering fixes (audit Q-12, Q-2, Q-5, C-8)."""

from __future__ import annotations

import hashlib
import json

import viewspec
from viewspec import (
    DetailBuilder,
    FormBuilder,
    ListBuilder,
    ViewSpecBuilder,
    compiler_refs,
    design_md,
    hosted_compile,
    sdk,
    types,
)
from viewspec import compiler as compiler_module
from viewspec.emitters import html_tailwind, react_tailwind_tsx
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


# Every public name `viewspec.compiler` exposed on main before the hosted client moved to
# `viewspec.hosted_compile` (modules and typing helpers it merely imported left out).
MAIN_COMPILER_PUBLIC_NAMES = (
    "AESTHETIC_PROFILE_TOKENS",
    "ASTBundle",
    "ActionIntent",
    "AestheticProfileError",
    "BULK_SELECTION_MAX_IDS",
    "BULK_SELECTION_VALUE_MAX_BYTES",
    "BindingSpec",
    "COLLECTION_ACTION_KINDS",
    "COLLECTION_MOTIF_KINDS",
    "COLLECTION_PAYLOAD_VALUE_MAX_BYTES",
    "CONFLICTING_STATE_MOTIF_KINDS",
    "CompileRequestPayload",
    "CompileResponse",
    "CompilerAPIError",
    "CompilerDiagnostic",
    "CompilerInputError",
    "CompilerResult",
    "CompositionIR",
    "DEFAULT_STYLE_TOKEN_VALUES",
    "DesignRequest",
    "DesignSystemContext",
    "DesignSystemError",
    "GroupSpec",
    "IRNode",
    "IntentBundle",
    "MAX_COLLECTION_ACTIONS_PER_COLLECTION",
    "MAX_COLLECTION_ACTION_PAYLOAD_BINDINGS",
    "MAX_COMPILE_NESTING_DEPTH",
    "MAX_IMPLICIT_PAGE_TITLE_BYTES",
    "MAX_STATE_MOTIFS",
    "MotifCompileContext",
    "MotifPlugin",
    "MotifPluginError",
    "MotifRegistry",
    "MotifSpec",
    "PRESENT_AS_TO_PRIMITIVE",
    "PRIMITIVE_DEFAULT_TOKENS",
    "PRODUCT_SINGLE_COLUMN_SURFACE_V1",
    "PRODUCT_SURFACE_PLANNER_V1_ROLES",
    "PRODUCT_SURFACE_PLANNER_V1_SURFACE",
    "Provenance",
    "RegionSpec",
    "SAFE_COMPILER_ID_RE",
    "SAFE_MOTIF_LOCAL_ACTION_KINDS",
    "SAFE_MOTIF_LOCAL_ACTION_ROOT_TAGS",
    "STATE_MOTIF_KINDS",
    "SUPPORTED_ACTION_KINDS",
    "SUPPORTED_AESTHETIC_PROFILES",
    "SUPPORTED_BINDING_CARDINALITIES",
    "SUPPORTED_GROUP_KINDS",
    "SUPPORTED_MOTIF_KINDS",
    "SUPPORTED_REGION_LAYOUTS",
    "SemanticNode",
    "SemanticSubstrate",
    "StyleSpec",
    "UnsupportedMotifError",
    "VIEWSPEC_API_URL",
    "ViewSpec",
    "WORKSPACE_CONTENT_GRID_ROLE_MARKERS",
    "WORKSPACE_HEADER_ROLE_MARKERS",
    "WORKSPACE_PRIMARY_ROLE_MARKERS",
    "WORKSPACE_SIDE_RAIL_ROLE_MARKERS",
    "builtin_motif_registry",
    "compile",
    "compile_auto",
    "compile_remote",
    "compile_remote_response",
    "create_motif_registry",
    "is_aesthetic_profile_token",
    "load_design_system",
    "merge_style_values",
    "profile_layout_props",
    "profile_style_values",
    "validate_aesthetic_profile_registry",
)


def test_compiler_module_keeps_every_public_name_it_exposed_on_main():
    missing = [name for name in MAIN_COMPILER_PUBLIC_NAMES if not hasattr(compiler_module, name)]
    assert missing == []

    from viewspec.compiler import CompileRequestPayload, CompileResponse, DesignSystemError

    # The hosted client's request and response types and the DESIGN.md error stay the same objects.
    assert CompileRequestPayload is types.CompileRequestPayload is viewspec.CompileRequestPayload
    assert CompileResponse is types.CompileResponse is viewspec.CompileResponse
    assert DesignSystemError is design_md.DesignSystemError is viewspec.DesignSystemError


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
