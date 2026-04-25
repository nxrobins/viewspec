"""Build the 15 Lines to Full UI demo page from the reference compiler."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

from viewspec import IntentBundle, ViewSpecBuilder, compile
from viewspec.emitters.html_tailwind import ACTION_EVENT_SCRIPT, _render_node
from viewspec.types import ASTBundle, DEFAULT_STYLE_TOKEN_VALUES

ROOT = Path(__file__).resolve().parents[1]

ROWS = [
    ("Design System Audit", "$4,200"),
    ("Component Library", "$8,500"),
    ("API Integration", "$3,100"),
    ("QA & Testing", "$2,800"),
    ("Documentation", "$1,400"),
]

CODE_LINES = [
    {"text": "from viewspec import ViewSpecBuilder", "outputStep": None, "final": False},
    {"text": "", "outputStep": None, "final": False},
    {"text": 'builder = ViewSpecBuilder("invoice")', "outputStep": 0, "final": False},
    {"text": "", "outputStep": None, "final": False},
    {"text": 'table = builder.add_table("items", region="main", group_id="rows")', "outputStep": 1, "final": False},
    {"text": 'table.add_row(label="Design System Audit", value="$4,200")', "outputStep": 2, "final": False},
    {"text": 'table.add_row(label="Component Library", value="$8,500")', "outputStep": 3, "final": False},
    {"text": 'table.add_row(label="API Integration", value="$3,100")', "outputStep": 4, "final": False},
    {"text": 'table.add_row(label="QA & Testing", value="$2,800")', "outputStep": 5, "final": False},
    {"text": 'table.add_row(label="Documentation", value="$1,400")', "outputStep": 6, "final": False},
    {"text": "", "outputStep": None, "final": False},
    {"text": 'builder.add_style("s1", "items_row_5_label", "tone.muted")', "outputStep": None, "final": False},
    {"text": 'builder.add_style("s2", "items_row_5_value", "emphasis.high")', "outputStep": 7, "final": False},
    {"text": "", "outputStep": None, "final": False},
    {"text": "bundle = builder.build_bundle()", "outputStep": 8, "final": True},
]


def render_fragment(ast: ASTBundle, namespace: str) -> str:
    """Render one compiled AST to an embeddable artifact fragment."""
    manifest: dict[str, Any] = {}
    style_values = dict(ast.style_values or DEFAULT_STYLE_TOKEN_VALUES)
    rendered = _render_node(ast.result.root.root, manifest, style_values)
    return rendered.replace('id="dom-', f'id="{namespace}-dom-')


def compile_fragment(builder: ViewSpecBuilder, namespace: str) -> tuple[str, IntentBundle]:
    bundle = builder.build_bundle()
    ast = compile(bundle)
    if ast.result.diagnostics:
        diagnostics = [diagnostic.to_json() for diagnostic in ast.result.diagnostics]
        raise RuntimeError(f"{namespace} compile produced diagnostics: {json.dumps(diagnostics, indent=2)}")
    return render_fragment(ast, namespace), bundle


def compile_fragments() -> tuple[list[str], dict[str, int]]:
    builder = ViewSpecBuilder("invoice")
    fragments: list[str] = []

    fragment, bundle = compile_fragment(builder, "step-0-empty")
    fragments.append(fragment)

    table = builder.add_table("items", region="main", group_id="rows")
    fragment, bundle = compile_fragment(builder, "step-1-table")
    fragments.append(fragment)

    for index, (label, value) in enumerate(ROWS, start=1):
        table.add_row(label=label, value=value)
        fragment, bundle = compile_fragment(builder, f"step-{index + 1}-row-{index}")
        fragments.append(fragment)

    builder.add_style("s1", "items_row_5_label", "tone.muted")
    builder.add_style("s2", "items_row_5_value", "emphasis.high")
    fragment, bundle = compile_fragment(builder, "step-7-styled")
    fragments.append(fragment)

    fragment, bundle = compile_fragment(builder, "step-8-final")
    fragments.append(fragment)

    stats = {
        "bindingCount": len(bundle.view_spec.bindings),
        "semanticNodeCount": len(bundle.substrate.nodes),
    }
    if len(fragments) != 9:
        raise RuntimeError(f"expected 9 fragments, built {len(fragments)}")
    return fragments, stats


def span(class_name: str, value: str) -> str:
    return f'<span class="{class_name}">{value}</span>'


def highlight_line(line: str) -> str:
    escaped = html.escape(line)
    escaped = re.sub(r"(&quot;.*?&quot;)", lambda match: span("syntax-string", match.group(1)), escaped)
    escaped = re.sub(r"\b(from|import)\b", lambda match: span("syntax-keyword", match.group(1)), escaped)
    escaped = re.sub(r"\b(ViewSpecBuilder)\b", lambda match: span("syntax-class", match.group(1)), escaped)
    escaped = re.sub(
        r"\.(add_table|add_row|add_style|build_bundle)\b",
        lambda match: f'.{span("syntax-method", match.group(1))}',
        escaped,
    )
    return escaped


def code_line_data() -> list[dict[str, object]]:
    if len(CODE_LINES) != 15:
        raise RuntimeError(f"expected 15 code lines, found {len(CODE_LINES)}")
    return [
        {
            "text": line["text"],
            "html": highlight_line(str(line["text"])),
            "outputStep": line["outputStep"],
            "final": line["final"],
        }
        for line in CODE_LINES
    ]


def safe_json_for_script(value: Any) -> str:
    """Serialize JSON for direct assignment inside a script tag."""
    return json.dumps(value, indent=2, sort_keys=True).replace("</script>", "<\\/script>")


def build_page(fragments: list[str], lines: list[dict[str, object]], stats: dict[str, int]) -> str:
    fragments_json = safe_json_for_script(fragments)
    lines_json = safe_json_for_script(lines)
    stats_json = safe_json_for_script(stats)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ViewSpec Demo - 15 Lines to Full UI</title>
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

    .app-shell {{
      display: grid;
      gap: 1rem;
      min-height: 100vh;
      padding: 1rem;
    }}

    .panel {{
      border: 1px solid rgba(148, 163, 184, 0.24);
      border-radius: 8px;
      overflow: hidden;
    }}

    .panel-header {{
      align-items: center;
      border-bottom: 1px solid rgba(148, 163, 184, 0.18);
      display: flex;
      gap: 0.75rem;
      justify-content: space-between;
      min-height: 3.25rem;
      padding: 0.75rem 1rem;
    }}

    .code-panel {{
      background: #0f172a;
      min-width: 0;
    }}

    .output-panel {{
      background: #f8fafc;
      color: #0f172a;
      min-width: 0;
    }}

    .code-body {{
      height: 32rem;
      overflow: auto;
      padding: 1rem 0.75rem 1.25rem;
    }}

    .code-line {{
      display: grid;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      font-size: 0.86rem;
      grid-template-columns: 2.25rem minmax(0, 1fr);
      line-height: 1.8;
      min-height: 1.55rem;
      white-space: pre;
    }}

    .line-number {{
      color: #64748b;
      padding-right: 0.75rem;
      text-align: right;
      user-select: none;
    }}

    .code-text {{
      color: #e2e8f0;
      overflow-x: visible;
    }}

    .syntax-keyword {{
      color: #60a5fa;
    }}

    .syntax-string {{
      color: #86efac;
    }}

    .syntax-class {{
      color: #c4b5fd;
    }}

    .syntax-method {{
      color: #5eead4;
    }}

    .cursor {{
      animation: blink 900ms steps(2, start) infinite;
      background: #5eead4;
      display: inline-block;
      height: 1.05em;
      margin-left: 0.08rem;
      transform: translateY(0.15em);
      width: 0.55rem;
    }}

    .output-body {{
      background: #f8fafc;
      display: grid;
      min-height: 32rem;
      overflow: auto;
      position: relative;
    }}

    .output-stage > main {{
      min-height: 0;
      padding: 1.25rem;
    }}

    [data-ir-id^="motif_items"]:empty {{
      border: 2px dashed #e2e8f0;
      border-radius: 0.75rem;
      min-height: 3rem;
    }}

    .ready-badge {{
      align-items: center;
      background: #047857;
      border: 1px solid #34d399;
      border-radius: 999px;
      bottom: 1rem;
      box-shadow: 0 18px 45px rgba(4, 120, 87, 0.24);
      color: #ecfdf5;
      display: flex;
      font-size: 0.78rem;
      font-weight: 900;
      gap: 0.55rem;
      left: 1rem;
      opacity: 0;
      padding: 0.65rem 0.9rem;
      pointer-events: none;
      position: absolute;
      transform: translateY(0.8rem);
      transition: opacity 220ms ease, transform 220ms ease;
      z-index: 20;
    }}

    .ready-badge::before {{
      content: "\\2713";
      font-size: 0.9rem;
    }}

    .ready-badge.visible {{
      opacity: 1;
      transform: translateY(0);
    }}

    .pill {{
      border-radius: 999px;
      border: 1px solid rgba(148, 163, 184, 0.24);
      color: #cbd5e1;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      font-size: 0.72rem;
      font-weight: 800;
      padding: 0.3rem 0.55rem;
      text-transform: uppercase;
    }}

    .replay-button {{
      border: 1px solid rgba(45, 212, 191, 0.45);
      border-radius: 999px;
      color: #ccfbf1;
      font-size: 0.8rem;
      font-weight: 900;
      padding: 0.45rem 0.75rem;
      transition: background 160ms ease, border-color 160ms ease, transform 160ms ease;
    }}

    .replay-button:hover {{
      background: rgba(20, 184, 166, 0.12);
      border-color: #5eead4;
      transform: translateY(-1px);
    }}

    .step-meter {{
      color: #475569;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      font-size: 0.78rem;
      font-weight: 800;
    }}

    @keyframes blink {{
      0%, 45% {{
        opacity: 1;
      }}
      46%, 100% {{
        opacity: 0;
      }}
    }}

    @media (min-width: 1024px) {{
      .app-shell {{
        grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      }}

      .code-body,
      .output-body {{
        height: calc(100vh - 5.25rem);
        min-height: 36rem;
      }}
    }}

    @media (max-width: 1023px) {{
      .app-shell {{
        min-height: 0;
      }}

      .code-body,
      .output-body {{
        height: 30rem;
      }}
    }}

    @media (max-width: 520px) {{
      .app-shell {{
        padding: 0.65rem;
      }}

      .code-line {{
        font-size: 0.74rem;
        grid-template-columns: 1.85rem minmax(0, 1fr);
      }}

      .panel-header {{
        align-items: flex-start;
        flex-direction: column;
      }}

      .ready-badge {{
        align-items: flex-start;
        border-radius: 8px;
        flex-direction: column;
        right: 1rem;
      }}
    }}
  </style>
</head>
<body>
  <main class="app-shell">
    <section class="panel code-panel" aria-label="Python source">
      <header class="panel-header">
        <div>
          <div class="pretext-canvas-wrap w-40">
            <canvas data-pretext-canvas data-text="invoice.py" data-size="14" data-weight="900" data-line-height="18" class="text-white" role="img" aria-label="invoice.py">invoice.py</canvas>
          </div>
          <div class="pretext-canvas-wrap mt-1 w-56">
            <canvas data-pretext-canvas data-text="15 lines of semantic intent" data-size="12" data-weight="600" data-line-height="16" class="text-slate-400" role="img" aria-label="15 lines of semantic intent">15 lines of semantic intent</canvas>
          </div>
        </div>
        <div class="flex items-center gap-2">
          <span class="pill">Python</span>
          <button id="replay-button" type="button" class="replay-button">Replay</button>
        </div>
      </header>
      <div class="code-body">
        <pre class="m-0"><code id="code-display" aria-live="polite"></code></pre>
      </div>
    </section>

    <section class="panel output-panel" aria-label="Rendered output">
      <header class="panel-header bg-white">
        <div>
          <div class="pretext-canvas-wrap w-44">
            <canvas data-pretext-canvas data-text="Rendered Output" data-size="14" data-weight="900" data-line-height="18" class="text-slate-950" role="img" aria-label="Rendered Output">Rendered Output</canvas>
          </div>
          <div class="pretext-canvas-wrap mt-1 w-56">
            <canvas data-pretext-canvas data-text="Reference compiler artifact" data-size="12" data-weight="600" data-line-height="16" class="text-slate-500" role="img" aria-label="Reference compiler artifact">Reference compiler artifact</canvas>
          </div>
        </div>
        <div class="step-meter">step <span id="step-count">1</span>/9</div>
      </header>
      <div class="output-body">
        <div id="output-display" class="output-stage"></div>
        <div id="ready-badge" class="ready-badge">
          <span class="pretext-canvas-wrap w-24">
            <canvas data-pretext-canvas data-text="Bundle Ready" data-size="12" data-weight="900" data-line-height="16" class="text-emerald-50" role="img" aria-label="Bundle Ready">Bundle Ready</canvas>
          </span>
          <span class="pretext-canvas-wrap w-72 max-w-full">
            <canvas id="ready-stats" data-pretext-canvas data-text="" data-size="12" data-weight="800" data-line-height="16" class="text-emerald-50" role="img" aria-label=""></canvas>
          </span>
        </div>
      </div>
    </section>
  </main>

  <script>
    const FRAGMENTS = {fragments_json};
    const CODE_LINES = {lines_json};
    const FINAL_STATS = {stats_json};

    const codeDisplay = document.getElementById('code-display');
    const outputDisplay = document.getElementById('output-display');
    const replayButton = document.getElementById('replay-button');
    const readyBadge = document.getElementById('ready-badge');
    const readyStats = document.getElementById('ready-stats');
    const stepCount = document.getElementById('step-count');

    let lineIndex = 0;
    let charIndex = 0;
    let timer = null;
    let lineNodes = [];

    function escapeHtml(value) {{
      return String(value)
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
    }}

    function setOutput(step) {{
      outputDisplay.innerHTML = FRAGMENTS[step] || '';
      stepCount.textContent = String(step + 1);
      window.ViewSpecPretext?.refresh(outputDisplay);
    }}

    function clearTimer() {{
      if (timer !== null) {{
        window.clearTimeout(timer);
        timer = null;
      }}
    }}

    function schedule(callback, delay) {{
      clearTimer();
      timer = window.setTimeout(callback, delay);
    }}

    function renderCodeRows() {{
      codeDisplay.innerHTML = CODE_LINES.map((line, index) => `
        <div class="code-line" data-line="${{index + 1}}">
          <span class="line-number">${{String(index + 1).padStart(2, '0')}}</span>
          <span class="code-text"></span>
        </div>
      `).join('');
      lineNodes = Array.from(codeDisplay.querySelectorAll('.code-text'));
    }}

    function setCanvasText(canvas, text) {{
      canvas.dataset.text = text;
      canvas.setAttribute('aria-label', text);
      canvas.textContent = text;
    }}

    function showReadyBadge() {{
      setCanvasText(readyStats, `${{FINAL_STATS.bindingCount}} bindings | ${{FINAL_STATS.semanticNodeCount}} nodes | full provenance`);
      readyBadge.classList.add('visible');
      window.ViewSpecPretext?.refresh(readyBadge);
    }}

    function typeNext() {{
      if (lineIndex >= CODE_LINES.length) return;

      const line = CODE_LINES[lineIndex];
      const lineNode = lineNodes[lineIndex];
      if (!lineNode) return;

      if (charIndex < line.text.length) {{
        charIndex += 1;
        lineNode.innerHTML = `${{escapeHtml(line.text.slice(0, charIndex))}}<span class="cursor"></span>`;
        const charDelay = 24 + ((lineIndex * 11 + charIndex * 17) % 26);
        schedule(typeNext, charDelay);
        return;
      }}

      lineNode.innerHTML = `${{line.html}}<span class="cursor"></span>`;
      if (Number.isInteger(line.outputStep)) {{
        setOutput(line.outputStep);
      }}
      if (line.final) {{
        showReadyBadge();
      }}

      const pause = line.text.length ? 390 : 230;
      schedule(() => {{
        lineNode.innerHTML = line.html;
        lineIndex += 1;
        charIndex = 0;
        if (lineIndex < CODE_LINES.length) {{
          lineNodes[lineIndex].innerHTML = '<span class="cursor"></span>';
          typeNext();
        }}
      }}, pause);
    }}

    function startDemo() {{
      clearTimer();
      lineIndex = 0;
      charIndex = 0;
      renderCodeRows();
      setOutput(0);
      readyBadge.classList.remove('visible');
      setCanvasText(readyStats, '');
      window.ViewSpecPretext?.refresh(document.body);
      schedule(typeNext, 250);
    }}

    replayButton.addEventListener('click', startDemo);

    window.__viewspecFifteenLines = {{
      codeLines: CODE_LINES,
      fragments: FRAGMENTS,
      replay: startDemo,
    }};

    startDemo();
  </script>
  {ACTION_EVENT_SCRIPT}
</body>
</html>
"""


def main() -> None:
    fragments, stats = compile_fragments()
    lines = code_line_data()
    output_dir = ROOT / "demos" / "fifteen-lines"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "index.html"
    output_path.write_text(build_page(fragments, lines, stats), encoding="utf-8")
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
