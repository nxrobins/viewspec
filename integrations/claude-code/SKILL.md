# ViewSpec Agent-Native UI Intent

Use this skill when an agent creates a new human-facing UI artifact, report, dashboard, explainer, or product surface.

## Rules

- Emit `viewspec.intent.json` as ViewSpec IntentBundle JSON for new UI.
- Use `viewspec init --out viewspec.intent.json` only as a scaffold; replace sample content with the user's actual intent before compiling.
- Do not write HTML, CSS, React, SwiftUI, Flutter, or CompositionIR as source. Those are compiler outputs.
- If the user explicitly needs local React source, compile the IntentBundle with `--target react-tsx` instead of hand-writing React; use `--target react-tailwind-tsx` only when the host app already has Tailwind.
- If validation fails, regenerate the full IntentBundle using the correction prompt.
- To revise existing source, regenerate the bundle or apply a standard JSON Patch, then run `viewspec check` on the file.
- If `DESIGN.md` is missing, run `viewspec init design --out DESIGN.md` once before compiling.
- If a `DESIGN.md` file exists, compile with it through ViewSpec.
- Treat compiled output as an artifact, not as the editable source.
- Use `viewspec prove --out .viewspec-proof` for a first local proof bundle; read `.viewspec-proof/PROOF.md` first, use `.viewspec-proof/proof_report.json` for tool output, and `.viewspec-proof/support_bundle.json` for redacted failure triage. It is not pixel-perfect visual regression, accessibility certification, or arbitrary host-app certification.
- For a narrow multi-screen internal tool, use AppBundle JSON as `viewspec.app.json` instead of hand-writing an app shell or router. Resources are `unbound_v0` or `fixture_readonly_v0`. The default target is Static Shell V0 with bounded browser navigation; use `--template react-app` and the `react-tailwind-app` target for a runnable React app.
- When the generated React AppBundle uses ViewSpec's supported numeric operations and the operator wants the additional proof, use `viewspec prove viewspec.app.json --target react-tailwind-app --install --freerange`. Require the operator to install stable Bun 1.x or newer explicitly; never claim ViewSpec or `--install` installs Bun. Treat this as generated numeric-kernel analysis, not CSS/Tailwind, rendered-geometry, or arbitrary-host proof.
- When the operator wants compiler-owned native text-wrapping evidence, use `viewspec prove viewspec.app.json --target react-tailwind-app --install --pretext`. Pretext needs no Bun; treat it as an exact loaded-font Chromium proof for the supported native-DOM profile, not cross-browser/Retina, canvas-rendering, pixel-perfect, accessibility, or arbitrary-host proof.
- For governed art direction, use at most one view-level aesthetic profile token in `viewspec.intent.json`: `aesthetic.calm_ops`, `aesthetic.premium_saas`, `aesthetic.data_dense`, `aesthetic.editorial_product`, `aesthetic.executive_review`, `aesthetic.brutalist`, `aesthetic.neon_cyber`, or `aesthetic.warm_organic`. These are deterministic art-direction handles, not CSS.
- Use raw HTML input only when importing existing HTML.
- Do not upload, share, call hosted APIs, or use remote services unless the user explicitly asks.
- Never patch or recursively compile generated artifacts such as `dist/index.html` or `react-output/ViewSpecView.tsx`.

## Commands

Create a starter IntentBundle scaffold:

```bash
viewspec init --out viewspec.intent.json
```

Validate, compile, and check it:

```bash
viewspec check viewspec.intent.json --json
viewspec compile viewspec.intent.json --design DESIGN.md --out dist/
viewspec check dist/
```

Compile local React source when the user explicitly needs it:

```bash
viewspec compile viewspec.intent.json --design DESIGN.md --target react-tsx --out react-output/
viewspec check react-output/
viewspec compile viewspec.intent.json --design DESIGN.md --target react-tailwind-tsx --out react-tailwind-output/
viewspec prove react-tailwind-output/ --install --json
```

Prove the result:

```bash
viewspec prove --out .viewspec-proof
viewspec prove viewspec.intent.json --target react-tailwind-tsx --install --out .viewspec-proof --json
```

Show a person the result while iterating. This serves a preview on loopback and rebuilds on every edit:

```bash
viewspec studio viewspec.intent.json --no-open --json
```

Build a multi-screen AppBundle:

```bash
viewspec init app --out viewspec.app.json
viewspec check viewspec.app.json --json
viewspec compile viewspec.app.json --target html-tailwind-app --out app-dist/
viewspec prove viewspec.app.json --with-shell --out .viewspec-app-proof --json
```
