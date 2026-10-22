# Security Policy

## Reporting a vulnerability

Report suspected vulnerabilities privately through GitHub's private vulnerability reporting at
<https://github.com/nxrobins/viewspec/security/advisories/new>. Do not open a public issue for a
security report. You should receive an initial response within 7 days.

## Supported versions

Security fixes land on the latest release line only. Older pre-release versions (0.x betas) are
superseded by each newer release and do not receive backported fixes.

## Security posture

What the local SDK does — and deliberately does not do:

- **No network calls.** The local compiler, validators, prove, and agent-asset tooling perform
  zero network calls; every provenance manifest records `guarantees.sdk_network_calls: "none"`.
  Only the optional hosted client (`viewspec[remote]`) talks to the hosted compiler endpoint.
- **Static, sanitized artifacts.** Emitters produce static output and `viewspec check` rejects
  active surfaces: unknown or unclosed inline scripts, every `on*` attribute, `javascript:`,
  `vbscript:` and `data:` URLs (raster image `data:` URLs on `<img src>` excepted) after entity,
  whitespace and backslash decoding, CSS that imports or loads a URL (`@import`, `url(`,
  `image-set(`, `image(`, `src(`, remote schemes) after CSS escapes and comments are normalized,
  and remote embed, `feImage`, and `ping` surfaces. A raw-HTML artifact may contain only the fixed
  document wrapper plus manifest-governed elements whose attributes match the manifest and the
  sanitizer allowlist. Raw HTML import sanitizes active content under the published allowlist
  policy (`viewspec-raw-html-allowlist@1`). `viewspec check` verifies that an artifact is
  consistent with its own unsigned `provenance_manifest.json`; it does not establish who produced
  either file, because whoever edits the artifact can re-hash the manifest. `viewspec prove` adds
  a `proof_identity` (artifact, manifest, and proof report hashes) for that purpose.
- **Bounded static shells.** AppBundle Static Shell V0 rejects external network, embed, and
  script surfaces; the full shell HTML is scanned for inline handlers, `http:` URLs, `url(`,
  worker construction, and import maps before a proof can pass.
- **Path containment.** File tools resolve paths against the working directory and fail closed
  with `PATH_OUTSIDE_CWD` when a path escapes it. The MCP server (`viewspec mcp`) resolves its
  working directory first and confines reads and writes to it unless `--allow-outside-cwd` is
  passed explicitly. Treat that flag as a security boundary: enable it only for workspaces you
  trust. `--force` on `prove`, `prove-app`, and `compile-app` replaces only an empty directory or
  one that holds a ViewSpec-written marker such as `proof_report.json`; any other directory is
  refused rather than deleted.
- **Untrusted input is the normal case.** Validators are fail-closed with hard caps on bytes,
  counts, and depth, and reject unknown fields instead of ignoring them. DESIGN.md is capped at
  64 KiB, checked against its on-disk size before it is read, and its YAML front matter rejects
  anchors and aliases as well as duplicate keys. HTML inputs to `compile`, `lift`, and `diff` are
  size-gated (`HTML_INPUT_TOO_LARGE`) before they are read. Compiling a bundle still runs the
  compiler over that input, so apply ordinary OS-level sandboxing when processing bundles from
  sources you do not trust at all.

## Out of scope

- `viewspec prove` certifies source-artifact integrity and the declared invariants. It is not a
  penetration test, not an XSS audit of author-provided text content, and not a browser security
  guarantee.
- Vulnerabilities in dependencies (Python, Node.js, protobuf, PyYAML) belong upstream, though we
  will ship version-bump releases when a dependency fix affects ViewSpec users.
