# Demo: Same Data, Three Motifs

## What It Proves
A single dataset renders as three completely different visual structures by changing one parameter. The data is constant. The motif is the variable. Layout is structural, not manual.

## Behavior

Single page. Three toggle buttons at top: **Table** | **Dashboard** | **Comparison** (pill-style, one active).

Below: a rendered ViewSpec output that transforms when the toggle changes.

**The dataset:** A team roster with 5 people. Each person has `name`, `role`, `location`, `status`.

- **Table view:** Rows with label-value pairs. Dense, scannable.
- **Dashboard view:** Cards with name as label, role as value. Spatial, glanceable.
- **Comparison view:** Side-by-side panels. Evaluative, comparative.

**The transition:** When the user clicks a toggle, the rendered output cross-fades (200ms opacity transition). No page reload. The HTML is pre-generated for all three motifs and swapped via JS.

## Implementation

### Data
```python
team = [
    {"name": "Alice Chen", "role": "Principal Engineer", "location": "SF", "status": "Active"},
    {"name": "Bob Kowalski", "role": "Design Lead", "location": "NYC", "status": "Active"},
    {"name": "Cara Oduya", "role": "ML Researcher", "location": "London", "status": "On Leave"},
    {"name": "David Park", "role": "Product Manager", "location": "Seoul", "status": "Active"},
    {"name": "Elena Vasquez", "role": "DevRel", "location": "Austin", "status": "Active"},
]
```

### Pre-generation
Write a Python script (`demos/build_motif_switcher.py`) that:
1. Imports `ViewSpecBuilder` from `viewspec`
2. Builds three `IntentBundle` JSONs from the same data — one per motif (table, dashboard, comparison)
3. For each, manually constructs a `CompilerResult` with the correct IR tree structure (since the compiler is private, hand-build representative IR trees that show what each motif produces)
4. Feeds each through `HtmlTailwindEmitter` to produce three HTML fragments
5. Wraps them in a single `index.html` with the toggle JS

### HTML Structure
```html
<div class="demo-header">
  <h1>Same Data, Three Motifs</h1>
  <p>One dataset. Three visual structures. The data doesn't change — the motif does.</p>
  <div class="toggle-group">
    <button data-motif="table" class="active">Table</button>
    <button data-motif="dashboard">Dashboard</button>
    <button data-motif="comparison">Comparison</button>
  </div>
</div>

<div class="demo-body">
  <div id="view-table" class="motif-view active"><!-- emitted table HTML --></div>
  <div id="view-dashboard" class="motif-view"><!-- emitted dashboard HTML --></div>
  <div id="view-comparison" class="motif-view"><!-- emitted comparison HTML --></div>
</div>

<div class="demo-footer">
  <p>The ViewSpec substrate is identical across all three views. Only the motif hint changed.</p>
  <pre><code>motif.kind = "table"  →  motif.kind = "dashboard"  →  motif.kind = "comparison"</code></pre>
</div>
```

### Styling
- Use Tailwind CDN
- Dark background (`bg-slate-900`) with light content cards
- Toggle buttons: pill group, teal active state
- Cross-fade transition on motif swap
- Mobile-responsive (stack vertically below 640px)

### JS (~20 lines)
```js
document.querySelectorAll('[data-motif]').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('[data-motif]').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.motif-view').forEach(v => v.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById(`view-${btn.dataset.motif}`).classList.add('active');
  });
});
```

## Output
`demos/motif-switcher/index.html` — single self-contained HTML file, no build step.

## Quality Bar
- Looks polished enough to tweet a screenshot
- The three motifs must look genuinely different, not just slightly rearranged
- Transition must feel smooth, not janky
- Mobile must work
