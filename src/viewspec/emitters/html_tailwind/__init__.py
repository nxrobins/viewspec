"""
Deterministic HTML + Tailwind emitter for ViewSpec CompositionIR.

This module is intentionally pure Python. It turns a CompilerResult into
standalone HTML, a provenance manifest, and a diagnostics JSON file.
"""

from __future__ import annotations

import json
import tempfile
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
    "root": "vs-root",
    "stack": "vs-stack",
    "grid": "vs-grid",
    "cluster": "vs-cluster",
    "surface": "vs-surface",
    "text": "vs-text",
    "label": "vs-label",
    "value": "vs-value",
    "badge": "vs-badge",
    "image_slot": "vs-image-slot",
    "rule": "vs-rule",
    "svg": "vs-svg",
    "button": "vs-button",
    "error_boundary": "vs-error-boundary",
}


OFFLINE_EMITTER_CSS = """
:root {
  color-scheme: light;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: #f8fafc;
  color: #020617;
}
* { box-sizing: border-box; }
body { margin: 0; min-height: 100vh; background: #f8fafc; }
.vs-root { min-height: 100vh; padding: 24px; display: flex; flex-direction: column; gap: 24px; color: #020617; }
.vs-stack { display: flex; flex-direction: column; gap: 12px; }
.vs-grid { display: grid; gap: 16px; }
.vs-cluster { display: flex; flex-flow: row wrap; gap: 12px; }
.vs-surface { border: 1px solid #e2e8f0; border-radius: 16px; background: #ffffff; padding: 16px; box-shadow: 0 1px 2px rgb(15 23 42 / 0.08); display: flex; flex-direction: column; gap: 12px; }
.vs-text { color: #1f2937; font-size: 1rem; line-height: 1.75; }
.vs-label { color: #64748b; font-size: 0.75rem; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; }
.vs-value { color: #020617; font-size: 1.5rem; font-weight: 900; line-height: 1.15; }
.vs-badge { display: inline-flex; width: fit-content; border-radius: 999px; background: #ccfbf1; color: #115e59; padding: 4px 12px; font-size: 0.875rem; font-weight: 700; box-shadow: inset 0 0 0 1px #99f6e4; }
.vs-image-slot { min-height: 96px; border-radius: 12px; background: #e2e8f0; color: #64748b; display: grid; place-items: center; }
.vs-rule { margin: 8px 0; border: 0; border-top: 1px solid #e2e8f0; }
.vs-svg { border: 1px solid #e2e8f0; border-radius: 12px; background: #f8fafc; color: #475569; padding: 12px; }
.vs-button { display: inline-flex; width: fit-content; align-items: center; border: 0; border-radius: 12px; background: #0f766e; color: #ffffff; padding: 8px 16px; font-size: 0.875rem; font-weight: 800; cursor: pointer; box-shadow: 0 1px 2px rgb(15 23 42 / 0.16); }
.vs-button:hover { background: #115e59; }
.vs-error-boundary { border: 2px dashed #ef4444; border-radius: 12px; background: #fef2f2; color: #991b1b; padding: 16px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.875rem; }
""".strip()


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


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(text)
        Path(temp_name).replace(path)
    except Exception:
        if temp_name:
            Path(temp_name).unlink(missing_ok=True)
        raise


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
            "<style>",
            OFFLINE_EMITTER_CSS,
            "</style>",
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
    try:
        _write_text_atomic(html_path, html)
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
