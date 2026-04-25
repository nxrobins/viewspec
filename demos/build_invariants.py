"""Build the Invariants demo page from the reference compiler."""

from __future__ import annotations

import copy
import dataclasses
import html
import json
from pathlib import Path
from typing import Any

from viewspec import IntentBundle, ViewSpecBuilder, compile
from viewspec.emitters.html_tailwind import ACTION_EVENT_SCRIPT, _render_node
from viewspec.types import ASTBundle, CompilerDiagnostic, DEFAULT_STYLE_TOKEN_VALUES, IRNode, Provenance

ROOT = Path(__file__).resolve().parents[1]

ERROR_STYLE_TOKEN = "invariant.error"
ERROR_STYLE_VALUE = "outline: 2px dashed #ef4444; outline-offset: 4px; background: #fef2f2;"


def build_exactly_once_bundle() -> IntentBundle:
    builder = ViewSpecBuilder("invariant_exactly_once")
    table = builder.add_table("rows", region="main", group_id="row_order")
    table.add_row(label="North Region", value="$420K", id="row_1", node_kind="revenue_row")
    table.add_row(label="South Region", value="$365K", id="row_2", node_kind="revenue_row")
    table.add_row(label="West Region", value="$288K", id="row_3", node_kind="revenue_row")
    table.add_row(label="Partner", value="$96K", id="row_4", node_kind="revenue_row")
    builder.add_style("style_row_1_value", "binding:row_1_value", "emphasis.high")
    return builder.build_bundle()


def build_grouping_bundle() -> IntentBundle:
    builder = ViewSpecBuilder("invariant_grouping")
    dashboard = builder.add_dashboard("cards", region="main", group_id="card_order")
    dashboard.add_card(label="Revenue", value="$2.4M", id="card_1")
    dashboard.add_card(label="Pipeline", value="$8.1M", id="card_2")
    dashboard.add_card(label="Retention", value="94%", id="card_3")
    builder.add_style("style_card_1_value", "binding:card_1_value", "emphasis.high")
    builder.add_style("style_card_3_value", "binding:card_3_value", "tone.accent")
    return builder.build_bundle()


def build_ordering_bundle() -> IntentBundle:
    builder = ViewSpecBuilder("invariant_ordering")
    table = builder.add_table("ordered_rows", region="main", group_id="declared_order")
    table.add_row(label="Step 1", value="Discover", id="row_1", node_kind="ordered_row")
    table.add_row(label="Step 2", value="Design", id="row_2", node_kind="ordered_row")
    table.add_row(label="Step 3", value="Build", id="row_3", node_kind="ordered_row")
    table.add_row(label="Step 4", value="Ship", id="row_4", node_kind="ordered_row")
    builder.add_style("style_row_4_value", "binding:row_4_value", "tone.accent")
    return builder.build_bundle()


def compile_valid_ast(bundle: IntentBundle, key: str) -> ASTBundle:
    ast = compile(bundle)
    if ast.result.diagnostics:
        diagnostics = [d.to_json() for d in ast.result.diagnostics]
        raise RuntimeError(f"{key} valid compile produced diagnostics: {json.dumps(diagnostics, indent=2)}")
    return ast


def clone_for_violation(ast: ASTBundle) -> ASTBundle:
    """Deep-copy compiler output before mutating a violation example."""
    style_values = dict(ast.style_values or DEFAULT_STYLE_TOKEN_VALUES)
    style_values[ERROR_STYLE_TOKEN] = ERROR_STYLE_VALUE
    return dataclasses.replace(ast, result=copy.deepcopy(ast.result), style_values=style_values)


def find_node(node: IRNode, node_id: str) -> IRNode | None:
    if node.id == node_id:
        return node
    for child in node.children:
        found = find_node(child, node_id)
        if found is not None:
            return found
    return None


def require_node(root: IRNode, node_id: str) -> IRNode:
    node = find_node(root, node_id)
    if node is None:
        raise KeyError(f"IR node not found: {node_id}")
    return node


def find_parent(node: IRNode, child_id: str) -> IRNode | None:
    for child in node.children:
        if child.id == child_id:
            return node
        parent = find_parent(child, child_id)
        if parent is not None:
            return parent
    return None


def require_parent(root: IRNode, child_id: str) -> IRNode:
    parent = find_parent(root, child_id)
    if parent is None:
        raise KeyError(f"Parent not found for IR node: {child_id}")
    return parent


def add_error_style(node: IRNode) -> None:
    if ERROR_STYLE_TOKEN not in node.style_tokens:
        node.style_tokens.append(ERROR_STYLE_TOKEN)


def append_diagnostic(
    ast: ASTBundle,
    *,
    code: str,
    message: str,
    intent_ref: str,
    content_ref: str | None = None,
    node_id: str | None = None,
) -> None:
    ast.result.diagnostics.append(
        CompilerDiagnostic(
            severity="error",
            code=code,
            message=message,
            intent_ref=intent_ref,
            content_ref=content_ref,
            region_id="main",
            node_id=node_id,
        )
    )


def prepend_error_boundary(
    motif: IRNode,
    *,
    boundary_id: str,
    code: str,
    message: str,
    intent_ref: str,
    content_ref: str | None = None,
) -> None:
    motif.children.insert(
        0,
        IRNode(
            id=boundary_id,
            primitive="error_boundary",
            props={"diagnostic_code": code, "message": message},
            provenance=Provenance(
                content_refs=[content_ref] if content_ref else [],
                intent_refs=[intent_ref],
            ),
        ),
    )


def build_exactly_once_violation(valid_ast: ASTBundle) -> ASTBundle:
    ast = clone_for_violation(valid_ast)
    root = ast.result.root.root
    motif = require_node(root, "motif_rows")
    row_2_value = require_node(root, "binding_row_2_value")
    row_3 = require_node(root, "motif_rows_row_3")

    duplicate = copy.deepcopy(row_2_value)
    duplicate.id = "binding_row_2_value_duplicate"
    add_error_style(row_2_value)
    add_error_style(duplicate)
    row_3.children.append(duplicate)

    code = "DUPLICATE_ROUTING"
    message = "Binding 'row_2_value' routed to multiple IR nodes"
    prepend_error_boundary(
        motif,
        boundary_id="error_boundary_duplicate_routing",
        code=code,
        message=message,
        intent_ref="viewspec:binding:row_2_value",
        content_ref="node:row_2#attr:value",
    )
    append_diagnostic(
        ast,
        code=code,
        message=message,
        intent_ref="viewspec:binding:row_2_value",
        content_ref="node:row_2#attr:value",
        node_id=duplicate.id,
    )
    return ast


def build_grouping_violation(valid_ast: ASTBundle) -> ASTBundle:
    ast = clone_for_violation(valid_ast)
    root = ast.result.root.root
    motif = require_node(root, "motif_cards")
    card_1 = require_node(root, "motif_cards_card_1")
    card_1_label = require_node(root, "binding_card_1_label")
    card_2_value_parent = require_parent(root, "binding_card_2_value")
    card_2_value = require_node(root, "binding_card_2_value")

    card_2_value_parent.children.remove(card_2_value)
    card_1.children.insert(1, card_2_value)
    add_error_style(card_1_label)
    add_error_style(card_2_value)

    code = "GROUP_VIOLATION"
    message = "Binding 'card_1_label' grouped with non-sibling 'card_2_value'"
    prepend_error_boundary(
        motif,
        boundary_id="error_boundary_group_violation",
        code=code,
        message=message,
        intent_ref="viewspec:binding:card_1_label",
        content_ref="node:card_1#attr:label",
    )
    append_diagnostic(
        ast,
        code=code,
        message=message,
        intent_ref="viewspec:binding:card_1_label",
        content_ref="node:card_1#attr:label",
        node_id=card_1.id,
    )
    return ast


def build_ordering_violation(valid_ast: ASTBundle) -> ASTBundle:
    ast = clone_for_violation(valid_ast)
    root = ast.result.root.root
    motif = require_node(root, "motif_ordered_rows")
    row_2 = require_node(root, "motif_ordered_rows_row_2")
    row_3 = require_node(root, "motif_ordered_rows_row_3")

    row_2_index = motif.children.index(row_2)
    row_3_index = motif.children.index(row_3)
    motif.children[row_2_index], motif.children[row_3_index] = motif.children[row_3_index], motif.children[row_2_index]
    add_error_style(row_2)
    add_error_style(row_3)

    code = "ORDER_VIOLATION"
    message = "Binding 'row_3_label' precedes 'row_2_label' but was declared after it in the substrate"
    prepend_error_boundary(
        motif,
        boundary_id="error_boundary_order_violation",
        code=code,
        message=message,
        intent_ref="viewspec:binding:row_3_label",
        content_ref="node:row_3#attr:label",
    )
    append_diagnostic(
        ast,
        code=code,
        message=message,
        intent_ref="viewspec:binding:row_3_label",
        content_ref="node:row_3#attr:label",
        node_id=row_3.id,
    )
    return ast


def render_fragment(ast: ASTBundle, namespace: str) -> str:
    """Render one compiled AST to an embeddable artifact fragment."""
    manifest: dict[str, Any] = {}
    style_values = dict(ast.style_values or DEFAULT_STYLE_TOKEN_VALUES)
    rendered = _render_node(ast.result.root.root, manifest, style_values)
    return rendered.replace('id="dom-', f'id="{namespace}-dom-')


def compile_sections() -> list[dict[str, str]]:
    valid_exactly_once = compile_valid_ast(build_exactly_once_bundle(), "exactly_once")
    valid_grouping = compile_valid_ast(build_grouping_bundle(), "grouping")
    valid_ordering = compile_valid_ast(build_ordering_bundle(), "ordering")

    sections = [
        {
            "key": "exactly-once",
            "title": "Exactly-Once Provenance",
            "description": "Every binding must route to one rendered IR node: nothing dropped, nothing duplicated.",
            "valid_label": "4/4 bindings routed exactly once.",
            "violation_label": "row_2_value is routed twice while keeping the same binding and semantic address.",
            "valid_fragment": render_fragment(valid_exactly_once, "exactly-once-valid"),
            "violation_fragment": render_fragment(
                build_exactly_once_violation(valid_exactly_once),
                "exactly-once-violation",
            ),
        },
        {
            "key": "grouping",
            "title": "Semantic Grouping",
            "description": "Bindings from one semantic node stay inside the same visual group.",
            "valid_label": "Each dashboard card contains only sibling bindings from the same semantic node.",
            "violation_label": "card_1_label is grouped with card_2_value, crossing semantic boundaries.",
            "valid_fragment": render_fragment(valid_grouping, "grouping-valid"),
            "violation_fragment": render_fragment(build_grouping_violation(valid_grouping), "grouping-violation"),
        },
        {
            "key": "ordering",
            "title": "Strict Ordering",
            "description": "Rendered order must follow the substrate and ordered group declaration.",
            "valid_label": "Rows render in declared order: row_1, row_2, row_3, row_4.",
            "violation_label": "row_3 renders before row_2 even though it was declared after it.",
            "valid_fragment": render_fragment(valid_ordering, "ordering-valid"),
            "violation_fragment": render_fragment(build_ordering_violation(valid_ordering), "ordering-violation"),
        },
    ]
    return sections


def build_section(section: dict[str, str], index: int) -> str:
    key = html.escape(section["key"])
    title = html.escape(section["title"])
    description = html.escape(section["description"])
    valid_label = html.escape(section["valid_label"])
    violation_label = html.escape(section["violation_label"])
    return f"""
    <section class="invariant-section" data-invariant-section="{key}">
      <div class="section-copy">
        <p class="section-kicker">Invariant {index}</p>
        <h2 class="sr-only">{title}</h2>
        <div class="pretext-canvas-wrap max-w-3xl">
          <canvas data-pretext-canvas data-text="{title}" data-size="34" data-weight="900" data-line-height="39" class="text-white" role="img" aria-label="{title}">{title}</canvas>
        </div>
        <p class="sr-only">{description}</p>
        <div class="pretext-canvas-wrap mt-3 max-w-3xl">
          <canvas data-pretext-canvas data-text="{description}" data-size="16" data-weight="400" data-line-height="25" class="text-slate-300" role="img" aria-label="{description}">{description}</canvas>
        </div>
      </div>
      <div class="toggle-row" aria-label="{title} state">
        <button type="button" class="state-toggle active" data-state="valid" aria-pressed="true">Valid</button>
        <button type="button" class="state-toggle" data-state="violation" aria-pressed="false">Violation</button>
      </div>
      <div class="state-shell">
        <article class="state-view active" data-state-view="valid">
          <div class="state-bar valid-state">
            <canvas data-pretext-canvas data-text="VALID: {valid_label}" data-size="13" data-weight="800" data-line-height="19" class="text-green-100" role="img" aria-label="VALID: {valid_label}">VALID: {valid_label}</canvas>
          </div>
          <div class="artifact-frame">
            {section["valid_fragment"]}
          </div>
        </article>
        <article class="state-view" data-state-view="violation">
          <div class="state-bar violation-state">
            <canvas data-pretext-canvas data-text="VIOLATION: {violation_label}" data-size="13" data-weight="800" data-line-height="19" class="text-red-100" role="img" aria-label="VIOLATION: {violation_label}">VIOLATION: {violation_label}</canvas>
          </div>
          <div class="artifact-frame">
            {section["violation_fragment"]}
          </div>
        </article>
      </div>
    </section>"""


def build_page(sections: list[dict[str, str]]) -> str:
    section_html = "\n".join(build_section(section, index + 1) for index, section in enumerate(sections))
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ViewSpec Demo - The Invariants</title>
  <link rel="icon" href="data:,">
  <script src="https://cdn.tailwindcss.com"></script>
  <script type="module" src="../shared/pretext-canvas-surfaces.js"></script>
  <style>
    :root {{
      color-scheme: dark;
    }}

    body {{
      background: #111315;
      color: #f8fafc;
      min-height: 100vh;
    }}

    .page-shell {{
      margin: 0 auto;
      max-width: 78rem;
      padding: 2rem 1rem 3rem;
    }}

    .hero {{
      border-bottom: 1px solid rgba(255, 255, 255, 0.1);
      display: grid;
      gap: 1.5rem;
      margin-bottom: 1.5rem;
      padding-bottom: 1.75rem;
    }}

    .hero h1 {{
      font-size: clamp(2.25rem, 5vw, 4.5rem);
      font-weight: 900;
      line-height: 0.95;
      margin: 0;
    }}

    .hero p {{
      color: #cbd5e1;
      font-size: 1.05rem;
      line-height: 1.7;
      margin: 1rem 0 0;
      max-width: 44rem;
    }}

    .hero-meta {{
      align-self: end;
      background: rgba(255, 255, 255, 0.04);
      border: 1px solid rgba(255, 255, 255, 0.1);
      border-radius: 8px;
      color: #cbd5e1;
      display: grid;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      font-size: 0.82rem;
      gap: 0.35rem;
      padding: 1rem;
    }}

    .hero-meta span {{
      color: #5eead4;
    }}

    .invariant-section {{
      border-bottom: 1px solid rgba(255, 255, 255, 0.1);
      display: grid;
      gap: 1rem;
      padding: 2.25rem 0;
    }}

    .section-copy {{
      max-width: 52rem;
    }}

    .section-kicker {{
      color: #5eead4;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      font-size: 0.78rem;
      font-weight: 800;
      letter-spacing: 0.16em;
      margin: 0 0 0.65rem;
      text-transform: uppercase;
    }}

    .section-copy h2 {{
      color: #ffffff;
      font-size: clamp(1.5rem, 3vw, 2.4rem);
      font-weight: 900;
      line-height: 1.08;
      margin: 0;
    }}

    .section-copy p:not(.section-kicker) {{
      color: #cbd5e1;
      line-height: 1.65;
      margin: 0.7rem 0 0;
    }}

    .toggle-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.55rem;
    }}

    .state-toggle {{
      border: 1px solid rgba(148, 163, 184, 0.35);
      border-radius: 999px;
      color: #cbd5e1;
      font-weight: 800;
      min-width: 7rem;
      padding: 0.6rem 1rem;
      transition: background 160ms ease, border-color 160ms ease, color 160ms ease, transform 160ms ease;
    }}

    .state-toggle:hover {{
      border-color: rgba(45, 212, 191, 0.75);
      color: #ffffff;
      transform: translateY(-1px);
    }}

    .state-toggle.active[data-state="valid"] {{
      background: #047857;
      border-color: #34d399;
      color: #ffffff;
    }}

    .state-toggle.active[data-state="violation"] {{
      background: #b91c1c;
      border-color: #f87171;
      color: #ffffff;
    }}

    .state-shell {{
      background: rgba(255, 255, 255, 0.04);
      border: 1px solid rgba(255, 255, 255, 0.1);
      border-radius: 8px;
      overflow: hidden;
    }}

    .state-view {{
      display: none;
      opacity: 0;
      transform: translateY(5px);
    }}

    .state-view.active {{
      animation: stateFade 180ms ease forwards;
      display: block;
    }}

    .state-bar {{
      border-bottom: 1px solid rgba(255, 255, 255, 0.09);
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      font-size: 0.78rem;
      font-weight: 800;
      letter-spacing: 0.04em;
      line-height: 1.45;
      padding: 0.75rem 1rem;
    }}

    .valid-state {{
      background: rgba(4, 120, 87, 0.16);
      color: #bbf7d0;
    }}

    .violation-state {{
      background: rgba(185, 28, 28, 0.2);
      color: #fecaca;
    }}

    .artifact-frame {{
      background: #f8fafc;
    }}

    .artifact-frame > main {{
      min-height: 0;
      padding: 1rem;
    }}

    .state-view[data-state-view="violation"].active [style*="invariant"],
    .state-view[data-state-view="violation"].active [style*="dashed"] {{
      animation: violationPulse 180ms ease;
    }}

    @keyframes stateFade {{
      from {{
        opacity: 0;
        transform: translateY(5px);
      }}
      to {{
        opacity: 1;
        transform: translateY(0);
      }}
    }}

    @keyframes violationPulse {{
      0%, 100% {{
        transform: translateX(0);
      }}
      33% {{
        transform: translateX(-2px);
      }}
      66% {{
        transform: translateX(2px);
      }}
    }}

    @media (min-width: 900px) {{
      .page-shell {{
        padding-left: 2rem;
        padding-right: 2rem;
      }}

      .hero {{
        grid-template-columns: 1fr auto;
        align-items: end;
      }}
    }}

    @media (max-width: 640px) {{
      .state-toggle {{
        flex: 1 1 9rem;
      }}

      .artifact-frame {{
        overflow-x: auto;
      }}
    }}
  </style>
</head>
<body>
  <main class="page-shell">
    <header class="hero">
      <div>
        <p class="section-kicker">ViewSpec Demo</p>
        <h1 class="sr-only">The Invariants</h1>
        <div class="pretext-canvas-wrap max-w-3xl">
          <canvas data-pretext-canvas data-text="The Invariants" data-size="64" data-weight="900" data-line-height="68" class="text-white" role="img" aria-label="The Invariants">The Invariants</canvas>
        </div>
        <p class="sr-only">Three compiler guarantees rendered side by side: exactly-once provenance, semantic grouping, and strict ordering.</p>
        <div class="pretext-canvas-wrap mt-4 max-w-3xl">
          <canvas data-pretext-canvas data-text="Three compiler guarantees rendered side by side: exactly-once provenance, semantic grouping, and strict ordering." data-size="17" data-weight="400" data-line-height="27" class="text-slate-300" role="img" aria-label="Three compiler guarantees rendered side by side: exactly-once provenance, semantic grouping, and strict ordering.">Three compiler guarantees rendered side by side: exactly-once provenance, semantic grouping, and strict ordering.</canvas>
        </div>
      </div>
      <div class="hero-meta">
        <div><span>compiler</span> = reference</div>
        <div><span>valid states</span> = ViewSpecBuilder + compile()</div>
        <div><span>violations</span> = copied IR + diagnostics</div>
      </div>
    </header>

{section_html}
  </main>

  <script>
    document.addEventListener('click', (event) => {{
      const button = event.target.closest('[data-state]');
      if (!button) return;
      const section = button.closest('[data-invariant-section]');
      if (!section) return;
      const state = button.dataset.state;

      section.querySelectorAll('[data-state]').forEach((candidate) => {{
        const active = candidate.dataset.state === state;
        candidate.classList.toggle('active', active);
        candidate.setAttribute('aria-pressed', String(active));
      }});

      section.querySelectorAll('[data-state-view]').forEach((view) => {{
        view.classList.toggle('active', view.dataset.stateView === state);
      }});
      window.ViewSpecPretext?.refresh(section);
    }});
  </script>
  {ACTION_EVENT_SCRIPT}
</body>
</html>
"""


def main() -> None:
    sections = compile_sections()
    output_dir = ROOT / "demos" / "invariants"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "index.html"
    output_path.write_text(build_page(sections), encoding="utf-8")
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
