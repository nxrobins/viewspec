"""Deterministic React TSX emitter using closed Tailwind utility recipes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from viewspec.emitters.base import (
    EmitterNodeContext,
    EmitterNodePlugin,
    EmitterNodeRegistry,
    EmitterPlugin,
    RenderedNode,
)
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
from viewspec.emitters.react_tailwind_tsx import recipes as _recipes
from viewspec.emitters.react_tailwind_tsx.recipes import (
    GRID_CLASS_BY_COLUMNS,
    LAYOUT_EMPHASIS_CLASS_BY_VALUE,
    RECIPE_BY_KEY,
    TAILWIND_AESTHETIC_RECIPE_OVERLAYS,
    TAILWIND_APP_ROLE_RULE_PRECEDENCE,
    TAILWIND_APP_V1_APP_ROLE_CONTRACTS,
    TAILWIND_MAX_ACTIONS,
    TAILWIND_MAX_ARTIFACT_BYTES,
    TAILWIND_MAX_CLASS_TOKENS,
    TAILWIND_MAX_IR_NODES,
    TAILWIND_MAX_RECIPES,
    TAILWIND_PRODUCT_APP_ROLE_RULE_IDS,
    TAILWIND_RECIPE_PACK,
    TAILWIND_RECIPE_REGISTRY_VERSION,
    TAILWIND_STRUCTURAL_APP_ROLE_RULE_IDS,
    CompilerConstraintError,
    ResolvedRecipe,
    _fail,
    _walk,
)
from viewspec.types import ASTBundle, CompilerResult, DEFAULT_STYLE_TOKEN_VALUES, IRNode


def _sync_recipe_module() -> None:
    _recipes.RECIPE_BY_KEY = RECIPE_BY_KEY
    _recipes.TAILWIND_AESTHETIC_RECIPE_OVERLAYS = TAILWIND_AESTHETIC_RECIPE_OVERLAYS
    _recipes.LAYOUT_EMPHASIS_CLASS_BY_VALUE = LAYOUT_EMPHASIS_CLASS_BY_VALUE
    _recipes.TAILWIND_APP_V1_APP_ROLE_CONTRACTS = TAILWIND_APP_V1_APP_ROLE_CONTRACTS
    _recipes.TAILWIND_APP_ROLE_RULE_PRECEDENCE = TAILWIND_APP_ROLE_RULE_PRECEDENCE
    _recipes.TAILWIND_PRODUCT_APP_ROLE_RULE_IDS = TAILWIND_PRODUCT_APP_ROLE_RULE_IDS
    _recipes.TAILWIND_STRUCTURAL_APP_ROLE_RULE_IDS = TAILWIND_STRUCTURAL_APP_ROLE_RULE_IDS


def _validate_recipe_registry() -> None:
    _sync_recipe_module()
    _recipes._validate_recipe_registry()


def _resolve_recipes(root: IRNode) -> dict[str, ResolvedRecipe]:
    _sync_recipe_module()
    return _recipes._resolve_recipes(root)


def _validate_tailwind_contract(root: IRNode, recipes: dict[str, ResolvedRecipe], source: str) -> None:
    _sync_recipe_module()
    _recipes._validate_tailwind_contract(root, recipes, source)


def resolve_manifest_recipe_metadata(
    entry: dict[str, Any],
    parent_entry: dict[str, Any] | None = None,
    *,
    aesthetic_profile: str | None = None,
) -> dict[str, Any]:
    """Recompute Tailwind recipe metadata from manifest node shape for artifact checks."""
    _sync_recipe_module()
    return _recipes.resolve_manifest_recipe_metadata(
        entry,
        parent_entry,
        aesthetic_profile=aesthetic_profile,
    )


def tailwind_recipe_registry_digest() -> str:
    _sync_recipe_module()
    return _recipes.tailwind_recipe_registry_digest()


def tailwind_recipe_registry_projection() -> dict[str, Any]:
    _sync_recipe_module()
    return _recipes.tailwind_recipe_registry_projection()



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
    aesthetic_profile = node.props.get("aesthetic_profile")
    if isinstance(aesthetic_profile, str) and aesthetic_profile:
        attrs.append(_jsx_attr("data-aesthetic-profile", aesthetic_profile))
    visibility_rule_id = node.props.get("visibility_rule_id")
    if isinstance(visibility_rule_id, str) and visibility_rule_id:
        rule_literal = _tsx_string(visibility_rule_id)
        attrs.extend(
            [
                _jsx_attr("data-visibility-rule", visibility_rule_id),
                f'data-visibility-state={{visibility[{rule_literal}] === false ? "hidden" : "visible"}}',
                f"hidden={{visibility[{rule_literal}] === false}}",
            ]
        )
    if node.primitive == "button":
        attrs.extend(
            [
                'type="button"',
                _jsx_attr("data-action-id", str(node.props.get("action_id", ""))),
                _jsx_attr("data-action-kind", str(node.props.get("action_kind", ""))),
                _jsx_attr("data-action-target-ref", str(node.props.get("target_ref", ""))),
                _jsx_attr("data-payload-bindings", _json_string_attr(node.props.get("payload_bindings", []))),
                f"onClick={_action_expression(node, source='viewspec-react-tailwind-tsx')}",
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
            ]
        )
        labelled_by = node.props.get("labelled_by")
        if isinstance(node.props.get("aria_label"), str):
            attrs.append(_jsx_attr("aria-label", str(node.props["aria_label"])))
        elif isinstance(labelled_by, str) and labelled_by:
            attrs.append(_jsx_attr("aria-labelledby", f"dom-{labelled_by}"))
        else:
            attrs.append(_jsx_attr("aria-label", binding_id))
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
    elif node.props.get("motif_kind") == "loading_state" and node.primitive == "surface":
        attrs.extend(
            [
                'role="status"',
                'aria-busy="true"',
                _jsx_attr("aria-label", str(node.props.get("aria_label", "Loading state"))),
            ]
        )
    elif node.props.get("motif_kind") == "error_state" and node.primitive == "surface":
        attrs.extend(['role="alert"', _jsx_attr("aria-label", str(node.props.get("aria_label", "Error state")))])
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


def _recipe_for_context_node(node: IRNode, context: EmitterNodeContext) -> ResolvedRecipe:
    recipes = context.state.get("recipes")
    if not isinstance(recipes, dict):
        _fail("TAILWIND_RECIPE_CONFLICT", "React Tailwind node renderer requires resolved recipe state.")
    recipe = recipes.get(node.id)
    if not isinstance(recipe, ResolvedRecipe):
        _fail("TAILWIND_RECIPE_CONFLICT", f"IRNode '{node.id}' has no resolved Tailwind recipe.")
    return recipe


def _render_node_spec(
    node: IRNode,
    context: EmitterNodeContext,
    *,
    text: str | None = None,
    self_closing: bool = False,
) -> RenderedNode:
    recipe = _recipe_for_context_node(node, context)
    tag = _tag_for_node(node)
    return RenderedNode(
        tag=tag,
        attrs=tuple(_attrs_for_node(node, recipe)),
        text=text,
        self_closing=self_closing,
        child_wrapper_tag="tbody" if tag == "table" else None,
        manifest_entry=_manifest_entry_for_node(node, recipe),
    )


def _matches_tailwind_self_closing(node: IRNode, context: EmitterNodeContext) -> bool:
    return context.target == "react-tailwind-tsx" and node.primitive in {"input", "rule"}


def _matches_tailwind_leaf(node: IRNode, context: EmitterNodeContext) -> bool:
    return context.target == "react-tailwind-tsx" and node.primitive in {
        "text",
        "label",
        "value",
        "badge",
        "button",
        "image_slot",
        "svg",
        "error_boundary",
    }


def _matches_tailwind_container(node: IRNode, context: EmitterNodeContext) -> bool:
    return context.target == "react-tailwind-tsx" and node.primitive in SUPPORTED_PRIMITIVES


def _render_tailwind_self_closing(node: IRNode, context: EmitterNodeContext) -> RenderedNode:
    return _render_node_spec(node, context, self_closing=True)


def _render_tailwind_leaf(node: IRNode, context: EmitterNodeContext) -> RenderedNode:
    return _render_node_spec(node, context, text=_text_expression(node))


def _render_tailwind_container(node: IRNode, context: EmitterNodeContext) -> RenderedNode:
    return _render_node_spec(node, context)


TAILWIND_NODE_REGISTRY = EmitterNodeRegistry(
    (
        EmitterNodePlugin(
            plugin_id="react_tailwind_tsx.self_closing",
            priority=30,
            matches=_matches_tailwind_self_closing,
            render=_render_tailwind_self_closing,
        ),
        EmitterNodePlugin(
            plugin_id="react_tailwind_tsx.leaf",
            priority=20,
            matches=_matches_tailwind_leaf,
            render=_render_tailwind_leaf,
        ),
        EmitterNodePlugin(
            plugin_id="react_tailwind_tsx.container",
            priority=0,
            matches=_matches_tailwind_container,
            render=_render_tailwind_container,
        ),
    )
)


def _render_node(
    node: IRNode,
    *,
    manifest: dict[str, Any],
    recipes: dict[str, ResolvedRecipe],
    root: IRNode,
    parent: IRNode | None = None,
    indent: int = 4,
    registry: EmitterNodeRegistry | None = None,
) -> list[str]:
    dom_id = f"dom-{node.id}"
    node_registry = registry or TAILWIND_NODE_REGISTRY
    rendered = node_registry.render(
        node,
        EmitterNodeContext(
            target="react-tailwind-tsx",
            root=root,
            parent=parent,
            state={"recipes": recipes},
        ),
    )
    if rendered.manifest_entry is not None:
        manifest[dom_id] = dict(rendered.manifest_entry)
    pad = " " * indent
    child_pad = " " * (indent + 2)
    attrs = " ".join(rendered.attrs)
    if rendered.self_closing:
        return [f"{pad}<{rendered.tag} {attrs} />"]
    if rendered.text is not None:
        return [f"{pad}<{rendered.tag} {attrs}>{rendered.text}</{rendered.tag}>"]
    lines = [f"{pad}<{rendered.tag} {attrs}>"]
    if rendered.child_wrapper_tag is not None:
        lines.append(f"{child_pad}<{rendered.child_wrapper_tag}>")
        for child in node.children:
            lines.extend(
                _render_node(
                    child,
                    manifest=manifest,
                    recipes=recipes,
                    root=root,
                    parent=node,
                    indent=indent + 4,
                    registry=node_registry,
                )
            )
        lines.append(f"{child_pad}</{rendered.child_wrapper_tag}>")
    else:
        for child in node.children:
            lines.extend(
                _render_node(
                    child,
                    manifest=manifest,
                    recipes=recipes,
                    root=root,
                    parent=node,
                    indent=indent + 2,
                    registry=node_registry,
                )
            )
    lines.append(f"{pad}</{rendered.tag}>")
    return lines



def _emit_source(result: CompilerResult, title: str) -> tuple[str, dict[str, Any], dict[str, ResolvedRecipe]]:
    root = result.root.root
    recipes = _resolve_recipes(root)
    manifest: dict[str, Any] = {}
    rendered = _render_node(root, manifest=manifest, recipes=recipes, root=root)
    input_values = _safe_json_literal(_initial_input_values(root))
    compiled_values = _safe_json_literal(_compiled_payload_values(root))
    initial_visibility = {
        str(node.props["visibility_rule_id"]): not bool(node.props.get("visibility_hidden_initial"))
        for node in _walk(root)
        if isinstance(node.props.get("visibility_rule_id"), str) and node.props.get("visibility_rule_id")
    }
    visibility_props = ["  visibility?: Record<string, boolean>;"] if initial_visibility else []
    visibility_default = f", visibility = {_safe_json_literal(initial_visibility)}" if initial_visibility else ""
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
        *visibility_props,
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
        f"export function ViewSpecView({{ data = {{}}, onAction{visibility_default} }}: ViewSpecViewProps) {{",
        f"  const [inputValues, setInputValues] = React.useState<Record<string, unknown>>({input_values});",
        f"  const compiledPayloadValues: Record<string, unknown> = {compiled_values};",
        "  const setInputValue = (id: string, value: unknown) => {",
        "    setInputValues((current) => ({ ...current, [id]: value }));",
        "  };",
        "  const utf8Bytes = (value: unknown): number => new TextEncoder().encode(String(value ?? \"\")).length;",
        "  const assertPayloadBounds = (kind: string, payloadBindings: string[], payloadValues: Record<string, unknown>) => {",
        "    if (kind === \"bulk_action\") {",
        "      const selectionBindings = payloadBindings.filter((id) => id.endsWith(\"_selection\") || id.endsWith(\"_selected_ids\"));",
        "      if (selectionBindings.length !== 1 || payloadBindings.length !== 1) throw new Error(\"COLLECTION_BULK_SELECTION_AMBIGUOUS\");",
        "      const value = payloadValues[selectionBindings[0]];",
        "      if (Array.isArray(value) && value.length > 100) throw new Error(\"COLLECTION_BULK_SELECTION_TOO_LARGE\");",
        "      if (utf8Bytes(value) > 4096) throw new Error(\"COLLECTION_BULK_SELECTION_TOO_LARGE\");",
        "      return;",
        "    }",
        "    if ([\"search\", \"filter\", \"sort\", \"paginate\"].includes(kind)) {",
        "      payloadBindings.forEach((bindingId) => {",
        "        if (utf8Bytes(payloadValues[bindingId]) > 512) throw new Error(\"COLLECTION_ACTION_PAYLOAD_TOO_LARGE\");",
        "      });",
        "    }",
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
    "TAILWIND_AESTHETIC_RECIPE_OVERLAYS",
    "TAILWIND_APP_V1_APP_ROLE_CONTRACTS",
    "TAILWIND_APP_ROLE_RULE_PRECEDENCE",
    "TAILWIND_PRODUCT_APP_ROLE_RULE_IDS",
    "TAILWIND_MAX_ACTIONS",
    "TAILWIND_MAX_ARTIFACT_BYTES",
    "TAILWIND_MAX_CLASS_TOKENS",
    "TAILWIND_MAX_IR_NODES",
    "TAILWIND_MAX_RECIPES",
    "TAILWIND_RECIPE_REGISTRY_VERSION",
    "TAILWIND_RECIPE_PACK",
    "TAILWIND_STRUCTURAL_APP_ROLE_RULE_IDS",
    "ReactTailwindTsxEmitter",
    "emit_compiler_result",
    "resolve_manifest_recipe_metadata",
    "tailwind_recipe_registry_digest",
    "tailwind_recipe_registry_projection",
]
