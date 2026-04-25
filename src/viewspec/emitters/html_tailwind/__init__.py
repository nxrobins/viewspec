"""
Deterministic HTML + Tailwind emitter for ViewSpec CompositionIR.

This module is intentionally pure Python. It turns a CompilerResult into
standalone HTML, a provenance manifest, and a diagnostics JSON file.
"""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any

from viewspec.types import (
    ASTBundle,
    CompilerResult,
    DEFAULT_STYLE_TOKEN_VALUES,
    IRNode,
)
from viewspec.emitters.base import EmitterPlugin


TAILWIND_BY_PRIMITIVE = {
    "root": "min-h-screen bg-slate-50 text-slate-950 p-6 space-y-6",
    "stack": "flex flex-col gap-3",
    "grid": "grid gap-4",
    "cluster": "flex flex-row flex-wrap gap-3",
    "surface": "rounded-2xl border border-slate-200 bg-white p-4 shadow-sm space-y-3",
    "text": "text-base leading-7 text-slate-800",
    "label": "text-xs font-bold uppercase tracking-widest text-slate-500",
    "value": "text-2xl font-black tracking-tight text-slate-950",
    "badge": "inline-flex w-fit rounded-full bg-teal-50 px-3 py-1 text-sm font-semibold text-teal-800 ring-1 ring-teal-200",
    "image_slot": "min-h-24 rounded-xl bg-slate-200 text-slate-500 grid place-items-center",
    "rule": "my-2 border-slate-200",
    "svg": "rounded-xl border border-slate-200 bg-slate-50 p-3 text-slate-600",
    "button": "inline-flex w-fit items-center rounded-xl bg-teal-700 px-4 py-2 text-sm font-bold text-white shadow-sm hover:bg-teal-800",
    "error_boundary": "rounded-xl border-2 border-dashed border-red-500 bg-red-50 p-4 font-mono text-sm text-red-800",
}


ACTION_EVENT_SCRIPT = """
<script>
document.addEventListener('click', (e) => {
  const btn = e.target.closest('[data-action-id]');
  if (!btn) return;
  const detail = {
    id: btn.dataset.actionId,
    kind: btn.dataset.actionKind,
    payloadBindings: JSON.parse(btn.dataset.payloadBindings || '[]')
  };
  document.dispatchEvent(new CustomEvent('viewspec-action', { detail }));
  console.log('Action Dispatched:', detail);
});
</script>
""".strip()


def _json_attr(value: Any) -> str:
    return escape(json.dumps(value, sort_keys=True), quote=True)


def _style_attr(node: IRNode, style_values: dict[str, str]) -> str:
    css = " ".join(style_values.get(token, "") for token in node.style_tokens if style_values.get(token))
    return f' style="{escape(css, quote=True)}"' if css else ""


def _manifest_entry(node: IRNode) -> dict[str, Any]:
    return {
        "ir_id": node.id,
        "primitive": node.primitive,
        "content_refs": list(node.provenance.content_refs),
        "intent_refs": list(node.provenance.intent_refs),
        "style_tokens": list(node.style_tokens),
        "props": dict(node.props),
    }


def _render_node(node: IRNode, manifest: dict[str, Any], style_values: dict[str, str]) -> str:
    dom_id = f"dom-{node.id}"
    manifest[dom_id] = _manifest_entry(node)
    classes = TAILWIND_BY_PRIMITIVE.get(node.primitive, "rounded border border-slate-200 p-2")
    attrs = [
        f'id="{escape(dom_id, quote=True)}"',
        f'class="{escape(classes, quote=True)}"',
        f'data-ir-id="{escape(node.id, quote=True)}"',
        f'data-content-refs="{_json_attr(list(node.provenance.content_refs))}"',
        f'data-intent-refs="{_json_attr(list(node.provenance.intent_refs))}"',
    ]
    if node.primitive == "grid":
        columns = int(node.props.get("columns") or 1)
        attrs.append(f'style="grid-template-columns: repeat({columns}, minmax(0, 1fr));"')
    else:
        style_attr = _style_attr(node, style_values)
        if style_attr:
            attrs.append(style_attr.strip())
    if node.primitive == "button":
        attrs.extend(
            [
                f'data-action-id="{escape(str(node.props.get("action_id", "")), quote=True)}"',
                f'data-action-kind="{escape(str(node.props.get("action_kind", "")), quote=True)}"',
                f'data-payload-bindings="{_json_attr(node.props.get("payload_bindings", []))}"',
            ]
        )

    if node.primitive == "root":
        tag = "main"
    elif node.primitive == "rule":
        tag = "hr"
    elif node.primitive == "button":
        tag = "button"
    else:
        tag = "div"

    if node.primitive == "rule":
        return f"<{tag} {' '.join(attrs)} />"
    if node.primitive == "image_slot":
        inner_html = escape(str(node.props.get("alt", "image slot")))
    elif node.primitive == "svg":
        inner_html = escape(str(node.props.get("label", "vector slot")))
    elif node.primitive == "button":
        inner_html = escape(str(node.props.get("text", node.props.get("label", "Action"))))
    elif node.primitive == "error_boundary":
        code = escape(str(node.props.get("diagnostic_code", "COMPILER_ERROR")))
        message = escape(str(node.props.get("message", "Compiler diagnostic")))
        inner_html = f"<div class=\"font-bold\">{code}</div><div class=\"mt-1\">{message}</div>"
    else:
        pieces: list[str] = []
        if "text" in node.props:
            pieces.append(escape(str(node.props["text"])))
        for child in node.children:
            pieces.append(_render_node(child, manifest, style_values))
        inner_html = "".join(pieces)
    return f"<{tag} {' '.join(attrs)}>{inner_html}</{tag}>"


def emit_compiler_result(
    result: CompilerResult,
    style_values: dict[str, str],
    *,
    output_dir: str | Path = "viewspec_output",
    title: str = "ViewSpec Artifact",
) -> dict[str, str]:
    """Emit a CompilerResult as HTML + Tailwind with provenance manifest."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {}
    body = _render_node(result.root.root, manifest, style_values)
    html = "\n".join(
        [
            "<!DOCTYPE html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{escape(title)}</title>",
            '<script src="https://cdn.tailwindcss.com"></script>',
            "</head>",
            "<body>",
            body,
            ACTION_EVENT_SCRIPT,
            "</body>",
            "</html>",
        ]
    )
    html_path = output_path / "index.html"
    manifest_path = output_path / "provenance_manifest.json"
    diagnostics_path = output_path / "diagnostics.json"
    html_path.write_text(html, encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    diagnostics_path.write_text(
        json.dumps([d.to_json() for d in result.diagnostics], indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return {
        "html": str(html_path),
        "manifest": str(manifest_path),
        "diagnostics": str(diagnostics_path),
    }


class HtmlTailwindEmitter(EmitterPlugin):
    """Deterministic HTML + Tailwind emitter plugin."""

    def emit(self, ast_bundle: ASTBundle, output_dir: str | Path) -> dict[str, str]:
        style_values = dict(ast_bundle.style_values or DEFAULT_STYLE_TOKEN_VALUES)
        return emit_compiler_result(ast_bundle.result, style_values, output_dir=output_dir, title=ast_bundle.title)


PLUGIN_CLASS = HtmlTailwindEmitter
