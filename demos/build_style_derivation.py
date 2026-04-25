"""Build the Style Derivation demo page from the reference compiler."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from viewspec import IntentBundle, ViewSpecBuilder, compile
from viewspec.emitters.html_tailwind import ACTION_EVENT_SCRIPT, _render_node
from viewspec.types import ASTBundle, DEFAULT_STYLE_TOKEN_VALUES, IRNode

ROOT = Path(__file__).resolve().parents[1]

KPIS = [
    {"id": "kpi_revenue", "label": "Revenue", "value": "$2.4M"},
    {"id": "kpi_users", "label": "Active Users", "value": "18,472"},
    {"id": "kpi_conversion", "label": "Conversion", "value": "3.8%"},
    {"id": "kpi_churn", "label": "Churn", "value": "1.2%"},
]

STYLE_PRESETS: dict[str, dict[str, str]] = {
    "default": {
        "label": "Default",
        "feel": "Clean, neutral, the reference compiler baseline.",
        "--bg": "#f8fafc",
        "--card-bg": "#ffffff",
        "--text-primary": "#0f172a",
        "--text-secondary": "#64748b",
        "--accent": "#0f766e",
        "--border-color": "#e2e8f0",
        "--border-radius": "1rem",
        "--card-padding": "1rem",
        "--card-gap": "1rem",
        "--font-heading": "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif",
        "--font-body": "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif",
        "--font-mono": "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
        "--label-size": "0.74rem",
        "--value-size": "2.15rem",
        "--label-weight": "800",
        "--value-weight": "900",
        "--label-tracking": "0.11em",
        "--label-transform": "uppercase",
        "--shadow": "0 18px 40px rgba(15, 23, 42, 0.08)",
    },
    "editorial": {
        "label": "Editorial",
        "feel": "Magazine-dense, high contrast, tight spacing.",
        "--bg": "#11100d",
        "--card-bg": "#171510",
        "--text-primary": "#f7efe0",
        "--text-secondary": "#b9ad98",
        "--accent": "#e8c468",
        "--border-color": "rgba(247, 239, 224, 0.24)",
        "--border-radius": "0.15rem",
        "--card-padding": "0.82rem",
        "--card-gap": "0.65rem",
        "--font-heading": "Georgia, 'Times New Roman', serif",
        "--font-body": "Georgia, 'Times New Roman', serif",
        "--font-mono": "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
        "--label-size": "0.72rem",
        "--value-size": "2.4rem",
        "--label-weight": "700",
        "--value-weight": "900",
        "--label-tracking": "0.04em",
        "--label-transform": "none",
        "--shadow": "0 1px 0 rgba(247, 239, 224, 0.18)",
    },
    "data-dense": {
        "label": "Data-Dense",
        "feel": "Bloomberg terminal energy, maximum information.",
        "--bg": "#050a07",
        "--card-bg": "#07120c",
        "--text-primary": "#d1fae5",
        "--text-secondary": "#86efac",
        "--accent": "#f59e0b",
        "--border-color": "rgba(34, 197, 94, 0.42)",
        "--border-radius": "0.25rem",
        "--card-padding": "0.6rem",
        "--card-gap": "0.5rem",
        "--font-heading": "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
        "--font-body": "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
        "--font-mono": "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
        "--label-size": "0.68rem",
        "--value-size": "1.85rem",
        "--label-weight": "800",
        "--value-weight": "900",
        "--label-tracking": "0.08em",
        "--label-transform": "uppercase",
        "--shadow": "inset 0 0 0 1px rgba(34, 197, 94, 0.2)",
    },
    "soft": {
        "label": "Soft",
        "feel": "Consumer app, lots of air, rounded everything.",
        "--bg": "#fff7ed",
        "--card-bg": "#fffbf3",
        "--text-primary": "#3b2f2f",
        "--text-secondary": "#9a7361",
        "--accent": "#f472b6",
        "--border-color": "#fed7aa",
        "--border-radius": "1.5rem",
        "--card-padding": "1.35rem",
        "--card-gap": "1.1rem",
        "--font-heading": "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif",
        "--font-body": "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif",
        "--font-mono": "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
        "--label-size": "0.78rem",
        "--value-size": "2.35rem",
        "--label-weight": "750",
        "--value-weight": "850",
        "--label-tracking": "0.02em",
        "--label-transform": "none",
        "--shadow": "0 24px 70px rgba(244, 114, 182, 0.18)",
    },
}


def build_bundle() -> IntentBundle:
    """Build a single KPI dashboard with stable semantic IDs."""
    builder = ViewSpecBuilder("style_derivation")
    dashboard = builder.add_dashboard("kpis", region="main", group_id="kpi_cards")
    for kpi in KPIS:
        dashboard.add_card(label=kpi["label"], value=kpi["value"], id=kpi["id"])
    return builder.build_bundle()


def render_fragment(ast: ASTBundle) -> str:
    """Render the compiled root to an embeddable artifact fragment."""
    manifest: dict[str, Any] = {}
    style_values = dict(ast.style_values or DEFAULT_STYLE_TOKEN_VALUES)
    return _render_node(ast.result.root.root, manifest, style_values)


def count_ir_nodes(node: IRNode) -> int:
    return 1 + sum(count_ir_nodes(child) for child in node.children)


def compile_demo() -> tuple[str, dict[str, int]]:
    """Compile and render the style derivation artifact once."""
    bundle = build_bundle()
    ast = compile(bundle)
    if ast.result.diagnostics:
        diagnostics = [diagnostic.to_json() for diagnostic in ast.result.diagnostics]
        raise RuntimeError(f"style derivation compile produced diagnostics: {json.dumps(diagnostics, indent=2)}")
    fragment = render_fragment(ast)
    stats = {
        "bindingCount": len(bundle.view_spec.bindings),
        "semanticNodeCount": len(bundle.substrate.nodes),
        "irNodeCount": count_ir_nodes(ast.result.root.root),
    }
    return fragment, stats


def safe_json_for_script(value: Any) -> str:
    """Serialize JSON for direct assignment inside a script tag."""
    return json.dumps(value, indent=2, sort_keys=True).replace("</script>", "<\\/script>")


def preset_buttons() -> str:
    return "\n".join(
        f'''        <button type="button" class="preset-toggle{' active' if key == 'default' else ''}" data-preset="{html.escape(key)}" aria-pressed="{'true' if key == 'default' else 'false'}">
          <span class="preset-label">{html.escape(preset["label"])}</span>
          <span class="preset-feel">{html.escape(preset["feel"])}</span>
        </button>'''
        for key, preset in STYLE_PRESETS.items()
    )


def preset_css() -> str:
    blocks: list[str] = []
    for key, preset in STYLE_PRESETS.items():
        variables = "\n".join(
            f"      {name}: {value};"
            for name, value in preset.items()
            if name.startswith("--")
        )
        blocks.append(f'    #artifact-wrapper[data-style-preset="{key}"] {{\n{variables}\n    }}')
    return "\n\n".join(blocks)


def build_page(fragment: str, stats: dict[str, int]) -> str:
    presets_json = safe_json_for_script(STYLE_PRESETS)
    stats_json = safe_json_for_script(stats)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ViewSpec Demo - Style Derivation</title>
  <link rel="icon" href="data:,">
  <script src="https://cdn.tailwindcss.com"></script>
  <script type="module" src="../shared/pretext-canvas-surfaces.js"></script>
  <script src="../shared/nav.js" defer></script>
  <style>
    :root {{
      color-scheme: dark;
    }}

    body {{
      background: #06080b;
      color: #f8fafc;
      min-height: 100vh;
    }}

    .page-shell {{
      margin: 0 auto;
      max-width: 72rem;
      padding: 2rem 1rem 3rem;
    }}

    .hero {{
      border-bottom: 1px solid rgba(255, 255, 255, 0.1);
      display: grid;
      gap: 1.5rem;
      margin-bottom: 1.5rem;
      padding-bottom: 1.75rem;
    }}

    .hero-meta {{
      align-self: end;
      background: rgba(255, 255, 255, 0.04);
      border: 1px solid rgba(255, 255, 255, 0.1);
      border-radius: 8px;
      color: #cbd5e1;
      display: grid;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 0.82rem;
      gap: 0.35rem;
      padding: 1rem;
    }}

    .hero-meta span {{
      color: #5eead4;
    }}

    .preset-grid {{
      display: grid;
      gap: 0.65rem;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      margin: 1.5rem 0;
    }}

    .preset-toggle {{
      background: rgba(255, 255, 255, 0.04);
      border: 1px solid rgba(148, 163, 184, 0.24);
      border-radius: 8px;
      color: #cbd5e1;
      display: grid;
      gap: 0.25rem;
      min-height: 5rem;
      padding: 0.9rem;
      text-align: left;
      transition: background 180ms ease, border-color 180ms ease, color 180ms ease, transform 180ms ease;
    }}

    .preset-toggle:hover {{
      border-color: rgba(45, 212, 191, 0.7);
      color: #f8fafc;
      transform: translateY(-1px);
    }}

    .preset-toggle.active {{
      background: rgba(20, 184, 166, 0.14);
      border-color: #2dd4bf;
      color: #ffffff;
    }}

    .preset-label {{
      font-size: 0.9rem;
      font-weight: 900;
    }}

    .preset-feel {{
      color: #94a3b8;
      font-size: 0.72rem;
      line-height: 1.45;
    }}

    .artifact-card {{
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid rgba(255, 255, 255, 0.1);
      border-radius: 8px;
      overflow: hidden;
      padding: 0.75rem;
    }}

    .dogfood-badge {{
      align-items: center;
      background: rgba(45, 212, 191, 0.08);
      border: 1px solid rgba(45, 212, 191, 0.2);
      border-radius: 999px;
      color: #99f6e4;
      display: inline-flex;
      font-size: 0.82rem;
      font-weight: 800;
      gap: 0.5rem;
      margin-top: 1rem;
      padding: 0.55rem 0.85rem;
    }}

    .tokens-panel {{
      border: 1px solid rgba(148, 163, 184, 0.22);
      border-radius: 8px;
      margin-top: 1.5rem;
      overflow: hidden;
    }}

    .tokens-panel summary {{
      color: #cbd5e1;
      cursor: pointer;
      font-size: 0.88rem;
      font-weight: 800;
      list-style: none;
      padding: 0.85rem 1rem;
    }}

    .tokens-panel summary::-webkit-details-marker {{
      display: none;
    }}

    .tokens-panel pre {{
      background: rgba(2, 6, 23, 0.86);
      border-top: 1px solid rgba(148, 163, 184, 0.16);
      color: #dbeafe;
      font-size: 0.78rem;
      line-height: 1.55;
      margin: 0;
      overflow-x: auto;
      padding: 1rem;
    }}

{preset_css()}

    #artifact-wrapper {{
      transition: background-color 300ms ease, border-color 300ms ease;
    }}

    #artifact-wrapper > main {{
      background: var(--bg);
      border-radius: calc(var(--border-radius) + 0.35rem);
      color: var(--text-primary);
      font-family: var(--font-body);
      min-height: 0;
      overflow: hidden;
      padding: clamp(1rem, 3vw, 1.6rem);
      transition: background-color 300ms ease, color 300ms ease, border-radius 300ms ease;
    }}

    #artifact-wrapper [data-ir-id="motif_kpis"] {{
      display: grid;
      gap: var(--card-gap);
      grid-template-columns: repeat(4, minmax(0, 1fr));
      transition: gap 300ms ease;
    }}

    #artifact-wrapper [data-ir-id^="motif_kpis_kpi_"] {{
      background-color: var(--card-bg);
      border: 1px solid var(--border-color);
      border-radius: var(--border-radius);
      box-shadow: var(--shadow);
      color: var(--text-primary);
      padding: var(--card-padding);
      transition: background-color 300ms ease, border-color 300ms ease, border-radius 300ms ease, box-shadow 300ms ease, color 300ms ease, padding 300ms ease;
    }}

    #artifact-wrapper [data-ir-id$="_label"] {{
      color: var(--text-secondary);
      font-family: var(--font-heading);
      font-size: var(--label-size);
      font-weight: var(--label-weight);
      letter-spacing: var(--label-tracking);
      line-height: 1.2;
      text-transform: var(--label-transform);
      transition: color 300ms ease, font-size 300ms ease, letter-spacing 300ms ease;
    }}

    #artifact-wrapper [data-ir-id$="_value"] {{
      color: var(--text-primary);
      font-family: var(--font-body);
      font-size: var(--value-size);
      font-weight: var(--value-weight);
      letter-spacing: -0.035em;
      line-height: 1;
      transition: color 300ms ease, font-size 300ms ease;
    }}

    #artifact-wrapper[data-style-preset="data-dense"] [data-ir-id^="motif_kpis_kpi_"] {{
      background-image:
        linear-gradient(rgba(34, 197, 94, 0.08) 1px, transparent 1px),
        linear-gradient(90deg, rgba(34, 197, 94, 0.08) 1px, transparent 1px);
      background-size: 12px 12px;
    }}

    #artifact-wrapper[data-style-preset="editorial"] [data-ir-id^="motif_kpis_kpi_"] {{
      border-width: 1px 0 0;
    }}

    #artifact-wrapper[data-style-preset="soft"] [data-ir-id^="motif_kpis_kpi_"] {{
      border-width: 2px;
    }}

    @media (min-width: 900px) {{
      .page-shell {{
        padding-left: 2rem;
        padding-right: 2rem;
      }}

      .hero {{
        align-items: end;
        grid-template-columns: 1fr auto;
      }}
    }}

    @media (max-width: 900px) {{
      .preset-grid {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}

      #artifact-wrapper [data-ir-id="motif_kpis"] {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}
    }}

    @media (max-width: 520px) {{
      .preset-grid {{
        grid-template-columns: 1fr;
      }}

      #artifact-wrapper [data-ir-id="motif_kpis"] {{
        grid-template-columns: 1fr;
      }}

      #artifact-wrapper > main {{
        padding: 0.85rem;
      }}
    }}
  </style>
</head>
<body>
  <main class="page-shell">
    <header class="hero">
      <div>
        <p class="mb-3 font-mono text-sm font-semibold uppercase tracking-[0.18em] text-teal-300">ViewSpec Demo</p>
        <h1 class="sr-only">Style Derivation</h1>
        <div class="pretext-canvas-wrap max-w-3xl">
          <canvas data-pretext-canvas data-text="Style Derivation" data-size="50" data-weight="900" data-line-height="54" class="text-white" role="img" aria-label="Style Derivation">Style Derivation</canvas>
        </div>
        <p class="sr-only">Same structure. Different feel. Toggle style presets to see the derivation function in action.</p>
        <div class="pretext-canvas-wrap mt-4 max-w-3xl">
          <canvas data-pretext-canvas data-text="Same structure. Different feel. Toggle style presets to see the derivation function in action." data-size="18" data-weight="400" data-line-height="29" class="text-slate-300" role="img" aria-label="Same structure. Different feel. Toggle style presets to see the derivation function in action.">Same structure. Different feel. Toggle style presets to see the derivation function in action.</canvas>
        </div>
      </div>
      <div class="hero-meta">
        <div><span>compiler</span> = reference</div>
        <div><span>semantic nodes</span> = {stats["semanticNodeCount"]}</div>
        <div><span>bindings</span> = {stats["bindingCount"]}</div>
        <div><span>ir nodes</span> = {stats["irNodeCount"]}</div>
      </div>
    </header>

    <section class="preset-grid" aria-label="Choose style preset">
{preset_buttons()}
    </section>

    <section class="artifact-card" aria-label="Rendered dashboard artifact">
      <div id="artifact-wrapper" data-style-preset="default">
        {fragment}
      </div>
    </section>

    <div class="dogfood-badge">Style tokens changed. Structure unchanged. This is the derivation function.</div>

    <details id="tokens-panel" class="tokens-panel">
      <summary id="tokens-summary">Show style tokens</summary>
      <pre><code id="style-token-json"></code></pre>
    </details>
  </main>

  <script>
    const STYLE_PRESETS = {presets_json};
    const STYLE_DERIVATION_STATS = {stats_json};
    const wrapper = document.getElementById('artifact-wrapper');
    const tokenJson = document.getElementById('style-token-json');
    const tokenPanel = document.getElementById('tokens-panel');
    const tokenSummary = document.getElementById('tokens-summary');
    const presetButtons = Array.from(document.querySelectorAll('[data-preset]'));

    function activatePreset(key) {{
      const preset = STYLE_PRESETS[key];
      if (!preset) return;
      wrapper.dataset.stylePreset = key;
      presetButtons.forEach((button) => {{
        const active = button.dataset.preset === key;
        button.classList.toggle('active', active);
        button.setAttribute('aria-pressed', String(active));
      }});
      tokenJson.textContent = JSON.stringify(preset, null, 2);
      window.ViewSpecPretext?.refresh(document.body);
    }}

    presetButtons.forEach((button) => {{
      button.addEventListener('click', () => activatePreset(button.dataset.preset));
    }});

    tokenPanel.addEventListener('toggle', () => {{
      tokenSummary.textContent = tokenPanel.open ? 'Hide style tokens' : 'Show style tokens';
      window.ViewSpecPretext?.refresh(tokenPanel);
    }});

    activatePreset('default');
    window.__viewspecStyleDerivation = {{
      STYLE_PRESETS,
      STYLE_DERIVATION_STATS,
    }};
  </script>
  {ACTION_EVENT_SCRIPT}
</body>
</html>
"""


def main() -> None:
    fragment, stats = compile_demo()
    output_dir = ROOT / "demos" / "style-derivation"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "index.html"
    output_path.write_text(build_page(fragment, stats), encoding="utf-8")
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
