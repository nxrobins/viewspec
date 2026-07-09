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
  active surfaces: unknown inline scripts, inline event handlers, `@import`/`url(` auto-fetch,
  and remote embed surfaces. Raw HTML import sanitizes active content under the published
  allowlist policy (`viewspec-raw-html-allowlist@1`).
- **Bounded static shells.** AppBundle Static Shell V0 rejects external network, embed, and
  script surfaces; the full shell HTML is scanned for inline handlers, `http:` URLs, `url(`,
  worker construction, and import maps before a proof can pass.
- **Path containment.** File tools resolve paths against the working directory and fail closed
  with `PATH_OUTSIDE_CWD` when a path escapes it. The MCP server (`viewspec mcp`) confines reads
  and writes to the working directory unless `--allow-outside-cwd` is passed explicitly. Treat
  that flag as a security boundary: enable it only for workspaces you trust.
- **Untrusted input is the normal case.** Validators are fail-closed with hard caps on bytes,
  counts, and depth, and reject unknown fields instead of ignoring them. Compiling a bundle still
  runs the compiler over that input, so apply ordinary OS-level sandboxing when processing
  bundles from sources you do not trust at all.

## Out of scope

- `viewspec prove` certifies source-artifact integrity and the declared invariants. It is not a
  penetration test, not an XSS audit of author-provided text content, and not a browser security
  guarantee.
- Vulnerabilities in dependencies (Python, Node.js, protobuf, PyYAML) belong upstream, though we
  will ship version-bump releases when a dependency fix affects ViewSpec users.
