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

## Theming with DESIGN.md

For hosted compilation, attach a `DESIGN.md` identity file without parsing it in the SDK:

```python
from viewspec import ViewSpecBuilder, compile_remote_response

builder = ViewSpecBuilder("invoice")
builder.attach_design("DESIGN.md")
response = compile_remote_response(builder.build_compile_request())

ast = response.ast
design_meta = response.meta.design
```

The API owns all DESIGN.md parsing, linting, cycle detection, and token mapping. Colors must be exact sRGB hex values such as `#FFFFFF`; `rgba()`, `#FFF`, and named colors are ignored with defaults. React/HTML can receive custom `fontFamily` CSS, while Flutter and SwiftUI coerce custom families to native system defaults and preserve size, weight, and tracking.
