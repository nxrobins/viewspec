# Getting Started: First ViewSpec In 5 Minutes

ViewSpec is an agent-native UI IR. Humans or agents describe semantic data and view intent; the compiler produces CompositionIR, diagnostics, provenance, and emitter-ready artifacts.

## Install

```bash
pip install viewspec
```

## Local Reference Compile

```python
from viewspec import ViewSpecBuilder, compile
from viewspec.emitters.html_tailwind import HtmlTailwindEmitter

builder = ViewSpecBuilder("invoice")
table = builder.add_table("items", region="main", group_id="rows")
table.add_row(label="Design System Audit", value="$4,200")
table.add_row(label="Component Library", value="$8,500")

ast = compile(builder.build_bundle())
HtmlTailwindEmitter().emit(ast, "output/")
```

Use the hosted compiler for projections, inputs, declarative rules, custom motifs, Level 2+ derivation, and mobile emitters.

## Local HTML Wedge

Use the CLI when you already have raw HTML and need a governed, offline artifact:

```bash
viewspec compile input.html --design DESIGN.md --out dist/
viewspec lift input.html --out lift.json
viewspec diff old.html new.html --json
```

Raw HTML compile writes `index.html`, `provenance_manifest.json`, and `diagnostics.json`. With `--lift-json`, it also writes `lift.json`.

This path is sanitize + theme + manifest + diff. It is not full ViewSpec decompilation, and it does not perform pixel review.

## Theming with DESIGN.md

For local compilation, parse a strict `DESIGN.md` subset and pass it to raw HTML or IntentBundle compilation:

```python
from viewspec import compile, compile_html, load_design_system

design = load_design_system(path="DESIGN.md")
html_result = compile_html("<h1>Report</h1>", design=design)
ast = compile(builder.build_bundle(), design=design)
```

From the CLI:

```bash
viewspec compile input.html --design DESIGN.md --out dist/
viewspec compile bundle.json --design DESIGN.md --out dist/
```

Parse errors, broken token references, and cycles are fatal. Malformed ignorable tokens produce diagnostics and fall back to defaults. `--strict-design` escalates warnings to failure.

For hosted-only surfaces, attach a `DESIGN.md` identity file as an opaque API payload:

```python
from viewspec import ViewSpecBuilder, compile_remote_response

builder = ViewSpecBuilder("invoice")
builder.attach_design("DESIGN.md")
response = compile_remote_response(builder.build_compile_request())

ast = response.ast
design_meta = response.meta.design
```

Colors must be exact sRGB hex values such as `#FFFFFF`; `rgba()`, `#FFF`, and named colors are ignored with defaults. React/HTML can receive custom `fontFamily` CSS, while Flutter and SwiftUI coerce custom families to native system defaults and preserve size, weight, and tracking.
