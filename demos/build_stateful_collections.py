"""Build the Stateful Collections Desk demo page from the reference compiler."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from viewspec import IntentBundle, ViewSpecBuilder, compile
from viewspec.emitters.html_tailwind import ACTION_EVENT_SCRIPT, OFFLINE_EMITTER_CSS, _render_node
from viewspec.types import ASTBundle, DEFAULT_STYLE_TOKEN_VALUES, IRNode
from seo_metadata import demo_head_metadata

ROOT = Path(__file__).resolve().parents[1]


INCIDENTS = [
    {
        "id": "inc_1042",
        "label": "INC-1042 API latency",
        "owner": "Core Platform",
        "severity": "P1",
        "status": "Open",
        "updated": "8 min ago",
        "sla": "42 min",
    },
    {
        "id": "inc_1057",
        "label": "INC-1057 Checkout failures",
        "owner": "Payments",
        "severity": "P0",
        "status": "Escalated",
        "updated": "13 min ago",
        "sla": "18 min",
    },
    {
        "id": "inc_1061",
        "label": "INC-1061 Mobile auth spike",
        "owner": "Identity",
        "severity": "P2",
        "status": "Investigating",
        "updated": "24 min ago",
        "sla": "2 hr",
    },
    {
        "id": "inc_1069",
        "label": "INC-1069 Data export lag",
        "owner": "Data Ops",
        "severity": "P3",
        "status": "Queued",
        "updated": "41 min ago",
        "sla": "6 hr",
    },
]


def build_loaded_bundle() -> IntentBundle:
    builder = ViewSpecBuilder("stateful_collections_desk")

    controls = builder.add_form("incident_controls", region="main", group_id="control_fields")
    controls.add_field(label="Search queue", value="tenant:atlas latency", id="search_query")
    controls.add_field(label="Status filter", value="open|escalated", id="status_filter")
    controls.add_field(label="Sort order", value="severity_desc", id="sort_order")
    controls.add_field(label="Page cursor", value="cursor:next", id="page_cursor")

    builder.add_node("selection_state", "selection_state", attrs={"label": "Selected ids", "selected_ids": "INC-1042,INC-1057"})
    selection_members = [
        builder.bind_attr("selection_label", "selection_state", "label", region="main", present_as="label"),
        builder.bind_attr("queue_selected_ids", "selection_state", "selected_ids", region="main", present_as="input"),
    ]
    controls._append_members(selection_members)

    table = builder.add_table("incident_queue", region="main", group_id="incident_rows")
    for incident in INCIDENTS:
        table.add_row(
            label=incident["label"],
            values={
                "owner": incident["owner"],
                "severity": incident["severity"],
                "status": incident["status"],
                "updated": incident["updated"],
                "sla": incident["sla"],
            },
            id=incident["id"],
        )

    builder.add_collection_action(
        "search_incidents",
        "search",
        "Search",
        collection_id="incident_queue",
        payload_bindings=["search_query_value"],
    )
    builder.add_collection_action(
        "filter_incidents",
        "filter",
        "Filter",
        collection_id="incident_queue",
        payload_bindings=["status_filter_value"],
    )
    builder.add_collection_action(
        "sort_incidents",
        "sort",
        "Sort",
        collection_id="incident_queue",
        payload_bindings=["sort_order_value"],
    )
    builder.add_collection_action(
        "paginate_incidents",
        "paginate",
        "Next page",
        collection_id="incident_queue",
        payload_bindings=["page_cursor_value"],
    )
    builder.add_collection_action(
        "bulk_assign_incidents",
        "bulk_action",
        "Assign selected",
        collection_id="incident_queue",
        payload_bindings=["queue_selected_ids"],
    )

    builder.add_style("style_controls", "motif:incident_controls", "surface.subtle")
    builder.add_style("style_p0", "binding:inc_1057_severity", "tone.accent")
    builder.add_style("style_escalated", "binding:inc_1057_status", "emphasis.high")
    builder.add_style("style_queue", "motif:incident_queue", "density.compact")
    return builder.build_bundle()


def build_loading_bundle() -> IntentBundle:
    builder = ViewSpecBuilder("stateful_collections_loading")
    builder.add_loading_state(
        "incident_loading",
        title="Loading incident queue",
        description="Waiting for the host data provider to return the next collection page.",
    )
    return builder.build_bundle()


def build_error_bundle() -> IntentBundle:
    builder = ViewSpecBuilder("stateful_collections_error")
    builder.add_error_state(
        "incident_error",
        title="Queue unavailable",
        description="The host data provider returned a retryable 503. ViewSpec renders the alert; the host owns retry behavior.",
    )
    return builder.build_bundle()


def build_empty_bundle() -> IntentBundle:
    builder = ViewSpecBuilder("stateful_collections_empty")
    builder.add_empty_state(
        "incident_empty",
        title="No incidents match this filter",
        description="The current collection is empty. ViewSpec renders the absence state; the host owns query changes.",
    )
    return builder.build_bundle()


def count_ir_nodes(node: IRNode) -> int:
    return 1 + sum(count_ir_nodes(child) for child in node.children)


def render_fragment(ast: ASTBundle, namespace: str) -> tuple[str, dict[str, Any]]:
    manifest: dict[str, Any] = {}
    style_values = dict(ast.style_values or DEFAULT_STYLE_TOKEN_VALUES)
    rendered = _render_node(ast.result.root.root, manifest, style_values)
    namespaced = rendered.replace('id="dom-', f'id="{namespace}-dom-')
    return namespaced, {f"{namespace}-{key}": value for key, value in manifest.items()}


def compile_demo_bundles() -> dict[str, dict[str, Any]]:
    bundles = {
        "loaded": build_loaded_bundle(),
        "loading": build_loading_bundle(),
        "error": build_error_bundle(),
        "empty": build_empty_bundle(),
    }
    states: dict[str, dict[str, Any]] = {}
    for key, bundle in bundles.items():
        ast = compile(bundle)
        if ast.result.diagnostics:
            diagnostics = [diagnostic.to_json() for diagnostic in ast.result.diagnostics]
            raise RuntimeError(f"{key} bundle produced diagnostics: {json.dumps(diagnostics, indent=2)}")
        fragment, manifest = render_fragment(ast, key)
        states[key] = {
            "fragment": fragment,
            "manifest": manifest,
            "intentJson": json.dumps(bundle.to_json(), indent=2, sort_keys=True),
            "bindingCount": len(bundle.view_spec.bindings),
            "actionCount": len(bundle.view_spec.actions),
            "motifKinds": [motif.kind for motif in bundle.view_spec.motifs],
            "nodeCount": count_ir_nodes(ast.result.root.root),
        }
    return states


def script_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True).replace("</script>", "<\\/script>")


def tab_button(key: str, label: str, *, active: bool = False) -> str:
    return (
        f'<button type="button" class="state-tab" data-state="{html.escape(key)}" '
        f'aria-pressed="{str(active).lower()}">{html.escape(label)}</button>'
    )


def build_page(states: dict[str, dict[str, Any]]) -> str:
    state_meta = {
        "loaded": {
            "label": "Loaded",
            "summary": "Controls plus table/list collection action events. Rows do not mutate in this demo.",
        },
        "loading": {
            "label": "Loading",
            "summary": "A checked status section with role=status and aria-busy=true.",
        },
        "error": {
            "label": "Error",
            "summary": "A checked alert section with role=alert.",
        },
        "empty": {
            "label": "Empty",
            "summary": "A checked absence state for no-results or first-run surfaces.",
        },
    }
    state_payload = {
        key: {
            "label": state_meta[key]["label"],
            "summary": state_meta[key]["summary"],
            "bindingCount": states[key]["bindingCount"],
            "actionCount": states[key]["actionCount"],
            "motifKinds": states[key]["motifKinds"],
            "nodeCount": states[key]["nodeCount"],
        }
        for key in states
    }
    tabs = "\n".join(tab_button(key, state_meta[key]["label"], active=key == "loaded") for key in states)
    views = "\n".join(
        f'''          <section class="artifact-view{' active' if key == 'loaded' else ''}" data-artifact-state="{html.escape(key)}" aria-label="{html.escape(state_meta[key]["label"])} artifact">
            {states[key]["fragment"]}
          </section>'''
        for key in states
    )
    intent_options = "\n".join(
        f'<option value="{html.escape(key)}">{html.escape(state_meta[key]["label"])} IntentBundle</option>'
        for key in states
    )
    head_meta = demo_head_metadata(
        title="ViewSpec Demo - Stateful Collections Desk",
        description="Inspect a bounded operational collection surface with ViewSpec collection actions, state motifs, provenance, and event-only payload dispatch.",
        canonical_path="stateful-collections",
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ViewSpec Demo - Stateful Collections Desk</title>
{head_meta}
  <link rel="icon" href="data:,">
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="../shared/nav.js" defer></script>
  <style>
{OFFLINE_EMITTER_CSS}

    :root {{
      color-scheme: dark;
    }}

    body {{
      background: #080b10;
      color: #f8fafc;
      min-height: 100vh;
    }}

    .page-shell {{
      width: min(1420px, calc(100% - 32px));
      margin: 0 auto;
      padding: 32px 0 48px;
    }}

    .hero {{
      display: grid;
      gap: 18px;
      padding: 32px 0 24px;
      border-bottom: 1px solid rgba(148, 163, 184, 0.16);
    }}

    .eyebrow {{
      color: #2dd4bf;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 0.78rem;
      font-weight: 900;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}

    h1 {{
      font-size: clamp(2.35rem, 5.5vw, 4.9rem);
      line-height: 0.96;
      margin: 0;
      letter-spacing: 0;
      max-width: 12ch;
    }}

    .lead {{
      color: #cbd5e1;
      font-size: 1.08rem;
      line-height: 1.7;
      margin: 0;
      max-width: 78ch;
    }}

    .demo-grid {{
      display: grid;
      gap: 18px;
      grid-template-columns: minmax(0, 1.65fr) minmax(320px, 0.85fr);
      margin-top: 24px;
    }}

    .panel {{
      background: rgba(15, 23, 42, 0.88);
      border: 1px solid rgba(148, 163, 184, 0.18);
      border-radius: 8px;
      overflow: hidden;
    }}

    .panel-header {{
      align-items: center;
      border-bottom: 1px solid rgba(148, 163, 184, 0.14);
      display: flex;
      gap: 12px;
      justify-content: space-between;
      padding: 14px 16px;
    }}

    .panel-title {{
      color: #ffffff;
      font-weight: 900;
      letter-spacing: 0;
    }}

    .state-tabs {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}

    .state-tab {{
      background: rgba(15, 23, 42, 0.85);
      border: 1px solid rgba(148, 163, 184, 0.24);
      border-radius: 999px;
      color: #cbd5e1;
      cursor: pointer;
      font: inherit;
      font-size: 0.85rem;
      font-weight: 850;
      min-height: 36px;
      padding: 0 12px;
    }}

    .state-tab[aria-pressed="true"] {{
      background: #0f766e;
      border-color: #2dd4bf;
      color: #ffffff;
    }}

    .artifact-stage {{
      background: #e5edf6;
      min-height: 620px;
      overflow: auto;
      padding: 16px;
    }}

    .artifact-view {{
      display: none;
    }}

    .artifact-view.active {{
      display: block;
    }}

    .artifact-stage .vs-root {{
      min-height: 0;
      width: min(100%, 1040px);
      margin: 0 auto;
    }}

    .artifact-stage .vs-role-app-shell {{
      width: 100%;
    }}

    .artifact-stage .vs-role-form-panel {{
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
    }}

    .artifact-stage .vs-role-action-row {{
      justify-content: flex-start;
      margin: 8px 0 2px;
    }}

    .artifact-stage .vs-button {{
      min-height: 38px;
    }}

    .artifact-stage table.vs-stack {{
      font-size: 0.9rem;
    }}

    .artifact-stage th.vs-label {{
      width: 30%;
    }}

    .inspector-body {{
      display: grid;
      gap: 14px;
      padding: 16px;
    }}

    .stat-grid {{
      display: grid;
      gap: 10px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }}

    .stat {{
      background: rgba(2, 6, 23, 0.38);
      border: 1px solid rgba(148, 163, 184, 0.16);
      border-radius: 8px;
      padding: 12px;
    }}

    .stat span {{
      color: #94a3b8;
      display: block;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 0.7rem;
      font-weight: 900;
      margin-bottom: 5px;
      text-transform: uppercase;
    }}

    .stat strong {{
      color: #ffffff;
      font-size: 1rem;
    }}

    .summary {{
      color: #cbd5e1;
      line-height: 1.62;
      margin: 0;
    }}

    .event-card {{
      background: #05070a;
      border: 1px solid rgba(45, 212, 191, 0.24);
      border-radius: 8px;
      overflow: hidden;
    }}

    .event-card header {{
      align-items: center;
      border-bottom: 1px solid rgba(148, 163, 184, 0.14);
      display: flex;
      gap: 10px;
      justify-content: space-between;
      padding: 10px 12px;
    }}

    .pill {{
      border: 1px solid rgba(148, 163, 184, 0.24);
      border-radius: 999px;
      color: #94a3b8;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 0.72rem;
      font-weight: 850;
      padding: 4px 8px;
    }}

    .pill[data-state="live"] {{
      border-color: rgba(45, 212, 191, 0.4);
      color: #5eead4;
    }}

    pre {{
      color: #dbeafe;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 0.76rem;
      line-height: 1.52;
      margin: 0;
      max-height: 360px;
      overflow: auto;
      padding: 12px;
      white-space: pre-wrap;
    }}

    .intent-select {{
      background: #020617;
      border: 1px solid rgba(148, 163, 184, 0.28);
      border-radius: 8px;
      color: #f8fafc;
      min-height: 40px;
      padding: 0 10px;
      width: 100%;
    }}

    .contract-list {{
      color: #cbd5e1;
      display: grid;
      gap: 8px;
      line-height: 1.5;
      margin: 0;
      padding-left: 18px;
    }}

    @media (max-width: 1100px) {{
      .demo-grid {{
        grid-template-columns: 1fr;
      }}

      .artifact-stage .vs-role-form-panel {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}
    }}

    @media (max-width: 720px) {{
      .page-shell {{
        width: min(100% - 20px, 1420px);
      }}

      .panel-header {{
        align-items: flex-start;
        flex-direction: column;
      }}

      .artifact-stage {{
        padding: 10px;
      }}

      .artifact-stage .vs-role-form-panel {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <main class="page-shell">
    <header class="hero">
      <span class="eyebrow">ViewSpec Demo</span>
      <h1>Stateful Collections Desk</h1>
      <p class="lead">A bounded operational queue compiled from IntentBundle JSON. ViewSpec owns collection structure, action payload identity, provenance, and current-state markup. The host owns search, filtering, sorting, pagination, selection, mutation, retries, and data refresh.</p>
    </header>

    <section class="demo-grid" aria-label="Stateful collection demo">
      <article class="panel">
        <header class="panel-header">
          <span class="panel-title">Compiled artifact</span>
          <div class="state-tabs" aria-label="Current rendered state">
{tabs}
          </div>
        </header>
        <div class="artifact-stage">
{views}
        </div>
      </article>

      <aside class="panel" aria-label="Collection event inspector">
        <header class="panel-header">
          <span class="panel-title">Event inspector</span>
          <span id="event-status" class="pill">waiting</span>
        </header>
        <div class="inspector-body">
          <p id="state-summary" class="summary">{html.escape(state_meta["loaded"]["summary"])}</p>
          <div class="stat-grid">
            <div class="stat"><span>motifs</span><strong id="motif-kinds">table, form</strong></div>
            <div class="stat"><span>actions</span><strong id="action-count">5</strong></div>
            <div class="stat"><span>bindings</span><strong id="binding-count">0</strong></div>
            <div class="stat"><span>IR nodes</span><strong id="node-count">0</strong></div>
          </div>

          <div class="event-card">
            <header>
              <strong>Last viewspec-action</strong>
              <span id="event-kind" class="pill">none</span>
            </header>
            <pre id="event-json">Click Search, Filter, Sort, Next page, or Assign selected in the compiled artifact.</pre>
          </div>

          <label>
            <span class="pill">IntentBundle source</span>
            <select id="intent-select" class="intent-select">
{intent_options}
            </select>
          </label>
          <div class="event-card">
            <pre id="intent-json"></pre>
          </div>

          <ul class="contract-list">
            <li>Collection actions target exactly one table/list motif by <code>motif:incident_queue</code>.</li>
            <li>Generated buttons dispatch ViewSpec events only; this page does not change the table rows.</li>
            <li>Loading and error tabs are separate compiled current states, not async transition logic.</li>
          </ul>
        </div>
      </aside>
    </section>
  </main>

  <script id="state-data" type="application/json">{script_json(state_payload)}</script>
  <script id="intent-data" type="application/json">{script_json({key: states[key]["intentJson"] for key in states})}</script>
  <script>
    const stateData = JSON.parse(document.getElementById('state-data').textContent);
    const intentData = JSON.parse(document.getElementById('intent-data').textContent);
    const tabs = Array.from(document.querySelectorAll('.state-tab[data-state]'));
    const views = Array.from(document.querySelectorAll('[data-artifact-state]'));
    const stateSummary = document.getElementById('state-summary');
    const motifKinds = document.getElementById('motif-kinds');
    const actionCount = document.getElementById('action-count');
    const bindingCount = document.getElementById('binding-count');
    const nodeCount = document.getElementById('node-count');
    const eventStatus = document.getElementById('event-status');
    const eventKind = document.getElementById('event-kind');
    const eventJson = document.getElementById('event-json');
    const intentSelect = document.getElementById('intent-select');
    const intentJson = document.getElementById('intent-json');

    function activateState(state) {{
      const meta = stateData[state];
      if (!meta) return;
      tabs.forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.state === state)));
      views.forEach((view) => view.classList.toggle('active', view.dataset.artifactState === state));
      stateSummary.textContent = meta.summary;
      motifKinds.textContent = meta.motifKinds.join(', ');
      actionCount.textContent = meta.actionCount;
      bindingCount.textContent = meta.bindingCount;
      nodeCount.textContent = meta.nodeCount;
      intentSelect.value = state;
      intentJson.textContent = intentData[state];
    }}

    tabs.forEach((button) => {{
      button.addEventListener('click', () => activateState(button.dataset.state));
    }});

    intentSelect.addEventListener('change', () => {{
      intentJson.textContent = intentData[intentSelect.value];
    }});

    document.addEventListener('viewspec-action', (event) => {{
      eventStatus.textContent = 'captured';
      eventStatus.dataset.state = 'live';
      eventKind.textContent = event.detail.kind || 'action';
      eventKind.dataset.state = 'live';
      eventJson.textContent = JSON.stringify(event.detail, null, 2);
    }});

    activateState('loaded');
    window.__viewspecStatefulCollectionsDemo = {{ stateData, intentData }};
  </script>
  {ACTION_EVENT_SCRIPT}
</body>
</html>
"""


def main() -> None:
    states = compile_demo_bundles()
    output_dir = ROOT / "demos" / "stateful-collections"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "index.html"
    output_path.write_text(build_page(states), encoding="utf-8")
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
