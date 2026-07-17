# Getting Started: First ViewSpec In 5 Minutes

ViewSpec's primary workflow is agent-native UI intent. Your agent writes `IntentBundle` JSON, ViewSpec validates it, then the compiler emits HTML and other concrete UI surfaces.

## Install

```bash
pip install viewspec
```

## First Proof

Run the one-command proof path before learning the full workflow:

```bash
viewspec prove --out .viewspec-proof
```

This writes a starter `viewspec.intent.json`, starter `DESIGN.md`, checked artifact output, human-readable `PROOF.md`, machine-readable `proof_report.json`, and redacted `support_bundle.json` under `.viewspec-proof/`. Read [ViewSpec Proof Bundle](proof-bundle.md) when you need to interpret status, hashes, checks, failure codes, or local support triage. The default proof is Python-only and no-network; it proves source artifact integrity and provenance. ViewSpec prove is not pixel-perfect visual regression, accessibility certification, arbitrary host-app certification, or hosted compiler publish automation.

## Runnable React App Golden Path

Use the checked AppBundle V4 starter when the goal is a running multi-screen React application rather than a source proof:

```bash
viewspec init-app --template react-app --out viewspec.app.json
viewspec compile-app viewspec.app.json --target react-tailwind-app --out app-dist
cd app-dist
npm ci
npm run dev
```

The target writes a complete Vite/React/Tailwind package. Browser-history routes, generated reducer events, resource-backed text, selectors, and visibility update at runtime. This is bounded browser navigation and frontend-state proof; host applications can pass resource records and typed callbacks through `ViewSpecAppProps`.

Make changes in `viewspec.app.json`, review them with `viewspec diff-app`, and recompile with `--force`; do not edit generated React. Prove the exact generated package in Chromium with:

```bash
viewspec prove-app --app viewspec.app.json --target react-tailwind-app --install
```

The proof runs `npm ci --ignore-scripts`, a Vite production build, and generated Playwright assertions for every static route, browser history, the unknown-route fallback, declared state actions, live resource rebinding, selector replay, and visibility. Authentication, persistence, arbitrary API clients, and production infrastructure remain host-owned.

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

`init-intent` writes a valid scaffold for table, dashboard, outline, comparison, list, form, detail, empty_state, loading_state, error_state, or hero motifs. Replace the sample content with real user intent before compiling. `validate-intent` rejects arrays, markdown-wrapped JSON, malformed JSON, CompositionIR-shaped payloads, oversized bundles, and bundles unsupported by the local reference compiler. `viewspec compile` runs the same validation before writing artifacts. Invalid results include a correction prompt for the agent to regenerate the full IntentBundle.

Use `viewspec diff-intent` when reviewing agent revisions. It reports `basis: "intent_bundle_v1"` and compares top-level bundle metadata, declared semantic nodes, regions, bindings, groups, motifs, styles, actions, selected field changes, and `semantic_changes` for region, group, motif, aesthetic profile, style, binding, and action contract changes before you inspect generated DOM. Human output prints concise section and semantic summaries; Python callers can use `intent_semantic_change_lines(diff["semantic_changes"])`; `--json` returns the full machine-readable payload.

V1 local caps keep agent repair loops predictable: max 256KB JSON, 200 substrate nodes, 32 regions, 400 bindings, 64 groups, 32 motifs, 400 styles, 64 actions, 64 attrs/slots/edges per node, 200 values per slot or edge, and 64 payload bindings per action.

Use `viewspec init-design --out DESIGN.md` for a starter design file when the repo does not already have one, and `viewspec doctor` to check local SDK readiness. `doctor` reports the intent-first commands, runs starter IntentBundle validation/compile/diff, aesthetic-profile diff, and semantic summary smoke checks, verifies `PyYAML`, and states the local no-network policy. It also reports Node.js availability, which is required only for AppBundle V3/V4 (`interactive_state_v0`) reducer conformance; V1/V2 and all IntentBundle flows are Python-only.

## AppBundle V1/V2/V3/V4

For a narrow multi-screen internal-tool contract, use AppBundle JSON. It keeps app generation at the contract/proof layer: embedded screen `IntentBundle`s, static routes, fixture resources, validation, semantic diffing, and per-screen checked `html-tailwind` artifacts. `schema_version: 1` reports `resource_binding: "unbound_v0"`; `schema_version: 2` adds proof-only `resource_binding: "fixture_readonly_v0"` and declared `resource_views`; `schema_version: 3` adds bounded `interactive_state_v0` state, mutations, selectors, replay assertions, and a generated pure TypeScript reducer; `schema_version: 4` adds bounded `visibility` rules with baked initial show/hide markers, `expect_visibility` replay proof, and an `evaluateViewSpecVisibility` reducer export.

```bash
viewspec init-app --out viewspec.app.json
viewspec init-app --resource-binding fixture-readonly-v0 --out viewspec.bound.app.json
viewspec validate-app viewspec.app.json --json
viewspec diff-app old.app.json new.app.json --json
viewspec compile-app viewspec.app.json --out app-dist --target html-tailwind-app --json
viewspec prove-app --app viewspec.app.json --out .viewspec-app-proof --with-shell --json
```

`prove-app` writes `APP_PROOF.md`, `app_proof_report.json`, `app_support_bundle.json`, and one checked screen artifact directory per screen. The default source proof and `--with-shell` remain bounded proof-artifact paths. The `react-tailwind-app` target additionally proves its exact generated package in the reference React host when `--install` is supplied.

Static Shell V0 is the bounded local shell artifact for this contract. `compile-app` writes `app-dist/index.html`, `shell_manifest.json`, `diagnostics.json`, checked screen artifacts, and for V3 `state_reducer.ts` plus `state_manifest.json`; reports `target: "html-tailwind-app"` and `route_navigation: "static_shell_v0"`; rejects external network/embed/script surfaces; and remains a local proof artifact, not a deployable framework app, live DOM rebinding layer, framework state adapter, persistence layer, browser-history proof, accessibility certification, or cross-browser visual proof.

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
viewspec check-agent-assets .viewspec --json
```

The asset manifest uses schema version `12`, declares the `local_v1` contract profile, and records the export/check commands. It includes IntentBundle, AppBundle, and IntentPatch schemas plus checked examples. Run the check command before reusing cached `.viewspec` assets.

For MCP-capable agents:

```bash
python -m pip install "viewspec[agents]"
viewspec mcp
viewspec doctor --agents
```

The MCP tools are local-only by default and reject paths outside the configured working directory. Intent tools are the default for new UI; `init_app`, `validate_app_file`, `diff_app_files`, `compile_app`, and `prove_app` cover AppBundle V1/V2/V3 and Static Shell V0; raw HTML MCP tools are import/fallback only. MCP also exposes `export_agent_assets` and `check_agent_assets` for local prompt, schema, valid example, and asset manifest workflows.

Treat compiled output directories as generated artifacts. Edit `viewspec.intent.json` or `DESIGN.md`, then re-run compile and check; do not patch `dist/index.html` or `react-output/ViewSpecView.tsx` by hand.

## Programmatic IntentBundle Compile

```python
import json

from viewspec import ViewSpecBuilder, compile, diff_intent_text, intent_semantic_change_lines, validate_intent_text
from viewspec.emitters.html_tailwind import HtmlTailwindEmitter
from viewspec.emitters.react_tailwind_tsx import ReactTailwindTsxEmitter
from viewspec.emitters.react_tsx import ReactTsxEmitter

builder = ViewSpecBuilder("invoice")
builder.set_aesthetic_profile("aesthetic.calm_ops")
table = builder.add_table("items", region="main", group_id="rows")
table.add_row(label="Design System Audit", value="$4,200")
table.add_row(label="Component Library", value="$8,500")

message_binding = builder.add_text_input("message", label="Message", value="")
builder.add_action("send", "submit", "Send", target_region="main", payload_bindings=[message_binding])

bundle = builder.build_bundle()
validation = validate_intent_text(json.dumps(bundle.to_json()))
diff = diff_intent_text(json.dumps(bundle.to_json()), json.dumps(bundle.to_json()))
semantic_summary = intent_semantic_change_lines(diff["semantic_changes"])
ast = compile(bundle)
HtmlTailwindEmitter().emit(ast, "output/")
ReactTsxEmitter().emit(ast, "react-output/")
ReactTailwindTsxEmitter().emit(ast, "react-tailwind-output/")
```

The local reference compiler supports safe text inputs, loading/error state motifs, and local action payload events. HTML action events dispatch `viewspec-action` with `detail.schemaVersion: 1`, `source`, `id`, `kind`, `targetRef`, `payloadBindings`, and `payloadValues`. Collection actions for table/list motifs dispatch `search`, `filter`, `sort`, `paginate`, and `bulk_action` events only; they do not locally mutate data. Pressing Enter inside a local inert form dispatches only a declared `submit` action whose `targetRef` exactly matches that form motif. React TSX output uses an `onAction` callback with the same V1 fields and `source: "viewspec-react-tsx"`.

Use `builder.set_aesthetic_profile("aesthetic.calm_ops")` when you want governed art direction without authoring CSS. V1 supports exactly one view-level profile per IntentBundle: `aesthetic.calm_ops`, `aesthetic.premium_saas`, `aesthetic.data_dense`, `aesthetic.editorial_product`, `aesthetic.executive_review`, `aesthetic.brutalist`, `aesthetic.neon_cyber`, or `aesthetic.warm_organic`; profiles are deterministic style and bounded-layout handles, not CSS, pixel-perfect visual proof, accessibility certification, arbitrary host-app compatibility, or design-review approval.

From the CLI, use `--target react-tsx` when you want component source instead of standalone HTML:

```bash
viewspec compile viewspec.intent.json --target react-tsx --out react-output/
```

Use `--target react-tailwind-tsx` when the host React app already has Tailwind and needs compiler-owned utility classes:

```bash
viewspec compile viewspec.intent.json --target react-tailwind-tsx --out react-tailwind-output/
```

The ViewSpec repo CI includes one bounded host proof for this target: it regenerates a representative fixture, runs `viewspec check`, mounts the exact checked `ViewSpecView.tsx` in a Vite/Tailwind host, builds it, and smoke-tests rendered DOM, computed Tailwind styles including grid column/span counts, profiled aesthetic marker/layout assertions, and action payloads in Chromium. The checked manifest summary also carries compact aesthetic style-delta counts when a profile is present. That proof is not run for every user artifact; `viewspec check` verifies source artifact integrity and provenance for the output you compiled.

Run the same bounded proof for a specific artifact with:

```bash
viewspec verify-host react-tailwind-output/ --target react-tailwind-tsx --install --json
viewspec prove --target react-tailwind-tsx --install --out .viewspec-proof --json
```

`--install` opts into `npm ci --ignore-scripts` inside the isolated reference host. Without it, the command stays no-install and fails fast when host dependencies are absent. JSON proof reports include `assertion_requirements` for expected `dom_count`, `style_assertion_count`, and manifest-derived `aesthetic_layout_assertion_count`, `aesthetic_profile_assertion_count`, and `grid_span_assertion_count` before comparing observed browser assertions.

`viewspec check` also verifies React TSX source artifacts: manifest shape, exact `ViewSpecView.tsx` hash, generated-source markers, diagnostics shape, and absence of active network/runtime escape surfaces. This is source artifact verification, not a rendered DOM proof inside a host React app. Use the hosted compiler for richer input controls, projections, declarative rules, custom motifs, Level 2+ derivation, and DESIGN.md context. Paid callers can use `compile_artifact_remote(...)` to receive integrity-checked HTML, React TSX, SwiftUI, or Flutter files. Hosted demo artifact indexes declare `contract_profile: "hosted_extended_v1"` when their IntentBundle uses fields beyond local V1 validation.

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
