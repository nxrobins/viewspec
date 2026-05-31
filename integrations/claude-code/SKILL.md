# ViewSpec Agent-Native UI Intent

Use this skill when an agent creates a new human-facing UI artifact, report, dashboard, explainer, or product surface.

## Rules

- Emit `viewspec.intent.json` as ViewSpec IntentBundle JSON for new UI.
- Use `viewspec init-intent --out viewspec.intent.json` only as a scaffold; replace sample content with the user's actual intent before compiling.
- Do not write HTML, CSS, React, SwiftUI, Flutter, or CompositionIR as source unless the user explicitly asks for emitter code.
- If validation fails, regenerate the full IntentBundle using the correction prompt.
- If `DESIGN.md` is missing, run `viewspec init-design --out DESIGN.md` once before compiling.
- If a `DESIGN.md` file exists, compile with it through ViewSpec.
- Treat compiled output as an artifact, not as the editable source.
- Use raw HTML commands only when importing existing HTML.

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

Diff two imported HTML versions using local lift signals:

```bash
viewspec diff report-v1.html report-v2.html --json
```

Check local SDK readiness:

```bash
viewspec doctor
```

## Output Contract

Compiled output is written to the selected output directory:

- `index.html`
- `provenance_manifest.json`
- `diagnostics.json`

The manifest is the trust boundary. It records SDK version, source hashes, design hash, artifact hash, compiler policy, command arguments, diagnostics, and external references.
