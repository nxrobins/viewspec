# Local HTML Wedge

The local HTML wedge is the beta SDK import/fallback path for existing HTML. For new UI, agents should emit `viewspec.intent.json` and use `viewspec validate-intent` before compiling.

```bash
viewspec compile input.html --design DESIGN.md --out dist/
viewspec lift input.html --out lift.json
viewspec diff old.html new.html --json
viewspec check dist/
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
- `manifest_schema_version`
- `kind`
- `sdk_version`
- `source_name`
- `raw_source_hash`
- `source_hash`
- `design_hash`
- `artifact_hash`
- `command`
- `command_args`
- `policy_version`
- `guarantees`
- `nodes`
- `diagnostics`
- `external_refs`

For raw HTML, `source_hash` is SHA-256 of the canonical lifted DOM token stream. It is not a raw file hash.
`raw_source_hash` is SHA-256 of the original input bytes interpreted as UTF-8 text. `artifact_hash` is SHA-256 of `index.html`.
`command_args` are normalized to avoid absolute paths, temp paths, and machine-local output directories.
The manifest envelope is fail-closed: `raw_html_compile` must use `command: compile_html`, policy `viewspec-raw-html-allowlist@1`, and `guarantees.decompilation: not_claimed`; `intent_bundle_compile` must use `command: compile`, policy `viewspec-intent-bundle@1`, and `guarantees.decompilation: not_applicable`.

Diagnostics have stable `severity`, `code`, and `message` fields. `node_id` and `path` may appear when the pipeline can locate the finding precisely.

`viewspec check <artifact_dir>` validates the manifest, artifact hash, diagnostics shape, manifest-to-DOM provenance links, and the no-autofetch policy.
