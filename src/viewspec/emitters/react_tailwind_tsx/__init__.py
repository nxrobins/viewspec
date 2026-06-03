"""Deterministic React TSX emitter using closed Tailwind utility recipes."""

from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from viewspec.emitters.base import EmitterPlugin
from viewspec.emitters.html_tailwind import (
    SUPPORTED_PRIMITIVES,
    _manifest_entry,
    _validate_ir_contract,
    _validate_style_values,
    _write_text_atomic,
)
from viewspec.emitters.react_tsx import (
    _action_expression,
    _compiled_payload_values,
    _initial_input_values,
    _json_string_attr,
    _node_fallback_text,
    _safe_json_literal,
    _tag_for_node,
    _text_expression,
    _tsx_string,
)
from viewspec.types import ASTBundle, CompilerResult, DEFAULT_STYLE_TOKEN_VALUES, IRNode


TAILWIND_RECIPE_PACK = "tailwind_app_v1"
TAILWIND_RECIPE_REGISTRY_VERSION = "tailwind_recipe_registry.v1"
TAILWIND_MAX_IR_NODES = 600
TAILWIND_MAX_IR_DEPTH = 16
TAILWIND_MAX_ACTIONS = 128
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
        "TAILWIND_STYLE_CONSTRAINT_VIOLATION",
        "TAILWIND_UNSAFE_CLASS_SOURCE",
        "TAILWIND_UNSUPPORTED_PRIMITIVE",
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
    "empty_state": {"primitives": ("surface",), "motif_kinds": ("empty_state",)},
    "loading_state": {"primitives": ("surface",), "state_roles": ("loading",)},
    "error_state": {"primitives": ("error_boundary",), "state_roles": ("error",)},
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
    "error_state": "tailwind_app_v1.structural.error_state_from_error_boundary",
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
    TAILWIND_STRUCTURAL_APP_ROLE_RULE_IDS["error_state"],
)


RECIPE_BY_KEY: dict[str, str] = {
    "app_role:app_shell": "min-h-screen bg-slate-50 px-6 py-6 text-slate-950 sm:px-8",
    "app_role:app_header": "border-b border-slate-200 pb-4 pt-1",
    "app_role:page_header": "bg-transparent p-0 shadow-none ring-0",
    "app_role:content_grid": "grid gap-5 lg:grid-cols-3",
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
    "app_role:metric_grid": "grid gap-3 sm:grid-cols-2 lg:grid-cols-2",
    "app_role:metric_card": "min-h-28 justify-between rounded-md border border-slate-200 bg-white p-4 shadow-sm",
    "app_role:form_panel": "rounded-md border border-slate-200 bg-white p-5 shadow-sm",
    "app_role:field_group": "rounded-md border border-slate-200 bg-slate-50 p-3 shadow-none",
    "app_role:detail_panel": "rounded-md border border-slate-200 bg-white p-4 shadow-sm",
    "app_role:action_row": "flex flex-row flex-wrap items-center justify-end gap-2 pt-1",
    "app_role:empty_state": "items-center rounded-md border border-dashed border-slate-300 bg-white p-8 text-center",
    "app_role:loading_state": "rounded-md border border-slate-200 bg-white p-4 text-slate-500",
    "app_role:error_state": "rounded-md border border-red-300 bg-red-50 p-4 font-mono text-sm text-red-800",
    "app_role:overlay_panel": "rounded-md border border-slate-200 bg-white p-5 shadow-lg",
    "product_role:app_shell": "mx-auto flex w-full max-w-6xl flex-col gap-5",
    "product_role:app_header": "flex flex-col gap-2",
    "product_role:page_header": "flex flex-col gap-2",
    "product_role:content_grid": "items-start",
    "product_role:primary_column": "min-w-0",
    "product_role:side_rail": "min-w-0",
    "product_role:metric_grid": "grid gap-3 sm:grid-cols-2",
    "product_role:metric_card": "flex flex-col gap-3",
    "product_role:form_panel": "flex flex-col gap-4",
    "product_role:field_group": "flex flex-col gap-2",
    "product_role:detail_panel": "flex flex-col gap-3",
    "product_role:action_row": "flex flex-row flex-wrap items-center justify-end gap-2",
    "motif:comparison:stack": "flex flex-col gap-3",
    "motif:dashboard:grid": "grid gap-3 sm:grid-cols-2",
    "motif:detail:cluster": "grid gap-2 border-b border-slate-200 py-2 last:border-b-0 sm:grid-cols-3",
    "motif:detail:stack": "m-0 flex flex-col gap-0",
    "motif:empty_state:surface": "flex flex-col gap-3",
    "motif:form:stack": "flex flex-col gap-4",
    "motif:form:surface": "flex flex-col gap-2",
    "motif:hero:surface": "flex flex-col gap-3",
    "motif:list:stack": "m-0 flex list-none flex-col gap-2 p-0",
    "motif:list:surface": "rounded-md border border-slate-200 bg-white p-3",
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

GRID_CLASS_BY_COLUMNS = {
    1: "grid-cols-1",
    2: "grid-cols-1 sm:grid-cols-2",
    3: "grid-cols-1 sm:grid-cols-2 lg:grid-cols-3",
}


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
    if len(TAILWIND_APP_V1_APP_ROLE_CONTRACTS) > 24:
        _fail("APP_ROLE_UNDECLARED_CONTRACT", "tailwind_app_v1 app-role registry exceeds 24 roles.")
    for role in TAILWIND_APP_V1_APP_ROLE_CONTRACTS:
        if f"app_role:{role}" not in RECIPE_BY_KEY:
            _fail("APP_ROLE_UNDECLARED_CONTRACT", f"App role {role} has no checked-in recipe.")
    for role, rule_id in TAILWIND_PRODUCT_APP_ROLE_RULE_IDS.items():
        if role not in TAILWIND_APP_V1_APP_ROLE_CONTRACTS or rule_id not in TAILWIND_APP_ROLE_RULE_PRECEDENCE:
            _fail("APP_ROLE_UNDECLARED_CONTRACT", f"Product app-role rule {rule_id} is not declared in registry precedence.")
    for role, rule_id in TAILWIND_STRUCTURAL_APP_ROLE_RULE_IDS.items():
        if role not in TAILWIND_APP_V1_APP_ROLE_CONTRACTS or rule_id not in TAILWIND_APP_ROLE_RULE_PRECEDENCE:
            _fail("APP_ROLE_UNDECLARED_CONTRACT", f"Structural app-role rule {rule_id} is not declared in registry precedence.")
    if len(RECIPE_BY_KEY) > TAILWIND_MAX_RECIPES:
        _fail("TAILWIND_LIMIT_EXCEEDED_RECIPES", "tailwind_app_v1 recipe registry exceeds 96 recipes.")
    for key, value in RECIPE_BY_KEY.items():
        if not isinstance(key, str) or not key:
            _fail("TAILWIND_RECIPE_CONFLICT", "Tailwind recipe keys must be non-empty strings.")
        _validate_class_string(value, code="TAILWIND_HOST_CONFIG_DEPENDENCY")


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
    if node.primitive == "error_boundary":
        return AppRoleDerivation("error_state", TAILWIND_STRUCTURAL_APP_ROLE_RULE_IDS["error_state"])
    return None


def _role_key(node: IRNode) -> str | None:
    for prop_name, prefix in (
        ("detail_role", "role:detail"),
        ("empty_state_role", "role:empty_state"),
        ("hero_role", "role:hero"),
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


def _resolve_recipe(node: IRNode, parent: IRNode | None) -> ResolvedRecipe:
    app_role, recipe_key = _recipe_key_for_node(node, parent)
    classes = RECIPE_BY_KEY[recipe_key].split()
    if node.primitive == "grid":
        classes.extend(GRID_CLASS_BY_COLUMNS[_grid_columns(node)].split())
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


def _jsx_attr(name: str, value: object) -> str:
    return f"{name}={{{_tsx_string(value)}}}"


def _literal_attr(name: str, value: str) -> str:
    if '"' in value or "\n" in value or "\r" in value:
        _fail("TAILWIND_UNSAFE_CLASS_SOURCE", f"Attribute {name} must be a normalized literal string.")
    return f'{name}="{value}"'


def _attrs_for_node(node: IRNode, recipe: ResolvedRecipe) -> list[str]:
    dom_id = f"dom-{node.id}"
    attrs = [
        _jsx_attr("id", dom_id),
        _jsx_attr("data-ir-id", node.id),
        _jsx_attr("data-content-refs", _json_string_attr(list(node.provenance.content_refs))),
        _jsx_attr("data-intent-refs", _json_string_attr(list(node.provenance.intent_refs))),
        _jsx_attr("data-style-tokens", _json_string_attr(list(node.style_tokens))),
        _literal_attr("className", " ".join(recipe.classes)),
    ]
    if node.props.get("binding_id") is not None:
        attrs.append(_jsx_attr("data-binding-id", str(node.props["binding_id"])))
    if node.primitive == "button":
        attrs.extend(
            [
                'type="button"',
                _jsx_attr("data-action-id", str(node.props.get("action_id", ""))),
                _jsx_attr("data-action-kind", str(node.props.get("action_kind", ""))),
                _jsx_attr("data-action-target-ref", str(node.props.get("target_ref", ""))),
                _jsx_attr("data-payload-bindings", _json_string_attr(node.props.get("payload_bindings", []))),
                f"onClick={_action_expression(node).replace('viewspec-react-tsx', 'viewspec-react-tailwind-tsx')}",
            ]
        )
    elif node.primitive == "input":
        binding_id = str(node.props.get("binding_id", node.id))
        attrs.extend(
            [
                'type="text"',
                f"name={{{_tsx_string(binding_id)}}}",
                f"value={{String(inputValues[{_tsx_string(binding_id)}] ?? \"\")}}",
                f"onChange={{(event) => setInputValue({_tsx_string(binding_id)}, event.target.value)}}",
                _jsx_attr("aria-label", str(node.props.get("aria_label", binding_id))),
            ]
        )
    elif node.primitive in {"image_slot", "svg"}:
        attrs.extend(['role="img"', _jsx_attr("aria-label", _node_fallback_text(node))])
    elif node.primitive == "error_boundary":
        attrs.append('role="alert"')
    elif node.props.get("motif_kind") == "form" and node.primitive == "stack":
        attrs.extend(['role="form"', _jsx_attr("aria-label", str(node.props.get("label", node.id)))])
    elif node.props.get("motif_kind") == "form" and node.primitive == "surface":
        attrs.append('role="group"')
    elif node.props.get("motif_kind") == "empty_state" and node.primitive == "surface":
        attrs.append(_jsx_attr("aria-label", str(node.props.get("aria_label", "Empty state"))))
    elif node.props.get("motif_kind") == "hero" and node.primitive == "surface":
        attrs.append(_jsx_attr("aria-label", str(node.props.get("aria_label", "Hero"))))
    elif node.props.get("table_cell_role") == "row_header":
        attrs.append('scope="row"')
    return attrs


def _manifest_entry_for_node(node: IRNode, recipe: ResolvedRecipe) -> dict[str, Any]:
    entry = _manifest_entry(node)
    entry["classes"] = list(recipe.classes)
    entry["recipe_pack"] = TAILWIND_RECIPE_PACK
    entry["recipe_key"] = recipe.recipe_key
    if recipe.app_role is not None:
        entry["app_role"] = recipe.app_role
        entry["app_role_source"] = recipe.app_role_source
    return entry


def _render_node(
    node: IRNode,
    *,
    manifest: dict[str, Any],
    recipes: dict[str, ResolvedRecipe],
    indent: int = 4,
) -> list[str]:
    dom_id = f"dom-{node.id}"
    recipe = recipes[node.id]
    manifest[dom_id] = _manifest_entry_for_node(node, recipe)
    pad = " " * indent
    child_pad = " " * (indent + 2)
    tag = _tag_for_node(node)
    attrs = " ".join(_attrs_for_node(node, recipe))
    if node.primitive in {"input", "rule"}:
        return [f"{pad}<{tag} {attrs} />"]
    if node.primitive in {"text", "label", "value", "badge", "button", "image_slot", "svg", "error_boundary"}:
        return [f"{pad}<{tag} {attrs}>{_text_expression(node)}</{tag}>"]
    lines = [f"{pad}<{tag} {attrs}>"]
    if tag == "table":
        lines.append(f"{child_pad}<tbody>")
        for child in node.children:
            lines.extend(_render_node(child, manifest=manifest, recipes=recipes, indent=indent + 4))
        lines.append(f"{child_pad}</tbody>")
    else:
        for child in node.children:
            lines.extend(_render_node(child, manifest=manifest, recipes=recipes, indent=indent + 2))
    lines.append(f"{pad}</{tag}>")
    return lines


def _resolve_recipes(root: IRNode) -> dict[str, ResolvedRecipe]:
    parents = _node_parent_map(root)
    return {node.id: _resolve_recipe(node, parents[node.id]) for node in _walk(root)}


def resolve_manifest_recipe_metadata(entry: dict[str, Any], parent_entry: dict[str, Any] | None = None) -> dict[str, Any]:
    """Recompute Tailwind recipe metadata from manifest node shape for artifact checks."""
    node = _manifest_entry_to_ir_node(entry)
    parent = _manifest_entry_to_ir_node(parent_entry) if parent_entry is not None else None
    recipe = _resolve_recipe(node, parent)
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
            "ir_depth": TAILWIND_MAX_IR_DEPTH,
            "ir_nodes": TAILWIND_MAX_IR_NODES,
            "actions": TAILWIND_MAX_ACTIONS,
            "recipes": TAILWIND_MAX_RECIPES,
            "recipe_precedence_tiers": TAILWIND_MAX_RECIPE_PRECEDENCE_TIERS,
        },
    }


def _emit_source(result: CompilerResult, title: str) -> tuple[str, dict[str, Any], dict[str, ResolvedRecipe]]:
    root = result.root.root
    recipes = _resolve_recipes(root)
    manifest: dict[str, Any] = {}
    rendered = _render_node(root, manifest=manifest, recipes=recipes)
    input_values = _safe_json_literal(_initial_input_values(root))
    compiled_values = _safe_json_literal(_compiled_payload_values(root))
    lines = [
        '"use client";',
        "",
        "import * as React from \"react\";",
        "",
        "export type ViewSpecActionIntent = {",
        "  schemaVersion: 1;",
        "  source: \"viewspec-react-tailwind-tsx\";",
        "  id: string;",
        "  kind: string;",
        "  targetRef: string;",
        "  payloadBindings: string[];",
        "  payloadValues: Record<string, unknown>;",
        "};",
        "",
        "export type ViewSpecData = Record<string, unknown>;",
        "",
        "export type ViewSpecViewProps = {",
        "  data?: ViewSpecData;",
        "  onAction?: (intent: ViewSpecActionIntent) => void;",
        "};",
        "",
        f"export const viewspecTitle = {_tsx_string(title)};",
        "",
        "function renderValue(value: unknown, fallback: React.ReactNode): React.ReactNode {",
        "  if (value == null) return fallback;",
        "  if (React.isValidElement(value)) return value;",
        "  if (typeof value === \"string\" || typeof value === \"number\") return value;",
        "  if (typeof value === \"boolean\") return value ? \"true\" : \"false\";",
        "  try {",
        "    return JSON.stringify(value);",
        "  } catch {",
        "    return fallback;",
        "  }",
        "}",
        "",
        "export function ViewSpecView({ data = {}, onAction }: ViewSpecViewProps) {",
        f"  const [inputValues, setInputValues] = React.useState<Record<string, unknown>>({input_values});",
        f"  const compiledPayloadValues: Record<string, unknown> = {compiled_values};",
        "  const setInputValue = (id: string, value: unknown) => {",
        "    setInputValues((current) => ({ ...current, [id]: value }));",
        "  };",
        "  const collectPayloadValues = (payloadBindings: string[]): Record<string, unknown> => {",
        "    const payloadValues: Record<string, unknown> = {};",
        "    payloadBindings.forEach((bindingId) => {",
        "      if (Object.prototype.hasOwnProperty.call(inputValues, bindingId)) {",
        "        payloadValues[bindingId] = inputValues[bindingId];",
        "      } else if (Object.prototype.hasOwnProperty.call(data, bindingId)) {",
        "        payloadValues[bindingId] = data[bindingId];",
        "      } else if (Object.prototype.hasOwnProperty.call(compiledPayloadValues, bindingId)) {",
        "        payloadValues[bindingId] = compiledPayloadValues[bindingId];",
        "      }",
        "    });",
        "    return payloadValues;",
        "  };",
        "  return (",
        *rendered,
        "  );",
        "}",
        "",
        "export default ViewSpecView;",
        "",
    ]
    source = "\n".join(lines)
    _validate_tailwind_contract(root, recipes, source)
    return source, manifest, recipes


def emit_compiler_result(
    result: CompilerResult,
    style_values: dict[str, str],
    *,
    output_dir: str | Path = "viewspec_react_tailwind_output",
    title: str = "ViewSpec Artifact",
) -> dict[str, str]:
    """Emit a CompilerResult as deterministic React TSX with closed Tailwind recipes."""
    _validate_recipe_registry()
    output_path = Path(output_dir)
    try:
        _validate_ir_contract(result.root.root, set())
    except ValueError as exc:
        raise CompilerConstraintError("TAILWIND_IR_CONTRACT_VIOLATION", str(exc)) from exc
    try:
        _validate_style_values(style_values)
    except ValueError as exc:
        raise CompilerConstraintError("TAILWIND_STYLE_CONSTRAINT_VIOLATION", str(exc)) from exc
    unsupported = {node.primitive for node in _walk(result.root.root)} - SUPPORTED_PRIMITIVES
    if unsupported:
        raise CompilerConstraintError(
            "TAILWIND_UNSUPPORTED_PRIMITIVE",
            f"Unsupported IR primitive(s) for React Tailwind TSX emitter: {', '.join(sorted(unsupported))}.",
        )
    tsx, manifest, _recipes = _emit_source(result, title)
    output_path.mkdir(parents=True, exist_ok=True)
    tsx_path = output_path / "ViewSpecView.tsx"
    manifest_path = output_path / "provenance_manifest.json"
    diagnostics_path = output_path / "diagnostics.json"
    try:
        _write_text_atomic(tsx_path, tsx)
        _write_text_atomic(manifest_path, json.dumps(manifest, indent=2))
        _write_text_atomic(diagnostics_path, json.dumps([d.to_json() for d in result.diagnostics], indent=2, sort_keys=True))
    except Exception as exc:
        try:
            _write_text_atomic(
                output_path / ".viewspec_write_failed.json",
                json.dumps(
                    {
                        "version": 1,
                        "severity": "error",
                        "code": "ARTIFACT_WRITE_FAILED",
                        "message": str(exc),
                    },
                    indent=2,
                    sort_keys=True,
                ),
            )
        except Exception:
            pass
        raise
    return {
        "tsx": str(tsx_path),
        "manifest": str(manifest_path),
        "diagnostics": str(diagnostics_path),
    }


class ReactTailwindTsxEmitter(EmitterPlugin):
    """Deterministic React Tailwind TSX emitter plugin."""

    def emit(self, ast_bundle: ASTBundle, output_dir: str | Path) -> dict[str, str]:
        style_values = dict(ast_bundle.style_values or DEFAULT_STYLE_TOKEN_VALUES)
        return emit_compiler_result(ast_bundle.result, style_values, output_dir=output_dir, title=ast_bundle.title)


PLUGIN_CLASS = ReactTailwindTsxEmitter


__all__ = [
    "GRID_CLASS_BY_COLUMNS",
    "CompilerConstraintError",
    "RECIPE_BY_KEY",
    "TAILWIND_APP_V1_APP_ROLE_CONTRACTS",
    "TAILWIND_APP_ROLE_RULE_PRECEDENCE",
    "TAILWIND_PRODUCT_APP_ROLE_RULE_IDS",
    "TAILWIND_RECIPE_REGISTRY_VERSION",
    "TAILWIND_RECIPE_PACK",
    "TAILWIND_STRUCTURAL_APP_ROLE_RULE_IDS",
    "ReactTailwindTsxEmitter",
    "emit_compiler_result",
    "resolve_manifest_recipe_metadata",
    "tailwind_recipe_registry_digest",
    "tailwind_recipe_registry_projection",
]
