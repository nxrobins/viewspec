# ViewSpec

**Universal UI from semantic data.**

ViewSpec is a declarative language for describing what data means. The compiler figures out how it looks. Every pixel has a birth certificate.

🌐 **[viewspec.dev](https://viewspec.dev)** — Live hosted compiler playground, demos, and pricing

```python
from viewspec import ViewSpecBuilder, compile
from viewspec.emitters.html_tailwind import HtmlTailwindEmitter

builder = ViewSpecBuilder("invoice")
table = builder.add_table("items", region="main", group_id="rows")
table.add_row(label="Design System Audit", value="$4,200")
table.add_row(label="Component Library", value="$8,500")
table.add_row(label="API Integration", value="$3,100")

ast = compile(builder.build_bundle())
HtmlTailwindEmitter().emit(ast, "output/")

# That's it. Full UI. Full provenance. No CSS.
```

## What ViewSpec Does

**Before ViewSpec:** You manually bridge the gap between data and UI. Every component, every prop, every layout decision — hand-wired by a developer.

**After ViewSpec:** You declare what the data means. The compiler determines the visual structure. Rendering is a pluggable backend.

```
Data → ViewSpec (semantic intent) → Compiler → CompositionIR → Emitter
                                                                ├── HTML/Tailwind (shipped)
                                                                ├── Canvas/Pretext
                                                                ├── PDF
                                                                ├── Native mobile
                                                                └── Your custom emitter
```

## Three Invariants

ViewSpec enforces three mathematical guarantees:

1. **Exactly-once provenance.** Every data binding is routed exactly once. Nothing dropped. Nothing duplicated. Nothing hallucinated.

2. **Semantic grouping.** Data is grouped by meaning, not by visual adjacency.

3. **Strict ordering.** The original data order is preserved as a mathematical guarantee.

## Install

```bash
pip install viewspec
```

Requires Python 3.11+.

## Hosted Playground

The home page at [viewspec.dev](https://viewspec.dev) runs a live hosted compile against `https://api.viewspec.dev/v1/compile`. It uses anonymous free-tier requests by default and shows the request, response, measured `compile_ms`, active derivation tokens, and provenance chain.

Agent and crawler entrypoints are published with the static site:

- `https://viewspec.dev/llms.txt` — concise LLM-facing product map
- `https://viewspec.dev/llms-full.txt` — expanded AI context and canonical facts
- `https://viewspec.dev/agent-system-prompt.txt` — system prompt for agents that emit `IntentBundle` JSON
- `https://viewspec.dev/agent-intent-bundle.schema.json` — JSON schema for agent-authored compiler input
- `https://viewspec.dev/openapi.json` — hosted compiler OpenAPI description
- `https://viewspec.dev/sitemap.xml` — canonical page sitemap

Runtime landing-page config is read from `window.VIEWSPEC_LANDING_CONFIG`:

| Key | Purpose |
|-----|---------|
| `apiUrl` | Hosted compiler endpoint. Defaults to `https://api.viewspec.dev/v1/compile`. |
| `fallbackApiUrls` | Optional fallback compiler endpoints for landing-page availability during custom-domain cutovers. |
| `endpointStaggerMs` | Delay before starting fallback endpoint requests. Defaults to `120`. |
| `endpointFailureTtlMs` | How long the browser session keeps a failed endpoint out of the hot path. Defaults to `300000`. |
| `publicApiKey` | Optional browser-safe public/demo key. `window.PUBLIC_LANDING_API_KEY` is also accepted. Omit it to use anonymous free-tier demo traffic. |
| `proStripeUrl` | Pro checkout link. Defaults to the live Stripe payment link. |
| `scaleStripeUrl` | Scale checkout link. Defaults to the live Stripe payment link. |
| `signupUrl` | Free CTA or pricing URL. Defaults to `https://viewspec.dev/#pricing`. |
| `requestTimeoutMs` | Hosted compile timeout. Defaults to `6000`. |

Keep secret API keys server-side; only browser-safe public/demo keys belong in static landing-page config.

## Demos

The hosted playground plus six reference demos are available at [viewspec.dev](https://viewspec.dev):

| Demo | What it shows |
|------|--------------|
| [Same Data, Three Motifs](https://viewspec.dev/motif-switcher/) | One dataset → table, dashboard, or comparison. Change one parameter. |
| [Provenance Inspector](https://viewspec.dev/provenance-inspector/) | Hover any element. Trace DOM → IR → binding → address → raw data. |
| [Live Builder](https://viewspec.dev/live-builder/) | Browse ViewSpec JSON, IR tree, and rendered output in sync. |
| [The Invariants](https://viewspec.dev/invariants/) | Watch the compiler enforce — and refuse — each guarantee. |
| [15 Lines → Full UI](https://viewspec.dev/fifteen-lines/) | An invoice table builds itself from 15 lines of Python. |
| [Style Derivation](https://viewspec.dev/style-derivation/) | Same structure, different feel. Toggle four visual presets. |

Text rendering powered by [Pretext](https://github.com/chenglou/pretext) canvas surfaces.

## Core Concepts

### Semantic Substrate

The raw data graph. Nodes with typed attributes, slots, and edges. This is WHAT the data is — no visual intent.

```python
builder = ViewSpecBuilder("my_view")
builder.add_node("user_1", "person", attrs={"name": "Alice", "role": "Engineer"})
builder.add_node("user_2", "person", attrs={"name": "Bob", "role": "Designer"})
```

### ViewSpec

The declarative intent layer. Regions (WHERE data can go), bindings (WHICH data goes WHERE), motifs (HOW it should be structured), and styles (how it should FEEL).

```python
table = builder.add_table("team", region="main", group_id="members")
table.add_row(label="Alice", value="Engineer")
table.add_row(label="Bob", value="Designer")
```

### CompositionIR

The compiler's output. A strict hierarchical tree of 12 UI primitives (`root`, `stack`, `grid`, `cluster`, `surface`, `text`, `label`, `value`, `badge`, `image_slot`, `rule`, `svg`) with full provenance tracking. Every IR node knows which semantic addresses and intent refs produced it.

### Emitters

Pluggable renderers that turn CompositionIR into concrete output. Subclass `EmitterPlugin`:

```python
from viewspec.emitters.base import EmitterPlugin

class MyEmitter(EmitterPlugin):
    def emit(self, ast_bundle, output_dir):
        # Walk ast_bundle.result.root.root and produce output
        ...
```

The included HTML/Tailwind emitter produces standalone HTML with full Tailwind styling, provenance data attributes on every DOM element, action event dispatch, and a JSON provenance manifest.

## Motif Types

| Builder | Motif | Use case |
|---------|-------|----------|
| `add_table()` | `table` | Tabular data with label-value rows |
| `add_dashboard()` | `dashboard` | KPI cards with label-value pairs |
| `add_outline()` | `outline` | Hierarchical outlines and trees |
| `add_comparison()` | `comparison` | Side-by-side comparisons |

Each builder returns a chained sub-builder. Compose them freely within a single ViewSpec.

## Compilation

### Reference Compiler (free, offline)

Handles the four standard motifs locally. No API, no network, no LLM. Deterministic.

```python
ast = compile(builder.build_bundle())
```

### Hosted Compiler (api.viewspec.dev)

For complex layouts, novel data shapes, and advanced derivation. The hosted compiler was **evolved** (not hand-written) using reinforcement learning:

- **13/13** on a static validation suite
- **50/50** on novel, randomized out-of-distribution layouts (one-shot)
- **Level 2 derivation tokens** — data-aware emphasis, narrative routing, palette energy
- **Zero LLM calls at runtime** — deterministic Python compile path; the live playground reports measured `compile_ms` for each request

```python
from viewspec import compile_auto

# Try local first, fall back to hosted for unsupported motifs
ast = compile_auto(builder.build_bundle())
```

| Tier | Price | Hosted Calls/Day |
|------|-------|-----------------|
| Free | $0 | 500 |
| Pro | $39/mo | 25,000 |
| Scale | $99/mo | 250,000 |

## Wire Format

Protocol Buffers for language-agnostic serialization. The same ViewSpec can be constructed in Python, Rust, Go, TypeScript, or any language with protobuf support.

```python
bundle = builder.build_bundle()
json_data = bundle.to_json()           # JSON round-trip
proto_bytes = bundle.to_proto().SerializeToString()  # Protobuf round-trip
```

## Examples

See [`examples/`](examples/):

- **`invoice_table.py`** — Build a table in 15 lines
- **`kpi_dashboard.py`** — KPI dashboard with style tokens
- **`comparison_view.py`** — Side-by-side comparison
- **`emit_html.py`** — Load a compiled AST and emit HTML

## License

MIT
