# Demo: Live Builder

## What It Proves
ViewSpec is a protocol, not a black box. You can see the semantic input, the IR structure, and the rendered output simultaneously. The relationship between intent and output is transparent and inspectable.

## Behavior

Three-panel layout (horizontal on desktop, stacked on mobile):

```
┌──────────────────┬──────────────────┬──────────────────┐
│                  │                  │                  │
│   VIEWSPEC       │   COMPOSITION    │   RENDERED       │
│   (JSON input)   │   IR (tree)      │   (HTML output)  │
│                  │                  │                  │
│   Editable       │   Read-only      │   Live preview   │
│   textarea       │   tree view      │                  │
│                  │                  │                  │
└──────────────────┴──────────────────┴──────────────────┘
```

**Left panel:** JSON textarea pre-loaded with an example IntentBundle. Editable. Dropdown above to switch between presets (Invoice, Dashboard, Team Roster, Comparison).

**Middle panel:** A collapsible tree visualization of the CompositionIR. Each node shows: `primitive`, `id`, provenance refs. Color-coded by primitive type (containers = slate, content = teal, layout = blue).

**Right panel:** The rendered HTML output, live. Updates when the left panel changes.

**The flow:** User edits JSON (or picks a preset) → IR tree updates → rendered output updates. The three panels are always in sync.

### Important Constraint
Since the compiler is private, this demo uses pre-built IR trees for each preset. The JSON editor is read-only in v1 (users can browse presets but not free-edit). The middle and right panels update when presets change. Future versions will hit the hosted compiler API for live editing.

## Implementation

### Pre-generation
Write a Python script (`demos/build_live_builder.py`) that:
1. Builds 4 IntentBundles using `ViewSpecBuilder` (invoice, dashboard, team roster, comparison)
2. Hand-builds representative `CompilerResult` IR trees for each
3. Emits HTML fragments via `HtmlTailwindEmitter` for the right panel
4. Serializes the IntentBundle JSON for the left panel
5. Serializes the IR tree as nested JSON for the middle panel
6. Packages everything into a single `index.html`

### Presets
1. **Invoice** — table motif, 5 line items
2. **Sales Dashboard** — dashboard motif, 4 KPI cards
3. **Team Roster** — table motif, 5 team members with multiple columns
4. **Pricing Comparison** — comparison motif, 3 tiers

### IR Tree Visualization
Recursive tree using nested `<details>` elements (HTML native, no JS framework needed):

```html
<details open>
  <summary>
    <span class="primitive root">root</span>
    <span class="node-id">root_0</span>
  </summary>
  <details open>
    <summary>
      <span class="primitive surface">surface</span>
      <span class="node-id">card_1</span>
      <span class="refs">← node:rev#attr:value</span>
    </summary>
    <details>
      <summary>
        <span class="primitive label">label</span>
        <span class="node-id">label_1</span>
      </summary>
    </details>
    <details>
      <summary>
        <span class="primitive value">value</span>
        <span class="node-id">value_1</span>
      </summary>
    </details>
  </details>
</details>
```

### Styling
- Three equal panels with thin divider borders
- Left panel: dark background, monospace font, syntax-highlighted JSON (simple regex-based highlighting, no library)
- Middle panel: tree with indentation guides (border-left on nested levels)
- Right panel: light background (the normal emitter output)
- Preset dropdown: pill-style selector above the left panel
- Color coding for primitives: container=slate-400, content=teal-400, layout=blue-400

### JS (~40 lines)
- Preset dropdown changes all three panels simultaneously
- All preset data embedded as inline JSON objects
- No external API calls

## Output
`demos/live-builder/index.html` — single self-contained HTML file.

## Quality Bar
- Must feel like a playground/IDE, not a documentation page
- JSON must be properly formatted and readable
- IR tree must be navigable (expand/collapse nodes)
- Rendered output must look identical to what the real emitter produces
- Responsive: panels stack vertically on mobile
