# Proof

ViewSpec checks a source before it compiles and checks the artifact after. `viewspec prove` does
both and writes a proof bundle for a person and a tool to read.

| Command | Takes | Checks |
|---|---|---|
| `viewspec check FILE` | A source file | The contract: shape, ids, references, motif completeness, and caps. An IntentBundle gets the lossless fixes first. |
| `viewspec check DIR` | A compiled directory | The DOM against `provenance_manifest.json`, the artifact hash, and active content. |
| `viewspec prove [SOURCE]` | Nothing or a source file | Compiles, runs both checks, and writes the proof bundle. With no source, it proves a starter. |

The proof bundle holds three files. Read `PROOF.md` first. `proof_report.json` is the machine report.
`support_bundle.json` is a redacted copy that is safe to share when you ask for help.

`--install` allows a bounded `npm ci --ignore-scripts` and adds a Chromium run on every target but
`react-tsx`. At 390, 768, and 1440 pixels wide, the run checks that every manifest node is present,
grids and spans lay out as declared, nothing overflows or overlaps, and every control has a name. A
browser proof exits `0` for conformant, `1` for nonconformant, and `2` for indeterminate.
`viewspec prove DIR --install` runs the same browser proof on a directory that is already compiled.

## Example

Save this as `incidents.compact.json`:

```json viewspec
{
  "schema_version": 1,
  "format": "compact",
  "id": "incidents",
  "title": "Incidents",
  "motifs": [
    {"id": "intro", "kind": "hero", "title": "Open incidents", "description": "Sorted by severity."},
    {"id": "queue", "kind": "table", "rows": [
      {"id": "inc_402", "label": "INC-402", "value": "Database replica lag"},
      {"id": "inc_403", "label": "INC-403", "value": "Checkout latency"}
    ]}
  ]
}
```

Then check it, compile it, check the artifact, and prove the whole path:

```bash
viewspec check incidents.compact.json --json
viewspec compile incidents.compact.json --out dist/
viewspec check dist/
viewspec prove incidents.compact.json --out .viewspec-proof
```

`dist/index.html` is compiler output. To change it, edit `incidents.compact.json` and run the
commands again.

## What it does not prove

ViewSpec proof is not pixel-perfect visual regression, accessibility certification, or arbitrary
host-app certification. A browser proof runs the artifact in a reference host, not in your
application. `--pretext` measures text in Arial, not in your production font. The
[Proof Bundle](../proof-bundle.md) guide explains each field of the report.
