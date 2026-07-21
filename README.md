# ViewSpec

**Describe intent. Compile a checked interface.**

ViewSpec is an agent-native UI and app compiler. Agents describe semantic UI, state, and action intent as `IntentBundle` or `AppBundle` JSON. The free local compiler turns that source into deterministic HTML or React artifacts with provenance and checks; `prove --target react-tailwind-tsx --install` adds bounded browser evidence. The paid hosted API adds Level 2 derivation, projections, rules, custom motifs, signed receipts, SwiftUI and Flutter artifacts, and verified runnable AppBundle projects.

Generated DOM and framework code stay compiler-owned. Developers and agents revise semantic source, then regenerate and verify the result instead of patching output by hand.

🌐 **[viewspec.dev](https://viewspec.dev)** — Compiled reference demos, pricing, and hosted compiler docs

## Quick Start

```bash
pip install viewspec
viewspec prove --out .viewspec-proof
```

Start with `.viewspec-proof/PROOF.md`. The same directory contains the machine-readable
`proof_report.json`, redacted `support_bundle.json`, semantic source, generated artifact, and
provenance manifest.

For an authored brief, use the canonical three-command lifecycle:

```bash
viewspec init-intent --out viewspec.intent.json
# Edit viewspec.intent.json; optionally author DESIGN.md.
viewspec validate-intent viewspec.intent.json --json
viewspec prove --intent viewspec.intent.json --target react-tailwind-tsx --install --out .viewspec-proof --json
```

Run `viewspec init-design --out DESIGN.md` once when the repository does not already provide a
governed design file.

## Core Workflow Evidence

The checked core workflow is exercised against a fixed ten-case corpus covering app queue/detail,
collection and outcome states, dense operations, dashboards, forms, landing pages, multi-step
flows, and settings. All 10 cases render conformantly at canonical mobile, tablet, and desktop
viewports with screenshot, DOM, accessibility, and log evidence. All ten passed the product-quality
scorecard on first compile with zero critical issues. Each case also has one bounded semantic
correction with a verified preview and applied receipt.

See the [eight-gate result](https://github.com/nxrobins/viewspec/blob/main/conformance/refinement/gate-status-v1.json),
[product-quality scorecard](https://github.com/nxrobins/viewspec/blob/main/conformance/refinement/scorecard-v2.json),
and [correction proof](https://github.com/nxrobins/viewspec/blob/main/conformance/refinement/correction-proof-v1.json).
This is fixed-corpus evidence for supported brief families, not a guarantee that every arbitrary
product brief is desirable, accessible, or production-ready.

## What ViewSpec Does

The primary workflow is Intent-first compilation: semantic UI intent goes in, concrete renderer output plus a manifest comes out.

**Before ViewSpec:** Agents author DOM, CSS, and framework code directly. The model gets trapped in markup details instead of expressing structure, data, hierarchy, and interaction intent.

**After ViewSpec:** Agents author `IntentBundle` JSON. The compiler owns layout lowering, state generation, renderer output, design-token application, diagnostics, and provenance.

```
IntentBundle JSON -> ViewSpec compiler -> HTML / React / SwiftUI / Flutter / CompositionIR
       |-- validate agent contract
       |-- apply DESIGN.md
       |-- generate and replay TypeScript reducers (AppBundle V3/V4)
       |-- write provenance_manifest.json
       `-- keep DOM and framework code as compiler output
```

## Three Invariants

ViewSpec enforces three deterministic invariants:

1. **Exactly-once provenance.** Every valid data binding is routed exactly once. Conflicting or duplicate bindings are deterministically resolved (first occurrence wins) and flagged as a diagnostic — never silently dropped, duplicated, or hallucinated.
2. **Semantic grouping.** Data is grouped by meaning, not by visual adjacency.
3. **Strict ordering.** The original data order is preserved deterministically, including across serialization round-trips.

## AppBundle V4 & State IR

AppBundle V3 introduced bounded interactive state; V4 adds replay-proved visibility rules over that state:

* **Declarative Mutation IR**: Agents define state transitions (`set`, `patch`, `toggle`, `append`, `remove`, `move`, `increment`) in JSON.
* **Deterministic Reducer Generation**: The compiler generates a pure TypeScript `reduceViewSpecState` reducer.
* **State and Visibility Replay**: Assertions prove expected state plus `visibility_v0` outcomes against the generated reducer before browser use.

## New: Custom Motif Plugins

Extend the local compiler securely with a microkernel architecture:

* **MotifPluginManifest**: Define strict input slots, ABI versions, and output guarantees for enterprise motifs (e.g., `financial_candlestick_chart`).
* **IR Portability**: Custom plugins lower directly into standard `CompositionIR`. You don't need to write custom HTML/React emitters for your new motifs!
* **Registry Support**: Pass a `motif_registry` to the `compile` pipeline for reusable, safe plugin execution.

## Install

```bash
pip install viewspec
```

Requires Python 3.11+. AppBundle **V3/V4** (`interactive_state_v0`) reducer conformance additionally requires Node.js (>=18) on `PATH`; V1/V2 and all IntentBundle flows are Python-only and no-network.

Python package: <https://pypi.org/project/viewspec/>

Hosted compiler pricing starts with Free at 500 hosted compile calls/day. Pro is $149/month for 10,000 hosted compile calls/day and up to 5 custom motif instances per compile; Enterprise is custom volume and terms.

## Runnable React App Golden Path

Generate a checked AppBundle V4 incident console, compile it into a complete Vite/React/Tailwind app, and run it:

```bash
viewspec init-app --template react-app --out viewspec.app.json
viewspec compile-app viewspec.app.json --target react-tailwind-app --out app-dist
cd app-dist
npm ci
npm run dev
```

The generated app wires browser-history routes, host-provided resources with fixture fallback, AppBundle mutations, selectors, and visibility into the checked React screen artifacts. `ViewSpecApp` exposes typed `resources`, `onNavigate`, `onAction`, `onStateChange`, and `onError` host boundaries.

Edit `viewspec.app.json`, then regenerate with `--force`; do not edit generated React. Run the exact-artifact build and Chromium proof with:

```bash
viewspec prove-app --app viewspec.app.json --target react-tailwind-app --install
```

This bounded target is a runnable frontend app and host bridge. Authentication, persistence, arbitrary API clients, optimistic updates, and production infrastructure remain host-owned.

## IntentBundle-First Local Workflow

For a first proof, run:

```bash
viewspec prove --out .viewspec-proof
```

This generates a starter IntentBundle and DESIGN.md inside `.viewspec-proof/`, compiles through the public local path, runs artifact checks, records compact style-delta counts when profiles are present, and writes `.viewspec-proof/PROOF.md` for humans, `.viewspec-proof/proof_report.json` for tools, and `.viewspec-proof/support_bundle.json` for redacted local support triage. Read [ViewSpec Proof Bundle](https://github.com/nxrobins/viewspec/blob/main/docs/proof-bundle.md) when you need to interpret status, hashes, checks, failure codes, or local support triage. Machine reports include proof identity metadata under `proof_identity` for artifact, manifest, proof report, human summary, and support bundle hashes. It proves source artifact integrity and provenance for the generated artifact; ViewSpec prove is not pixel-perfect visual regression, accessibility certification, arbitrary host-app certification, or hosted compiler publish automation.

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
* **V4**: Adds bounded `visibility_v0` rules, baked initial visibility, and replay-proved `evaluateViewSpecVisibility` output.

`compile-app` defaults to a single `app-dist/index.html` Static Shell V0 proof artifact; that default is not browser navigation proof. Use `--target react-tailwind-app` for a runnable Vite/React/Tailwind app with browser-history routing, live resource/state rebinding, and exact-artifact host verification. Neither target generates authentication, persistence, arbitrary API clients, or backend infrastructure.

Aesthetic Profiles V1 are deterministic art-direction handles, not CSS: `aesthetic.calm_ops`, `aesthetic.premium_saas`, `aesthetic.data_dense`, `aesthetic.editorial_product`, `aesthetic.executive_review`, `aesthetic.brutalist`, `aesthetic.neon_cyber`, and `aesthetic.warm_organic`. Checked summaries expose compact style-delta counts and bounded layout deltas for profiled artifacts, not arbitrary CSS control, pixel-perfect visual proof, or design certification.

## Import Existing HTML

The raw HTML path is an import/fallback tool for existing HTML. It sanitizes active content, applies local `DESIGN.md` tokens, writes deterministic provenance, and can report semantic diffs.

```bash
viewspec compile input.html --design DESIGN.md --out dist/
viewspec lift input.html --out lift.json
viewspec diff old.html new.html --json
```

## ViewSpec Converge: Approved Semantic Changes

Converge Sessions V1 turn Review feedback or verifier failures into a durable sequence of bounded
IntentPatch proposals. The agent automatically starts or resumes the controller, chooses only from a
source-bound legal-operation menu, submits the patch, and re-verifies after apply; the human opens
Review, inspects the semantic before/after and progress proof, then approves or rejects.

Humans do not need to operate hashes, task ids, operation names, tool names, or approval tokens.
Agent-facing session responses withhold both write capabilities, and Review authorizes only the exact
preview shown in its authenticated current frame.

The following commands are expert/debug surfaces for integrations, not the normal operator workflow:

```bash
viewspec converge-start viewspec.intent.json context.json --json
viewspec converge-submit viewspec.intent.json change.intentpatch.json --json
viewspec converge-status viewspec.intent.json --json
```

The verifier accepts only strict set-wise progress: the candidate must remove at least one existing
error, introduce none, and use the identical complete verification plan. Sessions permit three
attempts over ten minutes, reject cycles and out-of-band source edits, apply through the existing
atomic IntentPatch receipt transaction, and fail closed on post-apply proof drift. See
[Converge Sessions V1](https://github.com/nxrobins/viewspec/blob/main/docs/converge-sessions-v1.md) and [IntentPatch V1](https://github.com/nxrobins/viewspec/blob/main/docs/intent-patch-v1.md).

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

Agent assets use schema version `13`, contract profile `local_v1`, and the same export/check commands shown above; exported files include the local intent schema, AppBundle schema, IntentPatch schema, Convergence Authoring Task schema, checked examples, prompt, and asset manifest without SDK network calls.

Optional **MCP tooling** is available behind the agent extra:

```bash
python -m pip install "viewspec[agents]"
viewspec mcp
```
The MCP server exposes all intent-first local tools without requiring shell commands, including `validate_intent_bundle_file`, `compile_intent_bundle_file`, `build_intent_patch_context`, `start_convergence`, `submit_convergence_patch`, `get_convergence_status`, `verify_host`, `prove`, `validate_app_file`, `diff_app_files`, `compile_app`, and `prove_app`. Convergence approval remains human-gated in Review; the expert `approve_convergence` tool can only consume an operator-supplied capability that agent-facing tools never reveal.

For rendered conformance, compile React/Tailwind TSX and run:

```bash
viewspec verify react-tailwind-output/ --install --json
```

The verifier renders canonical mobile, tablet, and desktop viewports and writes integrity-addressed
screenshots, DOM snapshots, accessibility evidence, `result.json`, and a deterministic `repair.json`.
Exit codes distinguish conformant (`0`), nonconformant (`1`), and indeterminate (`2`) results.
`viewspec verify-host` remains the lower-level bounded host assertion proof for
grid column/span counts, profiled aesthetic markers/layout, and action payload behavior.
Its JSON `assertion_requirements` records `dom_count`, `style_assertion_count`, and
`aesthetic_layout_assertion_count` expectations from the checked manifest.
`viewspec prove` combines compilation checks with that proof.

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
| [The Invariants](https://viewspec.dev/invariants/) | Watch the compiler enforce each deterministic invariant. |
| [15 Lines → Full UI](https://viewspec.dev/fifteen-lines/) | An invoice table builds itself from 15 lines of Python. |
| [Style Derivation](https://viewspec.dev/style-derivation/) | Toggle five visual presets deterministically. |
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

The hosted fallback requires the `remote` extra: `python -m pip install "viewspec[remote]"` (adds `httpx`). Without it, `compile_auto` runs locally and raises `ImportError` only if a hosted fallback is actually needed.

Paid agents can submit a complete AppBundle to `submit_verification_remote(...)` for compiled
route/state proof plus per-screen browser evidence and a signed receipt. For bounded autonomous
repair, `compile_until_conformant_remote(...)` repeats that paid compile-and-verify step while a
caller-owned repair callback edits the semantic AppBundle. ViewSpec enforces lineage, plan
stability, attempt limits, and no-progress termination.

### Theming with DESIGN.md
The local SDK uses a strict YAML-front-matter `DESIGN.md` for offline HTML and IntentBundle compilation. The API requires exact sRGB hex values (e.g., `#FFFFFF`), enforcing strict design token discipline.

## Compatibility & Versioning

The local contract is anchored by the `local_v1` profile: document schemas (IntentBundle V1, AppBundle V1-V4), the closed error-code registry (`viewspec.ERROR_CODES`), the CLI surface, every name in `viewspec.__all__`, and per-version determinism. Within a major version those surfaces evolve additively only — caps only rise, codes are never repurposed, commands are never removed — and anything breaking waits for a new major version with a new contract profile and migration notes. Cross-version artifact bytes and hashes are explicitly *not* promised; determinism is a within-version guarantee. The full policy, including what is deliberately out of scope, is in [docs/compatibility.md](https://github.com/nxrobins/viewspec/blob/main/docs/compatibility.md); changes ship documented in [CHANGELOG.md](https://github.com/nxrobins/viewspec/blob/main/CHANGELOG.md).

## License
MIT
