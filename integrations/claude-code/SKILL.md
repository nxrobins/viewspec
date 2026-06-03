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

Compile the IntentBundle locally:

```bash
viewspec compile viewspec.intent.json --design DESIGN.md --out dist/
```

Validate a compiled artifact directory:

```bash
viewspec check dist/
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

## Output Contract

Compiled HTML output is written to the selected output directory:

- `index.html`
- `provenance_manifest.json`
- `diagnostics.json`

Compiled React output is written to the selected output directory:

- `ViewSpecView.tsx`
- `provenance_manifest.json`
- `diagnostics.json`

The manifest is the trust boundary. It records SDK version, source hashes, design hash, artifact hash, compiler policy, command arguments, diagnostics, and external references.
