# ViewSpec documentation index

User-facing documentation for the local ViewSpec SDK and CLI. Start with the top section; the others
are reference material for when a command, contract, or proof points you there.

## Start here

- [Getting Started](getting-started.md): Install, the first `viewspec prove` bundle, Studio, and the
  intent lifecycle in five minutes.
- [Agent Integration](agent-integration.md): The agent contract: emit `IntentBundle` JSON, then
  check, compile, and prove.
- [Proof Bundle](proof-bundle.md): How to read `.viewspec-proof/`: `PROOF.md`, `proof_report.json`,
  the redacted support bundle, and failure codes.

## Source contracts

- [AppBundle V1/V2/V3/V4](app-bundle-v0.md): Multi-screen internal tools: fixtures, bounded state,
  visibility, the Static Shell V0 proof target, and the runnable `react-tailwind-app` target.
- [Custom Motif Authoring](custom-motif-authoring.md): Trusted local motif plugins that lower into
  `CompositionIR`.
- [Local HTML Wedge](local-html-wedge.md): Importing existing HTML as a fallback path.
- [MCP Apps](mcp-apps.md): Rendering an MCP tool's result as an interactive `ui://` view from Python.
- [`manifest-v1.schema.json`](manifest-v1.schema.json): JSON Schema for the
  `provenance_manifest.json` that every compiled artifact carries.

## Emitters and known limits

- [Emitter Matrix](emitter-matrix.md): Capability matrix across HTML/Tailwind, React TSX, and React
  Tailwind TSX.
- [Known Limits: HTML/Tailwind](known-limits-html-tailwind.md)
- [Known Limits: React TSX](known-limits-react-tsx.md)
- [Known Limits: React Tailwind TSX](known-limits-react-tailwind-tsx.md)
- [Known Limits: Accessibility Proof](known-limits-a11y.md): Exactly what the fail-closed contrast and
  accessible-name checks prove, and what they deliberately do not.

## Stability and releases

- [Compatibility](compatibility.md): What is covered by the stable contract.
- [SDK Reliability](sdk-reliability.md): Determinism guarantees and the checks behind them.
- [Release Checklist](release-checklist.md): How a release is built and published.
