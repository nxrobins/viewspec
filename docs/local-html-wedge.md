# Local HTML Wedge

The local HTML wedge is the beta SDK path for existing HTML:

```bash
viewspec compile input.html --design DESIGN.md --out dist/
viewspec lift input.html --out lift.json
viewspec diff old.html new.html --json
```

It is intentionally not ViewSpec decompilation. The guarantee is sanitize + theme + manifest + semantic diff.

## Offline Contract

- SDK execution for `compile`, `lift`, and `diff` performs no network calls.
- Generated raw-HTML artifacts do not auto-fetch network resources when opened.
- External anchors remain clickable with `rel="noopener noreferrer"`.
- Remote image `src` values are replaced with inert external-image links and recorded in `external_refs`.

## Manifest v1

`provenance_manifest.json` follows the minimal schema in `docs/manifest-v1.schema.json`.

Required fields:

- `version`
- `kind`
- `source_name`
- `source_hash`
- `command`
- `guarantees`
- `nodes`
- `diagnostics`
- `external_refs`

For raw HTML, `source_hash` is SHA-256 of the canonical lifted DOM token stream. It is not a raw file hash.

Diagnostics have stable `severity`, `code`, and `message` fields. `node_id` and `path` may appear when the pipeline can locate the finding precisely.
