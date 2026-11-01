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

- **No network calls.** The compiler, validators, and prove perform zero network calls; every
  provenance manifest records `guarantees.sdk_network_calls: "none"`. The only registry access is
  the `npm ci --ignore-scripts` that `viewspec prove --install` explicitly authorizes.
- **Static, sanitized artifacts.** Emitters produce static output and `viewspec check` rejects
  active surfaces: unknown or unclosed inline scripts, every `on*` attribute, `javascript:`,
  `vbscript:` and `data:` URLs (raster image `data:` URLs on `<img src>` excepted) after entity,
  whitespace and backslash decoding, CSS that imports or loads a URL (`@import`, `url(`,
  `image-set(`, `image(`, `src(`, remote schemes) after CSS escapes and comments are normalized,
  remote embed, `feImage`, and `ping` surfaces, and auto-fetching `http:`/`https:` URLs written
  with or without `//` (detected inside tags, not in visible prose). It also rejects, with
  parser-independent raw-text rules, markup that browsers and Python's `html.parser` tokenize
  differently (`CHECK_AMBIGUOUS_MARKUP`), since a browser can run as a live element what the check
  parser reads as inert: comments, `<![CDATA[` and other `<![` sections, declarations other than
  `<!DOCTYPE html>`, and processing instructions; the raw-text, RCDATA and scripting elements
  `noscript`, `textarea`, `xmp`, `noembed`, `noframes`, `plaintext` and `iframe` (`noscript`
  differs on every version; the rest are text elements only on newer 3.11-3.14 patch releases);
  `<title>`, `<style>` or `<script>` carrying nested markup or a closer with a solidus or
  attributes (which older `html.parser` ends in a different place); a `<style>`/`<title>`/raw-text
  element inside `<svg>` or `<math>`; and a NUL byte. No emitter writes any of these. A raw-HTML
  artifact may contain only the fixed document wrapper plus manifest-governed elements whose
  attributes match the manifest and the sanitizer allowlist. Raw HTML import sanitizes active content under the published allowlist
  policy (`viewspec-raw-html-allowlist@1`). `viewspec check` verifies that an artifact is
  consistent with its own unsigned `provenance_manifest.json`; it does not establish who produced
  either file, because whoever edits the artifact can re-hash the manifest. `viewspec prove` adds
  the artifact, manifest, and proof report hashes in `PROOF.md` for that purpose.
- **Bounded static shells.** AppBundle Static Shell V0 rejects external network, embed, and
  script surfaces; the full shell HTML is scanned for inline handlers, `http:` URLs, `url(`,
  worker construction, and import maps before a proof can pass.
- **Local preview only.** `viewspec studio` binds 127.0.0.1, refuses requests for any other `Host`
  (which also blocks DNS rebinding), and serves only files inside its private build directory.
- **MCP path containment.** `viewspec mcp` resolves its working directory first and confines every
  tool's reads and writes to it, failing closed with `PATH_OUTSIDE_CWD`, unless
  `--allow-outside-cwd` is passed. Treat that flag as a security boundary: enable it only for
  workspaces you trust. Its `install` argument is the same explicit gate as `viewspec prove --install`.
- **MCP App renders fail closed.** A tool result can replace an MCP App's root. The bridge refuses a
  whole root that holds a tag or attribute the emitter cannot write, because a parsed inline handler
  would run under the specification's default CSP. Tool data belongs in a bundle, where the compiler
  escapes it. This does not make an untrusted server safe: it supplies the document, so the host's
  sandbox and CSP are the boundary there.
- **Output containment.** `--force` on `viewspec prove` and `viewspec compile` replaces only an empty
  directory or one that holds a ViewSpec-written marker such as `proof_report.json`; any other
  directory is refused rather than deleted.
- **Untrusted input is the normal case.** Validators are fail-closed with hard caps on bytes,
  counts, and depth, and reject unknown fields instead of ignoring them. DESIGN.md is capped at
  64 KiB, checked against its on-disk size before it is read, and its YAML front matter rejects
  anchors and aliases as well as duplicate keys. HTML inputs to `compile` are
  size-gated (`HTML_INPUT_TOO_LARGE`) before they are read. Compiling a bundle still runs the
  compiler over that input, so apply ordinary OS-level sandboxing when processing bundles from
  sources you do not trust at all.

## Out of scope

- `viewspec prove` certifies source-artifact integrity and the declared invariants. It is not a
  penetration test, not an XSS audit of author-provided text content, and not a browser security
  guarantee.
- Vulnerabilities in dependencies (Python, Node.js, protobuf, PyYAML) belong upstream, though we
  will ship version-bump releases when a dependency fix affects ViewSpec users.
