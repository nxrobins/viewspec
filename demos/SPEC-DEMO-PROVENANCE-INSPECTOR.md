# Demo: Provenance Inspector

## What It Proves
Every rendered element traces back to its exact semantic source. Nothing is unaddressed. Nothing is hallucinated. The UI is an audit trail.

## Behavior

A rendered dashboard (4 KPI cards + a 5-row table below). On hover over any element, a provenance panel appears showing the chain:

```
DOM Element  →  IR Node  →  Binding  →  Address  →  Semantic Node  →  Raw Value
```

Clicking an element locks the panel. Clicking elsewhere unlocks. The hovered element gets a glowing teal border. Connected chain elements highlight in the panel.

## Implementation

### Pre-generation
Write a Python script (`demos/build_provenance_inspector.py`) that:
1. Builds a ViewSpec with a dashboard (4 KPIs) + table (5 rows) using `ViewSpecBuilder`
2. Hand-builds a representative `CompilerResult` IR tree
3. Emits via `HtmlTailwindEmitter` — this already produces `data-ir-id`, `data-content-refs`, `data-intent-refs` on every element
4. Generates a provenance manifest JSON (the emitter already does this)
5. Wraps in `index.html` with inspector JS

### Data
```python
# KPI cards
kpis = [
    {"label": "Revenue", "value": "$2.4M"},
    {"label": "Active Users", "value": "18,472"},
    {"label": "Conversion", "value": "3.8%"},
    {"label": "Churn", "value": "1.2%"},
]

# Table rows
rows = [
    {"label": "Enterprise", "value": "$1.8M", "growth": "+22%"},
    {"label": "Mid-Market", "value": "$420K", "growth": "+15%"},
    {"label": "SMB", "value": "$180K", "growth": "+8%"},
    {"label": "Self-Serve", "value": "$45K", "growth": "+31%"},
    {"label": "Partner", "value": "$12K", "growth": "-3%"},
]
```

### Inspector Panel
Fixed-position panel, slides in from right (320px wide). Shows:

```
┌─────────────────────────────┐
│ PROVENANCE CHAIN            │
│                             │
│ DOM Element                 │
│   #dom-kpis_card_1_value    │
│   primitive: value          │
│                             │
│ IR Node                     │
│   id: kpis_card_1_value     │
│   primitive: value          │
│   props: { text: "$2.4M" }  │
│                             │
│ Binding                     │
│   id: rev_value             │
│   present_as: value         │
│                             │
│ Address                     │
│   node:revenue#attr:value   │
│                             │
│ Semantic Node               │
│   id: revenue               │
│   kind: dashboard_card      │
│   attrs: {                  │
│     label: "Revenue",       │
│     value: "$2.4M"          │
│   }                         │
│                             │
│ ✓ Provenance verified       │
│   1 content ref             │
│   1 intent ref              │
└─────────────────────────────┘
```

### Styling
- Dark sidebar panel with monospace font for data
- Teal glow border on hovered element (box-shadow, not border to avoid layout shift)
- Smooth slide-in animation (transform: translateX)
- Chain items connected with a thin vertical line (pseudo-element)
- Each chain level slightly indented

### JS (~60 lines)
- Load provenance manifest from inline JSON (embedded in page)
- On mouseover of any `[data-ir-id]` element: read data attributes, look up in manifest, populate panel
- On click: toggle lock state
- On click outside: unlock

## Output
`demos/provenance-inspector/index.html` — single self-contained HTML file.

## Quality Bar
- Panel must feel like a devtools overlay, not a tooltip
- Chain must be visually connected (not just a list)
- Glow effect must be subtle and beautiful
- Every element with `data-ir-id` must be hoverable
- The manifest data must be real (match the rendered elements exactly)
