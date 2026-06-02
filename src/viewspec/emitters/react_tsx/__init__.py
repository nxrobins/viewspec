"""Deterministic React TSX emitter for ViewSpec CompositionIR."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from viewspec.emitters.base import EmitterPlugin
from viewspec.emitters.html_tailwind import (
    SUPPORTED_PRIMITIVES,
    _manifest_entry,
    _node_classes,
    _style_css,
    _validate_style_values,
    _validate_ir_contract,
    _write_text_atomic,
)
from viewspec.types import ASTBundle, CompilerResult, DEFAULT_STYLE_TOKEN_VALUES, IRNode


BASE_STYLE_BY_PRIMITIVE: dict[str, dict[str, str]] = {
    "root": {
        "minHeight": "100vh",
        "padding": "24px",
        "display": "flex",
        "flexDirection": "column",
        "gap": "24px",
        "background": "#f8fafc",
        "color": "#020617",
    },
    "stack": {"display": "flex", "flexDirection": "column", "gap": "12px"},
    "grid": {"display": "grid", "gap": "16px"},
    "cluster": {"display": "flex", "flexFlow": "row wrap", "gap": "12px"},
    "surface": {
        "border": "1px solid #e2e8f0",
        "borderRadius": "16px",
        "background": "#ffffff",
        "padding": "16px",
        "boxShadow": "0 1px 2px rgb(15 23 42 / 0.08)",
        "display": "flex",
        "flexDirection": "column",
        "gap": "12px",
    },
    "text": {"color": "#1f2937", "fontSize": "1rem", "lineHeight": "1.75"},
    "label": {
        "color": "#64748b",
        "fontSize": "0.75rem",
        "fontWeight": "700",
        "letterSpacing": "0.08em",
        "textTransform": "uppercase",
    },
    "value": {"color": "#020617", "fontSize": "1.5rem", "fontWeight": "900", "lineHeight": "1.15"},
    "badge": {
        "display": "inline-flex",
        "width": "fit-content",
        "borderRadius": "999px",
        "background": "#ccfbf1",
        "color": "#115e59",
        "padding": "4px 12px",
        "fontSize": "0.875rem",
        "fontWeight": "700",
        "boxShadow": "inset 0 0 0 1px #99f6e4",
    },
    "input": {
        "width": "100%",
        "minWidth": "0",
        "border": "1px solid #cbd5e1",
        "borderRadius": "10px",
        "background": "#ffffff",
        "color": "#020617",
        "padding": "0.7rem 0.85rem",
        "font": "inherit",
    },
    "image_slot": {
        "minHeight": "96px",
        "borderRadius": "12px",
        "background": "#e2e8f0",
        "color": "#64748b",
        "display": "grid",
        "placeItems": "center",
    },
    "rule": {"margin": "8px 0", "border": "0", "borderTop": "1px solid #e2e8f0"},
    "svg": {
        "border": "1px solid #e2e8f0",
        "borderRadius": "12px",
        "background": "#f8fafc",
        "color": "#475569",
        "padding": "12px",
    },
    "button": {
        "display": "inline-flex",
        "width": "fit-content",
        "alignItems": "center",
        "border": "0",
        "borderRadius": "12px",
        "background": "#0f766e",
        "color": "#ffffff",
        "padding": "8px 16px",
        "fontSize": "0.875rem",
        "fontWeight": "800",
        "cursor": "pointer",
        "boxShadow": "0 1px 2px rgb(15 23 42 / 0.16)",
    },
    "error_boundary": {
        "border": "2px dashed #ef4444",
        "borderRadius": "12px",
        "background": "#fef2f2",
        "color": "#991b1b",
        "padding": "16px",
        "fontFamily": "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
        "fontSize": "0.875rem",
    },
}

BASE_STYLE_BY_PRODUCT_ROLE: dict[str, dict[str, str]] = {
    "app_shell": {"width": "min(100%, 1180px)", "margin": "0 auto", "padding": "28px", "gap": "18px"},
    "app_header": {"padding": "20px 0 6px", "borderBottom": "1px solid #dbe3ea"},
    "page_header": {
        "border": "0",
        "borderRadius": "0",
        "boxShadow": "none",
        "background": "transparent",
        "padding": "0 0 14px",
        "gap": "8px",
    },
    "content_grid": {"alignItems": "start", "gap": "18px"},
    "primary_column": {"gap": "18px"},
    "side_rail": {"gap": "14px", "alignSelf": "start"},
    "metric_grid": {"gap": "12px"},
    "metric_card": {"minHeight": "108px", "justifyContent": "space-between", "borderRadius": "8px"},
    "form_panel": {"borderRadius": "8px", "padding": "18px", "gap": "14px"},
    "field_group": {"borderRadius": "8px", "boxShadow": "none", "padding": "12px"},
    "detail_panel": {"borderRadius": "8px", "padding": "16px"},
    "action_row": {"alignItems": "center", "justifyContent": "flex-end", "gap": "10px", "padding": "4px 0 0"},
}

JS_IDENTIFIER_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


def _tsx_string(value: object) -> str:
    text = json.dumps(str(value), ensure_ascii=False)
    return (
        text.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _safe_json_literal(value: object) -> str:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _json_string_attr(value: object) -> str:
    return json.dumps(value, sort_keys=True)


def _jsx_attr(name: str, value: object) -> str:
    return f"{name}={{{_tsx_string(value)}}}"


def react_tsx_manifest_node_markers(dom_id: str, entry: dict[str, Any]) -> dict[str, str]:
    """Return deterministic source markers that tie a TSX node to manifest metadata."""
    markers: dict[str, str] = {"id": f"id={{{_tsx_string(dom_id)}}}"}
    ir_id = entry.get("ir_id")
    if isinstance(ir_id, str):
        markers["data-ir-id"] = f"data-ir-id={{{_tsx_string(ir_id)}}}"
    content_refs = entry.get("content_refs")
    if isinstance(content_refs, list):
        markers["data-content-refs"] = f"data-content-refs={{{_tsx_string(_json_string_attr(content_refs))}}}"
    intent_refs = entry.get("intent_refs")
    if isinstance(intent_refs, list):
        markers["data-intent-refs"] = f"data-intent-refs={{{_tsx_string(_json_string_attr(intent_refs))}}}"
    style_tokens = entry.get("style_tokens")
    if isinstance(style_tokens, list):
        markers["data-style-tokens"] = f"data-style-tokens={{{_tsx_string(_json_string_attr(style_tokens))}}}"
    props = entry.get("props")
    if isinstance(props, dict):
        binding_id = props.get("binding_id")
        if isinstance(binding_id, str):
            markers["data-binding-id"] = f"data-binding-id={{{_tsx_string(binding_id)}}}"
        action_id = props.get("action_id")
        if isinstance(action_id, str):
            markers["data-action-id"] = f"data-action-id={{{_tsx_string(action_id)}}}"
        action_kind = props.get("action_kind")
        if isinstance(action_kind, str):
            markers["data-action-kind"] = f"data-action-kind={{{_tsx_string(action_kind)}}}"
        target_ref = props.get("target_ref")
        if isinstance(target_ref, str):
            markers["data-action-target-ref"] = f"data-action-target-ref={{{_tsx_string(target_ref)}}}"
        payload_bindings = props.get("payload_bindings")
        if isinstance(payload_bindings, list):
            markers["data-payload-bindings"] = f"data-payload-bindings={{{_tsx_string(_json_string_attr(payload_bindings))}}}"
    return markers


def _css_prop_to_react(prop: str) -> str:
    if prop.startswith("--"):
        return prop
    parts = prop.split("-")
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def _css_to_style(css: str) -> dict[str, str]:
    style: dict[str, str] = {}
    for declaration in css.split(";"):
        if ":" not in declaration:
            continue
        prop, value = declaration.split(":", 1)
        prop = prop.strip()
        value = value.strip()
        if not prop or not value:
            continue
        style[_css_prop_to_react(prop)] = value
    return style


def _style_object(node: IRNode, style_values: dict[str, str]) -> str:
    style = dict(BASE_STYLE_BY_PRIMITIVE.get(node.primitive, {}))
    product_role = node.props.get("product_role")
    if isinstance(product_role, str):
        style.update(BASE_STYLE_BY_PRODUCT_ROLE.get(product_role, {}))
    if node.primitive == "grid":
        style["gridTemplateColumns"] = f"repeat({int(node.props.get('columns') or 1)}, minmax(0, 1fr))"
    style.update(_css_to_style(_style_css(node, style_values)))
    if not style:
        return ""
    pairs: list[str] = []
    for key in sorted(style):
        key_literal = key if JS_IDENTIFIER_RE.match(key) else _tsx_string(key)
        pairs.append(f"{key_literal}: {_tsx_string(style[key])}")
    return "style={{ " + ", ".join(pairs) + " } as React.CSSProperties}"


def _tag_for_node(node: IRNode) -> str:
    if node.primitive == "root":
        return "main"
    if node.props.get("motif_kind") == "table" and node.primitive == "stack":
        return "table"
    if node.props.get("motif_kind") == "table" and node.primitive == "cluster":
        return "tr"
    if node.props.get("motif_kind") == "detail" and node.primitive == "stack":
        return "dl"
    if node.props.get("detail_role") == "term":
        return "dt"
    if node.props.get("detail_role") == "description":
        return "dd"
    if node.props.get("motif_kind") == "empty_state" and node.primitive == "surface":
        return "section"
    if node.props.get("empty_state_role") == "title":
        return "h2"
    if node.props.get("empty_state_role") == "description":
        return "p"
    if node.props.get("motif_kind") == "hero" and node.primitive == "surface":
        return "header"
    if node.props.get("hero_role") == "title":
        return "h1"
    if node.props.get("hero_role") in {"description", "eyebrow"}:
        return "p"
    if node.props.get("table_cell_role") == "row_header":
        return "th"
    if node.props.get("table_cell_role") == "cell":
        return "td"
    if node.props.get("motif_kind") == "list" and node.primitive == "stack":
        return "ul"
    if node.props.get("motif_kind") == "list" and node.primitive == "surface":
        return "li"
    if node.props.get("motif_kind") == "form" and node.primitive == "stack":
        return "section"
    if node.primitive == "rule":
        return "hr"
    if node.primitive == "button":
        return "button"
    if node.primitive == "input":
        return "input"
    return "div"


def _node_fallback_text(node: IRNode) -> str:
    if node.primitive == "image_slot":
        return str(node.props.get("alt", "image slot"))
    if node.primitive == "svg":
        return str(node.props.get("label", "vector slot"))
    if node.primitive == "button":
        return str(node.props.get("text", node.props.get("label", "Action")))
    if node.primitive == "error_boundary":
        return f"{node.props.get('diagnostic_code', 'COMPILER_ERROR')}: {node.props.get('message', 'Compiler diagnostic')}"
    return str(node.props.get("text", ""))


def _text_expression(node: IRNode) -> str:
    fallback = _tsx_string(_node_fallback_text(node))
    binding_id = node.props.get("binding_id")
    if isinstance(binding_id, str) and binding_id:
        return f"{{renderValue(data[{_tsx_string(binding_id)}], {fallback})}}"
    return f"{{{fallback}}}"


def _action_expression(node: IRNode) -> str:
    payload_bindings = [item for item in node.props.get("payload_bindings", []) if isinstance(item, str)]
    return (
        "{() => onAction?.({ "
        "schemaVersion: 1, "
        'source: "viewspec-react-tsx", '
        f"id: {_tsx_string(node.props.get('action_id', ''))}, "
        f"kind: {_tsx_string(node.props.get('action_kind', ''))}, "
        f"targetRef: {_tsx_string(node.props.get('target_ref', ''))}, "
        f"payloadBindings: {json.dumps(payload_bindings, ensure_ascii=False)}, "
        f"payloadValues: collectPayloadValues({json.dumps(payload_bindings, ensure_ascii=False)}) "
        "})}"
    )


def _attrs_for_node(node: IRNode, style_values: dict[str, str]) -> list[str]:
    dom_id = f"dom-{node.id}"
    classes = " ".join(_node_classes(node))
    attrs = [
        _jsx_attr("id", dom_id),
        _jsx_attr("data-ir-id", node.id),
        _jsx_attr("data-content-refs", _json_string_attr(list(node.provenance.content_refs))),
        _jsx_attr("data-intent-refs", _json_string_attr(list(node.provenance.intent_refs))),
        _jsx_attr("data-style-tokens", _json_string_attr(list(node.style_tokens))),
    ]
    if node.primitive == "root":
        attrs.append(f'className={{[{_tsx_string(classes)}, className].filter(Boolean).join(" ")}}')
    else:
        attrs.append(_jsx_attr("className", classes))
    style = _style_object(node, style_values)
    if style:
        attrs.append(style)
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
                f"onClick={_action_expression(node)}",
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


def _render_node(node: IRNode, manifest: dict[str, Any], style_values: dict[str, str], indent: int = 4) -> list[str]:
    dom_id = f"dom-{node.id}"
    manifest[dom_id] = _manifest_entry(node)
    pad = " " * indent
    child_pad = " " * (indent + 2)
    tag = _tag_for_node(node)
    attrs = " ".join(_attrs_for_node(node, style_values))
    if node.primitive in {"input", "rule"}:
        return [f"{pad}<{tag} {attrs} />"]
    if node.primitive in {"text", "label", "value", "badge", "button", "image_slot", "svg", "error_boundary"}:
        return [f"{pad}<{tag} {attrs}>{_text_expression(node)}</{tag}>"]
    lines = [f"{pad}<{tag} {attrs}>"]
    if tag == "table":
        lines.append(f"{child_pad}<tbody>")
        for child in node.children:
            lines.extend(_render_node(child, manifest, style_values, indent + 4))
        lines.append(f"{child_pad}</tbody>")
    else:
        for child in node.children:
            lines.extend(_render_node(child, manifest, style_values, indent + 2))
    lines.append(f"{pad}</{tag}>")
    return lines


def _walk(node: IRNode) -> list[IRNode]:
    nodes = [node]
    for child in node.children:
        nodes.extend(_walk(child))
    return nodes


def _initial_input_values(root: IRNode) -> dict[str, object]:
    values: dict[str, object] = {}
    for node in _walk(root):
        if node.primitive != "input":
            continue
        binding_id = node.props.get("binding_id")
        if isinstance(binding_id, str) and binding_id:
            values[binding_id] = node.props.get("value", "")
    return values


def _compiled_payload_values(root: IRNode) -> dict[str, object]:
    values: dict[str, object] = {}
    for node in _walk(root):
        binding_id = node.props.get("binding_id")
        if not isinstance(binding_id, str) or not binding_id or node.primitive == "input":
            continue
        value = _node_fallback_text(node)
        if value:
            values[binding_id] = value
    return values


def _emit_source(result: CompilerResult, style_values: dict[str, str], title: str) -> tuple[str, dict[str, Any]]:
    root = result.root.root
    manifest: dict[str, Any] = {}
    rendered = _render_node(root, manifest, style_values)
    input_values = _safe_json_literal(_initial_input_values(root))
    compiled_values = _safe_json_literal(_compiled_payload_values(root))
    lines = [
        '"use client";',
        "",
        "import * as React from \"react\";",
        "",
        "export type ViewSpecActionIntent = {",
        "  schemaVersion: 1;",
        "  source: \"viewspec-react-tsx\";",
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
        "  className?: string;",
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
        "export function ViewSpecView({ data = {}, onAction, className }: ViewSpecViewProps) {",
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
    return "\n".join(lines), manifest


def emit_compiler_result(
    result: CompilerResult,
    style_values: dict[str, str],
    *,
    output_dir: str | Path = "viewspec_react_output",
    title: str = "ViewSpec Artifact",
) -> dict[str, str]:
    """Emit a CompilerResult as a deterministic React TSX component."""
    output_path = Path(output_dir)
    _validate_ir_contract(result.root.root, set())
    _validate_style_values(style_values)
    unsupported = {node.primitive for node in _walk(result.root.root)} - SUPPORTED_PRIMITIVES
    if unsupported:
        raise ValueError(f"Unsupported IR primitive(s) for React TSX emitter: {', '.join(sorted(unsupported))}.")
    output_path.mkdir(parents=True, exist_ok=True)
    tsx, manifest = _emit_source(result, style_values, title)
    tsx_path = output_path / "ViewSpecView.tsx"
    manifest_path = output_path / "provenance_manifest.json"
    diagnostics_path = output_path / "diagnostics.json"
    try:
        _write_text_atomic(tsx_path, tsx)
        _write_text_atomic(manifest_path, json.dumps(manifest, indent=2, sort_keys=True))
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


class ReactTsxEmitter(EmitterPlugin):
    """Deterministic React TSX emitter plugin."""

    def emit(self, ast_bundle: ASTBundle, output_dir: str | Path) -> dict[str, str]:
        style_values = dict(ast_bundle.style_values or DEFAULT_STYLE_TOKEN_VALUES)
        return emit_compiler_result(ast_bundle.result, style_values, output_dir=output_dir, title=ast_bundle.title)


PLUGIN_CLASS = ReactTsxEmitter
