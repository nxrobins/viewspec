# Demo 06: Style Derivation

## Concept

Same data. Same motif. Same structure. Different feel.

Toggle between visual treatments to see the derivation function's style layer in action. The compiler's structural decisions don't change — only the style tokens do. This is the "feel" knob.

## What the visitor sees

A dashboard motif (the KPI dataset from the provenance inspector demo — Revenue, Active Users, Conversion, Churn). Four style presets selectable via buttons:

| Preset | Feel | Key tokens |
|--------|------|------------|
| **Default** | Clean, neutral, the reference compiler baseline | White bg, slate text, minimal borders |
| **Editorial** | Magazine-dense, high contrast, tight spacing | Near-black bg, cream text, serif headings, hairline rules |
| **Data-Dense** | Bloomberg terminal energy, maximum information | Dark bg, mono font, green/amber accent, compact padding, visible grid lines |
| **Soft** | Consumer app, lots of air, rounded everything | Light warm bg, large border-radius, pastel accents, generous padding |

## Layout

```
┌─────────────────────────────────────────────────┐
│ [nav bar - injected by shared/nav.js]           │
├─────────────────────────────────────────────────┤
│ ViewSpec Demo                                   │
│ Style Derivation (Pretext canvas, 50px)         │
│ Same structure. Different feel. (18px)          │
│                                                 │
│ [ Default ] [ Editorial ] [ Data-Dense ] [ Soft ]│
│                                                 │
│ ┌─────────────────────────────────────────────┐ │
│ │  Rendered dashboard — style changes here    │ │
│ │  KPI cards: Revenue, Users, Conversion,     │ │
│ │  Churn. Same data, same motif, same IR.     │ │
│ └─────────────────────────────────────────────┘ │
│                                                 │
│ ⬡ Style tokens changed. Structure unchanged.    │
│                                                 │
│ ┌─ Style Tokens (collapsible) ────────────────┐ │
│ │ JSON showing the active derive_style_tokens  │ │
│ │ output — updates live on toggle              │ │
│ └─────────────────────────────────────────────┘ │
│                                                 │
│ ⬡ Compiled by ViewSpec — dashboard motif        │
└─────────────────────────────────────────────────┘
```

## Data

Reuse the KPI substrate from provenance inspector:

```python
nodes = [
    {"id": "kpi_revenue",    "label": "Revenue",      "value": "$2.4M"},
    {"id": "kpi_users",      "label": "Active Users",  "value": "18,472"},
    {"id": "kpi_conversion", "label": "Conversion",    "value": "3.8%"},
    {"id": "kpi_churn",      "label": "Churn",         "value": "1.2%"},
]
```

## Behavior

1. Page loads with "Default" preset active.
2. Clicking a preset button:
   - Swaps CSS custom properties / Tailwind classes on the rendered artifact
   - Updates the style tokens JSON display
   - Button gets active state (teal highlight)
   - Transition: 300ms ease on all color/spacing/font changes
3. The IR structure (what primitives, what order) stays identical across all presets — only the style tokens change.
4. Style tokens panel toggleable (collapsed by default, "Show style tokens ▸" link).

## Style presets (CSS custom properties)

Each preset defines these tokens at minimum:
- `--bg`, `--card-bg`, `--text-primary`, `--text-secondary`, `--accent`
- `--border-color`, `--border-radius`, `--card-padding`
- `--font-heading`, `--font-body`, `--font-mono`
- `--label-size`, `--value-size`, `--label-weight`, `--value-weight`
- `--label-tracking`, `--label-transform`

## Technical

- Static HTML page like the other demos
- Generator: `build_style_derivation.py` (compiles the dashboard, injects 4 preset stylesheets)
- OR: pure hand-built HTML is fine too — the point is the visual demonstration
- Uses Pretext canvas for title/subtitle (consistent with other demos)
- Include `<script src="../shared/nav.js" defer></script>` in `<head>`
- Include `<script type="module" src="../shared/pretext-canvas-surfaces.js"></script>` in `<head>`
- Dark page chrome matching other demos (`#06080b` bg, teal accents)
- The rendered dashboard artifact sits in a container that transitions between presets

## Dogfood badge

"Style tokens changed. Structure unchanged. This is the derivation function."

## What this proves

The compiler separates *routing* (which motif, which primitives, what order) from *derivation* (how it feels). You can swap the entire visual treatment without touching the semantic description or recompiling. Structure is compiled. Style is derived.
