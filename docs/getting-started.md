# Getting Started: First ViewSpec In 5 Minutes

ViewSpec's primary workflow is agent-native UI intent. Your agent writes `IntentBundle` JSON, ViewSpec validates it, then the compiler emits HTML and other concrete UI surfaces.

## Install

```bash
pip install viewspec
```

## Agent Intent First

For new UI, make the agent create `viewspec.intent.json` and run:

```bash
viewspec init-intent --out viewspec.intent.json
viewspec init-design --out DESIGN.md
viewspec validate-intent viewspec.intent.json --json
viewspec diff-intent old.intent.json new.intent.json --json
viewspec compile viewspec.intent.json --design DESIGN.md --out dist/
viewspec check dist/
```

`init-intent` writes a valid scaffold for table, dashboard, outline, comparison, list, form, detail, empty_state, or hero motifs. Replace the sample content with real user intent before compiling. `validate-intent` rejects arrays, markdown-wrapped JSON, malformed JSON, CompositionIR-shaped payloads, oversized bundles, and bundles unsupported by the local reference compiler. `viewspec compile` runs the same validation before writing artifacts. Invalid results include a correction prompt for the agent to regenerate the full IntentBundle.

Use `viewspec diff-intent` when reviewing agent revisions. It reports `basis: "intent_bundle_v1"` and compares top-level bundle metadata, declared semantic nodes, regions, bindings, motifs, styles, actions, selected field changes, and `semantic_changes` for motif, binding, and action contract changes before you inspect generated DOM.

V1 local caps keep agent repair loops predictable: max 256KB JSON, 200 substrate nodes, 32 regions, 400 bindings, 64 groups, 32 motifs, 400 styles, 64 actions, 64 attrs/slots/edges per node, 200 values per slot or edge, and 64 payload bindings per action.

Use `viewspec init-design --out DESIGN.md` for a starter design file when the repo does not already have one, and `viewspec doctor` to check local SDK readiness. `doctor` reports the intent-first commands, runs a starter IntentBundle validation/compile/diff smoke check, verifies `PyYAML`, and states the local no-network policy.

## Import Existing HTML

Use raw HTML commands only when you already have HTML and need an offline import/fallback artifact:

```bash
viewspec compile input.html --design DESIGN.md --out dist/
viewspec lift input.html --out lift.json
viewspec diff old.html new.html --json
viewspec check dist/
```

Raw HTML compile writes `index.html`, `provenance_manifest.json`, and `diagnostics.json`. With `--lift-json`, it also writes `lift.json`. This path is sanitize + theme + manifest + diff. It is not full ViewSpec decompilation, and it does not perform pixel review.

## Native Agent Use

Add managed ViewSpec instructions to a repo so agents create `viewspec.intent.json`, validate it, compile it, and check the artifact:

```bash
viewspec init-agent --target codex
viewspec init-agent --target claude-code
viewspec init-agent --target cursor
viewspec init-agent --target copilot
viewspec init-agent --target all --dry-run
```

The command preserves user content outside `<!-- BEGIN VIEWSPEC AGENT INSTRUCTIONS v1 -->` and `<!-- END VIEWSPEC AGENT INSTRUCTIONS v1 -->`.

Export local prompt, schema, valid example, and asset manifest files when an editor or agent runtime can consume them directly:

```bash
viewspec export-agent-assets --out .viewspec
```

For MCP-capable agents:

```bash
python -m pip install "viewspec[agents]"
viewspec mcp
viewspec doctor --agents
```

The MCP tools are local-only by default and reject paths outside the configured working directory. Intent tools are the default for new UI; raw HTML MCP tools are import/fallback only. MCP also exposes `export_agent_assets` for local prompt, schema, valid example, and asset manifest export.

Treat compiled output directories as generated artifacts. Edit `viewspec.intent.json` or `DESIGN.md`, then re-run compile and check; do not patch `dist/index.html` or `react-output/ViewSpecView.tsx` by hand.

## Programmatic IntentBundle Compile

```python
import json

from viewspec import ViewSpecBuilder, compile, diff_intent_text, validate_intent_text
from viewspec.emitters.html_tailwind import HtmlTailwindEmitter
from viewspec.emitters.react_tsx import ReactTsxEmitter

builder = ViewSpecBuilder("invoice")
table = builder.add_table("items", region="main", group_id="rows")
table.add_row(label="Design System Audit", value="$4,200")
table.add_row(label="Component Library", value="$8,500")

message_binding = builder.add_text_input("message", label="Message", value="")
builder.add_action("send", "submit", "Send", target_region="main", payload_bindings=[message_binding])

bundle = builder.build_bundle()
validation = validate_intent_text(json.dumps(bundle.to_json()))
diff = diff_intent_text(json.dumps(bundle.to_json()), json.dumps(bundle.to_json()))
ast = compile(bundle)
HtmlTailwindEmitter().emit(ast, "output/")
ReactTsxEmitter().emit(ast, "react-output/")
```

The local reference compiler supports safe text inputs and local action payload events. HTML action events dispatch `viewspec-action` with `detail.schemaVersion: 1`, `source`, `id`, `kind`, `targetRef`, `payloadBindings`, and `payloadValues`. Pressing Enter inside a local inert form dispatches only a declared `submit` action whose `targetRef` exactly matches that form motif. React TSX output uses an `onAction` callback with the same V1 fields and `source: "viewspec-react-tsx"`.

From the CLI, use `--target react-tsx` when you want component source instead of standalone HTML:

```bash
viewspec compile viewspec.intent.json --target react-tsx --out react-output/
```

`viewspec check` also verifies React TSX source artifacts: manifest shape, exact `ViewSpecView.tsx` hash, generated-source markers, diagnostics shape, and absence of active network/runtime escape surfaces. This is source artifact verification, not a rendered DOM proof inside a host React app. Use the hosted compiler for richer input controls, projections, declarative rules, custom motifs, Level 2+ derivation, and mobile emitters. Hosted demo artifact indexes declare `contract_profile: "hosted_extended_v1"` when their IntentBundle uses fields beyond local V1 validation.

## Theming with DESIGN.md

For local compilation, parse a strict `DESIGN.md` subset and pass it to IntentBundle or raw HTML import compilation:

```python
from viewspec import compile, compile_html, load_design_system

design = load_design_system(path="DESIGN.md")
html_result = compile_html("<h1>Report</h1>", design=design)
ast = compile(builder.build_bundle(), design=design)
```

From the CLI:

```bash
viewspec compile viewspec.intent.json --design DESIGN.md --out dist/
viewspec compile existing.html --design DESIGN.md --out dist/
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

Colors must be exact sRGB hex values such as `#FFFFFF`; `rgba()`, `#FFF`, and named colors are ignored with defaults. React/HTML can receive custom `fontFamily` CSS. In local HTML output, `typography.body` styles normal text and `typography.heading` styles prominent values through the compiler's emphasis token. Flutter and SwiftUI coerce custom families to native system defaults and preserve size, weight, and tracking.
