# Demo: 15 Lines → Full UI

## What It Proves
The ratio between intent and output is extreme. Fifteen lines of semantic description produces a complete, styled, provenance-tracked UI. No components. No layout code. No CSS.

## Behavior

Split screen. Left: Python code. Right: rendered output.

The code appears line by line (typewriter effect, ~80ms per character, ~400ms pause between lines). As each semantically meaningful line completes, the rendered output on the right updates to include the new element. The build-up is progressive — the user watches the UI construct itself from semantic declarations.

### The Code (15 lines)
```python
from viewspec import ViewSpecBuilder

builder = ViewSpecBuilder("invoice")

table = builder.add_table("items", region="main", group_id="rows")
table.add_row(label="Design System Audit", value="$4,200")
table.add_row(label="Component Library",   value="$8,500")
table.add_row(label="API Integration",     value="$3,100")
table.add_row(label="QA & Testing",        value="$2,800")
table.add_row(label="Documentation",       value="$1,400")

builder.add_style("s1", "items_row_5_label", "tone.muted")
builder.add_style("s2", "items_row_5_value", "emphasis.high")

bundle = builder.build_bundle()
```

### Progressive Reveal Steps
Lines 1-2 (import + builder): Empty root container appears on right
Line 4 (add_table): Table structure appears (empty, with header area)
Lines 5-9 (add_row × 5): Each row appears as the line completes
Lines 11-12 (add_style): The "Documentation" row's label dims and value bolds
Line 14 (build_bundle): A "✓ Bundle Ready" badge animates in below the table, showing binding count and provenance status

After the animation completes, a "Replay" button appears.

## Implementation

### Pre-generation
Write a Python script (`demos/build_fifteen_lines.py`) that:
1. Builds the full IntentBundle
2. Hand-builds IR trees for each progressive step (empty, table shell, 1 row, 2 rows, ..., 5 rows, styled, final)
3. Emits HTML fragments for each step
4. Packages into `index.html` with typewriter JS

### HTML Structure
```html
<div class="split-screen">
  <div class="code-panel">
    <div class="panel-header">
      <span class="filename">invoice.py</span>
      <span class="lang-badge">Python</span>
    </div>
    <pre><code id="code-display"></code></pre>
  </div>
  
  <div class="output-panel">
    <div class="panel-header">
      <span class="filename">Rendered Output</span>
      <span class="live-badge">● Live</span>
    </div>
    <div id="output-display"></div>
  </div>
</div>
```

### Styling
- Code panel: dark (slate-900), monospace, syntax highlighting via CSS classes
  - Keywords (from, import): blue-400
  - Strings: green-400
  - Method calls: teal-300
  - Comments: slate-500
- Output panel: light (slate-50), the normal emitter output
- Split: 50/50 on desktop, stacked on mobile (code on top)
- Cursor: blinking block cursor at end of current line during typing
- "✓ Bundle Ready" badge: slides up, green background, shows "10 bindings | 5 nodes | full provenance"

### JS (~80 lines)
- Array of code lines with metadata: `{ text, delay, outputStep }`
- Typewriter loop: character by character with configurable speed
- On each `outputStep`, swap the output panel's innerHTML to the pre-generated fragment
- Replay button resets and restarts
- Skip button (subtle, bottom-right) jumps to final state

## Output
`demos/fifteen-lines/index.html` — single self-contained HTML file.

## Quality Bar
- The typing must feel natural (not robotic, slight variance in speed)
- Syntax highlighting must be accurate for Python
- The progressive reveal must feel like the UI is being BUILT, not just shown
- The final state must be a genuinely good-looking invoice table
- The ratio (15 lines → complete UI) must be felt, not just counted
