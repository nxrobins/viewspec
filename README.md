# ViewSpec

**Universal UI from semantic data.**

ViewSpec is a declarative language for describing what data means. The compiler figures out how it looks.

```python
from viewspec import ViewSpecBuilder

builder = ViewSpecBuilder("invoice")
table = builder.add_table("line_items", region="main", group_id="rows")
table.add_row(label="Widget A", value="$50.00")
table.add_row(label="Widget B", value="$120.00")
table.add_row(label="Shipping", value="$15.00")

bundle = builder.build_bundle()  # → IntentBundle (JSON or protobuf)
```

That's it. No components. No layout code. No CSS. You described the data semantically. The compiler routes it into a mathematically correct visual structure — a `CompositionIR` tree — with full provenance from every rendered element back to its source data.

## What ViewSpec Does

**Before ViewSpec:** You manually bridge the gap between data and UI. Every component, every prop, every layout decision — hand-wired by a developer.

**After ViewSpec:** You declare what the data means. The compiler determines the visual structure. Rendering is a pluggable backend.

```
Data → ViewSpec (semantic intent) → Compiler → CompositionIR → Renderer
                                                                ├── HTML/Tailwind
                                                                ├── WebGPU
                                                                ├── PDF
                                                                ├── Native mobile
                                                                └── Your custom emitter
```

## Three Invariants

ViewSpec enforces three mathematical guarantees:

1. **Exactly-once provenance.** Every data binding is routed exactly once. Nothing dropped. Nothing duplicated. Nothing hallucinated. Every pixel has a birth certificate.

2. **Semantic grouping.** Data is grouped by meaning, not by visual adjacency. A "row" is defined by semantic boundaries, not grid coordinates.

3. **Strict ordering.** The original data order is preserved as a mathematical guarantee. The compiler cannot reorder your data.

## Install

```bash
pip install viewspec
```

Requires Python 3.11+.

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
# The builder handles the wiring — you think in data, not layout
table = builder.add_table("team", region="main", group_id="members")
table.add_row(label="Alice", value="Engineer")
table.add_row(label="Bob", value="Designer")
```

### CompositionIR

The compiler's output. A strict hierarchical tree of UI primitives (`root`, `stack`, `grid`, `surface`, `text`, `label`, `value`, `badge`, etc.) with full provenance tracking. Every IR node knows which semantic addresses and intent refs produced it.

### Emitters

Pluggable renderers that turn CompositionIR into concrete output. Ship your own by subclassing `EmitterPlugin`:

```python
from viewspec.emitters.base import EmitterPlugin

class MyEmitter(EmitterPlugin):
    def emit(self, ast_bundle, output_dir):
        # Walk ast_bundle.result.root.root and produce output
        ...
```

The included HTML/Tailwind emitter produces standalone HTML with:
- Full Tailwind styling
- Provenance data attributes on every DOM element (`data-ir-id`, `data-content-refs`, `data-intent-refs`)
- Action event dispatch (`viewspec-action` custom events)
- A provenance manifest (JSON mapping every DOM element to its semantic source)

## Motif Types

The SDK includes fluent builders for common data shapes:

| Builder | Motif | Use case |
|---------|-------|----------|
| `add_table()` | `table` | Tabular data with label-value rows |
| `add_dashboard()` | `dashboard` | KPI cards with label-value pairs |
| `add_outline()` | `outline` | Hierarchical outlines and trees |
| `add_comparison()` | `comparison` | Side-by-side comparisons |

Each builder returns a chained sub-builder. Compose them freely within a single ViewSpec.

## Wire Format

ViewSpec uses Protocol Buffers for language-agnostic serialization. The same ViewSpec can be constructed in Python, Rust, Go, TypeScript, or any language with protobuf support.

```python
# JSON round-trip
bundle = builder.build_bundle()
json_data = bundle.to_json()
restored = IntentBundle.from_json(json_data)

# Protobuf round-trip
proto_bytes = bundle.to_proto().SerializeToString()
restored = IntentBundle.from_proto(IntentBundlePb.FromString(proto_bytes))
```

## Examples

See the [`examples/`](examples/) directory:

- **`invoice_table.py`** — Build a table in 15 lines
- **`kpi_dashboard.py`** — KPI dashboard with style tokens
- **`comparison_view.py`** — Side-by-side comparison
- **`emit_html.py`** — Load a compiled AST and emit HTML + Tailwind

## The Compiler

The ViewSpec SDK is open source. The compiler — which routes ViewSpecs into CompositionIR trees with mathematically guaranteed provenance — is a hosted service.

The compiler was evolved (not hand-written) using a reinforcement learning loop that achieved:
- **13/13** on a static validation suite
- **50/50** on novel, randomized out-of-distribution layouts (one-shot)
- **Zero LLM cost at runtime** — the compiler is deterministic Python

The compiler handles any valid ViewSpec, including data shapes it has never seen before.

## License

MIT
