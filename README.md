# ViewSpec

**Stop asking agents to write React, DOM, or State.**

ViewSpec is an agent-native UI compiler. It acts as a universal Intermediate Representation (IR) for AI Software Engineering. Agents describe UI and state intent as `IntentBundle` or `AppBundle` JSON; ViewSpec compiles that intent into deterministic HTML and React surfaces with the free local compiler, and SwiftUI or Flutter surfaces via the hosted compiler.

By totally decoupling intent from implementation, ViewSpec eliminates the "visual context window" problem. Agents no longer need to see pixels to build complex, perfectly styled, and deeply interactive applications.

🌐 **[viewspec.dev](https://viewspec.dev)** — Compiled reference demos, pricing, and hosted compiler docs

```bash
viewspec prove --out .viewspec-proof
viewspec init-intent --out viewspec.intent.json
viewspec init-design --out DESIGN.md
viewspec validate-intent viewspec.intent.json --json
viewspec diff-intent old.intent.json new.intent.json --json
viewspec init-app --out viewspec.app.json
viewspec validate-app viewspec.app.json --json
viewspec compile-app viewspec.app.json --out app-dist --target html-tailwind-app --json
viewspec prove-app --app viewspec.app.json --out .viewspec-app-proof --with-shell --json
viewspec compile viewspec.intent.json --design DESIGN.md --out dist/
viewspec check dist/
```

## What ViewSpec Does

The primary workflow is Intent-first compilation: semantic UI intent goes in, concrete renderer output plus a manifest comes out. 

**Before ViewSpec:** Agents author DOM, CSS, and framework code directly. The model gets trapped in markup details instead of expressing structure, data, hierarchy, and interaction intent.

**After ViewSpec:** Agents author `IntentBundle` JSON. The compiler owns layout lowering, state generation, renderer output, design-token application, diagnostics, and provenance.

```
IntentBundle JSON -> ViewSpec compiler -> HTML / React / SwiftUI / Flutter / CompositionIR
       |-- validate agent contract
       |-- apply DESIGN.md
       |-- generate TypeScript Reducers (AppBundle V3)
       |-- write provenance_manifest.json
       `-- keep DOM and framework code as compiler output
```

## Three Invariants

ViewSpec enforces three deterministic invariants:

1. **Exactly-once provenance.** Every valid data binding is routed exactly once. Conflicting or duplicate bindings are deterministically resolved (first occurrence wins) and flagged as a diagnostic — never silently dropped, duplicated, or hallucinated.
2. **Semantic grouping.** Data is grouped by meaning, not by visual adjacency.
3. **Strict ordering.** The original data order is preserved deterministically, including across serialization round-trips.

## New: AppBundle V3 & State IR

ViewSpec is no longer just for static dashboards. With AppBundle V3, agents can declare fully interactive state mutations safely:

* **Declarative Mutation IR**: Agents define state transitions (`set`, `patch`, `toggle`, `append`, `move`, `increment`) in JSON.
* **Deterministic Reducer Generation**: The compiler automatically generates bulletproof TypeScript reducers (`reduceViewSpecState`).
* **State Replay Assertions**: Agents write expected state outputs; the compiler statically proves the generated logic against the Node.js runtime before the code even runs in a browser.

## New: Custom Motif Plugins

Extend the local compiler securely with a microkernel architecture:

* **MotifPluginManifest**: Define strict input slots, ABI versions, and output guarantees for enterprise motifs (e.g., `financial_candlestick_chart`).
* **IR Portability**: Custom plugins lower directly into standard `CompositionIR`. You don't need to write custom HTML/React emitters for your new motifs!
* **Registry Support**: Pass a `motif_registry` to the `compile` pipeline for reusable, safe plugin execution.

## Install

```bash
pip install viewspec
```

Requires Python 3.11+.

Python package: <https://pypi.org/project/viewspec/>

Hosted compiler pricing starts with Free at 500 hosted compile calls/day. Pro is $149/month for 10,000 hosted compile calls/day and up to 5 custom motif instances per compile; Enterprise is custom volume and terms.

## IntentBundle-First Local Workflow

For a first proof, run:

```bash
viewspec prove --out .viewspec-proof
```

This generates a starter IntentBundle and DESIGN.md inside `.viewspec-proof/`, compiles through the public local path, runs artifact checks, records compact style-delta counts when profiles are present, and writes `.viewspec-proof/PROOF.md` for humans, `.viewspec-proof/proof_report.json` for tools, and `.viewspec-proof/support_bundle.json` for redacted local support triage. Read [ViewSpec Proof Bundle](docs/proof-bundle.md) when you need to interpret status, hashes, checks, failure codes, or local support triage. Machine reports include proof identity metadata under `proof_identity` for artifact, manifest, proof report, human summary, and support bundle hashes. It proves source artifact integrity and provenance for the generated artifact; ViewSpec prove is not pixel-perfect visual regression, accessibility certification, arbitrary host-app certification, or hosted compiler publish automation.

### Core Commands

* `init-intent`: Writes a valid scaffold for all supported motifs.
* `init-design`: Scaffolds a local `DESIGN.md` for theming.
* `validate-intent`: Rejects malformed JSON and enforces the bounded local agent contract.
* `diff-intent`: Provides a semantic diff between intent states, including aesthetic profile changes, before generated HTML review; Python callers can format semantic changes with `intent_semantic_change_lines`.
* `compile`: Compiles the intent into HTML/React based on the target.
* `check`: Verifies the provenance manifest against the generated DOM.
* `doctor`: Reports the intent-first command surface and local agent prompt status.

**The Bounded Local Agent Contract**: The local schema enforces strict bounds to prevent agent hallucinations and infinite loops (e.g., max 256KB JSON, 32 regions, 400 bindings, 64 actions). Split larger products into smaller IntentBundles.

Generated outputs are artifacts, not source: standalone HTML writes `dist/index.html`, while React source targets write `react-output/ViewSpecView.tsx` plus checked `provenance_manifest.json` and `diagnostics.json`. Agents should edit `viewspec.intent.json` or `DESIGN.md`, then regenerate artifacts instead of patching generated files.

## AppBundle: Narrow App Generation

For multi-screen internal tool contracts, use AppBundle JSON:

```bash
viewspec init-app --out viewspec.app.json
viewspec init-app --resource-binding fixture-readonly-v0 --out viewspec.bound.app.json
viewspec validate-app viewspec.app.json --json
viewspec diff-app old.app.json new.app.json --json
viewspec compile-app viewspec.app.json --out app-dist --target html-tailwind-app --json
viewspec prove-app --app viewspec.app.json --out .viewspec-app-proof --with-shell --json
```

* **V1**: Unbound fixtures reported as `unbound_v0`.
* **V2**: Strict readonly fixture resources reported as `fixture_readonly_v0` with declared per-screen views.
* **V3**: Adds bounded interactive state, declarative mutations, and a generated pure TypeScript reducer artifact.

`compile-app` writes a single `app-dist/index.html` Static Shell V0 artifact with hash-based local routing, plus checked screen artifacts and generated TS state reducers for V3. The shell is proof-oriented source output; it does not claim production runtime navigation, browser navigation proof, framework adapters, persistence, sync, or hosted extended compiler behavior.

Aesthetic Profiles V1 are deterministic art-direction handles, not CSS: `aesthetic.calm_ops`, `aesthetic.premium_saas`, `aesthetic.data_dense`, `aesthetic.editorial_product`, and `aesthetic.executive_review`. Checked summaries expose compact style-delta counts and bounded layout deltas for profiled artifacts, not arbitrary CSS control, pixel-perfect visual proof, or design certification.

## Import Existing HTML (0.3.0b1 beta)

The raw HTML path is an import/fallback tool for existing HTML. It sanitizes active content, applies local `DESIGN.md` tokens, writes deterministic provenance, and can report semantic diffs.

```bash
viewspec compile input.html --design DESIGN.md --out dist/
viewspec lift input.html --out lift.json
viewspec diff old.html new.html --json
```

## Native Agent Use

Install managed instructions so coding agents natively understand how to use ViewSpec:

```bash
viewspec init-agent --target codex
viewspec init-agent --target claude-code
viewspec init-agent --target cursor
viewspec init-agent --target copilot
```

Use `--target all` to write every supported instruction file.

For schema-aware editors or agents, export the local contract assets:

```bash
viewspec export-agent-assets --out .viewspec
viewspec check-agent-assets .viewspec --json
```

Agent assets use schema version `7`, contract profile `local_v1`, and the same export/check commands shown above; exported files include the local intent schema, AppBundle schema, starter examples, prompt, and asset manifest without SDK network calls.

Optional **MCP tooling** is available behind the agent extra:

```bash
python -m pip install "viewspec[agents]"
viewspec mcp
```
The MCP server exposes all intent-first local tools without requiring shell commands, including `validate_intent_bundle_file`, `compile_intent_bundle_file`, `verify_host`, `prove`, `validate_app_file`, `diff_app_files`, `compile_app`, and `prove_app`.

For bounded React Tailwind runtime proof, run `viewspec prove --target react-tailwind-tsx --install --out .viewspec-proof --json` or `viewspec verify-host react-tailwind-output/ --target react-tailwind-tsx --install --json`. The host proof checks grid column/span counts, profiled aesthetic marker/layout assertions, and action payload behavior; JSON reports include `assertion_requirements` with `dom_count`, `style_assertion_count`, `aesthetic_layout_assertion_count`, `aesthetic_profile_assertion_count`, and `grid_span_assertion_count`.

## viewspec.dev

The home page at [viewspec.dev](https://viewspec.dev) shows compiled reference artifacts across the aesthetic profiles, with inspectable IntentBundle, provenance manifest, and generated artifact source for each.

Agent and crawler entrypoints are published:
- `https://viewspec.dev/llms.txt` — concise LLM-facing product map
- `https://viewspec.dev/llms-full.txt` — expanded AI context and canonical facts
- `https://viewspec.dev/agent-assets.json` — versioned manifest with contract identity
- `https://viewspec.dev/openapi.json` — hosted compiler OpenAPI description

## Demos

Reference demos are available at [viewspec.dev](https://viewspec.dev):

| Demo | What it shows |
|------|--------------|
| [Same Data, Three Motifs](https://viewspec.dev/motif-switcher/) | One dataset → table, dashboard, or comparison. |
| [Provenance Inspector](https://viewspec.dev/provenance-inspector/) | Hover any element. Trace DOM → IR → binding → address → raw data. |
| [The Invariants](https://viewspec.dev/invariants/) | Watch the compiler enforce each mathematical guarantee. |
| [15 Lines → Full UI](https://viewspec.dev/fifteen-lines/) | An invoice table builds itself from 15 lines of Python. |
| [Style Derivation](https://viewspec.dev/style-derivation/) | Toggle four visual presets deterministically. |
| [One Spec, Four Surfaces](https://viewspec.dev/cross-platform-dashboard/) | One intent compiles to HTML, React, SwiftUI, and Flutter. |
| [Custom Motif Authoring](https://viewspec.dev/custom-motifs/) | Define an MDL motif contract and lower it into portable IR. |
| [Interactive Compose](https://viewspec.dev/interactive-compose/) | State IR compiled into event surfaces. |

## Core Concepts

### Semantic Substrate
The raw data graph. Nodes with typed attributes, slots, and edges. This is WHAT the data is — no visual intent.

### ViewSpec
The declarative intent layer. Regions (WHERE), bindings (WHICH data goes WHERE), motifs (HOW it should be structured), and styles (how it should FEEL).

### CompositionIR
The compiler's output. A strict hierarchical tree of UI primitives with full provenance tracking.

### Emitters
Pluggable renderers that turn CompositionIR into concrete output. The local SDK ships `HtmlTailwindEmitter`, `ReactTsxEmitter`, and `ReactTailwindTsxEmitter`. Because custom local plugins lower into portable `CompositionIR`, emitters **do not** need custom code paths to support new motifs.

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
| `add_loading_state()` | `loading_state` | Current loading state for a collection or region |
| `add_error_state()` | `error_state` | Current error state for a collection or region |
| `add_hero()` | `hero` | Intro/header sections with eyebrow, title, and description |
| `add_collection_action()` | action helper | `search`, `filter`, `sort`, `paginate`, or `bulk_action` events for a table/list |

## Compilation

### Reference Compiler (free, offline)
Handles the local V1 motifs and bounded collection action events locally. No API, no network, no LLM. Deterministic. 

```python
ast = compile(builder.build_bundle())
```

### Hosted Compiler (api.viewspec.dev)
For complex layouts, novel data shapes, advanced derivation, and the SwiftUI/Flutter emitters, which are hosted-only and not shipped in the local SDK.

*   **Zero LLM calls at runtime** — deterministic layout resolution, same no-LLM contract as the local compiler.
*   **Derivation tokens** — data-aware emphasis, narrative routing, palette energy.

```python
from viewspec import compile_auto
# Try local first, fall back to hosted for unsupported motifs
ast = compile_auto(builder.build_bundle())
```

### Theming with DESIGN.md
The local SDK uses a strict YAML-front-matter `DESIGN.md` for offline HTML and IntentBundle compilation. The API requires exact sRGB hex values (e.g., `#FFFFFF`), enforcing strict design token discipline.

## License
MIT
