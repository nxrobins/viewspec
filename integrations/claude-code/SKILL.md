# ViewSpec Local HTML Governance

Use this skill when an agent creates or edits an HTML report, explainer, architecture note, dashboard, or other human-readable artifact.

## Rules

- Emit plain HTML first. Do not emit framework code unless the user asks for it.
- Keep the source HTML local. Do not upload, fetch, or call remote services.
- If a `DESIGN.md` file exists, compile with it.
- Treat compiled output as a governed artifact, not as the source file.
- Use `viewspec diff` before summarizing changes between two HTML versions.

## Commands

Compile an HTML artifact locally:

```bash
viewspec compile report.html --design DESIGN.md --out dist/
```

Compile without a design file:

```bash
viewspec compile report.html --out dist/
```

Diff two HTML versions using local lift signals:

```bash
viewspec diff report-v1.html report-v2.html --json
```

Validate a compiled artifact directory:

```bash
viewspec check dist/
```

Create a starter local design file:

```bash
viewspec init-design --out DESIGN.md
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
- optional `lift.json` when `--lift-json` is used

The manifest is the trust boundary. It records SDK version, source hashes, design hash, artifact hash, sanitizer policy, command arguments, diagnostics, and external references.
