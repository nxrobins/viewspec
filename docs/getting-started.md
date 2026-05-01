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
