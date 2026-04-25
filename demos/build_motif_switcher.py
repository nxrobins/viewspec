"""Build the Motif Switcher demo page from the reference compiler."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from viewspec import IntentBundle, ViewSpecBuilder, compile
from viewspec.emitters.html_tailwind import ACTION_EVENT_SCRIPT, _render_node
from viewspec.types import ASTBundle, DEFAULT_STYLE_TOKEN_VALUES

ROOT = Path(__file__).resolve().parents[1]


TEAM = [
    {
        "id": "alice_chen",
        "name": "Alice Chen",
        "role": "Principal Engineer",
        "location": "SF",
        "status": "Active",
    },
    {
        "id": "bob_kowalski",
        "name": "Bob Kowalski",
        "role": "Design Lead",
        "location": "NYC",
        "status": "Active",
    },
    {
        "id": "cara_oduya",
        "name": "Cara Oduya",
        "role": "ML Researcher",
        "location": "London",
        "status": "On Leave",
    },
    {
        "id": "david_park",
        "name": "David Park",
        "role": "Product Manager",
        "location": "Seoul",
        "status": "Active",
    },
    {
        "id": "elena_vasquez",
        "name": "Elena Vasquez",
        "role": "DevRel",
        "location": "Austin",
        "status": "Active",
    },
]

MOTIFS = {
    "table": {
        "label": "Table",
        "description": "Dense rows for scanning operational data.",
    },
    "dashboard": {
        "label": "Dashboard",
        "description": "Cards that make each person glanceable.",
    },
    "comparison": {
        "label": "Comparison",
        "description": "Side-by-side panels for evaluating roles.",
    },
}


def build_base_bundle() -> IntentBundle:
    """Build one semantic dataset and table motif using the SDK."""
    builder = ViewSpecBuilder("team_roster")
    table = builder.add_table("team", region="main", group_id="members")

    for member in TEAM:
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

    builder.add_style("style_name_muted", "binding:alice_chen_name", "tone.muted")
    builder.add_style("style_status_accent", "binding:elena_vasquez_status", "tone.accent")
    return builder.build_bundle()


def bundle_for_motif(base_bundle: IntentBundle, kind: str) -> IntentBundle:
    """Clone the frozen ViewSpec and replace only the motif kind."""
    base_motif = base_bundle.view_spec.motifs[0]
    motif = dataclasses.replace(base_motif, kind=kind)
    view_spec = dataclasses.replace(base_bundle.view_spec, motifs=[motif])
    return IntentBundle(substrate=base_bundle.substrate, view_spec=view_spec)


def render_fragment(ast: ASTBundle, namespace: str) -> tuple[str, dict[str, object]]:
    """Render one compiled AST to an embeddable fragment and namespaced manifest."""
    manifest: dict[str, object] = {}
    style_values = dict(ast.style_values or DEFAULT_STYLE_TOKEN_VALUES)
    html = _render_node(ast.result.root.root, manifest, style_values)

    namespaced_manifest = {f"{namespace}-{key}": value for key, value in manifest.items()}
    namespaced_html = html.replace('id="dom-', f'id="{namespace}-dom-')
    return namespaced_html, namespaced_manifest


def compile_variants() -> dict[str, dict[str, object]]:
    """Compile all motif variants from the same substrate and bindings."""
    base_bundle = build_base_bundle()
    variants: dict[str, dict[str, object]] = {}

    for kind in MOTIFS:
        bundle = bundle_for_motif(base_bundle, kind)
        ast = compile(bundle)
        if ast.result.diagnostics:
            diagnostics = [d.to_json() for d in ast.result.diagnostics]
            raise RuntimeError(f"{kind} compile produced diagnostics: {json.dumps(diagnostics, indent=2)}")
        fragment, manifest = render_fragment(ast, kind)
        variants[kind] = {
            "fragment": fragment,
            "manifest": manifest,
            "node_count": count_ir_nodes(ast.result.root.root),
            "binding_count": len(bundle.view_spec.bindings),
            "motif_kind": bundle.view_spec.motifs[0].kind,
        }

    return variants


def count_ir_nodes(node: object) -> int:
    """Count IR nodes without depending on test helpers."""
    children = getattr(node, "children", [])
    return 1 + sum(count_ir_nodes(child) for child in children)


def build_page(variants: dict[str, dict[str, object]]) -> str:
    """Return the complete demo HTML document."""
    variant_data = {
        kind: {
            "label": MOTIFS[kind]["label"],
            "description": MOTIFS[kind]["description"],
            "nodeCount": data["node_count"],
            "bindingCount": data["binding_count"],
            "motifKind": data["motif_kind"],
            "manifest": data["manifest"],
        }
        for kind, data in variants.items()
    }
    variants_json = json.dumps(variant_data, indent=2, sort_keys=True)

    toggles = "\n".join(
        f'''          <button type="button" data-motif="{kind}" class="motif-toggle{' active' if kind == 'table' else ''}">{meta["label"]}</button>'''
        for kind, meta in MOTIFS.items()
    )
    views = "\n".join(
        f'''        <section id="view-{kind}" class="motif-view{' active' if kind == 'table' else ''}" data-view="{kind}" aria-label="{MOTIFS[kind]["label"]} view">
          {data["fragment"]}
        </section>'''
        for kind, data in variants.items()
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ViewSpec Demo - Same Data, Three Motifs</title>
  <link rel="icon" href="data:,">
  <script src="https://cdn.tailwindcss.com"></script>
  <script type="module" src="../shared/pretext-canvas-surfaces.js"></script>
  <style>
    :root {{
      color-scheme: dark;
    }}

    body {{
      background: #101214;
      color: #f8fafc;
      min-height: 100vh;
    }}

    .motif-toggle {{
      border-radius: 999px;
      border: 1px solid rgba(148, 163, 184, 0.35);
      color: #cbd5e1;
      font-weight: 700;
      padding: 0.65rem 1rem;
      transition: background 160ms ease, color 160ms ease, border-color 160ms ease, transform 160ms ease;
      white-space: nowrap;
    }}

    .motif-toggle:hover {{
      border-color: rgba(45, 212, 191, 0.75);
      color: #f8fafc;
      transform: translateY(-1px);
    }}

    .motif-toggle.active {{
      background: #0f766e;
      border-color: #2dd4bf;
      color: #ffffff;
    }}

    .motif-view {{
      display: none;
      opacity: 0;
      transform: translateY(6px);
    }}

    .motif-view.active {{
      animation: motifFade 220ms ease forwards;
      display: block;
    }}

    .motif-view > main {{
      border-radius: 8px;
      min-height: 26rem;
      overflow: hidden;
    }}

    @keyframes motifFade {{
      from {{
        opacity: 0;
        transform: translateY(6px);
      }}
      to {{
        opacity: 1;
        transform: translateY(0);
      }}
    }}
  </style>
</head>
<body>
  <main class="mx-auto flex min-h-screen max-w-6xl flex-col px-4 py-8 sm:px-6 lg:px-8">
    <header class="mb-8 grid gap-6 border-b border-white/10 pb-8 lg:grid-cols-[1fr_auto] lg:items-end">
      <div class="max-w-3xl">
        <p class="mb-3 font-mono text-sm font-semibold uppercase tracking-[0.18em] text-teal-300">ViewSpec Demo</p>
        <h1 class="sr-only">Same Data, Three Motifs</h1>
        <div class="pretext-canvas-wrap max-w-2xl">
          <canvas data-pretext-canvas data-text="Same Data, Three Motifs" data-size="50" data-weight="900" data-line-height="54" class="text-white" role="img" aria-label="Same Data, Three Motifs">Same Data, Three Motifs</canvas>
        </div>
        <p class="sr-only">One semantic team roster compiles into three visual structures. The substrate and bindings stay fixed; only the motif hint changes.</p>
        <div class="pretext-canvas-wrap mt-4 max-w-2xl">
          <canvas data-pretext-canvas data-text="One semantic team roster compiles into three visual structures. The substrate and bindings stay fixed; only the motif hint changes." data-size="18" data-weight="400" data-line-height="29" class="text-slate-300" role="img" aria-label="One semantic team roster compiles into three visual structures. The substrate and bindings stay fixed; only the motif hint changes.">One semantic team roster compiles into three visual structures. The substrate and bindings stay fixed; only the motif hint changes.</canvas>
        </div>
      </div>
      <div class="rounded-lg border border-white/10 bg-white/[0.04] p-4 font-mono text-sm text-slate-300">
        <div><span class="text-teal-300">bindings</span> = 20</div>
        <div><span class="text-teal-300">nodes</span> = 6</div>
        <div><span class="text-teal-300">compiler</span> = reference</div>
      </div>
    </header>

    <section class="mb-6 grid gap-4 lg:grid-cols-[1fr_auto] lg:items-center">
      <div>
        <div class="pretext-canvas-wrap max-w-xl">
          <canvas id="motif-title" data-pretext-canvas data-text="Table" data-size="24" data-weight="800" data-line-height="30" class="text-white" role="img" aria-label="Table">Table</canvas>
        </div>
        <div class="pretext-canvas-wrap mt-1 max-w-xl">
          <canvas id="motif-description" data-pretext-canvas data-text="Dense rows for scanning operational data." data-size="14" data-weight="400" data-line-height="22" class="text-slate-400" role="img" aria-label="Dense rows for scanning operational data.">Dense rows for scanning operational data.</canvas>
        </div>
      </div>
      <div class="flex flex-wrap gap-2" aria-label="Choose motif">
{toggles}
      </div>
    </section>

    <section class="rounded-lg border border-white/10 bg-white/[0.05] p-3 shadow-2xl shadow-black/30 sm:p-4">
{views}
    </section>

    <footer class="mt-6 grid gap-4 text-sm text-slate-400 lg:grid-cols-[1fr_auto] lg:items-center">
      <p>The emitted DOM keeps provenance attributes intact: <code class="text-teal-300">data-ir-id</code>, <code class="text-teal-300">data-content-refs</code>, and <code class="text-teal-300">data-intent-refs</code>.</p>
      <code class="rounded border border-white/10 bg-black/25 px-3 py-2 text-slate-300">motif.kind: table -> dashboard -> comparison</code>
    </footer>
  </main>

  <script id="motif-data" type="application/json">
{variants_json}
  </script>
  <script>
    const motifData = JSON.parse(document.getElementById('motif-data').textContent);
    const title = document.getElementById('motif-title');
    const description = document.getElementById('motif-description');
    const toggles = Array.from(document.querySelectorAll('[data-motif]'));
    const views = Array.from(document.querySelectorAll('.motif-view'));

    function setCanvasText(canvas, text) {{
      canvas.dataset.text = text;
      canvas.setAttribute('aria-label', text);
      canvas.textContent = text;
    }}

    function activateMotif(kind) {{
      toggles.forEach((button) => {{
        const active = button.dataset.motif === kind;
        button.classList.toggle('active', active);
        button.setAttribute('aria-pressed', String(active));
      }});
      views.forEach((view) => {{
        view.classList.toggle('active', view.dataset.view === kind);
      }});
      setCanvasText(title, motifData[kind].label);
      setCanvasText(description, motifData[kind].description);
      window.ViewSpecPretext?.refresh(document.body);
    }}

    toggles.forEach((button) => {{
      button.addEventListener('click', () => activateMotif(button.dataset.motif));
    }});

    activateMotif('table');
    window.__viewspecMotifDemo = motifData;
  </script>
  {ACTION_EVENT_SCRIPT}
</body>
</html>
"""


def main() -> None:
    variants = compile_variants()
    output_dir = ROOT / "demos" / "motif-switcher"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "index.html"
    output_path.write_text(build_page(variants), encoding="utf-8")
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
