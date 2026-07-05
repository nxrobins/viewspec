"""Closed Tailwind recipe registry for the React Tailwind TSX emitter."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from viewspec.aesthetics import (
    AESTHETIC_PROFILE_TOKENS,
    MAX_AESTHETIC_PROFILE_CSS_BYTES,
)
from viewspec.types import IRNode

TAILWIND_RECIPE_PACK = "tailwind_app_v1"
TAILWIND_RECIPE_REGISTRY_VERSION = "tailwind_recipe_registry.v1"
TAILWIND_MAX_IR_NODES = 600
TAILWIND_MAX_IR_DEPTH = 16
TAILWIND_MAX_ACTIONS = 128
TAILWIND_MAX_APP_ROLES = 25
TAILWIND_MAX_RECIPES = 96
TAILWIND_MAX_CLASS_TOKENS = 512
TAILWIND_MAX_ARTIFACT_BYTES = 256 * 1024
TAILWIND_MAX_RECIPE_PRECEDENCE_TIERS = 5
TAILWIND_GENERIC_FALLBACK_RATIO = 0.25
TAILWIND_GRID_COLUMNS = {1, 2, 3}

CLASS_TOKEN_RE = re.compile(r"^[A-Za-z0-9:/.-]+$")
HOST_CONFIG_DEPENDENT_CLASS_RE = re.compile(r"\[|var\(|theme\(", re.IGNORECASE)


TAILWIND_CONSTRAINT_CODES = frozenset(
    {
        "APP_ROLE_AMBIGUOUS",
        "APP_ROLE_DERIVATION_MISMATCH",
        "APP_ROLE_LEXICAL_SOURCE",
        "APP_ROLE_UNDECLARED_CONTRACT",
        "APP_ROLE_UNDECLARED_SIGNAL",
        "TAILWIND_HOST_CONFIG_DEPENDENCY",
        "TAILWIND_IR_CONTRACT_VIOLATION",
        "TAILWIND_LIMIT_EXCEEDED_ACTIONS",
        "TAILWIND_LIMIT_EXCEEDED_ARTIFACT_BYTES",
        "TAILWIND_LIMIT_EXCEEDED_CLASS_TOKENS",
        "TAILWIND_LIMIT_EXCEEDED_DEPTH",
        "TAILWIND_LIMIT_EXCEEDED_GRID_COLUMNS",
        "TAILWIND_LIMIT_EXCEEDED_NODES",
        "TAILWIND_LIMIT_EXCEEDED_RECIPES",
        "TAILWIND_RECIPE_CONFLICT",
        "TAILWIND_AESTHETIC_RECIPE_MISSING",
        "TAILWIND_AESTHETIC_RECIPE_TOO_LARGE",
        "TAILWIND_AESTHETIC_UNSAFE_CLASS",
        "TAILWIND_STATEFUL_COLLECTION_RECIPE_MISSING",
        "TAILWIND_STYLE_CONSTRAINT_VIOLATION",
        "TAILWIND_UNSAFE_CLASS_SOURCE",
        "TAILWIND_UNSUPPORTED_PRIMITIVE",
        "TAILWIND_GENERIC_FALLBACK_EXCEEDED",
    }
)


class CompilerConstraintError(ValueError):
    """Stable-code Tailwind emitter constraint failure."""

    def __init__(self, code: str, message: str) -> None:
        if code not in TAILWIND_CONSTRAINT_CODES:
            code = "TAILWIND_RECIPE_CONFLICT"
            message = "Tailwind constraint errors must use checked-in stable codes."
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


TAILWIND_APP_V1_APP_ROLE_CONTRACTS: dict[str, dict[str, tuple[str, ...]]] = {
    "app_shell": {"primitives": ("root",), "product_roles": ("app_shell",)},
    "app_header": {"primitives": ("stack", "surface"), "product_roles": ("app_header",)},
    "page_header": {"primitives": ("surface",), "product_roles": ("page_header",)},
    "content_grid": {"primitives": ("grid",), "product_roles": ("content_grid",)},
    "primary_column": {"primitives": ("stack",), "product_roles": ("primary_column",)},
    "side_rail": {"primitives": ("stack",), "product_roles": ("side_rail",)},
    "sidebar_nav": {"primitives": ("stack",), "motif_kinds": ("list",)},
    "top_nav": {"primitives": ("stack",), "motif_kinds": ("list",)},
    "toolbar": {"primitives": ("cluster",), "product_roles": ("action_row",)},
    "tab_list": {"primitives": ("cluster",), "motif_kinds": ("list",)},
    "tab_panel": {"primitives": ("surface",), "motif_kinds": ("detail", "form")},
    "filter_bar": {"primitives": ("cluster",), "product_roles": ("action_row",)},
    "data_table": {"primitives": ("stack",), "motif_kinds": ("table",)},
    "data_row": {"primitives": ("cluster",), "motif_kinds": ("table",)},
    "metric_grid": {"primitives": ("grid",), "product_roles": ("metric_grid",)},
    "metric_card": {"primitives": ("surface",), "product_roles": ("metric_card",)},
    "form_panel": {"primitives": ("stack",), "product_roles": ("form_panel",)},
    "field_group": {"primitives": ("surface",), "product_roles": ("field_group",)},
    "detail_panel": {"primitives": ("stack",), "product_roles": ("detail_panel",)},
    "action_row": {"primitives": ("cluster",), "product_roles": ("action_row",)},
    "collection_action_bar": {"primitives": ("cluster",), "product_roles": ("action_row",)},
    "empty_state": {"primitives": ("surface",), "motif_kinds": ("empty_state",)},
    "loading_state": {"primitives": ("surface",), "state_roles": ("loading",)},
    "error_state": {"primitives": ("surface",), "state_roles": ("error",)},
    "overlay_panel": {"primitives": ("surface",), "product_roles": ("detail_panel", "form_panel")},
}

TAILWIND_PRODUCT_APP_ROLE_RULE_IDS: dict[str, str] = {
    "app_shell": "tailwind_app_v1.product_role.app_shell",
    "app_header": "tailwind_app_v1.product_role.app_header",
    "page_header": "tailwind_app_v1.product_role.page_header",
    "content_grid": "tailwind_app_v1.product_role.content_grid",
    "primary_column": "tailwind_app_v1.product_role.primary_column",
    "side_rail": "tailwind_app_v1.product_role.side_rail",
    "metric_grid": "tailwind_app_v1.product_role.metric_grid",
    "metric_card": "tailwind_app_v1.product_role.metric_card",
    "form_panel": "tailwind_app_v1.product_role.form_panel",
    "field_group": "tailwind_app_v1.product_role.field_group",
    "detail_panel": "tailwind_app_v1.product_role.detail_panel",
}

TAILWIND_STRUCTURAL_APP_ROLE_RULE_IDS: dict[str, str] = {
    "toolbar": "tailwind_app_v1.structural.toolbar_from_action_row",
    "filter_bar": "tailwind_app_v1.structural.filter_bar_from_form_action_row",
    "sidebar_nav": "tailwind_app_v1.structural.sidebar_nav_from_side_rail_list",
    "top_nav": "tailwind_app_v1.structural.top_nav_from_header_list",
    "data_table": "tailwind_app_v1.structural.data_table_from_table_stack",
    "data_row": "tailwind_app_v1.structural.data_row_from_table_cluster",
    "empty_state": "tailwind_app_v1.structural.empty_state_from_empty_surface",
    "loading_state": "tailwind_app_v1.structural.loading_state_from_state_surface",
    "error_state": "tailwind_app_v1.structural.error_state_from_state_surface",
    "collection_action_bar": "tailwind_app_v1.structural.collection_action_bar_from_collection_actions",
}

TAILWIND_APP_ROLE_RULE_PRECEDENCE: tuple[str, ...] = (
    *TAILWIND_PRODUCT_APP_ROLE_RULE_IDS.values(),
    TAILWIND_STRUCTURAL_APP_ROLE_RULE_IDS["filter_bar"],
    TAILWIND_STRUCTURAL_APP_ROLE_RULE_IDS["toolbar"],
    TAILWIND_STRUCTURAL_APP_ROLE_RULE_IDS["sidebar_nav"],
    TAILWIND_STRUCTURAL_APP_ROLE_RULE_IDS["top_nav"],
    TAILWIND_STRUCTURAL_APP_ROLE_RULE_IDS["data_table"],
    TAILWIND_STRUCTURAL_APP_ROLE_RULE_IDS["data_row"],
    TAILWIND_STRUCTURAL_APP_ROLE_RULE_IDS["empty_state"],
    TAILWIND_STRUCTURAL_APP_ROLE_RULE_IDS["loading_state"],
    TAILWIND_STRUCTURAL_APP_ROLE_RULE_IDS["error_state"],
    TAILWIND_STRUCTURAL_APP_ROLE_RULE_IDS["collection_action_bar"],
)


RECIPE_BY_KEY: dict[str, str] = {
    "app_role:app_shell": "min-h-screen bg-slate-50 px-6 py-6 text-slate-950 sm:px-8",
    "app_role:app_header": "border-b border-slate-200 pb-4 pt-1",
    "app_role:page_header": "bg-transparent p-0 shadow-none ring-0",
    "app_role:content_grid": "grid gap-5",
    "app_role:primary_column": "flex min-w-0 flex-col gap-5 lg:col-span-2",
    "app_role:side_rail": "flex min-w-0 flex-col gap-4 lg:col-span-1",
    "app_role:sidebar_nav": "flex flex-col gap-2",
    "app_role:top_nav": "flex flex-row flex-wrap items-center gap-2",
    "app_role:toolbar": "flex flex-row flex-wrap items-center justify-end gap-2",
    "app_role:tab_list": "flex flex-row flex-wrap items-center gap-1 border-b border-slate-200",
    "app_role:tab_panel": "rounded-md border border-slate-200 bg-white p-4",
    "app_role:filter_bar": "flex flex-row flex-wrap items-center gap-3 rounded-md border border-slate-200 bg-white p-3",
    "app_role:data_table": "w-full overflow-hidden rounded-md border border-slate-200 bg-white text-sm",
    "app_role:data_row": "border-b border-slate-200 last:border-b-0",
    "app_role:metric_grid": "grid gap-3",
    "app_role:metric_card": "min-h-28 justify-between rounded-md border border-slate-200 bg-white p-4 shadow-sm",
    "app_role:form_panel": "rounded-md border border-slate-200 bg-white p-5 shadow-sm",
    "app_role:field_group": "rounded-md border border-slate-200 bg-slate-50 p-3 shadow-none",
    "app_role:detail_panel": "rounded-md border border-slate-200 bg-white p-4 shadow-sm",
    "app_role:action_row": "flex flex-row flex-wrap items-center justify-end gap-2 pt-1",
    "app_role:collection_action_bar": "flex flex-row flex-wrap items-center gap-2 rounded-md border border-slate-200 bg-white p-3",
    "app_role:empty_state": "items-center rounded-md border border-dashed border-slate-300 bg-white p-8 text-center",
    "app_role:loading_state": "rounded-md border border-slate-200 bg-white p-4 text-slate-500",
    "app_role:error_state": "rounded-md border border-red-300 bg-red-50 p-4 text-red-800",
    "app_role:overlay_panel": "rounded-md border border-slate-200 bg-white p-5 shadow-lg",
    "product_role:app_shell": "mx-auto flex w-full max-w-6xl flex-col gap-5",
    "product_role:app_header": "flex flex-col gap-2",
    "product_role:page_header": "flex flex-col gap-2",
    "product_role:content_grid": "items-start",
    "product_role:primary_column": "min-w-0",
    "product_role:side_rail": "min-w-0",
    "product_role:metric_grid": "grid gap-3",
    "product_role:metric_card": "flex flex-col gap-3",
    "product_role:form_panel": "flex flex-col gap-4",
    "product_role:field_group": "flex flex-col gap-2",
    "product_role:detail_panel": "flex flex-col gap-3",
    "product_role:action_row": "flex flex-row flex-wrap items-center justify-end gap-2",
    "motif:comparison:stack": "flex flex-col gap-3",
    "motif:dashboard:grid": "grid gap-3",
    "motif:detail:cluster": "grid gap-2 border-b border-slate-200 py-2 last:border-b-0 sm:grid-cols-3",
    "motif:detail:stack": "m-0 flex flex-col gap-0",
    "motif:empty_state:surface": "flex flex-col gap-3",
    "motif:error_state:surface": "flex flex-col gap-3",
    "motif:form:stack": "flex flex-col gap-4",
    "motif:form:surface": "flex flex-col gap-2",
    "motif:hero:surface": "flex flex-col gap-3",
    "motif:list:stack": "m-0 flex list-none flex-col gap-2 p-0",
    "motif:list:surface": "rounded-md border border-slate-200 bg-white p-3",
    "motif:loading_state:surface": "flex flex-col gap-3",
    "motif:outline:stack": "flex flex-col gap-2",
    "motif:table:cluster": "table-row",
    "motif:table:stack": "w-full border-collapse text-left",
    "role:detail:description": "m-0 text-slate-700 sm:col-span-2",
    "role:detail:term": "font-semibold text-slate-500",
    "role:empty_state:description": "text-slate-600",
    "role:empty_state:title": "text-lg font-semibold text-slate-950",
    "role:hero:description": "max-w-3xl text-base leading-7 text-slate-700",
    "role:hero:eyebrow": "text-xs font-bold uppercase text-teal-700",
    "role:hero:title": "text-3xl font-black leading-tight text-slate-950",
    "role:table:cell": "border-b border-slate-200 px-3 py-2 align-top text-slate-700",
    "role:table:row_header": "border-b border-slate-200 bg-slate-50 px-3 py-2 align-top font-semibold text-slate-600",
    "role:state:description": "text-sm leading-6 text-slate-600",
    "role:state:title": "text-base font-semibold text-slate-950",
    "layout:cluster": "flex flex-row flex-wrap gap-3",
    "layout:grid": "grid gap-4",
    "layout:root": "flex flex-col gap-6",
    "layout:stack": "flex flex-col gap-3",
    "layout:surface": "flex flex-col gap-3 rounded-md border border-slate-200 bg-white p-4 shadow-sm",
    "primitive:badge": "inline-flex w-fit rounded-full bg-teal-100 px-3 py-1 text-sm font-bold text-teal-800 ring-1 ring-teal-200",
    "primitive:button": "inline-flex w-fit items-center justify-center rounded-md bg-teal-700 px-4 py-2 text-sm font-bold text-white shadow-sm hover:bg-teal-800 focus:outline-none focus:ring-2 focus:ring-teal-700 focus:ring-offset-2",
    "primitive:cluster": "flex flex-row flex-wrap gap-3",
    "primitive:error_boundary": "rounded-md border border-red-300 bg-red-50 p-4 font-mono text-sm text-red-800",
    "primitive:grid": "grid gap-4",
    "primitive:image_slot": "grid min-h-24 place-items-center rounded-md bg-slate-200 text-sm font-medium text-slate-500",
    "primitive:input": "w-full min-w-0 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-950 focus:outline-none focus:ring-2 focus:ring-teal-700",
    "primitive:label": "text-xs font-bold uppercase text-slate-500",
    "primitive:root": "min-h-screen bg-slate-50 text-slate-950",
    "primitive:rule": "my-2 border-0 border-t border-slate-200",
    "primitive:stack": "flex flex-col gap-3",
    "primitive:surface": "flex flex-col gap-3 rounded-md border border-slate-200 bg-white p-4 shadow-sm",
    "primitive:svg": "rounded-md border border-slate-200 bg-slate-50 p-3 text-slate-600",
    "primitive:text": "text-base leading-7 text-slate-700",
    "primitive:value": "text-2xl font-black leading-tight text-slate-950",
}

TAILWIND_AESTHETIC_RECIPE_OVERLAYS: dict[str, dict[str, str]] = {
    "aesthetic.calm_ops": {
        "app_role:app_shell": "bg-teal-50 text-slate-900",
        "app_role:metric_card": "rounded-xl border-slate-200 bg-white shadow-sm",
        "app_role:form_panel": "rounded-xl border-slate-200 bg-white shadow-sm",
        "app_role:detail_panel": "rounded-xl border-teal-200 bg-teal-50 shadow-sm",
        "app_role:data_table": "border-slate-200 bg-white",
        "app_role:collection_action_bar": "rounded-xl border-slate-200 bg-white",
        "primitive:button": "rounded-lg bg-teal-700 hover:bg-teal-800 focus:ring-teal-700",
        "primitive:badge": "bg-teal-100 text-teal-800 ring-teal-200",
        "primitive:label": "text-slate-500",
        "primitive:value": "text-xl font-extrabold leading-snug text-slate-950",
        "primitive:text": "text-base leading-7 text-slate-700",
    },
    "aesthetic.premium_saas": {
        "app_role:app_shell": "bg-violet-50 px-8 py-8 text-slate-950 sm:px-10",
        "app_role:metric_card": "rounded-2xl border-violet-200 bg-white p-6 shadow-xl",
        "app_role:form_panel": "rounded-2xl border-violet-200 bg-white p-6 shadow-xl",
        "app_role:detail_panel": "rounded-2xl border-violet-200 bg-indigo-50 p-6 shadow-lg",
        "app_role:data_table": "rounded-xl border-violet-200 bg-white shadow-md",
        "app_role:collection_action_bar": "rounded-2xl border-violet-200 bg-white p-4 shadow-md",
        "primitive:button": "rounded-full bg-indigo-600 px-5 py-2.5 hover:bg-indigo-700 focus:ring-indigo-600",
        "primitive:badge": "bg-indigo-100 text-indigo-800 ring-indigo-200",
        "primitive:label": "text-slate-500",
        "primitive:value": "text-3xl font-black tracking-tight text-slate-950",
        "primitive:text": "text-base leading-7 text-slate-700",
    },
    "aesthetic.data_dense": {
        "app_role:app_shell": "bg-slate-200 px-4 py-4 font-mono text-slate-950 sm:px-5",
        "app_role:metric_card": "min-h-20 rounded-sm border-slate-400 bg-white p-2 shadow-none",
        "app_role:form_panel": "rounded-sm border-slate-400 bg-white p-2 shadow-none",
        "app_role:detail_panel": "rounded-sm border-slate-400 bg-blue-50 p-2 shadow-none",
        "app_role:data_table": "rounded-sm border-slate-400 bg-white text-xs shadow-none",
        "app_role:collection_action_bar": "rounded-sm border-slate-400 bg-white p-1.5 shadow-none",
        "primitive:button": "rounded-sm bg-blue-700 px-2.5 py-1 text-xs hover:bg-blue-800 focus:ring-blue-700",
        "primitive:badge": "rounded-sm bg-blue-100 px-1.5 py-0 text-xs text-blue-800 ring-blue-200",
        "primitive:label": "text-xs font-semibold text-slate-600",
        "primitive:value": "text-lg font-bold leading-tight text-slate-950",
        "primitive:text": "text-xs leading-5 text-slate-700",
        "primitive:input": "rounded-sm border-slate-300 px-2 py-1 text-xs",
    },
    "aesthetic.editorial_product": {
        "app_role:app_shell": "bg-rose-50 px-8 py-10 font-serif text-neutral-950 sm:px-12",
        "app_role:metric_card": "rounded-3xl border-rose-200 bg-white p-6 shadow-none",
        "app_role:form_panel": "rounded-3xl border-rose-200 bg-white p-6 shadow-none",
        "app_role:detail_panel": "rounded-3xl border-rose-300 bg-rose-50 p-6 shadow-none",
        "app_role:data_table": "rounded-2xl border-rose-200 bg-white",
        "app_role:collection_action_bar": "rounded-3xl border-rose-200 bg-white p-5",
        "primitive:button": "rounded-2xl bg-rose-700 px-5 py-2.5 hover:bg-rose-800 focus:ring-rose-700",
        "primitive:badge": "bg-rose-100 text-rose-800 ring-rose-200",
        "primitive:label": "text-xs font-bold uppercase text-rose-700",
        "primitive:value": "text-4xl font-black leading-none text-neutral-950",
        "primitive:text": "max-w-2xl text-lg leading-9 text-neutral-700",
    },
    "aesthetic.executive_review": {
        "app_role:app_shell": "bg-slate-100 px-6 py-6 text-slate-950 sm:px-8",
        "app_role:metric_card": "rounded-none border-slate-400 bg-white p-4 shadow-sm",
        "app_role:form_panel": "rounded-none border-slate-400 bg-white p-4 shadow-sm",
        "app_role:detail_panel": "rounded-none border-slate-400 bg-slate-50 p-4 shadow-sm",
        "app_role:data_table": "rounded-none border-slate-400 bg-white",
        "app_role:collection_action_bar": "rounded-none border-slate-400 bg-white p-3",
        "primitive:button": "rounded-none bg-slate-950 px-4 py-2 hover:bg-slate-800 focus:ring-slate-950",
        "primitive:badge": "bg-cyan-100 text-cyan-900 ring-cyan-200",
        "primitive:label": "text-xs font-extrabold uppercase text-slate-600",
        "primitive:value": "text-2xl font-extrabold leading-tight text-slate-950",
        "primitive:text": "text-sm leading-6 text-slate-700",
    },
}

GRID_CLASS_BY_COLUMNS = {
    1: "grid-cols-1",
    2: "grid-cols-1 sm:grid-cols-2",
    3: "grid-cols-1 sm:grid-cols-2 lg:grid-cols-3",
}
GRID_SPAN_CLASS_BY_COLUMNS = {
    2: "sm:col-span-2",
    3: "sm:col-span-2 lg:col-span-3",
}
LAYOUT_EMPHASIS_CLASS_BY_VALUE = {
    "featured": "ring-2 ring-teal-300",
}
TEXT_SIZE_CLASSES = frozenset({"text-xs", "text-sm", "text-base", "text-lg", "text-xl", "text-2xl", "text-3xl", "text-4xl"})
FONT_FAMILY_CLASSES = frozenset({"font-mono", "font-sans", "font-serif"})
FONT_WEIGHT_CLASSES = frozenset({"font-bold", "font-extrabold", "font-black", "font-semibold"})


@dataclass(frozen=True)
class ResolvedRecipe:
    app_role: str | None
    app_role_source: str | None
    recipe_key: str
    classes: tuple[str, ...]


@dataclass(frozen=True)
class AppRoleDerivation:
    app_role: str
    rule_id: str


def _fail(code: str, message: str) -> None:
    raise CompilerConstraintError(code, message)


def _walk(node: IRNode) -> list[IRNode]:
    nodes = [node]
    for child in node.children:
        nodes.extend(_walk(child))
    return nodes


def _depth(node: IRNode) -> int:
    if not node.children:
        return 1
    return 1 + max(_depth(child) for child in node.children)


def _validate_recipe_registry() -> None:
    if len(TAILWIND_APP_V1_APP_ROLE_CONTRACTS) > TAILWIND_MAX_APP_ROLES:
        _fail("APP_ROLE_UNDECLARED_CONTRACT", f"tailwind_app_v1 app-role registry exceeds {TAILWIND_MAX_APP_ROLES} roles.")
    for role in TAILWIND_APP_V1_APP_ROLE_CONTRACTS:
        if f"app_role:{role}" not in RECIPE_BY_KEY:
            _fail("APP_ROLE_UNDECLARED_CONTRACT", f"App role {role} has no checked-in recipe.")
    for role, rule_id in TAILWIND_PRODUCT_APP_ROLE_RULE_IDS.items():
        if role not in TAILWIND_APP_V1_APP_ROLE_CONTRACTS or rule_id not in TAILWIND_APP_ROLE_RULE_PRECEDENCE:
            _fail("APP_ROLE_UNDECLARED_CONTRACT", f"Product app-role rule {rule_id} is not declared in registry precedence.")
    for role, rule_id in TAILWIND_STRUCTURAL_APP_ROLE_RULE_IDS.items():
        if role not in TAILWIND_APP_V1_APP_ROLE_CONTRACTS or rule_id not in TAILWIND_APP_ROLE_RULE_PRECEDENCE:
            _fail("APP_ROLE_UNDECLARED_CONTRACT", f"Structural app-role rule {rule_id} is not declared in registry precedence.")
    for key in (
        "app_role:collection_action_bar",
        "app_role:loading_state",
        "app_role:error_state",
        "motif:loading_state:surface",
        "motif:error_state:surface",
        "role:state:title",
        "role:state:description",
    ):
        if key not in RECIPE_BY_KEY:
            _fail("TAILWIND_STATEFUL_COLLECTION_RECIPE_MISSING", f"Missing checked Stateful Collections Tailwind recipe: {key}.")
    if len(RECIPE_BY_KEY) > TAILWIND_MAX_RECIPES:
        _fail("TAILWIND_LIMIT_EXCEEDED_RECIPES", "tailwind_app_v1 recipe registry exceeds 96 recipes.")
    for key, value in RECIPE_BY_KEY.items():
        if not isinstance(key, str) or not key:
            _fail("TAILWIND_RECIPE_CONFLICT", "Tailwind recipe keys must be non-empty strings.")
        _validate_class_string(value, code="TAILWIND_HOST_CONFIG_DEPENDENCY")
    for classes in LAYOUT_EMPHASIS_CLASS_BY_VALUE.values():
        _validate_class_string(classes, code="TAILWIND_AESTHETIC_UNSAFE_CLASS")
    _validate_aesthetic_recipe_overlays()


def _validate_aesthetic_recipe_overlays() -> None:
    for profile in AESTHETIC_PROFILE_TOKENS:
        overlay = TAILWIND_AESTHETIC_RECIPE_OVERLAYS.get(profile)
        if not isinstance(overlay, dict) or not overlay:
            _fail("TAILWIND_AESTHETIC_RECIPE_MISSING", f"Missing checked Tailwind aesthetic recipes for {profile}.")
        if len(" ".join(overlay.values()).encode("utf-8")) > MAX_AESTHETIC_PROFILE_CSS_BYTES:
            _fail("TAILWIND_AESTHETIC_RECIPE_TOO_LARGE", f"Tailwind aesthetic recipes for {profile} exceed 2KB.")
        for key, classes in overlay.items():
            if key not in RECIPE_BY_KEY:
                _fail("TAILWIND_AESTHETIC_RECIPE_MISSING", f"{profile} overlays unknown recipe {key}.")
            _validate_class_string(classes, code="TAILWIND_AESTHETIC_UNSAFE_CLASS")


def _validate_class_string(classes: str, *, code: str) -> None:
    if not classes or classes != " ".join(classes.split()):
        _fail(code, "Tailwind recipe classes must be non-empty normalized literal strings.")
    for token in classes.split():
        if not CLASS_TOKEN_RE.match(token):
            _fail(code, f"Tailwind class token is not closed and literal: {token}.")
        if HOST_CONFIG_DEPENDENT_CLASS_RE.search(token):
            _fail("TAILWIND_HOST_CONFIG_DEPENDENCY", f"Tailwind class token depends on host config: {token}.")


def _grid_columns(node: IRNode) -> int:
    try:
        columns = int(node.props.get("columns") or 1)
    except (TypeError, ValueError) as exc:
        raise CompilerConstraintError("TAILWIND_LIMIT_EXCEEDED_GRID_COLUMNS", f"Grid IRNode '{node.id}' columns must be 1, 2, or 3.") from exc
    if columns not in TAILWIND_GRID_COLUMNS:
        _fail("TAILWIND_LIMIT_EXCEEDED_GRID_COLUMNS", f"Grid IRNode '{node.id}' columns must be 1, 2, or 3.")
    return columns


def _grid_span_classes(node: IRNode) -> list[str]:
    span_columns = node.props.get("span_columns")
    if span_columns is None:
        return []
    if not isinstance(span_columns, int) or isinstance(span_columns, bool) or span_columns not in GRID_SPAN_CLASS_BY_COLUMNS:
        _fail("TAILWIND_LIMIT_EXCEEDED_GRID_COLUMNS", f"IRNode '{node.id}' span_columns must be 2 or 3.")
    return GRID_SPAN_CLASS_BY_COLUMNS[span_columns].split()


def _layout_emphasis_classes(node: IRNode) -> list[str]:
    value = node.props.get("layout_emphasis")
    if value is None:
        return []
    if not isinstance(value, str) or value not in LAYOUT_EMPHASIS_CLASS_BY_VALUE:
        allowed = ", ".join(sorted(LAYOUT_EMPHASIS_CLASS_BY_VALUE))
        _fail("TAILWIND_AESTHETIC_UNSAFE_CLASS", f"IRNode '{node.id}' layout_emphasis must be one of: {allowed}.")
    return LAYOUT_EMPHASIS_CLASS_BY_VALUE[value].split()


def _role_contract_matches(node: IRNode, app_role: str) -> bool:
    contract = TAILWIND_APP_V1_APP_ROLE_CONTRACTS[app_role]
    primitives = contract.get("primitives", ())
    if primitives and node.primitive not in primitives:
        return False
    product_roles = contract.get("product_roles", ())
    if product_roles and node.props.get("product_role") not in product_roles:
        return False
    motif_kinds = contract.get("motif_kinds", ())
    if motif_kinds and node.props.get("motif_kind") not in motif_kinds:
        return False
    state_roles = contract.get("state_roles", ())
    if state_roles:
        state_role = "error" if node.primitive == "error_boundary" else node.props.get("state_role")
        if state_role not in state_roles:
            return False
    return True


def _derive_app_role(node: IRNode, parent: IRNode | None) -> AppRoleDerivation | None:
    if node.props.get("app_role") is not None or node.props.get("app_role_source") is not None:
        _fail("APP_ROLE_LEXICAL_SOURCE", f"IRNode '{node.id}' declares Tailwind app-role metadata; roles are emitter-derived only.")
    product_role = node.props.get("product_role")
    if isinstance(product_role, str) and product_role in TAILWIND_PRODUCT_APP_ROLE_RULE_IDS:
        derivation = AppRoleDerivation(product_role, TAILWIND_PRODUCT_APP_ROLE_RULE_IDS[product_role])
        if not _role_contract_matches(node, derivation.app_role):
            _fail("APP_ROLE_DERIVATION_MISMATCH", f"IRNode '{node.id}' product role {product_role} violates its app-role contract.")
        return derivation
    structural = _derive_structural_app_role(node, parent)
    if structural is not None and not _role_contract_matches(node, structural.app_role):
        _fail("APP_ROLE_DERIVATION_MISMATCH", f"IRNode '{node.id}' violates app-role contract {structural.app_role}.")
    return structural


def _derive_structural_app_role(node: IRNode, parent: IRNode | None) -> AppRoleDerivation | None:
    if (
        node.primitive == "cluster"
        and node.props.get("product_role") == "action_row"
        and node.props.get("layout_strategy") == "collection_action_bar_v1"
    ):
        return AppRoleDerivation("collection_action_bar", TAILWIND_STRUCTURAL_APP_ROLE_RULE_IDS["collection_action_bar"])
    if (
        node.primitive == "cluster"
        and node.props.get("product_role") == "action_row"
        and node.props.get("layout_strategy") == "action_row_v1"
        and parent is not None
        and parent.props.get("motif_kind") == "form"
    ):
        return AppRoleDerivation("filter_bar", TAILWIND_STRUCTURAL_APP_ROLE_RULE_IDS["filter_bar"])
    if node.primitive == "cluster" and node.props.get("product_role") == "action_row":
        return AppRoleDerivation("toolbar", TAILWIND_STRUCTURAL_APP_ROLE_RULE_IDS["toolbar"])
    if (
        node.props.get("motif_kind") == "list"
        and node.primitive == "stack"
        and parent is not None
        and parent.props.get("product_role") == "side_rail"
    ):
        return AppRoleDerivation("sidebar_nav", TAILWIND_STRUCTURAL_APP_ROLE_RULE_IDS["sidebar_nav"])
    if (
        node.props.get("motif_kind") == "list"
        and node.primitive == "stack"
        and parent is not None
        and parent.props.get("product_role") == "app_header"
    ):
        return AppRoleDerivation("top_nav", TAILWIND_STRUCTURAL_APP_ROLE_RULE_IDS["top_nav"])
    if node.props.get("motif_kind") == "table" and node.primitive == "stack":
        return AppRoleDerivation("data_table", TAILWIND_STRUCTURAL_APP_ROLE_RULE_IDS["data_table"])
    if node.props.get("motif_kind") == "table" and node.primitive == "cluster":
        return AppRoleDerivation("data_row", TAILWIND_STRUCTURAL_APP_ROLE_RULE_IDS["data_row"])
    if node.props.get("motif_kind") == "empty_state" and node.primitive == "surface":
        return AppRoleDerivation("empty_state", TAILWIND_STRUCTURAL_APP_ROLE_RULE_IDS["empty_state"])
    if node.props.get("state_role") == "loading" and node.primitive == "surface":
        return AppRoleDerivation("loading_state", TAILWIND_STRUCTURAL_APP_ROLE_RULE_IDS["loading_state"])
    if node.props.get("state_role") == "error" and node.primitive == "surface":
        return AppRoleDerivation("error_state", TAILWIND_STRUCTURAL_APP_ROLE_RULE_IDS["error_state"])
    return None


def _role_key(node: IRNode) -> str | None:
    for prop_name, prefix in (
        ("detail_role", "role:detail"),
        ("empty_state_role", "role:empty_state"),
        ("hero_role", "role:hero"),
        ("state_motif_role", "role:state"),
        ("table_cell_role", "role:table"),
    ):
        value = node.props.get(prop_name)
        if not isinstance(value, str):
            continue
        key = f"{prefix}:{value}"
        if key in RECIPE_BY_KEY:
            return key
    return None


def _recipe_key_for_node(node: IRNode, parent: IRNode | None) -> tuple[AppRoleDerivation | None, str]:
    app_role = _derive_app_role(node, parent)
    if app_role is not None:
        return app_role, f"app_role:{app_role.app_role}"
    product_role = node.props.get("product_role")
    if isinstance(product_role, str):
        key = f"product_role:{product_role}"
        if key in RECIPE_BY_KEY:
            return None, key
    role_key = _role_key(node)
    if role_key is not None:
        return None, role_key
    motif_kind = node.props.get("motif_kind")
    if isinstance(motif_kind, str):
        key = f"motif:{motif_kind}:{node.primitive}"
        if key in RECIPE_BY_KEY:
            return None, key
    layout_role = node.props.get("layout_role")
    if isinstance(layout_role, str):
        key = f"layout:{layout_role}"
        if key in RECIPE_BY_KEY:
            return None, key
    key = f"primitive:{node.primitive}"
    if key in RECIPE_BY_KEY:
        return None, key
    _fail("TAILWIND_RECIPE_CONFLICT", f"IRNode '{node.id}' has no total Tailwind recipe.")
    raise AssertionError("unreachable")


def _aesthetic_profile_for_root(root: IRNode) -> str | None:
    profile = root.props.get("aesthetic_profile")
    if profile is None:
        return None
    if not isinstance(profile, str) or profile not in AESTHETIC_PROFILE_TOKENS:
        _fail("TAILWIND_AESTHETIC_RECIPE_MISSING", "Root aesthetic_profile must be a supported checked profile.")
    return profile


def _classes_for_recipe(recipe_key: str, aesthetic_profile: str | None) -> list[str]:
    classes = RECIPE_BY_KEY[recipe_key].split()
    if aesthetic_profile is not None:
        overlay = TAILWIND_AESTHETIC_RECIPE_OVERLAYS.get(aesthetic_profile)
        if overlay is None:
            _fail("TAILWIND_AESTHETIC_RECIPE_MISSING", f"Missing checked Tailwind aesthetic recipes for {aesthetic_profile}.")
        overlay_classes = overlay.get(recipe_key)
        if overlay_classes:
            classes = _merge_tailwind_overlay_classes(classes, overlay_classes.split())
    return classes


def _merge_tailwind_overlay_classes(base: list[str], overlay: list[str]) -> list[str]:
    overlay_keys = {_tailwind_conflict_key(token) for token in overlay}
    overlay_keys.discard(None)
    merged = [token for token in base if _tailwind_conflict_key(token) not in overlay_keys]
    merged.extend(overlay)
    return merged


def _tailwind_conflict_key(token: str) -> tuple[str, str] | None:
    parts = token.split(":")
    variants = ":".join(parts[:-1])
    utility = parts[-1]
    group = _tailwind_utility_group(utility)
    if group is None:
        return None
    return variants, group


def _tailwind_utility_group(utility: str) -> str | None:
    if utility.startswith("bg-"):
        return "background-color"
    if utility in TEXT_SIZE_CLASSES:
        return "font-size"
    if utility.startswith("text-"):
        return "color"
    if utility in FONT_FAMILY_CLASSES:
        return "font-family"
    if utility in FONT_WEIGHT_CLASSES:
        return "font-weight"
    if utility.startswith("px-"):
        return "padding-x"
    if utility.startswith("py-"):
        return "padding-y"
    if utility.startswith("p-"):
        return "padding"
    if utility.startswith("rounded"):
        return "border-radius"
    if utility.startswith("border-") and utility != "border-t":
        return "border-color"
    if utility.startswith("shadow"):
        return "box-shadow"
    if utility.startswith("min-h-"):
        return "min-height"
    if utility.startswith("gap-"):
        return "gap"
    if utility.startswith("col-span-"):
        return "grid-column"
    if utility.startswith("leading-"):
        return "line-height"
    if utility.startswith("tracking-"):
        return "letter-spacing"
    if utility.startswith("ring-") and utility not in {"ring-0", "ring-1", "ring-2", "ring-4", "ring-8"}:
        return "ring-color"
    if utility in {"uppercase", "normal-case", "lowercase", "capitalize"}:
        return "text-transform"
    return None


def _resolve_recipe(node: IRNode, parent: IRNode | None, aesthetic_profile: str | None = None) -> ResolvedRecipe:
    app_role, recipe_key = _recipe_key_for_node(node, parent)
    classes = _classes_for_recipe(recipe_key, aesthetic_profile)
    if node.primitive == "grid":
        classes.extend(GRID_CLASS_BY_COLUMNS[_grid_columns(node)].split())
    classes.extend(_grid_span_classes(node))
    classes.extend(_layout_emphasis_classes(node))
    return ResolvedRecipe(
        app_role=app_role.app_role if app_role is not None else None,
        app_role_source=app_role.rule_id if app_role is not None else None,
        recipe_key=recipe_key,
        classes=tuple(dict.fromkeys(classes)),
    )


def _node_parent_map(root: IRNode) -> dict[str, IRNode | None]:
    parents: dict[str, IRNode | None] = {root.id: None}

    def walk(node: IRNode) -> None:
        for child in node.children:
            parents[child.id] = node
            walk(child)

    walk(root)
    return parents


def _is_role_bearing(node: IRNode) -> bool:
    return any(
        node.props.get(prop) is not None
        for prop in (
            "detail_role",
            "empty_state_role",
            "hero_role",
            "layout_role",
            "motif_kind",
            "product_role",
            "state_motif_role",
            "state_role",
            "table_cell_role",
        )
    )


def _validate_tailwind_contract(root: IRNode, recipes: dict[str, ResolvedRecipe], source: str) -> None:
    nodes = _walk(root)
    if len(nodes) > TAILWIND_MAX_IR_NODES:
        _fail("TAILWIND_LIMIT_EXCEEDED_NODES", "react-tailwind-tsx output is capped at 600 IR nodes.")
    if _depth(root) > TAILWIND_MAX_IR_DEPTH:
        _fail("TAILWIND_LIMIT_EXCEEDED_DEPTH", "react-tailwind-tsx output is capped at IR depth 16.")
    action_count = sum(1 for node in nodes if node.primitive == "button" and node.props.get("action_id"))
    if action_count > TAILWIND_MAX_ACTIONS:
        _fail("TAILWIND_LIMIT_EXCEEDED_ACTIONS", "react-tailwind-tsx output is capped at 128 actions.")
    recipe_keys = {recipe.recipe_key for recipe in recipes.values()}
    if len(recipe_keys) > TAILWIND_MAX_RECIPES:
        _fail("TAILWIND_LIMIT_EXCEEDED_RECIPES", "react-tailwind-tsx output is capped at 96 recipes per artifact.")
    class_tokens = {token for recipe in recipes.values() for token in recipe.classes}
    if len(class_tokens) > TAILWIND_MAX_CLASS_TOKENS:
        _fail("TAILWIND_LIMIT_EXCEEDED_CLASS_TOKENS", "react-tailwind-tsx output is capped at 512 unique class tokens.")
    if len(source.encode("utf-8")) > TAILWIND_MAX_ARTIFACT_BYTES:
        _fail("TAILWIND_LIMIT_EXCEEDED_ARTIFACT_BYTES", "react-tailwind-tsx output is capped at 256 KiB.")
    role_bearing = [node for node in nodes if _is_role_bearing(node)]
    generic_fallbacks = [
        node for node in role_bearing if recipes[node.id].recipe_key.startswith("primitive:")
    ]
    if role_bearing and (len(generic_fallbacks) / len(role_bearing)) > TAILWIND_GENERIC_FALLBACK_RATIO:
        _fail("TAILWIND_GENERIC_FALLBACK_EXCEEDED", "More than 25% of role-bearing nodes used generic Tailwind fallback.")

def _resolve_recipes(root: IRNode) -> dict[str, ResolvedRecipe]:
    parents = _node_parent_map(root)
    aesthetic_profile = _aesthetic_profile_for_root(root)
    return {node.id: _resolve_recipe(node, parents[node.id], aesthetic_profile) for node in _walk(root)}


def resolve_manifest_recipe_metadata(
    entry: dict[str, Any],
    parent_entry: dict[str, Any] | None = None,
    *,
    aesthetic_profile: str | None = None,
) -> dict[str, Any]:
    """Recompute Tailwind recipe metadata from manifest node shape for artifact checks."""
    node = _manifest_entry_to_ir_node(entry)
    parent = _manifest_entry_to_ir_node(parent_entry) if parent_entry is not None else None
    recipe = _resolve_recipe(node, parent, aesthetic_profile)
    return {
        "app_role": recipe.app_role,
        "app_role_source": recipe.app_role_source,
        "recipe_pack": TAILWIND_RECIPE_PACK,
        "recipe_key": recipe.recipe_key,
        "classes": list(recipe.classes),
    }


def _manifest_entry_to_ir_node(entry: dict[str, Any] | None) -> IRNode:
    if not isinstance(entry, dict):
        return IRNode(id="", primitive="")
    props = entry.get("props") if isinstance(entry.get("props"), dict) else {}
    return IRNode(
        id=str(entry.get("ir_id") or ""),
        primitive=str(entry.get("primitive") or ""),
        props=dict(props),
        style_tokens=[item for item in entry.get("style_tokens", []) if isinstance(item, str)],
    )


def tailwind_recipe_registry_digest() -> str:
    payload = tailwind_recipe_registry_projection()
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def tailwind_recipe_registry_projection() -> dict[str, Any]:
    return {
        "recipe_pack": TAILWIND_RECIPE_PACK,
        "registry_version": TAILWIND_RECIPE_REGISTRY_VERSION,
        "recipes": dict(sorted(RECIPE_BY_KEY.items())),
        "aesthetic_recipe_overlays": {
            profile: dict(sorted(overlay.items()))
            for profile, overlay in sorted(TAILWIND_AESTHETIC_RECIPE_OVERLAYS.items())
        },
        "layout_emphasis_classes": dict(sorted(LAYOUT_EMPHASIS_CLASS_BY_VALUE.items())),
        "app_role_contracts": {
            role: {key: list(values) for key, values in sorted(contract.items())}
            for role, contract in sorted(TAILWIND_APP_V1_APP_ROLE_CONTRACTS.items())
        },
        "derivation_rule_ids": {
            "product": dict(sorted(TAILWIND_PRODUCT_APP_ROLE_RULE_IDS.items())),
            "structural": dict(sorted(TAILWIND_STRUCTURAL_APP_ROLE_RULE_IDS.items())),
        },
        "precedence_order": list(TAILWIND_APP_ROLE_RULE_PRECEDENCE),
        "caps": {
            "artifact_bytes": TAILWIND_MAX_ARTIFACT_BYTES,
            "class_tokens": TAILWIND_MAX_CLASS_TOKENS,
            "grid_columns": sorted(TAILWIND_GRID_COLUMNS),
            "grid_spans": sorted(GRID_SPAN_CLASS_BY_COLUMNS),
            "ir_depth": TAILWIND_MAX_IR_DEPTH,
            "ir_nodes": TAILWIND_MAX_IR_NODES,
            "actions": TAILWIND_MAX_ACTIONS,
            "app_roles": TAILWIND_MAX_APP_ROLES,
            "recipes": TAILWIND_MAX_RECIPES,
            "recipe_precedence_tiers": TAILWIND_MAX_RECIPE_PRECEDENCE_TIERS,
        },
    }


__all__ = [
    "AppRoleDerivation",
    "CLASS_TOKEN_RE",
    "CompilerConstraintError",
    "FONT_FAMILY_CLASSES",
    "FONT_WEIGHT_CLASSES",
    "GRID_CLASS_BY_COLUMNS",
    "GRID_SPAN_CLASS_BY_COLUMNS",
    "HOST_CONFIG_DEPENDENT_CLASS_RE",
    "LAYOUT_EMPHASIS_CLASS_BY_VALUE",
    "RECIPE_BY_KEY",
    "ResolvedRecipe",
    "TAILWIND_AESTHETIC_RECIPE_OVERLAYS",
    "TAILWIND_APP_ROLE_RULE_PRECEDENCE",
    "TAILWIND_APP_V1_APP_ROLE_CONTRACTS",
    "TAILWIND_CONSTRAINT_CODES",
    "TAILWIND_GENERIC_FALLBACK_RATIO",
    "TAILWIND_GRID_COLUMNS",
    "TAILWIND_MAX_ACTIONS",
    "TAILWIND_MAX_APP_ROLES",
    "TAILWIND_MAX_ARTIFACT_BYTES",
    "TAILWIND_MAX_CLASS_TOKENS",
    "TAILWIND_MAX_IR_DEPTH",
    "TAILWIND_MAX_IR_NODES",
    "TAILWIND_MAX_RECIPE_PRECEDENCE_TIERS",
    "TAILWIND_MAX_RECIPES",
    "TAILWIND_PRODUCT_APP_ROLE_RULE_IDS",
    "TAILWIND_RECIPE_PACK",
    "TAILWIND_RECIPE_REGISTRY_VERSION",
    "TAILWIND_STRUCTURAL_APP_ROLE_RULE_IDS",
    "TEXT_SIZE_CLASSES",
    "_resolve_recipes",
    "_validate_recipe_registry",
    "_validate_tailwind_contract",
    "_walk",
    "resolve_manifest_recipe_metadata",
    "tailwind_recipe_registry_digest",
    "tailwind_recipe_registry_projection",
]
