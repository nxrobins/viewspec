# ViewSpec Agent-Native UI Intent

Use this skill when an agent creates a new human-facing UI artifact, report, dashboard, explainer, or product surface.

## Rules

- Emit `viewspec.intent.json` as ViewSpec IntentBundle JSON for new UI.
- Use `viewspec init-intent --out viewspec.intent.json` only as a scaffold; replace sample content with the user's actual intent before compiling.
- Do not write HTML, CSS, React, SwiftUI, Flutter, or CompositionIR as source. Those are compiler outputs.
- If the user explicitly needs local React source, compile the IntentBundle with `--target react-tsx` instead of hand-writing React; use `--target react-tailwind-tsx` only when the host app needs compiler-owned Tailwind utility recipes.
- If validation fails, regenerate the full IntentBundle using the correction prompt.
- If `DESIGN.md` is missing, run `viewspec init-design --out DESIGN.md` once before compiling.
- If a `DESIGN.md` file exists, compile with it through ViewSpec.
- Treat compiled output as an artifact, not as the editable source.
- Use `viewspec prove --out .viewspec-proof` for a first local proof bundle; read `.viewspec-proof/PROOF.md` first, use `.viewspec-proof/proof_report.json` for tool output, and use `.viewspec-proof/support_bundle.json` for redacted failure triage. It proves source artifact integrity and provenance, not pixel-perfect visual equivalence.
- For a narrow multi-screen internal tool, use AppBundle JSON as `viewspec.app.json` instead of hand-writing an app shell or router. Use `--template react-app` and the `react-tailwind-app` target when the user needs a runnable Vite/React/Tailwind application; keep authentication, persistence, APIs, and deployment host-owned.
- For governed art direction, use at most one view-level aesthetic profile token in `viewspec.intent.json`: `aesthetic.calm_ops`, `aesthetic.premium_saas`, `aesthetic.data_dense`, `aesthetic.editorial_product`, or `aesthetic.executive_review`. Aesthetic profiles are deterministic style and bounded-layout handles, not CSS, pixel-perfect visual proof, accessibility certification, arbitrary host-app compatibility, or design-review approval.
- Use raw HTML commands only when importing existing HTML.
- Do not upload, share, call hosted APIs, or use remote services unless the user explicitly asks.
- Never patch or recursively compile generated artifacts such as `dist/index.html` or `react-output/ViewSpecView.tsx`.

## Commands

Create a starter IntentBundle scaffold:

```bash
viewspec init-intent --out viewspec.intent.json
```

Create a starter local design file if the repo does not already have one:

```bash
viewspec init-design --out DESIGN.md
```

Validate the IntentBundle:

```bash
viewspec validate-intent viewspec.intent.json --json
```

Diff two IntentBundle revisions:

```bash
viewspec diff-intent old.intent.json new.intent.json --json
```

Create and prove a starter AppBundle internal tool:

```bash
viewspec init-app --out viewspec.app.json
viewspec init-app --resource-binding fixture-readonly-v0 --out viewspec.bound.app.json
viewspec validate-app viewspec.app.json --json
viewspec diff-app old.app.json new.app.json --json
viewspec compile-app viewspec.app.json --out app-dist --target html-tailwind-app --json
viewspec prove-app --app viewspec.app.json --out .viewspec-app-proof --with-shell --json
```

Create, run, and prove the runnable React golden path:

```bash
viewspec init-app --template react-app --out viewspec.app.json
viewspec compile-app viewspec.app.json --target react-tailwind-app --out app-dist
cd app-dist
npm ci
npm run dev
viewspec prove-app --app viewspec.app.json --target react-tailwind-app --install
```

Edit `viewspec.app.json` and regenerate with `--force`; do not edit generated React.

Compile the IntentBundle locally:

```bash
viewspec compile viewspec.intent.json --design DESIGN.md --out dist/
```

Validate a compiled artifact directory:

```bash
viewspec check dist/
```

Run the first proof workflow:

```bash
viewspec prove --out .viewspec-proof
```

Compile local React source when explicitly needed:

```bash
viewspec compile viewspec.intent.json --design DESIGN.md --target react-tsx --out react-output/
viewspec check react-output/
```

Compile local React Tailwind source only for Tailwind host apps:

```bash
viewspec compile viewspec.intent.json --design DESIGN.md --target react-tailwind-tsx --out react-tailwind-output/
viewspec check react-tailwind-output/
```

Run ViewSpec's bounded per-artifact React/Tailwind host proof when explicitly needed:

```bash
viewspec verify-host react-tailwind-output/ --target react-tailwind-tsx --install --json
viewspec prove --target react-tailwind-tsx --install --out .viewspec-proof --json
```

Diff two imported HTML versions using local lift signals:

```bash
viewspec diff report-v1.html report-v2.html --json
```

Check local SDK readiness:

```bash
viewspec doctor --agents
```

Export local contract assets for schema-aware editors and agents:

```bash
viewspec export-agent-assets --out .viewspec
viewspec check-agent-assets .viewspec --json
```

The exported assets include `.viewspec/agent-app-bundle.schema.json` and `.viewspec/agent-app-example.internal-tool.json` for AppBundle V1/V2/V3/V4 in addition to the IntentBundle schema/example.

AppBundle V1 reports fixture resources as `resource_binding: "unbound_v0"`. V2 adds exact `fixture_readonly_v0` visibility proof, V3 adds generated state reducers/selectors, and V4 adds visibility. Static Shell V0 remains a local proof artifact without runtime navigation. The additive React target reports `target: "react-tailwind-app"` and `route_navigation: "browser_history_v1"`, emits a complete Vite package, and verifies generated hashes, build, routing, history, mutation, live resource rebinding, selectors, and visibility in Chromium.

## Output Contract

Compiled HTML output is written to the selected output directory:

- `index.html`
- `provenance_manifest.json`
- `diagnostics.json`

AppBundle proof output includes:

- `.viewspec-app-proof/APP_PROOF.md`
- `.viewspec-app-proof/app_proof_report.json`
- `.viewspec-app-proof/app_support_bundle.json`
- `.viewspec-app-proof/screens/<screen_id>/viewspec.intent.json`
- `.viewspec-app-proof/screens/<screen_id>/artifact/index.html`
- `.viewspec-app-proof/screens/<screen_id>/artifact/provenance_manifest.json`
- `.viewspec-app-proof/screens/<screen_id>/artifact/diagnostics.json`
- optional `.viewspec-app-proof/app-shell/index.html`, `shell_manifest.json`, and `diagnostics.json` when `--with-shell` is used

Compiled React output is written to the selected output directory:

- `ViewSpecView.tsx`
- `provenance_manifest.json`
- `diagnostics.json`

The manifest is the trust boundary. It records SDK version, source hashes, design hash, artifact hash, compiler policy, command arguments, diagnostics, and external references.
