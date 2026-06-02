"""
Deterministic HTML + Tailwind emitter for ViewSpec CompositionIR.

This module is intentionally pure Python. It turns a CompilerResult into
standalone HTML, a provenance manifest, and a diagnostics JSON file.
"""

from __future__ import annotations

import json
import re
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
from viewspec.compiler import PRODUCT_SURFACE_PLANNER_V1_ROLES
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
    "input": "vs-input",
    "image_slot": "vs-image-slot",
    "rule": "vs-rule",
    "svg": "vs-svg",
    "button": "vs-button",
    "error_boundary": "vs-error-boundary",
}

SAFE_IR_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
ACTION_TARGET_REF_RE = re.compile(r"^(region|binding|motif|view):[A-Za-z0-9_.-]+$")
UNSAFE_STYLE_VALUE_RE = re.compile(r"(?i)(@import|url\s*\(|expression\s*\(|javascript:|vbscript:|data:)")
SUPPORTED_PRIMITIVES = frozenset(TAILWIND_BY_PRIMITIVE)
LAYOUT_ROLE_CLASS_BY_ROLE = {
    "cluster": "vs-layout-cluster",
    "grid": "vs-layout-grid",
    "root": "vs-layout-root",
    "stack": "vs-layout-stack",
    "surface": "vs-layout-surface",
}
MOTIF_KIND_CLASS_BY_KIND = {
    "comparison": "vs-motif-comparison",
    "dashboard": "vs-motif-dashboard",
    "detail": "vs-motif-detail",
    "empty_state": "vs-motif-empty-state",
    "form": "vs-motif-form",
    "hero": "vs-motif-hero",
    "list": "vs-motif-list",
    "outline": "vs-motif-outline",
    "table": "vs-motif-table",
}
PRODUCT_ROLE_CLASS_BY_ROLE = {
    role: f"vs-role-{role.replace('_', '-')}" for role in sorted(PRODUCT_SURFACE_PLANNER_V1_ROLES)
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
ul.vs-stack { list-style: none; margin: 0; padding: 0; }
table.vs-stack { display: table; width: 100%; border-collapse: collapse; border: 1px solid #e2e8f0; border-radius: 12px; overflow: hidden; background: #ffffff; }
tr.vs-cluster { display: table-row; }
th.vs-label, td.vs-text, td.vs-value, td.vs-badge, td.vs-label { display: table-cell; padding: 0.7rem 0.85rem; border-bottom: 1px solid #e2e8f0; vertical-align: top; }
th.vs-label { width: 38%; text-align: left; background: #f8fafc; }
table.vs-stack tr:last-child th, table.vs-stack tr:last-child td { border-bottom: 0; }
dl.vs-stack { margin: 0; }
dl.vs-stack > div.vs-cluster { display: grid; grid-template-columns: minmax(8rem, 30%) 1fr; align-items: start; padding: 0.65rem 0; border-bottom: 1px solid #e2e8f0; }
dl.vs-stack > div.vs-cluster:last-child { border-bottom: 0; }
dl.vs-stack dt, dl.vs-stack dd { margin: 0; }
header.vs-surface { padding: 32px; gap: 14px; }
header.vs-surface h1.vs-value { margin: 0; font-size: 2.5rem; line-height: 1.05; letter-spacing: 0; }
header.vs-surface p.vs-text, header.vs-surface p.vs-label { max-width: 68ch; margin: 0; }
.vs-grid { display: grid; gap: 16px; }
.vs-cluster { display: flex; flex-flow: row wrap; gap: 12px; }
.vs-surface { border: 1px solid #e2e8f0; border-radius: 16px; background: #ffffff; padding: 16px; box-shadow: 0 1px 2px rgb(15 23 42 / 0.08); display: flex; flex-direction: column; gap: 12px; }
.vs-text { color: #1f2937; font-size: 1rem; line-height: 1.75; }
.vs-label { color: #64748b; font-size: 0.75rem; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; }
.vs-value { color: #020617; font-size: 1.5rem; font-weight: 900; line-height: 1.15; }
.vs-badge { display: inline-flex; width: fit-content; border-radius: 999px; background: #ccfbf1; color: #115e59; padding: 4px 12px; font-size: 0.875rem; font-weight: 700; box-shadow: inset 0 0 0 1px #99f6e4; }
.vs-input { width: 100%; min-width: 0; border: 1px solid #cbd5e1; border-radius: 10px; background: #ffffff; color: #020617; padding: 0.7rem 0.85rem; font: inherit; }
.vs-input:focus { outline: 2px solid #0f766e; outline-offset: 2px; }
.vs-image-slot { min-height: 96px; border-radius: 12px; background: #e2e8f0; color: #64748b; display: grid; place-items: center; }
.vs-rule { margin: 8px 0; border: 0; border-top: 1px solid #e2e8f0; }
.vs-svg { border: 1px solid #e2e8f0; border-radius: 12px; background: #f8fafc; color: #475569; padding: 12px; }
.vs-button { display: inline-flex; width: fit-content; align-items: center; border: 0; border-radius: 12px; background: #0f766e; color: #ffffff; padding: 8px 16px; font-size: 0.875rem; font-weight: 800; cursor: pointer; box-shadow: 0 1px 2px rgb(15 23 42 / 0.16); }
.vs-button:hover { background: #115e59; }
.vs-error-boundary { border: 2px dashed #ef4444; border-radius: 12px; background: #fef2f2; color: #991b1b; padding: 16px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.875rem; }
.vs-role-app-shell { width: min(100%, 1180px); margin: 0 auto; padding: 28px; gap: 18px; }
.vs-role-app-header { padding: 20px 0 6px; border-bottom: 1px solid #dbe3ea; }
.vs-role-page-header { border: 0; border-radius: 0; box-shadow: none; background: transparent; padding: 0 0 14px; gap: 8px; }
.vs-role-content-grid { align-items: start; gap: 18px; }
.vs-role-primary-column { gap: 18px; }
.vs-role-side-rail { gap: 14px; align-self: start; }
.vs-role-metric-grid { gap: 12px; }
.vs-role-metric-card { min-height: 108px; justify-content: space-between; border-radius: 8px; }
.vs-role-form-panel { border-radius: 8px; padding: 18px; gap: 14px; }
.vs-role-field-group { border-radius: 8px; box-shadow: none; padding: 12px; }
.vs-role-detail-panel { border-radius: 8px; padding: 16px; }
.vs-role-action-row { align-items: center; justify-content: flex-end; gap: 10px; padding: 4px 0 0; }
@media (max-width: 760px) {
  .vs-root { padding: 16px; }
  .vs-role-app-shell { padding: 16px; }
  .vs-role-content-grid { grid-template-columns: 1fr !important; }
  .vs-role-action-row { justify-content: stretch; }
  .vs-role-action-row .vs-button { width: 100%; justify-content: center; }
}
""".strip()


ACTION_EVENT_SCRIPT = """
<script>
function viewspecPayloadBindings(btn) {
  let payloadBindings = [];
  try {
    const parsedPayloadBindings = JSON.parse(btn.dataset.payloadBindings || '[]');
    if (Array.isArray(parsedPayloadBindings)) {
      payloadBindings = parsedPayloadBindings.filter((id) => typeof id === 'string');
    }
  } catch {
    payloadBindings = [];
  }
  return payloadBindings;
}

function dispatchViewSpecAction(btn) {
  const payloadBindings = viewspecPayloadBindings(btn);
  const detail = {
    schemaVersion: 1,
    source: 'viewspec-html-tailwind',
    id: btn.dataset.actionId,
    kind: btn.dataset.actionKind,
    targetRef: btn.dataset.actionTargetRef || '',
    payloadBindings,
    payloadValues: {}
  };
  const requested = new Set(detail.payloadBindings);
  const root = btn.closest('.vs-root') || document;
  root.querySelectorAll('[data-binding-id]').forEach((el) => {
    const id = el.dataset.bindingId;
    if (!requested.has(id)) return;
    detail.payloadValues[id] = 'value' in el ? el.value : el.textContent || '';
  });
  document.dispatchEvent(new CustomEvent('viewspec-action', { detail }));
}

document.addEventListener('click', (e) => {
  const target = e.target instanceof Element ? e.target : null;
  const btn = target ? target.closest('[data-action-id]') : null;
  if (!btn) return;
  dispatchViewSpecAction(btn);
});

document.addEventListener('keydown', (e) => {
  if (e.key !== 'Enter' || e.defaultPrevented || e.shiftKey || e.altKey || e.ctrlKey || e.metaKey) return;
  const target = e.target instanceof Element ? e.target : null;
  if (!target || target.tagName !== 'INPUT') return;
  const form = target.closest('[role="form"][data-ir-id]');
  if (!form) return;
  const irId = form.dataset.irId || '';
  if (!irId.startsWith('motif_')) return;
  const targetRef = `motif:${irId.slice(6)}`;
  const root = form.closest('.vs-root') || document;
  const btn = Array.from(root.querySelectorAll('[data-action-id][data-action-kind="submit"]'))
    .find((candidate) => candidate.dataset.actionTargetRef === targetRef);
  if (!btn) return;
  e.preventDefault();
  dispatchViewSpecAction(btn);
});
</script>
""".strip()


def _json_attr(value: Any) -> str:
    return escape(json.dumps(value, sort_keys=True), quote=True)


def _style_css(node: IRNode, style_values: dict[str, str]) -> str:
    return " ".join(style_values.get(token, "") for token in node.style_tokens if style_values.get(token))


def _validate_style_values(style_values: dict[str, str]) -> None:
    for token, value in style_values.items():
        if not isinstance(token, str) or not SAFE_IR_ID_RE.match(token):
            raise ValueError(f"Style token '{token}' must use only letters, digits, underscore, dot, and dash.")
        if not isinstance(value, str):
            raise ValueError(f"Style token '{token}' value must be a CSS declaration string.")
        if "<" in value or ">" in value or UNSAFE_STYLE_VALUE_RE.search(value):
            raise ValueError(f"Style token '{token}' contains an active or auto-fetching CSS surface.")


def _style_attr(css: str) -> str:
    return f' style="{escape(css, quote=True)}"' if css else ""


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="",
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


def _validate_ir_contract(node: IRNode, seen_ids: set[str]) -> None:
    if not node.id or not SAFE_IR_ID_RE.match(node.id):
        raise ValueError(
            f"IRNode.id '{node.id}' must use only letters, digits, underscore, dot, and dash."
        )
    if node.id in seen_ids:
        raise ValueError(f"Duplicate IRNode.id '{node.id}' would produce duplicate DOM artifact identity.")
    seen_ids.add(node.id)
    if node.primitive not in SUPPORTED_PRIMITIVES:
        supported = ", ".join(sorted(SUPPORTED_PRIMITIVES))
        raise ValueError(f"Unsupported IR primitive '{node.primitive}'. Supported primitives: {supported}.")
    _node_classes(node)
    if node.primitive == "grid":
        try:
            columns = int(node.props.get("columns") or 1)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Grid IRNode '{node.id}' columns must be a positive integer.") from exc
        if columns < 1:
            raise ValueError(f"Grid IRNode '{node.id}' columns must be a positive integer.")
    if node.primitive == "button":
        _validate_action_props(node)
    for child in node.children:
        _validate_ir_contract(child, seen_ids)


def _validate_action_props(node: IRNode) -> None:
    action_id = node.props.get("action_id")
    if not isinstance(action_id, str) or not SAFE_IR_ID_RE.match(action_id):
        raise ValueError(f"Button IRNode '{node.id}' action_id must be a non-empty safe id.")
    action_kind = node.props.get("action_kind")
    if not isinstance(action_kind, str) or not SAFE_IR_ID_RE.match(action_kind):
        raise ValueError(f"Button IRNode '{node.id}' action_kind must be a non-empty safe token.")
    target_ref = node.props.get("target_ref")
    if target_ref not in (None, "") and (not isinstance(target_ref, str) or not ACTION_TARGET_REF_RE.match(target_ref)):
        raise ValueError(f"Button IRNode '{node.id}' target_ref must be region:id, binding:id, motif:id, or view:id.")
    payload_bindings = node.props.get("payload_bindings", [])
    if not isinstance(payload_bindings, list) or any(not isinstance(item, str) or not SAFE_IR_ID_RE.match(item) for item in payload_bindings):
        raise ValueError(f"Button IRNode '{node.id}' payload_bindings must be a list of safe ids.")


def _closed_prop_class(node: IRNode, prop_name: str, classes_by_value: dict[str, str]) -> str | None:
    value = node.props.get(prop_name)
    if value is None:
        return None
    if not isinstance(value, str) or value not in classes_by_value:
        allowed = ", ".join(sorted(classes_by_value))
        raise ValueError(
            f"UNSAFE_ROLE_CLASS: IRNode '{node.id}' prop '{prop_name}' must use one of: {allowed}."
        )
    return classes_by_value[value]


def _node_classes(node: IRNode) -> list[str]:
    classes = [TAILWIND_BY_PRIMITIVE[node.primitive]]
    for prop_name, classes_by_value in (
        ("layout_role", LAYOUT_ROLE_CLASS_BY_ROLE),
        ("motif_kind", MOTIF_KIND_CLASS_BY_KIND),
        ("product_role", PRODUCT_ROLE_CLASS_BY_ROLE),
    ):
        class_name = _closed_prop_class(node, prop_name, classes_by_value)
        if class_name is not None:
            classes.append(class_name)
    return classes


def _manifest_entry(node: IRNode) -> dict[str, Any]:
    return {
        "ir_id": node.id,
        "primitive": node.primitive,
        "classes": _node_classes(node),
        "content_refs": list(node.provenance.content_refs),
        "intent_refs": list(node.provenance.intent_refs),
        "style_tokens": list(node.style_tokens),
        "props": dict(node.props),
    }


def _render_node(node: IRNode, manifest: dict[str, Any], style_values: dict[str, str]) -> str:
    dom_id = f"dom-{node.id}"
    manifest[dom_id] = _manifest_entry(node)
    classes = " ".join(_node_classes(node))
    attrs = [
        f'id="{escape(dom_id, quote=True)}"',
        f'class="{escape(classes, quote=True)}"',
        f'data-ir-id="{escape(node.id, quote=True)}"',
        f'data-content-refs="{_json_attr(list(node.provenance.content_refs))}"',
        f'data-intent-refs="{_json_attr(list(node.provenance.intent_refs))}"',
        f'data-style-tokens="{_json_attr(list(node.style_tokens))}"',
    ]
    style_css = _style_css(node, style_values)
    if node.primitive == "grid":
        columns = int(node.props.get("columns") or 1)
        style_css = f"grid-template-columns: repeat({columns}, minmax(0, 1fr)); {style_css}".strip()
        attrs.append(_style_attr(style_css).strip())
    else:
        style_attr = _style_attr(style_css)
        if style_attr:
            attrs.append(style_attr.strip())
    if node.props.get("binding_id") is not None:
        attrs.append(f'data-binding-id="{escape(str(node.props["binding_id"]), quote=True)}"')
    if node.primitive == "button":
        attrs.extend(
            [
                'type="button"',
                f'data-action-id="{escape(str(node.props.get("action_id", "")), quote=True)}"',
                f'data-action-kind="{escape(str(node.props.get("action_kind", "")), quote=True)}"',
                f'data-action-target-ref="{escape(str(node.props.get("target_ref", "")), quote=True)}"',
                f'data-payload-bindings="{_json_attr(node.props.get("payload_bindings", []))}"',
            ]
        )
    elif node.primitive == "input":
        attrs.extend(
            [
                'type="text"',
                f'value="{escape(str(node.props.get("value", "")), quote=True)}"',
                f'aria-label="{escape(str(node.props.get("aria_label", node.props.get("binding_id", "input"))), quote=True)}"',
            ]
        )
    elif node.primitive in {"image_slot", "svg"}:
        label = str(node.props.get("alt", node.props.get("label", node.primitive.replace("_", " "))))
        attrs.extend(['role="img"', f'aria-label="{escape(label, quote=True)}"'])
    elif node.primitive == "error_boundary":
        attrs.append('role="alert"')
    elif node.props.get("motif_kind") == "form" and node.primitive == "stack":
        attrs.extend(['role="form"', f'aria-label="{escape(str(node.props.get("label", node.id)), quote=True)}"'])
    elif node.props.get("motif_kind") == "form" and node.primitive == "surface":
        attrs.append('role="group"')
    elif node.props.get("motif_kind") == "empty_state" and node.primitive == "surface":
        attrs.append(f'aria-label="{escape(str(node.props.get("aria_label", "Empty state")), quote=True)}"')
    elif node.props.get("motif_kind") == "hero" and node.primitive == "surface":
        attrs.append(f'aria-label="{escape(str(node.props.get("aria_label", "Hero")), quote=True)}"')
    elif node.props.get("table_cell_role") == "row_header":
        attrs.append('scope="row"')

    if node.primitive == "root":
        tag = "main"
    elif node.props.get("motif_kind") == "table" and node.primitive == "stack":
        tag = "table"
    elif node.props.get("motif_kind") == "table" and node.primitive == "cluster":
        tag = "tr"
    elif node.props.get("motif_kind") == "detail" and node.primitive == "stack":
        tag = "dl"
    elif node.props.get("motif_kind") == "detail" and node.primitive == "cluster":
        tag = "div"
    elif node.props.get("detail_role") == "term":
        tag = "dt"
    elif node.props.get("detail_role") == "description":
        tag = "dd"
    elif node.props.get("motif_kind") == "empty_state" and node.primitive == "surface":
        tag = "section"
    elif node.props.get("empty_state_role") == "title":
        tag = "h2"
    elif node.props.get("empty_state_role") == "description":
        tag = "p"
    elif node.props.get("motif_kind") == "hero" and node.primitive == "surface":
        tag = "header"
    elif node.props.get("hero_role") == "title":
        tag = "h1"
    elif node.props.get("hero_role") in {"description", "eyebrow"}:
        tag = "p"
    elif node.props.get("table_cell_role") == "row_header":
        tag = "th"
    elif node.props.get("table_cell_role") == "cell":
        tag = "td"
    elif node.props.get("motif_kind") == "list" and node.primitive == "stack":
        tag = "ul"
    elif node.props.get("motif_kind") == "list" and node.primitive == "surface":
        tag = "li"
    elif node.props.get("motif_kind") == "form" and node.primitive == "stack":
        tag = "section"
    elif node.primitive == "rule":
        tag = "hr"
    elif node.primitive == "button":
        tag = "button"
    elif node.primitive == "input":
        tag = "input"
    else:
        tag = "div"

    if node.primitive == "rule":
        return f"<{tag} {' '.join(attrs)} />"
    if node.primitive == "input":
        return f"<{tag} {' '.join(attrs)}>"
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
        if tag == "table":
            inner_html = f"<tbody>{inner_html}</tbody>"
    return f"<{tag} {' '.join(attrs)}>{inner_html}</{tag}>"


def _has_action_node(node: IRNode) -> bool:
    if node.primitive == "button" and node.props.get("action_id"):
        return True
    return any(_has_action_node(child) for child in node.children)


def emit_compiler_result(
    result: CompilerResult,
    style_values: dict[str, str],
    *,
    output_dir: str | Path = "viewspec_output",
    title: str = "ViewSpec Artifact",
) -> dict[str, str]:
    """Emit a CompilerResult as HTML + Tailwind with provenance manifest."""
    output_path = Path(output_dir)
    _validate_ir_contract(result.root.root, set())
    _validate_style_values(style_values)
    output_path.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {}
    body = _render_node(result.root.root, manifest, style_values)
    body_tail = [body]
    if _has_action_node(result.root.root):
        body_tail.append(ACTION_EVENT_SCRIPT)
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
            *body_tail,
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
