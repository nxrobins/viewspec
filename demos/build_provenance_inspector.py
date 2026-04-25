"""Build the Provenance Inspector demo page from the reference compiler."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from viewspec import IntentBundle, ViewSpecBuilder, compile
from viewspec.emitters.html_tailwind import ACTION_EVENT_SCRIPT, _render_node
from viewspec.types import ASTBundle, DEFAULT_STYLE_TOKEN_VALUES

ROOT = Path(__file__).resolve().parents[1]

KPIS = [
    {"id": "kpi_revenue", "label": "Revenue", "value": "$2.4M"},
    {"id": "kpi_users", "label": "Active Users", "value": "18,472"},
    {"id": "kpi_conversion", "label": "Conversion", "value": "3.8%"},
    {"id": "kpi_churn", "label": "Churn", "value": "1.2%"},
]

SEGMENTS = [
    {"id": "segment_enterprise", "label": "Enterprise", "value": "$1.8M", "growth": "+22%"},
    {"id": "segment_mid_market", "label": "Mid-Market", "value": "$420K", "growth": "+15%"},
    {"id": "segment_smb", "label": "SMB", "value": "$180K", "growth": "+8%"},
    {"id": "segment_self_serve", "label": "Self-Serve", "value": "$45K", "growth": "+31%"},
    {"id": "segment_partner", "label": "Partner", "value": "$12K", "growth": "-3%"},
]


def build_bundle() -> IntentBundle:
    """Build one ViewSpec with dashboard and table motifs."""
    builder = ViewSpecBuilder("provenance_inspector")

    dashboard = builder.add_dashboard("kpis", region="main", group_id="kpi_cards")
    for kpi in KPIS:
        dashboard.add_card(label=kpi["label"], value=kpi["value"], id=kpi["id"])

    table = builder.add_table("segments", region="main", group_id="segment_rows")
    for segment in SEGMENTS:
        table.add_row(
            label=segment["label"],
            value=segment["value"],
            values={"growth": segment["growth"]},
            id=segment["id"],
            node_kind="revenue_segment",
        )

    builder.add_style("style_revenue_value", "binding:kpi_revenue_value", "emphasis.high")
    builder.add_style("style_partner_growth", "binding:segment_partner_growth", "tone.muted")
    return builder.build_bundle()


def render_fragment(ast: ASTBundle) -> tuple[str, dict[str, Any]]:
    """Render the compiled root and capture the emitter manifest."""
    manifest: dict[str, Any] = {}
    style_values = dict(ast.style_values or DEFAULT_STYLE_TOKEN_VALUES)
    html = _render_node(ast.result.root.root, manifest, style_values)
    return html, manifest


def build_inspector_data(bundle: IntentBundle, manifest: dict[str, Any]) -> dict[str, Any]:
    """Build serializable lookup tables for the frontend inspector."""
    return {
        "manifest": manifest,
        "provenance_by_ir_id": {entry["ir_id"]: entry for entry in manifest.values()},
        "binding_index": {binding.id: binding.to_json() for binding in bundle.view_spec.bindings},
        "semantic_index": {node_id: node.to_json() for node_id, node in bundle.substrate.nodes.items()},
    }


def compile_demo() -> tuple[str, dict[str, Any], IntentBundle]:
    """Compile the demo ViewSpec and return rendered HTML plus inspector data."""
    bundle = build_bundle()
    ast = compile(bundle)
    if ast.result.diagnostics:
        diagnostics = [d.to_json() for d in ast.result.diagnostics]
        raise RuntimeError(f"provenance inspector compile produced diagnostics: {json.dumps(diagnostics, indent=2)}")
    fragment, manifest = render_fragment(ast)
    return fragment, build_inspector_data(bundle, manifest), bundle


def json_script(value: Any) -> str:
    """Serialize data for inline JavaScript assignment."""
    return json.dumps(value, indent=2, sort_keys=True)


def build_page(fragment: str, data: dict[str, Any], bundle: IntentBundle) -> str:
    """Return the complete provenance inspector HTML document."""
    binding_count = len(bundle.view_spec.bindings)
    semantic_count = len(bundle.substrate.nodes)
    manifest_count = len(data["manifest"])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ViewSpec Demo - Provenance Inspector</title>
  <link rel="icon" href="data:,">
  <script src="https://cdn.tailwindcss.com"></script>
  <script type="module" src="../shared/pretext-canvas-surfaces.js"></script>
  <style>
    :root {{
      color-scheme: dark;
    }}

    body {{
      min-height: 100vh;
      background: #0f1115;
      color: #f8fafc;
    }}

    .content-shell {{
      min-height: 100vh;
    }}

    .artifact-frame {{
      border-radius: 8px;
      overflow: hidden;
    }}

    .artifact-frame > main {{
      min-height: 34rem;
    }}

    .inspector-panel {{
      background: rgba(15, 23, 42, 0.96);
      border: 1px solid rgba(45, 212, 191, 0.24);
      box-shadow: 0 24px 80px rgba(0, 0, 0, 0.35);
    }}

    .provenance-highlight {{
      box-shadow: 0 0 0 2px rgba(45, 212, 191, 0.9), 0 0 26px rgba(45, 212, 191, 0.42) !important;
      outline: 1px solid rgba(153, 246, 228, 0.7);
      outline-offset: 2px;
      position: relative;
      z-index: 5;
    }}

    .chain-card {{
      border-left: 2px solid rgba(45, 212, 191, 0.45);
      margin-left: 0.35rem;
      padding: 0.1rem 0 0.85rem 0.9rem;
    }}

    .chain-label {{
      color: #5eead4;
      font-size: 0.68rem;
      font-weight: 800;
      letter-spacing: 0.14em;
      text-transform: uppercase;
    }}

    .chain-value {{
      color: #e2e8f0;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      font-size: 0.78rem;
      line-height: 1.45;
      margin-top: 0.28rem;
      overflow-wrap: anywhere;
      white-space: pre-wrap;
    }}

    @media (min-width: 1024px) {{
      .content-shell {{
        padding-right: 25rem;
      }}

      .inspector-panel {{
        bottom: 1rem;
        position: fixed;
        right: 1rem;
        top: 1rem;
        width: 23rem;
      }}
    }}

    @media (max-width: 1023px) {{
      .inspector-panel {{
        position: static;
      }}
    }}
  </style>
</head>
<body>
  <main class="content-shell mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
    <header class="mb-8 border-b border-white/10 pb-8">
      <p class="mb-3 font-mono text-sm font-semibold uppercase tracking-[0.18em] text-teal-300">ViewSpec Demo</p>
      <div>
        <h1 class="sr-only">Provenance Inspector</h1>
        <div class="pretext-canvas-wrap max-w-3xl">
          <canvas data-pretext-canvas data-text="Provenance Inspector" data-size="50" data-weight="900" data-line-height="54" class="text-white" role="img" aria-label="Provenance Inspector">Provenance Inspector</canvas>
        </div>
        <p class="sr-only">Hover any rendered element to trace it from DOM node to IR, binding, canonical address, semantic node, and raw value.</p>
        <div class="pretext-canvas-wrap mt-4 max-w-3xl">
          <canvas data-pretext-canvas data-text="Hover any rendered element to trace it from DOM node to IR, binding, canonical address, semantic node, and raw value." data-size="18" data-weight="400" data-line-height="29" class="text-slate-300" role="img" aria-label="Hover any rendered element to trace it from DOM node to IR, binding, canonical address, semantic node, and raw value.">Hover any rendered element to trace it from DOM node to IR, binding, canonical address, semantic node, and raw value.</canvas>
        </div>
        <div class="mt-4 flex flex-wrap gap-4 font-mono text-sm text-slate-300">
          <span><span class="text-teal-300">bindings</span> = {binding_count}</span>
          <span><span class="text-teal-300">semantic nodes</span> = {semantic_count}</span>
          <span><span class="text-teal-300">manifest nodes</span> = {manifest_count}</span>
        </div>
      </div>
    </header>

    <section id="artifact" class="artifact-frame rounded-lg border border-white/10 bg-white/[0.05] p-3 shadow-2xl shadow-black/30 sm:p-4" aria-label="Rendered ViewSpec artifact">
      {fragment}
    </section>

    <aside id="inspector-panel" class="inspector-panel mt-5 flex flex-col rounded-lg p-4 lg:mt-0" aria-live="polite">
      <div class="mb-4 flex items-start justify-between gap-3 border-b border-white/10 pb-4">
        <div>
          <div class="pretext-canvas-wrap w-48">
            <canvas data-pretext-canvas data-text="Provenance Chain" data-size="12" data-weight="800" data-line-height="16" class="text-teal-300" role="img" aria-label="Provenance Chain">Provenance Chain</canvas>
          </div>
          <div class="pretext-canvas-wrap mt-2 w-56">
            <canvas id="inspector-title" data-pretext-canvas data-text="Ready" data-size="18" data-weight="900" data-line-height="23" class="text-white" role="img" aria-label="Ready">Ready</canvas>
          </div>
        </div>
        <span id="lock-state" class="pretext-canvas-wrap w-16 rounded-full border border-slate-600 px-2 py-1 text-slate-400">
          <canvas id="lock-state-text" data-pretext-canvas data-text="Hover" data-size="11" data-weight="800" data-line-height="14" class="text-slate-400" role="img" aria-label="Hover">Hover</canvas>
        </span>
      </div>
      <div id="inspector-body" class="min-h-0 flex-1 overflow-y-auto pr-1">
        <div class="rounded-lg border border-dashed border-slate-600 p-4 text-sm leading-6 text-slate-400">
          Hover over any element to inspect its provenance...
        </div>
      </div>
    </aside>
  </main>

  <script>
    const PROVENANCE_MANIFEST = {json_script(data["manifest"])};
    const PROVENANCE_BY_IR_ID = {json_script(data["provenance_by_ir_id"])};
    const BINDING_INDEX = {json_script(data["binding_index"])};
    const SEMANTIC_INDEX = {json_script(data["semantic_index"])};

    const artifact = document.getElementById('artifact');
    const panel = document.getElementById('inspector-panel');
    const panelBody = document.getElementById('inspector-body');
    const panelTitle = document.getElementById('inspector-title');
    const lockState = document.getElementById('lock-state');
    const lockStateText = document.getElementById('lock-state-text');
    let locked = false;
    let activeElement = null;

    function escapeHtml(value) {{
      return String(value)
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
    }}

    function setCanvasText(canvas, text) {{
      canvas.dataset.text = text;
      canvas.setAttribute('aria-label', text);
      canvas.textContent = text;
    }}

    function formatValue(value) {{
      if (value === null || value === undefined) return 'none';
      if (typeof value === 'string') return value;
      return JSON.stringify(value, null, 2);
    }}

    function parseBindingId(intentRefs) {{
      const bindingRef = (intentRefs || []).find((ref) => ref.startsWith('viewspec:binding:'));
      return bindingRef ? bindingRef.replace('viewspec:binding:', '') : null;
    }}

    function parseAddress(contentRef) {{
      if (!contentRef) return {{}};
      const nodeIdMatch = contentRef.match(/^node:([^#]+)/);
      const attrMatch = contentRef.match(/#attr:([^#]+)/);
      const slotMatch = contentRef.match(/#slot:([^#\\[]+)/);
      return {{
        nodeId: nodeIdMatch ? nodeIdMatch[1] : null,
        attr: attrMatch ? attrMatch[1] : null,
        slot: slotMatch ? slotMatch[1] : null,
      }};
    }}

    function rawValueFor(address, semanticNode) {{
      const parsed = parseAddress(address);
      if (!semanticNode) return null;
      if (parsed.attr && semanticNode.attrs) return semanticNode.attrs[parsed.attr];
      if (parsed.slot && semanticNode.slots) return semanticNode.slots[parsed.slot];
      return null;
    }}

    function chainCard(label, value) {{
      return `
        <section class="chain-card">
          <div class="chain-label">${{escapeHtml(label)}}</div>
          <div class="chain-value">${{escapeHtml(formatValue(value))}}</div>
        </section>
      `;
    }}

    function setActiveElement(element) {{
      if (activeElement && activeElement !== element) {{
        activeElement.classList.remove('provenance-highlight');
      }}
      activeElement = element;
      if (activeElement) {{
        activeElement.classList.add('provenance-highlight');
      }}
    }}

    function clearInspection() {{
      if (activeElement) activeElement.classList.remove('provenance-highlight');
      activeElement = null;
      locked = false;
      setCanvasText(panelTitle, 'Ready');
      setCanvasText(lockStateText, 'Hover');
      lockState.className = 'pretext-canvas-wrap w-16 rounded-full border border-slate-600 px-2 py-1 text-slate-400';
      lockStateText.className = 'text-slate-400';
      panelBody.innerHTML = `
        <div class="rounded-lg border border-dashed border-slate-600 p-4 text-sm leading-6 text-slate-400">
          Hover over any element to inspect its provenance...
        </div>
      `;
      window.ViewSpecPretext?.refresh(panel);
    }}

    function inspectElement(element, shouldLock) {{
      const irId = element.dataset.irId;
      const domId = element.id || `dom-${{irId}}`;
      const entry = PROVENANCE_BY_IR_ID[irId] || PROVENANCE_MANIFEST[domId] || PROVENANCE_MANIFEST[`dom-${{irId}}`];
      if (!entry) return;

      const bindingId = parseBindingId(entry.intent_refs);
      const binding = bindingId ? BINDING_INDEX[bindingId] : null;
      const contentRef = (entry.content_refs || [])[0] || (binding ? binding.address : null);
      const parsedAddress = parseAddress(contentRef);
      const semanticNode = parsedAddress.nodeId ? SEMANTIC_INDEX[parsedAddress.nodeId] : null;
      const rawValue = rawValueFor(contentRef, semanticNode);

      setActiveElement(element);
      locked = shouldLock;
      setCanvasText(panelTitle, shouldLock ? `Locked: ${{entry.primitive}}` : `Inspecting: ${{entry.primitive}}`);
      setCanvasText(lockStateText, shouldLock ? 'Locked' : 'Hover');
      lockState.className = shouldLock
        ? 'pretext-canvas-wrap w-16 rounded-full border border-teal-400 bg-teal-500/10 px-2 py-1 text-teal-200'
        : 'pretext-canvas-wrap w-16 rounded-full border border-slate-600 px-2 py-1 text-slate-400';
      lockStateText.className = shouldLock ? 'text-teal-200' : 'text-slate-400';

      const bindingSummary = binding
        ? `${{binding.id}}\\npresent_as: ${{binding.present_as}}\\nregion: ${{binding.target_region}}`
        : 'container-generated; no binding ref';
      const addressSummary = contentRef || 'container-generated; no content ref';
      const semanticSummary = semanticNode
        ? `${{semanticNode.id}}\\nkind: ${{semanticNode.kind}}\\nattrs: ${{JSON.stringify(semanticNode.attrs, null, 2)}}`
        : 'container-generated; no semantic node';

      panelBody.innerHTML = [
        chainCard('DOM Element', `#${{domId}}\\ndata-ir-id: ${{irId}}`),
        chainCard('IR Node', `${{entry.ir_id}}\\nprimitive: ${{entry.primitive}}\\nprops: ${{JSON.stringify(entry.props, null, 2)}}`),
        chainCard('Binding', bindingSummary),
        chainCard('Address', addressSummary),
        chainCard('Semantic Node', semanticSummary),
        chainCard('Raw Value', rawValue),
        `<div class="mt-2 rounded-lg border border-teal-400/30 bg-teal-400/10 p-3 text-xs font-semibold uppercase tracking-wide text-teal-200">
          Provenance verified: ${{(entry.content_refs || []).length}} content refs, ${{(entry.intent_refs || []).length}} intent refs
        </div>`,
      ].join('');
      window.ViewSpecPretext?.refresh(panel);
    }}

    artifact.addEventListener('pointerover', (event) => {{
      if (locked) return;
      const target = event.target.closest('[data-ir-id]');
      if (!target || !artifact.contains(target)) return;
      inspectElement(target, false);
    }});

    artifact.addEventListener('pointerout', (event) => {{
      if (locked) return;
      if (!artifact.contains(event.relatedTarget)) {{
        clearInspection();
      }}
    }});

    artifact.addEventListener('click', (event) => {{
      const target = event.target.closest('[data-ir-id]');
      if (!target || !artifact.contains(target)) return;
      event.stopPropagation();
      inspectElement(target, true);
    }});

    document.addEventListener('click', (event) => {{
      if (!locked) return;
      if (!artifact.contains(event.target) && !panel.contains(event.target)) {{
        clearInspection();
      }}
    }});

    window.__viewspecProvenanceInspector = {{
      PROVENANCE_MANIFEST,
      PROVENANCE_BY_IR_ID,
      BINDING_INDEX,
      SEMANTIC_INDEX,
    }};
  </script>
  {ACTION_EVENT_SCRIPT}
</body>
</html>
"""


def main() -> None:
    fragment, data, bundle = compile_demo()
    output_dir = ROOT / "demos" / "provenance-inspector"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "index.html"
    output_path.write_text(build_page(fragment, data, bundle), encoding="utf-8")
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
