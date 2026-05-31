# ViewSpec

**Stop asking agents to write DOM.**

ViewSpec is an agent-native UI compiler. Agents describe UI intent as `IntentBundle` JSON; ViewSpec compiles that intent into HTML and other concrete UI surfaces with deterministic provenance.

🌐 **[viewspec.dev](https://viewspec.dev)** — Live hosted compiler playground, demos, and pricing

```bash
viewspec init-intent --out viewspec.intent.json
viewspec init-design --out DESIGN.md
viewspec validate-intent viewspec.intent.json --json
viewspec diff-intent old.intent.json new.intent.json --json
viewspec compile viewspec.intent.json --design DESIGN.md --out dist/
viewspec check dist/
```

## What ViewSpec Does

The primary workflow is IntentBundle-first compilation: semantic UI intent goes in, concrete renderer output plus a manifest comes out. Raw HTML tools remain available when importing existing HTML.

**Before ViewSpec:** Agents author DOM, CSS, and framework code directly. The model gets trapped in markup details instead of expressing structure, data, hierarchy, and interaction intent.

**After ViewSpec:** Agents author `IntentBundle` JSON. The compiler owns layout lowering, renderer output, design-token application, diagnostics, and provenance.

```
IntentBundle JSON -> ViewSpec compiler -> HTML / React / SwiftUI / Flutter / CompositionIR
       |-- validate agent contract
       |-- apply DESIGN.md
       |-- write provenance_manifest.json
       `-- keep DOM and framework code as compiler output
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

## IntentBundle-First Local Workflow

Agents should create `viewspec.intent.json` for new UI. Validate the contract before compiling, then check the artifact manifest and hashes.

```bash
viewspec init-intent --out viewspec.intent.json
viewspec init-design --out DESIGN.md
viewspec validate-intent viewspec.intent.json --json
viewspec compile viewspec.intent.json --design DESIGN.md --out dist/
viewspec check dist/
viewspec doctor
```

`init-intent` writes a valid scaffold for table, dashboard, outline, comparison, list, form, detail, empty_state, or hero motifs. Replace the sample content with real user intent before compiling. `validate-intent` rejects malformed JSON, markdown-wrapped JSON, arrays, CompositionIR-shaped payloads, oversized bundles, and IntentBundles unsupported by the local reference compiler. `viewspec compile` runs the same validation before writing artifacts, so failed intent returns a deterministic correction prompt instead of a partial output directory.

Run `viewspec init-design --out DESIGN.md` only when the repo does not already have a design file. Existing `DESIGN.md` files should remain the source of truth for theming.

`viewspec validate-intent` exits `0` for valid intent, `2` for user-correctable invalid intent, and `1` for environment or internal failure.

`viewspec doctor` reports the intent-first command surface, runs a starter IntentBundle validation/compile/diff smoke check, verifies `PyYAML`, and states the local no-network policy for `validate-intent`, `compile`, `lift`, `diff`, `diff-intent`, `check`, `init-intent`, `init-design`, and `export-agent-assets`.

Use `viewspec diff-intent old.intent.json new.intent.json --json` to review semantic IntentBundle changes before looking at generated DOM or framework artifacts. The diff is intentionally honest: `basis: "intent_bundle_v1"` compares top-level bundle metadata, declared nodes, regions, bindings, groups, motifs, styles, actions, selected field changes, and a `semantic_changes` summary for motif, binding, and action contract changes. It is not a claim of full visual equivalence.

Python callers can use the same public SDK helpers from the package root: `starter_intent_bundle()`, `init_intent_file()`, `validate_intent_text()`, `validate_intent_file()`, `diff_intent_text()`, and `diff_intent_files()`.

The v1 local agent contract is intentionally bounded: max 256KB JSON, 200 substrate nodes, 32 regions, 400 bindings, 64 groups, 32 motifs, 400 styles, 64 actions, 64 attrs/slots/edges per node, 200 values per slot or edge, and 64 payload bindings per action. V1 ids and object keys use only letters, digits, underscore, dot, and dash. V1 agent bindings use `exactly_once` cardinality, group kind is `ordered`, region layouts are `stack`, `grid`, or `cluster`, regions must form one acyclic tree rooted at `view_spec.root_region`, `complexity_tier` starts at 1, region child bounds are non-negative with `max_children` null or at least `min_children`, and style tokens must come from the published agent schema. The local schema and `validate-intent` reject hosted-only fields with `HOSTED_ONLY_FIELD`: root `design`, root `motif_library`, `view_spec.inputs`, `view_spec.projections`, and `view_spec.rules`. Unknown extension fields fail with `UNKNOWN_FIELD` instead of being ignored. Split larger products into smaller IntentBundles.

Motif validation is semantic, not just structural. Empty motifs fail. `hero` and `empty_state` require title-like bindings, `form` requires an input binding, table/dashboard/detail motifs require label plus value/text-style bindings, and comparison motifs require at least two distinct semantic items.

## Import Existing HTML (0.3.0b1 beta)

The raw HTML path is an import/fallback tool for existing HTML. It is intentionally narrow: it sanitizes active content, applies local `DESIGN.md` tokens, writes deterministic provenance, and can report semantic diffs. It does not claim full ViewSpec IR recovery or pixel review.

```bash
viewspec compile input.html --design DESIGN.md --out dist/
viewspec lift input.html --out lift.json
viewspec diff old.html new.html --json
viewspec check dist/
viewspec init-design --out DESIGN.md
viewspec doctor
```

Example inputs live at `examples/raw_html_report.html` and `examples/raw_html_DESIGN.md`.

Raw HTML output files are:

- `index.html`
- `provenance_manifest.json`
- `diagnostics.json`
- optional `lift.json` with `--lift-json`

The local commands `compile`, `lift`, and `diff` make no SDK-process network calls. Generated raw-HTML artifacts also avoid automatic network fetches: remote image sources are replaced with inert links and disclosed in `external_refs`; user-clicked external anchors remain clickable with `rel="noopener noreferrer"`.

`provenance_manifest.json` is the trust boundary. It records SDK version, raw source hash, lifted source hash, design hash, artifact hash, command arguments, sanitizer policy, diagnostics, and external references. For IntentBundle artifacts, `viewspec check` also verifies DOM identity against manifest identity: no duplicate binding/action ids, binding nodes keep non-empty source provenance, and action/binding refs match their declared intent refs.

## Native Agent Use

Install managed instructions so coding agents create `viewspec.intent.json` for new UI, validate it, compile it, and check the artifact:

```bash
viewspec init-agent --target codex
viewspec init-agent --target claude-code
viewspec init-agent --target cursor
viewspec init-agent --target copilot
```

Use `--target all` to write every supported instruction file. The command only manages the block between:

```html
<!-- BEGIN VIEWSPEC AGENT INSTRUCTIONS v1 -->
<!-- END VIEWSPEC AGENT INSTRUCTIONS v1 -->
```

For schema-aware editors or agents, export the same local contract assets shipped in the package:

```bash
viewspec export-agent-assets --out .viewspec
```

That writes `.viewspec/agent-system-prompt.txt` and `.viewspec/agent-intent-bundle.schema.json` without any network call. Existing edited files are preserved unless `--force` is passed.

Optional MCP tooling is available behind the agent extra:

```bash
python -m pip install "viewspec[agents]"
viewspec mcp
```

The MCP server exposes intent-first local tools: `init_intent`, `validate_intent_bundle_file`, `diff_intent_bundle_files`, `compile_intent_bundle_file`, `agent_correction_prompt_file`, `check_artifact`, and `init_design`. `compile_intent_bundle_file` accepts `target="html-tailwind"` for checked standalone HTML or `target="react-tsx"` for checked React source artifacts. Raw HTML MCP tools remain available only for importing existing HTML. By default, all tool paths must resolve under the MCP working directory and the tools make no SDK network calls.

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
| `proStripeUrl` | Pro checkout link. Defaults to the live $149/month Stripe payment link. |
| `enterpriseUrl` | Enterprise contact URL. Defaults to `mailto:hello@viewspec.dev?subject=ViewSpec%20Enterprise`. |
| `requestTimeoutMs` | Hosted compile timeout. Defaults to `6000`. |

Keep secret API keys server-side; only browser-safe public/demo keys belong in static landing-page config.

## Demos

The hosted playground, reference demos, and launch demos are available at [viewspec.dev](https://viewspec.dev):

| Demo | What it shows |
|------|--------------|
| [Same Data, Three Motifs](https://viewspec.dev/motif-switcher/) | One dataset → table, dashboard, or comparison. Change one parameter. |
| [Provenance Inspector](https://viewspec.dev/provenance-inspector/) | Hover any element. Trace DOM → IR → binding → address → raw data. |
| [Live Builder](https://viewspec.dev/live-builder/) | Browse ViewSpec JSON, IR tree, and rendered output in sync. |
| [The Invariants](https://viewspec.dev/invariants/) | Watch the compiler enforce — and refuse — each guarantee. |
| [15 Lines → Full UI](https://viewspec.dev/fifteen-lines/) | An invoice table builds itself from 15 lines of Python. |
| [Style Derivation](https://viewspec.dev/style-derivation/) | Same structure, different feel. Toggle four visual presets. |
| [One Spec, Four Surfaces](https://viewspec.dev/cross-platform-dashboard/) | One hosted-extended launch dashboard compiles to HTML, React TSX, SwiftUI, and Flutter. |
| [Custom Motif Authoring](https://viewspec.dev/custom-motifs/) | Define an MDL motif contract and lower it into portable CompositionIR. |
| [Interactive Compose](https://viewspec.dev/interactive-compose/) | Inputs, rules, and submit payloads compiled into event surfaces. |

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

message_binding = builder.add_text_input("message", label="Message", value="")
builder.add_action("send", "submit", "Send", target_region="main", payload_bindings=[message_binding])
```

### CompositionIR

The compiler's output. A strict hierarchical tree of UI primitives (`root`, `stack`, `grid`, `cluster`, `surface`, `text`, `label`, `value`, `badge`, `input`, `image_slot`, `rule`, `svg`, `button`, `error_boundary`) with full provenance tracking. Every IR node knows which semantic addresses and intent refs produced it.

### Emitters

Pluggable renderers that turn CompositionIR into concrete output. Subclass `EmitterPlugin`:

```python
from viewspec.emitters.base import EmitterPlugin

class MyEmitter(EmitterPlugin):
    def emit(self, ast_bundle, output_dir):
        # Walk ast_bundle.result.root.root and produce output
        ...
```

The included HTML/Tailwind emitter produces standalone HTML with full Tailwind styling, provenance data attributes on every DOM element, semantic table/list markup for table and list motifs, definition-list markup for detail motifs, checked absence sections for empty_state motifs, checked header/heading markup for hero motifs, inert `role="form"` sections for form motifs, safe local text inputs, accessible roles for generated image/error primitives, action event dispatch only when actions exist, and a JSON provenance manifest. Local action events dispatch `viewspec-action` with versioned `detail.schemaVersion: 1` payloads containing `source`, `id`, `kind`, `targetRef`, `payloadBindings`, and `payloadValues`. Pressing Enter inside a local inert form dispatches only a declared `submit` action whose `targetRef` exactly matches that form motif.

The local SDK also includes a deterministic React TSX emitter for the same local V1 `ASTBundle`. Use it when you want source code instead of standalone HTML:

```bash
viewspec compile viewspec.intent.json --target react-tsx --out react-output/
```

It writes `ViewSpecView.tsx`, `provenance_manifest.json`, and `diagnostics.json`. React actions are surfaced through an `onAction` callback with the same V1 fields and `source: "viewspec-react-tsx"`. `viewspec check` verifies the React source artifact's manifest, hash, generated-source markers, diagnostics shape, and no active network/runtime escape surfaces. It does not prove rendered DOM equivalence inside a host React app.

## Motif Types

| Builder | Motif | Use case |
|---------|-------|----------|
| `add_table()` | `table` | Tabular data with label-value rows |
| `add_dashboard()` | `dashboard` | KPI cards with label-value pairs |
| `add_outline()` | `outline` | Hierarchical outlines and trees |
| `add_comparison()` | `comparison` | Side-by-side comparisons |
| `add_list()` | `list` | Ordered narrative or task lists |
| `add_form()` | `form` | Inert local form intent with text fields and action payloads |
| `add_detail()` | `detail` | Read-only record/profile/settings detail fields |
| `add_empty_state()` | `empty_state` | Absence, no-results, or first-run states |
| `add_hero()` | `hero` | Intro/header sections with eyebrow, title, and description |
| `add_text_input()` | binding helper | Safe local text input with inferred accessible label |

Each builder returns a chained sub-builder. Compose them freely within a single ViewSpec.

## Compilation

### Reference Compiler (free, offline)

Handles the nine standard motifs locally. No API, no network, no LLM. Deterministic. The default CLI target is standalone HTML/Tailwind; `--target react-tsx` emits a local React component from the same compiled `ASTBundle`.

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

### Theming with DESIGN.md

The local SDK can parse a strict YAML-front-matter `DESIGN.md` subset for offline HTML and IntentBundle compilation. Parse errors, broken token references, and cycles are fatal. Malformed ignorable tokens become diagnostics and fall back to defaults; `--strict-design` escalates warnings to failure.

```bash
viewspec compile viewspec.intent.json --design DESIGN.md --out dist/
viewspec compile existing.html --design DESIGN.md --out dist/
```

Python callers can use the same local parser:

```python
from viewspec import compile, compile_html, load_design_system

design = load_design_system(path="DESIGN.md")
html_result = compile_html("<h1>Report</h1>", design=design)
ast = compile(bundle, design=design)
```

The hosted compiler can still ingest a `DESIGN.md` identity file as an opaque payload for hosted-only surfaces:

```python
from viewspec import ViewSpecBuilder, compile_remote_response

builder = ViewSpecBuilder("invoice")
builder.attach_design("DESIGN.md")
request = builder.build_compile_request()

response = compile_remote_response(request)
ast = response.ast
design_meta = response.meta.design
```

Raw strings are also supported:

```python
request = builder.attach_design("name: Acme\ncolor.primary: #FFFFFF\n", is_path=False).build_compile_request()
```

The TypeScript/Node SDK contract will mirror this shape:

```ts
const result = await compiler.withDesign("DESIGN.md").compile(bundle)
const inline = await compiler.withDesign("name: Acme\n", false).compile(bundle)
```

`DESIGN.md` ingestion is intentionally strict locally and in the API:

- Colors must be exact sRGB hex values such as `#FFFFFF`. `rgba()`, `#FFF`, and named CSS colors are ignored and fall back to defaults.
- `fontFamily` tokens map to React/HTML CSS. In local HTML output, `typography.body` styles normal text and `typography.heading` styles prominent values through the compiler's emphasis token. Flutter and SwiftUI emitters coerce custom font families to native system defaults while preserving size, weight, and tracking.

| Tier | Price | Hosted Calls/Day |
|------|-------|-----------------|
| Free | $0 | 500 |
| Pro | $149/mo | 10,000 hosted compile calls/day |
| Enterprise | Custom | Custom volume, unlimited custom motifs |

## Launch Compiler Surface

The hosted compiler now exposes the May 6 launch surface: SwiftUI and Flutter emitters; projections; rich input bindings; rule bindings; submit/navigate actions; and custom motifs. The local SDK ships HTML/Tailwind and React TSX emitters for the bounded local V1 contract. Hosted extended demo artifacts declare `contract_profile: "hosted_extended_v1"` when they go beyond the local V1 `validate-intent` contract.

Pro includes mobile emitters, 5 custom motif instances per compile, and 10,000 hosted compile calls/day.

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
