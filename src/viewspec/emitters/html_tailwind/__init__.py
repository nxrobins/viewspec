"""
Deterministic HTML + Tailwind emitter for ViewSpec CompositionIR.

This module is intentionally pure Python. It turns a CompilerResult into
standalone HTML, a provenance manifest, and a diagnostics JSON file.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from html import escape
from pathlib import Path
from typing import Any

from viewspec.aesthetics import profile_shape_var_css
from viewspec.compiler_refs import PRODUCT_SURFACE_PLANNER_V1_ROLES
from viewspec.emitters._write import write_artifact_set
from viewspec.emitters.base import EmitterPlugin
from viewspec.types import (
    DEFAULT_STYLE_TOKEN_VALUES,
    ASTBundle,
    CompilerResult,
    IRNode,
)

_LOGGER = logging.getLogger(__name__)

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
    "error_state": "vs-motif-error-state",
    "form": "vs-motif-form",
    "hero": "vs-motif-hero",
    "list": "vs-motif-list",
    "loading_state": "vs-motif-loading-state",
    "outline": "vs-motif-outline",
    "table": "vs-motif-table",
}
PRODUCT_ROLE_CLASS_BY_ROLE = {
    role: f"vs-role-{role.replace('_', '-')}" for role in sorted(PRODUCT_SURFACE_PLANNER_V1_ROLES)
}
SPAN_CLASS_BY_COLUMNS = {
    2: "vs-span-2",
    3: "vs-span-3",
}
LAYOUT_EMPHASIS_CLASS_BY_VALUE = {
    "featured": "vs-layout-emphasis-featured",
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
.vs-stack { display: flex; min-width: 0; flex-direction: column; gap: 12px; }
ul.vs-stack { list-style: none; margin: 0; padding: 0; }
table.vs-stack { display: table; width: 100%; border-collapse: collapse; border: 1px solid #e2e8f0; border-radius: var(--vs-control-radius, 12px); overflow: hidden; background: #ffffff; }
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
.vs-grid { display: grid; min-width: 0; gap: 16px; }
.vs-cluster { display: flex; min-width: 0; flex-flow: row wrap; gap: 12px; }
.vs-surface { border: var(--vs-surface-border, 1px solid #e2e8f0); border-radius: var(--vs-radius, 16px); background: #ffffff; padding: 16px; box-shadow: var(--vs-surface-shadow, 0 1px 2px rgb(15 23 42 / 0.08)); display: flex; min-width: 0; flex-direction: column; gap: 12px; }
.vs-text { min-width: 0; color: #1f2937; font-size: 1rem; line-height: 1.75; overflow-wrap: anywhere; }
.vs-label { min-width: 0; color: #64748b; font-size: 0.75rem; font-weight: 700; line-height: normal; letter-spacing: 0.08em; text-transform: uppercase; overflow-wrap: anywhere; }
.vs-value { min-width: 0; color: #020617; font-size: 1.5rem; font-weight: var(--vs-value-weight, 900); line-height: 1.15; overflow-wrap: anywhere; }
.vs-badge { display: inline-flex; width: fit-content; max-width: 100%; border-radius: var(--vs-badge-radius, 999px); background: #ccfbf1; color: #115e59; padding: 4px 12px; font-size: 0.875rem; font-weight: 700; line-height: 1.25; box-shadow: inset 0 0 0 1px #99f6e4; overflow-wrap: anywhere; }
.vs-input { width: 100%; min-width: 0; border: 1px solid #cbd5e1; border-radius: var(--vs-control-radius, 10px); background: #ffffff; color: #020617; padding: 0.7rem 0.85rem; font: inherit; }
.vs-input:focus { outline: 2px solid #0f766e; outline-offset: 2px; }
.vs-image-slot { min-height: 96px; border-radius: var(--vs-control-radius, 12px); background: #e2e8f0; color: #475569; display: grid; place-items: center; }
.vs-rule { margin: 8px 0; border: 0; border-top: 1px solid #e2e8f0; }
.vs-svg { border: 1px solid #e2e8f0; border-radius: var(--vs-control-radius, 12px); background: #f8fafc; color: #475569; padding: 12px; }
.vs-button { display: inline-flex; width: fit-content; align-items: center; border: 0; border-radius: var(--vs-action-radius, 12px); background: #0f766e; color: #ffffff; padding: 8px 16px; font-family: inherit; font-size: 0.875rem; font-weight: 800; line-height: 1.5; cursor: pointer; box-shadow: var(--vs-action-shadow, 0 1px 2px rgb(15 23 42 / 0.16)); }
.vs-button:hover { background: #115e59; }
.vs-error-boundary { border: 2px dashed #ef4444; border-radius: 12px; background: #fef2f2; color: #991b1b; padding: 16px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.875rem; }
.vs-role-app-shell { width: min(100%, 1180px); max-width: 1180px; margin: 0 auto; padding: 28px; gap: 18px; }
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
.vs-motif-loading-state { border-radius: 12px; border-style: dashed; color: #475569; }
.vs-motif-error-state { border-color: #fca5a5; background: #fef2f2; color: #991b1b; }
.vs-motif-loading-state .vs-value, .vs-motif-error-state .vs-value { font-size: 1.125rem; }
@media (min-width: 761px) {
  .vs-span-2 { grid-column: span 2 / span 2; }
  .vs-span-3 { grid-column: span 3 / span 3; }
}
.vs-layout-emphasis-featured { outline: 2px solid #14b8a6; outline-offset: 0; }
@media (max-width: 760px) {
  .vs-root { padding: 16px; }
  .vs-role-app-shell { padding: 16px; }
  .vs-role-content-grid { grid-template-columns: 1fr !important; }
  .vs-role-metric-grid { grid-template-columns: 1fr !important; }
  dl.vs-stack > div.vs-cluster { grid-template-columns: 1fr; }
  .vs-span-2, .vs-span-3 { grid-column: auto; }
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

function viewspecUtf8Bytes(value) {
  return new TextEncoder().encode(String(value ?? '')).length;
}

function viewspecAssertPayloadBounds(detail) {
  if (detail.kind === 'bulk_action') {
    const selectionBindings = detail.payloadBindings.filter((id) => id.endsWith('_selection') || id.endsWith('_selected_ids'));
    if (selectionBindings.length !== 1 || detail.payloadBindings.length !== 1) {
      throw new Error('COLLECTION_BULK_SELECTION_AMBIGUOUS');
    }
    const value = detail.payloadValues[selectionBindings[0]];
    if (Array.isArray(value) && value.length > 100) {
      throw new Error('COLLECTION_BULK_SELECTION_TOO_LARGE');
    }
    if (viewspecUtf8Bytes(value) > 4096) {
      throw new Error('COLLECTION_BULK_SELECTION_TOO_LARGE');
    }
    return;
  }
  if (['search', 'filter', 'sort', 'paginate'].includes(detail.kind)) {
    for (const bindingId of detail.payloadBindings) {
      if (viewspecUtf8Bytes(detail.payloadValues[bindingId]) > 512) {
        throw new Error('COLLECTION_ACTION_PAYLOAD_TOO_LARGE');
      }
    }
  }
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
  viewspecAssertPayloadBounds(detail);
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


# The MCP Apps host bridge. The wire shapes follow the ext-apps SDK types, which hosts validate
# against: ui/message content is a block array, and the host sends nothing until the handshake ends.
MCP_APP_BRIDGE_SCRIPT = r"""
<script>
(() => {
  if (window.parent === window) return;
  const tools = JSON.parse(document.querySelector('meta[name="viewspec-mcp-tools"]').content);
  const pending = new Map();
  // Everything the emitter can write. A root with anything else did not come from the compiler.
  const rootTags = new Set('button dd div dl dt h1 h2 header hr input li main p section table tbody td th tr ul'.split(' '));
  const rootAttributes = /^(aria-[a-z]+|class|data-[a-z-]+|hidden|id|role|scope|style|type|value)$/;
  // The CSS that `check` refuses, plus the escapes and comments that could disguise it, which the
  // emitter never writes either. A style reaching this far is a hand-built root, and a fetch from
  // one would otherwise stop only at the host's CSP.
  const rootUnsafeStyle = /\\|\/\*|@import|url\(|image-set\(|image\(|src\(|expression\(|javascript:|vbscript:|https?:|\/\//i;
  let nextId = 1;

  const post = (message) => window.parent.postMessage({ jsonrpc: '2.0', ...message }, '*');
  const reportError = (error) => post({ method: 'notifications/message', params: { level: 'error', data: error.message } });
  const actionButton = (root, id) => root.querySelector(`[data-action-id="${CSS.escape(id)}"]`);

  function request(method, params) {
    const id = nextId++;
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      post({ id, method, params });
    });
  }

  // DOMParser keeps scripts inert, but an inline handler fires once its node joins this page, and
  // the host's default CSP allows it. So a root is refused whole, never cleaned, and the nodes
  // adopted are the ones checked here.
  function parseRoot(html) {
    const { head, body } = new DOMParser().parseFromString(html, 'text/html');
    const root = body.firstElementChild;
    const elements = root ? [root, ...root.querySelectorAll('*')] : [];
    const compiled = !head.firstChild && body.childElementCount === 1 && root?.classList.contains('vs-root')
      && elements.every((el) => rootTags.has(el.localName)
        && el.getAttributeNames().every((name) => rootAttributes.test(name))
        && !rootUnsafeStyle.test(el.getAttribute('style') ?? ''));
    if (!compiled) throw new Error('viewspecRoot is not a compiled ViewSpec root.');
    return root;
  }

  // The new root is built from the tool's data, so what only this page knows is carried across it:
  // typed text, the caret, and which buttons are still waiting on a tool.
  function replaceRoot(html) {
    const root = parseRoot(html);
    const current = document.querySelector('.vs-root');
    const twin = (el) => (el.dataset.actionId
      ? actionButton(root, el.dataset.actionId)
      : root.querySelector(`[data-binding-id="${CSS.escape(el.dataset.bindingId)}"]`));
    current.querySelectorAll('input[data-binding-id], [data-action-id]:disabled').forEach((el) => {
      const next = twin(el);
      if (next && el.tagName !== 'INPUT') next.disabled = true;
      else if (next && el.value !== el.defaultValue) next.value = el.value;
    });
    const active = document.activeElement;
    const focused = current.contains(active) ? twin(active) : null;
    current.replaceWith(root);
    focused?.focus({ preventScroll: true });
    focused?.setSelectionRange?.(active.selectionStart, active.selectionEnd);
  }

  function applyToolResult(result) {
    if (result?.isError) return;
    const { viewspecRoot, viewspecBindings: bindings } = result?.structuredContent ?? {};
    if (viewspecRoot !== undefined) replaceRoot(viewspecRoot);
    if (!bindings || typeof bindings !== 'object') return;
    document.querySelectorAll('[data-binding-id]').forEach((el) => {
      const id = el.dataset.bindingId;
      if (!Object.hasOwn(bindings, id)) return;
      const value = String(bindings[id] ?? '');
      if (el.tagName === 'INPUT') el.value = value;
      else el.textContent = value;
    });
  }

  function reportSize() {
    let scheduled = false;
    let lastWidth = 0;
    let lastHeight = 0;
    const measure = () => {
      if (scheduled) return;
      scheduled = true;
      requestAnimationFrame(() => {
        scheduled = false;
        const html = document.documentElement;
        const originalHeight = html.style.height;
        html.style.height = 'max-content';
        const height = Math.ceil(html.getBoundingClientRect().height);
        html.style.height = originalHeight;
        const width = Math.ceil(window.innerWidth);
        if (width === lastWidth && height === lastHeight) return;
        lastWidth = width;
        lastHeight = height;
        post({ method: 'ui/notifications/size-changed', params: { width, height } });
      });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(document.documentElement);
    observer.observe(document.body);
  }

  function sendAction({ id, kind, targetRef, payloadValues }, label) {
    if (tools[id]) return request('tools/call', { name: tools[id], arguments: payloadValues });
    const text = `${label}: ${JSON.stringify({ action: id, kind, target: targetRef, payload: payloadValues })}`;
    const content = [{ type: 'text', text }];
    if (kind === 'select') return request('ui/update-model-context', { content });
    return request('ui/message', { role: 'user', content });
  }

  window.addEventListener('message', (event) => {
    const message = event.data;
    if (event.source !== window.parent || message?.jsonrpc !== '2.0') return;
    if (message.method === undefined) {
      const call = pending.get(message.id);
      if (!call) return;
      pending.delete(message.id);
      if (message.error) call.reject(new Error(message.error.message));
      else call.resolve(message.result);
    } else if (message.method === 'ui/notifications/tool-result') {
      try {
        applyToolResult(message.params);
      } catch (error) {
        reportError(error);
      }
    } else if (message.id !== undefined) {
      const known = message.method === 'ping' || message.method === 'ui/resource-teardown';
      post(known ? { id: message.id, result: {} } : { id: message.id, error: { code: -32601, message: 'Method not found' } });
    }
  });

  const ready = request('ui/initialize', {
    appInfo: { name: 'viewspec-mcp-app', version: '1' },
    appCapabilities: {},
    protocolVersion: '2026-01-26'
  }).then(() => {
    post({ method: 'ui/notifications/initialized', params: {} });
    reportSize();
  });

  document.addEventListener('viewspec-action', (event) => {
    const { id } = event.detail;
    const button = actionButton(document, id);
    const label = button.textContent.trim();
    const refocus = document.activeElement === button;
    button.disabled = true;
    delete button.dataset.mcpState;
    ready
      .then(() => sendAction(event.detail, label))
      .then((result) => {
        if (result?.isError) throw new Error(`Action ${id} failed.`);
        applyToolResult(result);
      })
      .catch((error) => {
        const current = actionButton(document, id);
        if (current) current.dataset.mcpState = 'error';
        reportError(error);
      })
      .finally(() => {
        // A render may have replaced the button, and disabling it already cost it the focus.
        const current = actionButton(document, id);
        if (!current) return;
        current.disabled = false;
        if (refocus) current.focus({ preventScroll: true });
      });
  });
})();
</script>
""".strip()


# A host sizes the iframe from the reported content height, so a 100vh floor would stop it shrinking.
MCP_APP_EMITTER_CSS = """
body, .vs-root { min-height: 0; }
.vs-button:disabled { cursor: progress; opacity: 0.6; }
.vs-button[data-mcp-state="error"] { outline: 2px solid #dc2626; outline-offset: 2px; }
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
        if (
            "<" in value
            or ">" in value
            or "\\" in value
            or "/*" in value
            or "*/" in value
            or UNSAFE_STYLE_VALUE_RE.search(value)
        ):
            raise ValueError(f"Style token '{token}' contains an active or auto-fetching CSS surface.")


def _style_attr(css: str) -> str:
    return f' style="{escape(css, quote=True)}"' if css else ""


def _validate_ir_contract(node: IRNode, seen_ids: set[str]) -> None:
    if not node.id or not SAFE_IR_ID_RE.match(node.id):
        raise ValueError(f"IRNode.id '{node.id}' must use only letters, digits, underscore, dot, and dash.")
    if node.id in seen_ids:
        raise ValueError(f"Duplicate IRNode.id '{node.id}' would produce duplicate DOM artifact identity.")
    seen_ids.add(node.id)
    if node.primitive not in SUPPORTED_PRIMITIVES:
        supported = ", ".join(sorted(SUPPORTED_PRIMITIVES))
        raise ValueError(f"Unsupported IR primitive '{node.primitive}'. Supported primitives: {supported}.")
    visibility_rule_id = node.props.get("visibility_rule_id")
    if visibility_rule_id is not None and (
        not isinstance(visibility_rule_id, str) or not SAFE_IR_ID_RE.match(visibility_rule_id)
    ):
        raise ValueError(
            f"visibility_rule_id '{visibility_rule_id}' must use only letters, digits, underscore, dot, and dash."
        )
    state_text_id = node.props.get("state_text_id")
    if state_text_id is not None and (not isinstance(state_text_id, str) or not SAFE_IR_ID_RE.match(state_text_id)):
        raise ValueError(f"state_text_id '{state_text_id}' must use only letters, digits, underscore, dot, and dash.")
    if "state_text_initial" in node.props and not isinstance(node.props.get("state_text_initial"), str):
        raise ValueError(f"state_text_initial on '{node.id}' must be a string.")
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
    if not isinstance(payload_bindings, list) or any(
        not isinstance(item, str) or not SAFE_IR_ID_RE.match(item) for item in payload_bindings
    ):
        raise ValueError(f"Button IRNode '{node.id}' payload_bindings must be a list of safe ids.")


def _closed_prop_class(node: IRNode, prop_name: str, classes_by_value: dict[str, str]) -> str | None:
    value = node.props.get(prop_name)
    if value is None:
        return None
    if not isinstance(value, str) or value not in classes_by_value:
        allowed = ", ".join(sorted(classes_by_value))
        raise ValueError(f"UNSAFE_ROLE_CLASS: IRNode '{node.id}' prop '{prop_name}' must use one of: {allowed}.")
    return classes_by_value[value]


def _span_class(node: IRNode) -> str | None:
    value = node.props.get("span_columns")
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value not in SPAN_CLASS_BY_COLUMNS:
        allowed = ", ".join(str(value) for value in sorted(SPAN_CLASS_BY_COLUMNS))
        raise ValueError(f"UNSAFE_ROLE_CLASS: IRNode '{node.id}' prop 'span_columns' must use one of: {allowed}.")
    return SPAN_CLASS_BY_COLUMNS[value]


def _node_classes(node: IRNode) -> list[str]:
    classes = [TAILWIND_BY_PRIMITIVE[node.primitive]]
    for prop_name, classes_by_value in (
        ("layout_role", LAYOUT_ROLE_CLASS_BY_ROLE),
        ("layout_emphasis", LAYOUT_EMPHASIS_CLASS_BY_VALUE),
        ("motif_kind", MOTIF_KIND_CLASS_BY_KIND),
        ("product_role", PRODUCT_ROLE_CLASS_BY_ROLE),
    ):
        class_name = _closed_prop_class(node, prop_name, classes_by_value)
        if class_name is not None:
            classes.append(class_name)
    span_class = _span_class(node)
    if span_class is not None:
        classes.append(span_class)
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
    aesthetic_profile = node.props.get("aesthetic_profile")
    if isinstance(aesthetic_profile, str) and aesthetic_profile:
        # Inject the profile's shape-language custom properties (canonical order) ahead of the
        # node's own style. They inherit to every .vs-* descendant. Empty for all B2 profiles.
        shape_css = profile_shape_var_css(aesthetic_profile)
        if shape_css:
            style_css = f"{shape_css} {style_css}".strip()
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
    if isinstance(aesthetic_profile, str) and aesthetic_profile:
        attrs.append(f'data-aesthetic-profile="{escape(aesthetic_profile, quote=True)}"')
    if node.primitive == "root" and node.props.get("semantic_context") == "embedded_screen":
        attrs.append('data-viewspec-screen-root="embedded"')
    for prop, attribute in (
        ("resource_id", "data-resource-id"),
        ("resource_view_id", "data-resource-view-id"),
        ("record_id", "data-record-id"),
        ("resource_field", "data-resource-field"),
    ):
        value = node.props.get(prop)
        if isinstance(value, str) and value:
            attrs.append(f'{attribute}="{escape(value, quote=True)}"')
    visibility_rule_id = node.props.get("visibility_rule_id")
    if isinstance(visibility_rule_id, str) and visibility_rule_id:
        # AppBundle V4 visibility bake: marker always, `hidden` iff the initial verdict is false.
        hidden_initial = bool(node.props.get("visibility_hidden_initial"))
        attrs.append(f'data-visibility-rule="{escape(visibility_rule_id, quote=True)}"')
        attrs.append(f'data-visibility-state="{"hidden" if hidden_initial else "visible"}"')
        if hidden_initial:
            attrs.append("hidden")
    state_text_id = node.props.get("state_text_id")
    if isinstance(state_text_id, str) and state_text_id:
        attrs.append(f'data-state-text-id="{escape(state_text_id, quote=True)}"')
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
            ]
        )
        labelled_by = node.props.get("labelled_by")
        if isinstance(node.props.get("aria_label"), str):
            attrs.append(f'aria-label="{escape(str(node.props["aria_label"]), quote=True)}"')
        elif isinstance(labelled_by, str) and labelled_by:
            # Compiler-associated visible field label: the label's text IS the accessible name.
            attrs.append(f'aria-labelledby="dom-{escape(labelled_by, quote=True)}"')
        else:
            attrs.append(f'aria-label="{escape(str(node.props.get("binding_id", "input")), quote=True)}"')
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
    elif node.props.get("motif_kind") == "loading_state" and node.primitive == "surface":
        attrs.extend(
            [
                'role="status"',
                'aria-busy="true"',
                f'aria-label="{escape(str(node.props.get("aria_label", "Loading state")), quote=True)}"',
            ]
        )
    elif node.props.get("motif_kind") == "error_state" and node.primitive == "surface":
        attrs.extend(
            [
                'role="alert"',
                f'aria-label="{escape(str(node.props.get("aria_label", "Error state")), quote=True)}"',
            ]
        )
    elif node.props.get("motif_kind") == "hero" and node.primitive == "surface":
        attrs.append(f'aria-label="{escape(str(node.props.get("aria_label", "Hero")), quote=True)}"')
    elif node.props.get("table_cell_role") == "row_header":
        attrs.append('scope="row"')

    if node.primitive == "root":
        tag = "div" if node.props.get("semantic_context") == "embedded_screen" else "main"
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
    elif node.props.get("motif_kind") in {"loading_state", "error_state"} and node.primitive == "surface":
        tag = "section"
    elif node.props.get("state_motif_role") == "title":
        tag = "h2"
    elif node.props.get("state_motif_role") == "description":
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
        inner_html = f'<div class="font-bold">{code}</div><div class="mt-1">{message}</div>'
    else:
        pieces: list[str] = []
        if isinstance(node.props.get("state_text_id"), str):
            pieces.append(escape(str(node.props.get("state_text_initial") or "")))
        elif "text" in node.props:
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


# Author style tokens set `display` inline (e.g. flex on .vs-surface), which beats the UA's
# `[hidden]` rule — visibility-baked nodes need an !important override. Emitted ONLY when a
# visibility node exists so every existing artifact stays byte-identical.
VISIBILITY_EMITTER_CSS = ".vs-root [data-visibility-rule][hidden] { display: none !important; }"


def _has_visibility_node(node: IRNode) -> bool:
    if node.props.get("visibility_rule_id"):
        return True
    return any(_has_visibility_node(child) for child in node.children)


def emit_compiler_result(
    result: CompilerResult,
    style_values: dict[str, str],
    *,
    output_dir: str | Path = "viewspec_output",
    title: str = "ViewSpec Artifact",
    mcp_tools: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Emit a CompilerResult as HTML + Tailwind with provenance manifest.

    ``mcp_tools`` selects the MCP Apps document: it maps action ids to MCP tool names, and any
    mapping, even an empty one, adds the host bridge. That document always carries the action
    runtime, because a later tool result can bring the view its first action.
    """
    output_path = Path(output_dir)
    _validate_ir_contract(result.root.root, set())
    _validate_style_values(style_values)
    output_path.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {}
    body = _render_node(result.root.root, manifest, style_values)
    body_tail = [body]
    if _has_action_node(result.root.root) or mcp_tools is not None:
        body_tail.append(ACTION_EVENT_SCRIPT)
    emitter_css = OFFLINE_EMITTER_CSS
    if _has_visibility_node(result.root.root):
        emitter_css = f"{OFFLINE_EMITTER_CSS}\n{VISIBILITY_EMITTER_CSS}"
    head_extra: list[str] = []
    if mcp_tools is not None:
        body_tail.append(MCP_APP_BRIDGE_SCRIPT)
        emitter_css = f"{emitter_css}\n{MCP_APP_EMITTER_CSS}"
        head_extra.append(f'<meta name="viewspec-mcp-tools" content="{_json_attr(dict(mcp_tools))}">')
    html = "\n".join(
        [
            "<!DOCTYPE html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            *head_extra,
            f"<title>{escape(title)}</title>",
            "<style>",
            emitter_css,
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
    write_artifact_set(
        output_path,
        [
            (html_path, html),
            (manifest_path, json.dumps(manifest, indent=2)),
            (diagnostics_path, json.dumps([d.to_json() for d in result.diagnostics], indent=2, sort_keys=True)),
        ],
        logger=_LOGGER,
    )
    return {
        "html": str(html_path),
        "manifest": str(manifest_path),
        "diagnostics": str(diagnostics_path),
    }


class HtmlTailwindEmitter(EmitterPlugin):
    """Deterministic HTML + Tailwind emitter plugin."""

    def __init__(self, *, mcp_tools: Mapping[str, str] | None = None) -> None:
        self.mcp_tools = mcp_tools

    def emit(self, ast_bundle: ASTBundle, output_dir: str | Path) -> dict[str, str]:
        style_values = dict(ast_bundle.style_values or DEFAULT_STYLE_TOKEN_VALUES)
        return emit_compiler_result(
            ast_bundle.result, style_values, output_dir=output_dir, title=ast_bundle.title, mcp_tools=self.mcp_tools
        )


PLUGIN_CLASS = HtmlTailwindEmitter
