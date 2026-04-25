"""Build the Live Builder demo page from the reference compiler."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from viewspec import IntentBundle, ViewSpecBuilder, compile
from viewspec.emitters.html_tailwind import ACTION_EVENT_SCRIPT, _render_node
from viewspec.types import ASTBundle, DEFAULT_STYLE_TOKEN_VALUES, IRNode

ROOT = Path(__file__).resolve().parents[1]


def build_invoice_bundle() -> IntentBundle:
    builder = ViewSpecBuilder("live_invoice")
    table = builder.add_table("line_items", region="main", group_id="rows")
    table.add_row(label="Design System Audit", value="$4,200", id="invoice_audit")
    table.add_row(label="Component Library", value="$8,500", id="invoice_components")
    table.add_row(label="API Integration", value="$3,100", id="invoice_api")
    table.add_row(label="QA & Testing", value="$2,800", id="invoice_qa")
    table.add_row(label="Documentation", value="$1,400", id="invoice_docs")
    builder.add_style("style_docs_muted", "binding:invoice_docs_label", "tone.muted")
    builder.add_style("style_docs_value", "binding:invoice_docs_value", "emphasis.high")
    return builder.build_bundle()


def build_dashboard_bundle() -> IntentBundle:
    builder = ViewSpecBuilder("live_dashboard")
    dashboard = builder.add_dashboard("kpis", region="main", group_id="cards")
    dashboard.add_card(label="Revenue", value="$2.4M", id="kpi_revenue")
    dashboard.add_card(label="Customers", value="1,847", id="kpi_customers")
    dashboard.add_card(label="Churn", value="3.2%", id="kpi_churn")
    dashboard.add_card(label="MRR Growth", value="+18%", id="kpi_growth")
    builder.add_style("style_revenue_value", "binding:kpi_revenue_value", "emphasis.high")
    builder.add_style("style_growth_value", "binding:kpi_growth_value", "tone.accent")
    return builder.build_bundle()


def build_roster_bundle() -> IntentBundle:
    team = [
        {"id": "alice_chen", "name": "Alice Chen", "role": "Principal Engineer", "location": "SF", "status": "Active"},
        {"id": "bob_kowalski", "name": "Bob Kowalski", "role": "Design Lead", "location": "NYC", "status": "Active"},
        {"id": "cara_oduya", "name": "Cara Oduya", "role": "ML Researcher", "location": "London", "status": "On Leave"},
        {"id": "david_park", "name": "David Park", "role": "Product Manager", "location": "Seoul", "status": "Active"},
        {"id": "elena_vasquez", "name": "Elena Vasquez", "role": "DevRel", "location": "Austin", "status": "Active"},
    ]

    builder = ViewSpecBuilder("live_roster")
    table = builder.add_table("team", region="main", group_id="members")
    for member in team:
        node_id = member["id"]
        builder.add_node(
            node_id,
            "team_member",
            attrs={
                "name": member["name"],
                "role": member["role"],
                "location": member["location"],
                "status": member["status"],
            },
        )
        bindings = [
            builder.bind_attr(f"{node_id}_name", node_id, "name", region="main", present_as="label"),
            builder.bind_attr(f"{node_id}_role", node_id, "role", region="main", present_as="value"),
            builder.bind_attr(f"{node_id}_location", node_id, "location", region="main", present_as="text"),
            builder.bind_attr(f"{node_id}_status", node_id, "status", region="main", present_as="badge"),
        ]
        table._append_members(bindings)

    builder.add_style("style_cara_status", "binding:cara_oduya_status", "tone.muted")
    builder.add_style("style_elena_status", "binding:elena_vasquez_status", "tone.accent")
    return builder.build_bundle()


def build_comparison_bundle() -> IntentBundle:
    builder = ViewSpecBuilder("live_comparison")
    comparison = builder.add_comparison("plans", region="main", group_id="tiers")
    comparison.add_item(label="Starter", value="$9/mo - 1 seat, 10GB storage, email support", id="plan_starter")
    comparison.add_item(label="Pro", value="$29/mo - 5 seats, 100GB storage, priority support", id="plan_pro")
    comparison.add_item(
        label="Enterprise",
        value="Custom - unlimited seats, unlimited storage, dedicated CSM",
        id="plan_enterprise",
    )
    builder.add_style("style_pro_value", "binding:plan_pro_value", "emphasis.high")
    return builder.build_bundle()


PRESET_BUILDERS = {
    "invoice": {
        "label": "Invoice",
        "description": "A table motif for five invoice line items.",
        "builder": build_invoice_bundle,
    },
    "dashboard": {
        "label": "Dashboard",
        "description": "Four KPI cards compiled from dashboard intent.",
        "builder": build_dashboard_bundle,
    },
    "roster": {
        "label": "Team Roster",
        "description": "A multi-field table motif for people, roles, locations, and status.",
        "builder": build_roster_bundle,
    },
    "comparison": {
        "label": "Pricing Comparison",
        "description": "Three pricing tiers compiled through the comparison motif.",
        "builder": build_comparison_bundle,
    },
}


def render_fragment(ast: ASTBundle, namespace: str) -> str:
    """Render one compiled AST to an embeddable artifact fragment."""
    manifest: dict[str, Any] = {}
    style_values = dict(ast.style_values or DEFAULT_STYLE_TOKEN_VALUES)
    rendered = _render_node(ast.result.root.root, manifest, style_values)
    return rendered.replace('id="dom-', f'id="{namespace}-dom-')


def count_ir_nodes(node: IRNode) -> int:
    return 1 + sum(count_ir_nodes(child) for child in node.children)


def display_ref(ref: str) -> str:
    return ref.replace("viewspec:", "")


def primitive_class(primitive: str) -> str:
    if primitive in {"root", "stack", "grid", "cluster"}:
        return "primitive-layout"
    if primitive == "surface":
        return "primitive-surface"
    if primitive in {"label", "value", "text", "badge", "button", "image_slot", "rule", "svg"}:
        return "primitive-content"
    return "primitive-neutral"


def generate_ir_tree_html(node: IRNode, depth: int = 0) -> str:
    """Generate a native details/summary visualization for a CompositionIR node."""
    open_attr = " open" if depth < 3 else ""
    primitive = html.escape(node.primitive)
    node_id = html.escape(node.id)
    first_ref = node.provenance.intent_refs[0] if node.provenance.intent_refs else ""
    ref_html = f'<span class="refs">&larr; {html.escape(display_ref(first_ref))}</span>' if first_ref else ""
    child_html = "".join(generate_ir_tree_html(child, depth + 1) for child in node.children)
    child_wrapper = f'<div class="ir-children">{child_html}</div>' if child_html else ""
    return (
        f'<details class="ir-node" data-depth="{depth}"{open_attr}>'
        "<summary>"
        f'<span class="primitive {primitive_class(node.primitive)}">{primitive}</span>'
        f'<span class="node-id">{node_id}</span>'
        f"{ref_html}"
        "</summary>"
        f"{child_wrapper}"
        "</details>"
    )


def compile_presets() -> dict[str, dict[str, Any]]:
    presets: dict[str, dict[str, Any]] = {}
    for key, config in PRESET_BUILDERS.items():
        bundle = config["builder"]()
        ast = compile(bundle)
        if ast.result.diagnostics:
            diagnostics = [d.to_json() for d in ast.result.diagnostics]
            raise RuntimeError(f"{key} preset produced diagnostics: {json.dumps(diagnostics, indent=2)}")

        intent_json = json.dumps(bundle.to_json(), indent=2, sort_keys=True)
        presets[key] = {
            "label": config["label"],
            "description": config["description"],
            "intentJson": intent_json,
            "irTreeHtml": generate_ir_tree_html(ast.result.root.root),
            "renderedHtml": render_fragment(ast, key),
            "nodeCount": count_ir_nodes(ast.result.root.root),
            "bindingCount": len(bundle.view_spec.bindings),
        }
    return presets


def safe_json_for_script(value: Any) -> str:
    """Serialize JSON for direct assignment inside a script tag."""
    return json.dumps(value, indent=2, sort_keys=True).replace("</script>", "<\\/script>")


def build_page(presets: dict[str, dict[str, Any]]) -> str:
    preset_json = safe_json_for_script(presets)
    option_html = "\n".join(
        f'<option value="{html.escape(key)}">{html.escape(data["label"])}</option>' for key, data in presets.items()
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ViewSpec Demo - Live Builder</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    :root {{
      color-scheme: dark;
    }}

    body {{
      background: #101214;
      color: #f8fafc;
      min-height: 100vh;
    }}

    .panel {{
      background: rgba(15, 23, 42, 0.92);
      border: 1px solid rgba(148, 163, 184, 0.22);
      border-radius: 8px;
      min-height: 34rem;
      overflow: hidden;
    }}

    .panel-body {{
      height: 34rem;
      overflow: auto;
    }}

    .json-code {{
      color: #dbeafe;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      font-size: 0.74rem;
      line-height: 1.48;
      tab-size: 2;
      white-space: pre;
    }}

    .ir-tree {{
      color: #cbd5e1;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      font-size: 0.78rem;
      line-height: 1.45;
      padding: 0.85rem;
    }}

    .ir-node {{
      border-left: 1px solid rgba(148, 163, 184, 0.24);
      margin-left: 0.35rem;
      padding-left: 0.7rem;
    }}

    .ir-node > summary {{
      align-items: center;
      cursor: pointer;
      display: flex;
      gap: 0.45rem;
      min-height: 1.8rem;
      white-space: nowrap;
    }}

    .primitive {{
      border-radius: 999px;
      display: inline-flex;
      font-size: 0.68rem;
      font-weight: 800;
      letter-spacing: 0.08em;
      padding: 0.14rem 0.45rem;
      text-transform: uppercase;
    }}

    .primitive-layout {{
      background: rgba(59, 130, 246, 0.16);
      color: #93c5fd;
    }}

    .primitive-surface {{
      background: rgba(168, 85, 247, 0.16);
      color: #d8b4fe;
    }}

    .primitive-content {{
      background: rgba(20, 184, 166, 0.16);
      color: #5eead4;
    }}

    .primitive-neutral {{
      background: rgba(148, 163, 184, 0.16);
      color: #cbd5e1;
    }}

    .node-id {{
      color: #f8fafc;
      font-weight: 700;
    }}

    .refs {{
      color: #94a3b8;
      font-size: 0.72rem;
      overflow: hidden;
      text-overflow: ellipsis;
    }}

    .rendered-output > main {{
      min-height: 34rem;
      overflow: auto;
    }}

    @media (max-width: 1023px) {{
      .panel-body {{
        height: 28rem;
      }}
    }}
  </style>
</head>
<body>
  <main class="mx-auto flex min-h-screen max-w-[96rem] flex-col px-4 py-8 sm:px-6 lg:px-8">
    <header class="mb-6 grid gap-5 border-b border-white/10 pb-6 lg:grid-cols-[1fr_auto] lg:items-end">
      <div class="max-w-3xl">
        <p class="mb-3 font-mono text-sm font-semibold uppercase tracking-[0.18em] text-teal-300">ViewSpec Demo</p>
        <h1 class="text-4xl font-black leading-tight text-white sm:text-5xl">Live Builder</h1>
        <p class="mt-4 text-base leading-7 text-slate-300 sm:text-lg">
          Browse a compiled ViewSpec pipeline: declarative intent, CompositionIR, and emitted UI stay in sync.
        </p>
      </div>
      <label class="grid gap-2 text-sm font-bold text-slate-300">
        Preset
        <select id="preset-select" class="rounded-lg border border-teal-400/30 bg-slate-950 px-4 py-3 text-base font-bold text-white outline-none ring-0 focus:border-teal-300">
{option_html}
        </select>
      </label>
    </header>

    <section class="mb-4 grid gap-3 text-sm text-slate-400 lg:grid-cols-[1fr_auto] lg:items-center">
      <div>
        <h2 id="preset-title" class="text-xl font-black text-white">Invoice</h2>
        <p id="preset-description" class="mt-1 leading-6">A table motif for five invoice line items.</p>
      </div>
      <div class="flex flex-wrap gap-2 font-mono text-xs">
        <span class="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1"><span class="text-teal-300">bindings</span> <span id="binding-count">0</span></span>
        <span class="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1"><span class="text-teal-300">ir nodes</span> <span id="node-count">0</span></span>
      </div>
    </section>

    <section class="grid flex-1 gap-4 lg:grid-cols-3">
      <article class="panel">
        <header class="flex items-center justify-between gap-3 border-b border-white/10 px-4 py-3">
          <div>
            <h3 class="font-bold text-white">Intent JSON</h3>
            <p class="text-xs text-slate-400">IntentBundle input</p>
          </div>
          <span class="rounded-full border border-slate-600 px-2 py-1 font-mono text-[0.68rem] uppercase tracking-wide text-slate-400">Read Only</span>
        </header>
        <div class="panel-body bg-slate-950/80 p-4">
          <pre><code id="intent-json" class="json-code"></code></pre>
        </div>
      </article>

      <article class="panel">
        <header class="border-b border-white/10 px-4 py-3">
          <h3 class="font-bold text-white">CompositionIR</h3>
          <p class="text-xs text-slate-400">Native details tree</p>
        </header>
        <div id="ir-tree" class="panel-body ir-tree"></div>
      </article>

      <article class="panel">
        <header class="border-b border-white/10 px-4 py-3">
          <h3 class="font-bold text-white">Rendered Output</h3>
          <p class="text-xs text-slate-400">HTML emitted from compiled IR</p>
        </header>
        <div id="rendered-output" class="panel-body rendered-output bg-slate-100"></div>
      </article>
    </section>
  </main>

  <script>
    const PRESET_DATA = {preset_json};

    const presetSelect = document.getElementById('preset-select');
    const presetTitle = document.getElementById('preset-title');
    const presetDescription = document.getElementById('preset-description');
    const bindingCount = document.getElementById('binding-count');
    const nodeCount = document.getElementById('node-count');
    const intentJson = document.getElementById('intent-json');
    const irTree = document.getElementById('ir-tree');
    const renderedOutput = document.getElementById('rendered-output');

    function activatePreset(key) {{
      const preset = PRESET_DATA[key];
      if (!preset) return;
      presetTitle.textContent = preset.label;
      presetDescription.textContent = preset.description;
      bindingCount.textContent = preset.bindingCount;
      nodeCount.textContent = preset.nodeCount;
      intentJson.textContent = preset.intentJson;
      irTree.innerHTML = preset.irTreeHtml;
      renderedOutput.innerHTML = preset.renderedHtml;
    }}

    presetSelect.addEventListener('change', () => activatePreset(presetSelect.value));
    activatePreset('invoice');
    window.__viewspecLiveBuilder = PRESET_DATA;
  </script>
  {ACTION_EVENT_SCRIPT}
</body>
</html>
"""


def main() -> None:
    presets = compile_presets()
    output_dir = ROOT / "demos" / "live-builder"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "index.html"
    output_path.write_text(build_page(presets), encoding="utf-8")
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
