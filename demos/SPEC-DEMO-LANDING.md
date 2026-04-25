# Demo Site: Landing Page

## Purpose
The landing page for the ViewSpec demo site. Card grid linking to individual demos. Style: minimal, confident, Pretext-inspired.

## URL Structure
```
/                           → Landing page (card grid)
/motif-switcher/            → Same Data, Three Motifs
/provenance-inspector/      → Provenance Inspector
/live-builder/              → Live Builder
/invariants/                → The Invariants
/fifteen-lines/             → 15 Lines → Full UI
```

Deploy to GitHub Pages from `demos/` directory.

## Design

### Header
```
ViewSpec
Universal UI from semantic data.

Describe what your data means.
The compiler figures out how it looks.
```

Centered. Large title (text-5xl, font-black). Subtitle in muted tone. No logo yet — the name is the brand.

Below the subtitle, a single line in monospace: `pip install viewspec`

### Card Grid
Five cards in a responsive grid (3 columns on desktop, 2 on tablet, 1 on mobile). Each card:

```
┌──────────────────────────────────┐
│                                  │
│  Same Data, Three Motifs         │
│                                  │
│  One dataset renders as table,   │
│  dashboard, or comparison.       │
│  Change one parameter. The       │
│  visual structure transforms.    │
│                                  │
└──────────────────────────────────┘
```

Card styling:
- `bg-white` with subtle border and shadow
- Rounded corners (rounded-2xl)
- On hover: slight lift (translate-y -2px) + shadow increase + teal-500 top border appears
- Title: font-bold, text-lg
- Description: text-slate-600, text-sm, 2-3 lines max
- Entire card is a link

### Cards (in order)

1. **Same Data, Three Motifs**
   Table → Dashboard → Comparison. Same data, different motif. One parameter changes the entire visual structure.

2. **Provenance Inspector**
   Hover any element. See the full chain: DOM → IR → binding → address → raw data. Every pixel has a birth certificate.

3. **Live Builder**
   Browse ViewSpec JSON, see the IR tree, watch the rendered output. Three panels, always in sync.

4. **The Invariants**
   Exactly-once provenance. Semantic grouping. Strict ordering. Watch the compiler enforce — and refuse — each one.

5. **15 Lines → Full UI**
   Watch an invoice table build itself from 15 lines of Python. No components. No CSS. No layout code.

### Footer
```
ViewSpec is open source. MIT License.
GitHub → https://github.com/nxrobins/viewspec

The compiler is a hosted service.
The SDK is free. The invariants are mathematical.
```

Centered, muted, minimal.

### Styling
- Full page: `bg-slate-50 text-slate-950`
- Max width: `max-w-5xl mx-auto`
- Tailwind CDN
- Font: system font stack (no custom fonts for v1)
- Color accent: teal-600 / teal-700 for interactive elements
- Dark mode: not for v1 (keep it simple)

## Implementation
Single `demos/index.html` file. Pure HTML + Tailwind. No build step. No JS (landing page is static).

## Quality Bar
- Must feel like a product page, not a docs page
- Cards must invite clicking
- The page should take <2 seconds to understand what ViewSpec is
- `pip install viewspec` must be visible without scrolling
- Works perfectly on mobile
