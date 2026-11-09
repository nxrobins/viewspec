---
name: viewspec
description: Write human-facing UI (a dashboard, report, table, form, explainer, product surface, or multi-screen internal tool) as ViewSpec JSON, then check, compile, and prove it with the local viewspec CLI or its MCP tools. Use when the user asks for new UI, or for a change to UI, in a project that uses ViewSpec, or when viewspec.compact.json, viewspec.intent.json, or viewspec.app.json is present. Change the JSON source, never the compiled HTML or React.
---

# ViewSpec

ViewSpec compiles semantic JSON into checked HTML or React. You write the JSON source as an
IntentBundle, or as an AppBundle for a multi-screen tool. ViewSpec writes the HTML or React, checks
it, and proves it. Everything runs locally, and the SDK makes no network calls.

## Rules

### Write the source

- Write new UI in the compact form by default: `viewspec.compact.json`, with `"format": "compact"`
  at the root. It is an IntentBundle written as the Python builder's calls.
- Write the canonical IntentBundle, `viewspec.intent.json`, only for what the compact form cannot
  express. The common case is a `bulk_action`, which needs a hand-declared selection binding.
- Use `viewspec init compact --out viewspec.compact.json` or `viewspec init --out viewspec.intent.json`
  only as a scaffold. Replace the sample content with the user's actual intent before compiling.
- Do not write HTML, CSS, React, SwiftUI, Flutter, or CompositionIR as source. Those are compiler outputs.
- Treat compiled output as an artifact, not as the editable source.
- Never patch or recursively compile generated artifacts such as `dist/index.html` or
  `react-output/ViewSpecView.tsx`.
- Use raw HTML input only when importing existing HTML.

### Repair the source

- Run `viewspec check FILE --json` after every edit. Compile only after `ok` is true.
- `check` and `compile` apply lossless fixes first and list each one under `fixes`. A fix never
  changes what the compiler emits. ViewSpec never fixes a lossy problem: that stays an issue for you.
- When issues remain, use one of two repairs. Regenerate the full source from `correction_prompt`,
  which lists only the remaining issues. Or write an RFC 6902 JSON Patch and pass it with
  `--patch OPS.json`.
- On a compact source, patch pointers name the compact document, such as `/motifs/0/cards/1/value`.
  `fixes` and canonical issues name the canonical bundle instead. `--canonical-out viewspec.intent.json`
  writes that bundle, and `source_format` in the report says which document a path names.
- Keep editing the compact file. The bundle that `--canonical-out` writes is a copy for reading paths.
- To revise existing source, regenerate it or apply a JSON Patch, then run `viewspec check` on the file.

### Show and prove

- Use `viewspec prove --out .viewspec-proof` for a first local proof bundle. Read
  `.viewspec-proof/PROOF.md` first. Use `.viewspec-proof/proof_report.json` for tool output and
  `.viewspec-proof/support_bundle.json` for redacted failure triage. It is not pixel-perfect visual
  regression, accessibility certification, or arbitrary host-app certification.
- Before you claim the UI is valid, prove the source itself, such as
  `viewspec prove viewspec.compact.json --out .viewspec-proof`.
- To show a person the result while you iterate, start `viewspec studio` on the source in the
  background. It prints the URL and rebuilds on every edit.
- When a person points at an element in Studio, ask for its inspector path. Hovering an element
  shows the JSON path of its source, such as `$.view_spec.bindings[3]`, and a click copies it. For a
  compact source, the path names the canonical bundle that `--canonical-out` writes.
- To look an element up yourself, read `/__viewspec/inspector.json?id=ID` from the Studio URL. `ID`
  is a `data-ir-id`, binding id, or action id.

### React targets and the registry

- If the user explicitly needs local React source, compile the source with `--target react-tsx`
  instead of hand-writing React. Use `--target react-tailwind-tsx` only when the host app already has
  Tailwind.
- A React component registry is host input. Never write or edit a registry or the components it
  names. Pass `--registry REGISTRY.json` only when the host already has one.

### Design and style

- If `DESIGN.md` is missing, run `viewspec init design --out DESIGN.md` once before compiling.
- If a `DESIGN.md` file exists, compile with it through ViewSpec.
- For governed art direction, use at most one view-level aesthetic profile token: `aesthetic.calm_ops`,
  `aesthetic.premium_saas`, `aesthetic.data_dense`, `aesthetic.editorial_product`,
  `aesthetic.executive_review`, `aesthetic.brutalist`, `aesthetic.neon_cyber`, or
  `aesthetic.warm_organic`. In the compact form, set it as `aesthetic`. These tokens are
  deterministic art-direction handles, not CSS.

### Multi-screen apps

- For a narrow multi-screen internal tool, use AppBundle JSON as `viewspec.app.json` instead of
  hand-writing an app shell or router. Write each screen as a canonical IntentBundle.
- Resources are `unbound_v0` or `fixture_readonly_v0`.
- The default target is Static Shell V0 with bounded browser navigation. Use `--template react-app`
  and the `react-tailwind-app` target for a runnable React app.
- Use `schema_version` 5 only when a screen needs composed visibility (`all`, `any`, `not`), several
  placeholders in one state text, or a closed formatter. Otherwise keep the starter's version.
- When the generated React app uses ViewSpec's supported numeric operations and the operator wants
  the additional proof, run
  `viewspec prove viewspec.app.json --target react-tailwind-app --install --freerange`. The operator
  must install stable Bun 1.x or newer. Never claim that ViewSpec or `--install` installs Bun. Treat
  the result as generated numeric-kernel analysis, not CSS, Tailwind, rendered-geometry, or
  arbitrary-host proof.
- When the operator wants compiler-owned native text-wrapping evidence, run
  `viewspec prove viewspec.app.json --target react-tailwind-app --install --pretext`. Pretext needs no
  Bun. Treat it as a loaded-font Chromium proof of the supported native-DOM profile, not a
  cross-browser, Retina, canvas-rendering, pixel-perfect, accessibility, or arbitrary-host proof.

### Boundaries

- Do not upload, share, call hosted APIs, or use remote services unless the user explicitly asks.
- When the `viewspec` MCP server is connected, call its tools instead of the shell: `init`,
  `compile`, `check`, `patch`, `prove`, and `studio`.

## Commands

Create a starter in the compact form, and a `DESIGN.md` when the project has none:

```bash
viewspec init compact --out viewspec.compact.json
viewspec init design --out DESIGN.md
```

Validate, compile, and check it:

```bash
viewspec check viewspec.compact.json --json
viewspec compile viewspec.compact.json --design DESIGN.md --out dist/
viewspec check dist/
```

Repair it with a JSON Patch. This one sets the first card's value in the starter:

```bash
cat > ops.json <<'EOF'
[{"op": "replace", "path": "/motifs/0/cards/0/value", "value": "$13.1K"}]
EOF
viewspec check viewspec.compact.json --patch ops.json --patched-out viewspec.compact.json --json
```

Write the canonical bundle that `fixes` and canonical issues name:

```bash
viewspec check viewspec.compact.json --json --canonical-out viewspec.intent.json
```

The commands below name a canonical `viewspec.intent.json`. When the compact file is your source,
pass `viewspec.compact.json` instead.

Compile local React source when the user explicitly needs it:

```bash
viewspec compile viewspec.intent.json --design DESIGN.md --target react-tsx --out react-output/
viewspec check react-output/
viewspec compile viewspec.intent.json --design DESIGN.md --target react-tailwind-tsx --out react-tailwind-output/
viewspec prove react-tailwind-output/ --install --json
```

Prove a starter before there is any source:

```bash
viewspec prove --out .viewspec-proof
```

Prove the source in Chromium when the user allows `npm ci --ignore-scripts`:

```bash
viewspec prove viewspec.intent.json --target react-tailwind-tsx --install --out .viewspec-proof --json
```

Both write `.viewspec-proof`. Pass `--force` to replace an earlier proof there.

Show a person the result while iterating. This serves a preview on loopback with the inspector:

```bash
viewspec studio viewspec.compact.json --no-open --json
```

Build a multi-screen AppBundle:

```bash
viewspec init app --out viewspec.app.json
viewspec check viewspec.app.json --json
viewspec compile viewspec.app.json --target html-tailwind-app --out app-dist/
viewspec prove viewspec.app.json --with-shell --out .viewspec-app-proof --json
```
